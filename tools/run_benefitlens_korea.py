"""Collect Korean official evidence daily into a local, resumable audit store."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timedelta
from hashlib import sha256
from html import unescape
import io
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
import requests

from benefitlens_application_period import extract_dates
from benefitlens_opportunity_groups import norm
from benefitlens_source_storage import read_source_bytes, store_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json, encoded, retry_after, single_writer

ROOT = CACHE / 'korea-campaign-v1'
CONFIG = Path(__file__).resolve().parents[1] / 'pipeline/config/korea_campaign.json'
UA = 'BenefitLens-Python/1.0 public student-support research'
ACADEMIC_SUPPORT = r'장학|학자금|등록금.{0,10}지원|주거비|생활비|인턴|현장실습|일경험'
ACTIVITY_SUPPORT = (
    r'서포터즈|앰배서더|홍보대사|멘토링|공모전|경진대회|해커톤|'
    r'연구\s*참여|실험\s*(?:참여|참가)|설문\s*참여|활동비|참여수당|사례비|'
    r'창작\s*지원|창업\s*지원|논문\s*(?:완성|작성)\s*지원|긴급\s*지원|'
    r'조교|연구보조|stipend|assistantship|honorarium|bursary'
)
TOPIC = re.compile(ACADEMIC_SUPPORT + '|' + ACTIVITY_SUPPORT, re.I)
STUDENT = re.compile(r'대학\s*\(?원\)?\s*생|대학생|대학원생|학부생|재학생|휴학생|복학생|[석박]사과정')
INTERNSHIP = re.compile(r'인턴|현장\s*실습|일경험')
PAY = re.compile(r'(급여|수당|실습\s*지원비|보수|임금|시급|월급).{0,120}(?:\d[\d,]*\s*(?:만\s*)?원|최저임금)', re.S)
ACTIVITY_PAYMENT = re.compile(
    r'활동비|참여\s*(?:비|수당|보상)|수료\s*수당|사례비|사례금|상금|시상금|'
    r'훈련장려금|지원금|기프티콘|상품권|교통비|숙박비|임금|시급|월급|급여|'
    r'stipend|honorarium|allowance|cash prize|paid', re.I)
RANGE = re.compile(r'\s*[~∼～–—]\s*')
APPLY = re.compile(r'(신청|접수|지원|제출)\s*(기간|기한|마감|일정)|모집\s*기간|채용\s*기간')
ROUTE = re.compile(r'(신청|접수|지원|제출)\s*(방법|경로|처|사이트)|지원하기|신청하기')
DATE = re.compile(r'(?<!\d)(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})(?!\d)')
RANGE_END = re.compile(r'^(\d{1,2})\s*[.\-/월]\s*(\d{1,2})(?!\d)')
STATUS_NOTE = 'Current unique rows require source-bound review; catalog counts are discovery only.'
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DISK_RESERVE = 256 * 1024 * 1024
YOUTH_PUBLISHERS = {
    'www.wonju.go.kr': ('/wjyouth/',),
    'youth.chungnam.go.kr': ('/web/main/customSupp/',),
    'youth.seoul.go.kr': ('/content.do',),
    'www.ulsan.go.kr': ('/s/ulsanyouth/bbs/view.do',),
    'www.iksan.go.kr': ('/reserve/board/post/list.do',),
    'plus.gov.kr': ('/portal/benefitV2/benefitTotalSrvcList/benefitSrvcDtl',),
    'www.daejeonyouthportal.kr': ('/board/',),
    'apply.jobaba.net': ('/bsns/bsnsDetailView.do',),
}


class Held(ValueError):
    pass


class Evidence:
    def __init__(self, root, day):
        self.root, self.day = root, day
        self.local = threading.local()
        self.lock = threading.Lock()
        self.host_locks, self.last, self.policies, self.blocked = {}, {}, {}, {}
        with self.db() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS captures(
              day TEXT, key TEXT, meta TEXT, PRIMARY KEY(day,key));
            CREATE TABLE IF NOT EXISTS candidates(
              day TEXT, source TEXT, native TEXT, data TEXT, PRIMARY KEY(day,source,native));
            CREATE TABLE IF NOT EXISTS source_state(
              day TEXT, source TEXT, data TEXT, PRIMARY KEY(day,source));
            """)
            for (meta,) in db.execute("SELECT meta FROM captures ORDER BY day"):
                item = json.loads(meta)
                if item.get('blocked_until', 0) > time.time():
                    host = urlsplit(item['url']).hostname
                    until = item['blocked_until']
                    if until > self.blocked.get(host, (0, ''))[0]:
                        self.blocked[host] = (until, f"HTTP {item['status']}")

    def db(self):
        return sqlite3.connect(self.root/'korea.sqlite', timeout=30)

    def request(self, url, *, data=None, payload=None, redirect_metadata=False):
        u = urlsplit(url)
        if u.scheme != 'https' or u.username or u.password or u.port not in (None, 443):
            raise Held('Only public HTTPS publisher URLs are accepted')
        with self.lock:
            lock = self.host_locks.setdefault(u.hostname, threading.Lock())
        with lock:
            if u.hostname not in self.policies:
                self.check_robots(u.hostname)
            if not self.policies[u.hostname].can_fetch(UA, url):
                raise Held('robots disallows publisher path')
            return self.fetch(url, data=data, payload=payload, redirect_metadata=redirect_metadata)

    def check_robots(self, host):
        url = f'https://{host}/robots.txt'
        parser = RobotFileParser(url)
        raw, meta = self.fetch(url, robots=True, redirect_metadata=True)
        visited = {url}
        while meta['status'] in (301, 302, 303, 307, 308):
            target = urljoin(meta['url'], meta['headers'].get('location', ''))
            u = urlsplit(target)
            if (u.scheme != 'https' or u.port not in (None, 443) or u.username or u.password
                    or (u.hostname or '').removeprefix('www.') != host.removeprefix('www.')
                    or target in visited or len(visited) >= 5):
                raise Held('Robots redirect is outside the verified publisher scope')
            visited.add(target)
            raw, meta = self.fetch(target, robots=True, redirect_metadata=True)
        if meta['status'] == 200:
            if len(raw) > 512 * 1024 or raw.lstrip().startswith(b'<'):
                raise Held('Unusable robots policy')
            parser.parse(raw.decode('utf-8-sig').splitlines())
        else:
            parser.parse(['User-agent: *', 'Allow: /'])
        self.policies[host] = parser

    def fetch(self, url, *, data=None, payload=None, robots=False, redirect_metadata=False):
        host = urlsplit(url).hostname
        method = 'POST' if data is not None or payload is not None else 'GET'
        key = sha256(encoded([method, url, data, payload]).encode()).hexdigest()
        redirects = (301, 302, 303, 307, 308) if redirect_metadata and method == 'GET' else ()
        with self.db() as db:
            record = db.execute('SELECT meta FROM captures WHERE day=? AND key=?',
                                (self.day, key)).fetchone()
        if record:
            meta = json.loads(record[0])
            if meta['status'] in (200, *redirects) or robots and meta['status'] in (404, 410):
                raw = read_source_bytes(meta['path'], storage=meta['storage'])
                if sha256(raw).hexdigest() != meta['sha256']:
                    raise Held('Stored original hash mismatch')
                return raw, meta
            retry_cached = getattr(self, 'retry_cached_error', lambda _: False)
            if not retry_cached(meta):
                raise Held(f"Stored HTTP {meta['status']}")
        until, reason = self.blocked.get(host, (0, ''))
        if until > time.time():
            raise Held(f'Publisher paused: {reason}')
        byte_limit = getattr(self, 'response_byte_limit', lambda _: MAX_RESPONSE_BYTES)(url)
        if shutil.disk_usage(self.root).free < DISK_RESERVE + 2 * byte_limit:
            raise Held('Shared dataset storage reserve reached')
        delay = max(2, getattr(self, 'host_intervals', {}).get(host, 2),
                    0 if robots else self.policies[host].crawl_delay(UA) or 0)
        time.sleep(max(0, self.last.get(host, 0) + delay - time.monotonic()))
        self.last[host] = time.monotonic()
        if not hasattr(self.local, 'session'):
            self.local.session = requests.Session()
            self.local.session.headers['User-Agent'] = UA
        with self.local.session.request(method, url, data=data, json=payload, timeout=(10, 20),
                                        stream=True, allow_redirects=False) as response:
            blob = bytearray()
            for chunk in response.iter_content(65536):
                blob.extend(chunk)
                if len(blob) > byte_limit:
                    raise Held('Publisher response exceeds byte ceiling')
            raw = bytes(blob)
            status = response.status_code
            blocked_until = 0
            if status == 429:
                blocked_until = time.time() + retry_after(response.headers.get('Retry-After'), time.time())
            if status in (401, 403):
                blocked_until = time.time() + 12*3600
            if hasattr(self, 'publisher_retry_at'):
                blocked_until = self.publisher_retry_at(url, status, response.headers, raw, blocked_until)
            if blocked_until:
                self.blocked[host] = (blocked_until, f'HTTP {status}')
            meta = {'url': url, 'status': status, 'method': method, 'request_data': data or payload,
                    'at': datetime.now(KST).isoformat(), 'blocked_until': blocked_until,
                    'headers': {k.lower():v for k,v in response.headers.items()
                                if k.lower() in ('content-type','x-wp-total','x-wp-totalpages','location',
                                    'retry-after','link','date','etag') or k.lower().startswith('x-ratelimit-')},
                    **store_source_bytes(raw, self.root/'blobs')}
        with self.db() as db:
            if record:
                db.execute('CREATE TABLE IF NOT EXISTS capture_attempt(day TEXT,key TEXT,meta TEXT)')
                db.execute('INSERT INTO capture_attempt VALUES(?,?,?)',(self.day,key,record[0]))
            db.execute('INSERT OR REPLACE INTO captures VALUES(?,?,?)', (self.day, key, encoded(meta)))
        if status not in (200, *redirects) and not (robots and status in (404, 410)):
            raise Held(f'HTTP {status}')
        return raw, meta

    def save(self, source, native, record):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO candidates VALUES(?,?,?,?)',
                       (self.day, source, native, encoded(record)))

    def source_state(self, source, record):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO source_state VALUES(?,?,?)',
                       (self.day, source, encoded(record)))


