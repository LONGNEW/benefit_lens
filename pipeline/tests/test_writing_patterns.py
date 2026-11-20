import hashlib
import json
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from data_platform.writing_patterns import (
    extract, make_prompt, prepare_program, validate_result, knowledge_from_result,
    PROMPT_VERSION, LEGACY_PROMPT_VERSION, scope_fingerprint, fingerprint, _legacy_context, validate_cached_receipt,
)
from data_platform.demo_scope import require_collection_enabled


def test_current_scope_refuses_new_collection():
    with pytest.raises(ValueError, match='Collection is paused'):
        require_collection_enabled()


def context():
    prepared = {'program_id': 'gates-cambridge', 'target_round': '2026-2027',
            'prompt_version': PROMPT_VERSION,
            'cases': [{'case_id': 'C1'}], 'current_application': {},
            'documents': [{'source_id': 'essay', 'case_ids': ['C1'], 'role': 'application_statement',
                           'url': 'https://example.org/essay', 'document_id': 'doc',
                           'content_sha256': 'raw-hash', 'text_sha256': 'text-hash',
                           'text': 'I changed the lesson after observing the students.',
                           'submission_finality': 'unknown', 'source_round': None, 'prompt_id': None}]}
    prepared['input_scope_fingerprint'] = scope_fingerprint(prepared)
    return prepared


def result(prepared=None):
    prepared = prepared or context()
    return {'program_id': 'gates-cambridge',
            'input_scope_fingerprint': scope_fingerprint(prepared),
            'comparison_decision': {'status': 'not_applicable', 'reason': 'One known case only.'},
            'document_analyses': [{'source_id': 'essay', 'summary': '행동의 수정', 'quote': 'I changed the lesson'}],
            'case_analyses': [{'case_id': 'C1', 'fragments': [
                {'role': 'action', 'summary': '관찰 후 수업을 수정했다.', 'source_id': 'essay', 'quote': 'after observing the students.'}]}],
            'writing_pattern': [{'pattern_id': 'action', 'insight': '관찰과 행동의 수정을 연결한다.',
                                 'sections': ['관찰', '행동 수정'], 'required_inputs': ['관찰한 문제', '바꾼 행동'],
                                 'supporting_case_ids': ['C1'], 'variations': [],
                                 'observation_scope': 'complete_text', 'scope_limit': 'Public text with unverified finality.',
                                 'evidence': [{'case_id': 'C1', 'source_id': 'essay', 'quote': 'I changed the lesson'}]}],
            'criterion_observations': [], 'outcome_comparisons': []}


