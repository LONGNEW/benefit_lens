"""Collect public writing examples and prepare complete, case-bound model inputs.

No agent or model call. Discovery metadata, collected text, people, cases and
reported outcomes have separate counts. Public accessibility does not establish
that an example is the final application that was submitted.
"""
from __future__ import annotations

from collections import Counter
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from hashlib import sha256
from html import unescape
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import parse_qs, urljoin, urlsplit

import requests

from collect_benefitlens_crossborder import project_original
from benefitlens_source_storage import read_source_bytes, store_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json, single_writer
from run_benefitlens_korea import Held, compact, soup
from import_benefitlens_full_inventory import SharedEvidence
from benefitlens_experience_crawl import experience_content, discover_experience_links, literal_experience_passages, official_alumni_binding, attribute_case_passages

REPO = Path(__file__).resolve().parents[1]
ROOT = CACHE/'writing-corpus-expansion-v1'
CONFIG = REPO/'pipeline/config/writing_corpus_expansion.json'
FOCUS_CONFIG = REPO/'pipeline/config/experience_focus_sources.json'
REGISTRY = 'https://docs.google.com/spreadsheets/d/e/2PACX-1vQprlSKAyJpY-g6ETXQllc9j4ZECfJlRtoS8CUR8mNrN6bDE7KGl3DhGSNrwWm3JfOga_CbQ0hfNwKz/pubhtml/sheet?headers=false&gid=0'
WRITING_ROLES = {'application_statement', 'personal_statement', 'research_statement',
                 'application_bundle', 'experience_review', 'official_interview',
                 'reviewer_feedback', 'interview'}


def outcome(value):
    v = compact(value).casefold()
    if v in ('awarded', 'winner!', 'winner', 'selected'):
        return 'selected'
    if 'honorable mention' in v:
        return 'honorable_mention'
    if v in ('not selected', 'not_selected', 'rejected', 'unsuccessful', 'declined'):
        return 'not_selected'
    return 'unknown'


def unwrap_link(url):
    p = urlsplit(url)
    if p.hostname == 'www.google.com' and p.path == '/url':
        values = parse_qs(p.query).get('q', [])
        if len(values) == 1:
            return values[0]
    return url


def parse_registry(raw, url):
    cases, sources = [], []
    for index, tr in enumerate(soup(raw).select('tr')):
        cells = tr.select('td')
        values = [compact(x.get_text(' ', strip=True)) for x in cells]
        if len(values) < 9 or not values[0] or values[0] == 'Name' or not re.match(r'^20\d{2}', values[1]):
            continue
        name, cycle, area, pool, result = values[:5]
        cid = sha256(json.dumps(['nsf-grfp', name.casefold(), cycle]).encode()).hexdigest()[:20]
        cases.append({'case_id': cid, 'program_id': 'nsf-grfp', 'person_name': name,
                      'cycle': cycle, 'field': area, 'applicant_pool': pool,
                      'outcome': outcome(result), 'outcome_basis': 'public_author_registry',
                      'registry_url': url, 'registry_row': index,
                      'registry_quote': ' | '.join(values[:5]),
                      'count_as_collected_case': False})
        for col, role in [(5, 'research_statement'), (6, 'personal_statement'),
                          (7, 'reviewer_feedback'), (8, 'cv')]:
            for a in cells[col].select('a[href]'):
                link = unwrap_link(a['href'])
                if urlsplit(link).scheme == 'https':
                    sources.append({**cases[-1], 'url': link, 'role': role,
                                    'document_finality': 'public_example_unconfirmed_submission_copy'})
    return cases, sources


def index_cases(documents):
    cases = {}
    for doc in documents:
        if doc.get('status') != 'collected' or doc.get('role') not in WRITING_ROLES:
            continue
        for spec in doc.get('case_bindings', []):
            cid = spec['case_id']
            case = cases.setdefault(cid, {**spec, 'document_ids': [], 'document_roles': []})
            case['document_ids'].append(doc['document_id'])
            case['document_roles'].append(doc['role'])
    for case in cases.values():
        case['document_ids'] = sorted(set(case['document_ids']))
        case['document_roles'] = sorted(set(case['document_roles']))
    # An official grantee can publish more than one story about the same award.
    # Merge only a confirmed name and cycle within this official publication lane.
    merged, subjects = {}, {}
    for cid in sorted(cases):
        case = cases[cid]
        key = None
        if (case.get('case_scope') == 'post_award_experience'
                and case.get('outcome_basis') == 'official_program_grantee_story'
                and case.get('person_name') and case.get('cycle') and not case.get('cycle_conflicts')):
            key = (case['program_id'], ''.join(case['person_name'].casefold().split()), case['cycle'])
        canonical = subjects.get(key) if key else None
        if canonical:
            target = merged[canonical]
            target['document_ids'] = sorted(set(target['document_ids'] + case['document_ids']))
            target['document_roles'] = sorted(set(target['document_roles'] + case['document_roles']))
            target.setdefault('merged_case_ids', []).append(cid)
            target.setdefault('alternate_binding_evidence', []).append(case.get('binding_evidence'))
        else:
            merged[cid] = case
            if key:
                subjects[key] = cid
    return list(merged.values())


