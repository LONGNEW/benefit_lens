"""Discover observed official archive links and stage public report originals.

The resumable archive frontier is independent of the production experience queue.
Counts are documents, never people or semantically approved experiences.
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
import sqlite3
import subprocess
import os
import time
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit
import xml.etree.ElementTree as ET
import zipfile

from benefitlens_source_storage import read_source_bytes, store_source_bytes
from benefitlens_report_projection import project_report
from import_benefitlens_full_inventory import SharedEvidence, public_url
from run_benefitlens_campaign import CACHE, KST, atomic_json, checked_root, encoded, single_writer
from run_benefitlens_korea import Held, compact, soup
from scale_benefitlens_experiences import body_text

CONFIG = Path(__file__).resolve().parents[1] / 'pipeline/config/experience_university_archives_v2.json'
BLOCKED = ('helsinki.fi', 'bath.ac.uk', 'kcl.ac.uk', 'geeksforgeeks.org', 'linkareer.com')
TOPIC = re.compile(r'erfahrungsbericht|berichte|testimonial|experience|report|교환|보고서|후기|수기|체험|留学|体験', re.I)
ARCHIVE_TOPIC = re.compile(r'erfahrungsbericht|stories.of.exchange|exchange.{0,20}(?:report|experience)|'
                          r'(?:study|studying|abroad|internship).{0,20}(?:report|experience)|'
                          r'경험\s*보고서|파견\s*(?:보고서|후기)|교환.{0,15}(?:수기|후기|보고)|'
                          r'留学.{0,15}(?:体験|報告)|体験.{0,5}(?:記|談)', re.I)
INTERNATIONAL = re.compile(r'international|outgoing|erasmus|exchange|abroad|ausland|mobilit|'
                           r'국제|교류|교환|留学|海外|国際|internacional|échanges|internazional', re.I)
ATTACHMENT = re.compile(r'\.(?:pdf|docx|hwp|hwpx|zip)(?:$|[?\s])|/download(?:[/.?]|$)|mode=download', re.I)
EXCLUDE = re.compile(r'vorlage|template|muster|anleitung|checklist|양식|서식|매뉴얼|공지.*작성|작성.*안내', re.I)


def normalize(url):
    """Normalize URL identity without guessing unpublished IDs or routes."""
    if not public_url(url):
        return None
    p = urlsplit(url)
    if any(p.hostname == h or p.hostname.endswith('.' + h) for h in BLOCKED):
        return None
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.startswith('utm_') and k not in ('no_cache',)]
    if dict(query).get('mode') == 'view' and dict(query).get('articleNo'):
        query = [(k, v) for k, v in query if k not in ('article.offset', 'articleLimit', 'srSearchVal', 'srSearchKey')]
    return p._replace(path=quote(p.path, safe="/%:@!$&'()*+,;=-._~"),
                      query=urlencode(sorted(query)), fragment='').geturl()


def in_scope(url, source):
    value = normalize(url)
    if not value:
        return False
    host = urlsplit(value).hostname
    return host in source.get('allowed_hosts', []) or any(
        host == suffix or host.endswith('.' + suffix) for suffix in source.get('domain_suffixes', []))


def observed_links(raw, task, source):
    """Follow only the source's published anchors, preserving their parent receipt."""
    doc = soup(raw)
    node = (doc.select_one(source['content_selector']) if source.get('content_selector') else
            doc.select_one('main') or doc.select_one('article') or doc.select_one('#content') or
            doc.select_one('body')) or doc
    output = {}
    for anchor in node.select('a[href]'):
        url = normalize(urljoin(task['url'], anchor['href']))
        if not url or not in_scope(url, source) or url == normalize(task['url']):
            continue
        label = compact(anchor.get_text(' ', strip=True))
        topic = label + ' ' + url
        if re.search(source.get('exclude_link_pattern', r'(?!)'), topic, re.I):
            continue
        query = dict(parse_qsl(urlsplit(url).query))
        is_article = bool(re.search(source.get('article_pattern', r'(?!)'), url))
        is_attachment = bool(ATTACHMENT.search(topic))
        paging = ('next' in anchor.get('rel', []) or bool(re.search(
            r'(?:[?&])(?:page|paged|pageno|cp|article.offset|tx_[^=]+%5BcurrentPage%5D)=\d+', url, re.I)))
        if query.get('mode') in ('view', 'download'):
            paging = False
        if is_attachment:
            include = source.get('attachment_include_pattern')
            if (include and not re.search(include, topic, re.I)) or EXCLUDE.search(topic):
                continue
            kind = 'attachment'
        elif is_article:
            kind = 'article'
        elif task['depth'] < source.get('max_depth', 3) and (
                paging or re.search(source.get('follow_link_pattern', TOPIC.pattern), topic, re.I)):
            kind = 'index'
        else:
            continue
        if paging and urlsplit(url).path != urlsplit(task['url']).path:
            continue
        row = {'url': url, 'kind': kind, 'source_id': source['id'],
               'depth': task['depth'] if paging else task['depth'] + 1,
               'label': label, 'parent': task['url'],
               'listing_original': task.get('original'), 'semantic_review': 'unreviewed'}
        if url not in output or kind == 'attachment':
            output[url] = row
    if source.get('k2web_read_adapter'):
        for row in k2web_links(doc, task, source):
            output[row['url']] = row
    return list(output.values())


