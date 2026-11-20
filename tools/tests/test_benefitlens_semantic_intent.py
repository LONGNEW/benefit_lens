from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_semantic_intent import text_hash, validate_observations, compare_intents


def sample():
    text = 'Tell us about your work.\nI led the migration and tested the result.'
    bundle = {'documents': [{'document_id': 'd', 'text': text, 'text_sha256': text_hash(text),
                             'publisher_role': 'personal', 'source_role': 'participation_review', 'url': 'https://example.org/x'}],
              'tasks': [{'task_id': 't', 'document_id': 'd', 'question_text': 'Tell us about your work.',
                         'question_kind': 'review_question', 'context_start': 0, 'context_end': len(text),
                         'declared_use': 'experience_review'}]}
    row = {'task_id': 't', 'reader_question': '本人の行動を区別できるか',
           'purpose_status': 'semantic_interpretation', 'answer_use': 'experience_review',
           'temporal_scope': 'past', 'axes': ['own_agency'],
           'evidence_demands': [{'role': 'own_action', 'description': '本人の担当行動', 'basis': 'interpretation'}],
           'source_evidence': [{'document_id': 'd', 'quote': 'I led the migration and tested the result.'}],
           'reasoning': '担当と行動を尋ねる。', 'alternative_readings': [], 'missing_context': [],
           'answer_strategy': {'status': 'proposed', 'experience_selection': ['本人の選択'],
                               'logical_links': ['責任と実行'], 'opening_function': '担当',
                               'closing_function': '結果', 'scope_conditions': []}}
    return bundle, {'version': 'semantic-writing-intent-v1', 'observations': [row]}


def test_new_meaning_axis_is_retained_without_six_type_coercion():
    b, r = sample();r['observations'][0]['axes'].append('uncertainty_calibration')
    row = validate_observations(r, b)['observations'][0]
    assert row['new_axis_candidates'] == ['uncertainty_calibration']
    assert row['semantic_review'] == 'unreviewed' and row['approved_pattern'] is False


def test_quote_normalization_keeps_original_offsets_and_full_hash():
    b, r = sample();b['documents'][0]['text'] = b['documents'][0]['text'].replace('migration and', 'migration\n  and')
    text = b['documents'][0]['text'];b['documents'][0]['text_sha256'] = text_hash(text)
    b['tasks'][0]['context_end'] = len(text)
    span = validate_observations(r, b)['observations'][0]['source_evidence'][0]
    assert text[span['char_start']:span['char_end']] == span['exact_quote']


def test_statistics_do_not_acquire_a_selection_objective():
    b, r = sample();b['tasks'][0]['declared_use'] = 'data_collection'
    with pytest.raises(ValueError, match='declared source use'):
        validate_observations(r, b)
    row = r['observations'][0];row['answer_use'] = 'data_collection'
    with pytest.raises(ValueError, match='statistical data'):
        validate_observations(r, b)


def test_fact_only_request_has_no_forced_opening_or_closing():
    b, r = sample();b['tasks'][0]['declared_use'] = 'data_collection'
    row = r['observations'][0];row.update(answer_use='data_collection', purpose_status='declared_non_selection', axes=[])
    row['answer_strategy'].update(status='not_needed', logical_links=[], opening_function=None, closing_function=None)
    assert validate_observations(r, b)['observations'][0]['axes'] == []
    row['answer_strategy']['closing_function'] = 'Show why you deserve support'
    with pytest.raises(ValueError, match='forced essay'):
        validate_observations(r, b)


def test_official_explanation_cannot_be_claimed_for_a_community_or_review_source():
    b, r = sample();r['observations'][0]['purpose_status'] = 'explicit_guidance'
    with pytest.raises(ValueError, match='official-program'):
        validate_observations(r, b)


def test_missing_task_and_foreign_source_quotes_fail():
    b, r = sample();r['observations'][0]['task_id'] = 'other'
    with pytest.raises(ValueError, match='Every input task'):
        validate_observations(r, b)
    b, r = sample();r['observations'][0]['source_evidence'][0]['document_id'] = 'foreign'
    with pytest.raises(ValueError, match='different task source'):
        validate_observations(r, b)


def test_quote_must_be_inside_the_actual_task_context():
    b, r = sample();b['tasks'][0]['context_end'] = 24
    with pytest.raises(ValueError, match='absent or ambiguous'):
        validate_observations(r, b)


def test_same_axis_does_not_erase_different_evidence_demands():
    b, r = sample();a = r['observations'][0];z = deepcopy(a);z['task_id'] = 'z'
    z['evidence_demands'].append({'role': 'sustained_relationship', 'description': 'relationship maintenance', 'basis': 'interpretation'})
    out = compare_intents(a, z)
    assert out['relation'] == 'partial_overlap' and out['right_only_demands'] == ['sustained_relationship']


def test_matching_labels_are_only_a_candidate_for_context_review():
    _, r = sample();a = r['observations'][0];z = deepcopy(a);z['task_id'] = 'z'
    assert compare_intents(a, z)['relation'] == 'same_dimensions_candidate'
    z['temporal_scope'] = 'future'
    assert compare_intents(a, z)['relation'] == 'partial_overlap'
