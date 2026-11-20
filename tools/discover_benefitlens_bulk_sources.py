"""Probe publisher-advertised bulk reading interfaces from public blog URLs.

No credentials, guessed private endpoints, case promotion or model calls.
Original API responses are saved in the shared dataset cache. Reported post
totals are discovery inventory, never counts of attributable experiences.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import shutil
from urllib.parse import urlencode, urljoin, urlsplit
import xml.etree.ElementTree as ET

from import_benefitlens_full_inventory import SharedEvidence, public_url
from run_benefitlens_campaign import CACHE, KST, atomic_json
from run_benefitlens_korea import soup
from scale_benefitlens_experiences import capture

DEFAULT_SITES=[
    'https://blogs.helsinki.fi/vaihtokertomuksiamaailmalta/',
    'https://blogs.bath.ac.uk/careers/',
    'https://blogs.bath.ac.uk/students/',
    'https://blogs.kcl.ac.uk/kingssas/',
    'https://blogs.kcl.ac.uk/moneymentors/',
]


def advertised_interfaces(raw,url):
    doc=soup(raw);api=None;feeds=[]
    for link in doc.select('link[href]'):
        address=urljoin(url,link['href'])
        if not public_url(address):
            continue
        if 'https://api.w.org/' in (link.get('rel') or []):
            api=address
        if link.get('type') in {'application/rss+xml','application/atom+xml'}:
            feeds.append(address)
    return api,list(dict.fromkeys(feeds))


def inspect(site,root,sample_size):
    key=sha256(site.encode()).hexdigest()[:16]
    folder=root/key;folder.mkdir(parents=True,exist_ok=True)
    ledger=SharedEvidence(folder,datetime.now(KST).date().isoformat(),16)
    result={'site':site,'authentication':'none','accepted_experiences_added':0,
            'reported_total_is':'publisher_posts_before_episode_and_scope_review'}
    try:
        raw,meta,final=capture(ledger,site)
        result['page_original']=meta
        api,feeds=advertised_interfaces(raw,final)
        result.update(advertised_api=api,advertised_feeds=feeds)
        if api:
            # Resolve the advertised root itself; do not guess unrelated routes.
            api_raw,receipt,api_final=capture(ledger,api)
            schema=json.loads(api_raw)
            result['api_original']=receipt
            routes=schema.get('routes',{})
            result['content_routes']=[r for r in routes if r in {'/wp/v2/posts','/wp/v2/pages'}]
            if '/wp/v2/posts' in routes:
                endpoint=api_final.rstrip('/')+'/wp/v2/posts?'+urlencode({
                    'per_page':sample_size,'page':1,'_fields':'id,date,modified,link,title,content,author,categories,tags'})
                blob,receipt,_=capture(ledger,endpoint)
                posts=json.loads(blob)
                if not isinstance(posts,list):
                    raise ValueError('Advertised posts route did not return a collection')
                result.update(state='public_fulltext_batch_observed',posts_original=receipt,
                    reported_total_posts=receipt['headers'].get('x-wp-total'),
                    returned_posts=len(posts),full_content_fields=sum(bool(p.get('content',{}).get('rendered')) for p in posts),
                    sample=[{'id':p['id'],'url':p['link'],
                             'title':soup(p['title']['rendered'].encode()).get_text(' ',strip=True),
                             'content_characters':len(p.get('content',{}).get('rendered',''))} for p in posts],
                    proposed_adapter='generic_wordpress_fulltext; episode_attribution_required')
                return result
        if feeds:
            raw,receipt,_=capture(ledger,feeds[0])
            tree=ET.fromstring(raw);items=tree.findall('./channel/item')
            full=[x.findtext('{http://purl.org/rss/1.0/modules/content/}encoded') or '' for x in items]
            result.update(state='feed_observed',feed_original=receipt,feed_items=len(items),
                          feed_full_content_fields=sum(bool(x) for x in full),
                          history_extent='current_feed_only')
        else:
            result['state']='no_advertised_bulk_interface_observed'
    except Exception as error:
        result.update(state='held_or_unusable',reason=type(error).__name__+': '+str(error)[:250])
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site',action='append',help='Public publisher URL; repeat for a batch')
    parser.add_argument('--sample-size',type=int,default=3)
    parser.add_argument('--root',type=Path,default=CACHE/'experience-bulk-discovery-v1')
    args=parser.parse_args()
    if not 1<=args.sample_size<=100:
        parser.error('sample-size must be between 1 and 100')
    args.root.mkdir(parents=True,exist_ok=True)
    args.root.resolve().relative_to(CACHE.resolve())
    if shutil.disk_usage(args.root).free<2*1024**3:
        raise ValueError('Shared cache free space below 2 GiB reserve')
    sites=args.site or DEFAULT_SITES
    if not all(public_url(s) for s in sites):
        raise ValueError('Only public publisher URLs are supported')
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda s:inspect(s,args.root,args.sample_size),sites))
    report={'at_kst':datetime.now(KST).isoformat(),'schema':'bulk-source-discovery-v1',
            'model_called':False,'sources':results}
    atomic_json(args.root/'audit.json',report)
    for result in results:
        print(json.dumps({k:v for k,v in result.items() if not k.endswith('_original')},ensure_ascii=False))
    print(json.dumps({'receipt':str(args.root/'audit.json')},ensure_ascii=False))


if __name__=='__main__':
    main()