def k2web_links(doc, task, source):
    """Interpret the verified publisher's read-only JS/form actions, never IDs."""
    basis = source['k2web_read_adapter']
    raw_script = read_source_bytes(basis['script_original']['path'], storage=basis['script_original'].get('storage'))
    if sha256(raw_script).hexdigest() != basis['script_original']['sha256']:
        raise Held('K2Web published route script hash mismatch')
    script = raw_script.decode('utf-8')
    if not re.search(r"kurl\(\s*'/bbs/'\s*\+\s*siteId", script) or 'bbsArtclSeq' not in script:
        raise Held('K2Web published article route implementation changed')
    inline = '\n'.join(s.get_text() for s in doc.select('script:not([src])'))
    extension = re.search(r'var\s+urlPattern\s*=\s*[\"\'](\.do)[\"\']', inline)
    if not extension:
        return []
    rows = []
    view = doc.select_one('form[name="viewForm"]')
    fields = {i['name']: i.get('value', '') for i in view.select('input[name][type="hidden"]')
              if i['name'] != 'password'} if view else {}
    for a in doc.select('.js-view-artcl[data-site-id][data-fnct-no][data-bbs-artcl-seq]'):
        site, number, identity = a['data-site-id'], a['data-fnct-no'], a['data-bbs-artcl-seq']
        if site != basis['site_id'] or number != basis['board_id'] or not identity.isdigit() or not view:
            continue
        url = normalize(urljoin(task['url'], '/bbs/' + site + '/' + number + '/' + identity + '/artclView' + extension[1]))
        if in_scope(url, source):
            rows.append({'url': url, 'kind': 'article', 'source_id': source['id'],
                'depth': task['depth'] + 1, 'label': compact(a.get_text(' ', strip=True)),
                'parent': task['url'], 'listing_original': task.get('original'),
                'request_form': fields, 'published_read_route_script': basis['script_original'],
                'semantic_review': 'unreviewed'})
    paging = doc.select_one('form[name="pageForm"][action]')
    if paging:
        action = normalize(urljoin(task['url'], paging['action']))
        expected = '/bbs/' + basis['site_id'] + '/' + basis['board_id'] + '/artclList.do'
        if in_scope(action, source) and urlsplit(action).path == expected and 'function page_link(page)' in script:
            fields = {i['name']: i.get('value', '') for i in paging.select('input[name][type="hidden"]')}
            for a in doc.select('a[href]'):
                match = re.fullmatch(r"javascript:page_link\(['\"]?(\d+)['\"]?\)", a['href'])
                if match and 1 <= int(match[1]) <= 10000:
                    params = dict(fields, page=match[1])
                    logical = normalize(action + '?page=' + match[1])
                    if logical != normalize(task['url']):
                        rows.append({'url': logical, 'request_url': action, 'request_form': params,
                            'kind': 'index', 'source_id': source['id'], 'depth': task['depth'],
                            'parent': task['url'], 'listing_original': task.get('original'),
                            'published_read_route_script': basis['script_original']})
    return rows


def prior_inventory(path):
    """Read a frozen URL/hash baseline without mutating the main queue."""
    urls, texts = set(), set()
    if not path.exists():
        return urls, texts
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=30) as con:
        for url, encoded in con.execute('SELECT url,result FROM experience_task'):
            value = normalize(url)
            if value:
                urls.add(value)
            try:
                doc = json.loads(encoded or '{}').get('document', {})
                if doc.get('text', {}).get('sha256'):
                    texts.add(doc['text']['sha256'])
            except (TypeError, ValueError):
                pass
    return urls, texts


