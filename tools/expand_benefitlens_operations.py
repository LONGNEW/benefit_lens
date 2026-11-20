"""Build full-source refresh jobs and version-aware downstream dependencies.

This plans jobs from retained records. It neither fetches pages nor asserts a
notice is open merely because its source is scheduled for refresh.
"""
from collections import Counter
from datetime import datetime,timedelta,timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import run_benefitlens_compact as runner

REPO=Path(__file__).resolve().parents[1]
OUT=runner.BASE/'team-expansion-20261120/operations'


def parse_time(value):
    if not isinstance(value,str):return None
    try:
        date=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:return None
    return date.astimezone(timezone.utc) if date.tzinfo else None


def schedule(record,now,policy):
    original=record.get('original_ref')or{}
    captured=parse_time(original.get('fetched_at'))
    verified_dates=[]
    for condition in record.get('conditions',[]):
        if condition.get('field_id')=='application.deadline' and condition.get('status') in ('reviewed','approved'):
            date=parse_time(condition.get('value'))
            if date:verified_dates.append(date)
    near=any(now<=date<=now+timedelta(hours=policy['near_deadline_hours']) for date in verified_dates)
    hours=policy['near_deadline_check_hours'] if near else policy['default_hours']
    due=captured+timedelta(hours=hours) if captured else now
    return {'last_capture_at':captured.isoformat() if captured else None,
        'next_check_at':due.isoformat(),'refresh_due':due<=now,'interval_hours':hours,
        'reason':'verified_deadline_near' if near else 'initial_default_policy' if captured else 'missing_original_or_capture_time',
        'intake_mode':'unknown','current_open_status':'requires_explicit_reverification',
        'original_document_id':original.get('document_id'),
        'previous_content_sha256':original.get('content_sha256'),
        'previous_text_sha256':original.get('text_sha256')}


def run(now=None,output=OUT):
    now=now or datetime.now(timezone.utc);output.mkdir(parents=True,exist_ok=True)
    policy=json.loads((REPO/'pipeline/config/notice_refresh_policy_v2.json').read_text())
    counts=Counter();hosts=Counter();jobs={}
    for record in runner.jsonl(runner.BASE/'notice/notices.jsonl.gz'):
        counts['notice_records']+=1
        url=record.get('source_url')
        if not url:counts['missing_source_url']+=1;continue
        planned=schedule(record,now,policy)
        counts['records_refresh_due']+=int(planned['refresh_due'])
        counts['records_without_original']+=int(not record.get('original_ref'))
        key=sha256(url.encode()).hexdigest()
        job=jobs.get(key)
        if job is None:
            job={'job_id':'refresh-'+key[:28],'source_url':url,'source_host':urlparse(url).hostname,
                'candidate_keys':[],'source_versions':[],'state':'planned_not_fetched',
                'network_requests_executed':0,**planned}
            jobs[key]=job
        job['candidate_keys'].append(record['candidate_key'])
        job['source_versions'].append({k:planned[k] for k in ('original_document_id','previous_content_sha256','previous_text_sha256')})
        if planned['next_check_at']<job['next_check_at']:job.update(next_check_at=planned['next_check_at'],refresh_due=planned['refresh_due'])
    path=output/'source-refresh-jobs.jsonl.gz'
    with gzip.open(path,'wt',encoding='utf-8') as f:
        for job in sorted(jobs.values(),key=lambda r:(r['next_check_at'],r['job_id'])):
            hosts[job['source_host']]+=1;f.write(json.dumps(job,ensure_ascii=False)+'\n')
    manifest={'version':'team-expansion-operations-v1','as_of':now.astimezone(ZoneInfo('Asia/Seoul')).isoformat(),
        'counts':dict(counts),'unique_source_refresh_jobs':len(jobs),'source_host_counts':dict(hosts.most_common()),
        'network_requests_executed':0,'deployment_status':'refresh_jobs_planned_not_attached_to_fetch_services',
        'refresh_policy_ref':'pipeline/config/notice_refresh_policy_v2.json',
        'reuse_and_invalidation':[
            {'event':'source_bytes_unchanged','action':['reuse_preserved_text_and_semantic_groups']},
            {'event':'bytes_changed_text_unchanged','action':['update_capture_lineage','reuse_semantics']},
            {'event':'meaningful_source_text_changed','action':['extract_changed_sentences','reuse_unchanged_context_groups','requeue_changed_groups','invalidate_affected_personalized_decisions']},
            {'event':'official_writing_task_changed','action':['invalidate_task_specific_paragraph_plans','recheck_official_task_and_genre','preserve_historical_experience']},
            {'event':'source_fetch_failed','action':['retain_previous_version','mark_freshness_unknown','respect_provider_retry_after','keep_recruitment_status_separate']},
            {'event':'condition_catalog_changed','action':['revalidate_field_bindings','rebuild_personalization_requirements']},
            {'event':'writing_registry_changed','action':['recheck_genre_scoped_templates','preserve_user_fact_identity']}
        ],'semantic_completion_ref':str(runner.BASE/'compact/handoff/summary.json'),
        'job_file':str(path),'job_state':'planned_not_executed'}
    runner.save(output/'summary.json',manifest)
    return manifest


if __name__=='__main__':print(json.dumps(run(),ensure_ascii=False))
