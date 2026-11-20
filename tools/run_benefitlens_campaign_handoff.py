"""Stage reviewed campaign observations as a pinned, same-day expansion delta.

This bridge reads the campaign ledger and originals. It never fetches data,
changes an earlier handoff, or writes to the production database.
"""
import argparse
from collections import Counter, defaultdict
from datetime import date
from difflib import SequenceMatcher
import fcntl
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import build_benefitlens_live_batch as live
from benefitlens_campaign_identity import application_identity
from benefitlens_discovery_handoff import build_handoff
from benefitlens_handoff import to_delivery_rows, validate_handoff
from benefitlens_opportunity_groups import category, group_opportunities, issuer_key, role_key
from benefitlens_source_storage import read_source_bytes, store_source_bytes
from run_benefitlens_batch import validate_open_cohort
from run_benefitlens_campaign import DEFAULT_ROOT, app_key, atomic_json, checked_root, encoded, today
from run_benefitlens_expansion import current_rounds, file_ref, pinned_json
from run_benefitlens_handoff import immutable_json

VERSION = 'campaign-handoff-review-v1'


def read_blob(ref):
    path = Path(ref['path'])
    if not path.is_absolute():
        raise ValueError('Blob reference must be absolute')
    raw = read_source_bytes(path, storage=ref.get('storage'))
    if sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('Decoded blob hash mismatch: '+str(path))
    return json.loads(raw)


def keys(row):
    provider, native = live.source_id(row)
    return (encoded([provider, native]), app_key(row['application_url']),
            encoded([issuer_key(row), category(row), role_key(row)[0]]))


class PriorIndex:
    def __init__(self, entries):
        self.ids, self.apps, self.aliases, self.parents = set(), set(), set(), defaultdict(set)
        for identity, application, parent in entries:
            self.ids.add(identity)
            self.apps.add(application)
            if alias := application_identity(application):
                self.aliases.add(alias)
            issuer, kind, role = json.loads(parent)
            self.parents[issuer, kind].add(role)

    def reason(self, identity, application, parent):
        if identity in self.ids:
            return 'previous_source_native_identity'
        if application in self.apps or application_identity(application) in self.aliases:
            return 'previous_equivalent_application_route'
        issuer, kind, role = json.loads(parent)
        roles = self.parents.get((issuer, kind), ())
        if role in roles:
            return 'previous_issuer_role_variant'
        a = set(role.split())
        for other in roles:
            b = set(other.split())
            if (len(a | b) >= 4 and len(a & b)/len(a | b) >= .78) or SequenceMatcher(None, role, other).ratio() >= .92:
                return 'similar_prior_parent_requires_review'
        return None


def prior_entries(bundle):
    if bundle.get('contract_version') != 'team-handoff-v1':
        raise ValueError('Prior handoff must be a team-handoff-v1 bundle')
    programs = {r['program_id']: r for r in bundle['programs']}
    rounds = {(r['program_id'], r['round_id']): r for r in bundle['opportunity_rounds']}
    for entry in bundle['metadata'].get('source_specific', []):
        pid, rid = entry['program_id'], entry['round_id']
        r = rounds[pid, rid]
        fields = entry['fields']
        if r['status'] == 'open' and fields.get('source_family'):
            yield keys({**programs[pid], **r, **fields})


def validate_review(review, day):
    if (review.get('contract_version') != VERSION or review.get('as_of') != day
            or not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip()):
        raise ValueError('Expected a named, current-day campaign review')
    decisions = review.get('decisions')
    if not isinstance(decisions, list) or not isinstance(review.get('prior_handoffs'), list):
        raise ValueError('Review must contain decisions and pinned prior handoffs')
    result = {}
    for item in decisions:
        job, digest = item.get('job_id'), item.get('row_sha256')
        if (type(job) is not int or job <= 0 or job in result or not isinstance(digest, str)
                or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)
                or item.get('decision') not in {'handoff', 'held', 'reobserved'}
                or not isinstance(item.get('reason'), str) or not item['reason'].strip()):
            raise ValueError('Invalid, repeated, or ungrounded review decision')
        result[job] = item
    return result


