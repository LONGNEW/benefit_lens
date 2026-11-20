"""Collect public participant documents from pinned repositories and a gist queue.

An archive file is a document, never automatically an independent participant.
Only observed project-directory links enter the gist queue. No comments, code,
private resources, model calls, or outcome inference are collected.
"""
import argparse
from collections import Counter
from datetime import datetime
from hashlib import sha1, sha256
import io
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import quote, urlsplit
import zipfile

from benefitlens_source_storage import read_source_bytes, store_source_bytes
from collect_benefitlens_writing_foundations import PublicRead
from run_benefitlens_campaign import CACHE, KST, atomic_json, checked_root, single_writer

ROOT = CACHE / 'participant-documents-20261119'
PROJECT_INDEX = CACHE / 'experience-bulk-discovery-v1/gsoc-shared-directory/inventory.json'
DEFAULT_REPOS = [
    {'repo': 'fossology/gsoc', 'include': r'^docs/20\d\d/[^/]+/(?:updates/[^/]+|index|final[^/]*|report[^/]*)\.md$',
     'collection_role': 'participant_reports'},
    {'repo': 'OpenAstronomy/gsoc-proposals', 'include': r'^20\d\d/.+\.(?:md|pdf)$',
     'collection_role': 'application_proposals'},
    {'repo': 'Google-Summer-of-Code-Archive/gsoc-proposals-archive', 'include': r'\.pdf$',
     'collection_role': 'application_proposals'},
    {'repo': 'saketkc/fos-proposals', 'include': r'\.(?:pdf|md|txt)$',
     'collection_role': 'application_proposals'},
    {'repo': 'saurabhshri/GSoC-2017-Accepted-Proposals', 'include': r'\.pdf$',
     'collection_role': 'application_proposals'},
    {'repo': 'OpenLake/GSoC_Proposals', 'include': r'\.pdf$',
     'collection_role': 'application_proposals'},
    {'repo': 'crux-bphc/accepted-GSoC-proposals', 'include': r'\.pdf$',
     'collection_role': 'application_proposals'},
    {'repo': 'CodeClubSMVDU/gsoc-proposals', 'include': r'\.pdf$',
     'collection_role': 'application_proposals'},
    {'repo': 'c2siorg/GSoC', 'include': r'^GSoC[-/]20\d\d/.+\.(?:md|pdf|txt)$',
     'collection_role': 'participant_reports'},
]


def git_blob(raw):
    return sha1(b'blob ' + str(len(raw)).encode() + b'\x00' + raw).hexdigest()


def safe_member(path):
    return bool(path) and not path.startswith('/') and '..' not in Path(path).parts and '\\' not in path


def selected_members(tree, pattern):
    if tree.get('truncated'):
        raise ValueError('Truncated tree cannot establish a complete inventory')
    return [m for m in tree.get('tree', []) if m.get('type') == 'blob'
            and safe_member(m.get('path', '')) and re.search(pattern, m['path'], re.I)
            and not re.search(r'(^|/)(?:readme|authors|license|contributing|code_of_conduct|changelog|citation)\.', m['path'], re.I)]


def document_role(path, collection_role):
    if collection_role == 'application_proposals':
        return 'application_proposal'
    if '/updates/' in path:
        return 'participation_progress_report'
    if re.search(r'final|report|summary', Path(path).name, re.I):
        return 'participation_final_report_candidate'
    return 'participation_project_overview_candidate'


def text_content(raw, member):
    if member.lower().endswith('.pdf'):
        if not raw.startswith(b'%PDF'):
            raise ValueError('PDF signature absent')
        proc = subprocess.run(['pdftotext', '-layout', '-', '-'], input=raw,
                              capture_output=True, check=True, timeout=45)
        return proc.stdout.decode('utf8')
    return raw.decode('utf-8-sig')


def common_fields(url, role):
    return {'url': url, 'document_role': role, 'role_hint': role,
            'semantic_review': 'unreviewed', 'accepted_experience': False,
            'approved_pattern': False, 'model_called': False,
            'program_candidates': ['google-summer-of-code'],
            'reuse_rights': 'not_inferred_from_public_visibility'}


def project_directory(index):
    meta = json.loads(index.read_text())['candidate_index_jsonl']
    raw = read_source_bytes(meta['path'], storage=meta.get('storage'))
    if sha256(raw).hexdigest() != meta['sha256']:
        raise ValueError('Project directory checksum differs')
    return [json.loads(line) for line in raw.splitlines()]


