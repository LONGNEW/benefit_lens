"""Prepare reusable short requests for Codex subagents; never invoke a model."""
import argparse
from datetime import datetime,timezone
import importlib
import json
from pathlib import Path

import run_benefitlens_compact as runner


def prepare(root,kinds,batch_size):
    cx=runner.connect(root);jobs=[]
    for kind in kinds:
        groups,selection=runner.select_groups(cx,kind,batch_size)
        if not groups:continue
        module=importlib.import_module(runner.MODULES[kind])
        batch=module.build_batch(groups,batch_size=batch_size) if kind=='notice' else module.build_batch(groups)
        folder=root/'codex-prepared'/batch['batch_id'];folder.mkdir(parents=True,exist_ok=True)
        request_ref=runner.save(folder/'request.json',batch)
        runner.save(folder/'schema.json',module.result_schema())
        runner.save(folder/'selection.json',selection)
        jobs.append({'kind':kind,'groups':len(groups),'input_sha256':batch['input_sha256'],
            'request_ref':request_ref,'schema_path':str(folder/'schema.json'),
            'result_path':str(folder/'subagent-result.json'),
            'state':'prepared_not_analyzed','execution':'parent_dispatches_authorized_codex_subagent',
            'model_calls':0,'cli_calls':0})
    manifest={'created_at':datetime.now(timezone.utc).isoformat(),'jobs':jobs,
        'state':'prepared_not_analyzed','schema_and_source_context_preserved':True}
    runner.save(root/'codex-prepared/latest.json',manifest)
    return manifest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=runner.BASE/'compact')
    p.add_argument('--kinds',default='notice,experience')
    p.add_argument('--batch-size',type=int,default=40)
    args=p.parse_args();kinds=args.kinds.split(',')
    if not 1<=args.batch_size<=50 or set(kinds)-set(runner.MODULES):p.error('Invalid batch size or source kind')
    print(json.dumps(prepare(args.root,kinds,args.batch_size),ensure_ascii=False))


if __name__=='__main__':main()