def scoped_capture(ledger, url, source, data=None):
    seen = set()
    for _ in range(6):
        url = normalize(url)
        if not url or not in_scope(url, source) or url in seen:
            raise Held('Redirect outside registered official publisher scope or loop')
        if re.search(r'/(?:[^/]*login|signin|oauth|authorize)(?:[.;/]|$)', urlsplit(url).path, re.I):
            raise Held('Publisher redirected to an authentication route')
        seen.add(url)
        try:
            raw, meta = ledger.request(url, data=data, redirect_metadata=True)
        except Held:
            # The shared transport admits redirects for GET only. A K2Web
            # read-only POST may publish a 302 wrapper URL. Reuse that receipt
            # and follow the observed Location as GET, with normal scope/policy.
            if not data or not source.get('k2web_read_adapter') or not re.search(r'/artcl(?:View|List)\.do$', urlsplit(url).path):
                raise
            key = sha256(encoded(['POST', url, data, None]).encode()).hexdigest()
            with ledger.db() as con:
                row = con.execute('SELECT meta FROM captures WHERE day=? AND key=?', (ledger.day, key)).fetchone()
            meta = json.loads(row[0]) if row else {}
            if meta.get('status') not in (302, 303):
                raise
            raw = read_source_bytes(meta['path'], storage=meta.get('storage'))
            if sha256(raw).hexdigest() != meta['sha256']:
                raise Held('Read-only POST redirect receipt integrity failed')
        if meta['status'] in (301, 302, 303, 307, 308):
            url = urljoin(url, meta['headers'].get('location', ''))
            if meta['status'] in (301, 302, 303):
                data = None
            continue
        if re.search(rb'verify you are human|captcha required|access denied', raw[:25000], re.I):
            raise Held('Access challenge response')
        if not raw.startswith((b'%PDF', b'PK', b'\xd0\xcf\x11\xe0')):
            doc = soup(raw)
            title = compact(doc.title.get_text(' ', strip=True)) if doc.title else ''
            if re.search(r'로그인|login|sign in', title, re.I):
                raise Held('Publisher article requires authentication')
            message = doc.select_one('#_contentBuilder, .error-message, .error, .alert-danger')
            if message:
                text = compact(message.get_text(' ', strip=True))
                if len(text) < 2000 and re.search(r'접근\s*권한이\s*없습니다|열람\s*권한이\s*없|permission denied', text, re.I):
                    raise Held('Publisher explicitly denies article access')
        return raw, meta, url
    raise Held('Publisher redirect limit')


def extract_text(raw, row, source, root):
    if raw.startswith(b'%PDF'):
        return project_report(raw, root)
    if raw.startswith((b'PK', b'\xd0\xcf\x11\xe0')):
        return project_report(raw, root)
    if row['kind'] == 'attachment':
        raise Held('Expected an attached document; HTML response is not a report original')
    doc = soup(raw)
    node = doc.select_one(source.get('article_content_selector',
                                    '.b-content-box, .board-content, .view_cont, article, main, #content'))
    if node is None:
        raise Held('Article content selector missing; no page-shell text counted')
    text = body_text(node)
    if re.search(r'로그인.*(?:필요|하셔야)|로그인\s*후.*(?:이용|열람)|권한이\s*없|접근.*제한|비밀글|login required', text[:2000], re.I):
        raise Held('Publisher content requires authentication or permission')
    return 'html', text


def source_definition(source):
    return {k: source[k] for k in ('id', 'adapter', 'allowed_hosts', 'domain_suffixes',
                                  'official_publisher', 'institution', 'program_bindings') if k in source}


def stage(row, source, ledger, root, prior_texts):
    result = dict(row, source_definition=source_definition(source), approved_pattern=False,
                  accepted_experience=False, reuse_rights='not_inferred_from_public_access')
    try:
        raw, original, final = scoped_capture(ledger, row.get('request_url', row['url']), source, data=row.get('request_form'))
        result.update(original=original, final_url=final)
        kind, text = extract_text(raw, row, source, root)
        if len(text.strip()) < 250 or '\x00' in text:
            raise Held('Usable original text missing; OCR or semantic review required')
        ref = store_source_bytes(text.encode(), root/'blobs', suffix='.txt')
        result.update(state='body_staged', kind=kind, text=ref, text_characters=len(text),
                      text_duplicate_in_prior_queue=ref['sha256'] in prior_texts,
                      discovery={'archive_source_id': source['id'], 'parent': row['parent'],
                                 'listing_original': row.get('listing_original'), 'label': row.get('label')})
    except (ValueError, OSError, subprocess.SubprocessError, ET.ParseError, zipfile.BadZipFile) as error:
        result.update(state='held', reason=type(error).__name__ + ': ' + str(error)[:300])
    return result