def soup(raw):
    return BeautifulSoup(raw, 'lxml')


def compact(value):
    return ' '.join(str(value or '').split())


def view(raw, selector):
    body = soup(raw).select_one(selector)
    if body is None:
        raise Held('Publisher detail content selector not found')
    for node in body.select('script,style,noscript'):
        node.decompose()
    return body


def canonical(url, params):
    parsed = urlsplit(url)
    return parsed._replace(query=urlencode(params), fragment='').geturl()


def project_text(raw, selector):
    if selector == 'pdf':
        if not raw.startswith(b'%PDF-'):
            raise Held('Publisher attachment is not a PDF')
        result = subprocess.run(['pdftotext','-layout','-','-'],input=raw,
                                capture_output=True,timeout=20,check=True)
        return '\n'.join(compact(line) for line in result.stdout.decode().splitlines() if line.strip())
    return '\n'.join(compact(line) for line in view(raw,selector).stripped_strings)


def korean_dates(value):
    clean = compact(value)
    dates = extract_dates(clean)
    left, *right = RANGE.split(clean, maxsplit=1)
    def add(values):
        try:
            dates.add(datetime(*map(int,values)).date())
        except ValueError:
            pass
    if match := DATE.search(left):
        add(match.groups())
        if right:
            if close := DATE.search(right[0]):
                add(close.groups())
            elif end := RANGE_END.match(right[0]):
                add((match[1], *end.groups()))
    return dates


