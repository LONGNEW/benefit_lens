"""Collect public WordPress.com post bodies through the documented read API.

Example:
  /usr/bin/python3 tools/collect_benefitlens_wordpress_public_api.py \
    --site sampurnabuffalo.wordpress.com --max-pages 1

Optional --from-gsoc-directory reads only observed WordPress.com domains from
the cached shared directory. Everything is staged for episode attribution;
blog posts are not automatically promoted to accepted experiences.
"""
import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import signal
import threading
from urllib.parse import urlencode,urlsplit
import uuid

from benefitlens_documented_api import DocumentedPublicAPI,API_HOST
from benefitlens_source_storage import read_source_bytes,store_source_bytes
from run_benefitlens_campaign import CACHE,KST,atomic_json,checked_root,single_writer
from run_benefitlens_korea import soup

ROOT=CACHE/'experience-wordpress-com-v1'


def directory_sites(path):
    report=json.loads(path.read_text());ref=report['candidate_index_jsonl']
    raw=read_source_bytes(ref['path'],storage=ref.get('storage'))
    if sha256(raw).hexdigest()!=ref['sha256']:
        raise ValueError('Shared directory checksum mismatch')
    sites=set()
    for line in raw.decode().splitlines():
        row=json.loads(line)
        host=urlsplit(row.get('candidate_report_url') or '').hostname or ''
        if re.fullmatch(r'[a-z0-9][a-z0-9-]*\.wordpress\.com',host):
            sites.add(host)
    return sorted(sites)


def collect(args):
    root=checked_root(args.root)
    sites=set(args.site or [])
    if args.from_gsoc_directory:
        sites.update(directory_sites(args.from_gsoc_directory))
    sites=sorted(sites)[:args.max_sites]
    if not sites or not all(re.fullmatch(r'[a-z0-9][a-z0-9-]*\.wordpress\.com',s) for s in sites):
        raise ValueError('Supply public WordPress.com blog domains only')
    with single_writer(root):
        ledger=DocumentedPublicAPI(root,str(uuid.uuid4()),args.max_requests)
        with ledger.db() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS public_api_post(
                site TEXT,post_id TEXT,modified TEXT,payload TEXT,
                PRIMARY KEY(site,post_id))''')
        results=[]
        stopped=threading.Event()
        signal.signal(signal.SIGTERM,lambda *_:stopped.set())
        def checkpoint(current=None):
            with ledger.db() as c:
                total=c.execute('SELECT count(*) FROM public_api_post').fetchone()[0]
            atomic_json(root/'status.json',{'at_kst':datetime.now(KST).isoformat(),
                'state':'running','model_called':False,'accepted_experiences_added':0,
                'staged_unique_posts':total,'sites':results,'requested_sites':len(sites),
                'current_site':current,'network_requests_this_run':ledger.network_requests})
        checkpoint()
        try:
            for site in sites:
                if stopped.is_set():
                    break
                state={'site':site,'pages_this_run':0,'post_records_this_run':0,'state':'collecting'}
                try:
                    for page in range(1,args.max_pages+1):
                        if stopped.is_set():
                            state['state']='requested_checkpoint';break
                        query={'number':args.page_size,'page':page,'status':'publish','context':'display',
                               'fields':'ID,URL,title,content,date,modified,author,status'}
                        url=f'https://{API_HOST}/rest/v1.1/sites/{site}/posts/?'+urlencode(query)
                        raw,meta=ledger.request(url)
                        batch=json.loads(raw)
                        if not isinstance(batch,dict) or not isinstance(batch.get('posts'),list):
                            raise ValueError('Public API did not return post records')
                        state.update(reported_total_posts=batch.get('found'),access_basis=meta['access_basis'])
                        posts=batch['posts']
                        for index,post in enumerate(posts):
                            if not post.get('ID') or not post.get('URL') or not post.get('content'):
                                continue
                            if post.get('status') not in (None,'publish'):
                                raise ValueError('Unexpected non-published record')
                            text=soup(post['content'].encode()).get_text('\n',strip=True)
                            textref=store_source_bytes(text.encode(),root/'blobs',suffix='.txt')
                            author=post.get('author') or {}
                            row={'source_family':'wordpress_com_public_api','source_site':site,
                                 'source_record_id':str(post['ID']),'original_url':post['URL'],
                                 'title':soup(post.get('title','').encode()).get_text(' ',strip=True),
                                 'published_at':post.get('date'),'modified_at':post.get('modified'),
                                 'author':{k:author[k] for k in ('ID','name','URL') if k in author},
                                 'batch_original':meta,'record_pointer':'/posts/'+str(index),'text':textref,
                                 'program_binding':'unreviewed','episode_attribution':'unreviewed',
                                 'accepted_experience':False,'content_reuse_rights':'not_confirmed'}
                            with ledger.db() as c:
                                c.execute('INSERT OR REPLACE INTO public_api_post VALUES(?,?,?,?)',
                                          (site,str(post['ID']),post.get('modified'),json.dumps(row,ensure_ascii=False)))
                            state['post_records_this_run']+=1
                        state['pages_this_run']+=1
                        if not posts or page*args.page_size>=batch.get('found',page*args.page_size+1):
                            state['state']='publisher_posts_enumerated';break
                    else:
                        state['state']='page_limit_checkpoint'
                except Exception as error:
                    state.update(state='held',reason=type(error).__name__+': '+str(error)[:300])
                results.append(state)
                checkpoint(site)
                print(json.dumps(state,ensure_ascii=False),flush=True)
        finally:
            with ledger.db() as c:
                all_rows=[json.loads(r[0]) for r in c.execute('SELECT payload FROM public_api_post ORDER BY site,post_id')]
            index=('\n'.join(json.dumps(r,ensure_ascii=False,separators=(',',':')) for r in all_rows)+'\n').encode()
            manifest={'at_kst':datetime.now(KST).isoformat(),'state':'checkpoint',
                      'model_called':False,'accepted_experiences_added':0,
                      'staged_unique_posts':len(all_rows),'sites':results,
                      'requested_sites':len(sites),'processed_sites':len(results),
                      'stop_reason':'requested_checkpoint' if stopped.is_set() else 'bounded_batch_finished',
                      'network_requests_this_run':ledger.network_requests,
                      'records_jsonl':store_source_bytes(index,root/'blobs',suffix='.txt')}
            atomic_json(root/'status.json',manifest)
        print(json.dumps({k:v for k,v in manifest.items() if k not in ('sites','records_jsonl')},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site',action='append')
    parser.add_argument('--from-gsoc-directory',type=Path)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--max-sites',type=int,default=20)
    parser.add_argument('--max-pages',type=int,default=1)
    parser.add_argument('--page-size',type=int,default=100)
    parser.add_argument('--max-requests',type=int,default=100)
    args=parser.parse_args()
    if not 1<=args.page_size<=100 or min(args.max_sites,args.max_pages,args.max_requests)<1:
        parser.error('Use positive limits and a page size from 1 to 100')
    collect(args)
