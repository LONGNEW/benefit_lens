"""Enumerate the public K-Startup student catalogue without an LLM or API key.

These are source-bound review candidates, never an automatic verified count.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urljoin, urlsplit

import requests

from benefitlens_source_storage import store_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json, single_writer
from run_benefitlens_korea import Evidence, Held, compact, korean_dates, soup

ROOT = CACHE / 'catalogue-expansion-v1'
BASE = 'https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do'
VIEW = re.compile(r'javascript:go_view\((\d+)\);?')
PAGE = re.compile(r'fn_egov_link_page\((\d+)\); return false;')
WINDOW = re.compile(r"javascript:fn_open_window\('([^']+)'\);?")
MONEY = re.compile(r'활동비|장려금|상금|시상금|사업화\s*자금|지원금|보조금|'
                   r'급여|수당|제작비|재료비|바우처|임대료|임차료|무상|무료|전액\s*지원')


def public_url(url):
    p = urlsplit(url)
    return (p.scheme == 'https' and p.hostname and not p.username and not p.password
            and p.port in (None, 443))


def parse_listing(raw):
    doc = soup(raw)
    # The highlighted carousel is repeated on every page and ignores filters.
    rows = {}
    for a in doc.select('.middle a[href]'):
        match = VIEW.fullmatch(a['href'])
        title = a.select_one('.tit')
        if match and title:
            native = match[1]
            rows[native] = {'title': compact(title.get_text(' ', strip=True)),
                            'url': BASE + '?' + urlencode({'schM': 'view', 'pbancSn': native})}
    pages = {int(m[1]) for a in doc.select('.paginate a[onclick]')
             if (m := PAGE.fullmatch(a['onclick']))}
    selected = doc.select_one('.paginate .active')
    current = int(selected.get_text()) if selected and selected.get_text().isdigit() else None
    return rows, pages, current


def parse_detail(raw, url, now):
    doc = soup(raw)
    body = doc.select_one('.app_notice_details-wrap')
    if body is None or body.select_one('#scrTitle') is None:
        raise Held('K-Startup notice body or title absent')
    for node in body.select('script,style,noscript'):
        node.decompose()
    fields = {}
    for node in body.select('.table_inner'):
        key, value = node.select_one(':scope > .tit'), node.select_one(':scope > .txt')
        if key is not None and value is not None:
            fields[compact(key.get_text())] = compact(value.get_text(' ', strip=True))
    sections = {compact(n.select_one(':scope > .title').get_text()):
                compact(n.get_text(' ', strip=True))
                for n in body.select('.information_list') if n.select_one(':scope > .title')}
    links = {}
    for a in body.select('a[href]'):
        address = a['href']
        if m := WINDOW.fullmatch(address):
            address = m[1]
        address = urljoin(url, address)
        if public_url(address):
            links[address] = compact(a.get_text(' ', strip=True))
    applications = [u for u, label in links.items() if re.search('접수|신청|지원|홈페이지', label)]
    route_text = fields.get('신청방법', '')
    application_emails = sorted(set(re.findall(r'[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', route_text)))
    attachments = [u for u in links if urlsplit(u).path.startswith('/afile/fileDownload/')]
    text = '\n'.join(compact(line) for line in body.stripped_strings)
    # Eligibility is taken from the notice, not from the search filter.
    target = fields.get('대상', '')
    eligibility = fields.get('신청대상', '')
    exclusion = fields.get('제외대상', '')
    student = '대학생' in target or bool(re.search(r'대학생|대학원생|학부생|재학생', eligibility))
    individual = bool(re.search(r'예비\s*창업자|일반인|개인|누구나', eligibility))
    period = fields.get('신청기간', '') or fields.get('접수기간', '')
    dates = sorted(korean_dates(period))
    state = 'awaiting_review'
    if len(dates) != 2:
        state = 'application_period_unresolved'
    elif not dates[0] <= now.date() <= dates[-1]:
        state = 'not_currently_open'
    if state == 'awaiting_review' and dates[0] == now.date():
        opening = re.search(r'(\d{1,2}):(\d{2})', period.split('~')[0])
        if opening and (now.hour, now.minute) < tuple(map(int, opening.groups())):
            state = 'not_currently_open'
    if state == 'awaiting_review' and dates[-1] == now.date():
        # A same-day deadline with no parsed time must not become current-open.
        closing = re.search(r'~.*?(\d{1,2}):(\d{2})', period)
        if not closing:
            state = 'closing_time_unresolved'
        elif (now.hour, now.minute) >= tuple(map(int, closing.groups())):
            state = 'not_currently_open'
    if state == 'awaiting_review' and not (student or individual):
        state = 'individual_eligibility_unresolved'
    if state == 'awaiting_review' and re.search(r'(대학생|재학생).{0,15}(제외|불가)', exclusion):
        state = 'student_excluded'
    benefit = sections.get('지원내용', '')
    payment = [m.group() for m in MONEY.finditer(benefit)]
    if state == 'awaiting_review' and not (applications or application_emails):
        state = 'application_route_unresolved'
    if state == 'awaiting_review' and not payment:
        state = 'financial_benefit_unresolved'
    return {'title': compact(body.select_one('#scrTitle').get_text(' ', strip=True)),
            'url': url, 'issuer': fields.get('주관기관명'), 'fields': fields,
            'sections': sections, 'application_urls': applications,
            'application_emails': application_emails,
            'attachment_urls': attachments, 'state': state,
            'student_explicit': student, 'individual_conditional': individual,
            'period_quote': period, 'eligibility_quote': eligibility,
            'benefit_quote': benefit, 'payment_terms': sorted(set(payment)),
            'text_content': text,
            'review_required': ['student eligibility and exclusions',
                                'cash versus restricted costs or services',
                                'original attachments', 'cross-source deduplication']}


def run(max_pages=30, max_details=400, root=ROOT):
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KST)
    with single_writer(root):
        ledger = Evidence(root, now.date().isoformat())
        status = {'at': now.isoformat(), 'state': 'running', 'source': 'kstartup-student',
                  'pages': 0, 'candidates': 0, 'detailed': 0, 'verified_added': 0,
                  'listing_complete': False, 'warnings': []}
        out = root / now.date().isoformat()
        out.mkdir(exist_ok=True)
        rows, visited = {}, set()
        try:
            for page in range(1, max_pages + 1):
                query = {'pbancClssCd': 'PBC010', 'pbancEndYn': 'N',
                         'aplyTrgtCdList': 'POST_TARGET_TYPE_02', 'page': page}
                raw, meta = ledger.request(BASE + '?' + urlencode(query))
                batch, pages, current = parse_listing(raw)
                signature = tuple(sorted(batch))
                if current != page or signature in visited:
                    raise Held('Catalogue page repeats or publisher did not apply pagination')
                visited.add(signature)
                status['pages'] += 1
                for native, row in batch.items():
                    rows.setdefault(native, {**row, 'listing': meta})
                status['candidates'] = len(rows)
                atomic_json(root / 'status.json', status)
                if not batch or not any(p > page for p in pages):
                    status['listing_complete'] = True
                    break
            if not status['listing_complete']:
                status['warnings'].append('configured listing page budget reached')
            for native, row in list(rows.items())[:max_details]:
                try:
                    raw, meta = ledger.request(row['url'])
                    record = parse_detail(raw, row['url'], datetime.now(KST))
                    text = record.pop('text_content')
                    record.update(native=native, listing=row['listing'], detail=meta,
                                  text=store_source_bytes(text.encode(), root / 'blobs', suffix='.txt'))
                    status['detailed'] += 1
                except (Held, ValueError, requests.RequestException, OSError) as err:
                    record = {**row, 'native': native, 'state': 'held', 'reason': str(err)[:300]}
                ledger.save('kstartup-student', native, record)
                status['at'] = datetime.now(KST).isoformat()
                atomic_json(root / 'status.json', status)
            if len(rows) > max_details:
                status['warnings'].append('configured detail budget reached')
        except (Held, ValueError, requests.RequestException, OSError) as err:
            status['warnings'].append(str(err)[:300])
        with ledger.db() as db:
            records = [json.loads(x) for x, in db.execute(
                'SELECT data FROM candidates WHERE day=? AND source=?',
                (ledger.day, 'kstartup-student'))]
        status.update(at=datetime.now(KST).isoformat(),
                      state='partial_or_held' if status['warnings'] else 'complete',
                      dispositions=dict(Counter(x['state'] for x in records)))
        atomic_json(out / 'review_candidates.json', records)
        atomic_json(root / 'status.json', status)
        return status


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-pages', type=int, default=30)
    parser.add_argument('--max-details', type=int, default=400)
    args = parser.parse_args()
    if not 1 <= args.max_pages <= 200 or not 1 <= args.max_details <= 3000:
        parser.error('page and detail budgets are outside the supported bounds')
    print(json.dumps(run(args.max_pages, args.max_details), ensure_ascii=False))
