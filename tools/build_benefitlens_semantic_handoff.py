"""Read completed semantic-worker artifacts without calling a model or network.

Queue rows alone are never completion evidence. Every source chunk must have a
preserved result, a Claude execution receipt and source validation. Writing
observations remain single-case evidence until independently reviewed.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path('/home/longnew/.cache/datasets/benefitlens/scale50k-20261109/team-production-20261119/semantic')
FRAMEWORK = REPO/'pipeline/config/writing_evidence_framework_v2.json'
CONDITION_CATALOG = REPO/'pipeline/config/shared_condition_catalog_v1.json'


def _hash(raw):
    return sha256(raw).hexdigest()


def _read(path):
    if not path:
        raise ValueError('artifact_path_missing')
    raw = Path(path).read_bytes()
    return json.loads(raw), _hash(raw)


def _require(condition, error):
    if not condition:
        raise ValueError(error)


def verify_task_output(task):
    """Verify an actual preserved output independently of its validation state."""
    source, input_file_hash = _read(task['input_path'])
    result, result_hash = _read(task['result_path'])
    receipt, _ = _read(task['receipt_path'])
    _require(bool(result) and isinstance(result, dict), 'empty_semantic_result')
    _require(receipt.get('model_called') is True, 'model_not_called')
    _require(receipt.get('provider') == 'claude_cli', 'execution_provider_unverified')
    _require(bool(receipt.get('invocation_id')), 'invocation_missing')
    _require(receipt.get('exit_code') == 0 and bool(receipt.get('modelUsage')),
             'successful_provider_usage_missing')
    _require(receipt.get('input_sha256') == task['input_sha256'], 'semantic_input_hash_mismatch')
    _require(receipt.get('input_file_sha256') == input_file_hash, 'input_file_hash_mismatch')
    _require(receipt.get('output_sha256') == result_hash, 'result_hash_mismatch')
    _require(bool(receipt.get('raw_response_sha256')), 'raw_response_hash_missing')
    response = receipt.get('response_ref') or {}
    response_raw = Path(response['path']).read_bytes() if response.get('path') else b''
    _require(bool(response_raw) and _hash(response_raw) == response.get('sha256')
        == receipt['raw_response_sha256'], 'raw_response_file_hash_mismatch')
    for key, expected in [('input_path', task['input_path']), ('output_path', task['result_path'])]:
        if receipt.get(key):
            _require(Path(receipt[key]).resolve() == Path(expected).resolve(), key+'_mismatch')
    return {'job_id': task['job_id'], 'task_id': task['task_id'],
            'input': source, 'result': result, 'output_sha256': result_hash,
            'result_path': task['result_path'], 'receipt_path': task['receipt_path'],
            'validation_path': task['validation_path'], 'receipt': receipt}


def verify_task(task):
    """Check persisted provenance; this does not rejudge semantic correctness."""
    checked = verify_task_output(task)
    _require(task['state'] == 'completed', 'task_not_completed')
    validation, _ = _read(task['validation_path'])
    _require(validation.get('valid') is True, 'source_validation_not_passed')
    _require(bool(validation.get('source_validation')), 'source_validation_scope_missing')
    checked['validation'] = validation
    return checked


def verify_unit(unit, tasks):
    _require(unit['state'] == 'completed', 'unit_not_completed')
    _require(unit['task_count'] > 0 and len(tasks) == unit['task_count'], 'incomplete_task_coverage')
    _require(unit['completed_task_count'] == unit['task_count'], 'incomplete_completed_task_count')
    _require(len({t['job_id'] for t in tasks}) == len(tasks), 'duplicate_task_identifier')
    checked = [verify_task(task) for task in tasks]
    result, result_hash = _read(unit['result_path'])
    receipt, _ = _read(unit.get('receipt_path'))
    validation, _ = _read(unit.get('validation_path'))
    _require(receipt.get('state') == 'completed' and receipt.get('model_called') is True,
             'unit_execution_receipt_incomplete')
    _require(receipt.get('output_sha256') == result_hash, 'unit_result_hash_mismatch')
    _require(receipt.get('input_sha256') == unit['input_sha256'], 'unit_receipt_input_hash_mismatch')
    _require(validation.get('valid') is True and validation.get('all_source_chunks_completed') is True
        and bool(validation.get('source_validation')), 'unit_source_validation_incomplete')
    _require(isinstance(result, dict) and result.get('state') == 'completed', 'aggregate_not_completed')
    _require(result.get('model_called') is True, 'aggregate_model_not_called')
    _require(result.get('input_sha256') == unit['input_sha256'], 'aggregate_input_hash_mismatch')
    refs = result.get('task_results_refs', [])
    _require(receipt.get('task_results_refs') == refs and receipt.get('task_count') == len(tasks),
             'unit_receipt_task_reference_mismatch')
    _require(len(refs) == len(checked), 'aggregate_task_reference_count_mismatch')
    by_job = {r.get('job_id'): r for r in refs}
    _require(len(by_job) == len(refs) and by_job.keys() == {r['job_id'] for r in checked},
             'aggregate_task_reference_mismatch')
    for task in checked:
        ref = by_job[task['job_id']]
        _require(ref.get('output_sha256') == task['output_sha256'], 'aggregate_output_hash_mismatch')
        for field in ('result_path', 'receipt_path', 'validation_path'):
            _require(ref.get(field) and Path(ref[field]).resolve() == Path(task[field]).resolve(),
                     'aggregate_'+field+'_mismatch')
    return {'unit': unit, 'result': result, 'output_sha256': result_hash, 'tasks': checked}


def _binding(result):
    """Retain source identifiers without inventing a verified notice/round link."""
    raw = result.get('program_binding') or {}
    program = raw.get('program_id', result.get('program_id'))
    round_id = raw.get('round_id', result.get('round_id'))
    links = raw.get('production_notice_links') or []
    proof = raw.get('verification_ref') or {}
    verified = False
    if (raw.get('status') in ('verified', 'verified_program_round_link') and links and proof
            and program and round_id is not None):
        try:
            audit, audit_hash = _read(proof.get('path'))
            verified = (audit_hash == proof.get('sha256') and audit.get('valid') is True
                and audit.get('program_id') == program and audit.get('round_id') == round_id
                and audit.get('production_notice_links') == links and bool(audit.get('evidence_refs')))
        except (ValueError, OSError, TypeError):
            pass
    return {'status': 'verified' if verified else 'inferred_candidate' if program else 'unknown',
            'program_id': program, 'round_id': round_id,
            'production_notice_links': links if verified else [],
            'source_binding': raw,
            'reason': None if verified else 'requires_reviewed_program_and_round_mapping'}


def writing_pattern(checked, framework_hash):
    """Preserve what the model observed, including empty/unavailable structure."""
    result, unit = checked['result'], checked['unit']
    chunks = [task['result'] for task in checked['tasks']]
    observed = []
    paragraphs = []
    events = []
    evidence = []
    for task in checked['tasks']:
        row = task['result']
        provenance = {'job_id': task['job_id'], 'task_id': task['task_id'],
                      'result_path': task['result_path'], 'output_sha256': task['output_sha256']}
        for key, target in [('writing_observations', observed), ('paragraph_observations', paragraphs),
                            ('events', events), ('evidence', evidence)]:
            for value in row.get(key, []):
                target.append({'observation': value, 'execution_ref': provenance})
    return {'pattern_id': 'single-case:'+unit['unit_id'], 'unit_id': unit['unit_id'],
            'record_key': unit['record_key'], 'case_key': result.get('case_key', unit['record_key']),
            'status': 'single_case_observation', 'approved_pattern': False,
            'selection_success_pattern': False, 'independent_case_count': 1,
            'notice_binding': _binding(result),
            'source_roles': result.get('source_roles'),
            'source_document_ids': sorted({r['document_id'] for r in chunks if r.get('document_id')}),
            'application_text_visibility': [{'task_id': r.get('task_id'),
                'status': r.get('application_text_visibility', 'unknown')} for r in chunks],
            'source_subject_ids': sorted({r['source_subject_id'] for r in chunks if r.get('source_subject_id')}),
            'subject_identity_status': 'source_attribution_not_verified_applicant_identity',
            'experience_ids': sorted({r['experience_id'] for r in chunks if r.get('experience_id')}),
            'events_and_material_selection': events, 'writing_observations': observed,
            'paragraph_observations': paragraphs, 'source_evidence': evidence,
            'structure_observation_count': len(paragraphs),
            'distinct_source_structure_units': len({p['observation']['unit_id'] for p in paragraphs
                                                    if p['observation'].get('unit_id')}),
            'framework_ref': {'path': str(FRAMEWORK), 'sha256': framework_hash,
                              'review_chain': ['requirement','claim','grounds','warrant','paragraph','document']},
            'remaining_review': ['genre_and_prompt_applicability','claim_evidence_warrant_validity',
                                 'cross_paragraph_logic','independent_content_review'],
            'quality_score': None, 'aggregate_result_sha256': checked['output_sha256']}


def notice_projection(checked, approved=False):
    result, unit = checked['result'], checked['unit']
    catalog, catalog_hash = _read(CONDITION_CATALOG)
    common_fields = {field for field in catalog['fields'] if not field.startswith('program_specific.')}
    conditions = result.get('conditions', [])
    return {'unit_id': unit['unit_id'], 'result_path': unit['result_path'],
        'output_sha256': checked['output_sha256'],
        'common_schema_ref': {'path': str(CONDITION_CATALOG), 'sha256': catalog_hash},
        'shared_field_ids': sorted({r['field_id'] for r in conditions if r.get('field_id') in common_fields}),
        'program_specific_conditions': [r for r in conditions if r.get('field_id') not in common_fields],
        'conditions': conditions, 'logic_groups': result.get('logic_groups', []),
        'exceptions': result.get('exceptions', []), 'official_prompts': result.get('official_prompts', []),
        'unknowns': result.get('unknowns', []), 'source': result.get('source'),
        'semantic_result': result, 'status': 'source_validated_common_schema_candidate',
        'production_database_written': False,
        'independent_content_review': 'approved_by_two_blinded_humans' if approved else 'not_approved'}


def _approved_reviews(unit_id, output_hash, reviews):
    eligible = {r.get('reviewer_id') for r in reviews
                if r.get('unit_id') == unit_id and r.get('output_sha256') == output_hash
                and r.get('reviewer_type') == 'human' and r.get('reviewer_id')
                and r.get('independent_of_generation') is True and r.get('blinded') is True
                and r.get('source_review_performed') is True and r.get('criteria_refs')
                and r.get('verdict') == 'approve'}
    return len(eligible) >= 2


def build_handoff(ledger_path, output_root, reviews_path=None):
    ledger_path, output_root = Path(ledger_path), Path(output_root)
    reviews = _read(reviews_path)[0] if reviews_path else []
    _require(isinstance(reviews, list), 'reviews_must_be_a_list')
    framework_hash = _hash(FRAMEWORK.read_bytes())
    with sqlite3.connect('file:'+str(ledger_path)+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        units = [dict(r) for r in db.execute('SELECT * FROM units ORDER BY unit_id')]
        tasks = [dict(r) for r in db.execute('SELECT * FROM tasks ORDER BY unit_id,task_id,job_id')]
    by_unit = {}
    for task in tasks:
        by_unit.setdefault(task['unit_id'], []).append(task)
    counts = {}
    errors = []
    patterns = []
    notices = []
    invocations = set()
    verified_units = []
    for kind in {r['kind'] for r in units+tasks}:
        counts[kind] = Counter({key: 0 for key in (
            'units_in_queue','tasks_in_queue','model_output_preserved_tasks','source_validated_tasks',
            'completed_claimed_by_queue','completion_artifact_verification_failed',
            'llm_completed_source_validated_units','llm_completed_source_validated_tasks',
            'independent_human_approved_units','single_case_pattern_observations','observed_paragraph_structures')})
    for task in tasks:
        counter = counts.setdefault(task['kind'], Counter())
        try:
            output = verify_task_output(task)
        except (ValueError, OSError, KeyError, TypeError):
            continue
        counter['model_output_preserved_tasks'] += 1
        invocations.add(output['receipt']['invocation_id'])
        try:
            verify_task(task)
        except (ValueError, OSError, KeyError, TypeError):
            continue
        counter['source_validated_tasks'] += 1
    for unit in units:
        counter = counts.setdefault(unit['kind'], Counter())
        counter['units_in_queue'] += 1
        counter['queue_state_'+unit['state']] += 1
        counter['tasks_in_queue'] += len(by_unit.get(unit['unit_id'], []))
        if unit['state'] != 'completed':
            continue
        counter['completed_claimed_by_queue'] += 1
        try:
            checked = verify_unit(unit, by_unit.get(unit['unit_id'], []))
        except (ValueError, OSError, KeyError, TypeError) as error:
            errors.append({'unit_id': unit['unit_id'], 'error': str(error)})
            counter['completion_artifact_verification_failed'] += 1
            continue
        counter['llm_completed_source_validated_units'] += 1
        counter['llm_completed_source_validated_tasks'] += len(checked['tasks'])
        approved = _approved_reviews(unit['unit_id'], checked['output_sha256'], reviews)
        counter['independent_human_approved_units'] += int(approved)
        verified_units.append({'unit_id': unit['unit_id'], 'kind': unit['kind'],
            'record_key': unit['record_key'], 'result_path': unit['result_path'],
            'output_sha256': checked['output_sha256'],
            'machine_validation': 'source_validation_receipts_and_chunk_coverage_verified',
            'independent_content_review': 'approved_by_two_blinded_humans' if approved else 'not_approved'})
        if unit['kind'] == 'experience':
            pattern = writing_pattern(checked, framework_hash)
            patterns.append(pattern)
            counter['single_case_pattern_observations'] += 1
            counter['observed_paragraph_structures'] += pattern['structure_observation_count']
        elif unit['kind'] == 'notice':
            notices.append(notice_projection(checked, approved))
    summary = {'version': 'semantic-handoff-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
        'ledger_path': str(ledger_path), 'counts_by_kind': counts,
        'distinct_output_preserving_model_invocations': len(invocations),
        'single_case_pattern_observations': len(patterns), 'notice_common_schema_candidates': len(notices),
        'completion_errors': errors, 'network_requests': 0, 'model_called_this_run': False,
        'production_database_written': False, 'common_success_patterns_approved': 0,
        'framework_ref': {'path': str(FRAMEWORK), 'sha256': framework_hash},
        'counts_scope': 'Only complete units with all persisted task outputs, matching execution hashes and source-validation receipts.',
        'review_scope': 'Machine source validation is distinct from semantic correctness and independent human content approval.'}
    output_root.mkdir(parents=True, exist_ok=True)
    for name, rows in [('writing-pattern-observations.jsonl', patterns), ('notice-common-schema-candidates.jsonl', notices),
                       ('completed-unit-handoff.jsonl', verified_units)]:
        target = output_root/name
        temporary = target.with_suffix(target.suffix+'.tmp')
        temporary.write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows))
        temporary.replace(target)
    temporary = output_root/'summary.json.tmp'
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    temporary.replace(output_root/'summary.json')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--semantic-root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--output-root', type=Path)
    parser.add_argument('--reviews', type=Path)
    args = parser.parse_args()
    summary = build_handoff(args.semantic_root/'queue.sqlite', args.output_root or args.semantic_root/'handoff', args.reviews)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