def source_case(spec):
    if spec.get('case_specs'):
        return [case for child in spec['case_specs']
                for case in source_case({**spec, 'case_specs': None, **child})]
    if not (spec.get('person_name') or spec.get('case_id')):
        return []
    cid = spec.get('case_id') or sha256(json.dumps([
        spec.get('program_id'), spec.get('person_name'), spec.get('cycle')]).encode()).hexdigest()[:20]
    return [{'case_id': cid, **{k:spec.get(k) for k in
        ('program_id', 'person_name', 'cycle', 'outcome', 'outcome_basis',
         'registry_url', 'registry_snapshot', 'registry_quote', 'binding_evidence', 'binding_status',
         'case_scope', 'case_text_span', 'author_handle', 'outcome_phase', 'attempt_history',
         'cycle_conflicts', 'cycle_basis', 'experience_profile', 'application_targets')}}]


def receipt_document(spec, receipt):
    """Reuse an archived original only after both complete byte hashes match."""
    if receipt.get('access_scope') != 'public' or receipt.get('capture_mode') != 'full_document':
        raise ValueError('Archive receipt is not a complete public document')
    if receipt['url'] != spec['url']:
        raise ValueError('Archive receipt URL mismatch')
    raw = {'path': receipt['raw_path'], 'sha256': receipt['raw_sha256']}
    text_ref = {'path': receipt['text_path'], 'sha256': receipt['text_sha256']}
    for ref in (raw, text_ref):
        if sha256(read_source_bytes(ref['path'])).hexdigest() != ref['sha256']:
            raise ValueError('Archive receipt source hash mismatch')
    text = read_source_bytes(text_ref['path']).decode('utf-8')
    if len(text.strip()) < 100:
        raise ValueError('Archived document has no readable complete text')
    for binding in source_case(spec):
        evidence = binding.get('binding_evidence') or {}
        if evidence.get('source_id') == receipt['source_id']:
            quote = evidence.get('exact_quote')
            if not quote or quote not in text:
                raise ValueError('Archive case evidence quote is absent from its source')
    return {'document_id': 'writing-' + raw['sha256'][:24], 'url': spec['url'],
            'final_url': receipt.get('request_url', spec['url']), 'role': spec['role'],
            'program_id': spec.get('program_id'), 'status': 'collected',
            'origin': 'archived_receipt_verified_bytes',
            'raw': {**raw, 'at': receipt['fetched_at'], 'status': 200}, 'text': text_ref,
            'archive_source_id': receipt['source_id'], 'case_bindings': source_case(spec),
            'document_finality': 'public_example_unconfirmed_submission_copy',
            'access_scope': 'public', 'reuse_license': 'not_inferred_from_public_access'}


def archived_documents(specs):
    docs = []
    for spec in specs:
        path = CACHE.parent / spec['receipt_cache_path']
        try:
            docs.append(receipt_document(spec, json.loads(path.read_text())))
        except (KeyError, OSError, ValueError) as error:
            docs.append({'url': spec['url'], 'status': 'held', 'origin': 'archive',
                         'role': spec['role'], 'reason': str(error)})
    return docs


def previous_documents():
    """A temporary source failure must not discard an already collected original."""
    path = ROOT/'status.json'
    if not path.exists():
        return []
    state = json.loads(path.read_text())
    if not state.get('corpus'):
        return []
    ref = state['corpus']
    raw = read_source_bytes(ref['path'], storage=ref.get('storage'))
    if sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('Previous writing corpus hash mismatch')
    docs = json.loads(raw)['source_documents']
    for doc in docs:
        for key in ('raw', 'text'):
            ref = doc[key]
            if sha256(read_source_bytes(ref['path'], storage=ref.get('storage'))).hexdigest() != ref['sha256']:
                raise ValueError('Previous writing source hash mismatch')
    return docs


