from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_notice_refresh import build_manifest, instant, load_policy, schedule_notice, ROOT

NOW = '2026-11-19T12:00:00+09:00'


def record(**updates):
    result = {'record_id': 'test', 'application_status': 'open',
              'checked_at': NOW, 'original_available': True, 'source_state': 'parsed',
              'semantic_review': {'state': 'candidate', 'reason': 'Unreviewed meaning'}}
    result.update(updates)
    return result


def test_daily_schedule_and_semantic_review_are_preserved_without_input_mutation():
    data = record()
    before = deepcopy(data)
    result = schedule_notice(data, now=NOW)
    assert result['next_check_at'] == '2026-11-20T03:00:00Z'
    assert result['freshness'] == 'fresh' and not result['refresh_due']
    assert result['semantic_review'] == data['semantic_review'] and data == before


def test_deadline_within_72_hours_uses_six_hour_check_not_six_hour_deadline():
    result = schedule_notice(record(closes_at='2026-11-21T12:00:00+09:00'), now=NOW)
    assert result['interval_hours'] == 6
    assert result['next_check_at'] == '2026-11-19T09:00:00Z'


def test_date_only_closing_boundary_uses_declared_timezone_and_does_not_close():
    data = record(checked_at='2026-11-19T23:50:00+09:00', closes_at='2026-11-19')
    result = schedule_notice(data, now='2026-11-20T00:01:00+09:00')
    assert result['next_check_at'] == '2026-11-19T14:59:59.999999Z'
    assert result['status'] == 'open' and result['freshness'] == 'stale' and result['refresh_due']


def test_equivalent_timezone_inputs_schedule_the_same_instant():
    left = schedule_notice(record(), now=NOW)
    right = schedule_notice(record(checked_at='2026-11-19T03:00:00Z'), now='2026-11-19T03:00:00Z')
    assert left == right
    with pytest.raises(ValueError, match='timezone'):
        schedule_notice(record(checked_at='2026-11-19T12:00:00'), now=NOW)


def test_missing_original_is_stale_and_due_without_converting_status():
    result = schedule_notice(record(original_available=False), now=NOW)
    assert result['freshness'] == 'stale' and result['refresh_due'] and result['status'] == 'open'


@pytest.mark.parametrize('state', ['timeout', 'unparsed', 'parse_failed', 'policy_held'])
def test_failed_fetch_preserves_status_and_honors_retry_after(state):
    data = record(source_state=state, last_attempt_at=NOW, retry_after=7200)
    result = schedule_notice(data, now=NOW)
    assert result['status'] == 'open' and result['freshness'] == 'stale'
    assert result['desired_next_check_at'] == '2026-11-19T03:30:00Z'
    assert result['next_check_at'] == '2026-11-19T05:00:00Z'
    assert result['provider_waiting'] and not result['refresh_due']


def test_http_date_retry_after_is_a_separate_provider_gate():
    result = schedule_notice(record(original_available=False,
        retry_after='Thu, 19 Nov 2026 04:00:00 GMT'), now=NOW)
    assert result['desired_next_check_at'] == '2026-11-19T03:00:00Z'
    assert result['next_check_at'] == '2026-11-19T04:00:00Z'


def test_closed_notice_uses_weekly_checks_and_is_not_reopened_by_future_date():
    result = schedule_notice(record(application_status='closed'), now=NOW)
    assert result['interval_hours'] == 168 and result['status'] == 'closed'
    reopening = schedule_notice(record(application_status='closed', opens_at='2026-11-20'), now=NOW)
    assert reopening['interval_hours'] == 6 and reopening['status'] == 'closed'
    assert reopening['next_check_at'] == '2026-11-19T09:00:00Z'


def test_reopening_boundary_warrants_recheck_but_not_implicit_status_change():
    data = record(application_status='closed', checked_at='2026-11-19T23:00:00+09:00', opens_at='2026-11-20')
    result = schedule_notice(data, now='2026-11-20T00:05:00+09:00')
    assert result['status'] == 'closed' and result['refresh_due']
    assert result['next_check_at'] == '2026-11-19T15:00:00Z'
    observed = schedule_notice(dict(data, observed_status='open', status_observation_verified=True), now='2026-11-20T00:05:00+09:00')
    assert observed['status'] == 'open' and observed['status_basis'] == 'verified_explicit_observation'
    failed = schedule_notice(dict(data, observed_status='open', status_observation_verified=True, source_state='timeout'), now=NOW)
    assert failed['status'] == 'closed'