def select_rows(review, observations, day, prior):
    decisions = validate_review(review, day)
    if not set(decisions) <= set(observations):
        raise ValueError('Review references a non-current or missing observation')
    rows, outcomes, ids = [], [], {}
    cache = {}
    for job, obs in sorted(observations.items()):
        decision = decisions.get(job)
        if decision and decision['row_sha256'] != obs['row_ref']['sha256']:
            raise ValueError('Review row hash mismatch for job '+str(job))
        label = decision['decision'] if decision else 'held'
        reason = decision['reason'] if decision else 'no_explicit_review'
        if label == 'handoff':
            if obs['state'] != 'machine_verified':
                label, reason = 'held', 'observation_not_new_relative_to_baseline'
            else:
                row = read_blob(obs['row_ref'])
                identity, application, parent = keys(row)
                if (identity, application, parent) != (obs['identity'], obs['application'], obs['parent']):
                    raise ValueError('Observation keys differ from its pinned row')
                duplicate = prior.reason(identity, application, parent)
                if duplicate:
                    label, reason = 'held', duplicate
                else:
                    try:
                        live.verify_posting(row, date.fromisoformat(day), cache)
                    except (ValueError, OSError, KeyError) as exc:
                        label, reason = 'held', 'source_recheck_failed: '+str(exc)
                    else:
                        rows.append(row)
                        ids[identity] = job
        outcomes.append({'job_id': job, 'row_sha256': obs['row_ref']['sha256'],
                         'decision': label, 'reason': reason})
    groups, _, duplicates = group_opportunities(rows)
    if duplicates:
        raise ValueError('Duplicate native observations in reviewed input')
    ready_groups, ready_ids = [], set()
    for group in groups:
        group_ids = {keys(row)[0] for row in group['variants']}
        # A family can have several variants. They need separate variant review;
        # this increment admits only unambiguous single-family additions.
        if group['decision'] == 'confirmed' and len(group_ids) == 1:
            ready_groups.append(group)
            ready_ids.update(group_ids)
    seen_apps = Counter(app_key(row['application_url']) for row in rows if keys(row)[0] in ready_ids)
    seen_aliases = Counter(alias for row in rows if keys(row)[0] in ready_ids
                           if (alias := application_identity(app_key(row['application_url']))))
    shared = {keys(row)[0] for row in rows if seen_apps[app_key(row['application_url'])] > 1 or
              seen_aliases[application_identity(app_key(row['application_url']))] > 1}
    ready_ids -= shared
    ready_groups = [g for g in ready_groups if keys(g['variants'][0])[0] in ready_ids]
    blocked_jobs = {job for identity, job in ids.items() if identity not in ready_ids}
    for outcome in outcomes:
        if outcome['job_id'] in blocked_jobs:
            outcome.update(decision='held', reason='current_family_or_application_overlap_requires_review')
    return [r for r in rows if keys(r)[0] in ready_ids], ready_groups, outcomes


def staged_priors(outbox, review_ref):
    """Compare against earlier released deltas, without treating a replay as new."""
    refs = {}
    for path in sorted(outbox.glob('????-??-??/receipt-*.json')):
        receipt = json.loads(path.read_text())
        if path.stem != 'receipt-'+sha256(encoded(receipt).encode()).hexdigest()[:16]:
            raise ValueError('Prior handoff receipt content changed: '+str(path))
        if receipt.get('review') == review_ref or not receipt.get('bundle'):
            continue
        ref = receipt['bundle']
        refs[ref['sha256']] = ref
    return list(refs.values())


