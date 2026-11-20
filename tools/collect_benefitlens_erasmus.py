"""Follow the EACEA programme listings to public intake and funding pages."""
from argparse import ArgumentParser
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import ipaddress
import json
import re
import subprocess
from urllib.parse import urljoin, urlsplit

import requests

from benefitlens_source_storage import store_source_bytes
from collect_benefitlens_crossborder import project_original
from run_benefitlens_campaign import CACHE, KST, atomic_json, single_writer
from run_benefitlens_korea import Evidence, Held, compact, soup

CATALOGUE = 'https://www.eacea.ec.europa.eu/scholarships/erasmus-mundus-catalogue_en'
ROOT = CACHE / 'erasmus-campaign-v1'
NAVIGATION = re.compile(r'how to apply|call for applications?|application|admissions?|apply|scholarships?|financial support|funding|eligibility|candidatur|bewerb|becas|inscrip', re.I)
SOCIAL = {'facebook.com','instagram.com','twitter.com','linkedin.com','youtube.com','youtu.be','x.com'}
REDIRECTS = {301,302,303,307,308}


def site(address):
    name = urlsplit(address).hostname
    return name.removeprefix('www.') if name else ''


def public_url(address):
    try:
        u = urlsplit(address)
        if u.scheme != 'https' or u.username or u.password or u.port not in (None, 443) or not u.hostname:
            return False
    except ValueError:
        return False
    try:
        ipaddress.ip_address(u.hostname)
    except ValueError:
        return '.' in u.hostname and not u.hostname.endswith(('.local','.internal','.localhost','.test'))
    return False


def https_equivalent(address):
    parsed = urlsplit(address)
    if parsed.scheme == 'http' and parsed.port in (None,80):
        return parsed._replace(scheme='https',netloc=parsed.hostname or '').geturl()
    return address


def capture(ledger, address):
    address = https_equivalent(address)
    traversed = set()
    for _ in range(6):
        if not public_url(address) or address in traversed:
            raise Held('URL or redirect is outside the public HTTPS scope')
        traversed.add(address)
        raw, original = ledger.request(address, redirect_metadata=True)
        if original['status'] not in REDIRECTS:
            doc, content = project_original(raw,ledger.root)
            if len(content) < 80:
                raise Held('Publisher response has no readable programme material')
            return address, original, doc, content
        location = original['headers'].get('location')
        if not location:
            raise Held('Publisher redirect missing target')
        address = urljoin(address,location)
    raise Held('Publisher redirect limit reached')


def catalogue(ledger):
    rows = []
    page = CATALOGUE
    visited = set()
    while page and page not in visited:
        if len(visited) >= 30:
            raise Held('Catalogue pagination bound reached')
        visited.add(page)
        raw, meta = ledger.request(page)
        doc = soup(raw)
        cards = doc.select('article.ecl-card')
        if not cards:
            raise Held('EACEA catalogue source structure changed')
        for card in cards:
            link = card.select_one('.ecl-content-block__title a[href]')
            if link:
                url = urljoin(page,link['href'])
                rows.append({'index':len(rows)+1,'title':compact(link.get_text(' ',strip=True)),
                             'seed_url':url,'catalogue_page':page,'catalogue_sha256':meta['sha256']})
        a = doc.select_one('.ecl-pagination__item--next a[href]')
        page = urljoin(page,a['href']) if a else None
    return rows


def next_pages(doc, current):
    if doc is None:
        return []
    options = {}
    for anchor in doc.select('a[href]'):
        label = compact(anchor.get_text(' ',strip=True))
        try:
            address = urljoin(current, anchor['href'])
            u = urlsplit(address)
        except ValueError:
            continue
        address = u._replace(fragment='').geturl()
        if (not public_url(address) or not label or not NAVIGATION.search(label)
                or address == current or site(address) in SOCIAL or
                u.path.lower().endswith(('.jpg','.png','.zip','.docx'))):
            continue
        if site(address) != site(current) and not re.search(r'apply|admissions?|scholarship|application',label,re.I):
            continue
        strong = 0 if re.search(r'^(how to apply|admissions?|applications?|scholarships?|apply now)',label,re.I) else 1
        options.setdefault(address,(strong + (site(address)!=site(current)),label))
    return [x for x,_ in sorted(options.items(),key=lambda kv:(kv[1][0],len(kv[1][1]),kv[0]))]