def classify(text, title, day):
    if any(line.strip() in {'접수마감','모집마감','신청마감','접수 종료'} for line in text.splitlines()):
        return 'publisher_marks_closed'
    if not TOPIC.search(title):
        return 'outside_scope'
    is_intern = bool(INTERNSHIP.search(title))
    if not STUDENT.search(text):
        return 'student_evidence_missing'
    if is_intern and not PAY.search(text):
        return 'internship_payment_missing'
    if not re.search(ACADEMIC_SUPPORT, title) and not ACTIVITY_PAYMENT.search(text):
        return 'activity_payment_unconfirmed'
    dates = set()
    for m in APPLY.finditer(text):
        section = text[m.end():m.end()+135]
        if found := korean_dates(section):
            dates.update(found)
    now = datetime.fromisoformat(day).date()
    if not any(d >= now for d in dates):
        return 'current_application_dates_unconfirmed'
    if not ROUTE.search(text):
        return 'application_route_unconfirmed'
    return 'awaiting_review'


def capture_candidate(ledger, spec, native, item):
    active_item = item.copy()
    if item.get('state') == 'catalog_only':
        ledger.save(spec['id'], native, active_item)
        return
    try:
        url = item['url']
        if urlsplit(url).hostname != urlsplit(spec['url']).hostname and not (
                spec['kind']=='youth_publisher' and verified_publisher_url(url)):
            raise Held('Detail origin does not match registered publisher')
        raw, meta = ledger.request(url, data=item.get('detail_form'))
        selector = item.get('selector', spec.get('content','main,body'))
        text = project_text(raw,selector)
        if len(text)<80:
            raise Held('Detail content is empty')
        tr = store_source_bytes(text.encode(), ledger.root/'blobs', suffix='.txt')
        active_item.update(detail=meta, text=tr, state=classify(text,item['title'],ledger.day))
        if selector!='pdf':
            active_item['attachments']=[urljoin(url,n['href']) for n in soup(raw).select(
                'a[href*="attachNo="],a[href*="download"],a[href$=".pdf"]')][:20]
    except (ValueError, OSError, subprocess.SubprocessError, requests.RequestException) as exc:
        active_item.update(state='held', reason=str(exc)[:300])
    ledger.save(spec['id'],native,active_item)


