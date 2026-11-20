"""Subagent import provenance and idempotence, with synthetic source fixtures."""
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import benefitlens_compact_notice as notice
import import_benefitlens_codex_batch as importer
import run_benefitlens_compact as runner
from test_benefitlens_compact_notice import groups_for,response_for


def fixture(root):
    with patch.object(runner,'BASE',root):
        cx=runner.connect(root/'compact')
    group=groups_for('GPA of 3.0 is required.')[0]
    runner.add(cx,'notice',group['representative'],notice);cx.commit()
    batch=notice.build_batch([group]);raw=response_for(batch)
    request=root/'request.json';result=root/'result.json';completion=root/'completion.json'
    runner.save(request,batch)
    # Different formatting from the importer is intentional. Preserve raw bytes.
    result.write_text(runner.encoded(raw))
    proof={'agent_name':'/synthetic-fixture-only','invocation_id':'synthetic-codex-batch',
        'status':'completed','input_sha256':batch['input_sha256'],
        'response_sha256':runner.digest(result.read_bytes()),
        'completion_message':'Synthetic fixture, not actual model execution.'}
    runner.save(completion,proof)
    return cx,request,result,completion


def test_completed_subagent_is_imported_once_without_inference_and_keeps_raw_bytes(tmp_path):
    cx,request,result,completion=fixture(tmp_path)
    with patch.object(runner,'BASE',tmp_path),patch.object(runner.subprocess,'run',side_effect=AssertionError('Importer must never call a model')):
        first=importer.import_batch(tmp_path/'compact','notice',request,result,completion,'/synthetic-fixture-only')
        second=importer.import_batch(tmp_path/'compact','notice',request,result,completion,'/synthetic-fixture-only')
    assert first['interpreted_groups']==1 and second['state']=='already_imported'
    assert cx.execute('SELECT count(*) FROM calls').fetchone()[0]==1
    receipt=runner.read(first['receipt_path'])
    assert Path(receipt['response_ref']['path']).read_bytes()==result.read_bytes()
    assert receipt['usage'] is None and receipt['underlying_model_api_calls'] is None
    state=runner.status(cx,tmp_path/'compact')
    assert state['codex_subagent_batches']==1 and state['cli_attempts']==0
    assert state['actual_model_calls'] is None


def test_mismatched_completion_cannot_promote_groups(tmp_path):
    cx,request,result,completion=fixture(tmp_path)
    proof=runner.read(completion);proof['response_sha256']='0'*64;runner.save(completion,proof)
    with patch.object(runner,'BASE',tmp_path),pytest.raises(ValueError,match='completion evidence'):
        importer.import_batch(tmp_path/'compact','notice',request,result,completion,'/synthetic-fixture-only')
    assert cx.execute('SELECT count(*) FROM calls').fetchone()[0]==0
    assert cx.execute('SELECT state FROM groups').fetchone()[0]=='pending'
