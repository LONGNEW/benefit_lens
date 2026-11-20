"""Account for actual compact group interpretations and local writing moves.

This reader never calls a model. Source documents, representative passages,
expression occurrences and interpreted groups remain different count units.
"""
import argparse
import fcntl
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import stat
from jsonschema.exceptions import ValidationError

DEFAULT_ROOT = Path('/home/longnew/.cache/datasets/benefitlens/scale50k-20261109/team-production-20261119/compact')
FRAMEWORK = Path(__file__).resolve().parents[1]/'pipeline/config/writing_evidence_framework_v2.json'


def canonical_hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _read_bytes_ref(ref):
    raw = Path(ref['path']).read_bytes()
    if sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('artifact_hash_mismatch')
    return raw


def _read_ref(ref):
    return json.loads(_read_bytes_ref(ref))


def _groups_in(value, group_id):
    if isinstance(value, dict):
        if value.get('group_id') == group_id:
            yield value
        for child in value.values():
            yield from _groups_in(child, group_id)
    elif isinstance(value, list):
        for child in value:
            yield from _groups_in(child, group_id)


def _pointer(value, pointer):
    for part in pointer.strip('/').split('/'):
        part = part.replace('~1','/').replace('~0','~')
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def _source_fields_equal(raw, checked):
    if isinstance(raw, dict):
        return isinstance(checked, dict) and all(k in checked and _source_fields_equal(v, checked[k])
                                                for k,v in raw.items())
    if isinstance(raw, list):
        return isinstance(checked, list) and len(raw) == len(checked) and all(
            _source_fields_equal(a,b) for a,b in zip(raw,checked))
    return raw == checked


def _verify_codex_completion(receipt):
    """Verify stored agent completion provenance without fabricating usage."""
    if (receipt.get('model_called') is not True or not receipt.get('agent_name')
            or not receipt.get('invocation_id')):
        raise ValueError('codex_agent_execution_not_receipted')
    completion=_read_ref(receipt['completion_message_ref'])
    expected={'agent_name':receipt['agent_name'],'invocation_id':receipt['invocation_id'],
              'input_sha256':receipt['input_sha256'], 'response_sha256':receipt['response_ref']['sha256'],
              'status':'completed'}
    if any(completion.get(k)!=v for k,v in expected.items()):
        raise ValueError('codex_completion_identity_or_hash_mismatch')
    if not isinstance(completion.get('completion_message'),str) or not completion['completion_message'].strip():
        raise ValueError('codex_actual_completion_message_missing')
    if (receipt['response_ref']['sha256']!=receipt['result_ref']['sha256']
            or Path(receipt['response_ref']['path']).resolve()!=Path(receipt['result_ref']['path']).resolve()):
        raise ValueError('codex_original_response_and_result_differ')
    if receipt.get('independent_usage_telemetry_available') is not False or receipt.get('usage') is not None:
        raise ValueError('codex_usage_telemetry_boundary_missing')
    if any(receipt.get(k) for k in ('modelUsage','models','model')):
        raise ValueError('codex_unverified_model_telemetry_present')
    return completion


