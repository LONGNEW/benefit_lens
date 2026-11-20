"""Bounded public-source capture for the recorded BenefitLens funding expansion."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import time
import urllib.request
from urllib.parse import urlsplit, urljoin
from bs4 import BeautifulSoup
import re
from benefitlens_source_storage import read_source_bytes, read_source_text, store_source_bytes

LIMIT=15*1024*1024


def capture_sources(urls,folder,*,max_bytes=LIMIT):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(folder).free<max_bytes+128*1024*1024:raise ValueError('Insufficient reserved space')
    used=sum(x.stat().st_size for x in folder.rglob('*') if x.is_file());records=[];last={}
    if len(urls)>20:raise ValueError('Explicit batch exceeds 20 public URLs')
    for url in urls:
        if urlsplit(url).scheme!='https':raise ValueError('HTTPS required')
        key=sha256(url.encode()).hexdigest()[:20];meta=folder/(key+'.json')
        if meta.exists():
            saved=json.loads(meta.read_text())
            for k in ('raw','text'):
                if sha256(read_source_bytes(saved[k+'_path'],storage=saved.get(k+'_storage'))).hexdigest()!=saved[k+'_sha256']:raise ValueError('Existing source changed')
            records.append(saved);continue
        host=urlsplit(url).hostname;time.sleep(max(0,1-(time.monotonic()-last.get(host,0))));last[host]=time.monotonic()
        at=datetime.now(timezone.utc).isoformat()
        try:
            if used>=max_bytes:raise ValueError('Capture byte budget exhausted')
            request=urllib.request.Request(url,headers={'User-Agent':'BenefitLens public student-funding research/1.0'})
            with urllib.request.urlopen(request,timeout=30) as response:
                raw=response.read(min(1500000,max_bytes-used)+1);final=response.url;status=response.status
            if len(raw)>1500000 or used+len(raw)>max_bytes:raise ValueError('Capture byte limit')
            soup=BeautifulSoup(raw,'lxml');main=soup.select_one('main') or soup.body or soup
            for tag in main.select('script,style,noscript'):tag.decompose()
            text=main.get_text('\n',strip=True)+'\n';tb=text.encode()
            if used+len(raw)+len(tb)>max_bytes:raise ValueError('Text plus raw exceeds budget')
            rr=store_source_bytes(raw,folder/'blobs',suffix='.html')
            tr=store_source_bytes(tb,folder/'blobs',suffix='.txt')
            used+=rr['storage']['stored_bytes']+tr['storage']['stored_bytes']
            record={'url':url,'final_url':final,'fetched_at':at,'http_status':status,
                    'raw_path':rr['path'],'raw_sha256':rr['sha256'],'raw_storage':rr['storage'],
                    'text_path':tr['path'],'text_sha256':tr['sha256'],'text_storage':tr['storage'],
                    'links':[{'text':a.get_text(' ',strip=True),'url':urljoin(final,a['href'])} for a in main.select('a[href]')]}
            encoded=json.dumps(record,ensure_ascii=False,indent=2).encode()
            if used+len(encoded)>max_bytes:raise ValueError('Capture metadata exceeds byte budget')
            with meta.open('xb') as f:f.write(encoded)
            used+=len(encoded)
            records.append(record)
        except Exception as error:
            records.append({'url':url,'fetched_at':at,'capture_state':'held','reason':type(error).__name__+': '+str(error)})
    return records


def field_line(text,label):
    """Read an exact visible label and its following LF line, without inference."""
    lines=text.splitlines()
    normal=lambda x:' '.join(x.split()).rstrip(':').casefold()
    indices=[n for n,l in enumerate(lines) if normal(l)==normal(label)]
    if len(indices)!=1:raise ValueError('Missing or ambiguous field: '+label)
    n=indices[0]
    if n+1>=len(lines):raise ValueError('Field value missing: '+label)
    return {'value':lines[n+1],'label_locator':f'L{n+1}',
            'locator':f'L{n+2}','label':lines[n]}


def span(text,quote):
    """Require one original exact span and return a one-based LF locator."""
    if text.count(quote)!=1:raise ValueError('Missing or ambiguous exact quote')
    a=text.index(quote);first=text[:a].count('\n')+1
    return f'L{first}-L{first+quote.count(chr(10))}'


def current_window(opening,closing,as_of):
    """Select the current cycle, not a past date on a multi-intake page."""
    from benefitlens_application_period import extract_dates
    today=__import__('datetime').date.fromisoformat(as_of)
    opens=sorted(d for d in extract_dates(opening) if d<=today)
    closes=sorted(d for d in extract_dates(closing) if d>today)
    if not opens or not closes:raise ValueError('No strictly future closing and already-open window')
    return opens[-1].isoformat(),closes[0].isoformat()


def parse_auckland(record,as_of):
    """Parse the official award fields; semantic acceptance remains a separate review."""
    text=read_source_text(record['text_path'],storage=record.get('text_storage'))
    fields={name:field_line(text,name) for name in
            ['Application status','Applicable study','Opening date','Closing date','Value']}
    if fields['Application status']['value']!='Apply now':raise ValueError('Not currently applying')
    opening,closing=current_window(fields['Opening date']['value'],fields['Closing date']['value'],as_of)
    links=list(dict.fromkeys(a['url'] for a in record['links'] if a['text']=='Apply now'))
    if len(links)!=1:raise ValueError('Missing or ambiguous official application link')
    return {'fields':fields,'opens_on':opening,'closes_on':closing,'application_url':links[0],
            'title':BeautifulSoup(read_source_bytes(record['raw_path'],storage=record.get('raw_storage')),'lxml').select_one('h1').get_text(' ',strip=True)}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--urls',type=Path,required=True);p.add_argument('--cache',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True);args=p.parse_args()
    records=capture_sources(json.loads(args.urls.read_text()),args.cache)
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    with args.receipt.open('x') as f:json.dump({'records':records,'limit_bytes':LIMIT,'stored_bytes':sum(x.stat().st_size for x in args.cache.rglob('*') if x.is_file()),'new_http_only_explicit_urls':True},f,ensure_ascii=False,indent=2)
    print(json.dumps({'sources':len(records),'captured':sum('raw_sha256' in r for r in records),'receipt':str(args.receipt)},ensure_ascii=False))


if __name__=='__main__':main()