def gist_groups(projects):
    groups = {}
    for project in projects:
        url = project.get('candidate_report_url') or ''
        parsed = urlsplit(url)
        match = re.fullmatch(r'/([^/]+)/([a-fA-F0-9]{20,40})/?', parsed.path)
        if parsed.scheme == 'https' and parsed.hostname == 'gist.github.com' and match:
            gist_id = match[2].lower()
            groups.setdefault(gist_id, {'url': url.split('#')[0].split('?')[0],
                                       'project_candidates': []})['project_candidates'].append(project)
    return groups


def baseline_hashes(root):
    destination = root / 'preexisting-hashes.json'
    if destination.exists():
        return set(json.loads(destination.read_text()))
    paths = list((CACHE/'experience-gsoc-publisher-reports-v1').glob('*/index.json'))
    paths += list((CACHE/'writing-foundations-v1').glob('*/records.json'))
    paths += list((CACHE/'experience-bulk-discovery-v1').glob('*/index.json'))
    hashes = set()
    for path in paths:
        rows = json.loads(path.read_text())
        if isinstance(rows, list):
            for row in rows:
                for key in ('text', 'raw', 'original'):
                    value = row.get(key)
                    if isinstance(value, dict) and value.get('sha256'):
                        hashes.add(value['sha256'])
    atomic_json(destination, sorted(hashes))
    atomic_json(root/'baseline-scope.json', {'at_kst': datetime.now(KST).isoformat(),
                'files': [str(p) for p in paths], 'hashes': len(hashes),
                'scope': 'Listed cached source indexes only; global database duplicates require ingestion review'})
    return hashes


def collect_repository(spec, reader, root, records, inventories, save):
    repo = spec['repo']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Invalid repository scope')
    raw, metadata_receipt = reader.read('https://api.github.com/repos/' + repo)
    metadata = json.loads(raw)
    branch = metadata['default_branch']
    raw, commit_receipt = reader.read('https://api.github.com/repos/' + repo + '/commits/' + quote(branch, safe=''))
    revision = json.loads(raw)['sha']
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Invalid immutable commit identity')
    raw, tree_receipt = reader.read('https://api.github.com/repos/' + repo + '/git/trees/' + revision + '?recursive=1')
    tree = json.loads(raw)
    members = selected_members(tree, spec['include'])
    inventory = {'repository': repo, 'revision': revision, 'complete_recursive_tree': True,
                 'selected_members': members, 'metadata_receipt': metadata_receipt,
                 'commit_receipt': commit_receipt, 'tree_receipt': tree_receipt}
    inventories[repo] = inventory
    pending = [m for m in members if records.get(repo+'@'+revision+'/'+m['path'], {}).get('state') != 'body_staged'
               and repo+'@'+revision+'/'+m['path'] not in records]
    if not pending:
        save('running')
        return
    archive, archive_receipt = reader.read('https://codeload.github.com/' + repo + '/zip/' + revision)
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        infos = z.infolist()
        if sum(m.file_size for m in infos) > 512*1024**2:
            raise ValueError('Decoded repository archive exceeds 512 MiB bound')
        by_relative = {}
        for member in infos:
            relative = member.filename.split('/', 1)[-1]
            if relative and not member.is_dir():
                if not safe_member(relative) or relative in by_relative:
                    raise ValueError('Unsafe or duplicate archive member')
                by_relative[relative] = member
        for m in pending:
            path = m['path']
            key = repo+'@'+revision+'/'+path
            url = 'https://github.com/'+repo+'/blob/'+revision+'/'+quote(path, safe='/')
            row = dict(common_fields(url, document_role(path, spec['collection_role'])),
                       source_id='participant-repository-'+repo.replace('/', '-'),
                       repository=repo, revision=revision, member=path,
                       discovery={'repository_commit': revision, 'git_blob_sha': m['sha'],
                                  'path': path, 'complete_tree': True},
                       source_definition={'id': 'participant-repository-'+repo.replace('/', '-'),
                                          'document_role': document_role(path, spec['collection_role'])})
            try:
                info = by_relative.get(path)
                if info is None or info.file_size != m['size']:
                    raise ValueError('Archive member absent or pinned size differs')
                if info.file_size > 32*1024**2:
                    raise ValueError('Member exceeds 32 MiB bound')
                raw = z.read(info)
                if git_blob(raw) != m['sha']:
                    raise ValueError('Member differs from pinned Git blob')
                row['original'] = dict(store_source_bytes(raw, root/'blobs', suffix='.raw'),
                                       url=url, archive_receipt=archive_receipt,
                                       git_blob_sha=m['sha'], member=path,
                                       at=archive_receipt.get('at_kst'), status=200)
                text = text_content(raw, path)
                if len(text.strip()) < 200 or '\x00' in text:
                    raise ValueError('Insufficient usable text; review or OCR required')
                year = re.search(r'(?:^|/|[-_])(20\d\d)(?:/|$)', path)
                author = re.search(r'(?im)^\s*(?:author|student|contributor|name)\s*:\s*[\"\']?([^\n\"\']{3,100})', text)
                row.update(state='body_staged', kind='pdf' if path.lower().endswith('.pdf') else 'markdown',
                           text=store_source_bytes(text.encode(), root/'blobs', suffix='.txt'),
                           text_characters=len(text), round_candidate=year[1] if year else None,
                           author=None, author_display_name=author[1].strip() if author else None,
                           author_evidence='explicit_labeled_line' if author else 'unresolved',
                           program_binding='repository_scope_candidate_requires_body_review')
            except (ValueError, UnicodeError, OSError, subprocess.SubprocessError) as error:
                row.update(state='held', reason=str(error)[:220])
            records[key] = row
        save('running')


