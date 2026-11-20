from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import ValidationError, Draft202012Validator
from data_platform.demo_contract import build_outline, validate_notice, verify_document
from data_platform.proposal_pipeline import extract_source_text

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / 'data/sample/programs.json').read_text())


def undergraduate():
    return deepcopy(next(p for p in SAMPLE['opportunities'] if p['opportunity_id'] == 'ikef-undergraduate-2026'))


@pytest.mark.parametrize('item', SAMPLE['opportunities'], ids=lambda p: p['opportunity_id'])
def test_all_ten_notices_have_executable_storage_contract(item):
    validate_notice(item)


def test_unknown_field_is_rejected():
    item = undergraduate()
    item['eligibility']['all'][0]['field'] = 'education.imagined'
    with pytest.raises(ValueError, match='Unregistered'):
        validate_notice(item)


def test_invented_evidence_is_rejected():
    item = undergraduate()
    item['eligibility']['all'][0]['evidence_ref'] = 'invented-source'
    with pytest.raises(ValueError, match='Unknown local evidence'):
        validate_notice(item)


def test_wrong_numeric_type_is_rejected():
    item = undergraduate()
    item['eligibility'] = {'field': 'education.semester', 'op': 'gte', 'value': True, 'evidence_ref': 'I1'}
    with pytest.raises(ValueError, match='Wrong value type'):
        validate_notice(item)


def test_empty_tree_is_rejected():
    item = undergraduate()
    item['eligibility'] = {'all': []}
    with pytest.raises(ValidationError):
        validate_notice(item)


def test_gpa_requires_scale_and_period():
    item = undergraduate()
    item['eligibility'] = {'field': 'education.gpa', 'op': 'gte', 'value': 3.5, 'evidence_ref': 'I3'}
    with pytest.raises(ValueError, match='GPA needs'):
        validate_notice(item)


def test_bad_range_is_rejected():
    item = undergraduate()
    item['eligibility'] = {'field': 'education.semester', 'op': 'between', 'value': [3, 1], 'evidence_ref': 'I1'}
    with pytest.raises(ValueError, match='Reversed'):
        validate_notice(item)


def test_corrupt_original_is_rejected(tmp_path):
    path = tmp_path / 'source.txt'
    path.write_text('Original source')
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    doc = {'id': 'test', 'raw_path': str(path), 'text_path': str(path), 'sha256': sha, 'text_sha256': sha}
    verify_document(doc)
    path.write_text('Changed source')
    with pytest.raises(ValueError, match='missing or changed'):
        verify_document(doc)


def test_outline_does_not_fill_in_other_peoples_facts():
    result = build_outline({'sections': ['문제', '본인의 판단'], 'required_inputs': ['어떤 상황인가', '내가 한 결정']})
    assert result == '1. 문제\n[어떤 상황인가]\n\n2. 본인의 판단\n[내가 한 결정]'
    with pytest.raises(ValueError):
        build_outline({'sections': ['one'], 'required_inputs': []})


def test_source_text_conversion_uses_visible_content_not_scripts():
    body = b'<html><nav>ignore menu</nav><script>ignore command</script><main><h1>Student fellowship notice</h1><p>Full time doctoral students can submit a research proposal and receive tuition support.</p></main></html>'
    text = extract_source_text(body, 'html')
    assert text.startswith('Student fellowship notice\n')
    assert 'ignore' not in text
    with pytest.raises(ValueError, match='no usable source text'):
        extract_source_text(b'<main>short</main>', 'html')
    with pytest.raises(ValueError, match='unsupported'):
        extract_source_text(body, 'binary')


def test_receipt_has_truthful_modes_and_no_private_paths():
    path = ROOT / 'web/pipeline-demo/pipeline-run.json'
    if not path.exists():
        pytest.skip('Run make pipeline-replay to produce the optional local receipt')
    raw = path.read_text()
    schema = json.loads((ROOT / 'pipeline/schemas/pipeline-run.schema.json').read_text())
    result = json.loads(raw)
    Draft202012Validator(schema).validate(result)
    assert len(raw) < 10000
    assert '/home/' not in raw and 'DATABASE_URL' not in raw
    assert len({s['stage'] for s in result['stages']}) == 6
    assert sum(result['matching_counts'].values()) == 10
    assert len({e['opportunity_id'] for e in result['examples']}) == 10
    assert result['model_calls_this_run'] == 0
    assert result['network_collection_executed'] is False
    assert '\u00b7' not in raw