def institution_candidates(registry, countries):
    """A community-maintained institution list supplies candidates, not authority."""
    groups = {country: [] for country in countries}
    seen = set()
    for row in registry['records']:
        country = row.get('alpha_two_code')
        if country not in groups:
            continue
        for published_url in row.get('web_pages', []):
            p = urlsplit(published_url)
            # An explicit, recorded HTTPS transport upgrade of the same listed
            # host/path is allowed. No alternate path or institution ID is guessed.
            candidate = normalize(p._replace(scheme='https').geturl()) if p.scheme in ('http', 'https') else None
            if not candidate:
                continue
            identity = (country, urlsplit(candidate).hostname.removeprefix('www.'))
            if identity in seen:
                continue
            seen.add(identity)
            groups[country].append({'institution': row['name'], 'country': country,
                'url': candidate, 'registry_url': published_url, 'domains': row.get('domains', []),
                'https_transport_upgrade': p.scheme == 'http',
                'registry_revision': registry['revision'], 'registry_original': registry['original'],
                'official_publisher': False})
            break  # Keep one listed homepage per institution in the first pass.
    rows = []
    for i in range(max((len(rows) for rows in groups.values()), default=0)):
        for country in countries:
            if i < len(groups[country]):
                rows.append(groups[country][i])
    return rows


def registry_archive_links(raw, base, source):
    doc = soup(raw)
    links = {}
    for a in doc.select('a[href]'):
        url = normalize(urljoin(base, a['href']))
        label = compact(a.get_text(' ', strip=True))
        if not url or not in_scope(url, source):
            continue
        topic = label + ' ' + urlsplit(url).path
        if EXCLUDE.search(topic) or re.search(r'/(?:login|signin|oauth|auth)(?:/|$)', urlsplit(url).path, re.I):
            continue
        strong = bool(ARCHIVE_TOPIC.search(topic))
        if strong or INTERNATIONAL.search(topic):
            links[url] = {'url': url, 'label': label, 'parent': base,
                          'archive_candidate': strong, 'priority': 0 if strong else 1}
    return sorted(links.values(), key=lambda x:(x['priority'], len(x['url'])))


def scan_institution(row, ledger, pages_per_institution):
    source = {'id': 'institution-candidate-' + sha256(row['url'].encode()).hexdigest()[:20],
              'adapter': 'hierarchical_reports', 'allowed_hosts': [urlsplit(row['url']).hostname],
              'domain_suffixes': row['domains'], 'official_publisher': False,
              'institution': row['institution'], 'max_depth': 3}
    output = dict(row, state='running', pages=[], archive_candidates=[], publisher_verified=False)
    queue = [{'url': row['url'], 'depth': 0, 'parent': row['registry_url']}]
    seen = set()
    for _ in range(pages_per_institution):
        task = next((r for r in queue if r['url'] not in seen), None)
        if task is None:
            break
        seen.add(task['url'])
        page = dict(task)
        try:
            raw, original, final = scoped_capture(ledger, task['url'], source)
            if raw.startswith(b'%PDF'):
                page.update(state='document_candidate', original=original)
                output['pages'].append(page)
                continue
            doc = soup(raw)
            identity = compact((doc.title.get_text(' ', strip=True) if doc.title else '') + ' ' +
                               ' '.join(n.get_text(' ', strip=True) for n in doc.select('h1')[:3]))
            page.update(state='indexed', original=original, final_url=final, identity_text=identity[:800])
            if task['depth'] == 0:
                words = [w for w in re.findall(r'[^\W\d_]{4,}', row['institution'].casefold())
                         if w not in ('university', 'universitaet', 'universite', 'college', 'institute')]
                matched_name = any(word in identity.casefold() for word in words)
                output['publisher_verified'] = bool(matched_name and re.search(
                    r'universit|univerz|hochschule|college|institute|대학|大学|école|school|academ', identity, re.I))
                output['publisher_verification'] = {
                    'status': 'listed_domain_and_institutional_self_identification' if output['publisher_verified']
                              else 'institutional_identity_needs_review',
                    'evidence': identity[:800], 'official_registry_verified': False}
            for link in registry_archive_links(raw, final, source):
                if link['archive_candidate']:
                    output['archive_candidates'].append(dict(link, listing_original=original,
                        institution=row['institution'], country=row['country'],
                        registry_revision=row['registry_revision'], publisher_verified=output['publisher_verified']))
                if task['depth'] < 2:
                    queue.append(dict(link, depth=task['depth'] + 1))
        except (ValueError, OSError) as error:
            page.update(state='held', reason=type(error).__name__ + ': ' + str(error)[:260])
        output['pages'].append(page)
    output['archive_candidates'] = list({x['url']: x for x in output['archive_candidates']}.values())
    output['state'] = 'scanned_bounded' if any(p['state'] == 'indexed' for p in output['pages']) else 'held'
    output['source_definition'] = source
    return output


