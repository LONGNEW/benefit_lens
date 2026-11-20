"""Bounded, unauthenticated probes of documented writing-knowledge read APIs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from hashlib import sha256
import json
import re
import shutil
import time
from urllib.parse import urlencode, urlsplit

import requests

from benefitlens_source_storage import store_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json, single_writer
from run_benefitlens_korea import soup

ROOT=CACHE/'writing-knowhow-api-audit-v1'
UA='BenefitLens/1.0 (documented public API research)'
DOCS={
 'forem':'https://developers.forem.com/api/v1',
 'stackexchange':'https://api.stackexchange.com/docs/advanced-search',
 'hashnode':'https://github.com/Hashnode/gql-skill/blob/main/skills/gql-api/references/queries.md'}
ROUTES={
 'forem':r'https://dev\.to/api/articles/(?:search(?:\?.*)?|[0-9]+)',
 'stackexchange':r'https://api\.stackexchange\.com/2\.3/(?:search/advanced|questions/[0-9;]+/answers)\?.*',
 'hashnode':r'https://gql-beta\.hashnode\.com/'}


class Probe:
    def __init__(self,source):
        self.source=source;self.folder=ROOT/source;self.folder.mkdir(exist_ok=True)
        self.receipts=[];self.last=0;self.paused=False

    def read(self,url,query=None):
        if not re.fullmatch(ROUTES[self.source],url):
            raise ValueError('URL outside registered documented read API')
        if self.paused: raise ValueError('Provider paused after denial, backoff or exhausted quota')
        if query and (self.source!='hashnode' or not query.lstrip().startswith('query ') or 'mutation' in query):
            raise ValueError('Only documented public GraphQL query operations are registered')
        if len(self.receipts)>=30: raise ValueError('Bounded probe request budget reached')
        key=sha256(json.dumps([url,query],sort_keys=True).encode()).hexdigest()
        cached=self.folder/(key+'.json')
        if cached.exists():
            from benefitlens_source_storage import read_source_bytes
            meta=json.loads(cached.read_text());raw=read_source_bytes(meta['path'],storage=meta['storage'])
            if sha256(raw).hexdigest()!=meta['sha256']:raise ValueError('Cached API hash differs')
        else:
            time.sleep(max(0,self.last+2-time.monotonic()));self.last=time.monotonic()
            with requests.request('POST' if query else 'GET',url,
                    json={'query':query} if query else None,
                    headers={'User-Agent':UA,'Accept':'application/vnd.forem.api-v1+json' if self.source=='forem' else 'application/json'},
                    timeout=(10,25),allow_redirects=False,stream=True) as response:
                parts=[];size=0
                for part in response.iter_content(65536):
                    size+=len(part)
                    if size>8*1024**2:raise ValueError('Probe response byte limit')
                    parts.append(part)
                raw=b''.join(parts)
                meta={'url':url,'method':'POST' if query else 'GET','query':query,
                    'status':response.status_code,'at_kst':datetime.now(KST).isoformat(),
                    'documentation':DOCS[self.source],'access_scope':'documented_public_read_no_credentials',
                    'headers':{k:v for k,v in response.headers.items() if k.lower() in {'content-type','retry-after'}},
                    **store_source_bytes(raw,self.folder/'blobs',suffix='.json')}
            atomic_json(cached,meta)
        self.receipts.append(meta)
        if meta['status'] in {401,403,429}:self.paused=True
        if meta['status']!=200:raise ValueError('HTTP '+str(meta['status']))
        data=json.loads(raw)
        if isinstance(data,dict) and (data.get('backoff') or data.get('quota_remaining')==0):self.paused=True
        if isinstance(data,dict) and data.get('errors'):
            raise ValueError('GraphQL: '+str(data['errors'])[:400])
        return data,meta


def forem():
    p=Probe('forem');searches=[];full=[];seen=set();error=None
    try:
        for term in ['outreachy application','gsoc proposal','scholarship application','cover letter']:
            data,receipt=p.read('https://dev.to/api/articles/search?'+urlencode({'q':term,'per_page':5,'page':1}))
            searches.append({'query':term,'returned':len(data),'receipt':receipt,
                'titles':[r['title'] for r in data]})
            for item in data[:2]:
                if item['id'] in seen:continue
                seen.add(item['id']);article,meta=p.read('https://dev.to/api/articles/'+str(item['id']))
                body=article.get('body_markdown') or ''
                full.append({'id':item['id'],'title':article['title'],'url':article['url'],
                    'author':article.get('user',{}).get('username'),'body_characters':len(body),
                    'text':store_source_bytes(body.encode(),p.folder/'blobs',suffix='.txt'),
                    'original':meta,'material_scope':'unreviewed_personal_account_or_advice',
                    'ai_disclosure_label':article.get('ai_disclosure_label'),
                    'accepted_experience':False,'approved_pattern':False})
    except (ValueError,requests.RequestException) as e:error=str(e)
    atomic_json(p.folder/'fulltext.json',full)
    return {'source':'forem','queries':searches,'fulltext_count':len(full),'samples':[
        {k:r[k] for k in ['title','url','body_characters']} for r in full],
        'requests':len(p.receipts),'error':error}


def stackexchange():
    p=Probe('stackexchange');searches=[];questions={};answers=[];error=None
    try:
        for term in ['statement of purpose','research proposal','scholarship application']:
            data,meta=p.read('https://api.stackexchange.com/2.3/search/advanced?'+urlencode({
                'site':'academia','q':term,'filter':'withbody','pagesize':3,'sort':'relevance'}))
            items=data.get('items',[])
            searches.append({'query':term,'returned':len(items),'quota_remaining':data.get('quota_remaining'),
                             'backoff':data.get('backoff'),'receipt':meta})
            for item in items:questions[item['question_id']]=item
            if p.paused:break
        if questions and not p.paused:
            ids=';'.join(str(i) for i in questions)
            data,meta=p.read('https://api.stackexchange.com/2.3/questions/'+ids+'/answers?'+urlencode({
                'site':'academia','filter':'withbody','pagesize':100,'sort':'votes','order':'desc'}))
            answers=data.get('items',[])
            atomic_json(p.folder/'answers-response.json',{'original':meta,'items':answers,'has_more':data.get('has_more')})
        atomic_json(p.folder/'questions.json',list(questions.values()))
    except (ValueError,requests.RequestException) as e:error=str(e)
    return {'source':'stackexchange','queries':searches,'question_count':len(questions),
      'question_bodies':sum(bool(r.get('body')) for r in questions.values()),
      'answer_count':len(answers),'answer_bodies':sum(bool(r.get('body')) for r in answers),
      'samples':[{'title':q['title'],'url':q['link'],'body_characters':len(q.get('body',''))} for q in questions.values()],
      'requests':len(p.receipts),'error':error,
      'role':'general_advice; accepted_answer_is_not_scholarship_selection_outcome'}


def hashnode():
    p=Probe('hashnode');samples=[];error=None;pages=[]
    try:
        for tag in ['outreachy','gsoc']:
            query='query KnowhowTagPosts { tag(slug: "'+tag+'") { id name postsCount posts(first: 3) { edges { node { id title url content { markdown } author { username } } } pageInfo { hasNextPage endCursor } } } }'
            data,meta=p.read('https://gql-beta.hashnode.com/',query)
            info=data['data']['tag']
            if not info: raise ValueError('Requested public tag is absent')
            feed=info['posts'];pages.append({'tag':tag,'receipt':meta,
                'reported_tag_posts':info['postsCount'],'pageInfo':feed['pageInfo']})
            for edge in feed['edges']:
                node=edge['node'];body=node.get('content',{}).get('markdown','')
                samples.append({'id':node['id'],'title':node['title'],'url':node['url'],
                    'author':node['author']['username'],'body_characters':len(body),
                    'text':store_source_bytes(body.encode(),p.folder/'blobs',suffix='.txt'),
                    'original':meta,'accepted_experience':False,'approved_pattern':False})
        atomic_json(p.folder/'fulltext.json',samples)
    except (ValueError,requests.RequestException) as e:error=str(e)
    return {'source':'hashnode','pages':pages,'fulltext_count':len(samples),
        'samples':[{k:r[k] for k in ['title','url','body_characters']} for r in samples],
        'requests':len(p.receipts),'error':error}


def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(ROOT).free<512*1024**2:raise ValueError('Shared cache reserve')
    with single_writer(ROOT),ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda fn:fn(),[forem,stackexchange,hashnode]))
        status={'at_kst':datetime.now(KST).isoformat(),'mode':'bounded_api_discovery',
            'credentials_used':False,'results':results,'model_called':False,
            'accepted_experiences_added':0,'approved_patterns_added':0}
        atomic_json(ROOT/'status.json',status)
        print(json.dumps(status,ensure_ascii=False))


if __name__=='__main__':main()
