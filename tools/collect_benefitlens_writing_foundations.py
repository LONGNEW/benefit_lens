"""Collect complete public writing resources through repository archives and Q&A APIs.

No model, credential, account submission or application-outcome inference.
Pinned archive members retain source licenses and file-level provenance.
"""
import argparse
from collections import Counter
from datetime import datetime
from fnmatch import fnmatch
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.parse import quote, urlencode, urlsplit
import zipfile
from xml.etree import ElementTree

import requests

from benefitlens_source_storage import read_source_bytes, store_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json, single_writer
from run_benefitlens_korea import soup

REPO=Path(__file__).resolve().parents[1]
ROOT=CACHE/'writing-foundations-v1'
ALLOWED={'api.github.com','codeload.github.com','gitlab.com','api.stackexchange.com'}


def plain(raw):
    page=soup(raw)
    for node in page.select('script,style,nav,footer,header,noscript,.nav'):node.decompose()
    body=page.select_one('.section, main, article, body') or page
    return '\n'.join(s.strip() for s in body.get_text('\n').splitlines() if s.strip())


def material_unit(source_id, relative):
    """Keep repository administration separate from usable writing materials."""
    if source_id == 'good-docs-current':
        name = Path(relative).name
        for prefix, unit in [('template_', 'genre_template'), ('guide_', 'template_guide'),
                             ('example_', 'worked_example'), ('process_', 'authoring_process'),
                             ('resources_', 'resource_index')]:
            if not relative.startswith('.') and name.startswith(prefix):
                return unit
        return 'guidance_file' if relative == 'STYLE-GUIDE.md' else 'repository_context'
    if '/a-z-word-list' in relative:
        return 'terminology_entry'
    if source_id == 'writing-for-success':
        return 'textbook_section'
    if source_id == 'persuade-rubrics':
        return 'scoring_rubric' if relative.endswith('.pdf') else 'repository_context'
    if source_id == 'ellipse-corpus':
        return 'scoring_rubric' if relative.lower().endswith('.docx') else 'repository_context'
    if source_id in ('iterater-revisions','jfleg-revisions'):
        return 'repository_context'
    if source_id == 'open-grants':
        return 'application_proposal' if relative.lower().endswith('.pdf') else 'discovery_record' if relative.startswith('_grants/') else 'repository_context'
    if source_id.startswith(('gsoc-', 'opensource-proposals')):
        if relative.lower().endswith(('.pdf','.docx','.txt')):
            return 'application_proposal'
        return 'repository_context'
    if source_id.startswith(('academic-author-', 'graduate-admissions-')):
        if relative.startswith('Recommendations/'):
            return 'recommendation_letter'
        if relative.lower().endswith(('.pdf','.tex','.txt')):
            return 'application_text'
        return 'guidance_file'
    return 'guidance_file'


