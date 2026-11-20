"""Expand previously observed public Greenhouse posts into complete public boards.

Only the documented unauthenticated GET API is used. API access failures are held.
The API response is a source snapshot, not a count of verified opportunities.
"""
from datetime import datetime
from hashlib import sha256
from html import unescape
import json
from pathlib import Path
import re
import requests
from urllib.parse import urlsplit

from collect_benefitlens_crossborder import collect, projection
from benefitlens_source_storage import read_source_bytes, store_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json
from run_benefitlens_korea import Evidence, Held

CONFIG = Path(__file__).resolve().parents[1] / 'pipeline/config/crossborder_expansion_20261114.json'
CORPORATE_CONFIG = Path(__file__).resolve().parents[1] / 'pipeline/config/public_board_publishers.json'
ROOT = CACHE / 'public-board-expansion-v1'
STUDENT_ROLE = re.compile(r'\bintern(?:ship)?\b|\bworking student\b|\bstudent researcher\b|'
                          r'\bco[- ]?op\b|\bapprentice(?:ship)?\b|\bfellow(?:ship)?\b', re.I)


def discover(config):
    boards = {}
    for label, url in config.items():
        p = urlsplit(url)
        if p.scheme != 'https' or p.hostname not in ('boards.greenhouse.io', 'job-boards.greenhouse.io'):
            continue
        match = re.fullmatch(r'/([A-Za-z0-9_-]+)/jobs/[0-9]+/?', p.path)
        if match:
            token = match[1]
            row = boards.setdefault(token, {'url': f'https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true',
                                            'observed_post_urls': []})
            row['observed_post_urls'].append(url)
    return boards


def published_board_tokens(raw):
    """Use only board IDs literally disclosed by the corporate careers page."""
    html = unescape(raw.decode('utf-8', 'replace'))
    tokens = set(re.findall(r'https://(?:job-boards|boards)\.greenhouse\.io/([\w-]+)(?:/|[?\"\s])', html))
    tokens.update(re.findall(r'https://(?:boards-api|api)\.greenhouse\.io/v1/boards/([\w-]+)/(?:jobs|departments|offices)', html))
    # Moloco explicitly constructs the public API URL from this literal config.
    if 'https://boards-api.greenhouse.io/v1/boards/' in html:
        tokens.update(re.findall(r"\bboardToken\s*:\s*['\"]([\w-]+)['\"]", html))
    return sorted(tokens)


def corporate_boards(ledger, pages):
    boards, probes = {}, []
    for url in pages:
        try:
            raw, meta = ledger.request(url)
            tokens = published_board_tokens(raw)
            probes.append({'publisher_url': url, 'snapshot': meta, 'board_tokens': tokens})
            for token in tokens:
                row = boards.setdefault(token, {
                    'url': f'https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true',
                    'observed_post_urls': [], 'publisher_bindings': []})
                row['publisher_bindings'].append({'url': url, 'snapshot': meta,
                                                 'basis': 'public_career_page_literal_board_id'})
        except (Held, ValueError, OSError, requests.RequestException) as error:
            probes.append({'publisher_url': url, 'held': str(error)[:240]})
    return boards, probes


def run():
    ROOT.mkdir(parents=True, exist_ok=True)
    day = datetime.now(KST).date().isoformat()
    boards = discover(json.loads(CONFIG.read_text()))
    if CORPORATE_CONFIG.exists():
        additions, probes = corporate_boards(Evidence(ROOT, day), json.loads(CORPORATE_CONFIG.read_text())['career_pages'])
        for token, row in additions.items():
            if token in boards:
                boards[token]['publisher_bindings'] = row['publisher_bindings']
            else:
                boards[token] = row
        atomic_json(ROOT / 'publisher_discovery.json', probes)
    atomic_json(ROOT / 'board_provenance.json', boards)
    config = ROOT / 'sources.json'
    atomic_json(config, {token: row['url'] for token, row in boards.items()})
    manifest_path = collect(config, ROOT)
    manifest = json.loads(manifest_path.read_text())
    all_jobs, candidates, held = {}, [], {}
    for token, capture in manifest['rows'].items():
        if capture.get('held'):
            held[token] = capture['held']
            continue
        original = capture['original']
        raw = read_source_bytes(original['path'], storage=original['storage'])
        if sha256(raw).hexdigest() != original['sha256']:
            raise ValueError('Public board snapshot hash mismatch')
        data = json.loads(raw)
        jobs = data.get('jobs', [])
        all_jobs[token] = len(jobs)
        for job in jobs:
            if not STUDENT_ROLE.search(job.get('title', '')) or not job.get('internal_job_id'):
                continue
            _, text = projection(unescape(job.get('content', '')).encode())
            candidates.append({'source': token, 'native_id': str(job['id']),
                'internal_job_id': str(job['internal_job_id']), 'title': job['title'],
                'url': job['absolute_url'], 'location': job.get('location'),
                'source_snapshot': original,
                'text': store_source_bytes(text.encode(), ROOT / 'blobs', suffix='.txt'),
                'state': 'awaiting_review', 'verified': False,
                'review_required': ['student eligibility', 'payment evidence',
                                    'application availability', 'Korean applicant access',
                                    'issuer-role deduplication']})
    atomic_json(ROOT / day / 'review_candidates.json', candidates)
    status = {'at': datetime.now(KST).isoformat(), 'source_config_count': len(boards),
              'readable_boards': len(all_jobs), 'all_published_posts_by_board': all_jobs,
              'student_role_candidates': len(candidates), 'held': held, 'verified_added': 0,
              'candidate_ids_unique': len({(c['source'], c['native_id']) for c in candidates})}
    atomic_json(ROOT / 'status.json', status)
    print(json.dumps(status, ensure_ascii=False))


if __name__ == '__main__':
    run()
