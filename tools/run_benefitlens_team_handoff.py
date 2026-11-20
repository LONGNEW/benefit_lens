"""Read-only monitoring and code-only team material refresh after real batches.

Only fixed Python entry points are called. Source queues and model workers are
never modified. Each stage summary is preserved as an immutable small snapshot.
"""
from __future__ import annotations
import argparse
import ast
from datetime import datetime,timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

BASE=Path('/home/longnew/.cache/datasets/benefitlens/scale50k-20261109/team-production-20261119')
REPO=Path(__file__).resolve().parents[1]
SERVICE='benefitlens-codex-corpus-20261120.service'
VERSION='benefitlens-team-refresh-v2'
SUCCESS_STATES={'completed','reused_unchanged_inputs'}
STAGE_DEPENDENCIES={
    'source_links':('compact_handoff',),
    'experience_quality':('compact_handoff',),
    'personalization':('compact_handoff','experience_quality'),
    'writing_registry':('compact_handoff','experience_quality'),
    'condition_schema_links':('compact_handoff','personalization'),
    'writing_pattern_links':('compact_handoff','experience_quality','writing_registry'),
}


def utc():return datetime.now(timezone.utc).isoformat()
def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value):return sha256(value if isinstance(value,bytes) else encoded(value).encode()).hexdigest()
def read(path,default=None):
    try:return json.loads(Path(path).read_bytes())
    except (OSError,ValueError):return default
def reference(path):return {'path':str(Path(path).resolve()),'sha256':sha256(Path(path).read_bytes()).hexdigest()}


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    with tmp.open('w',encoding='utf8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
    tmp.replace(path);return reference(path)


def choose_experience(base):
    fresh=base/'team-expansion-20261120/source-refresh-20261120/experience'
    if all((fresh/name).exists() for name in ('documents.jsonl.gz','cases.jsonl.gz','segments.jsonl.gz')):
        return fresh,'new_source_snapshot_available'
    return base/'experience','new_snapshot_not_ready_fallback_to_preserved_experience'


def pattern_completion_stamp(path):
    """Ignore idle status timestamps and pending task counts from the worker."""
    value=read(path,{}) or {}
    names=('actual_successful_full_paragraph_analyses','completed_full_paragraph_analyses',
        'successful_full_paragraph_analyses','completion_identity',
        'completed_patterns','completed_analyses','actual_completed_pattern_analyses',
        'completed_results_sha256','completed_result_fingerprint','completed_pattern_fingerprint',
        'actual_llm_analyzed_patterns','analyzed','completed')
    selected={key:value[key] for key in names if key in value}
    for parent in ('counts','state_counts','analysis_counts'):
        if isinstance(value.get(parent),dict):selected[parent]={key:value[parent][key] for key in names if key in value[parent]}
    return {'completed_results_identity':digest(selected)}


def local_code_dependencies(paths):
    """Track imported repository helpers as well as the fixed entry points."""
    found=set();pending=list(paths)
    while pending:
        path=pending.pop()
        if path in found:continue
        found.add(path)
        if not path.exists():continue
        try:tree=ast.parse(path.read_text())
        except (OSError,SyntaxError,UnicodeError):continue
        for node in ast.walk(tree):
            modules=[entry.name for entry in node.names] if isinstance(node,ast.Import) else [node.module] if isinstance(node,ast.ImportFrom) and node.module else []
            for module in modules:
                dependency=REPO/'tools'/(module.split('.')[0]+'.py')
                if dependency.exists() and dependency not in found:pending.append(dependency)
    return found


def source_stamps(experience,base=None):
    result={}
    for name in ('documents.jsonl.gz','cases.jsonl.gz','segments.jsonl.gz'):
        path=experience/name
        if path.exists():
            st=path.stat();result[str(path)]={'size':st.st_size,'mtime_ns':st.st_mtime_ns}
        else:result[str(path)]=None
    config=REPO/'pipeline/config/experience_quality_policy_v1.json'
    # Include the actual quality config declared by its module, without importing
    # or executing the module's corpus processing code.
    quality=REPO/'tools/expand_benefitlens_experience_quality.py'
    if quality.exists():
        st=quality.stat();result[str(quality)]={'size':st.st_size,'mtime_ns':st.st_mtime_ns}
        for line in quality.read_text().splitlines():
            if line.startswith('CONFIG = ') or line.startswith('CONFIG='):
                import re
                names=re.findall(r"['\"]([^'\"]+\.json)['\"]",line)
                if names:config=REPO/'pipeline/config'/Path(names[-1]).name
    if config.exists():
        st=config.stat();result[str(config)]={'size':st.st_size,'mtime_ns':st.st_mtime_ns}
    # Rebuild derived artifacts after policy, schema or implementation changes.
    # These are inputs, whereas generated team summaries would trigger a loop.
    code_names=('run_benefitlens_team_handoff.py','build_benefitlens_compact_handoff.py',
        'audit_benefitlens_compact_source_links.py','expand_benefitlens_experience_quality.py',
        'expand_benefitlens_personalization.py','expand_benefitlens_writing_registry.py',
        'link_benefitlens_condition_schema.py','link_benefitlens_writing_patterns.py',
        'benefitlens_paragraph_patterns.py',
        'expand_benefitlens_operations.py','build_benefitlens_scope_report.py')
    paths=list(local_code_dependencies(REPO/'tools'/name for name in code_names))
    paths+=list((REPO/'pipeline/config').glob('*.json'))+list((REPO/'pipeline/schemas').glob('*.json'))
    if base is not None:
        paths.extend((base/'notice'/name for name in ('notices.jsonl.gz','documents.jsonl.gz')))
        # Worker output changes independently of this code-only coordinator.
        paths.append(base/'team-expansion-20261120/semantic-connections/writing/pattern-summary.json')
    for path in paths:
        if path.exists():
            if path.name=='pattern-summary.json':result[str(path)]=pattern_completion_stamp(path)
            else:
                st=path.stat();result[str(path)]={'size':st.st_size,'mtime_ns':st.st_mtime_ns}
        else:result[str(path)]=None
    return result


def connect_readonly(path):
    cx=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=10);cx.row_factory=sqlite3.Row
    cx.execute('PRAGMA query_only=ON');cx.execute('BEGIN');return cx


