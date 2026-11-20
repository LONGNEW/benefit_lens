from run_benefitlens_korea import CACHE, Evidence, KST, project_text
from benefitlens_source_storage import store_source_bytes
from benefitlens_company_evidence import preserve_capture
from datetime import datetime
import json,os,sys,re
from pathlib import Path

ROOT=CACHE/'korea-company-expansion-v1'
OUT=Path(os.environ.get("BENEFITLENS_COMPANY_OUTPUT",ROOT))
ROOT.mkdir(exist_ok=True)
def main():
    day = datetime.now(KST).date().isoformat()
    ledger = Evidence(ROOT,day)
    manifest={}
    for line in sys.stdin:
        result=json.loads(line)
        key='official-company-'+result['source']
        manifest[key]={'service_url':result['url'],'observed':len(result['observed']),'details':[],'listing_error':result.get('error')}
        if listing:=result.get('listing'):
            manifest[key]['listing']={k:listing[k] for k in ('url','finalUrl','at','status')}|store_source_bytes(listing['html'].encode(),ROOT/'blobs')
        for row in result['records']:
            uri=row['url']
            native=uri.rstrip('/').split('/')[-1].split('?',1)[0]
            if 'error' in row:
                manifest[key]['details'].append({'url':uri,'error':row['error']})
                continue
            raw=row['html'].encode()
            body=project_text(raw,'body')
            if len(body)<100:
                continue
            detail={'url':uri,'at':datetime.fromisoformat(row['at']).astimezone(KST).isoformat(),'status':row['status'],'method':'GET','source_kind':'rendered_publisher_dom','redirected_to':row['finalUrl'],**store_source_bytes(raw,ROOT/'blobs')}
            candidate={'title':row['title'].splitlines()[0] if row['title'] else '', 'url':uri,'detail':detail,
                'listing':manifest[key].get('listing'),'selector':'body','text':store_source_bytes(body.encode(),ROOT/'blobs',suffix='.txt'),'state':'awaiting_review'}
            preserve_capture(ledger,key,native,candidate)
            hit={}
            for i,text in enumerate(body.splitlines(),1):
                if re.search(r'266만원|실습비|지급|급여|지원자격|지원 자격|지원하기|서류접수|2026.11.05|2026년 11월 6일|졸업 예정|채용대행|[Cc]losed|접수마감',text):
                    if i<620:
                        hit[i]=text[:290]
            manifest[key]['details'].append({'id':native,'url':uri,'title':candidate['title'],'original':detail['sha256'],'spans':hit})
    (OUT/'browser_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
