"""Build a source-bound catalog containing completed units only.

Browsing requires no personal profile. Experience sources are not quota-limited;
literal duplicate question/answer pairs share an identity, not a person's facts.
This derived snapshot never deletes ongoing queues or approves eligibility.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import unicodedata
import project
from benefitlens_source_storage import read_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json, single_writer
from build_benefitlens_compact_handoff import (
    _receipt_execution, _groups_in, _source_fields_equal, canonical_hash,
)

BASE=CACHE/'team-production-20261119'
DEFAULT_ROOT=CACHE/'completed-content-20261120'


def normalized(text):
    return ' '.join(unicodedata.normalize('NFC',text).split())


def identity(question, answer):
    return sha256((normalized(question)+'\n'+normalized(answer)).encode()).hexdigest()


def completed_relevant(row):
    if row['state']!='analyzed' or not row.get('result_json'):
        return False
    result=json.loads(row['result_json'])
    return result.get('relevance')=='relevant'


def source_fragment(text, ref):
    a,b=ref['char_start'],ref['char_end']
    if not isinstance(a,int) or not isinstance(b,int) or not 0<=a<b<=len(text):
        raise ValueError('source_span_invalid')
    value=text[a:b]
    if ref.get('exact_quote') is not None and value!=ref['exact_quote']:
        raise ValueError('source_quote_changed')
    return value


def verified_completion(row, cache):
    receipt, _, original, _ = _receipt_execution(row['receipt_path'], cache)
    result=json.loads(row['result_json']);gid=row['group_id']
    records=[r for r in receipt.get('group_results',[]) if r.get('group_id')==gid]
    if len(records)!=1 or records[0].get('result_sha256')!=canonical_hash(result):
        raise ValueError('completion_result_hash_mismatch')
    originals=list(_groups_in(original,gid))
    if len(originals)!=1 or not _source_fields_equal(originals[0],result.get('source_model_result',result)):
        raise ValueError('semantic_fields_changed_from_actual_completion')
    return receipt


def ro(path):
    c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=60)
    c.row_factory=sqlite3.Row;c.execute('BEGIN');return c


def build(root):
    root.resolve().relative_to(CACHE.resolve());root.mkdir(parents=True,exist_ok=True)
    with single_writer(root):
        return _build(root)


def _build(root):
    stamp=datetime.now(KST).strftime('%Y%m%dT%H%M%S')
    generation=root/'generations'/stamp;generation.mkdir(parents=True)
    with project.connect() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        notices=c.execute('''SELECT r.candidate_key,r.title,r.source_url,r.source_country,
          r.issuer_name,d.fetched_at,d.content_sha256,r.payload->>'current_open_verified'
          FROM benefitlens.notice_original_ready r JOIN benefitlens.pipeline_document d
          ON d.document_id=r.payload->>'document_id' ORDER BY r.candidate_key''').fetchall()
        cases={key:{'program_id':program,'url':url,'evidence':ev or [],'roles':roles or []}
          for key,program,url,ev,roles in c.execute('''SELECT case_key,program_id,source_url,
            payload->'evidence',payload->'evidence_roles' FROM benefitlens.experience_original_ready
            ORDER BY case_key''').fetchall()}
        documents={key:(path,h) for key,path,h in c.execute('''SELECT document_id,text_path,text_sha256
          FROM benefitlens.pipeline_document WHERE document_id IN (
          SELECT jsonb_array_elements_text(payload->'document_ids')
          FROM benefitlens.experience_original_ready)''').fetchall()}
    @lru_cache(maxsize=2048)
    def document(key):
        path,h=documents[key];raw=read_source_bytes(path)
        if sha256(raw).hexdigest()!=h:raise ValueError('source_hash_changed')
        return raw.decode('utf-8'),h
    catalog=sqlite3.connect(generation/'catalog.sqlite')
    catalog.executescript('''CREATE TABLE notices(candidate_key TEXT PRIMARY KEY,title TEXT,
      source_url TEXT,country TEXT,issuer TEXT,checked_at TEXT,source_sha256 TEXT,
      qualification_status TEXT,source_open_flag INTEGER);
      CREATE TABLE experience_material(material_id TEXT PRIMARY KEY,group_id TEXT,case_key TEXT,
      source_url TEXT,source_role TEXT,question TEXT,answer_fragment TEXT,content_key TEXT,
      source_sha256 TEXT,char_start INTEGER,char_end INTEGER,analysis_json TEXT,receipt_path TEXT);
      CREATE INDEX material_content ON experience_material(content_key);
      CREATE INDEX material_case ON experience_material(case_key);''')
    catalog.executemany('INSERT INTO notices VALUES(?,?,?,?,?,?,?,?,?)',
      ((key,title,url,country,issuer,at.isoformat(),h,'not_evaluated',int(flag=='true'))
       for key,title,url,country,issuer,at,h,flag in notices))
    higher={k:v for k,v in cases.items() if v['program_id'].startswith('higherin:')}
    companies={v['program_id'].split(':')[1] for v in higher.values()}
    role_labels={v['program_id'].split(':')[3] for v in higher.values() if len(v['program_id'].split(':'))>3}
    pairs=Counter();unique_answers=set();verified_cases=set();evidence_issues=Counter();example_by_company={}
    for key,case in higher.items():
        for ev in case['evidence']:
            try:
                text,h=document(ev['document_id'])
                if h!=ev['text_sha256']:raise ValueError('evidence_hash_changed')
                answer=source_fragment(text,ev)
                if len(normalized(answer))<20:continue
                pairs[identity(ev.get('question') or '',answer)]+=1
                unique_answers.add(normalized(answer));verified_cases.add(key)
                employer=case['program_id'].split(':')[1]
                example_by_company.setdefault(employer,{'case_key':key,'program_id':case['program_id'],
                  'url':case['url'],'question':ev.get('question'),'answer':answer,'source_sha256':h,
                  'char_start':ev['char_start'],'char_end':ev['char_end']})
            except (OSError,ValueError,KeyError,TypeError):evidence_issues['unverified_evidence']+=1
    queue=ro(BASE/'compact/queue.sqlite');counts=Counter();dedupe=set();completed_cases=set();receipts={}
    for row in queue.execute("SELECT * FROM groups WHERE kind='experience' AND state='analyzed'"):
        row=dict(row)
        if not completed_relevant(row):counts['excluded_irrelevant']+=1;continue
        rep=json.loads(row['representative_json']);result=json.loads(row['result_json'])
        key=rep.get('case_key') or rep.get('record_key');case=cases.get(key)
        if not case:counts['excluded_case_not_in_verified_view']+=1;continue
        try:
            if not row['receipt_path'] or not Path(row['receipt_path']).is_file():
                raise ValueError('completion_receipt_missing')
            verified_completion(row,receipts)
            text,h=document(rep['document_id'])
            if h!=rep['text_sha256']:raise ValueError('analysis_source_hash_changed')
            source_fragment(text,rep)
            a,b=rep['char_start'],rep['char_end'];question=''
            # Reuse the source-bound answer spans, keeping form questions out of content identity.
            candidates=[ev for ev in case['evidence'] if ev.get('document_id')==rep['document_id']
              and ev.get('text_sha256')==h and isinstance(ev.get('char_start'),int)
              and isinstance(ev.get('char_end'),int)
              and min(b,ev['char_end'])-max(a,ev['char_start'])>=20]
            if candidates:
                ev=max(candidates,key=lambda x:min(b,x['char_end'])-max(a,x['char_start']))
                source_fragment(text,ev);a,b=max(a,ev['char_start']),min(b,ev['char_end'])
                question=ev.get('question') or ''
            elif case['program_id'].startswith('higherin:'):
                counts['excluded_outside_verified_answer']+=1;continue
            quote=text[a:b]
            if len(normalized(quote))<20:raise ValueError('answer_too_short')
            content=identity(question,quote);material=identity(row['group_id'],key+'\n'+str(a)+':'+str(b))
            catalog.execute('INSERT INTO experience_material VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
              (material,row['group_id'],key,case['url'],rep.get('source_role'),question,quote,
               content,h,a,b,json.dumps(result,ensure_ascii=False),row['receipt_path']))
            dedupe.add(content);completed_cases.add(key);counts['included_completed_materials']+=1
        except (OSError,ValueError,TypeError,KeyError):counts['excluded_incomplete_or_source_mismatch']+=1
    catalog.commit();catalog.close();queue.close()
    summary={'at':datetime.now(KST).isoformat(),'generation':str(generation),'catalog':str(generation/'catalog.sqlite'),
      'notice_browse_records':len(notices),'personal_profile_required_to_browse':False,
      'notice_eligibility_decisions':0,'experience_completed_materials':counts['included_completed_materials'],
      'experience_cases_with_completed_materials':len(completed_cases),
      'experience_distinct_question_answer_fragments':len(dedupe),
      'experience_case_unit':'case with at least one completed source-bound material; not whole-case completion',
      'higherin_content_audit':{'case_records':len(higher),'company_labels':len(companies),
       'role_labels':len(role_labels),'cases_with_verified_answers':len(verified_cases),
       'verified_answer_occurrences':sum(pairs.values()),'distinct_question_answer_pairs':len(pairs),
       'distinct_answer_texts':len(unique_answers),'duplicate_pair_occurrences':sum(pairs.values())-len(pairs),
       'issues':dict(evidence_issues),'identity_scope':'normalized exact text; not semantic equivalence or identity verification'},
      'completed_filter_counts':dict(counts),'source_quotas':None,
      'completion_receipts_revalidated':len(receipts),
      'incomplete_handling':'excluded from this published snapshot; source queues retain resumable history',
      'new_model_calls':0,'network_requests':0,'source_database_writes':0}
    atomic_json(generation/'summary.json',summary)
    atomic_json(generation/'different-content-examples.json',list(example_by_company.values())[:12])
    atomic_json(root/'summary.json',summary)
    return summary


def browse(path, query='',country=None,limit=10):
    if not 1<=limit<=100:raise ValueError('limit must be 1..100')
    c=ro(path)
    rows=c.execute('''SELECT candidate_key,title,source_url,country,issuer,checked_at,
      qualification_status FROM notices WHERE (?='' OR instr(lower(title),lower(?))>0
      OR instr(lower(coalesce(issuer,'')),lower(?))>0) AND (? IS NULL OR country=?)
      ORDER BY title,candidate_key LIMIT ?''',(query,query,query,country,country,limit)).fetchall()
    c.close();return [dict(r) for r in rows]


def find_materials(path,query='',source_role=None,limit=10):
    if not 1<=limit<=100:raise ValueError('limit must be 1..100')
    c=ro(path)
    rows=c.execute('''SELECT material_id,case_key,source_url,source_role,question,
      answer_fragment,content_key,source_sha256,char_start,char_end,analysis_json
      FROM experience_material WHERE (?='' OR instr(lower(answer_fragment),lower(?))>0
      OR instr(lower(analysis_json),lower(?))>0)
      AND (? IS NULL OR source_role=?)
      ORDER BY content_key,material_id''',(query,query,query,source_role,source_role))
    selected=[];seen=set()
    for row in rows:
        item=dict(row)
        if item['content_key'] in seen:continue
        seen.add(item['content_key']);item['analysis_json']=json.loads(item['analysis_json'])
        selected.append(item)
        if len(selected)==limit:break
    c.close();return selected


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    p.add_argument('--build',action='store_true');p.add_argument('--query',default='')
    p.add_argument('--country');p.add_argument('--limit',type=int,default=10)
    p.add_argument('--materials',action='store_true');p.add_argument('--source-role')
    a=p.parse_args()
    summary=build(a.root) if a.build else json.loads((a.root/'summary.json').read_text())
    result=(find_materials(summary['catalog'],a.query,a.source_role,a.limit) if a.materials
            else browse(summary['catalog'],a.query,a.country,a.limit))
    print(json.dumps({'summary':summary,'material_results' if a.materials else 'browse_results':result},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