def worker_state():
    argv=['systemctl','--user','show',SERVICE,'--property=ActiveState,SubState,MainPID,ExecMainStatus']
    try:
        result=subprocess.run(argv,text=True,capture_output=True,timeout=10)
        values=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
        return {'service':SERVICE,'active_state':values.get('ActiveState','unknown'),
            'sub_state':values.get('SubState'),'main_pid':values.get('MainPID'),
            'exec_main_status':values.get('ExecMainStatus'),'probe_exit_code':result.returncode}
    except (OSError,subprocess.TimeoutExpired) as error:
        return {'service':SERVICE,'active_state':'unknown','error':type(error).__name__}


def queue_bucket(state):
    state=state or ''
    return 'pending' if state=='pending' else 'running' if state in ('running','codex_running','processing') else 'waiting' if any(t in state for t in ('waiting','paused','deferred')) else 'unresolved' if any(t in state for t in ('unresolved','invalid','failed','blocked','interrupted')) else 'analyzed' if state=='analyzed' else 'other'


def monitor(base):
    queue=base/'compact/queue.sqlite';ledger=base/'compact/codex-corpus-v1/ledger.sqlite'
    counts=[];batches=[];errors=[];completed=0;provider_waiting=None
    if queue.exists():
        try:
            cx=connect_readonly(queue)
            counts=[dict(r) for r in cx.execute('SELECT kind,state,count(*) AS groups FROM groups GROUP BY kind,state')];cx.close()
        except sqlite3.Error as error:errors.append('queue: '+str(error))
    else:errors.append('queue_missing')
    if ledger.exists():
        try:
            cx=connect_readonly(ledger)
            batches=[dict(r) for r in cx.execute('SELECT kind,state,count(*) AS batches,sum(cli_invoked) AS cli_invocations,sum(model_called) AS completed_model_turns FROM batches GROUP BY kind,state')]
            completed=cx.execute("SELECT count(*) FROM batches WHERE state='completed' AND cli_invoked=1 AND model_called=1").fetchone()[0]
            row=cx.execute("SELECT value FROM meta WHERE key='provider_waiting'").fetchone()
            provider_waiting=json.loads(row[0]) if row else None;cx.close()
        except (sqlite3.Error,ValueError) as error:errors.append('ledger: '+str(error))
    buckets={'pending':0,'running':0,'waiting':0,'unresolved':0,'analyzed':0,'other':0}
    for row in counts:
        buckets[queue_bucket(row['state'])]+=row['groups']
    return {'at':utc(),'queue_counts':counts,'queue_state_buckets':buckets,
        'semantic_batches':batches,'actual_completed_semantic_batches':completed,
        'actual_analyzed_groups':buckets['analyzed'],
        'provider_waiting':provider_waiting,'worker_state':worker_state(),'errors':errors}


def semantic_fingerprint(queue):
    h=sha256();cx=connect_readonly(queue)
    try:
        for row in cx.execute("SELECT group_id,result_json,receipt_path FROM groups WHERE kind='experience' AND state='analyzed' ORDER BY group_id"):
            h.update(encoded(list(row)).encode());h.update(b'\n')
    finally:cx.close()
    return h.hexdigest()