def release_counts(receipts):
    """A validation replay can have a new code hash without adding a record."""
    identities, parents, reviews = set(), set(), set()
    for receipt in receipts:
        if not receipt.get('bundle'):
            continue
        reviews.add(receipt['review']['sha256'])
        for identity, _, parent in prior_entries(pinned_json(receipt['bundle'])):
            identities.add(identity)
            parents.add(parent)
    return {'released_batches': len(reviews), 'source_variants': len(identities),
            'rule_parent_candidates': len(parents)}


def stage(root, review_path):
    root = checked_root(root)
    outbox = root/'verified-handoffs'
    outbox.mkdir(exist_ok=True)
    with (outbox/'handoff.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stage_locked(root, review_path, outbox)


def stage_locked(root, review_path, outbox):
    review_ref = file_ref(review_path)
    review = pinned_json(review_ref)
    day = today()
    validate_review(review, day)
    with sqlite3.connect((root/'frontier.sqlite').as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        baseline = json.loads(db.execute("SELECT value FROM meta WHERE key='baseline_ref'").fetchone()[0])
        pinned_json(baseline['manifest'])
        records = {(r['identity'], r['application'], r['parent']) for r in read_blob(baseline['index'])}
        if records != {tuple(r) for r in db.execute('SELECT identity,application,parent FROM baseline')}:
            raise ValueError('Baseline deduplication index does not match its pin')
        observations = {r['job']: {**dict(r), 'row_ref': json.loads(r['row_ref'])} for r in db.execute(
            "SELECT * FROM observations WHERE day=? AND (state='machine_verified' OR job IN (SELECT value FROM json_each(?)))",
            (day, encoded([r['job_id'] for r in review['decisions']])))}
    prior_refs = review['prior_handoffs']+staged_priors(outbox, review_ref)
    for ref in prior_refs:
        records.update(prior_entries(pinned_json(ref)))
    rows, groups, outcomes = select_rows(review, observations, day, PriorIndex(records))
    out = outbox/day
    out.mkdir(parents=True, exist_ok=True)
    receipt = {'contract_version': VERSION, 'as_of': day, 'review': review_ref,
               'baseline': baseline['manifest'], 'prior_handoffs': prior_refs,
               'outcomes': outcomes, 'counts': dict(Counter(r['decision'] for r in outcomes)),
               'handoff_source_variants': len(rows), 'handoff_rule_parent_candidates': len(groups),
               'semantic_opportunity_total': None, 'production_database_writes': 0, 'bundle': None}
    if rows:
        bundle = build_handoff(rows, groups, input_ref=review_ref, curated=True,
                               created_at=max(r['fetched_at'] for r in rows), as_of=date.fromisoformat(day))
        validation = validate_handoff(bundle, artifact_root=Path('/'))
        if not validation['valid'] or len(current_rounds(bundle, day)) != len(rows):
            raise ValueError('Ineligible handoff: '+encoded(validation['errors']))
        delivery = to_delivery_rows(bundle, Path('/'))
        validate_open_cohort(delivery, {r['opportunity_id']: 'source_backed' for r in delivery['opportunity_round']}, date.fromisoformat(day))
        stored = store_source_bytes((encoded(bundle)+'\n').encode(), out, suffix='.json')
        receipt.update(bundle={**file_ref(stored['path']), 'role': 'current_opportunities', 'addition_kind': 'new'},
                       validation=validation, delivery_rows={k: len(v) for k, v in delivery.items()})
    digest = sha256(encoded(receipt).encode()).hexdigest()
    ref = immutable_json(out/f'receipt-{digest[:16]}.json', receipt)
    released = [json.loads(p.read_text()) for p in out.glob('receipt-*.json')]
    atomic_json(out/'summary.json', {'as_of': day, **release_counts(released),
                                    'latest_review_receipt': ref, 'production_database_writes': 0})
    return ref


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=DEFAULT_ROOT, type=Path)
    parser.add_argument('--review', required=True, type=Path)
    args = parser.parse_args()
    print(encoded(stage(args.root, args.review)))
