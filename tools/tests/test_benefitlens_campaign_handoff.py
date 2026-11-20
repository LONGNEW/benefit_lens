import json
from hashlib import sha256
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_benefitlens_campaign_handoff as bridge


DAY = '2026-11-10'
DIGEST = 'a'*64


def review(decision='handoff', digest=DIGEST):
    return {'contract_version': bridge.VERSION, 'as_of': DAY, 'reviewer': 'source_reviewer',
            'prior_handoffs': [], 'decisions': [{'job_id': 1, 'row_sha256': digest,
            'decision': decision, 'reason': 'Reviewed same-day source and application scope'}]}


def test_parent_and_application_alias_dedup_cross_source():
    old = 'https://lu.varbi.com/en/what:job/jobID:981772/type:job/where:39/apply:1'
    new = 'https://lu.varbi.com/en/what:login/jobID:981772/type:job/where:4/apply:1'
    parent = json.dumps(['lund university', 'student_role', 'doctoral student in thermodynamics'])
    index = bridge.PriorIndex([('old_id', old, parent)])
    assert index.reason('old_id', new, parent) == 'previous_source_native_identity'
    assert index.reason('new_id', new, parent) == 'previous_equivalent_application_route'
    assert index.reason('new_id', 'https://jobs.example.net/apply/1', parent) == 'previous_issuer_role_variant'
    similar = json.dumps(['lund university', 'student_role', 'doctoral students in thermodynamics'])
    assert index.reason('new_id', 'https://jobs.example.net/apply/1', similar) == 'similar_prior_parent_requires_review'


@pytest.mark.parametrize('mode', ['unreviewed', 'stale', 'mismatched', 'not_new'])
def test_only_matching_current_review_can_release_observation(mode):
    record = {1: {'state': 'machine_verified', 'row_ref': {'sha256': DIGEST}}}
    value = review()
    if mode == 'stale':
        value['as_of'] = '2026-11-09'
        with pytest.raises(ValueError, match='current-day'):
            bridge.select_rows(value, record, DAY, bridge.PriorIndex([]))
        return
    if mode == 'mismatched':
        value['decisions'][0]['row_sha256'] = 'b'*64
        with pytest.raises(ValueError, match='row hash mismatch'):
            bridge.select_rows(value, record, DAY, bridge.PriorIndex([]))
        return
    if mode == 'unreviewed':
        value['decisions'].clear()
    else:
        record[1]['state'] = 'known_baseline'
    rows, groups, outcomes = bridge.select_rows(value, record, DAY, bridge.PriorIndex([]))
    assert not rows and not groups and outcomes[0]['decision'] == 'held'


def test_prior_staged_delta_is_used_for_later_reviews_but_not_self_replay(tmp_path):
    review_ref = {'path': '/review.json', 'sha256': DIGEST}
    ref = {'path': '/reviewed-delta.json.gz', 'sha256': 'c'*64}
    receipt = {'review': review_ref, 'bundle': ref}
    dest = tmp_path/DAY
    dest.mkdir()
    file = dest/('receipt-'+sha256(bridge.encoded(receipt).encode()).hexdigest()[:16]+'.json')
    file.write_text(bridge.encoded(receipt))
    assert bridge.staged_priors(tmp_path, review_ref) == []
    assert bridge.staged_priors(tmp_path, {'path': '/later-review.json', 'sha256': 'd'*64}) == [ref]
    file.write_text('{}')
    with pytest.raises(ValueError, match='receipt content changed'):
        bridge.staged_priors(tmp_path, review_ref)


def test_revalidation_of_same_records_does_not_increase_release_count(monkeypatch):
    evidence = {1: [('one', 'url1', 'parent1')], 2: [('one', 'url1', 'parent1')],
                3: [('two', 'url2', 'parent2')]}
    monkeypatch.setattr(bridge, 'pinned_json', lambda ref: evidence[ref])
    monkeypatch.setattr(bridge, 'prior_entries', iter)
    receipts = [{'bundle': 1, 'review': {'sha256': 'a'}}, {'bundle': 2, 'review': {'sha256': 'a'}},
                {'bundle': 3, 'review': {'sha256': 'b'}}]
    assert bridge.release_counts(receipts) == {
        'released_batches': 2, 'source_variants': 2, 'rule_parent_candidates': 2}