def fingerprints(base,experience,output):
    old=read(output/'fingerprint-cache.json',{})or{};stamps=source_stamps(experience,base);files={};new={}
    for name,stamp in stamps.items():
        if stamp is None:files[name]=None;continue
        previous=old.get(name)or{}
        value=previous.get('sha256') if previous.get('stamp')==stamp else sha256(Path(name).read_bytes()).hexdigest()
        new[name]={'stamp':stamp,'sha256':value};files[name]=value
    save(output/'fingerprint-cache.json',new)
    semantic=semantic_fingerprint(base/'compact/queue.sqlite')
    quality_code=local_code_dependencies([REPO/'tools/expand_benefitlens_experience_quality.py'])
    quality_inputs={key:value for key,value in files.items() if
        Path(key).parent==experience or Path(key) in quality_code or
        Path(key).name in ('experience_quality_policy_v1.json','experience_quality_expansion_v1.json')}
    return {'source_files':files,'source_files_sha256':digest(files),'experience_semantics_sha256':semantic,
        'quality_input_sha256':digest([quality_inputs,semantic])}


def phase(current):
    b=current['queue_state_buckets'];worker=current['worker_state'].get('active_state')
    if current.get('errors'):return 'snapshot_unavailable'
    if current.get('provider_waiting') or b['waiting']:return 'provider_paused'
    if worker in ('failed','inactive','deactivating'):return 'worker_'+worker
    if not b['pending'] and not b['running']:return 'queue_drained'
    return 'processing' if worker in ('active','activating') else 'worker_'+str(worker)


def checkpoint(current):
    """Cheap, stable observation: timestamps and transient worker PIDs are omitted."""
    return {'completed_batches':current['actual_completed_semantic_batches'],
        'analyzed_groups':current.get('actual_analyzed_groups',current['queue_state_buckets']['analyzed']),
        'queue_counts':sorted(current['queue_counts'],key=lambda r:(r['kind'],r['state'] or '')),
        'phase':phase(current),'provider_waiting':current.get('provider_waiting'),
        'worker_state':current['worker_state'].get('active_state'),'errors':current.get('errors',[])}


def trigger_reason(current,previous,stamps,batch_interval,forced=False,group_interval=None,stage_retry_seconds=300):
    if forced:return 'forced_snapshot'
    if not previous:return 'initial_snapshot'
    if previous.get('worker_stopped_during_refresh'):return 'worker_stopped_during_previous_refresh'
    if previous.get('catchup_required'):return 'final_downstream_catchup'
    if current['actual_completed_semantic_batches']-previous.get('completed_batches_at_refresh',0)>=batch_interval:
        return 'completed_batch_interval'
    if group_interval and current.get('actual_analyzed_groups',current['queue_state_buckets']['analyzed'])-previous.get('analyzed_groups_at_refresh',0)>=group_interval:
        return 'completed_group_interval'
    if stamps!=previous.get('source_stamps'):return 'source_snapshot_changed'
    old_monitor=previous.get('monitor_end')or{}
    old=(old_monitor.get('worker_state')or{}).get('active_state');new=current['worker_state'].get('active_state')
    if old in ('active','activating') and new not in ('active','activating','unknown'):return 'worker_stopped'
    prior=previous.get('refresh_checkpoint')
    if prior is None and old_monitor.get('queue_state_buckets'):prior=checkpoint(old_monitor)
    now=checkpoint(current)
    prior_unresolved=sum(row['groups'] for row in (prior or {}).get('queue_counts',[]) if any(t in (row['state'] or '') for t in ('unresolved','invalid','failed','blocked','interrupted')))
    if current['queue_state_buckets']['unresolved']>prior_unresolved:return 'queue_failure_catchup'
    if prior!=now and (now['phase']!='processing' or (prior or {}).get('phase')!='processing'):
        return 'queue_boundary_catchup'
    if previous.get('state')=='team_refresh_partial_failure' and previous.get('at'):
        try:elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(previous['at'])).total_seconds()
        except (ValueError,TypeError):elapsed=0
        if elapsed>=stage_retry_seconds:return 'failed_stage_retry'
    return None


def stages(base,experience,lock_fd):
    compact=base/'compact';expansion=base/'team-expansion-20261120';team=expansion/'experience-personalization'
    python=sys.executable;tools=REPO/'tools'
    return [
        ('compact_handoff',[python,str(tools/'build_benefitlens_compact_handoff.py'),'--compact-root',str(compact),'--artifact-lock-fd',str(lock_fd)],compact/'handoff/summary.json'),
        ('source_links',[python,str(tools/'audit_benefitlens_compact_source_links.py')],compact/'source-link-audit.json'),
        ('experience_quality',[python,str(tools/'expand_benefitlens_experience_quality.py'),'--root',str(team),'--experience-root',str(experience),'--compact-db',str(compact/'queue.sqlite'),'--reuse-structure'],team/'quality/summary.json'),
        ('personalization',[python,str(tools/'expand_benefitlens_personalization.py'),'--root',str(team),'--notice-root',str(base/'notice'),'--compact-db',str(compact/'queue.sqlite')],team/'personalization/summary.json'),
        ('writing_registry',[python,str(tools/'expand_benefitlens_writing_registry.py'),'--output-root',str(expansion/'writing-validation'),'--experience-root',str(experience)],expansion/'writing-validation/summary.json'),
        ('condition_schema_links',[python,str(tools/'link_benefitlens_condition_schema.py'),'--base',str(base),'--artifact-lock-fd',str(lock_fd)],expansion/'semantic-connections/conditions/summary.json'),
        ('writing_pattern_links',[python,str(tools/'link_benefitlens_writing_patterns.py'),'--base',str(base)],expansion/'semantic-connections/writing/summary.json'),
        ('operations',[python,str(tools/'expand_benefitlens_operations.py')],expansion/'operations/summary.json')]


