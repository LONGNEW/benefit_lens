"""Compact accounting fixtures never call a model or count as real analysis."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from build_benefitlens_compact_handoff import build_handoff, canonical_hash, verify_inherited_lock_fd


def save(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False)+'\n')
    return {'path':str(path),'sha256':sha256(path.read_bytes()).hexdigest()}


def setup_store(root, results=None):
    if results is None:
        results=[{'group_id':'g1','relevance':'relevant','event_roles':['action'], 'actor':'self',
            'writing_move':'action supports a concrete claim','material_role':'own action',
            'claim_evidence_reasoning':{'claim':'contribution','evidence':'specific action','reasoning':None},
            'opening_or_closing_function':{'opening':'introduce action','closing':None,'scope':'visible_passage_only'},
            'local_connection':{'kind':'not_observed','description':None},'limits':['local passage only'],
            'support_quote':'I wrote the tool'}]
    raw_results=deepcopy(results)
    checked=[dict(r,semantic_review='unreviewed') for r in results]
    response=save(root/'response.json',{'modelUsage':{'synthetic-model':{'inputTokens':1}},
        'structured_output':{'results':raw_results}})
    request=save(root/'request.json',{'groups':[{'group_id':r['group_id'],'context':'I wrote the tool.'} for r in results]})
    parsed=save(root/'results.json',{'results':checked})
    receipt={'provider':'claude_cli','model_called':True,'models':['synthetic-model'],
        'invocation_id':'synthetic-fixture-only','input_sha256':canonical_hash({'groups':results}),
        'request_ref':request,'response_ref':response,'result_ref':parsed,
        'group_results':[{'group_id':r['group_id'],'result_sha256':canonical_hash(r),
            'result_pointer':'results/'+str(i),'raw_result_sha256':canonical_hash(raw_results[i])} for i,r in enumerate(checked)]}
    receipt_path=root/'receipt.json';save(receipt_path,receipt)
    database=root/'queue.sqlite'
    with sqlite3.connect(database) as db:
        db.executescript('''CREATE TABLE groups(group_id TEXT PRIMARY KEY,kind TEXT,group_key TEXT,
            representative_json TEXT,occurrence_count INTEGER,state TEXT,result_json TEXT,receipt_path TEXT);
            CREATE TABLE occurrences(occurrence_id TEXT PRIMARY KEY,group_id TEXT,kind TEXT,
            record_key TEXT,document_id TEXT,text_sha256 TEXT,char_start INTEGER,char_end INTEGER,
            context_start INTEGER,context_end INTEGER,source_url TEXT,source_role TEXT,genre TEXT,
            sentence_index INTEGER,paragraph_position TEXT,model_representative INTEGER);''')
        for i,r in enumerate(checked):
            kind='notice' if 'mappings' in r else 'experience'
            db.execute('INSERT INTO groups VALUES(?,?,?,?,?,?,?,?)',(r['group_id'],kind,'phrase-'+str(i),
                json.dumps({'text':'I wrote the tool.'}),1,'analyzed',json.dumps(r),str(receipt_path)))
            db.execute('INSERT INTO occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(
                'o'+str(i),r['group_id'],kind,'record-a','doc-a',sha256(b'source').hexdigest(),
                2+i,8+i,0,30,'https://example.test/source','application_text','application',
                0,'opening',1 if i==0 else 0))
    return database,receipt_path


def run(root):
    return build_handoff(root/'queue.sqlite',root/'handoff')


def test_two_group_occurrences_in_same_sentence_are_one_applied_sentence_and_one_invocation(tmp_path):
    database,_=setup_store(tmp_path)
    with sqlite3.connect(database) as db:
        row=list(db.execute('SELECT * FROM occurrences').fetchone());row[0]='o-extra';row[6]=10;row[7]=15;row[-1]=0
        db.execute('INSERT INTO occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',row)
        db.execute("UPDATE groups SET occurrence_count=2 WHERE group_id='g1'")
    summary=run(tmp_path);counts=summary['counts_by_kind']['experience']
    assert counts['llm_interpreted_groups']==1 and counts['code_applied_occurrences']==2
    assert counts['applied_unique_source_sentences']==counts['applied_source_documents']==1
    assert counts['model_representative_unique_sentences']==1
    assert summary['distinct_output_preserving_model_invocations']==1
    fragment=json.loads((tmp_path/'handoff/writing-fragments.jsonl').read_text())
    assert fragment['scope']=='visible_passage_only' and fragment['source_coverage']=='selected_occurrences_only'
    assert not fragment['approved_pattern'] and fragment['quality_score'] is None
    assert summary['whole_documents_analyzed'] is None


def test_uncertain_model_result_counts_returned_group_but_remains_unresolved(tmp_path):
    setup_store(tmp_path,[{'group_id':'g1','relevance':'uncertain','limits':['insufficient context']}])
    counts=run(tmp_path)['counts_by_kind']['experience']
    assert counts['llm_returned_groups']==counts['unresolved_groups']==1
    assert counts['llm_interpreted_groups']==counts['code_applied_occurrences']==0
    assert counts['model_representative_unique_sentences']==1


def test_notice_mapping_and_irrelevant_reference_are_distinguished(tmp_path):
    setup_store(tmp_path,[{'group_id':'g1','mappings':[{'field_id':'education.degree','operator':'eq','reference':'doctorate'}],
        'skip_reason':None},{'group_id':'g2','mappings':[],'skip_reason':'not an applicant requirement'}])
    summary=run(tmp_path);counts=summary['counts_by_kind']['notice']
    assert counts['llm_interpreted_groups']==2 and counts['relevant_groups']==counts['irrelevant_groups']==1
    assert counts['code_applied_occurrences']==counts['irrelevant_occurrences']==1
    assert not (tmp_path/'handoff/writing-fragments.jsonl').read_text()


@pytest.mark.parametrize('change,reason',[
    ('stored_result','group_result_hash_mismatch'),('response_file','artifact_hash_mismatch'),
    ('actual_meaning','model_semantic_fields_changed'),('model','actual_model_execution_not_receipted')])
def test_analyzed_state_alone_never_proves_actual_model_completion(tmp_path,change,reason):
    database,receipt_path=setup_store(tmp_path)
    if change=='stored_result':
        with sqlite3.connect(database) as db:
            result=json.loads(db.execute('SELECT result_json FROM groups').fetchone()[0]);result['writing_move']='invented'
            db.execute('UPDATE groups SET result_json=?',(json.dumps(result),))
    elif change=='response_file':
        save(tmp_path/'response.json',{'tampered':True})
    elif change=='actual_meaning':
        with sqlite3.connect(database) as db:
            result=json.loads(db.execute('SELECT result_json FROM groups').fetchone()[0]);result['writing_move']='invented'
            db.execute('UPDATE groups SET result_json=?',(json.dumps(result),))
        receipt=json.loads(receipt_path.read_text())
        receipt['group_results'][0]['result_sha256']=canonical_hash(result)
        receipt['result_ref']=save(tmp_path/'results.json',{'results':[result]});save(receipt_path,receipt)
    else:
        receipt=json.loads(receipt_path.read_text());receipt['model_called']=False;save(receipt_path,receipt)
    summary=run(tmp_path)
    assert summary['counts_by_kind']['experience']['llm_interpreted_groups']==0
    assert summary['unresolved_group_reason_counts'][reason]==1


def test_bad_occurrence_does_not_inflate_source_sentence_or_document_counts(tmp_path):
    database,_=setup_store(tmp_path)
    with sqlite3.connect(database) as db:
        db.execute('UPDATE occurrences SET char_end=99')
    counts=run(tmp_path)['counts_by_kind']['experience']
    assert counts['llm_interpreted_groups']==1 and counts['unresolved_occurrences']==1
    assert counts['applied_unique_source_sentences']==counts['applied_source_documents']==0


def test_pending_expression_is_not_llm_completion(tmp_path):
    database,_=setup_store(tmp_path)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE groups SET state='pending',result_json=NULL,receipt_path=NULL")
    counts=run(tmp_path)['counts_by_kind']['experience']
    assert counts['llm_interpreted_groups']==counts['llm_returned_groups']==0
    assert counts['pending_groups']==counts['pending_occurrences']==1
    assert counts['unresolved_groups']==counts['unresolved_occurrences']==0


@pytest.mark.parametrize('state,stage',[
    ('codex_running','running'),('running','running'),('processing','running'),
    ('waiting_codex_provider','waiting_provider'),('waiting_provider_limit','waiting_provider'),
    ('waiting_provider','waiting_provider'),('pending','pending')])
def test_parallel_inflight_and_waiting_are_not_semantic_failures(tmp_path,state,stage):
    database,_=setup_store(tmp_path)
    with sqlite3.connect(database) as db:
        db.execute('UPDATE groups SET state=?,result_json=NULL,receipt_path=?',
            (state,str(tmp_path/'not-returned-yet.json')))
    summary=run(tmp_path);counts=summary['counts_by_kind']['experience']
    assert counts[stage+'_groups']==counts[stage+'_occurrences']==1
    assert counts['unresolved_groups']==counts['unresolved_occurrences']==0
    assert counts['llm_interpreted_groups']==counts['llm_returned_groups']==0
    assert not summary['unresolved_group_reason_counts']
    item=json.loads((tmp_path/'handoff/unresolved-groups.jsonl').read_text())
    assert item['state']==stage


def test_null_writing_fields_do_not_create_an_invented_pattern(tmp_path):
    setup_store(tmp_path,[{'group_id':'g1','relevance':'relevant','event_roles':[],
        'writing_move':None,'material_role':None,'claim_evidence_reasoning':{'claim':None,'evidence':None,'reasoning':None},
        'opening_or_closing_function':{'opening':None,'closing':None,'scope':'visible_passage_only'},
        'local_connection':{'kind':'not_observed','description':None}}])
    assert run(tmp_path)['counts_by_kind']['experience']['writing_move_observations']==0


def test_roles_and_genres_are_reported_in_separate_strata(tmp_path):
    database,_=setup_store(tmp_path)
    with sqlite3.connect(database) as db:
        row=list(db.execute('SELECT * FROM occurrences').fetchone())
        row[0]='advice';row[4]='doc-b';row[11]='textbook_section';row[12]='general_language';row[-1]=0
        db.execute('INSERT INTO occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',row)
        db.execute('UPDATE groups SET occurrence_count=2')
    strata=run(tmp_path)['source_strata']
    assert len(strata)==2
    assert {(r['source_role'],r['genre']) for r in strata}=={
        ('application_text','application'),('textbook_section','general_language')}


def test_unaccepted_raw_group_still_counts_actual_return_and_representative_exposure(tmp_path):
    database,_=setup_store(tmp_path,[{'group_id':'g1','relevance':'uncertain','limits':['not enough context']}])
    with sqlite3.connect(database) as db:
        db.execute("UPDATE groups SET state='unresolved',result_json=NULL")
    summary=run(tmp_path);counts=summary['counts_by_kind']['experience']
    assert counts['llm_returned_groups']==counts['unresolved_groups']==1
    assert counts['llm_interpreted_groups']==0 and counts['model_representative_unique_sentences']==1
    assert summary['distinct_actual_model_invocations']==1


def test_same_document_with_two_versions_is_one_document_and_two_versions(tmp_path):
    database,_=setup_store(tmp_path)
    with sqlite3.connect(database) as db:
        row=list(db.execute('SELECT * FROM occurrences').fetchone())
        row[0]='revised-source';row[5]=sha256(b'revised source').hexdigest();row[-1]=0
        db.execute('INSERT INTO occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',row)
        db.execute('UPDATE groups SET occurrence_count=2')
    counts=run(tmp_path)['counts_by_kind']['experience']
    assert counts['applied_source_documents']==1 and counts['applied_source_document_versions']==2
    assert counts['applied_unique_source_sentences']==2


def test_provider_limit_is_waiting_not_semantic_failure_or_model_execution(tmp_path):
    database,receipt_path=setup_store(tmp_path)
    receipt=json.loads(receipt_path.read_text())
    receipt.update(model_called=False,state='waiting_provider_limit',group_results=[],models=[])
    receipt['response_ref']=save(tmp_path/'response.json',{'modelUsage':{},'is_error':True,
        'api_error':'usage_limit_reached','api_error_status':429,'usage':{'input_tokens':0,'output_tokens':0}})
    save(receipt_path,receipt)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE groups SET state='waiting_provider_limit',result_json=NULL")
        db.execute('CREATE TABLE calls(invocation_id TEXT,kind TEXT,state TEXT,request_path TEXT,response_path TEXT,receipt_path TEXT)')
        db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?)',('synthetic-fixture-only','experience','waiting_provider_limit',
            receipt['request_ref']['path'],receipt['response_ref']['path'],str(receipt_path)))
    summary=run(tmp_path);counts=summary['counts_by_kind']['experience'];audit=summary['cli_attempt_audit']
    assert counts['waiting_provider_groups']==counts['waiting_provider_occurrences']==1
    assert counts['unresolved_groups']==counts['pending_groups']==counts['llm_interpreted_groups']==0
    assert not summary['unresolved_group_reason_counts']
    assert audit['cli_attempts']==audit['verified_cli_attempts']==audit['waiting_provider_attempts']==1
    assert audit['actual_model_executions']==audit['verified_input_tokens']==audit['verified_output_tokens']==0
    assert not audit['file_verification_errors']
    assert summary['distinct_actual_model_invocations']==0


def setup_codex_store(root):
    import benefitlens_compact_experience as compact
    database,_=setup_store(root)
    text='I learned to test the prototype.'
    passage={'passage_id':'p1','kind':'experience','document_id':'doc-a',
        'text_sha256':sha256(text.encode()).hexdigest(),'char_start':0,'char_end':len(text),
        'text':text,'context_start':0,'context_end':len(text),'context_text':text,
        'case_key':'case-a','source_url':'https://example.test/source','source_role':'participation_review',
        'genre':'review','sentence_index':0,'paragraph_position':'middle'}
    occurrence=compact.tag_passage(passage)
    batch=compact.build_batch([{'group_id':'g1','representative':occurrence,
        'occurrence_count':1,'source_refs':[compact.source_ref(occurrence)]}])
    raw={'group_id':'g1','relevance':'relevant','event_roles':['reflection'],'actor':'self',
        'writing_move':'An action is connected to learning.','material_role':'reflection',
        'claim_evidence_reasoning':{'claim':None,'evidence':None,'reasoning':None},
        'opening_or_closing_function':{'opening':None,'closing':None},
        'local_connection':{'kind':'not_observed','description':None},
        'limits':['visible passage only'],'support_quote':'I learned to test'}
    checked=compact.validate_group(raw,batch['groups'][0])
    request_ref=save(root/'codex-request.json',batch)
    response_ref=save(root/'codex-result.json',{'groups':[raw]})
    completion={'agent_name':'/root/synthetic_fixture','invocation_id':'synthetic-codex-batch',
        'status':'completed','input_sha256':batch['input_sha256'],'response_sha256':response_ref['sha256'],
        'completion_message':'Synthetic contract fixture; not a real model execution.'}
    receipt={'provider':'codex_subagent','kind':'experience','agent_name':completion['agent_name'],
        'model_called':True,'invocation_id':completion['invocation_id'],'input_sha256':batch['input_sha256'],
        'request_ref':request_ref,'response_ref':response_ref,'result_ref':response_ref,
        'usage':None,'independent_usage_telemetry_available':False,
        'completion_message_ref':save(root/'completion.json',completion),
        'group_results':[{'group_id':'g1','result_sha256':canonical_hash(checked),
            'raw_result_sha256':canonical_hash(raw),'result_pointer':'/groups/0'}]}
    receipt_path=root/'codex-receipt.json';save(receipt_path,receipt)
    with sqlite3.connect(database) as db:
        db.execute('UPDATE groups SET result_json=?,receipt_path=?',(json.dumps(checked),str(receipt_path)))
        db.execute('UPDATE occurrences SET text_sha256=?,char_start=0,char_end=?,context_start=0,context_end=?',
            (passage['text_sha256'],len(text),len(text)))
        db.execute('CREATE TABLE calls(invocation_id TEXT,kind TEXT,state TEXT,request_path TEXT,response_path TEXT,receipt_path TEXT)')
        db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?)',(receipt['invocation_id'],'experience','completed',
            request_ref['path'],response_ref['path'],str(receipt_path)))
    return database,receipt_path


def test_codex_batch_has_completion_provenance_without_invented_cli_usage(tmp_path):
    setup_codex_store(tmp_path)
    summary=run(tmp_path);counts=summary['counts_by_kind']['experience'];audit=summary['cli_attempt_audit']
    assert counts['llm_interpreted_groups']==1
    assert summary['completed_codex_subagent_batches']==1
    assert summary['execution_batches_by_provider']['codex_subagent']==1
    assert summary['distinct_actual_model_invocations'] is None
    assert summary['codex_subagent_telemetry']['underlying_model_api_calls'] is None
    assert summary['codex_subagent_telemetry']['token_usage'] is None
    assert audit['cli_attempts']==audit['actual_model_executions']==0
    assert audit['verified_codex_subagent_batches']==1 and not audit['file_verification_errors']
    proof=json.loads((tmp_path/'handoff/group-interpretations.jsonl').read_text())['execution_ref']
    assert proof['source_binding_validation']=='domain_source_binding_validator_replayed_on_preserved_request'
    assert not proof['independent_usage_telemetry_available']


@pytest.mark.parametrize('tamper,reason',[
    ('agent','codex_completion_identity_or_hash_mismatch'),
    ('completion_hash','artifact_hash_mismatch'),('invented_usage','codex_usage_telemetry_boundary_missing'),
    ('semantic','model_semantic_fields_changed'),('missing_group','group_absent_or_duplicated_in_actual_response'),
    ('source_binding','source_binding_validation_changed')])
def test_codex_completion_and_semantic_or_source_tampering_are_rejected(tmp_path,tamper,reason):
    database,receipt_path=setup_codex_store(tmp_path)
    receipt=json.loads(receipt_path.read_text())
    if tamper=='agent':
        receipt['agent_name']='/root/another_agent'
    elif tamper=='completion_hash':
        Path(receipt['completion_message_ref']['path']).write_text('{}')
    elif tamper=='invented_usage':
        receipt['usage']={'input_tokens':100}
    elif tamper in ('semantic','source_binding'):
        with sqlite3.connect(database) as db:
            checked=json.loads(db.execute('SELECT result_json FROM groups').fetchone()[0])
            if tamper=='semantic':checked['writing_move']='invented new meaning'
            else:checked['representative_source_ref']['document_id']='foreign-document'
            db.execute('UPDATE groups SET result_json=?',(json.dumps(checked),))
        receipt['group_results'][0]['result_sha256']=canonical_hash(checked)
    else:
        receipt['response_ref']=receipt['result_ref']=save(tmp_path/'codex-result.json',{'groups':[]})
        completion=json.loads(Path(receipt['completion_message_ref']['path']).read_text())
        completion['response_sha256']=receipt['response_ref']['sha256']
        receipt['completion_message_ref']=save(tmp_path/'completion.json',completion)
    save(receipt_path,receipt)
    summary=run(tmp_path)
    assert summary['counts_by_kind']['experience']['llm_interpreted_groups']==0
    assert summary['unresolved_group_reason_counts'][reason]==1


def setup_codex_cli_store(root):
    import benefitlens_compact_experience as compact
    database,receipt_path=setup_codex_store(root)
    receipt=json.loads(receipt_path.read_text());request=json.loads(Path(receipt['request_ref']['path']).read_text())
    raw=json.loads(Path(receipt['result_ref']['path']).read_text())
    with sqlite3.connect(database) as db:
        checked=json.loads(db.execute('SELECT result_json FROM groups').fetchone()[0])
    usage={'input_tokens':100,'cached_input_tokens':60,'output_tokens':25}
    events=[{'type':'thread.started','thread_id':'synthetic-thread'},
        {'type':'item.completed','item':{'id':'i1','type':'agent_message','text':json.dumps(raw)}},
        {'type':'turn.completed','usage':usage}]
    path=root/'codex-events.jsonl';path.write_text(''.join(json.dumps(e)+'\n' for e in events))
    event_ref={'path':str(path),'sha256':sha256(path.read_bytes()).hexdigest()}
    prompt=root/'prompt.txt';prompt.write_text('Protect source\n'+request['request']['system']+'\n'+request['request']['user'])
    receipt.update(provider='codex_cli',cli_invoked=True,exit_code=0,thread_id='synthetic-thread',
        events_ref=event_ref,response_ref=event_ref,
        prompt_ref={'path':str(prompt),'sha256':sha256(prompt.read_bytes()).hexdigest()},
        actual_batch_schema_ref=save(root/'batch-schema.json',compact.result_schema()),
        final_response_ref=save(root/'final-response.json',raw),raw_result_ref=receipt['result_ref'],
        validated_result_ref=save(root/'validated.json',{'groups':[checked]}),
        turn_completed_usage=usage,usage_events=[{'event_index':2,'type':'turn.completed','usage':usage}])
    receipt['group_results'][0]['raw_result_pointer']='/groups/0'
    save(receipt_path,receipt)
    with sqlite3.connect(database) as db:
        db.execute('UPDATE calls SET response_path=?',(str(path),))
    return database,receipt_path


def test_codex_cli_uses_actual_completed_turn_and_final_file_not_subagent_evidence(tmp_path):
    setup_codex_cli_store(tmp_path)
    summary=run(tmp_path)
    assert summary['counts_by_kind']['experience']['llm_interpreted_groups']==1
    assert summary['completed_codex_cli_batches']==1 and summary['completed_codex_subagent_batches']==0
    assert summary['codex_cli_usage_observations'][0]['usage_events'][0]['usage']['output_tokens']==25
    assert summary['codex_cli_telemetry']['underlying_model_api_calls'] is None
    audit=summary['cli_attempt_audit']
    assert audit['codex_cli_attempts']==audit['verified_codex_cli_attempts']==1
    assert not audit['file_verification_errors'] and audit['cli_attempts']==0


@pytest.mark.parametrize('change,reason',[
    ('usage','codex_cli_usage_events_mismatch'),('prompt','codex_cli_actual_prompt_input_mismatch'),
    ('final','codex_cli_final_and_parsed_result_differ')])
def test_codex_cli_event_usage_prompt_and_final_artifacts_are_independently_checked(tmp_path,change,reason):
    _,receipt_path=setup_codex_cli_store(tmp_path)
    receipt=json.loads(receipt_path.read_text())
    if change=='usage':receipt['usage_events'][0]['usage']['output_tokens']=999
    elif change=='prompt':
        path=Path(receipt['prompt_ref']['path']);path.write_text('wrong actual stdin')
        receipt['prompt_ref']['sha256']=sha256(path.read_bytes()).hexdigest()
    else:receipt['final_response_ref']=save(tmp_path/'final-response.json',{'groups':[]})
    save(receipt_path,receipt)
    summary=run(tmp_path)
    assert summary['counts_by_kind']['experience']['llm_interpreted_groups']==0
    assert summary['unresolved_group_reason_counts'][reason]==1


def test_inherited_lock_is_matched_by_inode_not_only_a_claimed_file_name(tmp_path):
    expected=tmp_path/'.refresh.lock';other=tmp_path/'other.lock'
    with expected.open('a') as valid,other.open('a') as invalid:
        verify_inherited_lock_fd(valid.fileno(),expected)
        with pytest.raises(ValueError,match='inode'):
            verify_inherited_lock_fd(invalid.fileno(),expected)