def check_case_bindings(documents):
    """Attach curated claims only when their exact cited source survived capture."""
    text_by_id = {d['document_id']: read_source_bytes(d['text']['path'],
        storage=d['text'].get('storage')).decode() for d in documents}
    issues = []
    for doc in documents:
        accepted = []
        for binding in doc.get('case_bindings', []):
            evidence = binding.get('binding_evidence') or {}
            if evidence:
                target = evidence.get('document_id') or doc['document_id']
                text = text_by_id.get(target)
                quote = evidence.get('exact_quote')
                valid = text is not None and quote and quote in text
                if valid and evidence.get('text_sha256'):
                    valid = sha256(text.encode()).hexdigest() == evidence['text_sha256']
                if not valid:
                    issues.append({'document_id': doc['document_id'], 'case_id': binding['case_id'],
                                   'reason': 'Cited case-binding source or exact quote did not validate'})
                    continue
            accepted.append(binding)
        doc['case_bindings'] = accepted
    return issues


class Collector:
    def __init__(self, ledger):
        self.ledger = ledger
        self.drive_download_disallowed = False

    def capture(self, spec):
        requested, url = spec['url'], spec['url']
        sources, seen = [], set()
        try:
            if self.drive_download_disallowed and urlsplit(url).hostname == 'drive.google.com':
                raise Held('Published Drive download path disallowed by robots; link retained, document not collected')
            for _ in range(6):
                p = urlsplit(url)
                if p.scheme != 'https' or p.hostname == 'accounts.google.com' or re.search(r'/(?:login|signin|oauth)(?:/|\?|$)', p.path):
                    raise Held('Document redirect enters authentication or leaves HTTPS')
                if url in seen:
                    raise Held('Document redirect loop')
                seen.add(url)
                raw, meta = self.ledger.request(url, redirect_metadata=True)
                sources.append(meta)
                if meta['status'] in (301, 302, 303, 307, 308):
                    url = urljoin(url, meta['headers'].get('location', ''))
                    continue
                if p.hostname == 'drive.google.com' and '/file/d/' in p.path:
                    m = re.search(r'"(https://drive\.usercontent\.google\.com/uc\?[^"<>]+)"', raw.decode())
                    if not m:
                        raise Held('Public Drive viewer exposes no downloadable document URL')
                    target = json.loads('"' + m[1] + '"')
                    try:
                        raw, meta = self.ledger.request(target, redirect_metadata=True)
                    except Held as error:
                        if 'robots' in str(error):
                            self.drive_download_disallowed = True
                        raise
                    sources.append(meta)
                    if meta['status'] != 200 or not raw.startswith(b'%PDF-'):
                        raise Held('Published Drive download did not return a PDF')
                    url = target
                elif p.hostname == 'github.com' and '/blob/' in p.path:
                    link = soup(raw).select_one('a#raw-url,a[data-testid="raw-button"]')
                    published = re.search(r'"rawBlobUrl"\s*:\s*"([^"\n]+)"', raw.decode('utf-8', 'replace'))
                    if link is None and published is None:
                        raise Held('GitHub page has no published raw download link')
                    target = urljoin(url, link['href']) if link else json.loads('"' + published[1] + '"')
                    target_parts = urlsplit(target)
                    repository = '/'.join(p.path.split('/')[:3])
                    if (target_parts.scheme != 'https' or target_parts.hostname != p.hostname
                            or not target_parts.path.startswith(repository + '/raw/')):
                        raise Held('Published GitHub raw link does not match the original repository')
                    url = target
                    continue
                expected_pdf = urlsplit(requested).path.lower().endswith('.pdf')
                if expected_pdf and not raw.startswith(b'%PDF-'):
                    raise Held('Published PDF link returned HTML or another file type')
                doc, text = project_original(raw, self.ledger.root)
                content = experience_content(doc, spec)
                if len(text.strip()) < 100:
                    raise Held('Readable writing text missing; OCR or another parser required')
                digest = sha256(raw).hexdigest()
                bindings = source_case(spec)
                if not bindings:
                    official = official_alumni_binding(doc, spec, url, digest, text, content)
                    if official:
                        bindings = [official]
                return {'document_id': 'writing-' + digest[:24], 'url': requested, 'final_url': url,
                    'role': spec['role'], 'program_id': spec.get('program_id'),
                    'status': 'collected', 'origin': 'new_capture', 'raw': meta,
                    'capture_chain': sources,
                    'text': store_source_bytes(text.encode(), ROOT/'blobs', suffix='.txt'),
                    'experience_content': store_source_bytes(content.encode(), ROOT/'blobs', suffix='.txt') if content else None,
                    'experience_content_characters': len(content) if content else None,
                    'discovery': spec.get('discovery'),
                    'discovered_subject_name': spec.get('discovered_subject_name'),
                    'discovered_experience_links': discover_experience_links(doc, spec, url),
                    'case_bindings': bindings,
                    'document_finality': 'public_example_unconfirmed_submission_copy',
                    'access_scope': 'public', 'reuse_license': 'not_inferred_from_public_access',
                    'discovered_pdf_links': [urljoin(url, a['href']) for a in (doc.select('a[href]') if doc else [])
                                             if urlsplit(urljoin(url, a['href'])).path.lower().endswith('.pdf')]
                        if spec.get('follow_pdf_links') else []}
            raise Held('Document redirect limit reached')
        except (Held, ValueError, OSError, subprocess.SubprocessError, requests.RequestException) as error:
            return {'url': requested, 'role': spec['role'], 'program_id': spec.get('program_id'),
                    'status': 'held', 'reason': str(error)[:260], 'case_bindings': source_case(spec),
                    'capture_chain': sources}


