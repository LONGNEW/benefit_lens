"""Discover student funding boards through public university navigation, then collect.

The official university directory supplies institutions, not opportunities. Only
published links are followed; unknown CMS layouts remain held for adapter work.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urljoin, urlsplit

import requests

from discover_benefitlens_korea_universities import (
    DETAIL_PATH, directory, initial_sites, institution_host, public_school_url, request_page)
from run_benefitlens_campaign import KST, atomic_json, single_writer
from run_benefitlens_korea import (ROOT, Evidence, Held, TOPIC, board_items, collect,
                                   compact, normalized_link, snapshot, soup)

DISCOVERY = ROOT / 'university-discovery-v2'
BOARD = re.compile(r'공지|장학|공모전|경진대회|서포터즈|현장실습|인턴|일경험|연구\s*참여|대외활동')
HUB = re.compile(r'대학\s*생활|학생\s*지원|커뮤니티|대학\s*소식|소통|알림|열린\s*광장')
IDS = ('articleNo', 'article_no', 'bbsidx', 'board_seq', 'boardId', 'boardSeq',
       'nttId', 'nttSn', 'bbsSeq', 'wr_id')
CONTENT = ('.fr-view,.board-view,.board_view,.b-content-box,.view-con,'
           '.board-view-content,.bbsV_cont,.bbs_view_con,.view_cont,#bbs_cn,'
           '.bbs_contents,.board_content,.board-content,.boardview,.tb_contents,'
           '#bo_v_con,.board-view-contents,.prog_content')


class NavigationEvidence(Evidence):
    def request(self, url, *, data=None, payload=None, redirect_metadata=False):
        institution = institution_host(urlsplit(url).hostname)
        if institution and data is None and payload is None and not redirect_metadata:
            raw, meta, _ = request_page(self, url, institution)
            return raw, meta
        return super().request(url, data=data, payload=payload, redirect_metadata=redirect_metadata)


def public_redirect(raw, base, institution):
    """Read literal public landing-page redirects; never execute scripts."""
    doc = soup(raw)
    targets = []
    for meta in doc.select('meta[http-equiv]'):
        if meta.get('http-equiv', '').lower() == 'refresh':
            match = re.search(r'(?:^|;)\s*url\s*=\s*[\'"]?([^\'"<>]+)',
                              meta.get('content', ''), re.I)
            if match:
                targets.append(match[1].strip())
    if len(doc.select('a[href]')) < 3:
        for node in doc.select('script:not([src])'):
            targets += re.findall(r'(?:window\.|document\.)?location(?:\.href)?\s*=\s*'
                                  r'[\'"]([^\'"<>\s]+)[\'"]', node.get_text())
            targets += re.findall(r'(?:window\.|document\.)?location\.replace\('
                                  r'[\'"]([^\'"<>\s]+)[\'"]\)', node.get_text())
    urls = {urljoin(base, target) for target in targets}
    if len(urls) == 1:
        url = next(iter(urls))
        if public_school_url(url, institution) and url != base:
            return url
    return None


def navigation(raw, base, institution):
    links = {}
    for a in soup(raw).select('a[href]'):
        label = compact(a.get_text(' ', strip=True))
        href = a['href']
        if not href or href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
            continue
        url = normalized_link(base, href)
        u = urlsplit(url)
        if not public_school_url(url, institution) or u.path in ('', '/'):
            continue
        # Do not follow posts as navigation or synthesize unpublished IDs.
        if DETAIL_PATH.fullmatch(u.path) or any(k in parse_qs(u.query)
                for k in ('articleNo', 'article_no', 'nttId', 'nttSn', 'wr_id')):
            continue
        if BOARD.search(label) or HUB.search(label):
            priority = 0 if BOARD.search(label) else 1
            links.setdefault(u._replace(fragment='').geturl(), (priority, label))
    return sorted(((url, label, rank) for url, (rank, label) in links.items()),
                  key=lambda x: (x[2], bool(re.search('입학|대학원입학', x[1])),
                                 not bool(re.search('공지|공모|대외활동', x[1])), len(x[1]), x[0]))


def infer_board(raw, url, issuer):
    kinds, identities = {}, {}
    host = urlsplit(url).hostname
    for a in soup(raw).select('a[href]'):
        if not TOPIC.search(compact(a.get_text(' ', strip=True))):
            continue
        address = normalized_link(url, a['href'])
        link = urlsplit(address)
        if link.scheme != 'https' or link.hostname != host:
            continue
        if match := DETAIL_PATH.fullmatch(link.path):
            kinds.setdefault('path', set()).add(match[1])
        args = parse_qs(link.query)
        for param in IDS:
            if len(args.get(param, [])) == 1 and args[param][0].isdigit():
                identities.setdefault((param, link.path), set()).add(args[param][0])
    if len(kinds.get('path', ())) == 1:
        rule = {'detail_path': re.escape(next(iter(kinds['path']))) + r'([0-9]+)/artclView\.do'}
    elif identities:
        largest = max(map(len, identities.values()))
        winners = [key for key, values in identities.items() if len(values) == largest]
        if len(winners) != 1:
            return None
        param, path = winners[0]
        rule = {'identity': param, 'query_detail_path': path}
    else:
        return None
    spec = {'id': 'university-v2-' + sha256(url.encode()).hexdigest()[:16],
            'kind': 'board', 'issuer': issuer, 'url': url, 'content': CONTENT, **rule}
    return spec if list(board_items(raw, url, spec)) else None


def one_school(ledger, school, max_pages=6, max_boards=4):
    name, homepage = school
    institution = institution_host(urlsplit(homepage).hostname)
    out = {'university': name, 'homepage': homepage, 'pages': [], 'boards': [], 'admitted': []}
    queue = [(homepage, 'homepage', 0)]
    visited = set()
    while queue and len(visited) < max_pages and len(out['admitted']) < max_boards:
        address, label, depth = queue.pop(0)
        if address in visited:
            continue
        visited.add(address)
        entry = {'url': address, 'label': label, 'depth': depth}
        try:
            raw, capture, final = request_page(ledger, address, institution)
            visited.add(final)
            for _ in range(3):
                redirect = public_redirect(raw, final, institution)
                if not redirect or redirect in visited:
                    break
                entry.setdefault('public_landing_redirects', []).append(redirect)
                visited.add(redirect)
                raw, capture, final = request_page(ledger, redirect, institution)
            entry.update(status='fetched', capture=capture['sha256'], final_url=final)
            spec = infer_board(raw, final, name)
            if spec and spec['id'] not in out['admitted']:
                collect(ledger, spec)
                out['admitted'].append(spec['id'])
                out['boards'].append({'url': final, 'label': label, 'source_config': spec})
            if depth < 2:
                discovered = [(url, title, depth + 1) for url, title, rank in
                              navigation(raw, final, institution) if url not in visited]
                # Visit boards before intermediate navigation pages.
                queue = queue + discovered
                unique = {}
                for row in queue:
                    unique.setdefault(row[0], row)
                queue = list(unique.values())
        except (Held, ValueError, OSError, requests.RequestException) as err:
            entry.update(status='held', reason=str(err)[:220])
        out['pages'].append(entry)
    out.update(state='complete_within_budget', pages_budget=max_pages,
               pending_navigation=len(queue), more_navigation_available=bool(queue))
    return out


def run():
    DISCOVERY.mkdir(parents=True, exist_ok=True)
    day = datetime.now(KST).date().isoformat()
    with single_writer(DISCOVERY):
        ledger = NavigationEvidence(DISCOVERY, day)
        targets = initial_sites(directory(ledger))
        status = {'at': datetime.now(KST).isoformat(), 'day_kst': day, 'state': 'running',
                  'directory_hosts': len(targets), 'sites_processed': 0,
                  'admitted_boards': 0, 'verified_added': 0, 'sites': {}}
        atomic_json(DISCOVERY / 'status.json', status)
        with ThreadPoolExecutor(max_workers=10) as pool:
            tasks = {pool.submit(one_school, ledger, school): host for host, school in targets.items()}
            for future in as_completed(tasks):
                host = tasks[future]
                try:
                    status['sites'][host] = future.result()
                except (ValueError, OSError, requests.RequestException) as err:
                    status['sites'][host] = {'state': 'held', 'reason': str(err)[:220]}
                status.update(at=datetime.now(KST).isoformat(),
                              sites_processed=len(status['sites']),
                              admitted_boards=sum(len(s.get('admitted', []))
                                                  for s in status['sites'].values()))
                if len(status['sites']) % 5 == 0 or len(status['sites']) == len(targets):
                    atomic_json(DISCOVERY / 'status.json', status)
        ids = {sid for site in status['sites'].values() for sid in site.get('admitted', [])}
        details = snapshot(ledger, ids)
        # snapshot() writes status.json; keep directory progress and collection summary together.
        status.update(state='complete_within_budget', sources=details['sources'],
                      source_config_count=len(ids))
        atomic_json(DISCOVERY / 'status.json', status)
        (DISCOVERY / day).mkdir(exist_ok=True)
        atomic_json(DISCOVERY / day / 'sources.json', {'sources': [b['source_config']
            for s in status['sites'].values() for b in s.get('boards', [])]})
        print(json.dumps({k: v for k, v in status.items() if k not in ('sites', 'sources')},
                         ensure_ascii=False))


def refresh_discovered():
    """Reprocess captured boards after a parser correction without discovery search."""
    with single_writer(DISCOVERY):
        status = json.loads((DISCOVERY / 'status.json').read_text())
        day = datetime.now(KST).date().isoformat()
        ledger = NavigationEvidence(DISCOVERY, day)
        specs = {}
        for site in status['sites'].values():
            for board in site.get('boards', []):
                spec = board['source_config']
                spec['content'] = CONTENT
                specs[spec['id']] = spec
        status.update(state='refreshing_details', at=datetime.now(KST).isoformat())
        atomic_json(DISCOVERY / 'status.json', status)
        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(lambda spec: collect(ledger, spec), specs.values()))
        details = snapshot(ledger, set(specs))
        status.update(state='complete_within_budget', sources=details['sources'],
                      at=datetime.now(KST).isoformat(), detail_refresh_day=day)
        atomic_json(DISCOVERY / 'status.json', status)
        print(json.dumps({k: v for k, v in status.items() if k not in ('sites', 'sources')},
                         ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-discovered', action='store_true')
    args = parser.parse_args()
    refresh_discovered() if args.refresh_discovered else run()