def test_expedited_policy_cannot_override_provider_pacing():
    data = record(expedited=True, expedited_hours=0.5,
                  last_attempt_at=NOW, provider_min_interval_seconds=7200)
    result = schedule_notice(data, now=NOW)
    assert result['interval_hours'] == .5
    assert result['desired_next_check_at'] == '2026-11-19T03:30:00Z'
    assert result['next_check_at'] == '2026-11-19T05:00:00Z'


def test_rolling_admission_is_configurable_and_does_not_assert_open_status():
    result = schedule_notice(record(application_status='unknown', intake_mode='rolling'), now=NOW)
    assert result['interval_hours'] == 1 and result['status'] == 'unknown'
    policy = load_policy()
    policy['source_overrides'] = {'public-source': {'expedited': True, 'expedited_hours': 2}}
    result = schedule_notice(record(source_id='public-source'), now=NOW, policy=policy)
    assert result['interval_hours'] == 2


def test_unparsed_deadline_is_stale_and_never_implies_closed():
    result = schedule_notice(record(closes_at='See the institution timetable'), now=NOW)
    assert result['status'] == 'open' and result['freshness'] == 'stale'
    assert result['refresh_due'] and 'unparsed_closes_at' in result['reasons']


def test_future_capture_timestamp_is_stale_and_requires_validation():
    result = schedule_notice(record(checked_at='2026-11-20T12:00:00+09:00'), now=NOW)
    assert result['freshness'] == 'stale' and result['refresh_due']


@pytest.mark.parametrize('updates', [
    {'checked_at': '2026-11-18T11:00:00+09:00'},
    {'source_state': 'timeout'},
    {'source_state': 'unknown'},
    {'original_available': False},
    {'application_status': 'closed'},
    {'closes_at': '2026-11-18'},
])
def test_historical_verified_flag_is_retained_but_not_exposed_as_current(updates):
    data = record(current_open_verified=True, **updates)
    result = schedule_notice(data, now=NOW)
    assert result['input_current_open_verified'] is True
    assert result['current_open_verified'] is False
    assert result['status'] == data['application_status']
    assert data['current_open_verified'] is True


def test_current_verified_view_can_retain_but_never_create_a_trust_flag():
    result = schedule_notice(record(current_open_verified=True), now=NOW)
    assert result['input_current_open_verified'] is result['current_open_verified'] is True
    result = schedule_notice(record(current_open_verified=False,
        observed_status='open', status_observation_verified=True), now=NOW)
    assert result['input_current_open_verified'] is result['current_open_verified'] is False


def test_manifest_is_planning_only_and_keeps_historical_scope():
    result = build_manifest([record(scope='historical_demo')], now=NOW)
    assert result['records'][0]['scope'] == 'historical_demo'
    assert result['http_requests'] == result['database_writes'] == 0
    assert result['current_recruitment_verification_performed'] is False
    changed = load_policy()
    changed['default_hours'] = 48
    assert result['policy_sha256'] != build_manifest([record(scope='historical_demo')], now=NOW, policy=changed)['policy_sha256']


def test_semester_and_term_are_additive_valid_amount_periods_not_monthly_conversion():
    schema = json.loads((ROOT/'pipeline/schemas/common-fields.schema.json').read_text())
    validator = Draft202012Validator({'$ref': '#/$defs/amount', '$defs': schema['$defs']})
    amount = {'kind': 'exact', 'value': 6000000, 'min': None, 'max': None,
              'currency_code': 'KRW', 'raw_symbol': '원', 'frequency': 'per_semester',
              'currency_evidence_ids': ['official-amount'], 'gross_or_net': 'unknown'}
    for period in ['per_semester', 'per_term', 'per_month']:
        original = deepcopy(amount)
        original['frequency'] = period
        validator.validate(original)
        assert original['frequency'] == period and original['value'] == 6000000
