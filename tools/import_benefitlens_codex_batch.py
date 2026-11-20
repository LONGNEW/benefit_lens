"""Import an actually completed Codex subagent batch without calling a model.

Completion evidence is supplied by the parent after receiving the agent's real
completion message. Provider usage, model IDs and underlying API call counts are
unavailable for this execution path and are never invented here.
"""
import argparse
from datetime import datetime,timezone
import importlib
import json
from pathlib import Path

import run_benefitlens_compact as runner


def import_batch(root,kind,request_path,result_path,completion_path,agent_name):
    cx=runner.connect(root)
    batch=runner.read(request_path);raw=runner.read(result_path);completion=runner.read(completion_path)
    raw_hash=runner.digest(Path(result_path).read_bytes())
    if (completion.get('status')!='completed' or completion.get('agent_name')!=agent_name
            or completion.get('input_sha256')!=batch['input_sha256']
            or completion.get('response_sha256')!=raw_hash
            or not completion.get('completion_message')):
        raise ValueError('Actual subagent completion evidence does not match the batch')
    ident=completion['invocation_id']
    if cx.execute('SELECT 1 FROM calls WHERE invocation_id=?',(ident,)).fetchone():
        return {'state':'already_imported','invocation_id':ident}
    module=importlib.import_module(runner.MODULES[kind])
    groups=batch['source_groups'] if kind=='notice' else batch['groups']
    for group in groups:
        existing=cx.execute('SELECT kind,representative_json FROM groups WHERE group_id=?',(group['group_id'],)).fetchone()
        if not existing or existing['kind']!=kind:
            raise ValueError('Subagent group is not in the shared expression store')
        if runner.encoded(json.loads(existing['representative_json']))!=runner.encoded(group['representative']):
            raise ValueError('Stored representative source differs from submitted batch')
    checked=module.validate_result(raw,batch,allow_partial=True)
    accepted={r['group_id']:r for r in checked.get('groups',[])}
    uncertain={r['group_id']:r['annotation'] for r in checked.get('unresolved_groups',[]) if r.get('annotation')}
    raw_indices={r['group_id']:i for i,r in enumerate(raw.get('groups',[]))}
    folder=root/'codex-subagents-20261120'/ident;folder.mkdir(parents=True,exist_ok=True)
    request_ref=runner.save(folder/'request.json',batch)
    response_path=folder/'response.json';response_path.write_bytes(Path(result_path).read_bytes())
    response_ref={'path':str(response_path),'sha256':raw_hash}
    checked_ref=runner.save(folder/'validated-result.json',checked)
    completion_ref=runner.save(folder/'completion.json',completion)
    receipt={'provider':'codex_subagent','agent_name':agent_name,'model_called':True,
        'invocation_id':ident,'kind':kind,'input_sha256':batch['input_sha256'],
        'request_ref':request_ref,'response_ref':response_ref,'result_ref':response_ref,
        'completion_message_ref':completion_ref,'validated_result_ref':checked_ref,
        'created_at':datetime.now(timezone.utc).isoformat(),'state':'completed',
        'usage':None,'models':None,'underlying_model_api_calls':None,
        'independent_usage_telemetry_available':False,
        'execution_unit':'completed_codex_subagent_batch',
        'execution_evidence':'parent_received_actual_collaboration_agent_completion',
        'elapsed_seconds':None,'group_results':[],'issues':checked.get('issues',checked.get('unresolved_groups',[]))}
    for group in groups:
        gid=group['group_id'];result=accepted.get(gid) or uncertain.get(gid)
        state='analyzed' if gid in accepted else 'unresolved'
        raw_row=raw['groups'][raw_indices[gid]] if gid in raw_indices else None
        receipt['group_results'].append({'group_id':gid,'state':state,
            'result_sha256':runner.digest(runner.encoded(result)) if result is not None else None,
            'raw_result_sha256':runner.digest(runner.encoded(raw_row)) if raw_row is not None else None,
            'result_pointer':'/groups/'+str(raw_indices[gid]) if gid in raw_indices else None})
        cx.execute('UPDATE groups SET state=?,result_json=?,receipt_path=? WHERE group_id=?',
            (state,runner.encoded(result) if result is not None else None,str(folder/'receipt.json'),gid))
        rep=group['representative']
        cx.execute('UPDATE occurrences SET model_representative=1 WHERE group_id=? AND document_id=? AND char_start=? AND char_end=?',
            (gid,rep['document_id'],rep['char_start'],rep['char_end']))
    runner.save(folder/'receipt.json',receipt)
    cx.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)',(ident,kind,'completed',len(groups),
        request_ref['path'],response_ref['path'],str(folder/'receipt.json'),None,None))
    cx.commit()
    result={'kind':kind,'state':'imported','subagent_batches':1,'interpreted_groups':len(accepted),
        'unresolved_groups':len(groups)-len(accepted),'receipt_path':str(folder/'receipt.json'),
        'independent_usage_telemetry_available':False}
    runner.status(cx,root)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=runner.BASE/'compact')
    p.add_argument('--kind',choices=list(runner.MODULES),required=True)
    p.add_argument('--request',type=Path,required=True)
    p.add_argument('--result',type=Path,required=True)
    p.add_argument('--completion',type=Path,required=True)
    p.add_argument('--agent-name',required=True)
    args=p.parse_args()
    print(json.dumps(import_batch(args.root,args.kind,args.request,args.result,args.completion,args.agent_name),ensure_ascii=False))


if __name__=='__main__':main()