def legacy_documents():
    path = REPO/'.local/experience-originals/collection.json'
    if not path.exists():
        return []
    source_config = json.loads((REPO/'pipeline/config/proposal_demo_sources.json').read_text())['sources']
    specs = {s['id']: s for s in source_config}
    extra = json.loads((REPO/'pipeline/config/experience_additions.json').read_text())['cases']
    subjects = {x['case_id']: x for x in extra}
    out = []
    for doc in json.loads(path.read_text())['documents']:
        if doc.get('status') != 'collected':
            continue
        try:
            text, raw = Path(doc['text_path']).read_bytes(), Path(doc['raw_path']).read_bytes()
            if sha256(text).hexdigest() != doc['text_sha256'] or sha256(raw).hexdigest() != doc['sha256']:
                raise ValueError('Legacy writing snapshot hash mismatch')
            spec = specs.get(doc['id'], {})
            bindings = []
            for cid in spec.get('experience_case_ids', []):
                subject = subjects.get(cid, {})
                bindings.append({'case_id': 'legacy-' + cid, 'program_id': spec.get('program_id'),
                    'person_name': subject.get('name'), 'cycle': subject.get('application_round'),
                    'outcome': subject.get('outcome_status', 'unknown'),
                    'outcome_basis': subject.get('outcome_verification', 'legacy_case_review'),
                    'legacy_case_id': cid})
            out.append({'document_id': 'writing-' + doc['sha256'][:24],
                'url': doc.get('url') or spec.get('url'), 'role': spec.get('role', 'unknown'),
                'program_id': spec.get('program_id'), 'status': 'collected', 'origin': 'legacy_verified_bytes',
                'raw': {'path': doc['raw_path'], 'sha256': doc['sha256'], 'at': doc.get('fetched_at')},
                'text': {'path': doc['text_path'], 'sha256': doc['text_sha256']},
                'case_bindings': bindings,
                'document_finality': 'legacy_public_example_finality_not_assumed',
                'access_scope': 'public', 'reuse_license': 'not_inferred_from_public_access'})
        except (KeyError, OSError, ValueError) as err:
            out.append({'url': doc.get('url'), 'status': 'held', 'reason': str(err), 'origin': 'legacy'})
    return out