def verified_publisher_url(url):
    parsed = urlsplit(url)
    return parsed.scheme=='https' and parsed.netloc in YOUTH_PUBLISHERS and any(
        parsed.path==path or (path.endswith('/') and parsed.path.startswith(path))
        for path in YOUTH_PUBLISHERS[parsed.netloc])


def board_items(raw, url, spec):
    for a in soup(raw).select('a[href]'):
        address = a['href']
        if spec.get('kind')=='kyunghee':
            match = re.fullmatch(r"javascript:view\('([0-9]+)'\);",address)
            if not match:
                continue
            address = canonical(url.replace('/list.do','/view.do'),
                                {'boardId':match[1],'menuNo':'12300032'})
        href = urljoin(url,address)
        if urlsplit(href).hostname != urlsplit(url).hostname:
            continue
        if (path := spec.get('query_detail_path')) and urlsplit(href).path != path:
            continue
        query = parse_qs(urlsplit(href).query)
        if route := spec.get('detail_path'):
            match = re.fullmatch(route,urlsplit(href).path)
            native = match[1] if match else ''
        else:
            native = query.get(spec['identity'], [''])[0]
        if not native.isdigit() or query.get('mode') in (['download'], ['file-download']):
            continue
        title = compact(a.get_text(' ', strip=True))
        if (TOPIC.search(title) and not re.search(r'최종\s*합격자|선발\s*결과|현황\s*공개',title)):
            yield native, {'title': title, 'url':href, 'issuer':spec.get('issuer')}


def normalized_link(base, href):
    url = urlsplit(urljoin(base,href))
    return url._replace(path=quote(url.path,safe="/%:@!$&'()*+,;=-._~")).geturl()


def kboard_attachment(landing, document, original, filename):
    parent = urlsplit(landing)
    for node in document.select('.kboard-attach a[onclick]'):
        action = re.fullmatch(r"window\.location\.href='([^']+)'",node['onclick'])
        if not action:
            continue
        candidate = normalized_link(landing,action[1])
        parsed = urlsplit(candidate)
        params = parse_qs(parsed.query)
        if (parsed.scheme == 'https' and parsed.netloc == parent.netloc and parsed.path == parent.path
                and params.get('mod') == ['document']
                and params.get('uid') == parse_qs(parent.query).get('uid')
                and params.get('action') == ['kboard_file_download']
                and params.get('file') == [filename]
                and params.get('kboard-file-download-nonce')):
            return {**original, 'url':candidate}
    raise Held('KBoard attachment link did not match the official posting')