def collect_gist(gist_id, group, reader, root, records):
    raw, receipt = reader.read('https://api.github.com/gists/' + gist_id)
    data = json.loads(raw)
    if not data.get('public') or data.get('truncated'):
        raise ValueError('Gist private or files list truncated')
    revision = data['history'][0]['version']
    owner = (data.get('owner') or {}).get('login')
    rows = []
    for name, f in data.get('files', {}).items():
        role = 'participation_report_candidate'
        row = dict(common_fields(group['url'], role), gist_id=gist_id, revision=revision,
                   member=name, source_id='gsoc-participant-gists',
                   author={'key': 'github:'+owner, 'name': owner} if owner else None,
                   author_display_name=owner,
                   author_evidence='gist_owner_login; directory_person_identity_unreviewed',
                   project_candidates=group['project_candidates'],
                   discovery={'api_receipt': receipt, 'gist_revision': revision,
                              'directory_link': group['url']})
        if not name.lower().endswith(('.md', '.txt', '.rst', '.markdown', '.html')):
            row.update(state='held', reason='Non-prose extension; code metadata is not experience')
        elif f.get('truncated') or not isinstance(f.get('content'), str):
            row.update(state='held', reason='Gist file content truncated; observed raw_url pending',
                       download_url=f.get('raw_url'))
        else:
            content = f['content']
            if len(content.strip()) < 200:
                row.update(state='held', reason='Insufficient prose text')
            else:
                original = store_source_bytes(content.encode(), root/'blobs', suffix='.txt')
                row.update(state='body_staged', kind='markdown',
                           original=dict(original, api_receipt=receipt, url=group['url'],
                                         at=receipt.get('at_kst'), status=200),
                           text=original, text_characters=len(content),
                           document_finality='public_gist; actual_submission_unconfirmed')
        records['gist:'+gist_id+'@'+revision+'/'+name] = row
        rows.append(row)
    return rows