def registry_run(args):
    root = checked_root(args.root)
    registry_path = args.registry_input or CACHE/'university-archives-20261119/institution-registry/registry.json'
    registry = json.loads(registry_path.read_text())
    with single_writer(root):
        candidates = institution_candidates(registry, args.countries.split(','))
        atomic_json(root/'institution-candidates.json', candidates)
        records = json.loads((root/'institution-scans.json').read_text()) if (root/'institution-scans.json').exists() else {}
        pending = [r for r in candidates if r['url'] not in records][:args.max_institutions]
        ledger = SharedEvidence(root, datetime.now(KST).date().isoformat(), args.max_requests)
        before = ledger.network_requests

        def save(state):
            archives, manifest_sources = {}, []
            for institution in records.values():
                source = dict(institution['source_definition'])
                source['seeds'] = []
                for row in institution['archive_candidates']:
                    archives[row['url']] = row
                    source['seeds'].append({'kind': 'detail' if ATTACHMENT.search(row['url'] + ' ' + row['label']) else 'index',
                                            'url': row['url'], 'depth': 0,
                                            'report_scope': True, 'discovery_context': row})
                if source['seeds']:
                    source['publisher_verification'] = institution.get('publisher_verification')
                    manifest_sources.append(source)
            atomic_json(root/'institution-scans.json', records)
            atomic_json(root/'archive-candidates.json', list(archives.values()))
            atomic_json(root/'discovered-sources.json', {'version': 'registry-observed-archives-v1',
                'sources': manifest_sources, 'official_publisher_auto_approval': False})
            status = {'at_kst': datetime.now(KST).isoformat(), 'state': state,
                'registry_revision': registry['revision'], 'registry_institutions': len(registry['records']),
                'country_candidates': dict(Counter(r['country'] for r in candidates)),
                'candidate_institutions': len(candidates), 'scheduled_this_run': len(pending),
                'scanned_institutions': len(records),
                'institution_states': dict(Counter(r['state'] for r in records.values())),
                'self_identified_institution_pages': sum(r['publisher_verified'] for r in records.values()),
                'observed_archive_candidate_urls': len(archives),
                'original_experience_bodies_added': 0,
                'page_states': dict(Counter(p['state'] for r in records.values() for p in r['pages'])),
                'network_requests_lifetime': ledger.network_requests,
                'network_requests_this_run': ledger.network_requests - before,
                'model_called': False, 'accepted_experiences_added': 0}
            atomic_json(root/'status.json', status)
            return status

        save('running')
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            jobs = [pool.submit(scan_institution, row, ledger, args.pages_per_institution) for row in pending]
            for i, job in enumerate(as_completed(jobs), 1):
                result = job.result()
                records[result['url']] = result
                if i % 5 == 0:
                    print(json.dumps(save('running'), ensure_ascii=False), flush=True)
        result = save('complete_bounded_run')
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result


