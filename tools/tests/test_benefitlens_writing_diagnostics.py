"""Mechanical diagnostic boundaries; numeric fixtures are not human ratings."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_writing_diagnostics import (
    aggregate_human_reviews, diagnose_writing, main, quadratic_weighted_kappa,
)


def fixture():
    text = '자료를 수집할 계획입니다.'
    return {
        'document_id': 'fixture', 'provenance': 'synthetic_fixture',
        'subject_id': 'person', 'allowed_experience_ids': ['episode'],
        'text': text,
        'sources': [{'source_id': 'source', 'text': text, 'sha256': sha256(text.encode()).hexdigest()}],
        'facts': [{'fact_id': 'f1', 'subject_id': 'person', 'experience_id': 'episode',
                   'kind': 'future_plan', 'value': text, 'status': 'confirmed',
                   'source_ref': {'source_id': 'source', 'char_start': 0, 'char_end': len(text), 'quote': text}}],
        'sentences': [{'sentence_id': 's1', 'paragraph_id': 'p1', 'text': text, 'fact_ids': ['f1'],
                       'subject_id': 'person', 'experience_id': 'episode',
                       'claim_kind': 'future_plan', 'literal_fact_binding': True}],
        'paragraphs': [{'paragraph_id': 'p1', 'sentence_ids': ['s1']}],
        'paragraph_links': [],
        'claim_ground_links': [{'link_id': 'g1', 'claim_sentence_id': 's1', 'fact_ids': ['f1']}],
        'requirements': [{'requirement_id': 'r1', 'required': True, 'sentence_ids': ['s1']}],
        'outline_order': {'basis': 'test_fixture_declaration', 'expected_paragraph_ids': ['p1']},
        'constraints': {'max_characters': len(text), 'max_pages': 1},
        'tasks': [{'task_id': 't1', 'depends_on': [], 'start': '2026-11-20', 'end': '2026-11-21'}],
        'human_reviews': [],
    }


def checks(payload):
    return {row['check_id']: row for row in diagnose_writing(payload)['checks']}


def codes(payload):
    return {finding['code'] for check in diagnose_writing(payload)['checks'] for finding in check['findings']}


def test_complete_annotation_is_mechanical_only_and_input_unchanged():
    payload = fixture()
    before = deepcopy(payload)
    result = diagnose_writing(payload)
    assert not codes(payload)
    assert result['semantic_quality_scores'] is None
    assert result['semantic_quality_status'] == 'not_measured'
    assert result['automatic_quality_acceptance'] is False
    assert result['human_review_summary']['status'] == 'not_measured'
    assert result['execution']['model_calls'] == 0
    assert payload == before


def test_missing_annotation_is_unobserved_not_a_pass_or_semantic_zero():
    result = diagnose_writing({})
    for check in result['checks']:
        assert check['denominator'] == 0
        assert check['violation_fraction'] is None
        assert check['status'] == 'not_observable'
    assert result['semantic_quality_scores'] is None


def test_declared_empty_collections_differ_from_missing_collections():
    payload = fixture()
    payload['tasks'] = []
    check = checks(payload)['task_dependency_cycle']
    assert check['status'] == 'not_applicable'
    assert check['denominator'] == 0
    payload['tasks'] = None
    assert checks(payload)['task_dependency_cycle']['status'] == 'not_observable'


def test_subject_annotation_change_is_detected():
    payload = fixture()
    payload['sentences'][0]['subject_id'] = 'other-person'
    assert 'subject_id_mismatch' in codes(payload)


def test_shared_foreign_identity_still_violates_explicit_target_scope():
    payload = fixture()
    payload['facts'][0]['subject_id'] = payload['sentences'][0]['subject_id'] = 'other-person'
    assert 'fact_outside_target_subject' in codes(payload)
    assert 'subject_id_mismatch' not in codes(payload)


def test_prose_change_without_kind_annotation_is_not_interpreted():
    payload = fixture()
    payload['sentences'][0].pop('claim_kind')
    payload['sentences'][0].pop('literal_fact_binding')
    payload['sentences'][0]['text'] = '이미 자료를 수집했습니다.'
    payload['text'] = payload['sentences'][0]['text']
    check = checks(payload)['sentence_plan_status']
    assert check['status'] == 'not_observable'
    assert check['denominator'] == 0
    assert 'future_plan_presented_as_actual' not in codes(payload)


def test_plan_presented_as_actual_is_detected_from_annotations():
    payload = fixture()
    payload['sentences'][0]['claim_kind'] = 'performed_action'
    assert 'future_plan_presented_as_actual' in codes(payload)


def test_unknown_kind_is_unobserved_instead_of_pass():
    payload = fixture()
    payload['sentences'][0]['claim_kind'] = 'unknown'
    assert checks(payload)['sentence_plan_status']['status'] == 'not_observable'


def test_ongoing_action_cannot_be_relabelled_completed_in_annotations():
    payload = fixture()
    payload['facts'][0]['activity_status'] = 'ongoing'
    payload['sentences'][0]['activity_status'] = 'completed'
    assert 'declared_activity_status_mismatch' in codes(payload)
    payload['sentences'][0]['activity_status'] = 'unknown'
    assert checks(payload)['sentence_activity_status']['status'] == 'not_observable'


def test_dangling_fact_and_requirement_references_are_separate():
    payload = fixture()
    payload['sentences'][0]['fact_ids'] = ['absent']
    payload['requirements'][0]['sentence_ids'] = ['absent']
    assert {'unknown_fact_id', 'unknown_requirement_sentence_id',
            'required_annotation_has_no_existing_sentence'} <= codes(payload)
    assert checks(payload)['sentence_subject_alignment']['denominator'] == 0


def test_missing_requirement_annotation_does_not_claim_requirement_omission():
    payload = fixture()
    del payload['requirements'][0]['sentence_ids']
    assert checks(payload)['required_annotation_binding']['status'] == 'not_observable'
    payload['requirements'][0]['sentence_ids'] = []
    assert 'required_annotation_has_no_existing_sentence' in codes(payload)


def test_optional_or_inapplicable_requirement_not_treated_as_failure():
    payload = fixture()
    payload['requirements'] = [
        {'requirement_id': 'optional', 'required': False, 'sentence_ids': []},
        {'requirement_id': 'na', 'required': True, 'applicable': False, 'sentence_ids': []}]
    assert checks(payload)['required_annotation_binding']['not_applicable_count'] == 2
    assert not codes(payload)


def test_literal_binding_comparison_is_opt_in_not_a_paraphrase_detector():
    payload = fixture()
    payload['sentences'][0]['text'] = '자료를 확보할 예정입니다.'
    payload['text'] = payload['sentences'][0]['text']
    assert 'declared_literal_text_changed' in codes(payload)
    payload['sentences'][0].pop('literal_fact_binding')
    assert 'declared_literal_text_changed' not in codes(payload)


def test_unicode_source_offsets_and_hash_are_independent_checks():
    payload = fixture()
    payload['facts'][0]['source_ref']['quote'] = '없는 구절'
    payload['sources'][0]['sha256'] = 'bad'
    assert {'source_quote_mismatch', 'source_text_hash_mismatch'} <= codes(payload)
    payload['facts'][0]['source_ref']['char_start'] = -1
    assert 'invalid_source_span' in codes(payload)


def test_source_unavailable_is_not_an_invented_span_check():
    payload = fixture()
    del payload['sources']
    assert checks(payload)['fact_source_spans']['status'] == 'not_observable'
    assert checks(payload)['fact_source_spans']['denominator'] == 0


def test_bound_source_hash_compared_even_when_the_quote_matches():
    payload = fixture()
    payload['facts'][0]['source_ref']['text_sha256'] = 'wrong-version'
    assert 'bound_source_hash_mismatch' in codes(payload)


def test_false_state_and_explicit_as_of_are_preserved_without_inference():
    payload = fixture()
    fact, sentence = payload['facts'][0], payload['sentences'][0]
    fact.update(kind='state', value=False, as_of={'status': 'known', 'value': '2026-11-20'})
    sentence.update(claim_kind='state', state_value=False, as_of='2026-11-20')
    sentence.pop('literal_fact_binding')
    assert checks(payload)['sentence_state_value']['numerator'] == 0
    assert checks(payload)['sentence_state_value']['denominator'] == 1
    sentence['as_of'] = '2027-11-20'
    assert 'state_as_of_mismatch' in codes(payload)
    sentence['state_value'] = None
    assert checks(payload)['sentence_state_value']['status'] == 'not_observable'


def test_unknown_state_time_not_replaced_by_now():
    payload = fixture()
    payload['facts'][0].update(kind='state', as_of={'status': 'unknown', 'value': None})
    payload['sentences'][0].update(claim_kind='state', as_of='2026-11-20')
    assert checks(payload)['sentence_state_time']['status'] == 'not_observable'


def test_order_change_is_declared_order_diagnostic_not_coherence_score():
    payload = fixture()
    payload['paragraphs'].append({'paragraph_id': 'p2', 'sentence_ids': []})
    payload['outline_order']['expected_paragraph_ids'] = ['p2', 'p1']
    assert 'declared_outline_order_violated' in codes(payload)
    del payload['outline_order']
    assert checks(payload)['outline_order']['status'] == 'not_observable'


def test_orphan_sentence_and_dangling_claim_are_detected():
    payload = fixture()
    payload['paragraphs'][0]['sentence_ids'] = []
    payload['claim_ground_links'][0]['claim_sentence_id'] = 'absent'
    assert {'sentence_omitted_from_declared_paragraph', 'unknown_claim_sentence_id'} <= codes(payload)


def test_explicit_length_unicode_codepoints_and_page_unknown():
    payload = fixture()
    payload['constraints']['max_characters'] -= 1
    assert 'explicit_length_limit_exceeded' in codes(payload)
    assert checks(payload)['page_constraints']['status'] == 'not_observable'
    payload['constraints']['character_counting'] = 'bytes'
    assert checks(payload)['length_constraints']['denominator'] == 0


def test_cycle_is_graph_level_not_all_residual_nodes_are_called_cycles():
    payload = fixture()
    payload['tasks'] = [{'task_id': 'a', 'depends_on': ['b']},
                        {'task_id': 'b', 'depends_on': ['a']},
                        {'task_id': 'c', 'depends_on': ['b']}]
    assert 'dependency_cycle_detected' in codes(payload)
    check = checks(payload)['task_dependency_cycle']
    assert check['denominator'] == 1 and check['numerator'] == 1
    assert checks(payload)['task_dates']['denominator'] == 0


def test_unknown_dependency_withholds_acyclic_claim():
    payload = fixture()
    payload['tasks'][0]['depends_on'] = ['absent']
    assert 'unknown_dependency_task_id' in codes(payload)
    assert checks(payload)['task_dependency_cycle']['status'] == 'not_observable'


def test_dates_and_dependency_boundaries():
    payload = fixture()
    payload['tasks'].append({'task_id': 't2', 'depends_on': ['t1'],
                             'start': '2026-11-20', 'end': '2026-11-19'})
    assert {'dependency_finishes_after_task_start', 'task_end_precedes_start'} <= codes(payload)
    payload['tasks'][1]['start'] = '2026-02-30'
    assert 'invalid_iso_date' in codes(payload)


def test_date_vs_timestamp_and_naive_zone_are_unobserved():
    payload = fixture()
    payload['tasks'][0].update(start='2026-11-20', end='2026-11-21T00:00:00+09:00')
    assert checks(payload)['task_dates']['status'] == 'not_observable'
    payload['tasks'][0].update(start='2026-11-20T00:00:00', end='2026-11-21T00:00:00')
    assert checks(payload)['task_dates']['status'] == 'not_observable'


def test_timezone_equivalent_instants_compare_equal():
    payload = fixture()
    payload['tasks'][0].update(start='2026-11-20T09:00:00+09:00', end='2026-11-20T00:00:00Z')
    assert checks(payload)['task_dates']['numerator'] == 0
    assert checks(payload)['task_dates']['denominator'] == 1


def test_weighted_kappa_hand_calculation_is_11_over_16():
    result = quadratic_weighted_kappa([0, 0, 1, 1, 2], [0, 1, 1, 2, 2])
    assert result['quadratic_weighted_kappa'] == pytest.approx(11 / 16)
    assert result['exact_agreement'] == pytest.approx(3 / 5)
    assert result['confusion_matrix'] == [[1, 1, 0], [0, 1, 1], [0, 0, 1]]
    assert result['calculation_scope'] == 'supplied_numbers_not_authenticated_human_data'


def test_kappa_perfect_and_opposite_variable_ratings():
    assert quadratic_weighted_kappa([0, 1, 2], [0, 1, 2])['quadratic_weighted_kappa'] == 1
    assert quadratic_weighted_kappa([0, 1, 2], [2, 1, 0])['quadratic_weighted_kappa'] == -1


@pytest.mark.parametrize('a,b,reason', [
    ([None, 1], [0, None], 'insufficient_paired_units'),
    ([1, 1, 1], [1, 1, 1], 'constant_rater_distribution'),
    ([0, 0, 0], [0, 1, 2], 'constant_rater_distribution'),
])
def test_kappa_missing_or_constant_is_null(a, b, reason):
    result = quadratic_weighted_kappa(a, b)
    assert result['quadratic_weighted_kappa'] is None and result['reason'] == reason


def test_kappa_excludes_missing_pair_not_zero_imputing():
    result = quadratic_weighted_kappa([0, None, 2], [0, 1, 2])
    assert result['paired_units'] == 2 and result['excluded_missing_pairs'] == 1
    assert result['quadratic_weighted_kappa'] == 1


@pytest.mark.parametrize('a,b', [([True, 1], [0, 1]), ([0, 7], [0, 1]), ([0], [0, 1])])
def test_invalid_rating_arrays_rejected(a, b):
    with pytest.raises(ValueError):
        quadratic_weighted_kappa(a, b)


def test_fake_human_scores_on_synthetic_document_never_become_agreement():
    payload = fixture()
    payload['human_reviews'] = [{'reviewer_type': 'human', 'provenance': 'actual_human_review',
                                  'score': i % 3, 'review_record_id': 'invented',
                                  'criterion_id': 'coherence', 'unit_id': str(i // 2),
                                  'reviewer_id': str(i % 2), 'source_ref': {'evidence_id': 'invented'}}
                                 for i in range(6)]
    result = diagnose_writing(payload)['human_review_summary']
    assert result['status'] == 'not_measured' and result['criteria'] == {}
    assert result['reason'] == 'synthetic_document_cannot_establish_human_agreement'


def test_model_review_and_unreferenced_human_review_are_excluded():
    result = aggregate_human_reviews([
        {'reviewer_type': 'Codex', 'provenance': 'model_review', 'score': 2},
        {'reviewer_type': 'human', 'provenance': 'actual_human_review', 'score': 2}])
    assert result['eligible_records'] == 0
    assert len(result['excluded_records']) == 2
    assert result['criteria'] == {} and result['status'] == 'not_measured'


def test_aggregate_arithmetic_fixture_preserves_unverified_origin_and_missing_units():
    # These are fabricated numbers for testing grouping, not human observations.
    rows = []
    for unit, pair in enumerate([(0, 0), (0, 1), (1, 1), (1, 2), (2, 2), (None, 2)]):
        for reviewer, score in zip(('a', 'b'), pair):
            rows.append({'criterion_id': 'test-arithmetic', 'unit_id': str(unit), 'reviewer_id': reviewer,
                         'reviewer_type': 'human', 'provenance': 'actual_human_review',
                         'review_record_id': 'test-only', 'source_ref': {'evidence_id': 'test-only'},
                         'score': score})
    result = aggregate_human_reviews(rows)
    stats = result['criteria']['test-arithmetic']
    assert stats['quadratic_weighted_kappa'] == pytest.approx(11 / 16)
    assert stats['excluded_missing_pairs'] == 1 and stats['annotated_units'] == 6
    assert result['origin_authentication'] == 'declaration_and_reference_only_not_authenticated'
    # The same rows cannot establish human agreement for a declared fixture.
    assert aggregate_human_reviews(rows, document_provenance='synthetic_fixture')['criteria'] == {}


def test_aggregation_does_not_resolve_duplicate_or_third_rater_arbitrarily():
    row = {'criterion_id': 'test', 'unit_id': 'unit', 'reviewer_id': 'a',
           'reviewer_type': 'human', 'provenance': 'actual_human_review',
           'review_record_id': 'test-only', 'source_ref': {'evidence_id': 'test-only'}, 'score': 1}
    result = aggregate_human_reviews([row, deepcopy(row)])
    assert result['criteria']['test']['reason'] == 'duplicate_unit_rater_record'
    rows = [dict(row, reviewer_id=r) for r in ('a', 'b', 'c')]
    assert aggregate_human_reviews(rows)['criteria']['test']['reason'] == 'requires_exactly_two_raters'


def test_malformed_review_source_is_excluded_without_interpreting_it():
    result = aggregate_human_reviews([{'source_ref': 'not-an-object'}])
    assert result['excluded_records'][0]['reason'] == 'invalid_review_source_reference'


def test_duplicate_ids_rejected_instead_of_silently_overwritten():
    payload = fixture()
    payload['facts'].append(deepcopy(payload['facts'][0]))
    with pytest.raises(ValueError, match='unique'):
        diagnose_writing(payload)


def test_cli_preserves_raw_input_hash_and_never_fakes_measurement(tmp_path):
    inp, out = tmp_path / 'input.json', tmp_path / 'result.json'
    raw = json.dumps(fixture(), ensure_ascii=False).encode()
    inp.write_bytes(raw)
    assert main(['--input', str(inp), '--output', str(out)]) == 0
    result = json.loads(out.read_text())
    assert result['input_file_sha256'] == sha256(raw).hexdigest()
    assert result['human_review_summary']['status'] == 'not_measured'
    assert len(out.read_text()) < 10000