class PublicRead:
    def __init__(self,root,limit):
        self.root=root;self.limit=limit;self.calls=0;self.last={};self.receipts=[]
        (self.root/'receipts').mkdir(parents=True,exist_ok=True)
        self.session=requests.Session();self.session.headers['User-Agent']='BenefitLens/1.0 (public writing knowledge research)'
        self.holds_path=root/'api-holds.json'
        self.holds=json.loads(self.holds_path.read_text()) if self.holds_path.exists() else {}

    def read(self,url):
        original=url; key=sha256(url.encode()).hexdigest();meta_path=self.root/'receipts'/(key+'.json')
        if meta_path.exists():
            meta=json.loads(meta_path.read_text()); raw=read_source_bytes(meta['path'],storage=meta['storage'])
            if sha256(raw).hexdigest()!=meta['sha256']:raise ValueError('Cached response hash mismatch')
            if meta['status']==200:
                self.receipts.append(meta);return raw,meta
            headers={k.lower():v for k,v in meta.get('headers',{}).items()}
            reset=headers.get('x-ratelimit-reset','')
            if meta['status']==403 and headers.get('x-ratelimit-remaining')=='0' and reset.isdigit():
                deadline=int(reset);host=urlsplit(url).hostname
                self.holds[host]={'reason':'provider_quota','resume_after':deadline}
                atomic_json(self.holds_path,self.holds)
                if deadline>time.time():raise ValueError('Provider quota exhausted until '+reset)
                history=self.root/'receipts'/'history';history.mkdir(exist_ok=True)
                meta_path.rename(history/(key+'-'+str(time.time_ns())+'.json'))
            else:raise ValueError('Held cached HTTP '+str(meta['status']))
        for _ in range(5):
            host=urlsplit(url).hostname
            if urlsplit(url).scheme!='https' or host not in ALLOWED:raise ValueError('Outside registered public API/archive scope')
            hold=self.holds.get(host,{})
            if hold.get('resume_after',0)>time.time():raise ValueError('Provider hold: '+hold['reason'])
            if self.calls>=self.limit:raise ValueError('Local bounded request budget reached')
            time.sleep(max(0,self.last.get(host,0)+2-time.monotonic()));self.last[host]=time.monotonic()
            self.calls+=1
            with self.session.get(url,stream=True,timeout=(10,60),allow_redirects=False) as response:
                if response.status_code in (301,302,303,307,308):
                    url=response.headers['Location'];continue
                chunks=[];total=0
                for chunk in response.iter_content(1024*1024):
                    total+=len(chunk)
                    if total>256*1024**2:raise ValueError('Source archive/read exceeds configured byte ceiling')
                    chunks.append(chunk)
                raw=b''.join(chunks)
                meta={'url':original,'final_url':url,'status':response.status_code,'at_kst':datetime.now(KST).isoformat(),
                      'headers':{k:v for k,v in response.headers.items() if k.lower() in
                                 {'content-type','retry-after','x-ratelimit-remaining','x-ratelimit-reset'}},
                      **store_source_bytes(raw,self.root/'blobs',suffix='.raw')}
            if response.status_code in (401,403,429):
                wait=12*3600;retry=response.headers.get('Retry-After','')
                if response.status_code==429 and retry.isdigit():wait=max(60,int(retry))
                reset=response.headers.get('X-RateLimit-Reset','')
                if response.status_code==403 and response.headers.get('X-RateLimit-Remaining')=='0' and reset.isdigit():
                    self.holds[host]={'reason':'provider_quota','resume_after':int(reset)}
                else:self.holds[host]={'reason':'HTTP '+str(response.status_code),'resume_after':time.time()+wait}
                atomic_json(self.holds_path,self.holds)
            atomic_json(meta_path,meta);self.receipts.append(meta)
            if response.status_code!=200:raise ValueError('HTTP '+str(response.status_code))
            if host=='api.stackexchange.com':
                data=json.loads(raw)
                if data.get('backoff') or data.get('quota_remaining')==0:
                    self.holds[host]={'reason':'provider_backoff_or_quota','resume_after':time.time()+int(data.get('backoff') or 86400)}
                    atomic_json(self.holds_path,self.holds)
            return raw,meta
        raise ValueError('Redirect limit')