def run(args):
    root = checked_root(args.root)
    config = json.loads(args.config.read_text())
    for path in args.additional_config:
        if path.exists():
            config['sources'].extend(json.loads(path.read_text())['sources'])
    chosen = set(args.sources.split(',')) if args.sources else None
    sources = {s['id']: s for s in config['sources'] if not chosen or s['id'] in chosen}
    if not sources:
        raise ValueError('No registered archive sources selected')
    with single_writer(root):
        prior_urls, prior_texts = prior_inventory(args.prior_frontier)
        atomic_json(root/'baseline.json', {'at_kst': datetime.now(KST).isoformat(),
                    'prior_frontier': str(args.prior_frontier), 'normalized_prior_urls': len(prior_urls),
                    'prior_text_hashes': len(prior_texts)})
        ledger = SharedEvidence(root, datetime.now(KST).date().isoformat(), args.max_requests)
        before = ledger.network_requests
        ledger.response_byte_limit = lambda _: 32 * 1024**2
        states = json.loads((root/'discovery.json').read_text()) if (root/'discovery.json').exists() else {}
        records = {r['url']: r for r in json.loads((root/'index.json').read_text())} if (root/'index.json').exists() else {}
        for sid, source in sources.items():
            for seed in source['seeds']:
                url = normalize(seed['url'])
                if seed['kind'] in ('detail', 'attachment', 'article'):
                    records.setdefault(url, dict(seed, url=url, kind='attachment', source_id=sid,
                        parent=seed.get('discovery_context', {}).get('parent', ''),
                        listing_original=seed.get('discovery_context', {}).get('listing_original'),
                        state='existing_url_duplicate' if url in prior_urls else 'pending'))
                else:
                    states.setdefault(sid + '\n' + url, dict(seed, url=url, depth=0, source_id=sid, state='pending'))
            if source.get('content_access') == 'authentication_required':
                for row in [*states.values(), *records.values()]:
                    if row['source_id'] == sid and row['state'] in ('pending', 'held'):
                        row.update(state='policy_held', reason='Publisher article requires authentication; public list is metadata only')
        # A newly registered article layout must recover bodies already visited
        # as indices. Original captures are reused by the evidence ledger.
        for row in list(states.values()):
            source = sources.get(row['source_id'])
            if source and re.search(source.get('article_pattern', r'(?!)'), row['url']):
                records.setdefault(row['url'], dict(row, kind='article',
                    parent=row.get('parent', source['seeds'][0]['url']),
                    state='existing_url_duplicate' if row['url'] in prior_urls else 'pending'))

        def save(state):
            atomic_json(root/'discovery.json', states)
            atomic_json(root/'index.json', sorted(records.values(), key=lambda r:(r['source_id'], r['url'])))
            by_source = {}
            for sid in sources:
                rows = [r for r in records.values() if r['source_id'] == sid]
                listing = [r for r in states.values() if r['source_id'] == sid]
                by_source[sid] = {'discovered_document_urls': len(rows),
                    'existing_url_duplicates': sum(r['state'] == 'existing_url_duplicate' for r in rows),
                    'body_staged': sum(r['state'] == 'body_staged' for r in rows),
                    'unique_staged_texts': len({r['text']['sha256'] for r in rows if r.get('text')}),
                    'prior_text_duplicates': sum(r.get('text_duplicate_in_prior_queue', False) for r in rows),
                    'document_states': dict(Counter(r['state'] for r in rows)),
                    'index_states': dict(Counter(r['state'] for r in listing)),
                    'complete_observed_index_frontier': all(r['state'] == 'indexed' for r in listing)}
            result = {'at_kst': datetime.now(KST).isoformat(), 'state': state, 'sources': by_source,
                      'network_requests_lifetime': ledger.network_requests,
                      'network_requests_this_run': ledger.network_requests - before,
                      'accepted_experiences_added': 0, 'approved_patterns_added': 0,
                      'model_called': False, 'index': str(root/'index.json')}
            atomic_json(root/'status.json', result)
            return result

        save('discovering')
        done = 0
        def discover(task):
            try:
                raw, original, final = scoped_capture(ledger, task.get('request_url', task['url']), sources[task['source_id']], data=task.get('request_form'))
                links = observed_links(raw, dict(task, original=original), sources[task['source_id']])
                return dict(state='indexed', original=original, final_url=final, observed_links=len(links)), links
            except (ValueError, OSError) as error:
                return dict(state='held', reason=type(error).__name__ + ': ' + str(error)[:300]), []
        while done < args.max_index_pages:
            counts = Counter(r['source_id'] for r in states.values() if r['state'] == 'indexed')
            pending = sorted((r for r in states.values() if r['source_id'] in sources and r['state'] == 'pending'),
                             key=lambda r:(r['depth'], counts[r['source_id']], r['source_id'], r['url']))
            if not pending:
                break
            batch, hosts = [], set()
            for row in pending:
                host = urlsplit(row['url']).hostname
                if host not in hosts:
                    batch.append(row)
                    hosts.add(host)
                if len(batch) >= min(args.workers, args.max_index_pages - done):
                    break
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                jobs = {pool.submit(discover, row): row for row in batch}
                for job in as_completed(jobs):
                    row = jobs[job]
                    result, links = job.result()
                    row.update(result)
                    done += 1
                    for child in links:
                        if child['kind'] == 'index':
                            states.setdefault(child['source_id'] + '\n' + child['url'], dict(child, state='pending'))
                        else:
                            records.setdefault(child['url'], dict(child, state='existing_url_duplicate'
                                               if child['url'] in prior_urls else 'pending'))
                            if child['kind'] == 'article':
                                states.setdefault(child['source_id'] + '\n' + child['url'], dict(child, state='pending'))
            print(json.dumps(save('discovering'), ensure_ascii=False), flush=True)
        save('inventory_complete' if not args.max_documents else 'collecting')
        if args.max_documents:
            counts = Counter()
            pending = []
            for row in records.values():
                sid = row['source_id']
                if sid in sources and row['state'] == 'pending' and (
                        not args.sample_per_source or counts[sid] < args.sample_per_source):
                    pending.append(row)
                    counts[sid] += 1
            groups = {}
            for row in pending:
                groups.setdefault(row['source_id'], []).append(row)
            pending = [rows[i] for i in range(max((len(rows) for rows in groups.values()), default=0))
                       for rows in groups.values() if i < len(rows)][:args.max_documents]
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                jobs = [pool.submit(stage, r, sources[r['source_id']], ledger, root, prior_texts) for r in pending]
                for i, job in enumerate(as_completed(jobs), 1):
                    row = job.result()
                    records[row['url']] = row
                    if i % 10 == 0:
                        print(json.dumps(save('collecting'), ensure_ascii=False), flush=True)
        result = save('complete_bounded_run')
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result


