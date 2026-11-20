"""Operational tests use isolated fixtures, never the real completion ledger."""
import gzip
import json
from pathlib import Path
import sys
from jsonschema import Draft202012Validator

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_benefitlens_semantic_worker as worker


def fixture_store(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'BASE',tmp_path)
    root=tmp_path/'semantic'
    cx=worker.connect(root)
    return root,cx


def compressed(path,records):
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,'wt') as f:
        for row in records:f.write(json.dumps(row)+'\n')


def test_source_absence_and_null_scope_do_not_acquire_a_fake_completion(tmp_path,monkeypatch):
    root,cx=fixture_store(tmp_path,monkeypatch)
    compressed(tmp_path/'notice/notices.jsonl.gz',[
        {'candidate_key':'real','title':'Scholarship','original_ref':{'document_id':'original'}},
        {'candidate_key':'missing','original_ref':None},
        {'candidate_key':'demo','record_type':'sample'}])
    compressed(tmp_path/'experience/cases.jsonl.gz',[
        {'case_key':'real-case','active':True,'blockers':[],'source_roles':{'case_scope':None}}])
    assert worker.seed(cx)=={'notice':2,'experience':1}
    status=worker.status(cx,root)
    assert status['unit_states']['notice']=={'blocked':1,'queued':1}
    assert status['unit_states']['experience']=={'queued':1}
    assert status['llm_completed_units']=={'notice':0,'experience':0}
    assert worker.seed(cx)=={}


def test_chunk_completion_is_not_counted_as_case_completion(tmp_path,monkeypatch):
    root,cx=fixture_store(tmp_path,monkeypatch)
    cx.execute("INSERT INTO units(unit_id,kind,record_key,record_json,record_sha256,state,priority,task_count,completed_task_count) VALUES('case','experience','case','{}','source','running',0,2,1)")
    cx.commit()
    assert worker.status(cx,root)['llm_completed_units']['experience']==0


def test_prepared_or_running_work_is_not_an_actual_model_receipt(tmp_path,monkeypatch):
    root,cx=fixture_store(tmp_path,monkeypatch)
    cx.execute("INSERT INTO invocations(invocation_id,kind,state) VALUES('attempt','notice','running')")
    cx.commit()
    status=worker.status(cx,root)
    assert status['llm_completed_units']=={'notice':0,'experience':0}
    assert status['actual_cli_invocations']==1


def test_a_partial_failed_case_remains_incomplete(tmp_path,monkeypatch):
    root,cx=fixture_store(tmp_path,monkeypatch)
    cx.execute("INSERT INTO units(unit_id,kind,record_key,record_json,record_sha256,state,priority,task_count) VALUES('case','experience','case','{}','source','running',0,2)")
    for ident,state in [('part-one','completed'),('part-two','invalid')]:
        cx.execute('INSERT INTO tasks(job_id,unit_id,kind,record_key,task_id,input_sha256,input_path,schema_path,state) VALUES(?,?,?,?,?,?,?,?,?)',
            (ident,'case','experience','case',ident,'source','unused','unused',state))
    cx.commit();worker.finalize_ready(cx,root)
    assert cx.execute("SELECT state FROM units WHERE unit_id='case'").fetchone()[0]=='failed'
    assert worker.status(cx,root)['llm_completed_units']['experience']==0


def test_batch_wrapper_retains_root_references_used_by_the_real_notice_schema():
    original={'type':'object','properties':{'value':{'$ref':'#/$defs/number'}},
              'required':['value'],'$defs':{'number':{'type':'number'}}}
    schema=worker.batch_schema(original)
    Draft202012Validator(schema).validate({'results':[{'task_id':'real-task','analysis':{'value':3}}]})
    assert original['$defs']=={'number':{'type':'number'}}