def run(args):
    root = checked_root(args.root)
    with single_writer(root):
        baseline = baseline_hashes(root)
        records = json.loads((root/'records-map.json').read_text()) if (root/'records-map.json').exists() else {}
        inventories = json.loads((root/'repository-inventories.json').read_text()) if (root/'repository-inventories.json').exists() else {}
        gist_state = json.loads((root/'gist-checkpoint.json').read_text()) if (root/'gist-checkpoint.json').exists() else {}
        gist_targets = gist_groups(project_directory(args.project_index))
        reader = PublicRead(checked_root(args.transport_root) if args.transport_root else root/'transport', args.max_public_reads)
        failures = []

        def save(state):
            rows = list(records.values())
            # Normalize older staging rows to the shared consumer contract.
            for row in rows:
                if re.fullmatch(r'(?:readme|authors|license|contributing|code_of_conduct|changelog|citation)\.[^.]+',
                                Path(row.get('member', '')).name, re.I):
                    row.update(state='reference_index', document_role='collection_directory', role_hint='collection_directory')
                if isinstance(row.get('author'), str):
                    row['author_display_name'] = row['author']
                    row['author'] = {'key': 'github:'+row['author'], 'name': row['author']} if row.get('gist_id') else None
                if row.get('original'):
                    original = row['original']
                    context = original.get('archive_receipt') or original.get('api_receipt') or {}
                    original.setdefault('url', row['url'])
                    original.setdefault('at', context.get('at_kst'))
                    original.setdefault('status', 200)
                if row.get('member') and not row.get('round_candidate'):
                    year = re.search(r'(?:^|/|[-_])(20\d\d)(?:/|$)', row['member'])
                    row['round_candidate'] = year[1] if year else None
            staged = [r for r in rows if r.get('state') == 'body_staged']
            text_hashes = {r['text']['sha256'] for r in staged}
            status = {'at_kst': datetime.now(KST).isoformat(), 'state': state,
                      'original_documents_staged': len(staged), 'unique_texts': len(text_hashes),
                      'unique_texts_absent_from_cached_baseline': len(text_hashes-baseline),
                      'baseline_comparison_scope': str(root/'baseline-scope.json'),
                      'states': dict(Counter(r['state'] for r in rows)),
                      'document_roles': dict(Counter(r['document_role'] for r in staged)),
                      'repositories_inventoried': len(inventories),
                      'repository_candidate_files': sum(len(i['selected_members']) for i in inventories.values()),
                      'gist_candidate_urls': len(gist_targets), 'gists_processed': len(gist_state),
                      'gists_remaining': len(set(gist_targets)-set(gist_state)),
                      'public_reads_this_run': reader.calls, 'failures': failures,
                      'accepted_experiences_added': 0, 'approved_patterns_added': 0, 'model_called': False}
            atomic_json(root/'records-map.json', records)
            atomic_json(root/'index.json', rows)
            atomic_json(root/'repository-inventories.json', inventories)
            atomic_json(root/'gist-checkpoint.json', gist_state)
            atomic_json(root/'status.json', status)
            return status

        save('running')
        specs = json.loads(args.repository_config.read_text()) if args.repository_config else DEFAULT_REPOS
        if not args.gists_only:
            for spec in specs:
                try:
                    collect_repository(spec, reader, root, records, inventories, save)
                except (ValueError, OSError, zipfile.BadZipFile) as error:
                    failures.append({'repository': spec['repo'], 'reason': str(error)[:220]})
                    save('running')
                    if any(t in str(error) for t in ('Provider', 'HTTP 403', 'HTTP 429', 'budget')):
                        break
        if not args.repositories_only:
            for gist_id in sorted(set(gist_targets)-set(gist_state))[:args.max_gists]:
                try:
                    result = collect_gist(gist_id, gist_targets[gist_id], reader, root, records)
                    gist_state[gist_id] = {'state': 'processed', 'files': len(result)}
                except (ValueError, OSError, KeyError) as error:
                    failures.append({'gist_id': gist_id, 'reason': str(error)[:220]})
                    if any(t in str(error) for t in ('Provider', 'HTTP 403', 'HTTP 429', 'budget')):
                        break
                    gist_state[gist_id] = {'state': 'held', 'reason': str(error)[:220]}
                if len(gist_state) % 10 == 0:
                    print(json.dumps(save('running')), flush=True)
        print(json.dumps(save('complete_with_holds' if failures else 'complete')), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--project-index', type=Path, default=PROJECT_INDEX)
    parser.add_argument('--repository-config', type=Path)
    parser.add_argument('--transport-root', type=Path, help='Reuse a verified existing public response cache')
    parser.add_argument('--max-public-reads', '--max-api-requests', dest='max_public_reads', type=int, default=45)
    parser.add_argument('--max-gists', type=int, default=200)
    parser.add_argument('--repositories-only', action='store_true')
    parser.add_argument('--gists-only', action='store_true')
    parser.add_argument('--continuous', action='store_true', help='Resume unfinished gist queue with provider holds preserved')
    parser.add_argument('--interval', type=int, default=3600, help='Seconds between bounded batches')
    args = parser.parse_args()
    if args.repositories_only and args.gists_only or not 1 <= args.max_public_reads <= 100 or args.max_gists < 0 or args.interval < 60:
        parser.error('Choose one mode and positive bounded API budget')
    while True:
        run(args)
        status = json.loads((args.root/'status.json').read_text())
        if not args.continuous or args.repositories_only or not status['gists_remaining']:
            break
        deadline = time.time() + args.interval
        holds = args.root/'transport/api-holds.json'
        if holds.exists():
            deadline = max(deadline, json.loads(holds.read_text()).get('api.github.com', {}).get('resume_after', 0))
        atomic_json(args.root/'worker-wait.json', {'state': 'waiting_for_next_batch',
                    'next_batch_at_kst': datetime.fromtimestamp(deadline, KST).isoformat(),
                    'gist_queue_remaining': status['gists_remaining'], 'model_called': False})
        time.sleep(max(0, deadline-time.time()))
