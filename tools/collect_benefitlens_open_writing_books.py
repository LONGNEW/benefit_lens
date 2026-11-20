"""Collect publisher-linked Writing Spaces chapters with robots and PDF provenance."""
import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urljoin,urlsplit
from urllib.robotparser import RobotFileParser

from collect_benefitlens_writing_foundations import ALLOWED,PublicRead,ROOT
from benefitlens_source_storage import store_source_bytes
from run_benefitlens_campaign import KST,atomic_json,single_writer
from run_benefitlens_korea import soup

HOST='wacclearinghouse.org'
INDEX='https://'+HOST+'/books/writingspaces/'


def chapters(raw,page_url):
    """Keep individual chapter links, not the complete-volume duplicate."""
    found={}
    for a in soup(raw).select('a[href]'):
        url=urljoin(page_url,a['href']);title=a.get_text(' ',strip=True)
        if urlsplit(url).hostname!=HOST or not urlsplit(url).path.lower().endswith('.pdf'):continue
        if title.lower() in ('in pdf format','pdf format','pdf') or '/docs/books/writingspaces' not in url:continue
        parent=a.find_parent('p') or a.parent
        context=parent.get_text(' ',strip=True)
        if ' by ' not in context:continue
        author=context.rsplit(' by ',1)[-1]
        found[url]={'title':title,'author_as_printed':author,'url':url,'publisher_index':page_url}
    return list(found.values())


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request-limit',type=int,default=100)
    args=p.parse_args()
    if not 1<=args.request_limit<=200:p.error('Bounded request limit must be 1..200')
    root=ROOT/'open-books';root.mkdir(exist_ok=True)
    if shutil.disk_usage(root).free<2*1024**3:raise ValueError('Shared cache reserve')
    ALLOWED.add(HOST)
    with single_writer(root):
        reader=PublicRead(root,args.request_limit)
        robots_raw,robots_receipt=reader.read('https://'+HOST+'/robots.txt')
        robots=RobotFileParser();robots.parse(robots_raw.decode().splitlines())
        def read(url):
            if not robots.can_fetch('BenefitLens',url):raise ValueError('Publisher robots disallows URL')
            return reader.read(url)
        raw,index_receipt=read(INDEX)
        volumes=sorted({urljoin(INDEX,a['href']) for a in soup(raw).select('a[href]')
                        if re.fullmatch(r'/books/writingspaces/writingspaces\d+/?',urlsplit(urljoin(INDEX,a['href'])).path)})
        docs={d['document_id']:d for d in json.loads((root/'records.json').read_text())} if (root/'records.json').exists() else {}
        held=[];candidates=[]
        for volume in volumes:
            body,receipt=read(volume)
            candidates.extend({**c,'index_receipt':receipt} for c in chapters(body,volume))
        for spec in candidates:
            try:
                raw,receipt=read(spec['url'])
                if not raw.startswith(b'%PDF'):raise ValueError('Linked file is not a PDF')
                pdfs=root/'pdfs';pdfs.mkdir(exist_ok=True)
                target=pdfs/(sha256(raw).hexdigest()+'.pdf');target.write_bytes(raw)
                result=subprocess.run(['pdftotext','-layout',str(target),'-'],capture_output=True,timeout=30)
                if result.returncode:raise ValueError('PDF text extraction failed')
                text=result.stdout.decode()
                if len(text.strip())<100:raise ValueError('No complete extractable chapter text')
                key='writing-spaces:'+sha256(spec['url'].encode()).hexdigest()[:24]
                docs[key]={'document_id':key,'source_id':'writing-spaces','genre':'college_writing_instruction',
                           'unit':'instructional_chapter','role':'general_writing_principle',**spec,
                           'raw':receipt,'text':store_source_bytes(text.encode(),root/'blobs',suffix='.txt'),
                           'pdf_path':str(target),'review_state':'candidate',
                           'reuse_scope':'Publisher offers chapters under Creative Commons; preserve per-chapter license text.'}
                atomic_json(root/'records.json',list(docs.values()))
            except (ValueError,OSError,subprocess.TimeoutExpired) as error:
                held.append({'url':spec['url'],'reason':str(error)})
        report={'at_kst':datetime.now(KST).isoformat(),'robots':robots_receipt,'publisher_index':index_receipt,
                'volume_indexes':volumes,'discovered_chapters':len(candidates),'collected_chapters':len(docs),
                'held':held,'network_requests':reader.calls,'whole_volume_duplicates_counted':False,'model_called':False}
        atomic_json(root/'status.json',report);print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
