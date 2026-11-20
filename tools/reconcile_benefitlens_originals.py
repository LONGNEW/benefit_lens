"""Verify every linked original and exclude missing evidence through SQL views.

This does not approve source meaning, eligibility, or current recruitment.
Recovery reuses the existing policy-aware resolver and records every outcome.
"""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
import project
from benefitlens_source_storage import read_source_bytes
from import_benefitlens_full_inventory import Resolver, ROOT as IMPORT_ROOT, persist_checked
from audit_benefitlens_original_gaps import reason_family
from run_benefitlens_campaign import CACHE, KST, atomic_json, encoded


def verify_document(doc):
    key, raw_hash, text_hash, raw_path, text_path, meta = doc
    try:
        raw = read_source_bytes(raw_path, storage=meta.get('raw_storage'))
        text = read_source_bytes(text_path, storage=meta.get('text_storage'))
        if sha256(raw).hexdigest() != raw_hash or sha256(text).hexdigest() != text_hash:
            raise ValueError('content_hash_mismatch')
        decoded = text.decode('utf-8')
        if len(decoded.strip()) < 100:
            raise ValueError('usable_text_shorter_than_100_characters')
        return (key, raw_hash, text_hash, 'verified', 'raw_and_text_hashes_match', len(decoded))
    except (OSError, ValueError, TypeError) as error:
        return (key, raw_hash, text_hash, 'unavailable', type(error).__name__+': '+str(error)[:220], 0)


def verify_all(root, workers):
    with project.connect() as c:
        documents = c.execute("""SELECT d.document_id,d.content_sha256,d.text_sha256,
          d.raw_path,d.text_path,d.metadata FROM benefitlens.pipeline_document d
          WHERE d.document_id IN (
            SELECT payload->>'document_id' FROM benefitlens.support_discovery_record WHERE record_type='candidate'
            UNION SELECT jsonb_array_elements_text(coalesce(payload->'document_ids','[]'))
            FROM benefitlens.experience_discovery_case) ORDER BY d.document_id""").fetchall()
    results=[]
    with ThreadPoolExecutor(max_workers=workers) as pool, project.connect() as c:
        c.execute((project.ROOT/'pipeline/migrations/024_original_integrity_gate.sql').read_text())
        for result in pool.map(verify_document, documents):
            results.append(result)
            c.execute("""INSERT INTO benefitlens.source_integrity_check
              (document_id,raw_sha256,text_sha256,status,reason,text_characters)
              VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(document_id) DO UPDATE SET
              raw_sha256=excluded.raw_sha256,text_sha256=excluded.text_sha256,
              status=excluded.status,reason=excluded.reason,
              text_characters=excluded.text_characters,checked_at=now()""",result)
            if len(results)%500==0:
                c.commit(); print(encoded({'checked_documents':len(results),'total':len(documents)}),flush=True)
        c.commit()
    atomic_json(root/'document-checks.json', {'at':datetime.now(KST).isoformat(),
        'counts':dict(Counter(r[3] for r in results)), 'failures':[r for r in results if r[3]!='verified'],
        'checked_documents':len(results),'semantic_review':False,'current_open_review':False})


def upgrade_url(url):
    # Same observed host and path only; no host invention or credential removal.
    if url.startswith('http://'): return 'https://'+url[7:]
    return url


