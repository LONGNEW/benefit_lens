"""Import every pinned inventory group and resolve its original with Python only.

Candidate registration never approves eligibility or promotes an opportunity.
An independent durable queue permits restarts without discarding prior results.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from hashlib import sha256
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
from urllib.parse import urljoin, urlsplit

import requests

import project
from benefitlens_source_storage import read_source_bytes, store_source_bytes
from collect_benefitlens_crossborder import project_original, projection
from run_benefitlens_campaign import CACHE, KST, atomic_json, encoded, single_writer
from run_benefitlens_korea import Evidence, Held, compact, soup

ROOT = CACHE/'full-import-20261115-v1'
MANIFEST = CACHE/'full-history-inventory-v1/status.json'
MAIN = CACHE/'python-campaign-v1/frontier.sqlite'


def read_ref(ref):
    raw = read_source_bytes(ref['path'], storage=ref.get('storage'))
    if sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('Original or inventory hash mismatch: '+ref['path'])
    return raw


def capture_ref(cap):
    if not cap or cap.get('status', cap.get('http_status', 200)) != 200:
        return None
    if cap.get('raw_path'):
        return {'path': cap['raw_path'], 'sha256': cap['raw_sha256'],
                'storage': cap.get('raw_storage')}
    if cap.get('path') and cap.get('sha256'):
        return {k:cap[k] for k in ('path','sha256','storage') if k in cap}
    return None


def public_url(url):
    p = urlsplit(url)
    host=(p.hostname or '').lower()
    if host in {'localhost','localhost.localdomain'} or host.endswith(('.localhost','.local','.internal')):
        return False
    try:
        if not ipaddress.ip_address(host).is_global:return False
    except ValueError:
        pass
    return (p.scheme == 'https' and p.hostname and not p.username and not p.password
            and p.port in (None, 443) and not re.search(
                r'/(?:login|signin|oauth|authorize|authentication)(?:/|$)', p.path, re.I))


def project_body(raw, root, native=None):
    if raw.lstrip().startswith((b'{', b'[')):
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get('jobs'), list) and native:
            selected = [r for r in data['jobs'] if str(r.get('id')) == str(native)]
            if not selected:
                raise Held('Individual job ID absent from the stored board response')
            data = selected[0]
        lines = []
        def walk(node, field=''):
            if isinstance(node, dict):
                for k,v in node.items(): walk(v, str(k))
            elif isinstance(node, list):
                for v in node: walk(v, field)
            elif node is not None:
                value = str(node)
                if '<' in value and '>' in value:
                    _, value = projection(value.encode())
                lines.append(field+': '+value)
        walk(data)
        return None, '\n'.join(lines)
    return project_original(raw, root)


def extract_clauses(text):
    """Keep literal clauses; dates and keywords are not eligibility decisions."""
    patterns = {
        'eligibility': r'eligib|qualificat|enroll|pursuing|applicants? must|지원\s*자격|신청\s*대상|재학|졸업',
        'benefits': r'stipend|salary|compensation|scholarship|bursary|housing|급여|활동비|상금|장학금|지원금',
        'application': r'apply|application|deadline|closing date|접수|신청\s*기간|제출|마감',
    }
    return {name:[{'line':i,'quote':line} for i,line in enumerate(text.splitlines(),1)
                  if re.search(pattern,line,re.I)][:40] for name,pattern in patterns.items()}


class SharedEvidence(Evidence):
    """Respect existing source holds and coordinate host pacing with the campaign."""
    def __init__(self, root, day, network_limit):
        super().__init__(root, day)
        self.network_limit = network_limit
        self.network_lock = threading.Lock()
        self.network_requests = 0
        with self.db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS network_call(id INTEGER PRIMARY KEY,at REAL,url TEXT)')
            self.network_requests = c.execute('SELECT count(*) FROM network_call').fetchone()[0]

    def fetch(self, url, **kwargs):
        # Evidence.__init__ does not call fetch. Same-day own-cache reuse is safe.
        method = 'POST' if kwargs.get('data') is not None or kwargs.get('payload') is not None else 'GET'
        key = sha256(encoded([method,url,kwargs.get('data'),kwargs.get('payload')]).encode()).hexdigest()
        with self.db() as c:
            cached = c.execute('SELECT 1 FROM captures WHERE day=? AND key=?',(self.day,key)).fetchone()
        if not cached:
            host = urlsplit(url).hostname
            with sqlite3.connect(MAIN,timeout=30) as c:
                c.execute('BEGIN IMMEDIATE')
                row = c.execute('SELECT next_at,blocked_until,reason FROM hosts WHERE host=?',(host,)).fetchone()
                now = time.time()
                if row and row[1] > now:
                    raise Held('Existing campaign source hold: '+str(row[2]))
                reserved = max(now,row[0] if row else now)
                c.execute('INSERT INTO hosts(host,next_at) VALUES(?,?) ON CONFLICT(host) DO UPDATE SET next_at=excluded.next_at',
                          (host,reserved+max(2,getattr(self,'host_intervals',{}).get(host,2))))
            if reserved > now: time.sleep(reserved-now)
            with self.network_lock:
                if self.network_requests >= self.network_limit:
                    raise Held('Local task network budget reached')
                with self.db() as c:
                    c.execute('INSERT INTO network_call(at,url) VALUES(?,?)',(time.time(),url))
                self.network_requests += 1
        return super().fetch(url,**kwargs)


class Resolver:
    def __init__(self, root, network_limit):
        self.root = root
        self.ledger = SharedEvidence(root,datetime.now(KST).date().isoformat(),network_limit)
        self.local = threading.local()

    def connection(self,path):
        if not hasattr(self.local,'dbs'): self.local.dbs={}
        if path not in self.local.dbs:
            self.local.dbs[path]=sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True,timeout=30)
            self.local.dbs[path].row_factory=sqlite3.Row
        return self.local.dbs[path]

    def originals(self,group):
        seen=set()
        for member in reversed(group['members']):
            cap=member.get('source_snapshot')
            source=member.get('input',{})
            if source.get('job_id'):
                c=self.connection(source['path'])
                job=c.execute('SELECT day,key,capture FROM jobs WHERE id=?',(source['job_id'],)).fetchone()
                if job:
                    cap=json.loads(job['capture']) if job['capture'] else None
                    if not capture_ref(cap):
                        row=c.execute('SELECT capture FROM captures WHERE day=? AND key=? ORDER BY id DESC LIMIT 1',
                                      (job['day'],job['key'])).fetchone()
                        cap=json.loads(row[0]) if row else None
            ref=capture_ref(cap)
            if ref and (token:=(ref['path'],ref['sha256'])) not in seen:
                seen.add(token)
                yield member,cap,ref

    def result(self,group,member,cap,ref,origin):
        raw=read_ref(ref)
        # An HTTP success containing an access challenge is not a usable original.
        if re.search(rb'verify you are human|captcha required|access denied',raw[:25000],re.I):
            raise Held('Stored response is an access challenge')
        doc,text=project_body(raw,self.root,member.get('native'))
        if len(text.strip()) < 100:
            raise Held('Original needs another parser or OCR; usable text missing')
        textref=store_source_bytes(text.encode(),self.root/'blobs',suffix='.txt')
        url=member.get('url') or cap.get('url')
        docid='bulk-doc-'+sha256((url+'\n'+ref['sha256']).encode()).hexdigest()[:32]
        when=cap.get('fetched_at') or cap.get('at')
        if not when: raise Held('Capture timestamp absent')
        document={'document_id':docid,'canonical_url':url,'content_sha256':ref['sha256'],
            'text_sha256':textref['sha256'],'raw_path':ref['path'],'text_path':textref['path'],
            'kind':'json' if raw.lstrip().startswith((b'{',b'[')) else 'pdf' if raw.startswith(b'%PDF') else 'html_or_document',
            'fetched_at':when,'metadata':{'raw_storage':ref.get('storage'),'text_storage':textref.get('storage'),
                'original_capture':cap,'origin':origin,'projection':'full-inventory-original-v1',
                'capture_timestamp_preserved':True,'current_open_verified':False}}
        return {'state':'original_available','reason':'Full original and text hashes verified; semantic review pending',
            'origin':origin,'document':document,'text':text,'clauses':extract_clauses(text),
            'semantic_status':'unreviewed','current_open_verified':False}

    def process(self,group,reuse_only=False):
        errors=[]
        try:
            for member,cap,ref in self.originals(group):
                try:return self.result(group,member,cap,ref,'reused_original')
                except (ValueError,OSError,subprocess.SubprocessError) as error:errors.append(str(error)[:180])
            if reuse_only:
                return {'state':'original_missing','reason':' | '.join(dict.fromkeys(errors))[:1200] or 'No reusable original',
                        'semantic_status':'unreviewed','current_open_verified':False}
            # Distinct observed variants, not constructed alternative endpoints.
            members=list(reversed(group['members']));seen=set()
            for member in members:
                url=member.get('url')
                if not url or url in seen:continue
                seen.add(url)
                if not public_url(url):
                    errors.append('Observed URL requires HTTPS or public-route resolution');continue
                try:
                    current=url;chain=[]
                    for _ in range(6):
                        if not public_url(current):raise Held('Redirect enters authentication or leaves HTTPS')
                        raw,cap=self.ledger.request(current,redirect_metadata=True);chain.append(cap)
                        if cap['status'] in (301,302,303,307,308):
                            current=urljoin(current,cap['headers'].get('location',''));continue
                        value=self.result(group,member,cap,capture_ref(cap),'new_capture')
                        value['document']['metadata']['redirect_chain']=chain
                        return value
                    raise Held('Public redirect limit reached')
                except (ValueError,OSError,requests.RequestException,subprocess.SubprocessError) as error:
                    errors.append(str(error)[:180])
                    # One representative per notice is sufficient; another actual
                    # observed variant is tried only when it has a different URL.
                    if len(seen)>=3:break
        except Exception as error:
            errors.append(type(error).__name__+': '+str(error)[:180])
        return {'state':'original_unresolved','reason':' | '.join(dict.fromkeys(errors))[:1200] or 'No original locator',
                'semantic_status':'unreviewed','current_open_verified':False}


def connect_queue(root):
    c=sqlite3.connect(root/'intake.sqlite',timeout=30)
    c.row_factory=sqlite3.Row
    c.executescript('''PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS target(id TEXT PRIMARY KEY,position INTEGER NOT NULL,
        payload TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',result TEXT,updated REAL NOT NULL);
      CREATE INDEX IF NOT EXISTS target_state ON target(state,position);
      CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);''')
    return c


def initialize(root,queue):
    if not (root/'inventory.json').exists():
        inventory=json.loads(MANIFEST.read_text())
        atomic_json(root/'inventory.json',inventory)
    inventory=json.loads((root/'inventory.json').read_text())
    groups=json.loads(read_ref(inventory['records']))
    if len(groups)!=inventory['deduplicated_groups']:raise ValueError('Pinned target count mismatch')
    queue.executemany('INSERT OR IGNORE INTO target(id,position,payload,updated) VALUES(?,?,?,?)',
                      ((g['group_id'],i,encoded(g),time.time()) for i,g in enumerate(groups)))
    queue.execute("UPDATE target SET state='pending' WHERE state='processing'")
    queue.commit()
    return inventory


def import_candidates(queue,inventory):
    if queue.execute("SELECT 1 FROM meta WHERE key='postgres_import_complete'").fetchone():return
    from psycopg.types.json import Jsonb
    with project.connect() as conn:
        cursor=conn.cursor()
        chunk=[]
        for target in queue.execute('SELECT id,position,payload FROM target ORDER BY position'):
            g=json.loads(target['payload']);m=g['members'][-1];key='bulk-'+g['group_id']
            payload={'inventory_group':g['group_id'],'inventory_position':target['position'],
                'inventory_ref':inventory['records'],'variant_count':len(g['members']),
                'countries':g['countries'],'round_variants':g['round_variants'],
                'original_status':'pending','semantic_review':'unreviewed','current_open_verified':False}
            chunk.append((key,m.get('url') or '',g.get('title') or m.get('url') or 'Title unresolved',
                'observed_inventory_metadata','full_history_inventory_v1',g['group_id'],'candidate',
                'not_fetched',g.get('issuer'),m.get('country'),Jsonb(payload)))
            if len(chunk)>=500:
                insert_candidates(cursor,chunk);conn.commit();chunk=[]
        if chunk:insert_candidates(cursor,chunk);conn.commit()
        actual=conn.execute("SELECT count(*) FROM benefitlens.support_discovery_record WHERE source_family='full_history_inventory_v1'").fetchone()[0]
        if actual!=inventory['deduplicated_groups']:raise ValueError('Candidate import count mismatch')
    queue.execute("INSERT INTO meta VALUES('postgres_import_complete',?)",(str(actual),));queue.commit()


def insert_candidates(cursor,rows):
    cursor.executemany('''INSERT INTO benefitlens.support_discovery_record
      (candidate_key,source_url,title,title_basis,source_family,source_record_id,record_type,
       capture_status,issuer_name,source_country,payload,first_seen_at,last_seen_at)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now())
      ON CONFLICT(candidate_key) DO NOTHING''',rows)
    cursor.executemany('''INSERT INTO benefitlens.pipeline_work_item(candidate_key,source_family,status,reason)
      VALUES(%s,'full_history_inventory_v1','pending_original','Full inventory intake')
      ON CONFLICT(candidate_key) DO NOTHING''',[(r[0],) for r in rows])


def persist(conn,key,result):
    from psycopg.types.json import Jsonb
    doc=result.get('document')
    if doc:
        conn.execute('''INSERT INTO benefitlens.pipeline_document
          (document_id,canonical_url,content_sha256,text_sha256,kind,
           raw_path,text_path,fetched_at,metadata) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT(document_id) DO UPDATE SET text_sha256=excluded.text_sha256,
            text_path=excluded.text_path,metadata=excluded.metadata''',
          (doc['document_id'],doc['canonical_url'],
           doc['content_sha256'],doc['text_sha256'],doc['kind'],doc['raw_path'],doc['text_path'],
           doc['fetched_at'],Jsonb(doc['metadata'])))
    update={'original_status':result['state'],'original_reason':result['reason'],
        'semantic_review':'unreviewed','current_open_verified':False}
    if doc:update.update(document_id=doc['document_id'],source_document=doc,extracted_clauses=result['clauses'])
    conn.execute('''UPDATE benefitlens.support_discovery_record SET capture_status=%s,
      detail_text=coalesce(%s,detail_text),payload=payload || %s,last_seen_at=now()
      WHERE candidate_key=%s''',(result['state'],result.get('text'),Jsonb(update),key))
    conn.execute('''UPDATE benefitlens.pipeline_work_item SET status=%s,reason=%s,updated_at=now()
      WHERE candidate_key=%s''',('needs_semantic_review' if doc else result['state'],result['reason'],key))


def persist_checked(conn,key,result):
    from psycopg import DataError
    from psycopg.types.json import Jsonb
    try:
        with conn.transaction():
            persist(conn,key,result)
        return result
    except (DataError,ValueError,TypeError) as error:
        # One malformed source value must not trap the complete queue on restart.
        failed={'state':'storage_failed','reason':(type(error).__name__+': '+str(error)).replace('\x00',' ')[:1000],
                'current_open_verified':False,'semantic_status':'unreviewed'}
        with conn.transaction():
            conn.execute('''UPDATE benefitlens.support_discovery_record SET capture_status='storage_failed',
              payload=payload || %s WHERE candidate_key=%s''',(Jsonb({'original_status':'storage_failed','storage_error':failed['reason']}),key))
            conn.execute("UPDATE benefitlens.pipeline_work_item SET status='storage_failed',reason=%s,updated_at=now() WHERE candidate_key=%s",(failed['reason'],key))
        return failed


def write_status(root,queue,inventory,state,ledger=None):
    counts=dict(queue.execute('SELECT state,count(*) FROM target GROUP BY state').fetchall())
    value={'at_kst':datetime.now(KST).isoformat(),'state':state,'target_groups':inventory['deduplicated_groups'],
        'registered_in_postgres':int(queue.execute("SELECT value FROM meta WHERE key='postgres_import_complete'").fetchone()[0])
           if queue.execute("SELECT 1 FROM meta WHERE key='postgres_import_complete'").fetchone() else 0,
        'targets_by_state':counts,'network_requests':ledger.network_requests if ledger else 0,
        'current_open_approved_by_this_run':0,'inventory':inventory['records'],'runtime':'Python only'}
    atomic_json(root/'status.json',value)
    return value


def run(root,workers,network_limit):
    root.resolve().relative_to(CACHE.resolve());root.mkdir(parents=True,exist_ok=True)
    soup(b'')  # Validate the actual service runtime's required HTML parser.
    with single_writer(root):
        queue=connect_queue(root);inventory=initialize(root,queue)
        write_status(root,queue,inventory,'importing_candidates')
        import_candidates(queue,inventory)
        write_status(root,queue,inventory,'resolving_originals')
        resolver=Resolver(root,network_limit)
        with ThreadPoolExecutor(max_workers=workers) as pool, project.connect() as conn:
            # Exhaust local originals first so slow network hosts cannot prevent
            # cached evidence from reaching the database.
            for phase,source_state in [('reuse_originals','pending'),('fetch_missing_originals','original_missing')]:
                while targets:=queue.execute("SELECT id,payload FROM target WHERE state=? ORDER BY position LIMIT 128",(source_state,)).fetchall():
                    queue.executemany("UPDATE target SET state='processing',updated=? WHERE id=?",[(time.time(),r['id']) for r in targets]);queue.commit()
                    jobs={pool.submit(resolver.process,json.loads(r['payload']),phase=='reuse_originals'):r['id'] for r in targets}
                    completed=0
                    for future in as_completed(jobs):
                        groupid=jobs[future];result=persist_checked(conn,'bulk-'+groupid,future.result())
                        minimal={k:v for k,v in result.items() if k not in ('text','clauses')}
                        queue.execute('UPDATE target SET state=?,result=?,updated=? WHERE id=?',
                                      (result['state'],encoded(minimal),time.time(),groupid))
                        completed+=1
                        if completed%8==0:
                            conn.commit();queue.commit();write_status(root,queue,inventory,phase,resolver.ledger)
                    conn.commit();queue.commit()
                    write_status(root,queue,inventory,phase,resolver.ledger)
        state='all_targets_processed_with_unresolved_originals' if queue.execute(
            "SELECT 1 FROM target WHERE state<>'original_available' LIMIT 1").fetchone() else 'all_originals_available'
        print(encoded(write_status(root,queue,inventory,state,resolver.ledger)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=ROOT)
    p.add_argument('--workers',type=int,default=8,choices=range(1,17))
    p.add_argument('--network-limit',type=int,default=100000)
    args=p.parse_args();run(args.root,args.workers,args.network_limit)
