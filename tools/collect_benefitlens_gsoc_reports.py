"""Stage first-party GSoC report pages linked from the shared project index."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from benefitlens_source_storage import read_source_bytes, store_source_bytes
from import_benefitlens_full_inventory import SharedEvidence
from run_benefitlens_campaign import CACHE, KST, atomic_json, checked_root, single_writer
from run_benefitlens_korea import Held, soup
from scale_benefitlens_experiences import body_text, canonical

INDEX = CACHE/'experience-bulk-discovery-v1/gsoc-shared-directory/inventory.json'
ROOT = CACHE/'experience-gsoc-publisher-reports-v1'
SELECTORS = {
    'community.kde.org': '#mw-content-text .mw-parser-output',
    'blog.freifunk.net': 'article .entry-content',
    'blog.metabrainz.org': 'article .entry-content',
}


def candidate_groups(index):
    meta = json.loads(index.read_text())['candidate_index_jsonl']
    raw = read_source_bytes(meta['path'], storage=meta.get('storage'))
    if sha256(raw).hexdigest() != meta['sha256']:
        raise ValueError('Shared directory checksum mismatch')
    groups = {host: {} for host in SELECTORS}
    for line in raw.splitlines():
        item = json.loads(line)
        url = canonical(item.get('candidate_report_url') or '')
        if not url or (host := urlsplit(url).hostname) not in groups:
            continue
        target = {k: item[k] for k in ('program_id', 'round', 'person_name',
                                      'organization', 'project_title', 'official_project_url',
                                      'source_directory_url', 'source_directory_sha256')}
        groups[host].setdefault(url, []).append(target)
    return groups


def capture(ledger, url, host):
    visited = set()
    for _ in range(6):
        if not canonical(url) or url in visited or urlsplit(url).hostname != host:
            raise Held('Redirect outside the registered report host or redirect loop')
        visited.add(url)
        raw, receipt = ledger.request(url, redirect_metadata=True)
        if receipt['status'] in (301, 302, 303, 307, 308):
            url = urljoin(url, receipt['headers'].get('location', ''))
            continue
        return raw, receipt, url
    raise Held('Report redirect limit')


def collect_host(root, host, targets, limit):
    folder = root/host
    folder.mkdir(exist_ok=True)
    ledger = SharedEvidence(folder, datetime.now(KST).date().isoformat(), limit*2+10)
    rows = []
    stop_reason = None
    for url, projects in list(targets.items())[:limit]:
        result = {'url': url, 'project_candidates': projects, 'accepted_experience': False}
        try:
            raw, receipt, final = capture(ledger, url, host)
            result.update(original=receipt, final_url=final)
            if 'text/html' not in receipt['headers'].get('content-type', '').lower():
                raise Held('Expected an HTML article or report')
            node = soup(raw).select_one(SELECTORS[host])
            if node is None:
                raise Held('Report body selector absent')
            for child in node.select('.toc, .mw-editsection, .catlinks'):
                child.decompose()
            text = body_text(node)
            if len(text.strip()) < 200 or '\x00' in text:
                raise Held('Report body is too short or unusable')
            result.update(state='body_staged', text=store_source_bytes(text.encode(),
                          folder/'blobs', suffix='.txt'),
                          association='shared_directory_link; verify author and program before use')
        except (ValueError, OSError) as error:
            result.update(state='held', reason=str(error)[:300])
            if any(s in str(error) for s in ('robots disallows', 'HTTP 401', 'HTTP 403',
                                           'HTTP 429', 'Publisher paused', 'budget reached',
                                           'Existing campaign source hold', 'Unusable robots')):
                stop_reason = result['reason']
        rows.append(result)
        if len(rows) == 1 or len(rows) % 10 == 0 or stop_reason:
            atomic_json(folder/'index.json', rows)
            print(json.dumps({'source': host, 'processed': len(rows),
                              'latest_state': result['state'], 'reason': result.get('reason')}),
                  flush=True)
        if stop_reason:
            break
    atomic_json(folder/'index.json', rows)
    return {'host': host, 'candidate_links': len(targets), 'processed_links': len(rows),
            'network_requests': ledger.network_requests, 'states': dict(Counter(
                r['state'] for r in rows)), 'stop_reason': stop_reason,
            'index': str(folder/'index.json')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, default=INDEX)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--max-per-source', type=int, default=150)
    args = parser.parse_args()
    if not 1 <= args.max_per_source <= 500:
        parser.error('--max-per-source must be between 1 and 500')
    root = checked_root(args.root)
    groups = candidate_groups(args.index)
    with single_writer(root), ThreadPoolExecutor(max_workers=len(groups)) as pool:
        results = list(pool.map(lambda host: collect_host(root, host, groups[host],
                                                       args.max_per_source), groups))
        summary = {'at_kst': datetime.now(KST).isoformat(), 'sources': results,
                   'staged_bodies': sum(s['states'].get('body_staged', 0) for s in results),
                   'accepted_experiences_added': 0}
        atomic_json(root/'status.json', summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
