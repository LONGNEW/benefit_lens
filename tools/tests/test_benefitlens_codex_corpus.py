"""Temporary synthetic CLI fixtures only; never calls a model or production DB."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import benefitlens_compact_notice as notice
import run_benefitlens_codex_corpus as worker


class CodexCorpusTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.compact=self.base/'compact';self.compact.mkdir();self.root=self.compact/'codex-corpus-v1';self.root.mkdir()
        self.queue=self.compact/'queue.sqlite';c=sqlite3.connect(self.queue)
        c.executescript('''CREATE TABLE groups(group_id TEXT PRIMARY KEY,kind TEXT,group_key TEXT,
        representative_json TEXT,occurrence_count INTEGER DEFAULT 1,state TEXT DEFAULT 'pending',result_json TEXT,receipt_path TEXT);
        CREATE TABLE occurrences(occurrence_id TEXT PRIMARY KEY,group_id TEXT,document_id TEXT,
        char_start INTEGER,char_end INTEGER,model_representative INTEGER DEFAULT 0);''');c.close()
        self.source,self.ledger=worker.connect(self.queue,self.root/'ledger.sqlite');self.calls=[]

    def tearDown(self):
        self.source.close();self.ledger.close();self.tmp.cleanup()

    def populate(self,count):
        for i in range(count):
            text='Applicants must have GPA of 3.0 for fixture '+str(i)+'.'
            record={'record_type':'production_candidate','scope':'group_representative_original',
                'candidate_key':'synthetic-'+str(i),'source_url':'https://example.test/fixture',
                'original_ref':{'document_id':'fixture-'+str(i),'text_sha256':sha256(text.encode()).hexdigest()}}
            rep=next(o for o in notice.extract_contexts(record,source_text=text) if 'education.gpa' in o['tags'])
            gid=rep['group_id']
            self.source.execute('INSERT INTO groups(group_id,kind,group_key,representative_json) VALUES(?,?,?,?)',
                (gid,'notice',rep['group_key'],worker.encoded(rep)))
            self.source.execute('INSERT INTO occurrences(occurrence_id,group_id,document_id,char_start,char_end) VALUES(?,?,?,?,?)',
                (rep['occurrence_id'],gid,rep['document_id'],rep['char_start'],rep['char_end']))
        self.source.commit()

    def fake_cli(self,folder,executable,timeout=900,reasoning_effort='low',mode='success'):
        self.calls.append(str(folder));batch=worker.read(folder/'request.json')
        groups=[{'group_id':g['group_id'],'mappings':[],'skip_reason':'합성 테스트용 제외 사례'} for g in batch['source_groups']]
        if mode=='partial':groups[-1]['skip_reason']=None
        raw={k:batch[k] for k in ('version','batch_id','input_sha256')}|{'groups':groups}
        events=[{'type':'thread.started','thread_id':'synthetic-test-not-production'},
            {'type':'item.completed','item':{'type':'agent_message','text':worker.encoded(raw)}},
            {'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':20,'output_tokens':40}}]
        code=0
        if mode=='quota':events=[{'type':'error','message':"You've hit your usage limit"}];code=1
        if mode=='mismatch':raw['groups'][0]['skip_reason']='Changed final artifact'
        (folder/'events.jsonl').write_text('\n'.join(worker.encoded(e) for e in events)+'\n')
        (folder/'stderr.txt').write_text('');worker.save(folder/'final-response.json',raw)
        execution={'cli_invoked':True,'exit_code':code,'elapsed_seconds':0.01,
            'completed_at':worker.utc(),'interrupted':False,'timed_out':False,'command':['synthetic-fixture-no-real-cli']}
        worker.save(folder/'execution.json',execution);return execution

    def run_main(self,*arguments,mode='success'):
        runner=lambda *a,**kw:self.fake_cli(*a,**kw,mode=mode)
        with patch.object(worker,'BASE',self.base),patch.object(worker.shutil,'which',return_value='/fake/test/codex'),patch.object(worker,'run_cli',side_effect=runner):
            return worker.main(['--root',str(self.compact),'--kinds','notice',*arguments])

    def test_transport_only_removes_unsupported_schema_keywords(self):
        original=notice.result_schema();saved=deepcopy(original)
        transport=worker.transport_schema(original)
        self.assertEqual(original,saved)
        self.assertNotIn('allOf',transport['$defs']['mapping'])
        self.assertIn('allOf',original['$defs']['mapping'])
        special={'properties':{'not':{'type':'string'}},'not':{'type':'number'}}
        self.assertIn('not',worker.transport_schema(special)['properties'])
        self.assertEqual(transport['properties']['version']['enum'],[notice.VERSION])
        self.assertEqual(transport['$defs']['mapping']['properties']['field_id']['type'],'string')

    def test_command_has_read_only_tools_disabled_and_no_model_override(self):
        argv=worker.command('/test/codex',self.root)
        self.assertEqual(argv[-1],'-');self.assertIn('read-only',argv)
        for feature in ('shell_tool','apps','multi_agent'):self.assertIn(feature,argv)
        self.assertIn('--ephemeral',argv);self.assertNotIn('--model',argv);self.assertNotIn('--profile',argv)
        self.assertIn('web_search="disabled"',argv)
        self.assertIn('model_reasoning_effort="low"',argv)
        self.assertIn('model_reasoning_effort="high"',worker.command('/test/codex',self.root,'high'))

    def test_default_drains_all_pending_and_preserves_actual_group_receipts(self):
        self.populate(45);self.assertEqual(self.run_main(),0)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.source.execute("SELECT count(*) FROM groups WHERE state='analyzed'").fetchone()[0],45)
        summary=worker.read(self.root/'summary.json');self.assertEqual(summary['state'],'pending_drained')
        self.assertEqual(summary['observed_usage_totals']['input_tokens'],200)
        self.assertEqual(self.ledger.execute('SELECT count(*) FROM group_results').fetchone()[0],45)
        receipt=worker.read(Path(self.calls[0])/'receipt.json')
        self.assertEqual(receipt['provider'],'codex_cli');self.assertTrue(receipt['model_called'])
        self.assertEqual(receipt['usage_events'][0]['event_index'],2)
        batch=worker.read(receipt['request_ref']['path']);prompt=Path(receipt['prompt_ref']['path']).read_text()
        self.assertIn(batch['request']['user'],prompt)
        self.assertTrue(all(g['result_pointer'].startswith('/groups/') for g in receipt['group_results']))

    def test_fifty_group_notice_argument_is_forwarded(self):
        self.populate(50);self.assertEqual(self.run_main('--batch-size','50'),0)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(len(worker.read(Path(self.calls[0])/'request.json')['source_groups']),50)

    def test_explicit_executable_pin_wins_over_path(self):
        self.populate(1)
        with patch.object(worker,'BASE',self.base),patch.object(worker.shutil,'which',return_value='/different/path/codex'),patch.object(worker,'run_cli',side_effect=self.fake_cli) as runner:
            self.assertEqual(worker.main(['--root',str(self.compact),'--kinds','notice','--executable',sys.executable]),0)
        self.assertEqual(runner.call_args.args[1],str(Path(sys.executable).absolute()))

    def test_explicit_non_executable_pin_is_rejected_before_claiming_groups(self):
        self.populate(1)
        invalid=self.base/'not-executable';invalid.write_text('fixture')
        with patch.object(worker,'BASE',self.base),self.assertRaises(SystemExit) as error:
            worker.main(['--root',str(self.compact),'--executable',str(invalid)])
        self.assertEqual(error.exception.code,2)
        self.assertEqual(self.source.execute("SELECT count(*) FROM groups WHERE state='pending'").fetchone()[0],1)

    def test_hundred_group_chunks_run_four_at_once_with_one_writer_and_no_duplicates(self):
        self.populate(420);barrier=threading.Barrier(4)
        def concurrent_cli(*a,**kw):
            barrier.wait(timeout=5)
            return self.fake_cli(*a,**kw)
        with patch.object(worker,'BASE',self.base),patch.object(worker.shutil,'which',return_value='/fake/test/codex'),patch.object(worker,'run_cli',side_effect=concurrent_cli):
            self.assertEqual(worker.main(['--root',str(self.compact),'--kinds','notice','--batch-size','100','--concurrency','4','--max-batches','4']),0)
        self.assertEqual(len(self.calls),4)
        ids=[]
        for folder in self.calls:
            groups=worker.read(Path(folder)/'request.json')['source_groups']
            self.assertEqual(len(groups),100);ids.extend(g['group_id'] for g in groups)
        self.assertEqual(len(set(ids)),400)
        self.assertEqual(dict(self.source.execute('SELECT state,count(*) FROM groups GROUP BY state').fetchall()),{'analyzed':400,'pending':20})
        self.assertEqual(self.ledger.execute('SELECT count(*) FROM group_results').fetchone()[0],400)
        self.assertEqual(worker.read(self.root/'runtime.json')['database_writers'],1)

    def test_input_budget_splits_batch_and_leaves_tail_pending_without_truncation(self):
        self.populate(100);groups=worker.select_groups(self.source,self.ledger,'notice',100)
        batch=notice.build_batch(groups,batch_size=100)
        limit=(len(batch['request']['user'])+len(batch['request']['system']))//2
        ident,folder=worker.prepare(self.source,self.ledger,self.root,'notice',groups,100,max_input_characters=limit)
        actual=worker.read(folder/'request.json');kept=actual['source_groups']
        self.assertLess(len(kept),100);self.assertGreater(len(kept),0)
        self.assertEqual(kept,groups[:len(kept)])
        self.assertLessEqual(len(actual['request']['user'])+len(actual['request']['system']),limit)
        self.assertEqual(self.source.execute("SELECT count(*) FROM groups WHERE state='pending'").fetchone()[0],100-len(kept))

    def test_parallel_provider_errors_drain_inflight_and_resume_all_owned_waiting(self):
        self.populate(420);barrier=threading.Barrier(4);gate=threading.Lock();started=[]
        def parallel_cli(*a,**kw):
            with gate:
                number=len(started);started.append(str(a[0]))
            barrier.wait(timeout=5)
            return self.fake_cli(*a,**kw,mode='quota' if number<2 else 'success')
        with patch.object(worker,'BASE',self.base),patch.object(worker.shutil,'which',return_value='/fake/test/codex'),patch.object(worker,'run_cli',side_effect=parallel_cli):
            # Bound the first wave: a sibling may fail only after an earlier
            # success returns, so completion order cannot promise zero refill.
            self.assertEqual(worker.main(['--root',str(self.compact),'--kinds','notice','--batch-size','100','--concurrency','4','--max-batches','4']),2)
        self.assertEqual(len(self.calls),4)
        self.assertEqual(dict(self.source.execute('SELECT state,count(*) FROM groups GROUP BY state').fetchall()),{'analyzed':200,'pending':20,'waiting_codex_provider':200})
        self.assertEqual(self.run_main('--resume-provider','--batch-size','100','--concurrency','4'),0)
        self.assertEqual(self.source.execute("SELECT count(*) FROM groups WHERE state='analyzed'").fetchone()[0],420)

    def test_drain_file_finishes_inflight_without_claiming_next_wave(self):
        self.populate(420)
        def draining_cli(*a,**kw):
            (self.root/'drain.request').write_text('synthetic graceful stop')
            return self.fake_cli(*a,**kw)
        with patch.object(worker,'BASE',self.base),patch.object(worker.shutil,'which',return_value='/fake/test/codex'),patch.object(worker,'run_cli',side_effect=draining_cli):
            self.assertEqual(worker.main(['--root',str(self.compact),'--kinds','notice','--batch-size','100','--concurrency','4']),0)
        self.assertGreater(len(self.calls),0);self.assertLessEqual(len(self.calls),4)
        self.assertEqual(self.source.execute("SELECT count(*) FROM groups WHERE state='codex_running'").fetchone()[0],0)
        self.assertEqual(worker.read(self.root/'summary.json')['state'],'drained_for_restart')

    def test_larger_chunk_bound_and_invalid_concurrency_are_rejected_before_calls(self):
        for flag,value in [('--batch-size','201'),('--concurrency','0'),('--concurrency','9'),('--max-input-characters','3999')]:
            with self.subTest(flag=flag,value=value),self.assertRaises(SystemExit):self.run_main(flag,value)
        self.assertFalse(self.calls)

    def test_max_batches_is_explicit_partial_budget_not_completion(self):
        self.populate(45);self.assertEqual(self.run_main('--max-batches','1'),0)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.source.execute("SELECT count(*) FROM groups WHERE state='pending'").fetchone()[0],5)
        self.assertEqual(worker.read(self.root/'summary.json')['state'],'max_batches_reached')

    def test_bad_group_does_not_discard_or_retry_good_siblings(self):
        self.populate(3);self.assertEqual(self.run_main(mode='partial'),0)
        self.assertEqual(len(self.calls),1)
        states=dict(self.source.execute('SELECT state,count(*) FROM groups GROUP BY state').fetchall())
        self.assertEqual(states,{'analyzed':2,'unresolved':1})

    def test_provider_limit_stops_next_call_and_survives_restart(self):
        self.populate(45);self.assertEqual(self.run_main(mode='quota'),2)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.run_main(),2);self.assertEqual(len(self.calls),1)
        self.assertEqual(self.source.execute("SELECT count(*) FROM groups WHERE state='pending'").fetchone()[0],5)
        self.assertEqual(worker.meta(self.ledger,'provider_waiting')['reason'],'provider_limit')

    def test_explicit_resume_releases_only_owned_provider_waiting_groups(self):
        self.populate(3);self.run_main(mode='quota')
        self.assertEqual(self.run_main('--resume-provider'),0)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.source.execute("SELECT count(*) FROM groups WHERE state='analyzed'").fetchone()[0],3)

    def test_recovery_replays_saved_execution_without_another_call(self):
        self.populate(3);groups=worker.select_groups(self.source,self.ledger,'notice',40)
        ident,folder=worker.prepare(self.source,self.ledger,self.root,'notice',groups,40)
        self.fake_cli(folder,'/fake')
        recovered=worker.recover(self.source,self.ledger)
        self.assertEqual(recovered['recovered'],1);self.assertEqual(len(self.calls),1)
        original=(folder/'receipt.json').read_bytes()
        self.ledger.execute("UPDATE batches SET state='running' WHERE invocation_id=?",(ident,));self.ledger.commit()
        worker.recover(self.source,self.ledger)
        self.assertEqual((folder/'receipt.json').read_bytes(),original)
        self.assertEqual(self.ledger.execute('SELECT count(*) FROM group_results').fetchone()[0],3)
        self.assertEqual(len(self.calls),1)

    def test_imported_actual_batch_keeps_its_input_sha_and_syncs_source_call_registry(self):
        self.populate(2);groups=worker.select_groups(self.source,self.ledger,'notice',40)
        original=notice.build_batch(groups)
        self.source.execute('''CREATE TABLE calls(invocation_id TEXT PRIMARY KEY,kind TEXT,state TEXT,
        groups_count INTEGER,request_path TEXT,response_path TEXT,receipt_path TEXT,elapsed_seconds REAL,error TEXT)''');self.source.commit()
        ident,folder=worker.prepare(self.source,self.ledger,self.root,'notice',groups,40,existing_batch=original)
        self.assertEqual(worker.read(folder/'request.json')['input_sha256'],original['input_sha256'])
        self.fake_cli(folder,'/fake')
        execution=worker.read(folder/'execution.json');execution['elapsed_seconds']=None
        execution['imported_execution_refs']={'description':'synthetic import fixture, no real model invocation'}
        worker.save(folder/'execution.json',execution)
        row=self.ledger.execute('SELECT * FROM batches WHERE invocation_id=?',(ident,)).fetchone()
        receipt=worker.finalize(self.source,self.ledger,row)
        self.assertIsNone(receipt['elapsed_seconds'])
        self.assertIn('imported_execution_refs',receipt)
        registered=self.source.execute('SELECT * FROM calls WHERE invocation_id=?',(ident,)).fetchone()
        self.assertEqual(registered['groups_count'],2)
        self.assertEqual(registered['response_path'],receipt['events_ref']['path'])
        worker.finalize(self.source,self.ledger,row)
        self.assertEqual(self.source.execute('SELECT count(*) FROM calls').fetchone()[0],1)
        self.assertEqual(len(self.calls),1)

    def test_interrupted_unknown_exit_is_held_not_resubmitted(self):
        self.populate(2);groups=worker.select_groups(self.source,self.ledger,'notice',40)
        worker.prepare(self.source,self.ledger,self.root,'notice',groups,40)
        worker.recover(self.source,self.ledger)
        self.assertEqual(self.source.execute("SELECT count(*) FROM groups WHERE state='codex_interrupted'").fetchone()[0],2)
        self.assertFalse(worker.select_groups(self.source,self.ledger,'notice',40));self.assertFalse(self.calls)

    def test_live_orphan_process_prevents_concurrent_model_calls(self):
        self.populate(1);groups=worker.select_groups(self.source,self.ledger,'notice',40)
        _,folder=worker.prepare(self.source,self.ledger,self.root,'notice',groups,40)
        worker.save(folder/'process.json',{'identity':worker.process_identity(os.getpid())})
        self.assertEqual(worker.recover(self.source,self.ledger)['state'],'waiting_orphan_cli')
        self.assertFalse(self.calls)

    def test_keyset_wrap_finds_earlier_pending_without_full_sort(self):
        self.populate(3);worker.set_meta(self.ledger,'cursor:notice','zzzzzz')
        groups=worker.select_groups(self.source,self.ledger,'notice',40);self.assertEqual(len(groups),3)
        plan=' '.join(str(tuple(r)) for r in self.source.execute("EXPLAIN QUERY PLAN SELECT group_id FROM groups WHERE kind='notice' AND state='pending' AND group_id>'' ORDER BY group_id LIMIT 40"))
        self.assertIn('codex_pending_key',plan);self.assertNotIn('TEMP B-TREE',plan)

    def test_auth_and_tool_events_are_distinct_from_group_text(self):
        p=self.root/'events.jsonl'
        p.write_text(worker.encoded({'type':'error','message':'HTTP 401 authentication failed'})+'\n')
        self.assertEqual(worker.inspect_events(p)['waiting_reason'],'provider_auth_or_access')
        p.write_text(worker.encoded({'type':'item.completed','item':{'type':'agent_message','text':'{"note":"quota aid"}'}})+'\n'+worker.encoded({'type':'turn.completed','usage':{'input_tokens':1}})+'\n')
        self.assertIsNone(worker.inspect_events(p)['waiting_reason'])
        p.write_text(worker.encoded({'type':'item.completed','item':{'type':'command_execution'}})+'\n')
        self.assertEqual(worker.inspect_events(p)['waiting_reason'],'unexpected_tool_activity')

    def test_mismatched_final_file_is_not_accepted_despite_completed_turn(self):
        self.populate(2);self.assertEqual(self.run_main(mode='mismatch'),0)
        self.assertEqual(self.source.execute("SELECT count(*) FROM groups WHERE state='unresolved'").fetchone()[0],2)
        receipt=worker.read(Path(self.calls[0])/'receipt.json');self.assertTrue(receipt['model_called'])
        self.assertIn('differs',receipt['error'])

    def test_handoff_refresh_is_a_fixed_local_argument_list(self):
        with patch.object(worker.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as runner:
            result=worker.refresh_handoff(self.compact,self.root)
        self.assertEqual(result['state'],'completed')
        argv=runner.call_args.args[0]
        self.assertEqual(argv[0],sys.executable)
        self.assertEqual(Path(argv[1]).name,'build_benefitlens_compact_handoff.py')
        self.assertEqual(argv[2:4],['--compact-root',str(self.compact)])
        self.assertEqual(argv[4],'--artifact-lock-fd')
        self.assertEqual(runner.call_args.kwargs['pass_fds'],(int(argv[5]),))
        self.assertNotIn('shell',runner.call_args.kwargs)

    def test_optional_refresh_runs_at_final_shutdown(self):
        self.populate(1)
        with patch.object(worker,'refresh_handoff',return_value={'state':'completed'}) as refresh:
            self.assertEqual(self.run_main('--refresh-handoff'),0)
        refresh.assert_called_once_with(self.compact.resolve(),self.root.resolve())


if __name__=='__main__':unittest.main()