def board(ledger, spec, stats):
    url = spec['url']
    raw, meta = ledger.request(url)
    if len(raw) < 500 or b'system_notice/warning' in raw:
        raise Held('Publisher returned a warning instead of notice list')
    stats['pages'] = 1
    for native,item in spec.get('additional_urls',{}).items():
        if spec['kind'] == 'kboard_attachment':
            item = kboard_attachment(url,soup(raw),item,item['file_field'])
        elif item.get('selector')=='pdf':
            anchors = {a['href'] for a in soup(raw).select('a[href]')}
            if item.get('file_action'):
                action = item['file_action']
                if spec['kind'] != 'gojobs' or action not in anchors:
                    raise Held('Publisher attachment action missing from official posting')
                match = re.fullmatch(r"javascript:gfn_fileDown\('([^']+)','([^']+)','([^']+)'\)",action)
                if not match or urlsplit(item['url']).path != '/downFile.do':
                    raise Held('Unrecognized public attachment download')
                fields = ('filenm','uuid','saveGbn')
                if any(item['detail_form'].get(k) != v for k,v in zip(fields,match.groups())):
                    raise Held('Attachment download form differs from published action')
            elif item['url'] not in {normalized_link(url,href) for href in anchors}:
                raise Held('Publisher attachment link missing from official posting')
        yield native, {**item,'issuer':spec.get('issuer'),'listing':meta,'curated_locator':True}
    if spec.get('listing_only'):
        return
    for native,item in board_items(raw,url,spec):
        yield native, {**item, 'listing':meta}
    seen = {url}
    limit = 4
    while stats['pages'] < limit:
        nxt = next((urljoin(url,a['href']) for a in soup(raw).select('a[href]')
                    if compact(a.get_text(' ',strip=True)) in ('다음 페이지로 이동하기','다음 페이지로 이동','다음 페이지','다음페이지','다음')
                    and urlsplit(urljoin(url,a['href'])).hostname==urlsplit(url).hostname
                    and urlsplit(urljoin(url,a['href'])).path==urlsplit(url).path
                    and urljoin(url,a['href']) not in seen),None)
        if not nxt:
            break
        seen.add(nxt)
        url = nxt
        raw, meta = ledger.request(url)
        stats['pages'] += 1
        for native,item in board_items(raw,url,spec):
            yield native,{**item,'listing':meta}


def kosaf_csv(ledger, spec, stats):
    url = spec['url']
    raw, meta = ledger.request(url)
    m = re.search(rb"fn_fileDataDown\('15028252',\s*'(uddi:[a-f0-9-]+)'",raw)
    if not m:
        raise Held('Official dataset download descriptor absent')
    origin = 'https://www.data.go.kr'
    args = {'publicDataPk':'15028252','publicDataDetailPk':m[1].decode(),
            'atchFileId':'','publicDataTyCode':'PR0051'}
    descriptor, _ = ledger.request(origin+'/tcs/dss/selectFileDataDownload.do?'+urlencode(args))
    data = json.loads(descriptor)
    if data.get('status') is not True or not re.fullmatch(r'FILE_\d+',data.get('atchFileId','')):
        raise Held('Official file descriptor was not returned')
    form = {k:data[k] for k in ('atchFileId','fileDetailSn')}
    policy, _ = ledger.request(origin+'/cmm/cmm/check-limit.json',data=form)
    if json.loads(policy).get('needCaptcha') is not False:
        raise Held('Official download requires interactive CAPTCHA')
    title = data['dataSetFileDetailInfo']['publicDataSj']
    file, cap = ledger.request(origin+'/cmm/cmm/fileDownload.do?'+urlencode({**form,'dataNm':title}))
    try:
        text = file.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = file.decode('cp949')
    reader = csv.DictReader(io.StringIO(text))
    required = {'번호','운영기관명','상품명','모집시작일','모집종료일'}
    if not reader.fieldnames or not required <= set(reader.fieldnames):
        raise Held('Official CSV header differs from the declared dataset')
    rows = list(reader)
    stats['reported'] = len(rows)
    stats['pages'] = 1
    stats['issuer_product_pairs'] = len({(norm(x['운영기관명']),norm(x['상품명'])) for x in rows})
    for row in rows:
        native = row['번호'].strip()
        if native:
            yield native, {'state':'catalog_only','title':row['상품명'],'issuer':row['운영기관명'],
                           'url':row.get('홈페이지 주소') or url,'catalog':row,'listing':meta,'csv':cap}