def recover(root, limit, workers):
    with project.connect() as c:
        rows=c.execute("""SELECT candidate_key,payload->>'inventory_group',
          payload->>'original_reason' FROM benefitlens.support_discovery_record
          WHERE record_type='candidate' AND capture_status='original_unresolved'
          ORDER BY candidate_key""").fetchall()
    db=sqlite3.connect(root/'recovery.sqlite');db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS jobs(key TEXT PRIMARY KEY,group_id TEXT,family TEXT,state TEXT,result TEXT)')
    # Explicit policy holds and denied routes stay excluded; do not bypass them.
    blocked={'inherited_source_403_hold','robots_explicit_path_disallow','direct_http_403',
             'redirect_requires_login_or_leaves_https','tls_certificate_error'}
    for key,gid,reason in rows:
        family=reason_family(reason)
        db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?,?,NULL)',
            (key,gid,family,'excluded_policy_or_access' if family in blocked else 'pending'))
    db.commit()
    targets=db.execute("SELECT * FROM jobs WHERE state='pending' ORDER BY CASE family WHEN 'parser_or_ocr_missing' THEN 0 WHEN 'url_requires_verified_https_or_public_route' THEN 1 ELSE 2 END,key LIMIT ?",(limit,)).fetchall()
    q=sqlite3.connect((IMPORT_ROOT/'intake.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    prepared=[]
    for row in targets:
        source=q.execute('SELECT payload FROM target WHERE id=?',(row['group_id'],)).fetchone()
        if not source:
            db.execute("UPDATE jobs SET state='excluded_no_inventory' WHERE key=?",(row['key'],));continue
        group=json.loads(source[0]); members=[]
        for member in group['members']:
            item=dict(member); item['url']=upgrade_url(item.get('url') or '')
            members.append(item)
        group['members']=members;prepared.append((row['key'],group))
    db.commit()
    resolver=Resolver(root,100000)
    def resolve(item):return item[0],resolver.process(item[1])
    with ThreadPoolExecutor(max_workers=workers) as pool, project.connect() as c:
        futures=[pool.submit(resolve,item) for item in prepared]
        for future in as_completed(futures):
            key,result=future.result()
            with c.transaction():
                current=c.execute('SELECT capture_status FROM benefitlens.support_discovery_record WHERE candidate_key=%s FOR UPDATE',(key,)).fetchone()
                if result.get('document') and current and current[0]=='original_unresolved':
                    result=persist_checked(c,key,result)
                    d=result.get('document')
                    if d:
                        checked=verify_document((d['document_id'],d['content_sha256'],d['text_sha256'],d['raw_path'],d['text_path'],d['metadata']))
                        c.execute('''INSERT INTO benefitlens.source_integrity_check
                          (document_id,raw_sha256,text_sha256,status,reason,text_characters)
                          VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(document_id) DO NOTHING''',checked)
            minimal={k:v for k,v in result.items() if k not in ('text','clauses','document')}
            db.execute('UPDATE jobs SET state=?,result=? WHERE key=?',
                       (result['state'],encoded(minimal),key));db.commit()
            atomic_json(root/'recovery-status.json',{'at':datetime.now(KST).isoformat(),
                'states':dict(db.execute('SELECT state,count(*) FROM jobs GROUP BY state')),
                'network_requests':resolver.ledger.network_requests,'runtime':'Python only'})
            print(encoded({'key':key,'state':result['state']}),flush=True)


def report(root):
    with project.connect() as c:
        counts={name:c.execute('SELECT count(*) FROM benefitlens.'+name).fetchone()[0]
            for name in ('notice_original_ready','experience_original_ready')}
        counts['notice_candidate_total']=c.execute("SELECT count(*) FROM benefitlens.support_discovery_record WHERE record_type='candidate'").fetchone()[0]
        counts['experience_total']=c.execute('SELECT count(*) FROM benefitlens.experience_discovery_case').fetchone()[0]
        counts['notice_excluded']=counts['notice_candidate_total']-counts['notice_original_ready']
        counts['experience_excluded']=counts['experience_total']-counts['experience_original_ready']
        failures=c.execute("""SELECT c.case_key,c.evidence_status,c.payload->>'active',
          c.payload->>'legacy_case_id',array_agg(v.reason) FILTER(WHERE v.status <> 'verified')
          FROM benefitlens.experience_discovery_case c
          LEFT JOIN LATERAL jsonb_array_elements_text(coalesce(c.payload->'document_ids','[]')) x(id) ON true
          LEFT JOIN benefitlens.source_integrity_check v ON v.document_id=x.id
          WHERE NOT EXISTS(SELECT 1 FROM benefitlens.experience_original_ready r WHERE r.case_key=c.case_key)
          GROUP BY c.case_key LIMIT 100""").fetchall()
    value={'at':datetime.now(KST).isoformat(),'counts':counts,'excluded_experience_examples':failures,
       'exclusion':'SQL ready views; retained audit history; no physical deletion',
       'readiness':'complete linked raw/text hashes, not semantic truth or current openness',
       'existing_analysis_queues_rebuilt':False}
    atomic_json(root/'summary.json',value);print(encoded(value),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=CACHE/'original-recheck-20261120')
    p.add_argument('--verify',action='store_true');p.add_argument('--recover',type=int,default=0)
    p.add_argument('--workers',type=int,choices=range(1,17),default=8)
    a=p.parse_args();a.root.resolve().relative_to(CACHE.resolve());a.root.mkdir(parents=True,exist_ok=True)
    if a.verify:verify_all(a.root,a.workers)
    if a.recover:recover(a.root,a.recover,a.workers)
    report(a.root)
if __name__=='__main__':main()
