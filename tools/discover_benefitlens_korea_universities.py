"""Find public university scholarship and internship boards from the official directory."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urljoin, urlsplit

import requests

from benefitlens_source_storage import read_source_bytes
from run_benefitlens_campaign import KST, atomic_json, single_writer
from run_benefitlens_korea import CONFIG, ROOT, Evidence, Held, TOPIC, board_items, collect, compact, soup

BOARD_NAME = re.compile(r'장학\s*(?:금\s*)?(?:공지|소식|공고|안내)|교외\s*장학|현장실습|인턴십|일경험')
DETAIL_PATH = re.compile(r'(/bbs/[A-Za-z0-9_-]+/\d+/)\d+/artclView\.do')
ACCESS_PATH = re.compile(r'(?:login|signin|sso|auth|oauth)', re.I)
QUERY_IDS = ('articleNo','bbsidx','board_seq','boardId','boardSeq')


def initial_sites(source):
    sites = {}
    for row in source:
        if row.get('UNIV_SE_NM') not in ('대학','전문대학') or not row.get('HMPG_ADDR'):
            continue
        text = row['HMPG_ADDR'].strip()
        parsed = urlsplit(text if '://' in text else 'https://'+text)
        if parsed.scheme not in ('http','https') or not parsed.hostname or not parsed.hostname.endswith('.ac.kr'):
            continue
        if parsed.username or parsed.password or parsed.port not in (None,443):
            continue
        sites.setdefault(parsed.hostname,(row['SCHL_NM'],parsed._replace(scheme='https',query='',fragment='').geturl()))
    return sites


def institution_host(host):
    return '.'.join(host.split('.')[-3:]) if host and host.endswith('.ac.kr') else None


def public_school_url(url, school):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and not parsed.username and not parsed.password
            and parsed.port in (None, 443) and institution_host(parsed.hostname) == school
            and not ACCESS_PATH.search(parsed.path))


def request_page(ledger, source_url, school):
    url = source_url
    seen = set()
    for _ in range(4):
        if url in seen or not public_school_url(url, school):
            raise Held('University redirect loops or leaves public school URLs')
        seen.add(url)
        raw, meta = ledger.request(url, redirect_metadata=True)
        if meta['status'] == 200:
            return raw, meta, url
        location = meta['headers'].get('location')
        if not location:
            raise Held('University redirect has no location')
        url = urljoin(url,location)
    raise Held('University redirect limit reached')


def one_site(ledger, school):
    name, homepage = school
    institution = institution_host(urlsplit(homepage).hostname)
    output = {'university':name,'homepage':homepage,'boards':[],'admitted':[]}
    try:
        raw, origin, homepage = request_page(ledger,homepage,institution)
    except (ValueError, OSError, requests.RequestException) as fail:
        return {**output,'status':'held','reason':str(fail)[:180]}
    links = {}
    for a in soup(raw).select('a[href]'):
        label = compact(a.get_text(' ',strip=True))
        url = urljoin(homepage,a['href'])
        uri = urlsplit(url)
        if (BOARD_NAME.search(label) and public_school_url(url,institution)
                and uri.path not in ('','/') and not DETAIL_PATH.fullmatch(uri.path)
                and not any(param+'=' in uri.query for param in QUERY_IDS)):
            links.setdefault(uri._replace(fragment='').geturl(),label)
    for url,label in sorted(links.items(),key=lambda a:('공지' not in a[1],len(a[1])))[:3]:
        candidate = {'url':url,'label':label}
        try:
            html, capture, url = request_page(ledger,url,institution)
            host = urlsplit(url).hostname
            anchors = [a for a in soup(html).select('a[href]')
                       if urlsplit(urljoin(url,a['href'])).netloc==host]
            kinds = {}
            for a in anchors:
                if not TOPIC.search(compact(a.get_text(' ',strip=True))):
                    continue
                link = urlsplit(urljoin(url,a['href']))
                if match := DETAIL_PATH.fullmatch(link.path):
                    kinds.setdefault('path',set()).add(match[1])
                for param in QUERY_IDS:
                    if re.search(fr'(?:^|&){param}=\d+(?:&|$)',link.query):
                        kinds.setdefault('query',set()).add((param,link.path))
            candidate.update(status='fetched', homepage_capture=origin['sha256'],
                             board_capture=capture['sha256'])
            if len(kinds.get('path',()))==1:
                folder = next(iter(kinds['path']))
                rule = {'detail_path':re.escape(folder)+r'([0-9]+)/artclView\.do',
                        'content':'.view-con,.board-view'}
            elif len(kinds.get('query',()))==1:
                param, detail_path = next(iter(kinds['query']))
                rule = {'identity':param, 'query_detail_path':detail_path,
                        'content':'.fr-view,.board-view,.board_view,.b-content-box'}
            else:
                rule = None
            if rule:
                sid='university-'+sha256(url.encode()).hexdigest()[:16]
                spec={'id':sid,'kind':'board','issuer':name,'url':url,**rule}
                count = len({native for native,_ in board_items(html,url,spec)})
                candidate['links_found'] = count
                if count >= 2:
                    collect(ledger,spec)
                    output['admitted'].append(sid)
                    candidate['source_config'] = spec
        except (ValueError, OSError, requests.RequestException) as fail:
            candidate.update(status='held',reason=str(fail)[:180])
        output['boards'].append(candidate)
    output['status']='scanned'
    return output


def directory(ledger):
    base='https://www.data.go.kr'
    schema,_=ledger.request(base+'/download/columList.json?pk=15107736&ext=CSV')
    h=json.loads(schema)
    args={'publicDataPk':'15107736','colNmList':h['tableVO']['colNmList'],
          'totalCount':h['totalCount'],'svcTableNm':h['tableVO']['svcTableNm'],
          'perPage':10000,'page':1}
    raw,_=ledger.request(base+'/download/standard.json?'+urlencode(args,doseq=True))
    rows=json.loads(raw)
    if len(rows)!=h['totalCount']:
        raise ValueError('Directory response record count differs from published total')
    return rows


def run():
    now=datetime.now(KST)
    root=ROOT/'university-discovery'
    root.mkdir(parents=True,exist_ok=True)
    with single_writer(root):
        day=now.date().isoformat()
        ledger=Evidence(root,day)
        sites=initial_sites(directory(ledger))
        configured = {institution_host(urlsplit(s['url']).hostname)
                      for s in json.loads(CONFIG.read_text())['sources']}
        targets = {host:school for host,school in sites.items() if institution_host(host) not in configured}
        completed={}
        out=root/'status.json'
        with ThreadPoolExecutor(max_workers=12) as pool:
            pending={pool.submit(one_site,ledger,school):host for host,school in targets.items()}
            for f in as_completed(pending):
                host=pending[f]
                try:
                    completed[host]=f.result()
                except (ValueError, OSError, requests.RequestException) as err:
                    completed[host]={'status':'held','reason':str(err)[:180]}
                if len(completed)%10==0 or len(completed)==len(targets):
                    atomic_json(out,{'at':datetime.now(KST).isoformat(),'directory_hosts':len(sites),
                        'hosts_to_scan':len(targets),'sites_processed':len(completed),'admitted_boards':sum(len(x.get('admitted',())) for x in completed.values()),
                        'sites':completed})


if __name__=='__main__':
    run()
