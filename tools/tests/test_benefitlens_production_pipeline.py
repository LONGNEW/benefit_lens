"""Operational checks for sharing a real condition contract across teams."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_benefitlens_production_pipeline import validate_condition_rows, validate_personalization_rows


def catalog():
    return {'fields': {'education.degree': {}}, 'condition_contract': {
        'status_values': ['candidate'], 'required': ['condition_id', 'candidate_key',
            'field_id', 'operator', 'value', 'value_type', 'unit', 'at', 'evidence', 'status', 'unknown_reasons']}}


def condition():
    return {'condition_id': 'source-bound-mention', 'candidate_key': 'real-notice',
        'field_id': 'education.degree', 'operator': 'unknown', 'value': 'phd', 'value_type': 'enum',
        'unit': None, 'at': 'unknown', 'evidence': [], 'status': 'candidate',
        'unknown_reasons': ['mention_does_not_establish_required_degree']}


def test_personalization_cannot_use_a_different_definition_of_the_common_field():
    with pytest.raises(ValueError, match='fingerprints differ'):
        validate_personalization_rows([{'candidate_key': 'real-notice', 'catalog_sha256': 'other-catalog'}], 'notice-catalog')


def test_demo_rows_are_not_counted_as_production_personalization():
    with pytest.raises(ValueError, match='Demo or synthetic'):
        validate_personalization_rows([{'candidate_key': 'demo', 'catalog_sha256': 'shared', 'record_type': 'sample'}], 'shared')


def test_a_word_mention_cannot_be_promoted_to_a_reviewed_condition_by_the_coordinator():
    row = condition()
    row['status'] = 'accepted'
    with pytest.raises(ValueError, match='semantic approval'):
        validate_condition_rows([row], catalog())


def test_unknown_scope_reasons_survive_the_cross_team_projection():
    result = validate_condition_rows([condition()], catalog())
    assert result['unknown_reason_counts'] == {'mention_does_not_establish_required_degree': 1}
    assert result['field_counts'] == {'education.degree': 1}


def test_consumer_field_alias_cannot_silently_form_a_second_condition_dictionary():
    row = condition()
    row['field_id'] = 'academicLevel'
    with pytest.raises(ValueError, match='outside the shared catalog'):
        validate_condition_rows([row], catalog())