def capture_summary(path,folder,name):
    if not path.exists():return None,None
    data=path.read_bytes();copy=folder/(name+'-summary.json')
    tmp=copy.with_name(copy.name+'.tmp');tmp.write_bytes(data);tmp.replace(copy)
    return reference(copy),{'path':str(path.resolve()),'sha256':sha256(data).hexdigest()}


def run_stage(name,argv,summary_path,folder,lock_fd=None):
    stdout=folder/(name+'-stdout.json');stderr=folder/(name+'-stderr.txt');started=time.monotonic();error=None
    with stdout.open('wb') as out,stderr.open('wb') as err:
        try:
            kwargs={'stdout':out,'stderr':err,'cwd':REPO,'timeout':3600}
            if lock_fd is not None:kwargs['pass_fds']=(lock_fd,)
            code=subprocess.run(argv,**kwargs).returncode
        except (OSError,subprocess.TimeoutExpired) as problem:
            code=None;error=type(problem).__name__+': '+str(problem)[:600]
    summary_ref,original_ref=capture_summary(summary_path,folder,name)
    state='completed' if code==0 and summary_ref else 'failed'
    if code==0 and not summary_ref:error='Expected stage summary was not produced'
    return {'name':name,'state':state,'command':argv,'exit_code':code,
        'elapsed_seconds':round(time.monotonic()-started,3),'stdout_ref':reference(stdout),
        'stderr_ref':reference(stderr),'summary_ref':summary_ref,'original_summary_ref':original_ref,'error':error}


def final_state(monitor_end,stage_results):
    b=monitor_end['queue_state_buckets'];active=monitor_end['worker_state'].get('active_state') in ('active','activating')
    if any(s['state'] in ('failed','skipped_dependency_failure') for s in stage_results):return 'team_refresh_partial_failure'
    if monitor_end.get('errors'):return 'snapshot_unavailable'
    if monitor_end.get('provider_waiting') or b['waiting']:return 'waiting_with_pending' if b['pending'] else 'waiting'
    if b['running']:return 'processing' if active else 'running_claims_without_active_worker'
    if b['pending']:return 'processing' if active else 'stopped_with_pending'
    if b['unresolved']:return 'queue_drained_with_unresolved'
    return 'queue_drained_reference_review_pending'