def youth(ledger, spec, stats):
    url = spec['url']
    landing, _ = ledger.request(url)
    if b'/pubot/search/portalPolicySearch' not in landing:
        raise Held('Public portal search action is no longer declared')
    endpoint = 'https://www.youthcenter.go.kr/pubot/search/portalPolicySearch'
    seen = set()
    for page in range(1,101):
        args = {'query':'','pageNum':page,'listCount':100,'searchFields':'all','sortFields':''}
        raw, meta = ledger.request(endpoint,payload=args)
        info = json.loads(raw)
        rows = info['searchResult']['youthpolicy']
        if not isinstance(rows,list):
            raise Held('Policy search response schema changed')
        stats['reported'] = int(info['totalCount'])
        if not rows:
            break
        ids = {str(row['DOCID']) for row in rows}
        if ids <= seen:
            raise Held('Policy search repeated a page')
        seen.update(ids)
        stats['pages'] += 1
        for row in rows:
            native = str(row['DOCID'])
            if not native.isdigit():
                continue
            relevant = any(STUDENT.search(str(row.get(k,''))) for k in (
                'PLCY_NM','PLCY_EXPLN_CN','ADD_APLY_QLFC_CND_CN','PTCP_PRP_TRGT_CN','PLCY_SPRT_CN'))
            yield native, {'state':'catalog_only','title':row['PLCY_NM'],
                           'url':url+'/ythPlcyDetail/'+native,'issuer':row.get('SPRVSN_INST_CD_NM'),
                           'student_candidate':relevant,'catalog':row,'listing':meta}
        if len(seen) >= stats['reported'] or len(rows)<100:
            break


def youth_publisher(ledger, spec, stats):
    day = ledger.day.replace('-','')
    with ledger.db() as db:
        policies = list(db.execute("SELECT data FROM candidates WHERE day=? AND source='ontong-youth'",
                                   (ledger.day,)))
    stats['policies_screened'] = len(policies)
    for (encoded_policy,) in policies:
        item = json.loads(encoded_policy)
        row = item['catalog']
        start, end = row.get('APLY_PRD_BGNG_YMD',''), row.get('APLY_PRD_END_YMD','')
        if not item['student_candidate'] or not (
                len(start)==len(end)==8 and start<=day<=end or row.get('APLY_PRD_SE_CD')=='상시'):
            continue
        for field in ('REF_URL_ADDR1','APLY_URL_ADDR','REF_URL_ADDR2'):
            url = str(row.get(field) or '').strip()
            if verified_publisher_url(url):
                url = urlsplit(url)._replace(fragment='').geturl()
                native = sha256(url.encode()).hexdigest()[:24]
                selector = 'main' if urlsplit(url).hostname == 'www.wonju.go.kr' else 'main,body'
                yield native, {**item,'url':url,'selector':selector,'state':'pending_detail'}


def work_experience(ledger, spec, stats):
    url = spec['url']
    for page in range(1,101):
        raw, meta = ledger.request(canonical(url,{'prgmSecd':'I','currentPageNo':page,'recordCountPerPage':12}))
        total = re.search(rb'totalRecordCount\s*:\s*"(\d+)"',raw)
        last = re.search(rb'totalPageCount\s*:\s*"(\d+)"',raw)
        if not total or not last:
            raise Held('Work24 official pagination metadata absent')
        stats['reported'] = int(total[1])
        stats['pages'] += 1
        s = soup(raw)
        for a in s.select('a[href^="javascript:fn_searchDetail"]'):
            native = re.fullmatch(r"javascript:fn_searchDetail\('I','(PG\d+)'\);",a['href'])
            if native:
                yield native[1], {'title':compact(a.get_text(' ',strip=True)), 'issuer':spec['issuer'],
                                  'url':'https://yw.work24.go.kr/d/a/selectItrnPrjtEsgPrgmDtal.do',
                                  'detail_form':{'untyPrgmCtn':native[1],'prgmSecd':'I'},
                                  'listing':meta}
        if page >= int(last[1]):
            break