def archive_members(raw,spec,root,revision):
    """Read registered members without extracting or executing archive contents."""
    docs=[];licenses=[]
    previous_path=root/'records.json'
    previous=json.loads(previous_path.read_text()) if previous_path.exists() else []
    pdf_texts={d['raw']['sha256']:d['text'] for d in previous
               if d.get('member','').lower().endswith('.pdf')}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if sum(i.file_size for i in z.infolist())>512*1024**2:raise ValueError('Archive decoded size limit')
        for info in z.infolist():
            if info.is_dir():continue
            relative=info.filename.split('/',1)[-1]
            if '..' in Path(relative).parts or relative.startswith('/'):raise ValueError('Unsafe archive member')
            if re.match(r'^(LICENSE|COPYING)(\.|$)',relative,re.I):
                content=z.read(info);licenses.append({'member':relative,**store_source_bytes(content,root/'blobs',suffix='.txt')})
            if not any(fnmatch(relative,p) for p in spec['include']):continue
            maximum=32*1024**2 if relative.lower().endswith('.pdf') else 8*1024**2
            if info.file_size>maximum:
                spec.get('_held_members',[]).append({'member':relative,'reason':'registered_member_byte_ceiling'})
                continue
            if spec['id']=='writing-for-success' and not re.match(r'^s\d+-\d+-.*\.html$',relative):continue
            original=z.read(info)
            original_ref=store_source_bytes(original,root/'blobs',suffix='.pdf' if relative.lower().endswith('.pdf') else '.html' if relative.endswith('.html') else '.raw' if relative.lower().endswith('.docx') else '.txt')
            if relative.lower().endswith('.pdf'):
                p=root/'pdfs'/(sha256(original).hexdigest()+'.pdf');p.parent.mkdir(exist_ok=True);p.write_bytes(original)
                prior=pdf_texts.get(original_ref['sha256'])
                if prior:
                    cached=read_source_bytes(prior['path'],storage=prior['storage'])
                    if sha256(cached).hexdigest()!=prior['sha256']:raise ValueError('Cached PDF text hash mismatch')
                    text=cached.decode('utf8')
                else:
                    try:
                        extraction=subprocess.run(['pdftotext','-layout',str(p),'-'],capture_output=True,timeout=30)
                    except FileNotFoundError:
                        spec.get('_held_members',[]).append({
                            'member':relative,'reason':'pdf_text_converter_unavailable','raw':original_ref,
                            'install_hint':'Ubuntu/Debian: sudo apt-get install -y poppler-utils; macOS: brew install poppler'})
                        continue
                    except subprocess.TimeoutExpired:
                        spec.get('_held_members',[]).append({'member':relative,'reason':'pdf_text_extraction_timeout','raw':original_ref})
                        continue
                    if extraction.returncode:
                        spec.get('_held_members',[]).append({'member':relative,'reason':'pdf_text_extraction_failed','raw':original_ref})
                        continue
                    text=extraction.stdout.decode('utf8')
            elif relative.lower().endswith('.docx'):
                with zipfile.ZipFile(io.BytesIO(original)) as word:
                    document=ElementTree.fromstring(word.read('word/document.xml'))
                    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                    text='\n'.join(''.join(p.itertext()) for p in document.findall('.//w:p',ns))
            elif relative.endswith('.html'):text=plain(original)
            else:text=original.decode('utf-8-sig')
            if len(text.strip())<80:
                spec.get('_held_members',[]).append({'member':relative,'reason':'insufficient_extractable_body','raw':original_ref})
                continue
            heading=next((line.lstrip('# ').strip() for line in text.splitlines() if line.strip() and not line.startswith('---')),relative)
            if relative.endswith('.html'):
                title_node=soup(original).select_one('h1,h2')
                if title_node:heading=title_node.get_text(' ',strip=True)
            elif relative.endswith('.md'):
                title_match=re.search(r'^#\s+(.+)$',text,re.M)
                if title_match:heading=title_match.group(1)
            unit=material_unit(spec['id'], relative)
            url=('https://gitlab.com/'+spec['project']+'/-/blob/'+revision+'/'+relative if 'project' in spec else
                 'https://github.com/'+spec['repo']+'/blob/'+revision+'/'+relative)
            docs.append({'document_id':spec['id']+':'+relative,'source_id':spec['id'],'member':relative,
                         'title':heading[:180],'url':url,
                         'revision':revision,'genre':spec['genre'],'role':'source_context' if unit=='repository_context' else spec['role'],'unit':unit,'scope':spec['scope'],
                         'raw':original_ref,'text':store_source_bytes(text.encode(),root/'blobs',suffix='.txt'),
                         'review_state':'candidate','admission_outcome':None,
                         'source_outcome_label':'rejected' if '/Rejected/' in '/'+relative else 'accepted' if '/Accepted/' in '/'+relative else None})
    return docs,licenses


def github(spec,reader,root):
    meta_raw,meta_receipt=reader.read('https://api.github.com/repos/'+spec['repo'])
    metadata=json.loads(meta_raw)
    commit_raw,_=reader.read('https://api.github.com/repos/'+spec['repo']+'/commits/'+spec['branch'])
    revision=json.loads(commit_raw)['sha']
    raw,receipt=reader.read('https://api.github.com/repos/'+spec['repo']+'/zipball/'+revision)
    local_spec={**spec,'_held_members':[]}
    docs,licenses=archive_members(raw,local_spec,root,revision)
    for d in docs:d['source_archive_sha256']=receipt['sha256']
    return docs,{'id':spec['id'],'repository':spec['repo'],'revision':revision,'archive':receipt,
                 'publisher_metadata':meta_receipt,'archived':metadata.get('archived'),
                 'declared_license':metadata.get('license'),'license_files':licenses,
                 'body_records':len(docs),'units':dict(Counter(d['unit'] for d in docs)),
                 'held_members':local_spec['_held_members']}


def source_config(path):
    """Combine a short extension registry with its explicit base registry."""
    path=Path(path).resolve()
    path.relative_to(REPO/'pipeline/config')
    current=json.loads(path.read_text())
    if not current.get('extends'):return current
    base=source_config(path.parent/current['extends'])
    for key in ('repositories','gitlab','communities'):
        combined={row['id']:row for row in base.get(key,[])}
        combined.update({row['id']:row for row in current.get(key,[])})
        current[key]=list(combined.values())
    return current