def completion_summary(snapshot,payloads=None):
    """Project evidenced counts without turning inventory or code work into LLM work."""
    payloads=payloads or {}
    stage_states={row['name']:row['state'] for row in snapshot.get('stages',[])}
    if not payloads:
        for row in snapshot.get('stages',[]):
            if row.get('state') not in SUCCESS_STATES:continue
            ref=row.get('summary_ref')
            if not ref:continue
            raw=Path(ref['path']).read_bytes()
            if ref.get('sha256') and sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('Snapshot summary bytes changed')
            payloads[row['name']]=json.loads(raw)
    payloads={key:value for key,value in payloads.items() if stage_states.get(key) in SUCCESS_STATES}
    handoff=payloads.get('compact_handoff',{});notice=handoff.get('counts_by_kind',{}).get('notice',{})
    experience=handoff.get('counts_by_kind',{}).get('experience',{})
    condition=payloads.get('condition_schema_links',{});cc=condition.get('counts',{})
    writing=payloads.get('writing_pattern_links',{});wc=writing.get('counts',{})
    quality=payloads.get('experience_quality',{});qc=quality.get('counters',{});analysis=writing.get('paragraph_analysis',{})
    personal=payloads.get('personalization',{});pc=personal.get('counters',{})
    registry=payloads.get('writing_registry',{})
    def field(obj,*keys):
        for key in keys:
            value=obj.get(key)
            if isinstance(value,(int,float)) and not isinstance(value,bool):return value
        return None
    def prefer_count(primary,key,fallback,fallback_key,stage):
        value=field(primary,key)
        return (value,stage) if value is not None else (field(fallback,fallback_key),'compact_handoff')
    def cohort(stage):
        source=payloads.get(stage,{})
        return {'stage':stage,'as_of':next((source[key] for key in ('at','created_at','at_kst','as_of','generated_at') if source.get(key)),None)}
    notice_analyzed,notice_analyzed_stage=prefer_count(cc,'actual_llm_analyzed_distinct_notice_groups',notice,'llm_interpreted_groups','condition_schema_links')
    notice_relevant,notice_relevant_stage=prefer_count(cc,'actual_llm_relevant_distinct_notice_groups',notice,'relevant_groups','condition_schema_links')
    notice_occurrences,notice_occurrences_stage=prefer_count(cc,'verified_linked_source_occurrences',notice,'code_applied_occurrences','condition_schema_links')
    experience_analyzed,experience_analyzed_stage=prefer_count(wc,'analyzed_compact_observations_seen',experience,'llm_interpreted_groups','writing_pattern_links')
    experience_relevant,experience_relevant_stage=prefer_count(wc,'verified_relevant_compact_observations',experience,'relevant_groups','writing_pattern_links')
    experience_occurrences,experience_occurrences_stage=prefer_count(wc,'source_bound_occurrence_links',experience,'code_applied_occurrences','writing_pattern_links')
    experience_cohorts={'actual_llm_analyzed_experience_groups':cohort(experience_analyzed_stage),
        'relevant_llm_analyzed_experience_groups':cohort(experience_relevant_stage),
        'code_applied_source_occurrences':cohort(experience_occurrences_stage)}
    teams={
        'source_schema':{'stage':'condition_schema_links','actual_llm_analyzed_notice_groups':notice_analyzed,
            'relevant_llm_analyzed_notice_groups':notice_relevant,
            'code_applied_source_occurrences':notice_occurrences,
            'count_cohorts':{'actual_llm_analyzed_notice_groups':cohort(notice_analyzed_stage),
                'relevant_llm_analyzed_notice_groups':cohort(notice_relevant_stage),
                'code_applied_source_occurrences':cohort(notice_occurrences_stage)},
            'source_schema_mapping_instances':cc.get('verified_relevant_mapping_instances'),
            'source_schema_linked_notice_groups':cc.get('verified_linked_distinct_notice_groups'),
            'source_schema_linked_candidate_notices':cc.get('verified_linked_unique_candidate_notices'),
            'common_field_mapping_instances':cc.get('common_field_mapping_instances'),
            'program_specific_mapping_instances':cc.get('program_specific_mapping_instances'),
            'projection_freshness':condition.get('freshness'),
            'content_approved_canonical_rules':cc.get('content_approved_canonical_rules')},
        'experience_quality':{'stage':'experience_quality','actual_llm_analyzed_experience_groups':experience_analyzed,
            'relevant_llm_analyzed_experience_groups':experience_relevant,
            'code_applied_source_occurrences':experience_occurrences,'count_cohorts':experience_cohorts,
            'paragraph_linked_semantic_groups':wc.get('paragraph_linked_observations'),
            'paragraph_case_linked_semantic_groups':wc.get('paragraph_case_linked_observations'),
            'source_hash_matched_segments':qc.get('segment_hash_checks',{}).get('matched'),
            'whole_document_llm_analyses':quality.get('whole_document_latent_semantics_completed')},
        'writing':{'stage':'writing_pattern_links','actual_llm_analyzed_experience_groups':experience_analyzed,
            'relevant_llm_analyzed_experience_groups':experience_relevant,'count_cohorts':experience_cohorts,
            'compact_writing_observations':registry.get('local_writing_observations'),
            'source_occurrence_links':field(wc,'source_bound_occurrence_links','verified_source_occurrence_links','source_occurrence_links'),
            'paragraph_linked_source_occurrences':wc.get('paragraph_bound_occurrence_links'),
            'paragraph_case_linked_source_occurrences':wc.get('paragraph_case_linked_occurrences'),
            'paragraph_linked_observations':field(wc,'paragraph_linked_observations','observations_with_paragraph_links'),
            'actual_full_paragraph_llm_analyses':field(wc,'actual_successful_full_paragraph_analyses','full_paragraph_llm_analyses_completed') if field(wc,'actual_successful_full_paragraph_analyses','full_paragraph_llm_analyses_completed') is not None else field(analysis,'successful_full_paragraph_analyses','actual_successful_full_paragraph_analyses','completed_full_paragraph_analyses'),
            'active_full_paragraph_llm_analyses':analysis.get('active_successful_full_paragraph_analyses'),
            'full_paragraph_criterion_assessment_links':analysis.get('criterion_assessment_links'),
            'full_paragraph_pattern_source_occurrence_links':analysis.get('source_bound_pattern_occurrence_links'),
            'materialized_full_paragraph_analyses':wc.get('source_verified_full_paragraph_analyses_materialized'),
            'materialized_pattern_source_occurrence_case_links':wc.get('actual_pattern_source_occurrence_case_links'),
            'materialized_pattern_linked_compact_observations':wc.get('actual_pattern_linked_compact_observations'),
            'materialized_semantic_criterion_assessment_links':wc.get('actual_semantic_criterion_assessment_links'),
            'held_paragraph_completion_revalidations':wc.get('held_full_paragraph_completion_revalidation'),
            'full_paragraph_completion_identity':analysis.get('completion_identity'),
            'criterion_linked_observations':field(wc,'generic_criterion_linked_observations','criterion_linked_observations','observations_with_criterion_links'),
            'generic_criterion_input_links':wc.get('generic_criterion_input_links'),
            'verified_program_round_bindings':field(writing,'verified_program_round_bindings','program_round_bound_patterns'),
            'content_approved_patterns':writing.get('content_approved_patterns',wc.get('content_approved_patterns')),
            'program_specific_approved_patterns':writing.get('program_specific_approved_patterns',wc.get('program_specific_approved_patterns'))},
        'personalization':{'stage':'personalization','user_input_contract':personal.get('user_input_contract'),'source_verified_notice_projections':pc.get('notice_projection',{}).get('with_new_source_verified_mapping'),
            'source_verified_question_route_links':cc.get('source_verified_question_route_links'),
            'source_verified_requirement_routes':condition.get('by_requirement_route'),
            'issued_user_questions':personal.get('live_questions_issued'),
            'issued_personal_eligibility_decisions':personal.get('personal_eligibility_decisions_issued')},
        'operations':{'stage':'operations','completed_team_stages':sum(value in SUCCESS_STATES for value in stage_states.values()),
            'failed_team_stages':sum(value=='failed' for value in stage_states.values()),
            'dependency_skipped_stages':sum(value=='skipped_dependency_failure' for value in stage_states.values()),
            'semantic_batches_completed_during_refresh':snapshot.get('monitor_end',{}).get('actual_completed_semantic_batches',0)-snapshot.get('monitor_start',{}).get('actual_completed_semantic_batches',0),
            'source_schema_projection_lag_groups':condition.get('freshness',{}).get('analyzed_groups_since_personalization_snapshot'),
            'active_paragraph_tasks_pending':max(0,analysis['active_tasks']-analysis['active_successful_full_paragraph_analyses']) if isinstance(analysis.get('active_tasks'),int) and isinstance(analysis.get('active_successful_full_paragraph_analyses'),int) else None}}
    for row in teams.values():row['stage_state']=stage_states.get(row['stage'],'not_recorded')
    declared_writes=[v.get('canonical_database_writes') for v in payloads.values() if 'canonical_database_writes' in v]
    return {'version':'benefitlens-team-completion-v1','at':snapshot.get('at',utc()),'state':snapshot.get('state'),
        'count_scope':'cumulative_counts_in_preserved_stage_snapshots_not_delta_or_inventory',
        'monitor':snapshot.get('monitor_end',{}),'teams':teams,
        'new_semantic_batches_during_refresh':snapshot.get('monitor_end',{}).get('actual_completed_semantic_batches',0)-snapshot.get('monitor_start',{}).get('actual_completed_semantic_batches',0),
        'canonical_postgresql_writes':sum(declared_writes) if declared_writes else None,
        'canonical_write_scope':'declared_downstream_stage_writes; private applicant facts require user profile and consent',
        'stage0':{'status':'not_measured_by_this_handoff','completed':None},
        'evidence':{row['name']:{**row['summary_ref'],'as_of':next((payloads[row['name']].get(key) for key in ('at','created_at','at_kst','as_of','generated_at') if payloads[row['name']].get(key)),None)}
            for row in snapshot.get('stages',[]) if row.get('summary_ref') and row.get('state') in SUCCESS_STATES},
        'projection_model_calls':0,'projection_source_database_writes':0}