def work24_general(ledger, spec, stats):
    base = spec['url']
    for term in spec['terms']:
        seen = set()
        reported = None
        for page in range(1, spec.get('max_pages', 60) + 1):
            url = canonical(base, {'srcKeyword':term, 'keywordWantedTitle':'Y',
                                   'pageIndex':page, 'currentPageNo':page})
            raw, meta = ledger.request(url)
            document = soup(raw)
            form = document.select_one('#mForm')
            total_label = document.find(string=re.compile(r'^\s*검색건수\s*$'))
            if not form or total_label is None:
                raise Held('Work24 public search form or result count changed')
            if reported is None:
                total = re.search(r'검색건수\s*([\d,]+)\s*건', compact(total_label.parent.get_text(' ',strip=True)))
                if not total:
                    raise Held('Work24 public search result count missing')
                reported = int(total[1].replace(',',''))
                stats.setdefault('reported_by_term', {})[term] = reported
            matches = {}
            for anchor in document.select('a[href^="/wk/a/b/1500/empDetailAuthView.do?"]'):
                address = urljoin(base, anchor['href'])
                params = parse_qs(urlsplit(address).query)
                number = params.get('wantedAuthNo',[''])[0]
                provider = params.get('infoTypeCd',[''])[0]
                if not re.fullmatch(r'[0-9A-Z]+', number) or not re.fullmatch(r'[0-9A-Z]+', provider):
                    continue
                native = provider + ':' + number
                listing_row = anchor.find_parent('tr')
                if listing_row is None:
                    continue
                issuer = listing_row.select_one('.cp_name')
                matches.setdefault(native, {
                    'title':compact(anchor.get_text(' ',strip=True)), 'url':address,
                    'issuer':compact(issuer.get_text(' ',strip=True)) if issuer else '',
                    'provider':provider, 'search_term':term, 'listing':meta,
                    'list_summary':compact(listing_row.get_text(' ',strip=True)),
                })
            stats['pages'] += 1
            added = set(matches) - seen
            if not added and reported and len(seen) < reported:
                stats.setdefault('listing_warnings', {})[term] = 'Repeated or empty page before declared total'
                break
            for native in matches:
                if native in added:
                    yield native, matches[native]
            seen.update(added)
            if len(seen) >= reported:
                break
        stats.setdefault('listed_by_term', {})[term] = len(seen)
        if reported and len(seen) < reported:
            stats.setdefault('listing_warnings', {}).setdefault(term,'Page limit before declared total')


def work24_priority(item):
    title = compact(item['title'])
    if item['search_term'] != '인턴' or re.search(r'재학|대학생|대학원|현장\s*실습|연구\s*인턴',title):
        return 0
    if re.search(r'체험형|학생|연구|신입',title):
        return 1
    return 2


def alio(ledger,spec,stats):
    url = spec['url']; params = dict((k,v[0]) for k,v in parse_qs(urlsplit(url).query).items())
    seen = set()
    for page in range(1,101):
        urlp = canonical(url,params | {'pageNo':page})
        raw,meta = ledger.request(urlp)
        rows = [(a,parse_qs(urlsplit(a['href']).query).get('idx',[''])[0])
                for a in soup(raw).select('a[href^="/recruitview.do?idx="]')]
        batch = {native for _,native in rows if native.isdigit()}
        if not batch or batch <= seen:
            break
        seen |= batch;stats['pages'] += 1
        for a,native in rows:
            title = compact(a.get_text(' ',strip=True)) or compact(a.parent.get_text(' ',strip=True))
            if native.isdigit():
                yield native, {'title':title or '공공기관 인턴 모집', 'url':urljoin(url,a['href']),'listing':meta}
        if not re.search(fr'goPage\({page+1}\)'.encode(),raw):
            break


def wordpress(ledger,spec,stats):
    url=spec['url']
    seen=set()
    for term in ('현장실습','인턴십','장학생'):
        for page in range(1,4):
            raw,meta=ledger.request(canonical(url,{'search':term,'per_page':25,'page':page,'subtype':'post'}))
            rows=json.loads(raw)
            stats['pages']+=1
            stats.setdefault('reported_by_query',{})[term]=int(meta['headers']['x-wp-total'])
            for row in rows:
                native=str(row['id'])
                if native in seen:
                    continue
                seen.add(native)
                yield native,{'title':compact(unescape(row['title'])),'url':row['url'],'issuer':spec['issuer'],
                              'selector':'.entry-content','listing':meta}
            if page>=int(meta['headers']['x-wp-totalpages']):
                break


ADAPTERS={'board':board,'kboard_attachment':board,'gojobs':board,'kyunghee':board,'kosaf_csv':kosaf_csv,'youth':youth,'youth_publisher':youth_publisher,
          'work_experience':work_experience,'work24_general':work24_general,
          'alio':alio,'wordpress':wordpress}