def _verify_codex_cli(receipt, source_request, require_completed=True):
    if receipt.get('cli_invoked') is not True or not receipt.get('invocation_id'):
        raise ValueError('codex_cli_invocation_missing')
    events_raw=_read_bytes_ref(receipt['events_ref'])
    if _read_bytes_ref(receipt['response_ref'])!=events_raw:
        raise ValueError('codex_cli_event_response_mismatch')
    events=[json.loads(line) for line in events_raw.decode().splitlines() if line.strip()]
    usage_events=[{'event_index':i,'type':'turn.completed','usage':event.get('usage')}
                  for i,event in enumerate(events) if event.get('type')=='turn.completed']
    if receipt.get('usage_events')!=usage_events:
        raise ValueError('codex_cli_usage_events_mismatch')
    last_usage=usage_events[-1]['usage'] if usage_events else None
    if receipt.get('turn_completed_usage')!=last_usage or receipt.get('model_called') is not bool(usage_events):
        raise ValueError('codex_cli_turn_completion_mismatch')
    thread_ids=[e.get('thread_id') for e in events if e.get('type')=='thread.started']
    if receipt.get('thread_id') and (not thread_ids or receipt['thread_id']!=thread_ids[-1]):
        raise ValueError('codex_cli_thread_identity_mismatch')
    prompt=_read_bytes_ref(receipt['prompt_ref']).decode()
    submitted=(source_request.get('request') or {}).get('user')
    if not isinstance(submitted,str) or submitted not in prompt:
        raise ValueError('codex_cli_actual_prompt_input_mismatch')
    _read_ref(receipt['actual_batch_schema_ref'])
    if not usage_events:
        if require_completed:
            raise ValueError('codex_cli_no_completed_turn')
        return None,usage_events
    if receipt.get('exit_code')!=0 or not isinstance(last_usage,dict) or not last_usage:
        raise ValueError('codex_cli_successful_completion_missing')
    final_raw=_read_bytes_ref(receipt['final_response_ref'])
    final=json.loads(final_raw)
    if final!=_read_ref(receipt['result_ref']) or (receipt.get('raw_result_ref')
            and final!=_read_ref(receipt['raw_result_ref'])):
        raise ValueError('codex_cli_final_and_parsed_result_differ')
    messages=[e['item'].get('text') for e in events if e.get('type')=='item.completed'
              and isinstance(e.get('item'),dict) and e['item'].get('type')=='agent_message']
    if not messages or json.loads(messages[-1])!=final:
        raise ValueError('codex_cli_final_response_not_in_event_trace')
    return final,usage_events


def _receipt_execution(receipt_path, receipt_cache):
    if not receipt_path:
        raise ValueError('model_receipt_missing')
    if receipt_path not in receipt_cache:
        receipt = json.loads(Path(receipt_path).read_text())
        if (receipt.get('provider') not in ('claude_cli','codex_subagent','codex_cli')
                or receipt.get('model_called') is not True or not receipt.get('invocation_id')):
            raise ValueError('actual_model_execution_not_receipted')
        source_request = _read_ref(receipt['request_ref'])
        request = source_request
        response = _read_ref(receipt['response_ref']) if receipt['provider']!='codex_cli' else None
        if not receipt.get('input_sha256') or (isinstance(request,dict) and request.get('input_sha256')
                and request['input_sha256'] != receipt['input_sha256']):
            raise ValueError('batch_input_identity_mismatch')
        if receipt['provider']=='claude_cli':
            if not response.get('modelUsage'):
                raise ValueError('provider_usage_missing_from_original_response')
        elif receipt['provider']=='codex_subagent':
            _verify_codex_completion(receipt)
        else:
            response,_=_verify_codex_cli(receipt,source_request)
        submitted = (request.get('request') or {}).get('user') if isinstance(request,dict) else None
        if isinstance(submitted,str):
            request = json.loads(submitted)
        original = response if receipt['provider'] in ('codex_subagent','codex_cli') else response.get('structured_output')
        if original is None and response.get('result'):
            try:
                original = json.loads(response['result'])
            except (ValueError,TypeError):
                original = None
        receipt_cache[receipt_path] = (receipt, request, original, source_request)
    return receipt_cache[receipt_path]


