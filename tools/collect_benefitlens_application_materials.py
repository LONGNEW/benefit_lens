"""Expand bounded publisher archives into hashed writing-material review inputs."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urljoin, urlsplit

import project
from benefitlens_source_storage import store_source_bytes
from import_benefitlens_full_inventory import SharedEvidence
from run_benefitlens_campaign import CACHE, KST, atomic_json, checked_root, single_writer
from run_benefitlens_korea import Held, soup

CONFIG = Path(__file__).resolve().parents[1]/'pipeline/config/experience_application_sources_v1.json'
ROOT = CACHE/'experience-application-expansion-v1'


def known_urls():
    with project.connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        return {r[0] for r in conn.execute('SELECT source_url FROM benefitlens.experience_discovery_case')}


def checked_url(parent, href, prefix):
    target=urljoin(parent,href).split('#')[0]
    parsed=urlsplit(target)
    if (not target.startswith(prefix) or parsed.username or parsed.password
            or parsed.port not in (None,443) or parsed.scheme!='https'):
        return None
    return target


def capture(ledger,url,prefix):
    seen=set()
    for _ in range(5):
        if url in seen or not checked_url(url,url,prefix):
            raise Held('Unregistered redirect or redirect loop')
        seen.add(url)
        raw,meta=ledger.request(url,redirect_metadata=True)
        if meta['status'] in (301,302,303,307,308):
            target=checked_url(url,meta['headers'].get('location',''),prefix)
            if not target: raise Held('Redirect leaves registered publisher route')
            url=target
            continue
        return raw,meta,url
    raise Held('Redirect limit')


def html_text(raw,selector):
    doc=soup(raw)
    node=doc.select_one(selector)
    if not node: raise Held('Registered content selector absent')
    for child in node.select('script,style,nav,footer,noscript,form'):
        child.decompose()
    return '\n'.join(' '.join(part.split()) for part in node.stripped_strings),doc


def collect(source,root,existing):
    folder=root/source['id'];folder.mkdir(exist_ok=True)
    ledger=SharedEvidence(folder,datetime.now(KST).date().isoformat(),120)
    prefix=source['allowed_prefix'];origin='https://'+urlsplit(source['seed']).netloc+'/'
    pending=[source['seed']];seen=set();targets={};pages=[];records=[];stop=None
    try:
        while pending and len(seen)<source['max_index_pages']:
            url=pending.pop(0)
            if url in seen: continue
            seen.add(url)
            raw,meta,final=capture(ledger,url,origin)
            if 'text/html' not in meta['headers'].get('content-type',''):
                raise Held('Expected HTML archive')
            doc=soup(raw)
            pages.append({'url':final,'original':meta})
            for anchor in doc.select(source['index_selector']):
                target=checked_url(final,anchor.get('href',''),prefix)
                if target:
                    targets.setdefault(target,{'parent_url':final,
                        'link_text':anchor.get_text(' ',strip=True),
                        'parent_sha256':meta['sha256']})
            if source['pagination_selector']:
                for anchor in doc.select(source['pagination_selector']):
                    next_url=checked_url(final,anchor.get('href',''),prefix)
                    if next_url and 'chevening_category=application' in next_url:
                        pending.append(next_url)
        for url,discovery in list(targets.items())[:source['max_documents']]:
            row={'url':url,'source_id':source['id'],'discovery':discovery,
                 'role_hint':source['role_hint'],'scope':source['scope'],
                 'accepted_experience':False,'semantic_review':'unreviewed',
                 'existing_experience_url':url in existing}
            try:
                raw,meta,final=capture(ledger,url,prefix)
                row.update(original=meta,final_url=final)
                if raw.startswith(b'%PDF-'):
                    text=subprocess.run(['pdftotext','-layout','-','-'],input=raw,
                        capture_output=True,timeout=30,check=True).stdout.decode('utf8')
                    title=discovery['link_text'];kind='pdf'
                else:
                    if 'text/html' not in meta['headers'].get('content-type',''):
                        raise Held('Expected HTML or PDF material')
                    text,doc=html_text(raw,source['content_selector'])
                    title=doc.title.get_text(' ',strip=True) if doc.title else discovery['link_text']
                    kind='html'
                if len(text.strip())<400: raise Held('Insufficient extracted material')
                flags=[]
                if re.search('reading committee|assessor|selection panel',title,re.I):
                    flags.append('assessor_guidance')
                if 'interview' in title.lower(): flags.append('interview_preparation')
                if row['existing_experience_url']: flags.append('already_registered_url')
                row.update(state='body_staged',title=title,kind=kind,
                    text=store_source_bytes(text.encode(),folder/'blobs',suffix='.txt'),
                    text_characters=len(text),form_feed_pages=text.count('\f') if kind=='pdf' else None,
                    review_flags=flags,identity_status='needs_review',round_status='needs_review',
                    reuse_rights='not_inferred_from_public_access',
                    model_called=False,approved_pattern=False)
            except (ValueError,OSError,subprocess.SubprocessError) as error:
                row.update(state='held',reason=str(error)[:240])
            records.append(row)
            atomic_json(folder/'index.json',records)
            print(json.dumps({'source':source['id'],'processed':len(records),
                              'state':row['state']},ensure_ascii=False),flush=True)
            if row['state']=='held' and re.search('HTTP (401|403|429)|Publisher paused',row['reason']):
                stop=row['reason'];break
    except (ValueError,OSError) as error:
        stop=str(error)[:240]
    atomic_json(folder/'archive_pages.json',pages)
    atomic_json(folder/'index.json',records)
    staged=[r for r in records if r['state']=='body_staged']
    return {'source_id':source['id'],'index_pages':len(pages),'discovered_urls':len(targets),
      'processed':len(records),'states':dict(Counter(r['state'] for r in records)),
      'staged_originals':len(staged),'unique_text_hashes':len({r['text']['sha256'] for r in staged}),
      'known_experience_urls':sum(r['existing_experience_url'] for r in staged),
      'new_material_urls':sum(not r['existing_experience_url'] for r in staged),
      'stop_reason':stop,'index':str(folder/'index.json')}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=CONFIG)
    parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args()
    root=checked_root(args.root);existing=known_urls()
    config=json.loads(args.config.read_text())
    with single_writer(root),ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda s:collect(s,root,existing),config['sources']))
        summary={'at_kst':datetime.now(KST).isoformat(),'sources':results,
            'config_sha256':sha256(args.config.read_bytes()).hexdigest(),
            'accepted_experiences_added':0,'approved_patterns_added':0,'model_called':False,
            'staged_originals':sum(r['staged_originals'] for r in results),
            'new_material_urls':sum(r['new_material_urls'] for r in results)}
        atomic_json(root/'status.json',summary)
        print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