def collect(ledger,spec):
    sid=spec['id']
    stats={'source':sid,'kind':spec['kind'],'url':spec['url'],'pages':0,'state':'running'}
    ledger.source_state(sid,stats)
    seen={}
    try:
        for native,item in ADAPTERS[spec['kind']](ledger,spec,stats):
            if native not in seen:
                seen[native]=item
                with ledger.db() as db:
                    db.execute('INSERT OR IGNORE INTO candidates VALUES(?,?,?,?)',
                               (ledger.day,sid,native,encoded(item | {'state':item.get('state','pending_detail')})))
        if spec['kind']=='youth' and stats.get('reported')!=len(seen):
            raise Held('Captured distinct policy IDs differ from publisher search total')
        stats['state']='listed'
    except (Held,ValueError,KeyError,TypeError,requests.RequestException) as exc:
        stats.update(state='partial_or_held',reason=str(exc)[:300])
    ledger.source_state(sid,stats)
    items = seen.items()
    if spec['kind'] == 'work24_general':
        items = sorted(items, key=lambda pair:work24_priority(pair[1]))
    for native,item in items:
        if item.get('state')!='catalog_only':
            if spec['kind'] == 'work24_general':
                with ledger.db() as db:
                    record = db.execute(
                        'SELECT data FROM candidates WHERE day=? AND source=? AND native=?',
                        (ledger.day,sid,native)).fetchone()
                if record and json.loads(record[0]).get('detail'):
                    continue
            capture_candidate(ledger,spec,native,item)
    if stats['state'] == 'listed':
        stats['state'] = 'partial_or_held' if stats.get('listing_warnings') else 'complete'
    ledger.source_state(sid,stats)


def snapshot(ledger, source_ids):
    with ledger.db() as db:
        states=[json.loads(x) for source,x in db.execute(
            'SELECT source,data FROM source_state WHERE day=? ORDER BY source',(ledger.day,))
                if source in source_ids]
        for state in states:
            rows=[json.loads(x) for x, in db.execute('SELECT data FROM candidates WHERE day=? AND source=?',
                                                    (ledger.day,state['source']))]
            state.update(candidates=len(rows), distinct_originals=len({x['detail']['sha256'] for x in rows if x.get('detail')}),
                         detailed=sum(bool(x.get('detail')) for x in rows),
                         student_catalog_candidates=sum(x.get('student_candidate',False) for x in rows),
                         dispositions=dict(Counter(x['state'] for x in rows)))
    result={'at':datetime.now(KST).isoformat(),'day_kst':ledger.day,'sources':states,'note':STATUS_NOTE}
    atomic_json(ledger.root/'status.json', result)
    return result


def available_sources(root, config):
    sources = {s['id']:s for s in json.loads(config.read_text())['sources']}
    for folder in ('university-discovery', 'university-discovery-v2'):
        discovered = root/folder/'status.json'
        if discovered.exists():
            for site in json.loads(discovered.read_text()).get('sites', {}).values():
                for board in site.get('boards',[]):
                    if spec := board.get('source_config'):
                        # The same published board may be discovered by both versions.
                        if any(s['url'] == spec['url'] for s in sources.values()):
                            continue
                        sources[spec['id']] = spec
    return list(sources.values())


def run(root,config,once):
    root.mkdir(parents=True,exist_ok=True)
    with single_writer(root):
        while True:
            day=datetime.now(KST).date().isoformat()
            ledger=Evidence(root,day)
            sources=available_sources(root,config)
            source_ids={s['id'] for s in sources}
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures=[pool.submit(collect,ledger,s) for s in sources if s['kind']!='youth_publisher']
                youth_scan=next((f for f,s in zip(futures,(s for s in sources if s['kind']!='youth_publisher'))
                                 if s['kind']=='youth'), None)
                def collect_publishers(spec):
                    if youth_scan:
                        youth_scan.result()
                    collect(ledger,spec)
                futures.extend(pool.submit(collect_publishers,s) for s in sources if s['kind']=='youth_publisher')
                while not all(f.done() for f in futures):
                    snapshot(ledger,source_ids)
                    time.sleep(5)
                for f in futures:
                    f.result()
            snapshot(ledger,source_ids)
            if once:
                return
            midnight=datetime.fromisoformat(day).replace(tzinfo=KST)+timedelta(days=1)
            time.sleep(max(0,(midnight-datetime.now(KST)).total_seconds()))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('run','status'))
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--config',type=Path,default=CONFIG)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    root=args.root.resolve()
    if not root.is_relative_to(CACHE.resolve()):
        parser.error('Korea source cache must be inside the shared BenefitLens dataset root')
    if args.command=='status':
        print((root/'status.json').read_text())
    else:
        run(root,args.config,args.once)
