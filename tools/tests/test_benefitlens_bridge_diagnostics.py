"""Structural adapter tests; no human or semantic quality observations."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_bridge_diagnostics import adapt_bridge_to_diagnostics, diagnose_bridge, load_saved_bridge


def fixture():
    text = '기록을 비교하겠습니다.'
    original = {'provenance': 'synthetic_fixture', 'raw_experiences': [
        {'experience_id': 'e', 'subject_id': 'p', 'text': text}]}
    result = {'compiler_task': {'task_id': 't', 'subject_id': 'p', 'experience_id': 'e',
              'requirements': [{'id': 'R1', 'required': True}]}, 'draft_text_preview': text,
        'compiler_facts': [{'fact_id': 'F1', 'subject_id': 'p', 'experience_id': 'e', 'kind': 'future_plan',
             'activity_status': 'planned', 'status': 'confirmed', 'value': text,
             'source_ref': {'source_sha256': sha256(text.encode()).hexdigest(),
                            'source_span': {'start': 0, 'end': len(text), 'quote': text}}}],
        'architecture': {'effective_constraints': {'max_characters': 100}, 'sections': [
            {'section_id': 's', 'paragraphs': [{'paragraph_id': 'p1', 'sentences': [
                {'sentence_id': 's1', 'text': text, 'fact_ids': ['F1'], 'experience_id': 'e',
                 'fact_kind': 'future_plan', 'activity_status': 'planned',
                 'inference_status': 'literal_input_statement'}]}]}]}}
    return original, result


def test_structural_copy_without_adding_missing_semantic_annotations():
    original, bridge = fixture()
    before = deepcopy((original, bridge))
    payload = adapt_bridge_to_diagnostics(original, bridge)
    assert payload['requirements'][0]['sentence_ids'] is None
    assert payload['outline_order'] is None and payload['tasks'] is None
    assert payload['claim_ground_links'] is None
    assert payload['annotation_provenance']['added_semantic_annotations'] == 0
    assert (original, bridge) == before


def test_projection_executes_source_and_reference_checks_but_not_quality():
    result = diagnose_bridge(*fixture())
    checks = {c['check_id']: c for c in result['checks']}
    assert checks['fact_source_spans']['denominator'] == 1
    assert checks['fact_source_spans']['numerator'] == 0
    assert checks['required_annotation_binding']['status'] == 'not_observable'
    assert checks['outline_order']['status'] == 'not_observable'
    assert result['semantic_quality_scores'] is None
    assert result['human_review_summary']['status'] == 'not_measured'


def test_existing_activity_metadata_mismatch_is_not_repaired_by_adapter():
    original, bridge = fixture()
    bridge['architecture']['sections'][0]['paragraphs'][0]['sentences'][0]['activity_status'] = 'completed'
    result = diagnose_bridge(original, bridge)
    assert any(f['code'] == 'declared_activity_status_mismatch' for c in result['checks'] for f in c['findings'])


def test_no_preview_stays_none_and_cannot_be_marked_ready():
    original, bridge = fixture()
    bridge['draft_text_preview'] = None
    result = diagnose_bridge(original, bridge)
    checks = {c['check_id']: c for c in result['checks']}
    assert checks['length_constraints']['status'] == 'not_observable'
    assert result['automatic_quality_acceptance'] is False


def test_missing_sentence_state_not_filled_from_fact():
    original, bridge = fixture()
    bridge['compiler_facts'][0].update(kind='state', value=False, as_of='2026-11-20')
    bridge['architecture']['sections'][0]['paragraphs'][0]['sentences'][0]['fact_kind'] = 'state'
    payload = adapt_bridge_to_diagnostics(original, bridge)
    assert 'as_of' not in payload['sentences'][0] and 'state_value' not in payload['sentences'][0]


def test_saved_bundle_rejects_input_from_another_execution(tmp_path):
    from benefitlens_writing_material_bridge import fingerprint
    original, _ = fixture()
    path = tmp_path / 'input.json'
    path.write_text(json.dumps(original))
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    (bundle / 'input_fingerprint.json').write_text(json.dumps(fingerprint(original)))
    other = deepcopy(original)
    other['raw_experiences'][0]['text'] = '다른 실행의 원문입니다.'
    path.write_text(json.dumps(other))
    with pytest.raises(ValueError, match='bridge_input_fingerprint_mismatch'):
        load_saved_bridge(path, bundle)