def programme(ledger, seed, limit, previous=None):
    previous = previous or {}
    discovered = list(previous.get('remaining_urls', [seed]))
    documents = list(previous.get('documents', []))
    held = list(previous.get('held', []))
    visited = {source['requested_url'] for source in documents} | {source['url'] for source in held}
    limit += len(visited)
    while discovered and len(visited) < limit:
        url = discovered.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            final, cap, doc, content = capture(ledger,url)
        except (ValueError,OSError,requests.RequestException,subprocess.SubprocessError) as err:
            held.append({'url':url,'reason':str(err)[:190]})
            continue
        if final != url:
            discovered = [address for address in discovered if address != final]
        lines = content.splitlines()
        flags = {name:sum(bool(pattern.search(line)) for line in lines)
                 for name,pattern in {
                    'cycle':re.compile(r'2027'),
                    'funding':re.compile(r'scholarship|stipend|grant|bourse|stipendium',re.I),
                    'application':re.compile(r'application|deadline|admission|apply',re.I),
                 }.items()}
        documents.append({'requested_url':url,'url':final,'original':cap,
                          'text':store_source_bytes(content.encode(),ledger.root/'blobs',suffix='.txt'),
                          'title':compact(doc.title.get_text(' ',strip=True)) if doc and doc.title else '',
                          'signals':flags})
        found = [new for new in next_pages(doc,final) if new not in visited and new not in discovered]
        discovered.extend(found[:limit])
    return {'seed_url':seed, 'documents':documents, 'held':held,
            'left_discovered':len(discovered), 'remaining_urls':discovered}


def run(root, limit, continue_frontier=False):
    if not root.resolve().is_relative_to(CACHE.resolve()):
        raise ValueError('Use the shared BenefitLens dataset cache')
    root.mkdir(parents=True,exist_ok=True)
    with single_writer(root):
        day = datetime.now(KST).date().isoformat()
        ledger = Evidence(root,day)
        directory = root/day
        directory.mkdir(exist_ok=True)
        rows = catalogue(ledger)
        atomic_json(directory/'catalogue.json',{'catalogue':CATALOGUE,'rows':rows})
        pending = {row['seed_url'] for row in rows}
        output = directory/'status.json'
        results = {}
        if continue_frontier and output.exists():
            results = {k: v for k, v in json.loads(output.read_text())['programmes'].items()
                       if v['seed_url'] in pending}
        queue = []
        for seed in sorted(pending):
            key = sha256(seed.encode()).hexdigest()[:16]
            previous = results.get(key)
            if not previous or previous.get('remaining_urls'):
                queue.append((key, seed, previous))

        def write():
            totals = Counter({k:sum(bool(x['documents']) for x in results.values()) if k=='readable_seeds' else
                                sum(len(x[k]) for x in results.values()) for k in ('readable_seeds','documents','held')})
            atomic_json(output, {'as_of':datetime.now(KST).isoformat(),'catalogue_rows':len(rows),
                     'unique_seed_urls':len(pending),'visited_seed_urls':len(results),
                     'pending_seed_urls':len(pending)-len(results),'totals':dict(totals),'programmes':results})
        write()
        with ThreadPoolExecutor(max_workers=24) as pool:
            futures = {pool.submit(programme, ledger, seed, limit, previous): key
                       for key, seed, previous in queue}
            for index, f in enumerate(as_completed(futures), 1):
                results[futures[f]] = f.result()
                if index % 5 == 0:
                    write()
        write()
        return output


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--pages-per-seed',type=int,default=8,choices=range(1,13))
    parser.add_argument('--continue-frontier', action='store_true')
    args = parser.parse_args()
    print(run(args.root, args.pages_per_seed, args.continue_frontier))
