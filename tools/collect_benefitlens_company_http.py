from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit, urlencode
import json, os, re
import requests

from run_benefitlens_korea import CACHE, Evidence, KST, Held, soup, compact, project_text
from benefitlens_source_storage import store_source_bytes
from benefitlens_company_evidence import preserve_capture

ROOT = CACHE/'korea-company-expansion-v1'
OUT = Path(os.environ.get("BENEFITLENS_COMPANY_OUTPUT", ROOT))
NAVER = 'https://recruit.navercorp.com'
WEBTOON = 'https://recruit.webtoonscorp.com'
SK = 'https://www.skcareers.com'
AUMOVIO = 'https://jobs.aumovio.com'
GLOW = 'https://guhada.ninehire.site'
MANPOWER = 'https://manpowergroupkorea.ninehire.site'
PEARL = 'https://www.pearlabyss.com'
INTERNSHIP = re.compile(r'인턴|intern', re.I)

SOURCES = {
    'naver': ('naver', NAVER+'/rcrt/list.do?empTypeCdArr=0040&empTypeCdData=0040', {}),
    'webtoon': ('naver', WEBTOON+'/rcrt/list.do?sw=%EC%9D%B8%ED%84%B4', {'30005611':'2027 상반기 채용연계형 인턴십'}),
    'sk': ('sk', SK+'/Recruit/GetRecruitList', {}),
    'aumovio': ('direct', AUMOVIO+'/en/', {
      'https://jobs.aumovio.com/typo3temp/assets/tx_conjobs/pdf_export/REF82159P-p-56a1b04dd7c861129174b362eaa9e6c8-de-744000161096899.pdf':'AUMOVIO Star Internship IT',
      'https://jobs.aumovio.com/typo3temp/assets/tx_conjobs/pdf_export/REF82288J-p-4ffbee2339fb3b597d05ffdde44340c5-de-744000161095420.pdf':'AUMOVIO Star Internship Quality'}),
    'guhada': ('direct', GLOW+'/', {GLOW+'/job_posting/FoYAywAr':'TikTok Lab Intern'}),
    'manpower': ('direct', MANPOWER+'/', {MANPOWER+'/job_posting/MRpA1hXQ':'Haleon Korea CE Marketing Intern'}),
    'pearlabyss': ('pearl', PEARL+'/ko-KR/Company/Careers/List', {}),
}
SK_QUERY = {'sort':'2','searchText':'','corpCode':'','jobRole':'0','recruitType':'','workingType':'','workingRegion':''}

def list_rows(mode, raw, extra):
    posts = {}
    if mode == 'naver':
        for a in soup(raw).select('a.card_link[onclick]'):
            match = re.fullmatch(r"show\('(\d+)'\)", a['onclick'].strip())
            if match:
                ident = match.group(1)
                title = compact(a.get_text(" ",strip=True)).split(' 모집 부서',1)[0]
                posts[ident] = title
    elif mode == 'sk':
        j = json.loads(raw)
        if not j.get('success') or j['totalCount'] != len(j['list']):
            raise Held('SK list result does not match published total')
        posts = {e['noticeID']:e['title'].strip() for e in j['list']}
    elif mode == 'pearl':
        for a in soup(raw).select('a[href*="/Company/Careers/detail?_jobOpeningNo="]'):
            ident = parse_qs(urlsplit(a['href']).query).get('_jobOpeningNo',[''])[0]
            if ident.isdigit():
                posts[ident]=compact(a.get_text(' ',strip=True))
    elif mode == 'direct':
        hosts = {urlsplit(u).netloc for u in extra}
        for a in soup(raw).select('a[href*="/job_posting/"],a[href$=".pdf"]'):
            link = urljoin(next(iter(extra)), a['href'])
            if urlsplit(link).netloc in hosts:
                posts[link] = compact(a.get_text(" ",strip=True))
    return posts, posts | extra

def scrape(ledger, label, spec):
    mode, list_url, extra = spec
    output = {'service_url':list_url, 'records':[], 'listed':0,'details':0}
    key='official-company-'+label
    try:
        raw, listing = ledger.request(list_url, data=SK_QUERY if mode=='sk' else None)
        discovered, rows = list_rows(mode,raw,extra)
        output['listed'] = len(discovered)
        output['listing'] = listing
    except (ValueError,requests.RequestException,OSError) as e:
        rows = extra
        output['listing_error'] = str(e)
        listing = None
    for ident,title in rows.items():
        if not INTERNSHIP.search(title) and ident not in extra:
            continue
        if mode=='naver':
            uri = urljoin(list_url,'/rcrt/view.do?'+urlencode({'annoId':ident,'lang':'ko'}))
        elif mode=='sk':
            uri = SK+'/Recruit/Detail/'+ident
        elif mode=='pearl':
            if title.startswith('인재 Pool'):
                continue
            uri=PEARL+'/ko-KR/Company/Careers/detail?_jobOpeningNo='+ident
        else:
            uri = ident
            ident = urlsplit(uri).path.rsplit('/',1)[-1]
        try:
            raw, reference = ledger.request(uri)
            selector = 'pdf' if raw.startswith(b'%PDF-') else 'body'
            body = project_text(raw,selector)
            if len(body)<100:
                raise Held('detail content is missing')
            candidate = {'title':title,'url':uri,'detail':reference,'listing':listing,
                         'text':store_source_bytes(body.encode(),ROOT/'blobs',suffix='.txt'),
                         'selector':selector,'state':'awaiting_review'}
            preserve_capture(ledger,key,ident,candidate)
            output['details']+=1
            output['records'].append({'id':ident,'url':uri,'title':title,'original':reference['sha256']})
        except (OSError, ValueError, requests.RequestException) as ex:
            output['records'].append({'id':ident,'url':uri,'title':title,'error':str(ex)[:300]})
    return key,output

def main():
    ROOT.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    ledger = Evidence(ROOT, datetime.now(KST).date().isoformat())
    with ThreadPoolExecutor(max_workers=8) as pool:
        manifest = dict(pool.map(lambda label:scrape(ledger,label,SOURCES[label]),SOURCES))
    (OUT/'http_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    for k,row in manifest.items(): print(k,row['listed'],row['details'],row.get('listing_error'))
if __name__=='__main__':main()
