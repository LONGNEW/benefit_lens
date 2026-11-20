"""Boundary/counterexample tests, not corpus precision or user eligibility."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_condition_values import (assess, compare_gpa, contract, normalize_degree,
                                         parse_candidate, tri_all, tri_any, tri_not)
from audit_benefitlens_rule_readiness import source_binding_reuse


def mapping(field, quote, evidence=None, **kwargs):
    return {'field_id': field, 'value_quote': quote, 'evidence_quote': evidence or quote or '',
            'operator': 'eq', 'value_extraction_method': 'literal_text', 'unit': None,
            'reference_phase': 'application', 'subject': 'applicant', 'modality': 'required',
            'statement_type': 'eligibility_requirement', 'logic_cues': [], 'ambiguities': [],
            'program_specific_key': None, **kwargs}


@pytest.mark.parametrize('quote,unit', [('2', 'years'), ('26 semester hours', 'semester hours'),
                                       ('two semesters', 'semesters'), ('1', 'year')])
def test_study_quantity_never_becomes_completion_boolean(quote, unit):
    result = assess(mapping('education.coursework_completed', quote,
                            'Must have completed ' + quote + ' of study', operator='gte', unit=unit))
    assert result['candidate'] is None
    assert 'quantity_is_not_boolean' in result['parse_holds']
    assert result['additional_type_proposals'][0]['value'] in (1, 2, 26)
    assert not result['execution_possible']


def test_completion_boolean_remains_valid_separately_from_duration():
    positive = assess(mapping('education.coursework_completed', 'completed all coursework'))
    negative = assess(mapping('education.coursework_completed', 'not completed coursework'))
    assert positive['candidate']['value'] is True
    assert negative['candidate']['value'] is False
    assert not positive['additional_type_proposals']


@pytest.mark.parametrize('text,expected', [('not full-time student', False),
    ('full-time or part-time student', None), ('not required to be full-time student', None)])
def test_boolean_negation_and_alternative_scope(text, expected):
    candidate = assess(mapping('education.full_time', text))['candidate']
    assert (candidate['value'] if candidate else None) is expected


@pytest.mark.parametrize('text', [None, 'unknown', 'Available for Work Visa Sponsorship?'])
def test_unknown_boolean_is_not_false(text):
    result = assess(mapping('work.visa_sponsorship', text))
    assert result['candidate'] is None
    assert not result['type_parse_success']


def test_original_spelled_one_is_parsed_without_digit_substitution_in_source():
    m = mapping('experience.years', 'one year', 'At least one year of experience', operator='gte', unit='year')
    original = json.dumps(m, sort_keys=True)
    result = assess(m)
    assert result['candidate']['value'] == 1
    assert json.dumps(m, sort_keys=True) == original


def test_unsupported_number_word_compound_is_not_partially_parsed():
    result = assess(mapping('experience.years', 'twenty-seven', 'twenty-seven years of experience', unit='years'))
    assert result['candidate'] is None


def test_negative_quantity_and_months_are_not_positive_years():
    negative = assess(mapping('experience.years', '-2', 'experience -2 years', unit='years'))
    months = assess(mapping('experience.years', '6', '6 months experience', unit='months'))
    assert negative['candidate'] is None
    assert months['candidate'] is None


@pytest.mark.parametrize('evidence', ['GDP is 3.0 percent', 'Annual salary is 3.0 million', '연봉 3.0억원'])
def test_wrong_numeric_field_is_held(evidence):
    result = assess(mapping('education.gpa', '3.0', evidence, unit='GPA'))
    assert result['candidate'] is None
    assert any('numeric_context' in x for x in result['parse_holds'])


def test_gpa_missing_scale_is_parseable_but_incomplete():
    result = assess(mapping('education.gpa', '3.0', 'minimum cumulative undergraduate GPA 3.0', unit='GPA'))
    assert result['candidate']['value'] == 3
    assert 'missing_attribute:scale' in result['local_attribute_holds']
    assert not result['required_attributes_complete_local']


def test_gpa_explicit_scale_and_incompatible_scales_remain_distinct():
    result = assess(mapping('education.gpa', '3.0/4.0', 'cumulative undergraduate GPA 3.0/4.0'))
    assert result['candidate']['value'] == 3
    assert result['candidate']['attributes']['scale'] == 4
    assert compare_gpa(3.2, 3.0, 4.3, 4.0) is None
    assert compare_gpa(3.2, 3.0, 4.0, 4.0) is True
    assert compare_gpa(3.2, 3.0, None, 4.0) is None


def test_gpa_threshold_above_explicit_scale_is_not_valid_candidate():
    result = assess(mapping('education.gpa', '5', 'undergraduate cumulative GPA 5 out of 4'))
    assert result['candidate'] is None


def test_money_currency_and_payment_period_not_inferred_from_dollar_symbol():
    bare = assess(mapping('benefit.amount', '$20 - $25', '$20 - $25', operator='between',
                          statement_type='benefit_description', modality='informational'))
    assert bare['candidate']['value'] == [20, 25]
    assert 'missing_attribute:currency' in bare['local_attribute_holds']
    assert 'missing_attribute:payment_period' in bare['local_attribute_holds']
    explicit = assess(mapping('benefit.amount', 'USD 20 - 25', 'USD 20 - 25 per hour', operator='between'))
    assert explicit['candidate']['attributes']['currency'] == 'USD'
    assert explicit['candidate']['attributes']['payment_period'] == 'hour'


def test_korean_amount_magnitude_is_preserved_as_krw_candidate():
    result = assess(mapping('benefit.amount', '100만원', '100만원 매월 지급'))
    assert result['candidate']['value'] == 1000000
    assert result['candidate']['attributes']['currency'] == 'KRW'
    assert result['candidate']['attributes']['payment_period'] == 'month'


def test_degree_namespace_is_explicit_and_professional_equivalence_held():
    assert normalize_degree('Ph.D.', 'shared_v2') == 'doctorate'
    assert normalize_degree('doctorate', 'demo') == 'phd'
    with pytest.raises(ValueError):
        normalize_degree('doctorate', 'automatic')
    result = assess(mapping('education.degree', 'Doctorate (PharmD) or equivalent Pharmacist degree'))
    assert result['candidate'] is None


def test_unreviewed_local_success_does_not_create_review_or_execution():
    result = assess(mapping('education.full_time', 'full-time student'))
    assert result['type_parse_success']
    assert result['required_attributes_complete_local']
    assert not result['human_meaning_review_match']
    assert not result['execution_possible']
    assert result['stored_semantic_review'] == 'unreviewed'


def test_preference_and_institution_condition_are_not_required_applicant_predicates():
    preferred = assess(mapping('education.full_time', 'full-time student', modality='preferred', statement_type='selection_preference'))
    institution = assess(mapping('application.nomination', 'nomination', subject='institution', statement_type='institution_requirement'))
    assert preferred['predicate_role'] == 'preference_only'
    assert institution['predicate_role'] == 'not_applicant_eligibility_predicate'


def test_number_parse_does_not_invent_and_or_scope():
    result = assess(mapping('person.age', '18', 'age 18 or enrolled', unit='year',
                            logic_cues=[{'kind': 'or', 'quote': 'or enrolled'}]))
    assert result['candidate']['value'] == 18
    assert result['logic']['tree'] is None
    assert 'scoped_logic_tree_missing' in result['local_attribute_holds']


def test_negated_status_is_not_positive_enrollment():
    result = assess(mapping('education.status', 'not enrolled'))
    assert result['candidate'] is None


def test_stored_source_binding_reuse_detects_reference_corruption():
    m = mapping('education.full_time', 'full-time student')
    binding = {'property': 'evidence_quote', 'document_id': 'd', 'text_sha256': 'a' * 64,
               'char_start': 10, 'char_end': 27, 'exact_source_quote': 'full-time student'}
    row = {**m, 'mapping_id': 'm', 'candidate_key': 'c', 'document_id': 'd', 'text_sha256': 'a' * 64}
    record = {'mapping_id': 'm', 'candidate_key': 'c', 'mapping': m, 'bindings': [binding]}
    assert source_binding_reuse(row, record, [binding]) == []
    corrupt = dict(binding, text_sha256='b' * 64)
    issues = source_binding_reuse(row, record, [corrupt])
    assert 'stored_binding_record_mismatch' in issues
    assert 'stored_binding_source_version_mismatch' in issues


@pytest.mark.parametrize('values,and_expected,or_expected', [([True, None], None, True),
    ([False, None], False, None), ([True, False], False, True), ([None, None], None, None),
    ([True, True], True, True), ([False, False], False, False)])
def test_three_valued_logic_counterexamples(values, and_expected, or_expected):
    assert tri_all(values) is and_expected
    assert tri_any(values) is or_expected


def test_not_unknown_and_reject_non_truth_values():
    assert tri_not(None) is None
    assert tri_not(False) is True
    with pytest.raises(ValueError):
        tri_all([0])


def test_new_fields_are_proposals_outside_operational_nineteen():
    c = contract()
    assert len(c['field_definitions']) == 19
    assert 'education.coursework_completed' in c['field_definitions']
    assert 'education.study_duration' not in c['field_definitions']
    assert c['proposed_additions']['education.study_duration']['not_equivalent_to'] == 'education.coursework_completed'