def verify_group(group, receipt_cache):
    """Verify both preserved artifacts and the actual returned group object."""
    result = json.loads(group['result_json'])
    gid = group['group_id']
    if result.get('group_id') != gid:
        raise ValueError('group_result_identity_mismatch')
    receipt_path = group.get('receipt_path')
    receipt, request, original, source_request = _receipt_execution(receipt_path,receipt_cache)
    batch_result = _read_ref(receipt['result_ref'])
    refs = [r for r in receipt.get('group_results', []) if r.get('group_id') == gid]
    if len(refs) != 1 or refs[0].get('result_sha256') != canonical_hash(result):
        raise ValueError('group_result_hash_mismatch')
    if not list(_groups_in(request, gid)):
        raise ValueError('group_absent_from_submitted_request')
    raw_matches = list(_groups_in(original, gid))
    if len(raw_matches) != 1:
        raise ValueError('group_absent_or_duplicated_in_actual_response')
    raw = raw_matches[0]
    if receipt['provider']=='codex_cli':
        validated=_read_ref(receipt['validated_result_ref'])
        persisted=_pointer(validated,refs[0]['result_pointer'])
        raw_pointer=_pointer(batch_result,refs[0]['raw_result_pointer'])
        if canonical_hash(raw_pointer)!=canonical_hash(raw):
            raise ValueError('codex_cli_raw_group_pointer_mismatch')
    else:
        persisted = _pointer(batch_result, refs[0]['result_pointer'])
    if not _source_fields_equal(raw, result.get('source_model_result', result)):
        raise ValueError('model_semantic_fields_changed')
    if canonical_hash(persisted) not in (canonical_hash(result), canonical_hash(raw)):
        raise ValueError('batch_result_pointer_mismatch')
    expected_raw = refs[0].get('raw_result_sha256') or result.get('raw_result_sha256')
    if expected_raw and canonical_hash(raw) != expected_raw:
        raise ValueError('raw_group_hash_mismatch')
    source_check='receipt_and_original_semantic_fields_verified'
    sources=source_request.get('source_groups')
    kind=receipt.get('kind') or group.get('kind')
    if kind=='notice' and sources:
        from benefitlens_compact_notice import validate_group
        candidates=[g for g in sources if g.get('group_id')==gid]
    elif kind=='experience' and source_request.get('groups') and 'view' in source_request['groups'][0]:
        from benefitlens_compact_experience import validate_group
        candidates=[g for g in source_request['groups'] if g.get('group_id')==gid]
    else:
        candidates=None
    if candidates is not None:
        if len(candidates)!=1:
            raise ValueError('source_group_identity_missing_or_duplicated')
        try:
            source_checked=validate_group(raw,candidates[0])
        except (ValueError,TypeError,KeyError,ValidationError) as error:
            raise ValueError('source_binding_validator_rejected_result') from error
        if not _source_fields_equal(source_checked,result):
            raise ValueError('source_binding_validation_changed')
        source_check='domain_source_binding_validator_replayed_on_preserved_request'
    elif receipt['provider']=='codex_subagent':
        raise ValueError('codex_source_bound_request_missing')
    return result, {'receipt_path': receipt_path, 'invocation_id': receipt['invocation_id'],
        'provider':receipt['provider'],'agent_name':receipt.get('agent_name'),
        'completion_message_ref':receipt.get('completion_message_ref'),
        'independent_usage_telemetry_available':receipt['provider'] in ('claude_cli','codex_cli'),
        'source_binding_validation':source_check,
        'input_sha256': receipt.get('input_sha256'), 'result_sha256': canonical_hash(result),
        'request_ref': receipt['request_ref'], 'response_ref': receipt['response_ref']}


def _sentence_key(row):
    if (not row.get('document_id') or not row.get('text_sha256') or row.get('sentence_index') is None
            or row['sentence_index'] < 0 or not isinstance(row.get('char_start'),int)
            or not isinstance(row.get('char_end'),int) or not 0 <= row['char_start'] < row['char_end']):
        raise ValueError('invalid_source_sentence_identity_or_span')
    if not row.get('context_start', row['char_start']) <= row['char_start'] < row['char_end'] <= row.get('context_end', row['char_end']):
        raise ValueError('occurrence_outside_preserved_context')
    return (row['document_id'],row['text_sha256'],row['sentence_index'])