def community(spec,reader,root,pages):
    if spec['id']!='writing-stackexchange':
        root=root/'communities'/spec['id'];root.mkdir(parents=True,exist_ok=True)
    path=root/'community-checkpoint.json';state=json.loads(path.read_text()) if path.exists() else {'next_page':1,'done':False,'answer_batches':{}}
    index_path=root/'community-records.json';docs={d['document_id']:d for d in json.loads(index_path.read_text())} if index_path.exists() else {}
    signature=sha256(json.dumps([spec['site'],spec['tags'],spec['nottagged']]).encode()).hexdigest()
    if state.get('search_signature')!=signature:
        previous=state
        scope={'site':spec['site'],'nottagged':spec['nottagged']}
        compatible=previous.get('search_scope')==scope
        if spec.get('previous_tags'):
            old_signature=sha256(json.dumps([spec['site'],spec['previous_tags'],spec['nottagged']]).encode()).hexdigest()
            compatible=compatible or previous.get('search_signature')==old_signature
        state={'queries':previous.get('queries',{}) if compatible else {},'answer_batches':previous.get('answer_batches',{}),
               'search_signature':signature,'previous_search':previous}
    state['search_scope']={'site':spec['site'],'nottagged':spec['nottagged']}
    def keep(item,kind):
        native=str(item[kind+'_id']);key=spec['id']+'-'+kind+':'+native
        body=item.get('body',''); text=plain(body.encode())
        if not text:
            state.setdefault('body_holds',{})[key]={'reason':'No extractable text in retained API body',
                'question_id':str(item['question_id']),'raw':store_source_bytes(body.encode(),root/'blobs',suffix='.html')}
            return
        row={'document_id':key,'native_id':native,'source_id':spec['id'],'url':item.get('link') or 'https://'+spec['site']+'.stackexchange.com/a/'+native,
             'title':item.get('title'),'question_id':str(item['question_id']), 'genre':'general_writing',
             'role':'community_advice' if kind=='answer' else 'question_context','unit':kind,
             'author':item.get('owner'),'content_license':item.get('content_license'),'tags':item.get('tags'),
             'community_score':item.get('score'),'accepted_answer':item.get('is_accepted'),
             'raw':store_source_bytes(body.encode(),root/'blobs',suffix='.html'),
             'text':store_source_bytes(text.encode(),root/'blobs',suffix='.txt'),
             'review_state':'candidate','admission_outcome':None}
        if kind=='answer':
            parent=docs.get(spec['id']+'-question:'+str(item['question_id']))
            if parent is None:
                state.setdefault('body_holds',{})[key]={'reason':'Original question text is not available',
                                                     'question_id':str(item['question_id'])}
                return
            row['original_question']={'document_id':parent['document_id'],'url':parent['url'],'title':parent['title'],'text':parent['text']}
        docs[key]=row
    def save():atomic_json(index_path,list(docs.values()));atomic_json(path,state)
    for tag in spec['tags']:
        query_state=state['queries'].setdefault(tag,{'next_page':1,'done':False})
        for _ in range(pages):
            if query_state['done']:break
            params={'site':spec['site'],'tagged':tag,'nottagged':spec['nottagged'],'answers':1,'filter':'withbody',
                    'pagesize':100,'page':query_state['next_page'],'sort':'votes','order':'desc'}
            raw,_=reader.read(spec['api']+'?'+urlencode(params));data=json.loads(raw)
            if data.get('error_id'):raise ValueError('API error '+data['error_name'])
            for item in data.get('items',[]):keep(item,'question')
            query_state.update(next_page=query_state['next_page']+1,done=not data['has_more'])
            state['quota_remaining']=data.get('quota_remaining');save()
    questions=[d for d in docs.values() if d['unit']=='question']
    for offset in range(0,len(questions),100):
        ids=';'.join(d['native_id'] for d in questions[offset:offset+100])
        batch=state['answer_batches'].setdefault(ids,{'next_page':1,'done':False})
        while not batch['done']:
            raw,_=reader.read('https://api.stackexchange.com/2.3/questions/'+ids+'/answers?'+urlencode({
                'site':spec['site'],'filter':'withbody','pagesize':100,'page':batch['next_page'],'sort':'votes','order':'desc'}))
            data=json.loads(raw)
            if data.get('error_id'):raise ValueError('API error '+data['error_name'])
            for item in data.get('items',[]):keep(item,'answer')
            batch.update(next_page=batch['next_page']+1,done=not data['has_more'])
            state['quota_remaining']=data.get('quota_remaining');save()
    return list(docs.values()),{'id':spec['id'],'questions':len(questions),'answers':sum(d['unit']=='answer' for d in docs.values()),
            'has_more_questions':any(not q['done'] for q in state['queries'].values()),
            'query_checkpoints':state['queries'],'quota_remaining':state.get('quota_remaining'),
            'held_body_records':len(state.get('body_holds',{}))}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=ROOT/'open-guides')
    p.add_argument('--community-pages',type=int,default=3)
    p.add_argument('--answers-only',action='store_true')
    p.add_argument('--community-only',action='store_true')
    p.add_argument('--request-limit',type=int,default=64)
    p.add_argument('--source-config',type=Path,default=REPO/'pipeline/config/writing_foundation_sources_v3.json')
    a=p.parse_args()
    if not 0<=a.community_pages<=10 or not 1<=a.request_limit<=100:p.error('Invalid bounded read budget')
    a.root.resolve().relative_to(CACHE.resolve());a.root.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(a.root).free<2*1024**3:raise ValueError('Shared cache reserve')
    config=source_config(a.source_config)
    with single_writer(a.root):
        reader=PublicRead(a.root,a.request_limit);sources=[];docs=[]
        previous_path=a.root/'records.json'
        previous=json.loads(previous_path.read_text()) if previous_path.exists() else []
        status_path=a.root/'status.json'
        previous_status=json.loads(status_path.read_text()) if status_path.exists() else {}
        if a.community_only:
            ids={s['id'] for s in config['communities']}
            docs=[d for d in previous if d['source_id'] not in ids]
            sources=[s for s in previous_status.get('sources',[]) if s['id'] not in ids]
            config={**config,'repositories':[],'gitlab':[]}
        for spec in config['repositories']:
            try:
                records,summary=github(spec,reader,a.root);docs.extend(records);sources.append(summary)
                print(json.dumps({'source':spec['id'],'body_records':len(records)}),flush=True)
            except (ValueError,OSError,requests.RequestException) as e:
                kept=[d for d in previous if d['source_id']==spec['id']];docs.extend(kept)
                sources.append({'id':spec['id'],'held':str(e),'previous_records_retained':len(kept)})
        # Verify the current upstream API; a denial is retained, not evaded.
        for spec in config['gitlab']:
            try:
                base='https://gitlab.com/api/v4/projects/'+quote(spec['project'],safe='')
                raw,receipt=reader.read(base);data=json.loads(raw)
                commit,_=reader.read(base+'/repository/commits/'+data['default_branch'])
                revision=json.loads(commit)['id']
                raw,archive_receipt=reader.read(base+'/repository/archive.zip?sha='+revision)
                registered={**spec,'include':['*.md'],'scope':spec['scope']}
                records,licenses=archive_members(raw,registered,a.root,revision)
                for row in records:row['source_archive_sha256']=archive_receipt['sha256']
                docs.extend(records);sources.append({'id':spec['id'],'metadata':receipt,'revision':revision,
                    'archive':archive_receipt,'license_files':licenses,'body_records':len(records),'access':'full_archive_collected'})
            except (ValueError,OSError,requests.RequestException) as e:
                kept=[d for d in previous if d['source_id']==spec['id']];docs.extend(kept)
                sources.append({'id':spec['id'],'held':str(e),'previous_records_retained':len(kept)})
        if a.community_pages or a.answers_only:
            for spec in config['communities']:
                try:
                    records,summary=community(spec,reader,a.root,0 if a.answers_only else a.community_pages);docs.extend(records);sources.append(summary)
                except (ValueError,OSError,requests.RequestException) as e:
                    sources.append({'id':spec['id'],'held':str(e)})
                    partial=(a.root if spec['id']=='writing-stackexchange' else a.root/'communities'/spec['id'])/'community-records.json'
                    if partial.exists():docs.extend(json.loads(partial.read_text()))
        else:
            community_ids={spec['id'] for spec in config['communities']}
            docs.extend(d for d in previous if d['source_id'] in community_ids)
            sources.extend(s for s in previous_status.get('sources',[]) if s['id'] in community_ids)
        atomic_json(a.root/'records.json',docs)
        summary={'at_kst':datetime.now(KST).isoformat(),'sources':sources,'record_count':len(docs),
                 'unit_counts':dict(Counter(d['unit'] for d in docs)),'unique_texts':len({d['text']['sha256'] for d in docs}),
                 'network_requests':reader.calls,'model_called':False,'database_promoted':False,
                 'housekeeping':'Repository instructions and code are source data and are never executed.',
                 'source_records_are_not_applicants':True}
        atomic_json(a.root/'status.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