def completion_report(completion):
    def n(value):return '미집계' if value is None else f'{value:,}'
    teams=completion['teams'];s=teams['source_schema'];q=teams['experience_quality'];w=teams['writing'];p=teams['personalization'];o=teams['operations']
    return '\n'.join([
        'BenefitLens 팀별 실제 해석과 연결 집계',str(completion['at']),'상태: '+str(completion['state']),'',
        f"원문과 스키마: 직접 LLM 해석 {n(s['actual_llm_analyzed_notice_groups'])}공고 그룹 중 관련 {n(s['relevant_llm_analyzed_notice_groups'])}그룹, 코드 적용 {n(s['code_applied_source_occurrences'])}원문 구간. 조건과 필드의 매핑 {n(s['source_schema_mapping_instances'])}행, 한 개 이상 연결된 공고 {n(s['source_schema_linked_candidate_notices'])}건.",
        f"경험 품질: 직접 LLM 해석 {n(q['actual_llm_analyzed_experience_groups'])}문맥 그룹 중 관련 {n(q['relevant_llm_analyzed_experience_groups'])}그룹, 문단 연결 {n(q['paragraph_linked_semantic_groups'])}그룹, 문단 및 사례 연결 {n(q['paragraph_case_linked_semantic_groups'])}그룹.",
        f"글쓰기: 원문 연결 {n(w['source_occurrence_links'])}건, 문단 연결 관찰 {n(w['paragraph_linked_observations'])}건, 전체 문단 LLM 분석 {n(w['actual_full_paragraph_llm_analyses'])}건. 재검증 후 연결된 분석 {n(w['materialized_full_paragraph_analyses'])}건, 실제 패턴과 원문 및 사례 연결 {n(w['materialized_pattern_source_occurrence_case_links'])}건, 실제 기준 평가 연결 {n(w['materialized_semantic_criterion_assessment_links'])}건.",
        f"프로그램과 회차: 검증된 결합 {n(w['verified_program_round_bindings'])}건, 내용 승인 패턴 {n(w['content_approved_patterns'])}건, 프로그램별 승인 패턴 {n(w['program_specific_approved_patterns'])}건.",
        f"사용자 입력: 공유 필드 종류 {n((p.get('user_input_contract') or {}).get('shared_field_types'))}종, 기존 재사용 개인 정보 {n((p.get('user_input_contract') or {}).get('reusable_user_fact_field_types'))}종. 초기 입력 {n((p.get('user_input_contract') or {}).get('initial_fields'))}항목, 질문 묶음 {n((p.get('user_input_contract') or {}).get('question_chunks'))}개. 조건 유형 {n((p.get('user_input_contract') or {}).get('configured_condition_types'))}종.",
        f"개인화: 원문 검증 질문 경로 연결 {n(p['source_verified_question_route_links'])}건, 매핑이 있는 공고 투영 {n(p['source_verified_notice_projections'])}건, 실제 발행 질문 {n(p['issued_user_questions'])}건.",
        f"통합 운영: 완료 단계 {n(o['completed_team_stages'])}개, 실패 {n(o['failed_team_stages'])}개, 의존성 보류 {n(o['dependency_skipped_stages'])}개, 단계에서 선언한 정식 PostgreSQL 기록 {n(completion['canonical_postgresql_writes'])}건.",
        f"집계 시차: 후속 처리 중 새 의미 해석 배치 {n(o['semantic_batches_completed_during_refresh'])}개, 개인화 투영 이후 새 공고 해석 {n(o['source_schema_projection_lag_groups'])}그룹, 전체 문단 분석 대기 {n(o['active_paragraph_tasks_pending'])}건.",
        '','수치는 각 단계가 보존한 시점의 누적값이다. 미집계는 완료 근거가 없는 항목이다.',
        '직접 LLM 해석의 공고 그룹과 경험 그룹은 각각 한 번만 센다. 경험 그룹은 품질 팀과 작성 팀이 같은 결과를 참조한다.',
        '정식 사용자 경험 사실 저장은 사용자 프로필과 동의를 전제로 한다. Stage 0 완료 여부는 이 인계에서 측정하지 않았다.'
    ]).replace('\u00b7',',')[:9500]+'\n'


