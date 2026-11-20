"""Pure notice scheduling and a read-only historical-demo scheduling manifest.

No HTTP client, collector invocation, DB mutation or service update is performed.
Application status, semantic review and source freshness are independent axes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / 'pipeline/config/notice_refresh_policy_v1.json'
STATUSES = {'open', 'closed', 'unknown'}
FAILED_STATES = {'timeout', 'unparsed', 'parse_failed', 'failed', 'policy_held', 'unavailable'}


def load_policy(path=None):
    policy = json.loads(Path(path or POLICY_PATH).read_text())
    for key in ('default_hours', 'near_deadline_hours', 'near_deadline_check_hours',
                'inactive_days', 'expedited_hours', 'failure_retry_minutes'):
        if not isinstance(policy[key], (int, float)) or policy[key] <= 0:
            raise ValueError('Refresh intervals must be positive: ' + key)
    ZoneInfo(policy['timezone'])
    return policy


def instant(value, *, zone='Asia/Seoul', allow_date=False, date_boundary='end'):
    """A date boundary guides checks, never establishes a new lifecycle state."""
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        result = value
    elif allow_date and (isinstance(value, date) or len(str(value)) == 10):
        day = value if isinstance(value, date) else date.fromisoformat(value)
        result = datetime.combine(day, time.min if date_boundary == 'start' else time.max, ZoneInfo(zone))
    else:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('An explicit timestamp requires a timezone')
    return result.astimezone(timezone.utc)


def _iso(value):
    return value.isoformat().replace('+00:00', 'Z') if value else None


def schedule_notice(record, *, now, policy=None):
    policy = deepcopy(policy or load_policy())
    policy.update(policy.get('source_overrides', {}).get(record.get('source_id'), {}))
    at = instant(now)
    if at is None:
        raise ValueError('now is required')
    zone = record.get('deadline_timezone') or policy['timezone']
    checked = instant(record.get('checked_at'))
    attempted = instant(record.get('last_attempt_at'))
    invalid_dates = []
    def boundary(key, side):
        try:
            return instant(record.get(key), zone=zone, allow_date=True, date_boundary=side)
        except ValueError:
            invalid_dates.append(key)
            return None
    closing = boundary('closes_at', 'end')
    opening = boundary('opens_at', 'start')
    status = record.get('application_status', 'unknown')
    if status not in STATUSES:
        raise ValueError('Use open, closed or unknown for application_status')
    observation = record.get('observed_status')
    fetch_state = record.get('source_state', 'unknown')
    if invalid_dates:
        fetch_state = 'unparsed'
    if observation is not None and observation not in STATUSES:
        raise ValueError('Unknown observed_status')
    if record.get('status_observation_verified') and fetch_state == 'parsed' and observation is not None:
        status = observation
    reasons = ['unparsed_' + key for key in invalid_dates]
    interval = timedelta(hours=policy['default_hours'])
    if status == 'closed' or record.get('inactive') is True:
        interval = timedelta(days=policy['inactive_days'])
        reasons.append('inactive_policy')
    else:
        reasons.append('default_policy')
    near = timedelta(hours=policy['near_deadline_hours'])
    if ((closing and status != 'closed' and timedelta(0) <= closing-at <= near)
            or (opening and status == 'closed' and timedelta(0) <= opening-at <= near)):
        interval = min(interval, timedelta(hours=policy['near_deadline_check_hours']))
        reasons.append('near_deadline_or_reopening_check')
    expedited = (record.get('expedited', policy.get('expedited', False)) is True
                  or record.get('intake_mode') in policy.get('expedited_intake_modes', []))
    if expedited:
        hours = record.get('expedited_hours', policy['expedited_hours'])
        if hours <= 0:
            raise ValueError('expedited_hours must be positive')
        interval = min(interval, timedelta(hours=hours))
        reasons.append('expedited_source')
    desired = checked + interval if checked else at
    if not checked:
        reasons.append('no_successful_check')
    elif checked > at:
        desired = at
        reasons.append('future_checked_at_needs_validation')
    # A known boundary warrants a new observation; it does not flip the status.
    for boundary in (opening, closing):
        if boundary and checked and boundary > checked:
            desired = min(desired, boundary)
    if not record.get('original_available', False):
        desired = min(desired, at)
        reasons.append('original_missing')
    failed = fetch_state in FAILED_STATES
    if failed:
        desired = ((attempted + timedelta(minutes=policy['failure_retry_minutes']))
                   if attempted else at)
        reasons.append('source_failure_requires_retry')
    provider_gate = instant(record.get('provider_not_before'))
    retry_after = record.get('retry_after')
    if retry_after is not None:
        if isinstance(retry_after, (int, float)) or str(retry_after).isdigit():
            if float(retry_after) < 0 or attempted is None:
                raise ValueError('Retry-After seconds require a recorded attempt time')
            candidate = attempted + timedelta(seconds=float(retry_after))
        else:
            candidate = instant(parsedate_to_datetime(str(retry_after)))
        provider_gate = max(provider_gate, candidate) if provider_gate else candidate
    spacing = record.get('provider_min_interval_seconds', policy['provider_min_interval_seconds'])
    if spacing < 0:
        raise ValueError('Provider pacing cannot be negative')
    if attempted and spacing:
        candidate = attempted + timedelta(seconds=spacing)
        provider_gate = max(provider_gate, candidate) if provider_gate else candidate
    effective = max(desired, provider_gate) if provider_gate else desired
    stale = (not checked or checked > at or not record.get('original_available', False)
             or failed or at >= desired or (status == 'open' and closing is not None and closing <= at))
    input_current_open_verified = deepcopy(record.get('current_open_verified', False))
    current_open_verified = (input_current_open_verified is True and status == 'open'
                             and not stale and record.get('original_available') is True
                             and fetch_state == 'parsed' and not failed)
    return {
        'record_id': record.get('record_id') or record.get('opportunity_id'),
        'policy_version': policy['version'], 'scope': record.get('scope', 'candidate'),
        'status': status, 'status_basis': ('verified_explicit_observation'
            if record.get('status_observation_verified') and fetch_state == 'parsed' and observation is not None
            else 'preserved_input_status'),
        'status_as_of': record.get('status_as_of'),
        'freshness': 'stale' if stale else 'fresh',
        'checked_at': _iso(checked), 'desired_next_check_at': _iso(desired),
        'provider_not_before': _iso(provider_gate), 'next_check_at': _iso(effective),
        'refresh_due': at >= effective, 'provider_waiting': bool(provider_gate and at < provider_gate),
        'interval_hours': interval.total_seconds()/3600,
        'closing_time_passed': bool(closing and closing <= at),
        'semantic_review': deepcopy(record.get('semantic_review', 'unreviewed')),
        'input_current_open_verified': input_current_open_verified,
        'current_open_verified': current_open_verified,
        'reasons': reasons,
    }


def build_manifest(records, *, now, policy=None):
    policy = policy or load_policy()
    schedules = [schedule_notice(record, now=now, policy=policy) for record in records]
    return {'contract_version': 'notice-refresh-manifest-v1',
            'at': _iso(instant(now)), 'policy_version': policy['version'],
            'policy_sha256': sha256(json.dumps(policy, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'policy_status': policy['status'], 'records': schedules,
            'counts': {'records': len(schedules),
                       'refresh_due': sum(row['refresh_due'] for row in schedules),
                       'status': dict(Counter(row['status'] for row in schedules)),
                       'freshness': dict(Counter(row['freshness'] for row in schedules))},
            'http_requests': 0, 'database_writes': 0,
            'current_recruitment_verification_performed': False}


def load_demo_records():
    """Read existing snapshots without asserting that any call rechecks the web."""
    import project
    from benefitlens_source_storage import read_source_bytes
    with project.connect() as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        rows = connection.execute('SELECT opportunity_id,payload,source_refs,resolution_status,extraction_status '
                                  'FROM benefitlens.opportunities ORDER BY opportunity_id').fetchall()
        urls = sorted({ref['url'] for _, _, refs, _, _ in rows for ref in refs})
        hashes = sorted({ref['content_sha256'] for _, _, refs, _, _ in rows for ref in refs if ref.get('content_sha256')})
        documents = connection.execute('SELECT document_id,canonical_url,content_sha256,text_sha256,raw_path,text_path,fetched_at,metadata '
                                       'FROM benefitlens.pipeline_document WHERE canonical_url=ANY(%s) OR content_sha256=ANY(%s)', (urls, hashes)).fetchall()
    records = []
    for ident, payload, refs, resolution, extraction in rows:
        original_checks = []
        for doc_id, url, raw_sha, text_sha, raw_path, text_path, fetched, metadata in documents:
            if not any(ref['url'] == url or ref.get('content_sha256') == raw_sha for ref in refs):
                continue
            exact = any(ref.get('content_sha256') == raw_sha for ref in refs)
            try:
                storage = metadata.get('raw_storage')
                if metadata.get('experience_scale'):
                    storage = metadata['experience_scale']['raw'].get('storage')
                raw = read_source_bytes(raw_path, storage=storage)
                text = read_source_bytes(text_path)
                verified = sha256(raw).hexdigest() == raw_sha and sha256(text).hexdigest() == text_sha
            except (OSError, ValueError):
                verified = False
            original_checks.append({'document_id': doc_id, 'url': url, 'raw_sha256': raw_sha,
                'text_sha256': text_sha, 'raw_path': raw_path, 'text_path': text_path,
                'fetched_at': fetched.isoformat(), 'hashes_valid': verified,
                'binding': 'exact_hash' if exact else 'url_only_unreviewed'})
        application = payload.get('application') or {}
        link_check = application.get('link_verification') or {}
        matched = [doc for doc in original_checks if doc['hashes_valid'] and doc['binding'] == 'exact_hash']
        successful = link_check.get('checked_at') if link_check.get('status') == 200 else None
        if not successful and matched:
            successful = max(doc['fetched_at'] for doc in matched)
        records.append({'record_id': ident, 'source_id': payload.get('source_family'),
            'scope': 'historical_demo', 'application_status': 'unknown',
            'checked_at': successful, 'original_available': any(doc['hashes_valid'] for doc in original_checks),
            'source_state': 'unparsed' if extraction == 'partial' else 'parsed' if successful else 'unknown',
            'closes_at': application.get('closes_on'), 'opens_at': application.get('opens_on'),
            'semantic_review': payload.get('semantic_review', 'unreviewed'),
            'current_open_verified': False,
            'original_checks': original_checks, 'source_refs': refs,
            'normalized_snapshot': payload,
            'input_states': {'resolution_status': resolution, 'extraction_status': extraction,
                             'sample_scope': payload.get('sample_scope')}})
    return records


def main():
    from run_benefitlens_campaign import CACHE, atomic_json, checked_root
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--from-demo-db', action='store_true')
    source.add_argument('--input', type=Path)
    parser.add_argument('--policy', type=Path, default=POLICY_PATH)
    parser.add_argument('--now', default=datetime.now(timezone.utc).isoformat())
    parser.add_argument('--output-root', type=Path, default=CACHE/'team-execution-20261119/notice-refresh')
    args = parser.parse_args()
    records = load_demo_records() if args.from_demo_db else json.loads(args.input.read_text())
    if isinstance(records, dict):
        records = records['records']
    output = checked_root(args.output_root)
    manifest = build_manifest(records, now=args.now, policy=load_policy(args.policy))
    atomic_json(output/'input-records.json', records)
    manifest['input_records'] = str(output/'input-records.json')
    manifest['input_sha256'] = sha256((output/'input-records.json').read_bytes()).hexdigest()
    manifest['schema_sha256'] = {name: sha256((ROOT/ref.split('#', 1)[0]).read_bytes()).hexdigest()
                                for name, ref in load_policy(args.policy)['schema_refs'].items()}
    atomic_json(output/'manifest.json', manifest)
    print(json.dumps({'manifest': str(output/'manifest.json'), **manifest['counts'],
                      'http_requests': 0, 'database_writes': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