def test_prepare_feeds_the_entire_original_not_old_authored_patterns(tmp_path):
    sample = json.loads((Path(__file__).resolve().parents[2]/'data/sample/programs.json').read_text())
    old = next(k for k in sample['program_knowledge'] if k['program_id'] == 'gates-cambridge')
    old['writing_pattern'] = [{'insight': 'OLD_PATTERN_MUST_NOT_BE_INPUT'}]
    old['experience_cases'][0]['fragments'] = [{'summary': 'OLD_FRAGMENT_MUST_NOT_BE_INPUT'}]
    next(p for p in sample['opportunities'] if p['program_id']=='gates-cambridge')['application']['link_verification']={'checked_at':'LINK_CHECK_ONLY_METADATA'}
    text = 'Beginning\n' + ('complete original paragraph\n' * 2000) + 'END_OF_COMPLETE_ORIGINAL'
    path = tmp_path/'essay.txt'; path.write_text(text)
    collection = {'documents': [{'id': 'essay', 'status': 'collected', 'text_path': str(path),
                                'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                                'sha256': 'raw-hash', 'document_id': 'doc', 'final_url': 'https://example.org/essay'}]}
    sources = {'sources': [{'id': 'essay', 'program_id': 'gates-cambridge',
                           'role': 'application_statement', 'experience_case_ids': ['C1']}]}
    prepared = prepare_program(sample, collection, sources, 'gates-cambridge')
    prompt = make_prompt(prepared, 500000)
    assert prepared['documents'][0]['text'] == text
    assert 'END_OF_COMPLETE_ORIGINAL' in prompt
    assert 'OLD_PATTERN_MUST_NOT_BE_INPUT' not in prompt
    assert 'OLD_FRAGMENT_MUST_NOT_BE_INPUT' not in prompt
    assert 'LINK_CHECK_ONLY_METADATA' not in prompt
    with pytest.raises(ValueError, match='no text was truncated'):
        make_prompt(prepared, 100)
    collection['documents'][0]['status'] = 'failed'
    with pytest.raises(ValueError, match='unavailable'):
        prepare_program(sample, collection, sources, 'gates-cambridge')


@pytest.mark.parametrize('damage', ['quote', 'missing_document', 'unknown_case', 'section_alignment'])
def test_model_output_cannot_drop_documents_or_invent_evidence(damage):
    output = result()
    if damage == 'quote': output['case_analyses'][0]['fragments'][0]['quote'] = 'Invented achievement'
    if damage == 'missing_document': output['document_analyses'] = []
    if damage == 'unknown_case': output['writing_pattern'][0]['supporting_case_ids'] = ['invented']
    if damage == 'section_alignment': output['writing_pattern'][0]['required_inputs'] = []
    with pytest.raises(ValueError): validate_result(output, context())


def test_case_cannot_borrow_another_persons_document():
    prepared = context()
    prepared['documents'][0]['case_ids'] = ['another-person']
    with pytest.raises(ValueError, match='another person'):
        validate_result(result(prepared), prepared)


def test_model_receipt_and_validated_cache(tmp_path):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        assert '--sandbox' in args and args[args.index('--sandbox')+1] == 'read-only'
        assert 'I changed the lesson after observing the students.' in kwargs['input']
        Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(result()))
        return SimpleNamespace(returncode=0, stderr='model: configured-model\n')
    receipt = extract(context(), tmp_path, runner=run)
    assert receipt['input_document_count'] == 1
    assert receipt['network_collection_executed'] is False
    assert extract(context(), tmp_path, runner=run) == receipt
    assert len(calls) == 1
    previous = {'program_id': 'gates-cambridge', 'experience_cases': [
        {'case_id': 'C1', 'application_round': '2017', 'source_refs': [{'id': 'official-outcome', 'url': 'https://example.org/outcome'}]}]}
    knowledge = knowledge_from_result(previous, context(), receipt)
    assert knowledge['extractor'] == 'llm_full_experience_v3'
    assert knowledge['writing_pattern'][0]['basis'] == 'single_case_observation'
    assert knowledge['writing_pattern'][0]['causal_success_claim'] is False
    assert knowledge['experience_cases'][0]['source_refs'][0]['id'] == 'official-outcome'
    assert knowledge['experience_cases'][0]['source_refs'][1]['anchor'] == 'after observing the students.'


def test_invalid_quote_is_returned_to_model_without_truncating_original(tmp_path):
    calls = []
    def run(args, **kwargs):
        calls.append(kwargs['input'])
        candidate = result()
        if len(calls) == 1:
            candidate['case_analyses'][0]['fragments'][0]['quote'] = 'invented quote'
        Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(candidate))
        return SimpleNamespace(returncode=0, stderr='model: configured-model\n')
    receipt = extract(context(), tmp_path, runner=run)
    assert receipt['model_attempts'] == 2
    assert 'invented quote' in calls[1]
    assert all('I changed the lesson after observing the students.' in p for p in calls)
    assert list(tmp_path.glob('*-rejected-*.json'))


def comparison_fixture():
    prepared, output = context(), result()
    prepared['evaluation_criteria'] = [{'criterion_id': 'leadership'}]
    prepared['cases'] = [{'case_id': 'C1', 'outcome_status': 'selected'},
                         {'case_id': 'C2', 'outcome_status': 'not_selected'}]
    prepared['documents'].append({**prepared['documents'][0], 'source_id': 'other', 'case_ids': ['C2']})
    output['document_analyses'].append({**output['document_analyses'][0], 'source_id': 'other'})
    output['case_analyses'].append({'case_id': 'C2', 'fragments': [
        {**output['case_analyses'][0]['fragments'][0], 'source_id': 'other'}]})
    proof = [{'case_id': case, 'source_id': source, 'quote': 'I changed the lesson'}
             for case, source in [('C1', 'essay'), ('C2', 'other')]]
    output['criterion_observations'] = [{'case_id': p['case_id'], 'criterion_id': 'leadership',
        'claim': '수업 변경', 'personal_action': '수업 수정', 'result_evidence': '미확인',
        'institution_fit': '미확인', 'limitations': '결과는 미확인이다.', 'evidence': [p]} for p in proof]
    output['outcome_comparisons'] = [{'criterion_id': 'leadership', 'observation': '동일한 행동을 서술한다.',
        'supporting_case_ids': ['C1', 'C2'], 'comparison_scope': 'limited_textual_contrast',
        'evidence': proof, 'limitations': '심사 원인은 미확인이다.',
        'user_questions': ['수업을 바꾼 후 관찰한 변화는 무엇인가?']}]
    prepared['input_scope_fingerprint'] = scope_fingerprint(prepared)
    output['input_scope_fingerprint'] = scope_fingerprint(prepared)
    output['comparison_decision'] = {'status': 'observed', 'reason': 'Public text only; finality and round unknown.'}
    return prepared, output


@pytest.mark.parametrize('damage', ['one_group_only', 'wrong_owner', 'missing_case', 'ambiguous_outcome'])
def test_outcome_comparison_cannot_invent_group_evidence(damage):
    prepared, output = comparison_fixture()
    validate_result(output, prepared)
    if damage == 'one_group_only': output['outcome_comparisons'][0]['evidence'].pop()
    if damage == 'wrong_owner': output['criterion_observations'][0]['evidence'][0]['source_id'] = 'other'
    if damage == 'missing_case': output['criterion_observations'].pop()
    if damage == 'ambiguous_outcome': prepared['cases'][1]['outcome_status'] = 'ambiguous'
    with pytest.raises(ValueError): validate_result(output, prepared)


def test_mixed_outcomes_can_abstain_with_a_concrete_reason():
    prepared, output = comparison_fixture()
    output['outcome_comparisons'] = []
    output['comparison_decision'] = {'status': 'no_comparable_pair',
        'reason': 'Public documents have different unknown prompt and round bindings.'}
    validate_result(output, prepared)
    output['comparison_decision']['reason'] = ''
    with pytest.raises(ValueError, match='reason'):
        validate_result(output, prepared)


@pytest.mark.parametrize('field,value', [('role', 'official_interview'),
    ('submission_finality', 'public_draft'), ('source_round', '2025'), ('prompt_id', 'different')])
def test_scope_relabel_invalidates_previous_model_result(field, value):
    prepared, output = comparison_fixture()
    prepared['documents'][0][field] = value
    with pytest.raises(ValueError, match='scope changed'):
        validate_result(output, prepared)


def test_recomputed_scope_cannot_promote_interview_to_statement_pattern():
    prepared, output = comparison_fixture()
    prepared['documents'][0]['role'] = 'official_interview'
    output['input_scope_fingerprint'] = scope_fingerprint(prepared)
    with pytest.raises(ValueError, match='actual document role'):
        validate_result(output, prepared)


def test_relabeling_pattern_as_interview_still_does_not_create_submission_comparison():
    prepared, output = comparison_fixture()
    prepared['documents'][0]['role'] = 'official_interview'
    output['writing_pattern'][0]['observation_scope'] = 'interview'
    output['input_scope_fingerprint'] = scope_fingerprint(prepared)
    with pytest.raises(ValueError, match='Only public application statements'):
        validate_result(output, prepared)


def test_unknown_finality_allows_limited_text_but_not_final_submission_comparison():
    prepared, output = comparison_fixture()
    validate_result(output, prepared)
    output['outcome_comparisons'][0]['comparison_scope'] = 'same_prompt_final_statements'
    with pytest.raises(ValueError, match='verified finality'):
        validate_result(output, prepared)
    for doc in prepared['documents']:
        doc.update(submission_finality='confirmed_final', source_round='2025', prompt_id='leadership')
    output['input_scope_fingerprint'] = scope_fingerprint(prepared)
    validate_result(output, prepared)
    prepared['documents'][1]['prompt_id'] = 'research'
    output['input_scope_fingerprint'] = scope_fingerprint(prepared)
    with pytest.raises(ValueError, match='same round and prompt'):
        validate_result(output, prepared)


def test_legacy_v2_cache_remains_readable_but_is_not_scope_approved(tmp_path):
    prepared = context()
    legacy = _legacy_context(prepared)
    old_result = result()
    for field in ['input_scope_fingerprint', 'comparison_decision']:
        old_result.pop(field)
    for field in ['observation_scope', 'scope_limit', 'evidence']:
        old_result['writing_pattern'][0].pop(field)
    saved = {'prompt_version': LEGACY_PROMPT_VERSION, 'extractor': 'llm_full_experience_v2',
             'input_sha256': fingerprint(legacy), 'result': old_result}
    path = tmp_path / ('gates-cambridge-' + fingerprint(legacy)[:16] + '.json')
    original = json.dumps(saved)
    path.write_text(original)
    def no_model(*args, **kwargs):
        pytest.fail('Legacy read must not invoke a model')
    receipt = extract(prepared, tmp_path, runner=no_model)
    assert receipt['compatibility']['approved_handoff'] is False
    assert receipt['compatibility']['state'] == 'legacy_v2_requires_scope_review'
    assert path.read_text() == original
    relabeled = deepcopy(prepared)
    relabeled['documents'][0]['submission_finality'] = 'confirmed_final'
    with pytest.raises(ValueError, match='does not bind'):
        validate_cached_receipt(saved, relabeled)
    relabeled = deepcopy(prepared)
    relabeled['documents'][0]['role'] = 'official_interview'
    with pytest.raises(ValueError, match='input binding changed'):
        validate_cached_receipt(saved, relabeled)


def test_index_only_inputs_can_report_no_pattern_instead_of_inventing_structure():
    prepared, output = context(), result()
    prepared['documents'][0]['role'] = 'experience_index'
    output['writing_pattern'] = []
    output['input_scope_fingerprint'] = scope_fingerprint(prepared)
    validate_result(output, prepared)


def cli_helpers():
    # Import helpers only: neither CLI main nor the DB-backed replay is run.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
    from run_pipeline_demo import revalidate_saved_pattern_run, validate_sample_knowledge
    from extract_patterns import receipt_version_summary
    return revalidate_saved_pattern_run, validate_sample_knowledge, receipt_version_summary


def legacy_replay_fixture():
    prepared = context()
    old_result = result()
    for field in ['input_scope_fingerprint', 'comparison_decision']:
        old_result.pop(field)
    for field in ['observation_scope', 'scope_limit', 'evidence']:
        old_result['writing_pattern'][0].pop(field)
    saved = {'prompt_version': LEGACY_PROMPT_VERSION, 'extractor': 'llm_full_experience_v2',
             'input_sha256': fingerprint(_legacy_context(prepared)), 'result': old_result,
             'model': 'preserved-fixture-model', 'created_at': '2026-10-15T00:00:00Z'}
    previous = {'program_id': prepared['program_id'], 'experience_cases': [
        {'case_id': 'C1', 'application_round': '2017', 'source_refs': []}]}
    knowledge = knowledge_from_result(previous, prepared, saved)
    knowledge.pop('comparison_decision')
    knowledge['pattern_extraction'].pop('compatibility')
    for pattern in knowledge['writing_pattern']:
        pattern.pop('scope_validation')
    return prepared, saved, knowledge


def test_replay_reads_legacy_cache_without_model_or_sample_mutation(tmp_path):
    revalidate, _, _ = cli_helpers()
    prepared, saved, knowledge = legacy_replay_fixture()
    before = deepcopy(knowledge)
    path = tmp_path / (prepared['program_id'] + '-' + saved['input_sha256'][:16] + '.json')
    original = json.dumps(saved)
    path.write_text(original)
    run = revalidate(knowledge, prepared, tmp_path)
    assert run['extractor'] == 'llm_full_experience_v2'
    assert run['scope_validation'] == 'legacy_v2_requires_scope_review'
    assert run['input_sha256'] == saved['input_sha256']
    assert run['prepared_context_sha256'] == fingerprint(prepared)
    assert run['model_called_this_run'] is False and run['approved_handoff'] is False
    assert set(run['compatibility_labels_added']) == {
        'comparison_decision', 'pattern_extraction.compatibility', 'writing_pattern[0].scope_validation'}
    assert knowledge == before and path.read_text() == original
    path.unlink()
    with pytest.raises(FileNotFoundError):
        revalidate(knowledge, prepared, tmp_path)


@pytest.mark.parametrize('damage', ['insight', 'source_ref', 'provenance', 'approval_label'])
def test_legacy_replay_compatibility_does_not_ignore_changed_semantics(damage):
    _, validate, _ = cli_helpers()
    prepared, saved, knowledge = legacy_replay_fixture()
    if damage == 'insight':
        knowledge['writing_pattern'][0]['insight'] = 'Invented success rule'
    if damage == 'source_ref':
        knowledge['experience_cases'][0]['source_refs'][0]['anchor'] = 'Invented quote'
    if damage == 'provenance':
        knowledge['pattern_extraction']['extractor'] = 'llm_full_experience_v3'
    if damage == 'approval_label':
        knowledge['writing_pattern'][0]['scope_validation'] = 'v3_scope_bound'
    with pytest.raises(ValueError, match='beyond compatibility labels'):
        validate(knowledge, prepared, saved)


def test_manifest_reports_actual_returned_versions_instead_of_current_constant():
    _, _, summarize = cli_helpers()
    v2 = {'prompt_version': LEGACY_PROMPT_VERSION, 'extractor': 'llm_full_experience_v2', 'input_sha256': 'a'*64}
    v3 = {'prompt_version': PROMPT_VERSION, 'extractor': 'llm_full_experience_v3', 'input_sha256': 'b'*64}
    old = summarize([('old-program', v2)])
    assert old['pattern_extractor'] == 'llm_full_experience_v2'
    assert old['pattern_scope_states'] == {'legacy_v2_requires_scope_review': 1}
    mixed = summarize([('old-program', v2), ('new-program', v3)])
    assert mixed['pattern_extractor'] == 'mixed'
    assert mixed['pattern_extractors'] == ['llm_full_experience_v2', 'llm_full_experience_v3']
    assert all(r['approved_handoff'] is False for r in mixed['pattern_extraction_runs'])
    assert summarize([('unknown', {})])['pattern_scope_states'] == {'unknown_version': 1}


def test_legacy_replay_adds_only_missing_limited_comparison_scope():
    _, validate, _ = cli_helpers()
    prepared, output = comparison_fixture()
    output.pop('input_scope_fingerprint')
    output.pop('comparison_decision')
    output['outcome_comparisons'][0].pop('comparison_scope')
    for field in ['observation_scope', 'scope_limit', 'evidence']:
        output['writing_pattern'][0].pop(field)
    saved = {'prompt_version': LEGACY_PROMPT_VERSION, 'extractor': 'llm_full_experience_v2',
             'input_sha256': fingerprint(_legacy_context(prepared)), 'result': output}
    previous = {'program_id': prepared['program_id'], 'experience_cases': [
        {'case_id': c['case_id'], 'source_refs': []} for c in prepared['cases']]}
    knowledge = knowledge_from_result(previous, prepared, saved)
    knowledge['outcome_comparisons'][0].pop('comparison_scope')
    _, added = validate(knowledge, prepared, saved)
    assert added == ['outcome_comparisons[0].comparison_scope']
    knowledge['outcome_comparisons'][0]['comparison_scope'] = 'same_prompt_final_statements'
    with pytest.raises(ValueError, match='beyond compatibility labels'):
        validate(knowledge, prepared, saved)