def report(snapshot):
    end=snapshot['monitor_end'];b=end['queue_state_buckets'];lines=[
        'BenefitLens 팀 자료 갱신 보고',snapshot['at'],'',
        '현재 상태: '+snapshot['state'],
        f"대기 {b['pending']:,}그룹, 처리 중 {b['running']:,}그룹, 제공자 대기 {b['waiting']:,}그룹, 미해결 {b['unresolved']:,}그룹, 해석 기록 {b['analyzed']:,}그룹",
        f"실제 완료된 Codex CLI 의미 해석 배치: {end['actual_completed_semantic_batches']:,}",
        '경험 입력: '+snapshot['experience_root'],'',
        '단계별 결과:']
    for stage in snapshot['stages']:lines.append(stage['name']+': '+stage['state']+(' / '+stage['error'] if stage.get('error') else ''))
    lines+=['','집계 단위는 문장과 문맥 그룹 및 연결 구간이다. 원문 전처리와 코드 기반 파생 자료는 실제 LLM 해석 배치와 구분했다.',
        '파이프라인 실행 중에는 각 단계가 읽은 시점이 다르며 snapshot의 시작과 종료 상태에 처리 범위를 기록했다.',
        '독립 의미 검토와 사용자별 최종 자격 판정은 별도 단계다.']
    return '\n'.join(lines)[:9500]+'\n'