def continuous(args):
    root = checked_root(args.root)
    while True:
        atomic_json(root/'runtime.json', {'at_kst': datetime.now(KST).isoformat(), 'pid': os.getpid(),
            'mode': 'registry_scan' if args.registry_scan else 'archive_collection', 'state': 'running'})
        result = registry_run(args) if args.registry_scan else run(args)
        if result['network_requests_lifetime'] >= args.max_requests:
            atomic_json(root/'runtime.json', {'at_kst': datetime.now(KST).isoformat(), 'pid': os.getpid(),
                'state': 'local_lifetime_request_budget_reached', 'max_requests': args.max_requests})
            return
        pending = (result['candidate_institutions'] - result['scanned_institutions'] if args.registry_scan else
                   sum(s['document_states'].get('pending', 0) + s['index_states'].get('pending', 0)
                       for s in result['sources'].values()))
        delay = 2 if pending else args.idle_seconds
        atomic_json(root/'runtime.json', {'at_kst': datetime.now(KST).isoformat(), 'pid': os.getpid(),
            'state': 'waiting_next_batch' if pending else 'waiting_new_observed_sources',
            'remaining_candidates': pending, 'next_check_seconds': delay})
        time.sleep(delay)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--additional-config', type=Path, action='append', default=[])
    parser.add_argument('--root', type=Path, default=CACHE/'university-archives-20261119')
    parser.add_argument('--prior-frontier', type=Path, default=CACHE/'experience-scale-v1/exploration.sqlite')
    parser.add_argument('--sources')
    parser.add_argument('--max-index-pages', type=int, default=300)
    parser.add_argument('--max-documents', type=int, default=1000)
    parser.add_argument('--max-requests', type=int, default=5000)
    parser.add_argument('--sample-per-source', type=int, default=0)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--registry-scan', action='store_true')
    parser.add_argument('--registry-input', type=Path)
    parser.add_argument('--countries', default='KR,DE,JP,FR,ES,GB,US,CA,AU,IT')
    parser.add_argument('--max-institutions', type=int, default=150)
    parser.add_argument('--pages-per-institution', type=int, default=4)
    parser.add_argument('--continuous', action='store_true')
    parser.add_argument('--idle-seconds', type=int, default=300)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or min(args.max_index_pages, args.max_documents, args.sample_per_source) < 0:
        parser.error('Nonnegative budgets and 1..8 workers required')
    continuous(args) if args.continuous else registry_run(args) if args.registry_scan else run(args)
