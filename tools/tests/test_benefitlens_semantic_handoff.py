"""Synthetic provenance fixtures test accounting, not live semantic quality."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_benefitlens_semantic_handoff import build_handoff


def write(path, value):
    raw = (json.dumps(value, ensure_ascii=False)+'\n').encode()
    path.write_bytes(raw)
    return sha256(raw).hexdigest()


def setup_queue(tmp_path, count=2, kind='experience'):
    db_path = tmp_path/'queue.sqlite'
    tasks, refs = [], []
    for i in range(count):
        prefix = tmp_path/str(i)
        inp, out, rec, val = [Path(str(prefix)+suffix) for suffix in ('.input.json','.result.json','.receipt.json','.validation.json')]
        input_hash = write(inp, {'task_id': str(i), 'text': 'Synthetic source material.'})
        result = {'task_id': str(i), 'source_subject_id': 'source-person-a', 'experience_id': 'episode-a',
            'evidence': [{'evidence_id': 'e'+str(i), 'quote': 'Synthetic source material.'}],
            'events': [{'event_id': 'event'+str(i), 'slots': {'action': {'value': 'reported action','evidence_ids': ['e'+str(i)]}}}],
            'writing_observations': [{'observation_id': 'w'+str(i), 'layer': 'observed_strategy',
                                      'statement': 'A reported action supports a claim.', 'evidence_ids': ['e'+str(i)]}],
            'paragraph_observations': [{'unit_id': 'p'+str(i), 'unit_role': 'paragraph',
                'purpose': 'explain action', 'opening_function': 'introduce obstacle',
                'closing_function': 'state learning', 'claim': 'reported contribution',
                'evidence_use': 'reported event', 'reasoning': 'action is related to outcome',
                'previous_connection': None, 'next_connection': 'learning to future work'}]}
        output_hash = write(out, result)
        response = tmp_path/(str(i)+'.response.json')
        response_hash = write(response, {'synthetic': True, 'structured_output': result})
        write(rec, {'model_called': True, 'provider': 'claude_cli', 'invocation_id': 'synthetic-test-invocation',
            'models': ['synthetic-test-model'], 'input_sha256': input_hash, 'input_file_sha256': input_hash,
            'modelUsage': {'synthetic-test-model': {'inputTokens': 1}}, 'exit_code': 0,
            'output_sha256': output_hash, 'raw_response_sha256': response_hash,
            'response_ref': {'path': str(response), 'sha256': response_hash},
            'input_path': str(inp), 'output_path': str(out)})
        write(val, {'valid': True, 'source_validation': 'synthetic fixture source-span check'})
        tasks.append({'job_id': 'job'+str(i), 'unit_id': kind+':case-a', 'kind': kind,
            'record_key': 'case-a', 'task_id': str(i), 'input_sha256': input_hash,
            'input_path': str(inp), 'state': 'completed', 'result_path': str(out),
            'receipt_path': str(rec), 'validation_path': str(val)})
        refs.append({'job_id': 'job'+str(i), 'result_path': str(out), 'output_sha256': output_hash,
                     'receipt_path': str(rec), 'validation_path': str(val)})
    aggregate = {'state': 'completed', 'model_called': True, 'case_key': 'case-a',
        'input_sha256': sha256(b'synthetic unit').hexdigest(), 'task_results_refs': refs,
        'program_binding': {'status': 'unreviewed_source_identifier', 'program_id': 'program-candidate',
                            'round_id': None, 'production_notice_links': []}}
    aggregate_path = tmp_path/'aggregate.json'
    aggregate_hash = write(aggregate_path, aggregate)
    receipt_path, validation_path = tmp_path/'unit-receipt.json', tmp_path/'unit-validation.json'
    write(receipt_path, {'state':'completed','model_called':True,'input_sha256':aggregate['input_sha256'],
        'output_sha256':aggregate_hash,'task_count':count,'task_results_refs':refs})
    write(validation_path, {'valid':True,'all_source_chunks_completed':True,'source_validation':'synthetic fixture'})
    unit = {'unit_id': kind+':case-a', 'kind': kind, 'record_key': 'case-a',
        'input_sha256': aggregate['input_sha256'], 'state': 'completed', 'task_count': count,
        'completed_task_count': count, 'result_path': str(aggregate_path),
        'receipt_path':str(receipt_path),'validation_path':str(validation_path)}
    with sqlite3.connect(db_path) as db:
        for name, rows in [('units', [unit]), ('tasks', tasks)]:
            db.execute('CREATE TABLE '+name+'('+','.join(k+(' INTEGER' if isinstance(v,int) else ' TEXT') for k,v in rows[0].items())+')')
            db.executemany('INSERT INTO '+name+' VALUES('+','.join('?' for _ in rows[0])+')', [tuple(r.values()) for r in rows])
    return db_path, unit, tasks, aggregate_hash


def run(tmp_path, **kwargs):
    return build_handoff(tmp_path/'queue.sqlite', tmp_path/'handoff', **kwargs)


def test_all_chunks_and_execution_proofs_produce_one_unapproved_case_observation(tmp_path):
    setup_queue(tmp_path)
    summary = run(tmp_path)
    counts = summary['counts_by_kind']['experience']
    assert counts['model_output_preserved_tasks'] == counts['source_validated_tasks'] == 2
    assert counts['llm_completed_source_validated_units'] == 1
    assert summary['distinct_output_preserving_model_invocations'] == 1
    assert summary['common_success_patterns_approved'] == 0
    pattern = json.loads((tmp_path/'handoff/writing-pattern-observations.jsonl').read_text())
    assert pattern['status'] == 'single_case_observation'
    assert not pattern['approved_pattern'] and not pattern['selection_success_pattern']
    assert pattern['notice_binding']['status'] == 'inferred_candidate'
    assert pattern['notice_binding']['production_notice_links'] == []
    assert pattern['paragraph_observations'][0]['observation']['opening_function'] == 'introduce obstacle'
    assert pattern['paragraph_observations'][1]['observation']['next_connection'] == 'learning to future work'
    assert pattern['quality_score'] is None


def test_partial_chunk_completion_counts_actual_outputs_but_no_completed_case(tmp_path):
    db, _, tasks, _ = setup_queue(tmp_path)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE units SET state='pending',completed_task_count=1")
        connection.execute("UPDATE tasks SET state='pending' WHERE job_id=?", (tasks[1]['job_id'],))
    for name in ('result_path','receipt_path','validation_path'):
        Path(tasks[1][name]).unlink()
    summary = run(tmp_path)
    assert summary['counts_by_kind']['experience']['model_output_preserved_tasks'] == 1
    assert summary['single_case_pattern_observations'] == 0


@pytest.mark.parametrize('tamper,expected', [
    ('input','input_file_hash_mismatch'), ('result','result_hash_mismatch'),
    ('model','model_not_called'), ('validation','source_validation_not_passed'),
    ('source_scope','source_validation_scope_missing'), ('aggregate','unit_result_hash_mismatch')])
def test_claimed_completed_row_does_not_count_after_artifact_or_provenance_failure(tmp_path, tamper, expected):
    _, unit, tasks, _ = setup_queue(tmp_path)
    task = tasks[0]
    if tamper in ('input','result'):
        path = Path(task[tamper+'_path'])
        value = json.loads(path.read_text()); value['tampered'] = True; write(path, value)
    elif tamper == 'model':
        path = Path(task['receipt_path']); value = json.loads(path.read_text()); value['model_called'] = False; write(path,value)
    elif tamper in ('validation','source_scope'):
        write(Path(task['validation_path']), {'valid': tamper != 'validation',
            'source_validation': None if tamper == 'source_scope' else 'source span'})
    else:
        path = Path(unit['result_path']); value = json.loads(path.read_text())
        value['task_results_refs'][0]['output_sha256'] = 'wrong'; write(path,value)
    summary = run(tmp_path)
    assert summary['single_case_pattern_observations'] == 0
    assert summary['completion_errors'][0]['error'] == expected


def test_missing_task_and_duplicate_reference_never_complete_a_case(tmp_path):
    db, unit, _, _ = setup_queue(tmp_path)
    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM tasks WHERE task_id='1'")
    summary = run(tmp_path)
    assert summary['completion_errors'][0]['error'] == 'incomplete_task_coverage'


def test_independent_review_requires_two_distinct_humans_bound_to_current_output(tmp_path):
    _, unit, _, output_hash = setup_queue(tmp_path)
    review = {'unit_id': unit['unit_id'], 'output_sha256': output_hash, 'reviewer_id': 'human-a',
        'reviewer_type': 'human', 'independent_of_generation': True, 'blinded': True,
        'source_review_performed': True, 'criteria_refs': ['requirement','grounds','warrant'], 'verdict': 'approve'}
    review_path = tmp_path/'reviews.json'
    write(review_path, [review,deepcopy(review)])
    assert run(tmp_path,reviews_path=review_path)['counts_by_kind']['experience']['independent_human_approved_units'] == 0
    other = dict(review, reviewer_id='human-b', output_sha256='outdated')
    write(review_path,[review,other])
    assert run(tmp_path,reviews_path=review_path)['counts_by_kind']['experience']['independent_human_approved_units'] == 0
    other['output_sha256'] = output_hash; write(review_path,[review,other])
    assert run(tmp_path,reviews_path=review_path)['counts_by_kind']['experience']['independent_human_approved_units'] == 1


def test_notice_is_only_a_candidate_and_does_not_write_product_schema(tmp_path):
    setup_queue(tmp_path, count=1, kind='notice')
    summary = run(tmp_path)
    row = json.loads((tmp_path/'handoff/notice-common-schema-candidates.jsonl').read_text())
    assert row['status'] == 'source_validated_common_schema_candidate'
    assert not row['production_database_written']
    assert summary['notice_common_schema_candidates'] == 1
    assert summary['single_case_pattern_observations'] == 0


def test_a_claimed_verified_program_link_without_review_artifact_stays_candidate(tmp_path):
    _, unit, _, _ = setup_queue(tmp_path)
    path = Path(unit['result_path']); result = json.loads(path.read_text())
    result['program_binding'].update(status='verified', production_notice_links=[{'notice_id': 'n1'}])
    output_hash = write(path,result)
    path = Path(unit['receipt_path']); receipt = json.loads(path.read_text())
    receipt['output_sha256'] = output_hash; write(path,receipt)
    run(tmp_path)
    pattern = json.loads((tmp_path/'handoff/writing-pattern-observations.jsonl').read_text())
    assert pattern['notice_binding']['status'] == 'inferred_candidate'
    assert not pattern['notice_binding']['production_notice_links']


def test_common_schema_links_keep_program_specific_and_non_eligibility_meanings(tmp_path):
    _, unit, _, _ = setup_queue(tmp_path, count=1, kind='notice')
    path = Path(unit['result_path']); result = json.loads(path.read_text())
    result['conditions'] = [
        {'field_id': 'education.degree', 'statement_type': 'eligibility', 'subject': 'applicant', 'value': 'doctorate'},
        {'field_id': 'program_specific.requirement', 'program_specific_key': 'lab_rotation',
         'statement_type': 'activity_obligation', 'subject': 'applicant', 'value': 'complete a rotation'}]
    result['logic_groups'] = [{'operator': 'AND', 'condition_ids': ['c1','c2']}]
    output_hash = write(path,result)
    path = Path(unit['receipt_path']); receipt = json.loads(path.read_text())
    receipt['output_sha256'] = output_hash; write(path,receipt)
    run(tmp_path)
    notice = json.loads((tmp_path/'handoff/notice-common-schema-candidates.jsonl').read_text())
    assert notice['shared_field_ids'] == ['education.degree']
    assert notice['program_specific_conditions'][0]['program_specific_key'] == 'lab_rotation'
    assert notice['program_specific_conditions'][0]['statement_type'] == 'activity_obligation'
    assert notice['logic_groups'] == result['logic_groups']
