"""Synthetic read-only queue monitoring, cache reuse and fixed pipeline tests."""
from copy import deepcopy
import fcntl
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_benefitlens_team_handoff as team


class TeamHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.compact=self.base/'compact';self.compact.mkdir();(self.compact/'codex-corpus-v1').mkdir()
        self.output=self.base/'team-expansion-20261120/operations'
        self.queue=self.compact/'queue.sqlite';cx=sqlite3.connect(self.queue)
        cx.executescript("CREATE TABLE groups(group_id TEXT PRIMARY KEY,kind TEXT,state TEXT,result_json TEXT,receipt_path TEXT); INSERT INTO groups VALUES('one','experience','analyzed','{\"x\":\"a\"}','receipt-one'); INSERT INTO groups VALUES('two','notice','pending',NULL,NULL);")
        cx.commit();cx.close()
        self.ledger=self.compact/'codex-corpus-v1/ledger.sqlite';cx=sqlite3.connect(self.ledger)
        cx.executescript('CREATE TABLE batches(kind TEXT,state TEXT,cli_invoked INTEGER,model_called INTEGER); CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);')
        cx.execute('INSERT INTO batches VALUES(?,?,?,?)',('notice','completed',1,1));cx.commit();cx.close()
        old=self.base/'experience';old.mkdir()
        for name in ('documents.jsonl.gz','cases.jsonl.gz','segments.jsonl.gz'):(old/name).write_bytes(b'synthetic-'+name.encode())
        self.executed=[]
        self.worker_patch=patch.object(team,'worker_state',return_value={'service':team.SERVICE,'active_state':'active'})
        self.worker_patch.start()

    def tearDown(self):self.worker_patch.stop();self.tmp.cleanup()

    def fake_stage(self,name,argv,summary_path,folder,lock_fd=None,fail=None):
        self.executed.append(name)
        team.save(summary_path,{'stage':name,'source_documents':3,'model_calls':0,'sequence':len(self.executed)})
        sr,origin=team.capture_summary(summary_path,folder,name)
        return {'name':name,'state':'failed' if name==fail else 'completed','command':argv,
            'summary_ref':sr,'original_summary_ref':origin,'error':'synthetic failure' if name==fail else None}

    def cycle(self,fail=None,with_pdf=False):
        fn=lambda *a,**kw:self.fake_stage(*a,**kw,fail=fail)
        with patch.object(team,'run_stage',side_effect=fn):
            return team.run_cycle(self.base,'test_snapshot',with_pdf)

    def test_monitor_separates_real_batches_and_queue_states_without_writes(self):
        cx=sqlite3.connect(self.queue)
        cx.executemany('INSERT INTO groups VALUES(?,?,?,?,?)',[(str(i),'notice',state,None,None) for i,state in enumerate(['codex_running','waiting_codex_provider','unresolved','codex_interrupted'])]);cx.commit();cx.close()
        cx=sqlite3.connect(self.ledger);cx.execute('INSERT INTO batches VALUES(?,?,?,?)',('experience','completed',0,0));cx.commit();cx.close()
        before=self.queue.read_bytes();snapshot=team.monitor(self.base)
        self.assertEqual(snapshot['actual_completed_semantic_batches'],1)
        self.assertEqual(snapshot['queue_state_buckets'],{'pending':1,'running':1,'waiting':1,'unresolved':2,'analyzed':1,'other':0})
        self.assertEqual(self.queue.read_bytes(),before)
        cx=team.connect_readonly(self.queue)
        with self.assertRaises(sqlite3.OperationalError):cx.execute("UPDATE groups SET state='pending'")
        cx.close()

    def test_trigger_is_batch_interval_source_change_or_worker_stop(self):
        observed=team.monitor(self.base);stamps={'source':'same'}
        previous={'completed_batches_at_refresh':0,'source_stamps':stamps,'monitor_end':deepcopy(observed)}
        observed['actual_completed_semantic_batches']=24
        self.assertIsNone(team.trigger_reason(observed,previous,stamps,25))
        observed['actual_completed_semantic_batches']=25
        self.assertEqual(team.trigger_reason(observed,previous,stamps,25),'completed_batch_interval')
        observed['actual_completed_semantic_batches']=1
        self.assertEqual(team.trigger_reason(observed,previous,{'source':'new'},25),'source_snapshot_changed')
        observed['worker_state']={'active_state':'inactive'}
        self.assertEqual(team.trigger_reason(observed,previous,stamps,25),'worker_stopped')
        self.assertEqual(team.trigger_reason(observed,previous,stamps,25,True),'forced_snapshot')

    def test_new_experience_root_requires_all_manifests(self):
        self.assertEqual(team.choose_experience(self.base)[0],self.base/'experience')
        fresh=self.base/'team-expansion-20261120/source-refresh-20261120/experience';fresh.mkdir(parents=True)
        for name in ('documents.jsonl.gz','cases.jsonl.gz'):(fresh/name).write_bytes(b'new')
        self.assertEqual(team.choose_experience(self.base)[0],self.base/'experience')
        (fresh/'segments.jsonl.gz').write_bytes(b'new')
        self.assertEqual(team.choose_experience(self.base)[0],fresh)

    def test_fixed_stage_order_and_quality_reuse_flag(self):
        steps=team.stages(self.base,self.base/'experience',12)
        self.assertEqual([s[0] for s in steps],['compact_handoff','source_links','experience_quality','personalization','writing_registry','condition_schema_links','writing_pattern_links','operations'])
        self.assertTrue(all(argv[0]==sys.executable for _,argv,_ in steps))
        self.assertIn('--artifact-lock-fd',steps[0][1]);self.assertIn('--reuse-structure',steps[2][1])
        self.assertIn('--experience-root',steps[4][1])

    def test_cycle_preserves_summary_refs_and_source_queue_bytes(self):
        before=self.queue.read_bytes();status=self.cycle();snapshot=team.read(status['snapshot_ref']['path'])
        self.assertEqual(self.queue.read_bytes(),before)
        self.assertEqual(snapshot['coordinator_source_queue_writes'],0)
        self.assertEqual(snapshot['coordinator_model_calls'],0)
        self.assertEqual(len(snapshot['stages']),8)
        for stage in snapshot['stages']:
            copy=Path(stage['summary_ref']['path']);self.assertEqual(sha256(copy.read_bytes()).hexdigest(),stage['summary_ref']['sha256'])
        old=Path(snapshot['stages'][0]['summary_ref']['path']).read_bytes()
        team.save(self.compact/'handoff/summary.json',{'changed':'later'})
        self.assertEqual(Path(snapshot['stages'][0]['summary_ref']['path']).read_bytes(),old)
        self.assertLess(len((self.output/'report.txt').read_text()),10000)
        completion=team.read(self.output/'team-completion-summary.json')
        self.assertIsNone(completion['stage0']['completed'])
        self.assertIsNone(completion['teams']['source_schema']['actual_llm_analyzed_notice_groups'])
        self.assertLess(len((self.output/'team-completion-report.txt').read_text()),10000)
        self.assertNotIn('\u00b7',(self.output/'team-completion-report.txt').read_text())

    def test_expensive_quality_stage_reuses_identical_sources_and_semantics(self):
        self.cycle();self.cycle()
        self.assertEqual(self.executed.count('experience_quality'),1)
        status=team.read(self.output/'team-refresh-status.json')
        self.assertEqual(status['stage_states']['experience_quality'],'reused_unchanged_inputs')
        cx=sqlite3.connect(self.queue);cx.execute("UPDATE groups SET result_json='{\"x\":\"b\"}' WHERE group_id='one'");cx.commit();cx.close()
        self.cycle();self.assertEqual(self.executed.count('experience_quality'),2)
        (self.base/'experience/documents.jsonl.gz').write_bytes(b'new source version')
        self.cycle();self.assertEqual(self.executed.count('experience_quality'),3)

    def test_quality_failure_skips_personalization_but_records_operations(self):
        status=self.cycle(fail='experience_quality')
        self.assertEqual(status['state'],'team_refresh_partial_failure')
        self.assertNotIn('personalization',self.executed)
        self.assertIn('operations',self.executed)
        self.assertEqual(status['stage_states']['personalization'],'skipped_dependency_failure')

    def test_artifact_lock_busy_does_not_overwrite_active_materials(self):
        lockpath=self.compact/'handoff/.refresh.lock';lockpath.parent.mkdir()
        with lockpath.open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(team.run_cycle(self.base,'test')['state'],'artifact_refresh_busy')
        self.assertFalse(self.output.joinpath('team-refresh-status.json').exists())

    def test_stage_subprocess_is_fixed_argv_and_streams_large_output_to_files(self):
        folder=self.base/'snapshot';folder.mkdir();summary=self.base/'summary.json';team.save(summary,{'ok':True})
        with patch.object(team.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as runner:
            result=team.run_stage('fixture',[sys.executable,'fixed.py'],summary,folder,lock_fd=77)
        self.assertEqual(result['state'],'completed')
        self.assertEqual(runner.call_args.kwargs['pass_fds'],(77,))
        self.assertNotIn('capture_output',runner.call_args.kwargs);self.assertNotIn('shell',runner.call_args.kwargs)

    def test_final_state_does_not_claim_completion_with_pending_or_unresolved(self):
        observed=team.monitor(self.base)
        self.assertEqual(team.final_state(observed,[]),'processing')
        observed['worker_state']={'active_state':'inactive'}
        self.assertEqual(team.final_state(observed,[]),'stopped_with_pending')
        observed['queue_state_buckets']['pending']=0;observed['queue_state_buckets']['unresolved']=4
        self.assertEqual(team.final_state(observed,[]),'queue_drained_with_unresolved')
        observed['errors']=['queue unreadable']
        self.assertEqual(team.final_state(observed,[]),'snapshot_unavailable')

    def test_worker_exit_during_refresh_gets_a_final_followup_snapshot(self):
        first=team.monitor(self.base);last=deepcopy(first);last['worker_state']={'active_state':'inactive'}
        with patch.object(team,'monitor',side_effect=[first,last]):status=self.cycle()
        self.assertTrue(status['worker_stopped_during_refresh'])
        self.assertEqual(team.trigger_reason(last,status,status['source_stamps'],25),'worker_stopped_during_previous_refresh')

    def test_source_change_during_refresh_is_not_lost_by_the_checkpoint(self):
        calls=0
        def changing_stage(*args,**kwargs):
            nonlocal calls
            result=self.fake_stage(*args,**kwargs);calls+=1
            if calls==3:(self.base/'experience/documents.jsonl.gz').write_bytes(b'changed during the stage')
            return result
        with patch.object(team,'run_stage',side_effect=changing_stage):status=team.run_cycle(self.base,'test')
        self.assertNotEqual(status['source_stamps'],status['observed_source_stamps_at_end'])
        observed=team.monitor(self.base)
        self.assertEqual(team.trigger_reason(observed,status,team.source_stamps(self.base/'experience'),25),'source_snapshot_changed')

    def test_optional_pdf_is_post_snapshot_with_fixed_parameters(self):
        repo=self.base/'repo';(repo/'tools').mkdir(parents=True);(repo/'tools/build_benefitlens_scope_report.py').write_text('# fixture')
        with patch.object(team,'REPO',repo):status=self.cycle(with_pdf=True)
        pdf=team.read(status['pdf_stage_ref']['path']);argv=pdf['command']
        self.assertEqual(Path(argv[1]).name,'build_benefitlens_scope_report.py')
        self.assertEqual(argv[2],'--snapshot');self.assertEqual(argv[4],'--output-root')
        self.assertEqual(reference_sha(status['snapshot_ref']['path']),status['snapshot_ref']['sha256'])

    def test_queue_drain_with_active_worker_catches_up_below_batch_interval_once(self):
        observed=team.monitor(self.base);prior=deepcopy(observed)
        previous={'completed_batches_at_refresh':observed['actual_completed_semantic_batches'],
            'source_stamps':{},'monitor_end':prior,'refresh_checkpoint':team.checkpoint(prior)}
        observed['queue_state_buckets']['pending']=0
        observed['queue_state_buckets']['analyzed']+=1;observed['actual_analyzed_groups']+=1
        observed['queue_counts']=[{'kind':'experience','state':'analyzed','groups':1},{'kind':'notice','state':'analyzed','groups':1}]
        self.assertEqual(team.trigger_reason(observed,previous,{},25),'queue_boundary_catchup')
        previous['refresh_checkpoint']=team.checkpoint(observed);previous['monitor_end']=observed
        self.assertIsNone(team.trigger_reason(observed,previous,{},25))

    def test_provider_pause_and_queue_failure_trigger_without_25_new_batches(self):
        observed=team.monitor(self.base);prior=deepcopy(observed)
        previous={'completed_batches_at_refresh':1,'source_stamps':{},'monitor_end':prior,'refresh_checkpoint':team.checkpoint(prior)}
        observed['provider_waiting']={'reason':'provider_usage_limit'}
        self.assertEqual(team.trigger_reason(observed,previous,{},25),'queue_boundary_catchup')
        observed['provider_waiting']=None;observed['queue_state_buckets']['unresolved']=1
        self.assertEqual(team.trigger_reason(observed,previous,{},25),'queue_failure_catchup')

    def test_final_queue_drain_during_refresh_requires_followup_even_with_active_worker(self):
        first=team.monitor(self.base);last=deepcopy(first)
        last['queue_state_buckets']['pending']=0;last['queue_state_buckets']['analyzed']+=1;last['actual_analyzed_groups']+=1
        last['queue_counts']=[{'kind':'experience','state':'analyzed','groups':1},{'kind':'notice','state':'analyzed','groups':1}]
        with patch.object(team,'monitor',side_effect=[first,last]):status=self.cycle()
        self.assertTrue(status['catchup_required'])
        self.assertEqual(team.trigger_reason(last,status,status['source_stamps'],25),'final_downstream_catchup')
        with patch.object(team,'monitor',return_value=last):status=self.cycle()
        self.assertFalse(status['catchup_required'])
        self.assertIsNone(team.trigger_reason(last,status,status['source_stamps'],25))

    def test_completed_pattern_and_code_changes_invalidate_inputs_without_self_refresh_loop(self):
        repo=self.base/'repo';(repo/'tools').mkdir(parents=True);code=repo/'tools/link_benefitlens_writing_patterns.py';code.write_text('first')
        pattern=self.base/'team-expansion-20261120/semantic-connections/writing/pattern-summary.json'
        with patch.object(team,'REPO',repo):
            old=team.source_stamps(self.base/'experience',self.base)
            team.save(pattern,{'actual_successful_full_paragraph_analyses':1})
            pattern_changed=team.source_stamps(self.base/'experience',self.base)
            self.assertNotEqual(old,pattern_changed)
            team.save(pattern.parent/'summary.json',{'generated_projection':123})
            self.assertEqual(pattern_changed,team.source_stamps(self.base/'experience',self.base))
            team.save(pattern,{'actual_successful_full_paragraph_analyses':1,'at':'new idle observation','pending':123})
            self.assertEqual(pattern_changed,team.source_stamps(self.base/'experience',self.base))
            code.write_text('second code revision')
            self.assertNotEqual(pattern_changed,team.source_stamps(self.base/'experience',self.base))

    def test_group_interval_can_trigger_before_batch_interval(self):
        observed=team.monitor(self.base);previous={'completed_batches_at_refresh':1,'analyzed_groups_at_refresh':1,'source_stamps':{},'monitor_end':observed}
        observed=deepcopy(observed);observed['actual_analyzed_groups']=1001
        self.assertEqual(team.trigger_reason(observed,previous,{},25,group_interval=1000),'completed_group_interval')

    def test_failed_condition_dependency_and_registry_dependency_are_explicit(self):
        status=self.cycle(fail='personalization')
        self.assertEqual(status['stage_states']['condition_schema_links'],'skipped_dependency_failure')
        self.assertEqual(status['stage_states']['writing_pattern_links'],'completed')
        status=self.cycle(fail='writing_registry')
        self.assertEqual(status['stage_states']['writing_pattern_links'],'skipped_dependency_failure')
        self.assertEqual(status['stage_states']['condition_schema_links'],'completed')

    def test_actual_completion_uses_verified_counts_not_source_inventories_or_failed_summaries(self):
        payloads={
            'compact_handoff':{'counts_by_kind':{'notice':{'llm_interpreted_groups':7,'code_applied_occurrences':11},'experience':{'llm_interpreted_groups':5,'code_applied_occurrences':6}}},
            'condition_schema_links':{'counts':{'verified_relevant_mapping_instances':19,'verified_linked_unique_candidate_notices':3}},
            'writing_pattern_links':{'counts':{'actual_successful_full_paragraph_analyses':2,'content_approved_patterns':0}},
            'experience_quality':{'source_documents':999999,'whole_document_latent_semantics_completed':0},
            'writing_registry':{'source_rows_routed':999999,'local_writing_observations':4}}
        snapshot={'stages':[{'name':name,'state':'completed'} for name in payloads]}
        summary=team.completion_summary(snapshot,payloads)
        self.assertEqual(summary['teams']['source_schema']['actual_llm_analyzed_notice_groups'],7)
        self.assertEqual(summary['teams']['source_schema']['source_schema_mapping_instances'],19)
        self.assertEqual(summary['teams']['writing']['actual_full_paragraph_llm_analyses'],2)
        self.assertEqual(summary['teams']['experience_quality']['whole_document_llm_analyses'],0)
        self.assertNotIn('999,999',team.completion_report(summary))
        snapshot['stages'][2]['state']='failed'
        summary=team.completion_summary(snapshot,payloads)
        self.assertIsNone(summary['teams']['writing']['actual_full_paragraph_llm_analyses'])

    def test_actual_paragraph_worker_keys_keep_analysis_and_generic_inputs_separate(self):
        payload={'counts':{'generic_criterion_linked_observations':3,'generic_criterion_input_links':8,'source_verified_full_paragraph_analyses_materialized':1,
                           'actual_pattern_source_occurrence_case_links':3,'actual_semantic_criterion_assessment_links':5},
                 'paragraph_analysis':{'successful_full_paragraph_analyses':2,'active_successful_full_paragraph_analyses':1,
                                       'source_bound_pattern_occurrence_links':4,'criterion_assessment_links':10,'completion_identity':'exact'},
                 'program_round_bound_patterns':0,'content_approved_patterns':0,'program_specific_approved_patterns':0}
        summary=team.completion_summary({'stages':[{'name':'writing_pattern_links','state':'completed'}]}, {'writing_pattern_links':payload})
        writing=summary['teams']['writing']
        self.assertEqual(writing['actual_full_paragraph_llm_analyses'],2)
        self.assertEqual(writing['active_full_paragraph_llm_analyses'],1)
        self.assertEqual(writing['criterion_linked_observations'],3)
        self.assertEqual(writing['full_paragraph_criterion_assessment_links'],10)
        self.assertEqual(writing['materialized_full_paragraph_analyses'],1)
        self.assertEqual(writing['materialized_pattern_source_occurrence_case_links'],3)
        self.assertEqual(writing['materialized_semantic_criterion_assessment_links'],5)
        self.assertEqual(writing['verified_program_round_bindings'],0)
        path=self.base/'pattern-summary.json';team.save(path,{'successful_full_paragraph_analyses':2,'completion_identity':'a','active_tasks':99})
        old=team.pattern_completion_stamp(path)
        team.save(path,{'successful_full_paragraph_analyses':2,'completion_identity':'a','active_tasks':1000,'at':'later'})
        self.assertEqual(old,team.pattern_completion_stamp(path))
        team.save(path,{'successful_full_paragraph_analyses':2,'completion_identity':'b'})
        self.assertNotEqual(old,team.pattern_completion_stamp(path))

    def test_linker_cohort_counts_stay_consistent_when_upstream_worker_advances(self):
        payloads={
            'compact_handoff':{'created_at':'2026-11-19T23:12:44+00:00','counts_by_kind':{
                'notice':{'llm_interpreted_groups':5941,'relevant_groups':3450,'code_applied_occurrences':10000},
                'experience':{'llm_interpreted_groups':6200,'relevant_groups':5559,'code_applied_occurrences':5736}}},
            'condition_schema_links':{'created_at':'2026-11-19T23:15:30+00:00','counts':{
                'actual_llm_analyzed_distinct_notice_groups':6028,'actual_llm_relevant_distinct_notice_groups':3508,
                'verified_linked_distinct_notice_groups':3508,'verified_linked_source_occurrences':10123}},
            'writing_pattern_links':{'generated_at':'2026-11-19T23:16:00+00:00','counts':{
                'analyzed_compact_observations_seen':6300,'verified_relevant_compact_observations':5600,
                'source_bound_occurrence_links':5800,'paragraph_linked_observations':5580,
                'paragraph_case_linked_observations':5570}}}
        snapshot={'stages':[{'name':name,'state':'completed'} for name in payloads]}
        teams=team.completion_summary(snapshot,payloads)['teams'];notice=teams['source_schema']
        self.assertEqual(notice['actual_llm_analyzed_notice_groups'],6028)
        self.assertEqual(notice['relevant_llm_analyzed_notice_groups'],3508)
        self.assertEqual(notice['code_applied_source_occurrences'],10123)
        self.assertLessEqual(notice['source_schema_linked_notice_groups'],notice['relevant_llm_analyzed_notice_groups'])
        self.assertEqual(notice['count_cohorts']['relevant_llm_analyzed_notice_groups'],
                         {'stage':'condition_schema_links','as_of':'2026-11-19T23:15:30+00:00'})
        for name in ('experience_quality','writing'):
            self.assertEqual(teams[name]['actual_llm_analyzed_experience_groups'],6300)
            self.assertEqual(teams[name]['relevant_llm_analyzed_experience_groups'],5600)
            self.assertEqual(teams[name]['count_cohorts']['relevant_llm_analyzed_experience_groups']['stage'],'writing_pattern_links')
        self.assertLessEqual(teams['experience_quality']['paragraph_linked_semantic_groups'],teams['experience_quality']['relevant_llm_analyzed_experience_groups'])

    def test_missing_linker_cohort_fields_preserve_explicit_handoff_fallback_asof(self):
        payloads={'compact_handoff':{'created_at':'2026-11-19T23:12:44+00:00','counts_by_kind':{
            'notice':{'llm_interpreted_groups':7,'relevant_groups':4},
            'experience':{'llm_interpreted_groups':8,'relevant_groups':5}}},
            'condition_schema_links':{'created_at':'2026-11-19T23:15:30+00:00','counts':{'verified_linked_distinct_notice_groups':4}},
            'writing_pattern_links':{'generated_at':'2026-11-19T23:16:00+00:00','counts':{'paragraph_linked_observations':5}}}
        snapshot={'stages':[{'name':name,'state':'completed'} for name in payloads]}
        teams=team.completion_summary(snapshot,payloads)['teams']
        self.assertEqual(teams['source_schema']['actual_llm_analyzed_notice_groups'],7)
        self.assertEqual(teams['source_schema']['count_cohorts']['relevant_llm_analyzed_notice_groups'],
                         {'stage':'compact_handoff','as_of':'2026-11-19T23:12:44+00:00'})
        self.assertEqual(teams['writing']['count_cohorts']['actual_llm_analyzed_experience_groups']['stage'],'compact_handoff')
        payloads['condition_schema_links']['counts'].update({'actual_llm_analyzed_distinct_notice_groups':0,'actual_llm_relevant_distinct_notice_groups':0})
        teams=team.completion_summary(snapshot,payloads)['teams']
        self.assertEqual(teams['source_schema']['actual_llm_analyzed_notice_groups'],0)
        self.assertEqual(teams['source_schema']['relevant_llm_analyzed_notice_groups'],0)


def reference_sha(path):return sha256(Path(path).read_bytes()).hexdigest()


if __name__=='__main__':unittest.main()
