"""Resume selective public proposal downloads from a commit-pinned GitHub tree.

Outputs the existing staged-report contract, which the Python experience worker
can ingest. File and person counts remain separate; no outcome is inferred.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

from benefitlens_source_storage import store_source_bytes
from collect_benefitlens_writing_foundations import PublicRead
from import_benefitlens_full_inventory import SharedEvidence
from run_benefitlens_campaign import CACHE, KST, atomic_json, checked_root, single_writer


def proposal_members(tree):
    if tree.get('truncated'):
        raise ValueError('Recursive tree is truncated; do not claim a complete inventory')
    members = []
    for row in tree.get('tree', []):
        name = row.get('path', '')
        if (row.get('type') == 'blob' and name.lower().endswith('.pdf')
                and not name.startswith('/') and '..' not in Path(name).parts):
            members.append(row)
    return sorted(members, key=lambda row: row['path'])


def source_program_candidates(member_path):
    """A publisher folder is a discovery hint, not a verified body binding."""
    program = {'GSoC': 'google-summer-of-code', 'LFX': 'lfx-mentorship',
               'Summer of Bitcoin': 'summer-of-bitcoin'}.get(member_path.split('/')[0])
    return [program] if program else []


def collect_file(member, *, repo, revision, root, ledger):
    member_path = member['path']
    url = 'https://raw.githubusercontent.com/' + repo + '/' + revision + '/' + quote(member_path, safe='/')
    published_url = 'https://github.com/' + repo + '/blob/' + revision + '/' + quote(member_path, safe='/')
    row = {'url': published_url, 'download_url': url, 'member': member_path,
           'repository': repo, 'revision': revision, 'git_blob_sha': member['sha'],
           'role_hint': 'public_application_proposal_candidate',
           'source_outcome_label': 'not_selected' if re.search(r'(^|/)rejected(/|$)', member_path, re.I)
                                   else 'selected' if re.search(r'(^|/)accepted(/|$)', member_path, re.I) else None,
           'semantic_review': 'unreviewed', 'accepted_experience': False,
           'approved_pattern': False, 'model_called': False,
           'reuse_rights': 'not_inferred_from_repository_visibility'}
    if member.get('size', 0) > 32 * 1024**2:
        return dict(row, state='held', reason='Registered per-file byte ceiling')
    try:
        raw, original = ledger.request(url, redirect_metadata=True)
        if original['status'] != 200 or not raw.startswith(b'%PDF'):
            raise ValueError('Expected a public PDF response')
        if len(raw) != member['size']:
            raise ValueError('Pinned tree size differs from downloaded PDF')
        # Git uses a SHA-1 over the blob header and bytes, not a file SHA-1.
        import hashlib
        blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\x00' + raw).hexdigest()
        if blob != member['sha']:
            raise ValueError('Pinned Git blob identity differs from downloaded PDF')
        extraction = subprocess.run(['pdftotext', '-layout', '-', '-'], input=raw,
                                    capture_output=True, check=True, timeout=40)
        text = extraction.stdout.decode('utf8')
        if len(text.strip()) < 250 or '\x00' in text:
            raise ValueError('Usable proposal text missing; OCR or review needed')
        row.update(state='body_staged', kind='pdf', original=original,
                   text=store_source_bytes(text.encode(), root/'blobs', suffix='.txt'),
                   text_characters=len(text),
                   discovery={'repository_tree_revision': revision, 'path': member_path,
                              'git_blob_sha': blob},
                   program_candidates=source_program_candidates(member_path),
                   document_finality='public_example_unconfirmed_submission_copy')
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        row.update(state='held', reason=type(error).__name__ + ': ' + str(error)[:220])
    return row


def run(args):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repo):
        raise ValueError('Expected one public owner/repository')
    root = checked_root(args.root)
    with single_writer(root):
        reader = PublicRead(root, 6)
        metadata, _ = reader.read('https://api.github.com/repos/' + args.repo)
        branch = json.loads(metadata)['default_branch']
        commit, _ = reader.read('https://api.github.com/repos/' + args.repo + '/commits/' + quote(branch, safe=''))
        revision = json.loads(commit)['sha']
        if not re.fullmatch('[a-f0-9]{40}', revision):
            raise ValueError('Unusable commit identity')
        raw_tree, tree_receipt = reader.read('https://api.github.com/repos/' + args.repo + '/git/trees/' + revision + '?recursive=1')
        members = proposal_members(json.loads(raw_tree))
        atomic_json(root/'tree-inventory.json', {'repository': args.repo, 'revision': revision,
                    'tree_receipt': tree_receipt, 'pdf_members': members})
        records = {r['member']: r for r in json.loads((root/'index.json').read_text())} if (root/'index.json').exists() else {}
        for row in records.values():
            row['program_candidates'] = source_program_candidates(row['member'])
        previous = sum(r.get('state') == 'body_staged' for r in records.values())
        # Preserve holds. Existing bodies are reused only for the same Git blob.
        pending = [r for r in members if r['path'] not in records or
                   records[r['path']].get('git_blob_sha') != r['sha'] or
                   (args.retry_size_holds and records[r['path']].get('reason', '').endswith(
                       'Publisher response exceeds byte ceiling') and r['size'] <= 32*1024**2)][:args.max_files]
        ledger = SharedEvidence(root, datetime.now(KST).date().isoformat(), args.max_requests)
        requests_before_run = ledger.network_requests
        pinned_prefix = 'https://raw.githubusercontent.com/' + args.repo + '/' + revision + '/'
        ledger.response_byte_limit = lambda url: 32*1024**2 if url.startswith(pinned_prefix) else 8*1024**2
        def save(state):
            atomic_json(root/'index.json', list(records.values()))
            status = {'at_kst': datetime.now(KST).isoformat(), 'state': state,
                      'repository': args.repo, 'revision': revision,
                      'inventory_pdf_files': len(members), 'scheduled_this_run': len(pending),
                      'processed_records': len(records),
                      'staged_originals': sum(r.get('state') == 'body_staged' for r in records.values()),
                      'staged_before_run': previous,
                      'states': dict(Counter(r['state'] for r in records.values())),
                      'unique_text_hashes': len({r['text']['sha256'] for r in records.values() if r.get('text')}),
                      'network_requests': ledger.network_requests,
                      'network_requests_this_run': ledger.network_requests - requests_before_run,
                      'api_network_requests_this_run': reader.calls,
                      'model_called': False, 'accepted_experiences_added': 0, 'approved_patterns_added': 0}
            atomic_json(root/'status.json', status)
            return status
        save('running')
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(collect_file, m, repo=args.repo, revision=revision,
                                   root=root, ledger=ledger) for m in pending]
            for i, future in enumerate(as_completed(futures), 1):
                row = future.result()
                records[row['member']] = row
                if i % 10 == 0:
                    print(json.dumps(save('running'), ensure_ascii=False), flush=True)
        print(json.dumps(save('complete'), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='devweekends/open-source-proposals')
    parser.add_argument('--root', type=Path, default=CACHE/'experience-gsoc-publisher-reports-v1/devweekends-proposals')
    parser.add_argument('--max-files', type=int, default=300)
    parser.add_argument('--max-requests', type=int, default=650)
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--retry-size-holds', action='store_true',
                        help='Retry only local byte-ceiling holds within the registered 32 MiB file budget')
    args = parser.parse_args()
    if not 1 <= args.workers <= 4 or args.max_files < 1 or args.max_requests < 1:
        parser.error('Positive budgets and 1..4 workers required')
    run(args)