def _writing_fragment(group, result, proof, occurrences):
    fields = ('event_roles','actor','writing_move','material_role','claim_evidence_reasoning',
              'opening_or_closing_function','local_connection','limits','support_quote')
    return {'group_id': group['group_id'], 'kind': group['kind'],
        'status': 'unreviewed_local_passage_observation', 'approved_pattern': False,
        'scope': 'visible_passage_only', 'source_coverage': 'selected_occurrences_only',
        'observed_structure': {key:result.get(key) for key in fields},
        'execution_ref': proof, 'occurrence_ids': [r['occurrence_id'] for r in occurrences],
        'framework_ref': str(FRAMEWORK), 'quality_score': None,
        'case_independence': 'not_inferred_from_expression_group_membership',
        'position_applicability': 'preserve_occurrence_position_and_review_context_before_template_use',
        'selection_effect_established': False}


def _relevance(result):
    if 'relevance' in result:
        return result['relevance'] if result['relevance'] in ('relevant','irrelevant','uncertain') else 'uncertain'
    if result.get('mappings'):
        return 'relevant'
    return 'irrelevant' if result.get('skip_reason') else 'uncertain'


def _has_writing_content(result):
    return (bool(result.get('writing_move') or result.get('material_role') or result.get('event_roles'))
        or any((result.get('claim_evidence_reasoning') or {}).values())
        or any((result.get('opening_or_closing_function') or {}).get(k) for k in ('opening','closing'))
        or ((result.get('local_connection') or {}).get('kind') in ('logical','continuation')
            and bool((result.get('local_connection') or {}).get('description'))))


def _write_jsonl(path, rows):
    temporary = path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
    temporary.replace(path)