def run(*, experience_focus=False, reindex_only=False):
    ROOT.mkdir(parents=True, exist_ok=True)
    with single_writer(ROOT):
        preserved = previous_documents()
        now = datetime.now(KST)
        ledger = SharedEvidence(ROOT, now.date().isoformat(), network_limit=10000)
        collector = Collector(ledger)
        config = json.loads(CONFIG.read_text())
        focus = json.loads(FOCUS_CONFIG.read_text()) if FOCUS_CONFIG.exists() else {}
        source_specs = ([] if experience_focus else config['sources']) + focus.get('sources', [])
        registry_cases, registry_sources = [], []
        prior_material = {}
        if (experience_focus or reindex_only) and preserved:
            prior = json.loads((ROOT/'status.json').read_text())['corpus']
            prior_material = json.loads(read_source_bytes(prior['path'], storage=prior.get('storage')))
            registry = prior_material.get('discovery_registry', {})
            registry_meta, registry_cases = registry.get('source', {}), registry.get('cases', [])
        elif reindex_only:
            raise ValueError('No committed experience corpus is available for reindexing')
        else:
            try:
                raw, registry_meta = ledger.request(REGISTRY)
                registry_cases, registry_sources = parse_registry(raw, REGISTRY)
            except (Held, ValueError, OSError, requests.RequestException) as error:
                registry_meta = {'url': REGISTRY, 'status': 'held', 'reason': str(error)}
        specs = {s['url']: s for s in source_specs + registry_sources}
        documents = legacy_documents()
        baseline = sum(x.get('status') == 'collected' for x in documents)
        archives = archived_documents(config.get('archived_sources', []))
        documents.extend(archives)
        if reindex_only:
            documents = preserved + prior_material.get('held', [])
            overrides = {s['url']:s for s in config['sources'] + focus.get('sources', [])}
            for document in documents:
                override = overrides.get(document.get('url'), {})
                if document.get('status') == 'collected' and override.get('role'):
                    document['role'] = override['role']
                if document.get('status') == 'collected' and any(override.get(k) for k in ('case_id','person_name','case_specs')):
                    document['case_bindings'] = source_case(override)
        state = {'at_kst': now.isoformat(), 'state': 'running', 'configured_urls': len(specs),
                 'mode': 'experience_reindex' if reindex_only else 'experience_focus' if experience_focus else 'all_writing_sources',
                 'registry_cases': len(registry_cases), 'legacy_documents': baseline,
                 'reused_archive_documents': sum(x['status'] == 'collected' for x in archives),
                 'processed_new_urls': 0, 'new_readable_documents': 0}
        if preserved:
            # Retain the last committed corpus pointer while this pass is running.
            previous = json.loads((ROOT/'status.json').read_text())
            state.update({key:previous[key] for key in ('corpus', 'full_text_model_input') if key in previous})
        atomic_json(ROOT/'status.json', state)
        pdfs = []
        discovered_specs = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs = {} if reindex_only else {pool.submit(collector.capture, s): s for s in specs.values()}
            for future in as_completed(jobs):
                result, spec = future.result(), None
                spec = jobs[future]
                documents.append(result)
                if result['status'] == 'collected':
                    state['new_readable_documents'] += 1
                    discovered_specs.extend(result.get('discovered_experience_links', []))
                    for url in result.get('discovered_pdf_links', []):
                        if url not in specs:
                            pdfs.append({**spec, 'url': url,
                                'role': 'reviewer_feedback' if re.search('review', url, re.I)
                                        else 'personal_statement' if re.search('personal', url, re.I)
                                        else 'research_statement' if re.search('research', url, re.I)
                                        else 'application_bundle', 'follow_pdf_links': False})
                state.update(at_kst=datetime.now(KST).isoformat(),
                             processed_new_urls=state['processed_new_urls'] + 1)
                atomic_json(ROOT/'status.json', state)
            for result in pool.map(collector.capture, {s['url']:s for s in pdfs}.values()):
                documents.append(result)
                if result['status'] == 'collected':
                    state['new_readable_documents'] += 1
            visited = set(specs) | {s['url'] for s in pdfs}
            extra_limit = focus.get('max_discovered_urls', 160)
            extra_count = 0
            for depth in range(focus.get('max_link_depth', 3)):
                wave = {s['url']:s for s in discovered_specs if s['url'] not in visited}
                wave = dict(list(wave.items())[:max(0, extra_limit-extra_count)])
                if not wave:
                    break
                visited.update(wave)
                extra_count += len(wave)
                discovered_specs = []
                pending = {pool.submit(collector.capture, s):s for s in wave.values()}
                for future in as_completed(pending):
                    result = future.result()
                    documents.append(result)
                    if result['status'] == 'collected':
                        state['new_readable_documents'] += 1
                        discovered_specs.extend(result.get('discovered_experience_links', []))
                    state.update(at_kst=datetime.now(KST).isoformat(),
                        processed_discovered_urls=state.get('processed_discovered_urls', 0)+1,
                        discovered_link_depth=depth+1, discovered_urls=extra_count)
                    atomic_json(ROOT/'status.json', state)
        # Same byte-identical document can have multiple URLs and case bindings.
        unique, held = {}, []
        for doc in documents:
            if doc['status'] != 'collected':
                held.append(doc)
                continue
            if doc['document_id'] in unique:
                unique[doc['document_id']]['case_bindings'].extend(doc.get('case_bindings', []))
                unique[doc['document_id']].setdefault('alternate_urls', []).append(doc['url'])
            else:
                unique[doc['document_id']] = doc
        for doc in preserved:
            if doc['document_id'] not in unique:
                unique[doc['document_id']] = doc
        binding_issues = check_case_bindings(list(unique.values()))
        cases = index_cases(list(unique.values()))
        attribute_case_passages(cases, unique, lambda doc: read_source_bytes(
            doc['text']['path'], storage=doc['text'].get('storage')).decode())
        material = {'at_kst': datetime.now(KST).isoformat(), 'source_documents': list(unique.values()),
                    'experience_cases': cases, 'held': held, 'case_binding_issues': binding_issues,
                    'discovery_registry': {'source': registry_meta, 'cases': registry_cases},
                    'scope': 'historical_writing_and_guidance_not_current_recruitment_inventory'}
        stored = store_source_bytes(json.dumps(material, ensure_ascii=False).encode(), ROOT/'blobs', suffix='.json')
        if experience_focus:
            (ROOT/'experience-focus').mkdir(parents=True, exist_ok=True)
            atomic_json(ROOT/'experience-focus/latest-crawl.json', {
                'at_kst':datetime.now(KST).isoformat(), 'documents': documents,
                'specs': list(specs.values()), 'case_binding_issues':binding_issues})
        # Include full text, rather than selected cues, for subsequent LLM analysis.
        model_input = {'schema': 'writing-pattern-full-input-v1', 'model_called': False,
            'instructions': ['Read every complete source document for each case.',
                'Separate official requirements, observations, and proposed guidance.',
                'Identify the applicant claim, concrete actions, evidence, relevance, and future plan.',
                'Quote exact LF line spans and cite document IDs for every observation.',
                'Keep selected, not selected, honorable mention and unknown outcomes separate.',
                'Compare outcomes only when the program, round and document context permit.',
                'Do not attribute selection causally to a writing feature.',
                'Preserve the applicant facts and leave unsupported facts as questions.'],
            'cases': cases, 'documents': []}
        for doc in unique.values():
            text = read_source_bytes(doc['text']['path'], storage=doc['text'].get('storage')).decode()
            model_input['documents'].append({'document_id': doc['document_id'], 'role': doc['role'],
                'program_id': doc.get('program_id'), 'url': doc['url'],
                'text_sha256': sha256(text.encode()).hexdigest(), 'full_text': text,
                'line_count': len(text.splitlines())})
        inputs = store_source_bytes(json.dumps(model_input, ensure_ascii=False).encode(), ROOT/'blobs', suffix='.json')
        state.update({'at_kst': datetime.now(KST).isoformat(), 'state': 'complete',
            'unique_collected_documents': len(unique), 'collected_cases': len(cases),
            'named_public_authors': len({compact(c['person_name']).casefold() for c in cases if c.get('person_name')}),
            'cases_without_confirmed_person_name': sum(not c.get('person_name') for c in cases),
            'case_outcomes': dict(Counter(c.get('outcome', 'unknown') for c in cases)),
            'cases_with_application_documents': sum(bool(set(c['document_roles']) &
                {'application_statement', 'personal_statement', 'research_statement', 'application_bundle'}) for c in cases),
            'cases_with_feedback_documents': sum('reviewer_feedback' in c['document_roles'] for c in cases),
            'document_roles': dict(Counter(d['role'] for d in unique.values())),
            'held_count': len(held), 'corpus': stored, 'full_text_model_input': inputs,
            'case_binding_issues': len(binding_issues),
            'case_attribution_statuses': dict(Counter(c['quality_audit']['attribution_status'] for c in cases)),
            'model_called': False, 'approved_patterns_added': 0})
        atomic_json(ROOT/'status.json', state)
        print(json.dumps({k:v for k,v in state.items() if k not in ('corpus','full_text_model_input')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experience-focus', action='store_true')
    parser.add_argument('--reindex-only', action='store_true', help='Rebind collected cases without network requests')
    args = parser.parse_args()
    run(experience_focus=args.experience_focus, reindex_only=args.reindex_only)
