"""Resumable public-document frontier. Never infer saturation from request caps.

Web search results are imported from recorded tool output, not fabricated SERPs.
Case-directory rows and unlinked documents do not become reviewed writing cases.
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
import sqlite3
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
import shutil
import fcntl
import tempfile
import subprocess
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qs

from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

from . import db
from .broad_collection import digest, plain, utc_now, normalize_url, postgres_safe, funding_hints
from .original_collection import role_for_document
from .http import SafeHttpClient, document_kind
from .linked import extract_document

ROOT = Path(__file__).resolve().parents[2]
RUN = '2026-10-15-saturation'
TOPIC = re.compile(r'장학|지원.*(?:공고|모집|후기)|합격|후기|수기|scholar|fellow|bursar|studentship|funding|\bgrant\b|personal statement|research statement|research proposal|application (?:material|essay)|interview (?:experience|tips)|rejection|grfp', re.I)
ASSET = re.compile(r'\.(?:pdf|hwp|hwpx)(?:$|\?)|drive\.google\.com/(?:file/d/|open\?)', re.I)
NOISE = re.compile(r'(^|\.)(?:wikipedia\.org|youtube\.com|youtu\.be|arxiv\.org)$',re.I)
TERMS = [
 'studentship','bursary','traineeship','doctoral training partnership','travel grant',
 'travel bursary','maintenance allowance','living allowance','tuition waiver',
 'research statement','personal statement','statement of purpose','research proposal',
 'broader impacts','intellectual merit','mock interview','reviewer comments',
 '합격 수기','지원 후기','연구계획서','자기소개서','생활비','연구장려금','학회 참가',
]
SHEETS = [
 ('alex-lang','1xoezGhbtcpg3BvNdag2F5dTQM-Xl2EELUgAfG1eUg0s'),
 ('grfp-help','1k2Y03XZ-L19C7YdarrndGpvzB339nlpmHpcZJK5thlc'),
]


def saturation(waves):
    """Every counter must be explicit: unknown or failed work prevents closure."""
    keys = ('new_terms','new_programs','new_cases','new_documents','pending_queries','pending_urls','failures','inherited_pending')
    return len(waves) >= 2 and all(all(w.get(k) == 0 for k in keys) and w.get('rescan_complete') is True for w in waves[-2:])


def open_search_stage(wave,batches,counters,inherited_pending):
    queries=[q for batch in batches if batch['wave']==wave for q in batch['queries']]
    return dict(wave=wave,phase='open',queries=queries,accepted_terms=[],accepted_terms_status='not_reviewed',
                status='원문 수집 중, 표현 신규성과 포화 집계 대기',executed_queries=len(queries),
                new_terms=None,new_programs=None,new_cases=None,new_documents=counters.get('collected',0),
                pending_queries=0,pending_urls=counters.get('pending',0),failures=counters.get('failed',0),
                inherited_pending=inherited_pending,rescan_complete=False,
                reason='Search batches are not closed stages while source links remain; unresolved counters stay unknown')


def case_identity(program, person, round_id, source, row):
    person = re.sub(r'\s+', ' ', person.strip()).casefold()
    round_id=re.sub(r'^(\d{4})\.0+$',r'\1',str(round_id or '').strip())
    # Anonymous and unknown-round rows are provisional; never merge distinct people.
    provisional = not person or 'anonym' in person or not round_id
    identity = [program,person,round_id] if not provisional else [program,source,str(row)]
    return 'case:' + digest(json.dumps(identity)), provisional


def frontier_url(url,parent=None):
    if url.startswith('http://'):
        url='https://'+url[7:]
    url=normalize_url(url,parent)
    if not url:
        return None
    if urlsplit(url).hostname=='drive.google.com':
        file_id=re.search(r'/file/d/([A-Za-z0-9_-]+)',url)
        file_id=file_id[1] if file_id else parse_qs(urlsplit(url).query).get('id',[None])[0]
        if file_id:
            url='https://drive.google.com/uc?export=download&id='+file_id
    return url


def document_text(body,kind):
    try:
        return extract_document(body,kind)
    except ValueError:
        if kind!='pdf':
            raise
    # Scanned PDFs use the same OCR path as poster images; keep every page.
    with tempfile.TemporaryDirectory(prefix='benefitlens-scan-') as tmp:
        folder=Path(tmp)
        (folder/'source.pdf').write_bytes(body)
        subprocess.run(['pdftoppm','-r','130','-png',str(folder/'source.pdf'),str(folder/'page')],check=True,capture_output=True,timeout=180)
        pages=sorted(folder.glob('page-*.png'))
        text='\n\n'.join(extract_document(page.read_bytes(),'image') for page in pages)
        if len(text.strip())<60:
            raise ValueError('insufficient_pdf_ocr_text')
        return text


class Frontier:
    def __init__(self, folder):
        self.folder = Path(folder)
        for name in ('raw','text'):
            (self.folder/name).mkdir(parents=True,exist_ok=True)
        self.path = self.folder/'exploration.sqlite'
        self.lock = threading.RLock()
        self.host_locks = {}
        self.host_time = {}
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS url(url TEXT PRIMARY KEY,lane TEXT,status TEXT,payload TEXT);
            CREATE TABLE IF NOT EXISTS edge(parent TEXT,child TEXT,payload TEXT,PRIMARY KEY(parent,child));
            CREATE TABLE IF NOT EXISTS document(document_id TEXT PRIMARY KEY,payload TEXT);
            CREATE TABLE IF NOT EXISTS experience(case_key TEXT PRIMARY KEY,payload TEXT);
            CREATE TABLE IF NOT EXISTS term(term TEXT,source TEXT,PRIMARY KEY(term,source));
            CREATE TABLE IF NOT EXISTS wave(wave INTEGER PRIMARY KEY,payload TEXT);
            CREATE TABLE IF NOT EXISTS batch(batch_key TEXT PRIMARY KEY,payload TEXT);
            ''')

    def connect(self):
        c=sqlite3.connect(self.path,timeout=60)
        c.execute('PRAGMA journal_mode=WAL')
        return c

    def enqueue(self,url,lane,*,parent='',label='',depth=0,program=None,wave=1):
        url=frontier_url(url,parent or None)
        if not url:
            return False
        host=urlsplit(url).hostname or ''
        excluded=bool(NOISE.search(host) or (host.endswith('reddit.com') and not TOPIC.search(label)))
        status='excluded_scope' if excluded else 'pending'
        p=dict(url=url,lane=lane,parent=parent,label=label,depth=depth,program_id=program,wave=wave)
        with self.lock,self.connect() as c:
            changed=c.execute('INSERT OR IGNORE INTO url VALUES (?,?,?,?)',(url,lane,status,json.dumps(p,ensure_ascii=False))).rowcount
            if parent:
                c.execute('INSERT OR IGNORE INTO edge VALUES (?,?,?)',(parent,url,json.dumps(p,ensure_ascii=False)))
        return bool(changed)

    def request(self,url):
        host=urlsplit(url).hostname
        with self.lock:
            lock=self.host_locks.setdefault(host,threading.Lock())
        with lock:
            time.sleep(max(0,1-(time.monotonic()-self.host_time.get(host,0))))
            try:
                client=SafeHttpClient(allowed_hosts={host},user_agent='BenefitLens research public-document collector/1.0',timeout_seconds=25,max_body_bytes=None,allow_public_redirects=True,max_download_seconds=120)
                return client.get(url)
            finally:
                self.host_time[host]=time.monotonic()

    def persist_response(self,r,kind=None):
        kind=kind or document_kind(r.body,r.content_type,r.final_url,r.headers)
        path=self.folder/'raw'/(r.sha256+'.'+kind)
        path.write_bytes(r.body)
        return path

    def collect(self,p):
        url=p['url']
        try:
            r=self.request(url)
            kind=document_kind(r.body,r.content_type,r.final_url,r.headers)
            raw=self.persist_response(r,kind)
            if re.search(r'accounts\.google\.com|/ServiceLogin|/signin',r.final_url,re.I):
                raise ValueError('authentication_required')
            if kind=='html':
                soup=BeautifulSoup(r.body,'html.parser')
                if soup.select_one('form[action*="signin"],form[action*="login"],#captcha') and len(soup.get_text(' ',strip=True))<1500:
                    raise ValueError('access_challenge')
                title=soup.title.get_text(' ',strip=True) if soup.title else p.get('label',url)
                for node in soup.select('script,style,nav,header,footer,noscript'):
                    node.decompose()
                # Follow topical content links and document attachments, not navigation.
                content=soup.select_one('main,article,[role="main"]') or soup.body or soup
                text=plain(str(content))
                if re.search(r'Just a moment|Attention Required|Enable JavaScript and cookies to continue|Verify you are human',title+'\n'+text[:1500],re.I):
                    raise ValueError('access_challenge')
                for a in content.select('a[href]'):
                    label=a.get_text(' ',strip=True)
                    child=urljoin(r.final_url,a['href'])
                    if ASSET.search(child) or TOPIC.search(label):
                        self.enqueue(child,p['lane'],parent=url,label=label,depth=p['depth']+1,program=p.get('program_id'),wave=p.get('wave',1))
            elif kind in {'pdf','hwp','hwpx','image'}:
                text=document_text(r.body,kind)
                title=p.get('label') or urlsplit(url).path.rsplit('/',1)[-1]
            else:
                raise ValueError('unsupported_document_kind:'+kind)
            if len(text.strip())<60:
                raise ValueError('insufficient_extracted_text')
            # A Google viewer can be public while the underlying document is private.
            if 'drive.google.com' in url and kind=='html':
                raise ValueError('drive_viewer_not_original_document')
            text_path=self.folder/'text'/(digest(text)+'.txt')
            text_path.write_text(text,encoding='utf-8')
            document_id=digest(url+'\n'+r.sha256)
            doc=dict(document_id=document_id,canonical_url=url,final_url=r.final_url,kind=kind,
                     content_sha256=r.sha256,text_sha256=digest(text),raw_path=str(raw.resolve()),
                     text_path=str(text_path.resolve()),text_chars=len(text),title=title,
                     fetched_at=utc_now(),lane=p['lane'],program_id=p.get('program_id'),
                     rights_status='review_required',case_link_status='not_reviewed')
            with self.lock,self.connect() as c:
                c.execute('INSERT OR REPLACE INTO document VALUES (?,?)',(document_id,json.dumps(doc,ensure_ascii=False)))
                for term in TERMS:
                    if term.casefold() in text.casefold():
                        c.execute('INSERT OR IGNORE INTO term VALUES (?,?)',(term,url))
            p.update(status='collected',document_id=document_id,final_url=r.final_url)
        except Exception as e:
            p.update(status='failed',error=type(e).__name__+': '+str(e)[:400])
        with self.lock,self.connect() as c:
            c.execute('UPDATE url SET status=?,payload=? WHERE url=?',(p['status'],json.dumps(p,ensure_ascii=False),url))
        return p['status']

    def import_searches(self):
        for f in sorted(self.folder.glob('search-*.json')):
            b=json.loads(f.read_text(encoding='utf-8'))
            key=f.stem
            response=b['response']
            lane=b.get('lane','mixed')
            with self.connect() as c:
                c.execute('INSERT OR REPLACE INTO batch VALUES (?,?)',(key,json.dumps(b,ensure_ascii=False)))
            # Only tool-returned source headers, never all quoted or incidental URLs.
            for title,url in re.findall(r'(?:^|\n)([^\n]{1,300}) \((https?://\S+)\)\n',response):
                self.enqueue(url,lane,parent='search:'+key,label=title,wave=b['wave'],program=b.get('program_id'))

    def import_sheets(self):
        for name,key in SHEETS:
            base='https://docs.google.com/spreadsheets/d/'+key
            response=self.request(base+'/export?format=csv')
            self.persist_response(response,'csv')
            rows=list(csv.reader(io.StringIO(response.body.decode('utf-8-sig'))))
            header_index=next(i for i,r in enumerate(rows) if 'Name' in r)
            header=rows[header_index]
            for n,r in enumerate(rows[header_index+1:],header_index+2):
                row=dict(zip(header,r))
                person=row.get('Name','').strip()
                if not person:
                    continue
                year=row.get('Year',row.get('Cycle Year**','')).strip()
                outcome=row.get('Success',row.get('Status','unknown')).strip()
                case_key,provisional=case_identity('nsf-grfp',person,year,base,n)
                p=dict(case_key=case_key,program_id='nsf-grfp',person_name=person,
                       application_round=year or None,outcome_claim=outcome,
                       evidence_status='directory_reported_not_independently_verified',
                       source_url=base+'/edit',source_name=name,row_number=n,
                       identity_provisional=provisional,row=row,
                       document_link_status='directory_documents_not_yet_row_linked',
                       source_sha256=response.sha256,observed_at=utc_now())
                with self.connect() as c:
                    c.execute('INSERT OR REPLACE INTO experience VALUES (?,?)',(case_key,json.dumps(p,ensure_ascii=False)))
            r=self.request(base+'/edit')
            self.persist_response(r,'html')
            text=html.unescape(r.body.decode('utf-8',errors='replace')).replace('\\u003d','=')
            ids=set(re.findall(r'https://drive\.google\.com/(?:file/d/([A-Za-z0-9_-]+)|open\?id=([A-Za-z0-9_-]+))',text))
            for a,b in ids:
                self.enqueue('https://drive.google.com/uc?export=download&id='+(a or b),'experience',parent=base+'/edit',label='Public GRFP directory attachment, row linkage pending',program='nsf-grfp')

    def link_sheet_documents(self):
        """Read public XLSX relationships; do not edit the publisher's workbook."""
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        report=[]
        for name,key in SHEETS:
            base='https://docs.google.com/spreadsheets/d/'+key
            r=self.request(base+'/export?format=xlsx')
            self.persist_response(r,'xlsx')
            with zipfile.ZipFile(io.BytesIO(r.body)) as z:
                strings=[]
                if 'xl/sharedStrings.xml' in z.namelist():
                    strings=[''.join(si.itertext()) for si in ET.fromstring(z.read('xl/sharedStrings.xml'))]
                linked=0
                for filename in z.namelist():
                    if not re.fullmatch(r'xl/worksheets/sheet\d+\.xml',filename):
                        continue
                    root=ET.fromstring(z.read(filename))
                    relfile='xl/worksheets/_rels/'+filename.rsplit('/',1)[-1]+'.rels'
                    rels={x.attrib['Id']:x.attrib['Target'] for x in ET.fromstring(z.read(relfile))} if relfile in z.namelist() else {}
                    hrefs={h.attrib['ref']:rels.get(next((v for k,v in h.attrib.items() if k.endswith('}id')),'')) for h in root.findall('.//s:hyperlink',ns)}
                    rows={}
                    for row in root.findall('.//s:sheetData/s:row',ns):
                        values={}
                        for cell in row:
                            address=cell.attrib['r']
                            val=cell.findtext('s:v','',ns)
                            if cell.attrib.get('t')=='s' and val:
                                val=strings[int(val)]
                            elif cell.attrib.get('t')=='inlineStr':
                                val=''.join(cell.find('s:is',ns).itertext())
                            values[re.sub(r'\d','',address)]=dict(value=val,url=hrefs.get(address),cell=address)
                        rows[int(row.attrib['r'])]=values
                    header_no=next((n for n,v in rows.items() if any(c['value']=='Name' for c in v.values())),None)
                    if header_no is None:
                        continue
                    header={col:c['value'] for col,c in rows[header_no].items()}
                    for n,v in rows.items():
                        data={header.get(col,col):c['value'] for col,c in v.items()}
                        if n<=header_no or not data.get('Name','').strip():
                            continue
                        person=data['Name'].strip()
                        year=data.get('Year',data.get('Cycle Year**','')).strip()
                        identity,_=case_identity('nsf-grfp',person,year,base,n)
                        with self.connect() as c:
                            old=c.execute('SELECT payload FROM experience WHERE case_key=?',(identity,)).fetchone()
                        if not old:
                            continue
                        p=json.loads(old[0])
                        docs=[dict(document_role=header.get(col,col),url=cell['url'],source_cell=cell['cell'],source_sheet=filename) for col,cell in v.items() if cell.get('url')]
                        p['document_refs']=list({(d['url'],d['document_role']):d for d in p.get('document_refs',[])+docs}.values())
                        p['document_link_status']='publisher_row_linked' if p['document_refs'] else 'no_hyperlinks_in_row'
                        p['link_evidence_sha256']=r.sha256
                        with self.connect() as c:
                            c.execute('UPDATE experience SET payload=? WHERE case_key=?',(json.dumps(p,ensure_ascii=False),identity))
                        for doc in docs:
                            self.enqueue(doc['url'],'experience',parent=base+'/edit#'+doc['source_cell'],label=person+' '+doc['document_role'],program='nsf-grfp')
                        linked+=bool(docs)
                report.append(dict(source=name,cases_with_document_links=linked))
        (self.folder/'sheet-link-report.json').write_text(json.dumps(report,indent=2))
        return report

    def import_reviewed_cases(self):
        for path in self.folder.glob('reviewed-cases*.json'):
            for case in json.loads(path.read_text(encoding='utf-8')):
                with self.connect() as c:
                    raw=c.execute("SELECT payload FROM document WHERE json_extract(payload,'$.canonical_url')=?",(frontier_url(case['source']),)).fetchone()
                if not raw:
                    continue
                doc=json.loads(raw[0])
                text=Path(doc['text_path']).read_text(encoding='utf-8')
                if case['anchor'] not in text:
                    raise ValueError('review anchor absent: '+case['person']+' '+case['source'])
                key,provisional=case_identity(case['program'],case['person'],case.get('round'),case['source'],case['person'])
                refs=[dict(url=frontier_url(u,case['source']),document_role='application_material') for u in case['documents']]
                refs.append(dict(url=case['source'],document_role='outcome_or_experience_source'))
                value=dict(case_key=key,program_id=case['program'],person_name=case['person'],application_round=case.get('round'),outcome_claim=case['outcome'],evidence_status=case['evidence'],source_url=case['source'],identity_provisional=provisional,document_refs=refs,evidence_anchor=case['anchor'],note=case.get('note'),source_sha256=doc['content_sha256'],observed_at=doc['fetched_at'],writing_pattern_status='not_aggregated')
                with self.connect() as c:
                    previous=c.execute('SELECT payload FROM experience WHERE case_key=?',(key,)).fetchone()
                    if previous:
                        previous=json.loads(previous[0])
                        value={**previous,**value}
                        value['document_refs']=list({frontier_url(r['url']):{**r,'url':frontier_url(r['url'])} for r in previous.get('document_refs',[])+refs}.values())
                        observation=dict(source_url=previous['source_url'],evidence_status=previous['evidence_status'],outcome_claim=previous['outcome_claim'])
                        value['evidence_observations']=list({json.dumps(o,sort_keys=True):o for o in previous.get('evidence_observations',[])+[observation]}.values())
                    c.execute('INSERT OR REPLACE INTO experience VALUES(?,?)',(key,json.dumps(value,ensure_ascii=False)))
                for ref in refs:
                    self.enqueue(ref['url'],'experience',parent=case['source'],label=case['person']+' '+ref['document_role'],program=case['program'])

    def drain(self,workers=6):
        completed=0
        checkpoint=time.monotonic()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            running={}
            busy_hosts=set()
            while True:
                if shutil.disk_usage(self.folder).free<10_000_000_000:
                    raise RuntimeError('storage_guard: less than 10 GB free; not saturated')
                with self.connect() as c:
                    rows=c.execute("SELECT payload FROM url WHERE status='pending' ORDER BY rowid").fetchall()
                if not rows and not running:
                    break
                active_urls={p['url'] for p in running.values()}
                for (raw,) in rows:
                    if len(running)>=workers:
                        break
                    item=json.loads(raw)
                    host=urlsplit(item['url']).hostname
                    if host not in busy_hosts and item['url'] not in active_urls:
                        busy_hosts.add(host)
                        running[pool.submit(self.collect,item)]=item
                    if len(running)>=workers:
                        break
                if not running:
                    break
                done,_=wait(running,timeout=1,return_when=FIRST_COMPLETED)
                for future in done:
                    busy_hosts.discard(urlsplit(running.pop(future)['url']).hostname)
                    future.result()
                    completed+=1
                if completed and (completed%24==0 or time.monotonic()-checkpoint>=60):
                    print(json.dumps(self.stats()),flush=True)
                if time.monotonic()-checkpoint>=60:
                    self.ingest()
                    checkpoint=time.monotonic()

    def stats(self):
        with self.connect() as c:
            return dict(urls=dict(c.execute('SELECT status,count(*) FROM url GROUP BY status')),
                        documents=c.execute('SELECT count(*) FROM document').fetchone()[0],
                        discovered_cases=c.execute('SELECT count(*) FROM experience').fetchone()[0],
                        observed_terms=c.execute('SELECT count(DISTINCT term) FROM term').fetchone()[0])

    def ingest(self):
        # Commit schema changes before the bulk transaction, so a checkpoint
        # does not hold a DDL lock against the running demo for the whole import.
        with db.connect() as schema:
            for version,filename in [('021_exploration_frontier','021_exploration_frontier.sql'),('022_long_url_identity','022_long_url_identity.sql')]:
                if not schema.execute('SELECT 1 FROM platform.schema_migration WHERE version=%s',(version,)).fetchone():
                    schema.execute((ROOT/'migrations'/filename).read_text())
        with db.connect() as pg,self.connect() as c:
            documents={json.loads(raw)['canonical_url']:json.loads(raw) for (raw,) in c.execute('SELECT payload FROM document')}
            known_urls={r[0] for r in pg.execute('SELECT source_url FROM benefitlens.support_discovery_record')}
            for table in ('url','document','experience','batch'):
                for (raw,) in c.execute('SELECT payload FROM '+table):
                    p=postgres_safe(json.loads(raw))
                    if table=='url':
                        result=pg.execute('''INSERT INTO benefitlens.exploration_url(run_id,url,lane,status,payload,updated_at)
                            VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT(run_id,url_hash)
                            DO UPDATE SET status=EXCLUDED.status,payload=EXCLUDED.payload,updated_at=now()
                            WHERE benefitlens.exploration_url.url=EXCLUDED.url''',
                            (RUN,p['url'],p['lane'],p.get('status','pending'),Jsonb(p)))
                        if result.rowcount!=1:
                            raise ValueError('URL hash identity collision; original URL was preserved')
                    elif table=='document':
                        text=Path(p['text_path']).read_text(encoding='utf-8')
                        p['document_role']=role_for_document(p['canonical_url'],p['title'],text,p['kind'])
                        pg.execute('INSERT INTO benefitlens.pipeline_document(document_id,canonical_url,content_sha256,text_sha256,kind,raw_path,text_path,fetched_at,metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                                   (p['document_id'],p['canonical_url'],p['content_sha256'],p['text_sha256'],p['kind'],p['raw_path'],p['text_path'],p['fetched_at'],Jsonb(p)))
                        if p['lane']!='experience' and p['document_role']=='notice_or_policy' and p['canonical_url'] not in known_urls:
                            key='support:'+digest(p['canonical_url'])
                            pg.execute('''INSERT INTO benefitlens.support_discovery_record(
                                candidate_key,source_url,title,title_basis,source_family,record_type,capture_status,
                                funding_hints,detail_text,payload,first_seen_at,last_seen_at)
                                VALUES(%s,%s,%s,'observed_title','exploration_originals','document_candidate','stored_document',%s,%s,%s,%s,%s)
                                ON CONFLICT DO NOTHING''',(key,p['canonical_url'],p['title'],Jsonb(funding_hints(text)),postgres_safe(text),Jsonb(p),p['fetched_at'],p['fetched_at']))
                            pg.execute("INSERT INTO benefitlens.pipeline_work_item(candidate_key,source_family,status,reason) VALUES(%s,'exploration_originals','pending','New original notice candidate awaiting eligibility normalization') ON CONFLICT DO NOTHING",(key,))
                            pg.execute('INSERT INTO benefitlens.support_discovery_observation VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(digest(RUN+'\n'+p['canonical_url']),key,'exploration_originals',p['canonical_url'],p['content_sha256'],Jsonb(p),p['fetched_at']))
                            known_urls.add(p['canonical_url'])
                    elif table=='experience':
                        refs=[]
                        for ref in p.get('document_refs',[]):
                            doc=documents.get(frontier_url(ref['url']))
                            actual=pg.execute('SELECT document_id FROM benefitlens.pipeline_document WHERE canonical_url=%s AND content_sha256=%s',(doc['canonical_url'],doc['content_sha256'])).fetchone() if doc else None
                            refs.append({**ref,'capture_status':'collected' if actual else 'not_collected',**({'document_id':actual[0]} if actual else {})})
                        p['document_refs']=refs
                        p['stored_documents']=sum(r['capture_status']=='collected' for r in refs)
                        pg.execute('INSERT INTO benefitlens.experience_discovery_case(case_key,program_id,person_name,application_round,outcome_claim,evidence_status,source_url,payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(case_key) DO UPDATE SET payload=EXCLUDED.payload,updated_at=now()',
                                   (p['case_key'],p['program_id'],p['person_name'],p['application_round'],p['outcome_claim'],p['evidence_status'],p['source_url'],Jsonb(p)))
                    else:
                        pg.execute('INSERT INTO benefitlens.exploration_search_batch VALUES (%s,%s,%s,%s,now()) ON CONFLICT(batch_key) DO UPDATE SET payload=EXCLUDED.payload',
                                   (RUN+':'+digest(raw),RUN,p['wave'],Jsonb(p)))
            batches=[json.loads(raw) for (raw,) in c.execute('SELECT payload FROM batch')]
            summary={**self.stats(),'updated_at':utc_now(),'search_batches':len(batches),'executed_queries':sum(len(b['queries']) for b in batches),
                     'saturated':False,'reason':'Unprocessed source links, failed retrievals and search expansion remain',
                     'reviewed_program_knowledge_unchanged':True,'scope':'11 registered catalogs; recorded web-tool results; public linked originals; KR, EN, DE, FR, JA search expressions'}
            summary['new_original_notice_candidates']=pg.execute("SELECT count(*) FROM benefitlens.support_discovery_record WHERE source_family='exploration_originals'").fetchone()[0]
            summary['cases_with_stored_documents']=pg.execute("SELECT count(*) FROM benefitlens.experience_discovery_case WHERE coalesce((payload->>'stored_documents')::int,0)>0").fetchone()[0]
            summary['state']='collecting' if summary['urls'].get('pending') else 'incomplete_failed_sources' if summary['urls'].get('failed') else 'awaiting_search_rescan'
            search_state=None
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='saturation_stage'").fetchone():
                from .saturation_search import SearchLoop
                search_state=SearchLoop(self.folder).status()
                summary['saturation_search']=search_state
            waves=[]
            for wave in sorted({b['wave'] for b in batches}):
                counters=dict(c.execute("SELECT status,count(*) FROM url WHERE json_extract(payload,'$.wave')=? GROUP BY status",(wave,)))
                stage=open_search_stage(wave,batches,counters,summary['urls'].get('pending',0))
                if search_state and wave==search_state['wave']:
                    stage.update({k:v for k,v in search_state.items() if k not in {'baseline_counts','queries'}})
                    stage['status']='검색과 원문 탐색 진행 중' if search_state['phase']=='open' else search_state['phase']
                    stage['accepted_terms_status']='evidence_linked'
                    with self.connect() as terms:
                        prior=terms.execute('SELECT payload FROM saturation_stage WHERE wave=?',(wave,)).fetchone()
                        baseline=set(json.loads(prior[0])['baseline']['terms']) if prior else set()
                        stage['accepted_terms']=[json.loads(payload) for key,payload in terms.execute('SELECT term_key,payload FROM saturation_term') if key not in baseline]
                waves.append(stage)
                pg.execute('INSERT INTO benefitlens.pipeline_search_wave VALUES(%s,%s,%s) ON CONFLICT(run_id,wave) DO UPDATE SET payload=EXCLUDED.payload',(RUN,wave,Jsonb(stage)))
            summary['saturated']=search_state['saturated'] if search_state else saturation(waves)
            summary['stage_metrics_status']='measured_with_pending_work' if search_state else 'open_pending_frontier_and_entity_reconciliation'
            pg.execute('INSERT INTO benefitlens.exploration_run VALUES (%s,%s,now()) ON CONFLICT(run_id) DO UPDATE SET payload=EXCLUDED.payload,updated_at=now()',(RUN,Jsonb(summary)))
        (self.folder/'frontier-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        return summary


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--sheets',action='store_true')
    p.add_argument('--sheet-links',action='store_true')
    p.add_argument('--collect',action='store_true')
    p.add_argument('--ingest',action='store_true')
    p.add_argument('--workers',type=int,default=6)
    a=p.parse_args()
    f=Frontier(a.output)
    collector_lock=None
    if a.collect:
        collector_lock=(a.output/'collector.lock').open('a')
        fcntl.flock(collector_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    f.import_searches()
    f.import_reviewed_cases()
    if a.sheets:
        f.import_sheets()
    if a.sheet_links:
        print(json.dumps(f.link_sheet_documents()),flush=True)
    if a.collect:
        try:
            f.drain(a.workers)
        finally:
            if a.ingest:
                f.ingest()
    print(json.dumps(f.ingest() if a.ingest else f.stats()),flush=True)


if __name__=='__main__':
    main()