def _group_snapshot(ledger_path):
    """Stream source rows instead of loading representative passages in memory."""
    with sqlite3.connect('file:'+str(ledger_path)+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        groups = db.execute('SELECT group_id,kind,occurrence_count,state,result_json,receipt_path FROM groups ORDER BY group_id')
        occurrences = iter(db.execute('SELECT * FROM occurrences ORDER BY group_id,occurrence_id'))
        current = next(occurrences,None)
        for group in groups:
            rows = []
            while current is not None and current['group_id'] < group['group_id']:
                raise ValueError('occurrence_references_unknown_group')
            while current is not None and current['group_id'] == group['group_id']:
                rows.append(dict(current));current = next(occurrences,None)
            yield dict(group),rows
        if current is not None:
            raise ValueError('occurrence_references_unknown_group')


def _call_audit(ledger_path):
    with sqlite3.connect('file:'+str(ledger_path)+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='calls'").fetchone()
        rows=[dict(r) for r in db.execute('SELECT * FROM calls')] if exists else []
    result={'attempt_records':len(rows),'cli_attempts':0,'verified_cli_attempts':0,'waiting_provider_attempts':0,
        'codex_subagent_batches':0,'verified_codex_subagent_batches':0,
        'codex_cli_attempts':0,'verified_codex_cli_attempts':0,'completed_codex_cli_batches':0,
        'codex_cli_usage_events':[],
        'actual_model_executions':0,'verified_input_tokens':0,'verified_output_tokens':0,
        'actual_model_execution_scope':'claude_cli_usage_telemetry_only',
        'codex_underlying_model_api_calls':None,'codex_token_usage':None,
        'token_usage_scope':'Claude CLI only; independent Codex usage telemetry unavailable',
        'attempt_state_counts':dict(Counter(r['state'] for r in rows)),
        'file_verification_errors':[],'verified_attempts':[]}
    for row in rows:
        try:
            receipt=json.loads(Path(row['receipt_path']).read_text())
            request=_read_ref(receipt['request_ref'])
            provider=receipt.get('provider')
            if provider not in ('claude_cli','codex_subagent','codex_cli') or receipt.get('invocation_id')!=row['invocation_id']:
                raise ValueError('call_receipt_identity_mismatch')
            counter={'claude_cli':'cli_attempts','codex_subagent':'codex_subagent_batches','codex_cli':'codex_cli_attempts'}
            result[counter[provider]]+=1
            response=_read_ref(receipt['response_ref']) if provider!='codex_cli' else {}
            for key,ref in [('request_path','request_ref'),('response_path','response_ref')]:
                if Path(row[key]).resolve()!=Path(receipt[ref]['path']).resolve():
                    raise ValueError('call_artifact_path_mismatch')
            if request.get('input_sha256') and request['input_sha256']!=receipt.get('input_sha256'):
                raise ValueError('call_input_identity_mismatch')
            if provider=='claude_cli':
                actual=bool(response.get('modelUsage'))
                if receipt.get('model_called') is not actual:
                    raise ValueError('call_model_usage_mismatch')
                waiting=row['state']=='waiting_provider_limit'
                if waiting and (actual or receipt.get('state')!='waiting_provider_limit'
                        or response.get('api_error')!='usage_limit_reached' or response.get('api_error_status')!=429):
                    raise ValueError('provider_wait_evidence_mismatch')
                result['verified_cli_attempts']+=1
                result['actual_model_executions']+=int(actual)
                result['waiting_provider_attempts']+=int(waiting)
                usage=response.get('usage') or {}
                result['verified_input_tokens']+=usage.get('input_tokens',0)
                result['verified_output_tokens']+=usage.get('output_tokens',0)
            elif provider=='codex_subagent':
                _verify_codex_completion(receipt)
                actual=True
                result['verified_codex_subagent_batches']+=1
            else:
                _,usage_events=_verify_codex_cli(receipt,request,require_completed=False)
                actual=bool(usage_events)
                result['verified_codex_cli_attempts']+=1
                result['completed_codex_cli_batches']+=int(actual)
                result['codex_cli_usage_events'].append({'invocation_id':receipt['invocation_id'],
                    'usage_events':usage_events,'events_ref':receipt['events_ref']})
            result['verified_attempts'].append({'invocation_id':row['invocation_id'],'kind':row['kind'],
                'provider':provider,'agent_name':receipt.get('agent_name'),
                'independent_usage_telemetry_available':provider in ('claude_cli','codex_cli'),
                'state':row['state'],'model_called':actual,'api_error':response.get('api_error'),
                'api_error_status':response.get('api_error_status'),
                'request_ref':receipt['request_ref'],'response_ref':receipt['response_ref']})
        except (ValueError,OSError,TypeError,KeyError) as error:
            result['file_verification_errors'].append({'invocation_id':row['invocation_id'],'error':str(error)})
    return result


def build_handoff(ledger_path, output_root):
    counters = {}
    sentences, documents, document_versions, representative_sentences = (
        defaultdict(set),defaultdict(set),defaultdict(set),defaultdict(set))
    strata = {}
    group_rows, applications, fragments, unresolved = [], [], [], []
    receipts = {}
    invocations = set()
    actual_invocations = set()
    provider_batches = defaultdict(set)
    codex_cli_usage={}
    reasons = Counter()
    occurrence_reasons = Counter()
    for group,rows in _group_snapshot(ledger_path):
        gid, kind = group['group_id'],group['kind']
        counts = counters.setdefault(kind, Counter({k:0 for k in (
            'groups_in_queue','llm_returned_groups','llm_interpreted_groups','relevant_groups','irrelevant_groups',
            'unresolved_groups','stored_occurrences','code_applied_occurrences','unresolved_occurrences',
            'pending_groups','pending_occurrences','running_groups','running_occurrences',
            'waiting_provider_groups','waiting_provider_occurrences',
            'irrelevant_occurrences','writing_move_observations')}))
        counts['groups_in_queue'] += 1
        counts['stored_occurrences'] += len(rows)
        proof = result = None
        failure = None
        if group.get('receipt_path'):
            try:
                execution, submitted, original, _ = _receipt_execution(group['receipt_path'],receipts)
                if list(_groups_in(submitted,gid)):
                    actual_invocations.add((execution['provider'],execution['invocation_id']))
                    provider_batches[execution['provider']].add(execution['invocation_id'])
                    if execution['provider']=='codex_cli':
                        codex_cli_usage[execution['invocation_id']]={
                            'invocation_id':execution['invocation_id'],'events_ref':execution['events_ref'],
                            'usage_events':execution['usage_events']}
                    if len(list(_groups_in(original,gid))) == 1:
                        counts['llm_returned_groups'] += 1
                    for row in rows:
                        if row.get('model_representative'):
                            try:
                                representative_sentences[kind].add(_sentence_key(row))
                            except (ValueError,TypeError):
                                pass
            except (ValueError,OSError,TypeError,KeyError,IndexError):
                pass
        if group['state'] not in ('analyzed','unresolved') or not group.get('result_json'):
            failure = 'group_not_interpreted'
        else:
            try:
                result, proof = verify_group(group, receipts)
                invocations.add((proof['provider'],proof['invocation_id']))
                if group.get('occurrence_count') != len(rows):
                    raise ValueError('occurrence_count_mismatch')
            except (ValueError,OSError,TypeError,KeyError,IndexError) as error:
                failure = str(error)
        relevance = _relevance(result) if result else 'uncertain'
        if not failure and (group['state'] != 'analyzed' or relevance == 'uncertain'):
            failure = 'meaning_or_applicability_uncertain'
        if failure:
            pending = group['state'] == 'pending'
            running = group['state'] in ('codex_running','running','processing')
            waiting = group['state'] in ('waiting_provider_limit','waiting_codex_provider','waiting_provider')
            stage='waiting_provider' if waiting else 'running' if running else 'pending' if pending else 'unresolved'
            counts[stage+'_groups'] += 1
            counts[stage+'_occurrences'] += len(rows)
            if stage=='unresolved':
                reasons[failure] += 1
            unresolved.append({'group_id':gid,'kind':kind,'state':stage,
                'reason':'provider_waiting' if waiting else 'execution_in_progress' if running else failure,
                'occurrence_count':len(rows)})
            continue
        counts['llm_interpreted_groups'] += 1
        counts[relevance+'_groups'] += 1
        valid_rows = []
        for row in rows:
            try:
                key = _sentence_key(row)
            except (ValueError,TypeError) as error:
                counts['unresolved_occurrences'] += 1
                occurrence_reasons[str(error)] += 1
                continue
            if relevance == 'irrelevant':
                counts['irrelevant_occurrences'] += 1
                continue
            valid_rows.append(row)
            counts['code_applied_occurrences'] += 1
            sentences[kind].add(key)
            documents[kind].add(row['document_id'])
            document_versions[kind].add((row['document_id'],row['text_sha256']))
            stratum = (kind,row.get('source_role') or 'unknown',row.get('genre') or 'unknown')
            bucket = strata.setdefault(stratum, {'sentences':set(),'documents':set(),'document_versions':set(),'occurrences':0})
            bucket['sentences'].add(key); bucket['documents'].add(key[0])
            bucket['document_versions'].add(key[:2]); bucket['occurrences'] += 1
            applications.append({**row, 'result_sha256':proof['result_sha256'],
                'receipt_path':proof['receipt_path'], 'application_scope':'code_projection_from_expression_group',
                'semantic_review':'representative_passage_only' if row.get('model_representative') else 'context_reuse_candidate',
                'original_quote_reused_from_representative':False})
        group_rows.append({'group_id':gid,'kind':kind,'interpretation':result,'execution_ref':proof,
                           'valid_applicable_occurrences':len(valid_rows)})
        if relevance == 'relevant' and kind == 'experience' and _has_writing_content(result):
            fragments.append(_writing_fragment(group,result,proof,valid_rows))
            counts['writing_move_observations'] += 1
    for kind, counts in counters.items():
        counts['applied_unique_source_sentences'] = len(sentences[kind])
        counts['applied_source_documents'] = len(documents[kind])
        counts['applied_source_document_versions'] = len(document_versions[kind])
        counts['model_representative_unique_sentences'] = len(representative_sentences[kind])
    summary = {'version':'compact-writing-handoff-v1','created_at':datetime.now(timezone.utc).isoformat(),
        'ledger_path':str(ledger_path),'counts_by_kind':counters,
        'source_strata':[{'kind':k,'source_role':r,'genre':g,'applied_unique_source_sentences':len(v['sentences']),
            'applied_source_documents':len(v['documents']),
            'applied_source_document_versions':len(v['document_versions']),'code_applied_occurrences':v['occurrences']}
            for (k,r,g),v in sorted(strata.items())],
        'distinct_output_preserving_execution_batches':len(invocations),
        'distinct_execution_batches':len(actual_invocations),
        'distinct_output_preserving_model_invocations':None if (provider_batches['codex_subagent'] or provider_batches['codex_cli']) else len(invocations),
        'distinct_actual_model_invocations':None if (provider_batches['codex_subagent'] or provider_batches['codex_cli']) else len(actual_invocations),
        'verified_claude_model_executions':len(provider_batches['claude_cli']),
        'execution_batches_by_provider':{p:len(v) for p,v in provider_batches.items()},
        'completed_codex_subagent_batches':len(provider_batches['codex_subagent']),
        'completed_codex_cli_batches':len(provider_batches['codex_cli']),
        'codex_cli_usage_observations':list(codex_cli_usage.values()),
        'codex_cli_telemetry':{'model_name':None,'underlying_model_api_calls':None,
            'usage_basis':'exact turn.completed events, not estimated API call counts'},
        'codex_subagent_telemetry':{'independent_usage_telemetry_available':False,
            'underlying_model_api_calls':None,'model_name':None,'token_usage':None,
            'counting_unit':'completed agent batch, not underlying model API calls'},
        'execution_counting_unit':'receipt invocation_id; Codex entries count agent batches, not underlying model API calls',
        'cli_attempt_audit':_call_audit(ledger_path),
        'unresolved_group_reason_counts':dict(reasons),
        'unresolved_occurrence_reason_counts':dict(occurrence_reasons),'common_success_patterns_approved':0,
        'independent_human_content_reviews':0,'whole_documents_analyzed':None,
        'model_called_this_run':False,'network_requests':0,
        'counting_scope':'Groups, expression occurrences, anchor sentences and source documents are separate units; strata may overlap.',
        'scope':'Only local group interpretations and their stored source-sentence links; no whole-document completion claim.'}
    output_root = Path(output_root); output_root.mkdir(parents=True,exist_ok=True)
    for name,rows in [('group-interpretations.jsonl',group_rows),('applied-occurrences.jsonl',applications),
                      ('writing-fragments.jsonl',fragments),('unresolved-groups.jsonl',unresolved)]:
        _write_jsonl(output_root/name,rows)
    _write_jsonl(output_root/'summary.jsonl',[summary])
    target=output_root/'summary.json';temporary=output_root/'summary.json.tmp'
    temporary.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');temporary.replace(target)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compact-root',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--output-root',type=Path)
    parser.add_argument('--artifact-lock-fd',type=int)
    args=parser.parse_args()
    output_root=args.output_root or args.compact_root/'handoff';output_root.mkdir(parents=True,exist_ok=True)
    lock_path=output_root/'.refresh.lock'
    if args.artifact_lock_fd is not None:
        verify_inherited_lock_fd(args.artifact_lock_fd,lock_path)
        print(json.dumps(build_handoff(args.compact_root/'queue.sqlite',output_root),ensure_ascii=False))
        return
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'state':'refresh_skipped_lock_busy','model_called_this_run':False}))
            return
        print(json.dumps(build_handoff(args.compact_root/'queue.sqlite',output_root),ensure_ascii=False))


def verify_inherited_lock_fd(fd,path):
    inherited=os.fstat(fd);expected=Path(path).stat()
    if (not stat.S_ISREG(inherited.st_mode)
            or (inherited.st_dev,inherited.st_ino)!=(expected.st_dev,expected.st_ino)):
        raise ValueError('Inherited artifact lock FD does not match output lock inode')


if __name__ == '__main__':
    main()