def run_cycle(base,reason,with_pdf=False):
    compact=base/'compact';output=base/'team-expansion-20261120/operations';output.mkdir(parents=True,exist_ok=True)
    lockpath=compact/'handoff/.refresh.lock';lockpath.parent.mkdir(parents=True,exist_ok=True)
    with lockpath.open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'state':'artifact_refresh_busy'}
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ');folder=output/'snapshots'/stamp;folder.mkdir(parents=True)
        current=monitor(base);experience,choice=choose_experience(base)
        previous=read(output/'team-refresh-status.json',{})or{};input_stamps=source_stamps(experience,base)
        fp=fingerprints(base,experience,output)
        results=[];quality_fingerprint=previous.get('quality_completed_fingerprint');states={}
        for name,argv,summary_path in stages(base,experience,lock.fileno()):
            failed_deps=[dep for dep in STAGE_DEPENDENCIES.get(name,()) if states.get(dep) not in SUCCESS_STATES]
            if failed_deps:
                result={'name':name,'state':'skipped_dependency_failure','command':argv,
                    'error':'Required upstream stages: '+', '.join(failed_deps),'summary_ref':None}
            elif name=='experience_quality' and quality_fingerprint==fp['quality_input_sha256'] and summary_path.exists():
                sr,origin=capture_summary(summary_path,folder,name)
                result={'name':name,'state':'reused_unchanged_inputs','command':argv,'summary_ref':sr,'original_summary_ref':origin,'error':None}
            else:
                result=run_stage(name,argv,summary_path,folder,lock.fileno() if name in ('compact_handoff','condition_schema_links','writing_pattern_links') else None)
                if name=='experience_quality' and result['state']=='completed':quality_fingerprint=fp['quality_input_sha256']
            results.append(result);states[name]=result['state']
            save(output/'team-refresh-progress.json',{'state':'refreshing','snapshot_directory':str(folder),'at':utc(),'completed_stages':results})
        end=monitor(base);state=final_state(end,results)
        payloads={r['name']:read(r['summary_ref']['path']) for r in results if r.get('summary_ref') and r['state'] in SUCCESS_STATES}
        code_counts={name:{k:v for k,v in (payload or {}).items() if k in (
            'source_documents','distinct_occurrence_source_sentences','whole_document_latent_semantics_completed',
            'new_model_calls','model_calls','external_requests','new_manual_meaning_interpretations','new_manual_semantic_groups_this_run',
            'canonical_database_writes','source_database_writes','source_link_audit','counters','execution_counts','record_counts')}
            for name,payload in payloads.items()}
        snapshot={'version':VERSION,'at':utc(),'trigger':reason,'state':state,
            'monitor_start':current,'monitor_end':end,'experience_root':str(experience),'experience_root_selection':choice,
            'fingerprints':fp,'stages':results,'derived_counts':code_counts,'coordinator_model_calls':0,
            'coordinator_source_queue_writes':0,'processing_scope':{
                'completed_batches_at_start':current['actual_completed_semantic_batches'],
                'completed_batches_at_end':end['actual_completed_semantic_batches'],
                'consistency':'stage_specific_readonly_snapshots_while_worker_may_continue',
                'coverage':'compact expression groups and code-derived team materials'}}
        completion=completion_summary(snapshot,payloads)
        completion_ref=save(folder/'team-completion-summary.json',completion)
        save(output/'team-completion-summary.json',completion)
        completion_text=completion_report(completion)
        (folder/'team-completion-report.txt').write_text(completion_text)
        (output/'team-completion-report.txt').write_text(completion_text)
        snapshot['team_completion']=completion;snapshot['team_completion_ref']=completion_ref
        snapshot_ref=save(folder/'snapshot.json',snapshot)
        text=report(snapshot);(folder/'report.txt').write_text(text);(output/'report.txt').write_text(text)
        status={'version':VERSION,'state':state,'at':utc(),'snapshot_ref':snapshot_ref,
            'completed_batches_at_refresh':current['actual_completed_semantic_batches'],
            'analyzed_groups_at_refresh':current['actual_analyzed_groups'],
            'refresh_checkpoint':checkpoint(current),
            'catchup_required':checkpoint(current)!=checkpoint(end) and phase(end)!='processing',
            'worker_stopped_during_refresh':current['worker_state'].get('active_state') in ('active','activating') and end['worker_state'].get('active_state') not in ('active','activating','unknown'),
            'source_stamps':input_stamps,'observed_source_stamps_at_end':source_stamps(experience,base),
            'quality_completed_fingerprint':quality_fingerprint,
            'monitor_end':end,'stage_states':{s['name']:s['state'] for s in results},
            'team_completion_ref':completion_ref}
        if with_pdf:
            builder=REPO/'tools/build_benefitlens_scope_report.py'
            if builder.exists():
                pdf=run_stage('pdf_report',[sys.executable,str(builder),'--snapshot',str(folder/'snapshot.json'),'--output-root',str(folder/'report')],folder/'report/summary.json',folder)
            else:pdf={'name':'pdf_report','state':'builder_not_available','error':None}
            status['pdf_stage_ref']=save(folder/'pdf-stage.json',pdf)
        save(output/'team-refresh-status.json',status)
        return status


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--watch',action='store_true');mode.add_argument('--once',action='store_true')
    parser.add_argument('--batch-interval',type=int,default=25)
    parser.add_argument('--group-interval',type=int,default=1000,help='Refresh after this many newly analyzed groups; 0 disables this additional trigger')
    parser.add_argument('--poll-seconds',type=float,default=60)
    parser.add_argument('--with-pdf',action='store_true');args=parser.parse_args(argv)
    if args.batch_interval<1 or args.poll_seconds<1 or args.group_interval<0:parser.error('Intervals must be positive; group interval may be zero')
    output=BASE/'team-expansion-20261120/operations'
    if args.once:
        print(encoded(run_cycle(BASE,'forced_snapshot',args.with_pdf)));return 0
    while True:
        experience,_=choose_experience(BASE);previous=read(output/'team-refresh-status.json',{})or{}
        observed=monitor(BASE);reason=trigger_reason(observed,previous,source_stamps(experience,BASE),args.batch_interval,group_interval=args.group_interval)
        if reason:print(encoded(run_cycle(BASE,reason,args.with_pdf)),flush=True)
        time.sleep(args.poll_seconds)


if __name__=='__main__':raise SystemExit(main())
