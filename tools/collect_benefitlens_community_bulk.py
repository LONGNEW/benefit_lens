"""Acquire public site dumps without credentials; keep complete posts and select writing threads.

The September 2026 Internet Archive item is a community redistribution, not a
first-party release. First-party historical metadata is retained for comparison.
No Stack Exchange API quota, model call, authentication or crawler bypass is used.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
from hashlib import md5, sha1, sha256
import html
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
from urllib.parse import quote, urlsplit
from urllib.robotparser import RobotFileParser
import zlib

from bs4 import BeautifulSoup
from lxml import etree
import requests

from benefitlens_source_storage import store_source_bytes
from run_benefitlens_campaign import CACHE, atomic_json, checked_root, single_writer

ROOT = CACHE / 'community-bulk-20261119'
CONFIG = Path(__file__).resolve().parents[1] / 'pipeline/config/community_bulk_sources_v1.json'
UA = 'BenefitLens/1.0 (public licensed archive research; no credentials)'


def now():
    return datetime.now(timezone.utc).isoformat()


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def plain(body):
    node = BeautifulSoup(body, 'lxml')
    for item in node.select('script,style'):
        item.decompose()
    return '\n'.join(s.strip() for s in node.get_text('\n').splitlines() if s.strip())


def question_scope(site, attrs, config):
    text = html.unescape(attrs.get('Title', '') + '\n' + attrs.get('Body', ''))
    tags = set(re.findall(r'<([^>]+)>', attrs.get('Tags', '')))
    reasons = []
    if re.search(config['application_pattern'], text, re.I):
        reasons.append('application_or_professional_document_expression')
        if re.search(config['application_pattern'], html.unescape(attrs.get('Title','')), re.I):
            reasons.append('document_expression_in_question_title')
    if tags.intersection(config.get('application_tags', [])):
        reasons.append('application_document_tag')
    if tags.intersection(config['writing_tags']):
        reasons.append('registered_writing_tag')
    # General fiction and workplace questions are retained in the full archive,
    # but are not silently selected as application-writing evidence.
    if not reasons:
        return [], []
    intents = [key for key, pattern in config['intents'].items()
               if re.search(pattern, text, re.I)]
    return reasons, intents or ['writing_advice_unclassified']


def connect(root):
    db = sqlite3.connect(root / 'corpus.sqlite3')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=NORMAL')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS posts (
        site TEXT NOT NULL, id INTEGER NOT NULL, type INTEGER NOT NULL,
        parent INTEGER, author INTEGER, title TEXT, selected INTEGER NOT NULL,
        attrs BLOB NOT NULL, reasons TEXT NOT NULL, intents TEXT NOT NULL,
        PRIMARY KEY(site,id));
      CREATE INDEX IF NOT EXISTS posts_parent ON posts(site,parent);
      CREATE INDEX IF NOT EXISTS posts_selected ON posts(selected,type);
      CREATE TABLE IF NOT EXISTS users (site TEXT, id INTEGER, attrs BLOB,
        PRIMARY KEY(site,id));
      CREATE TABLE IF NOT EXISTS imports (site TEXT PRIMARY KEY, archive_sha256 TEXT,
        state TEXT, posts INTEGER, users INTEGER, at TEXT);
    ''')
    return db


def small_read(root, url, session, *, max_bytes=4*1024**2):
    key = sha256(url.encode()).hexdigest()
    receipt_path = root / 'receipts' / (key + '.json')
    if receipt_path.exists():
        receipt = load(receipt_path, {})
        data = (root / 'receipts' / (key + '.body')).read_bytes()
        if sha256(data).hexdigest() != receipt['sha256']:
            raise ValueError('Cached receipt hash mismatch')
        if receipt['status'] != 200:
            raise ValueError('Cached HTTP denial/error: ' + str(receipt['status']))
        return data, receipt
    if urlsplit(url).hostname not in {'archive.org', 'meta.stackexchange.com'}:
        raise ValueError('Unregistered metadata domain')
    response = session.get(url, timeout=(10, 45), stream=True)
    chunks, size = [], 0
    for part in response.iter_content(65536):
        size += len(part)
        if size > max_bytes:
            raise ValueError('Metadata byte ceiling')
        chunks.append(part)
    data = b''.join(chunks)
    receipt = {'url': url, 'final_url': response.url, 'status': response.status_code,
               'at': now(), 'bytes': size, 'sha256': sha256(data).hexdigest(),
               'access_scope': 'public_unauthenticated_archive',
               'headers': {k: v for k, v in response.headers.items()
                           if k.lower() in {'content-type', 'content-length', 'retry-after'}}}
    (root / 'receipts' / (key + '.body')).write_bytes(data)
    atomic_json(receipt_path, receipt)
    if response.status_code != 200:
        raise ValueError('HTTP ' + str(response.status_code) + ': ' + url)
    return data, receipt


def verify_archive(path, expected):
    hashes = {'md5': md5(), 'sha1': sha1(), 'sha256': sha256()}
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            for digest in hashes.values():
                digest.update(chunk)
    result = {name: digest.hexdigest() for name, digest in hashes.items()}
    if path.stat().st_size != int(expected['size']):
        raise ValueError('Archive size differs from metadata')
    for name in ('md5', 'sha1'):
        if expected.get(name) and result[name] != expected[name]:
            raise ValueError('Archive ' + name + ' differs from metadata')
    return result


def download(root, item, file, session, robot):
    name = file['name']
    url = 'https://archive.org/download/' + quote(item) + '/' + quote(name, safe='/')
    if not robot.can_fetch(UA, url):
        raise ValueError('Archive robots disallows file')
    target = root / 'archives' / Path(name).name
    receipt_path = target.with_suffix('.receipt.json')
    if target.exists():
        hashes = verify_archive(target, file)
        receipt = load(receipt_path, {})
        if not receipt:
            raise ValueError('Archive receipt absent')
        return target, receipt
    previous = load(receipt_path, {})
    if previous.get('status') in {401,403,429}:
        raise ValueError('Previously held provider denial; no automatic retry')
    if shutil.disk_usage(root).free < int(file['size'])*5 + 1024**3:
        raise ValueError('Shared cache reserve insufficient for archive and database')
    temporary = target.with_suffix('.partial')
    # A partial anonymous download restarts; authenticated or denial retries do not occur.
    response = session.get(url, timeout=(15, 90), stream=True)
    if response.status_code != 200:
        receipt = {'url': url, 'final_url': response.url, 'status': response.status_code, 'at': now()}
        atomic_json(receipt_path, receipt)
        raise ValueError('Download HTTP ' + str(response.status_code))
    total = 0
    expected_size = int(file['size'])
    with temporary.open('wb') as stream:
        for part in response.iter_content(1024*1024):
            total += len(part)
            if total > expected_size:
                raise ValueError('Archive exceeds metadata byte count')
            stream.write(part)
    hashes = verify_archive(temporary, file)
    temporary.replace(target)
    receipt = {'url': url, 'final_url': response.url, 'status': response.status_code,
               'at': now(), 'path': str(target.absolute()), 'resolved_path': str(target.resolve()),
               'bytes': total, **hashes, 'metadata_file': file,
               'publisher_role': 'community_redistributor', 'access_scope': 'public_no_credentials',
               'checksum_basis': 'Internet Archive item metadata; authenticity is not first-party attestation'}
    atomic_json(receipt_path, receipt)
    return target, receipt


def archive_rows(archive, member):
    """Stream only an allowlisted XML member; never extract archive paths."""
    if member not in {'Posts.xml', 'Users.xml'}:
        raise ValueError('Unexpected XML member')
    process = subprocess.Popen(['7z', 'x', '-so', str(archive), member],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _, node in etree.iterparse(process.stdout, events=('end',), tag='row',
                                      resolve_entities=False, no_network=True, huge_tree=True):
            yield dict(node.attrib)
            node.clear()
            while node.getprevious() is not None:
                del node.getparent()[0]
        error = process.stderr.read().decode('utf-8', 'replace')
        if process.wait() != 0:
            raise ValueError('7z extraction failed: ' + error[:300])
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()
        process.stdout.close()
        process.stderr.close()


def packed(value):
    return zlib.compress(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode(), 6)


def unpacked(value):
    return json.loads(zlib.decompress(value))


def rescope(root, db, config):
    """Reclassify retained questions when lexical policy changes; no redownload."""
    signature = sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
    prior = load(root/'scope-policy.json', {})
    if prior.get('sha256') == signature:
        return
    count = 0
    for site, ident, data in db.execute('SELECT site,id,attrs FROM posts WHERE type=1'):
        reasons, intents = question_scope(site,unpacked(data),config)
        db.execute('UPDATE posts SET selected=?,reasons=?,intents=? WHERE site=? AND id=?',
                   (bool(reasons),json.dumps(reasons),json.dumps(intents),site,ident))
        count += 1
        if count % 5000 == 0:
            db.commit()
    db.commit()
    atomic_json(root/'scope-policy.json', {'sha256':signature,'at':now(),'questions_reclassified':count})


def ingest(db, site, archive, receipt, config):
    sid = site['id']
    previous = db.execute('SELECT archive_sha256,state FROM imports WHERE site=?', (sid,)).fetchone()
    if previous == (receipt['sha256'], 'complete'):
        return {'site': sid, 'reused_completed_import': True}
    if previous and previous[0] != receipt['sha256']:
        raise ValueError('Use a new root for another source revision')
    db.execute('INSERT OR REPLACE INTO imports VALUES (?,?,?,?,?,?)',
               (sid, receipt['sha256'], 'importing', 0, 0, now()))
    db.commit()
    count = 0
    seen = set()
    for attrs in archive_rows(archive, 'Posts.xml'):
        kind = int(attrs.get('PostTypeId', '0'))
        if kind not in {1, 2}:
            continue
        ident = int(attrs['Id'])
        if ident in seen:
            previous_attrs = db.execute('SELECT attrs FROM posts WHERE site=? AND id=?', (sid,ident)).fetchone()
            if unpacked(previous_attrs[0]) != attrs:
                raise ValueError('Conflicting duplicate post identity in archive')
            continue
        seen.add(ident)
        reasons, intents = question_scope(sid, attrs, config) if kind == 1 else ([], [])
        db.execute('INSERT OR REPLACE INTO posts VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (sid, ident, kind, int(attrs['ParentId']) if attrs.get('ParentId') else None,
                    int(attrs['OwnerUserId']) if attrs.get('OwnerUserId') else None,
                    attrs.get('Title'), bool(reasons), packed(attrs), json.dumps(reasons), json.dumps(intents)))
        count += 1
        if count % 5000 == 0:
            db.execute('UPDATE imports SET posts=?,at=? WHERE site=?', (count, now(), sid))
            db.commit()
            atomic_json(ROOT_FOR_STATUS / 'progress.json', {'at': now(), 'site': sid, 'posts_imported': count})
    users = 0
    for attrs in archive_rows(archive, 'Users.xml'):
        # The original Users.xml remains in the verified archive. The working
        # attribution table needs no biographies, locations or external links.
        db.execute('INSERT OR REPLACE INTO users VALUES (?,?,?)',
                   (sid, int(attrs['Id']), packed({key:attrs[key] for key in ('Id','DisplayName') if key in attrs})))
        users += 1
        if users % 10000 == 0:
            db.commit()
    db.execute('UPDATE imports SET state=?,posts=?,users=?,at=? WHERE site=?',
               ('complete', count, users, now(), sid))
    db.commit()
    return {'site': sid, 'posts': count, 'users': users, 'reused_completed_import': False}


ROOT_FOR_STATUS = ROOT


def existing_keys(paths):
    ids, hashes = set(), set()
    for path in paths:
        if not path.exists():
            continue
        rows = json.loads(path.read_text())
        for row in rows:
            url = row.get('url', '')
            domain = urlsplit(url).hostname or ''
            domain = 'writing.stackexchange.com' if domain == 'writers.stackexchange.com' else domain
            match = re.search(r'/(?:questions|a)/(\d+)', urlsplit(url).path)
            native = row.get('native_id') or (match.group(1) if match else None)
            if native and domain.endswith('stackexchange.com'):
                ids.add((domain, str(native)))
            for key in ('text_sha256',):
                if row.get(key): hashes.add(row[key])
            for key in ('text', 'text_ref', 'raw'):
                if isinstance(row.get(key), dict) and row[key].get('sha256'):
                    hashes.add(row[key]['sha256'])
    return ids, hashes


def make_record(root, site, attrs, parent, reasons, intents, user, receipt):
    kind = 'question' if attrs['PostTypeId'] == '1' else 'answer'
    body = plain(attrs.get('Body', ''))
    if not body:
        return None
    if kind == 'answer' and (not parent or not plain(parent.get('Body', ''))):
        return None
    ident = str(attrs['Id'])
    qid = ident if kind == 'question' else str(attrs['ParentId'])
    domain = site['domain']
    text = store_source_bytes(body.encode(), root / 'bodies', suffix='.txt')
    original = store_source_bytes(attrs['Body'].encode(), root / 'bodies', suffix='.html')
    author_id = attrs.get('OwnerUserId')
    record = {
        'document_id': 'stackexchange:' + site['id'] + ':' + kind + ':' + ident,
        'source': 'stackexchange-' + site['id'], 'native_id': ident, 'question_id': qid,
        'url': 'https://' + domain + ('/questions/' if kind == 'question' else '/a/') + ident,
        'title': html.unescape((parent or attrs).get('Title', '')),
        'text_ref': text, 'text_sha256': text['sha256'], 'original_body_html': original,
        'body_scope': 'full_' + kind, 'source_role': 'question_context' if kind == 'question' else 'public_advice',
        'publisher_role': 'community', 'retrieval_intents': intents, 'selection_reasons': reasons,
        'evidence_scope': ('application_document_advice_candidate'
                           if any(reason in reasons for reason in ('document_expression_in_question_title','application_document_tag'))
                           else 'document_context_only_candidate'
                           if 'application_or_professional_document_expression' in reasons
                           else 'general_writing_advice_candidate'),
        'tags': re.findall(r'<([^>]+)>', (parent or attrs).get('Tags', '')),
        'author': {'user_id': author_id, 'display_name': user.get('DisplayName') or attrs.get('OwnerDisplayName'),
                   'link': 'https://' + domain + '/users/' + author_id if author_id else None,
                   'attribution_state': 'named' if user.get('DisplayName') or attrs.get('OwnerDisplayName') else 'deleted_or_unresolved'},
        'content_license': attrs.get('ContentLicense'),
        'license_basis': 'per_post_ContentLicense' if attrs.get('ContentLicense') else 'archive_declaration_only',
        'creation_date': attrs.get('CreationDate'), 'last_edit_date': attrs.get('LastEditDate'),
        'community_score': int(attrs.get('Score', '0')),
        'community_answer_accepted': str((parent or {}).get('AcceptedAnswerId', '')) == ident if kind == 'answer' else None,
        'raw_response': receipt, 'original_xml_member': 'Posts.xml',
        'source_row_locator': {'path':str((root/'corpus.sqlite3').absolute()), 'table':'posts',
                               'key':{'site':site['id'],'id':int(ident)},
                               'encoding':'zlib-compressed JSON in attrs column'},
        'original_row_attributes_sha256': sha256(json.dumps(attrs, sort_keys=True).encode()).hexdigest(),
        'original_question': {'id': qid, 'document_id': 'stackexchange:' + site['id'] + ':question:' + qid,
                              'url': 'https://' + domain + '/questions/' + qid,
                              'context': 'Public community question, not an official application prompt.'},
        'exact_prompt': None, 'semantic_review': {'state': 'pending'},
        'accepted_experience': False, 'approved_pattern': False,
        'limitations': ['Lexical selection requires semantic review.',
                       'Community acceptance and score do not identify applicant selection.',
                       'September 2026 public redistribution is not a first-party authenticated release.']}
    return record


def export_new_records(root, rows):
    """Write only previously unseen source posts, compressed for downstream staging."""
    target = root/'new-records.jsonl.gz'
    temporary = root/'new-records.jsonl.gz.partial'
    count = 0
    new_ids = {row['document_id'] for row in rows if not any(row['prior_library_overlap'].values())}
    required_parents = {row['original_question']['document_id'] for row in rows
                        if row['document_id'] in new_ids and row['body_scope']=='full_answer'} - new_ids
    with temporary.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0) as stream:
            for row in rows:
                if any(row['prior_library_overlap'].values()):
                    continue
                stream.write((json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n').encode())
                count += 1
    temporary.replace(target)
    contexts = root/'required-existing-parent-contexts.jsonl.gz'
    with contexts.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0) as stream:
            for row in rows:
                if row['document_id'] in required_parents:
                    stream.write((json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n').encode())
    return {'path':str(target.absolute()),'codec':'gzip','records':count,
            'sha256':sha256(target.read_bytes()).hexdigest(),'stored_bytes':target.stat().st_size,
            'required_existing_parent_contexts':{'path':str(contexts.absolute()),'records':len(required_parents),
                'sha256':sha256(contexts.read_bytes()).hexdigest(),
                'role':'question_context_already_present_in_previous_libraries; do not count as new'}}


def export(root, db, config, receipts):
    ids, hashes = existing_keys([
        CACHE/'question-knowledge-v1/records.json',
        CACHE/'writing-foundations-v1/open-guides/records.json'])
    signature = sha256(json.dumps({'export_schema':'community-bulk-v2','config':config,
        'archives':{key:value['sha256'] for key,value in receipts.items()},
        'existing_ids':sorted(ids), 'existing_hashes':sorted(hashes)},sort_keys=True).encode()).hexdigest()
    checkpoint = load(root/'export-checkpoint.json', {})
    if checkpoint.get('signature') == signature and (root/'records.json').exists():
        if sha256((root/'records.json').read_bytes()).hexdigest() == checkpoint.get('records_sha256'):
            prior = load(root/'status.json', {})
            new_export = prior.get('new_records_export') or {}
            refs = [new_export,new_export.get('required_existing_parent_contexts') or {}]
            if any(not ref.get('path') or not Path(ref['path']).exists()
                   or sha256(Path(ref['path']).read_bytes()).hexdigest()!=ref.get('sha256') for ref in refs):
                prior['new_records_export'] = export_new_records(root,load(root/'records.json',[]))
            return dict(prior,reused_verified_export=True)
    counts, samples, rows = Counter(), [], []
    site_counts = {}
    for site in config['sites']:
        sid = site['id']
        receipt = receipts.get(sid)
        if not receipt: continue
        per_site = Counter()
        query = '''SELECT q.attrs,q.attrs,q.reasons,q.intents,u.attrs,q.id AS postid
          FROM posts q LEFT JOIN users u ON u.site=q.site AND u.id=q.author
          WHERE q.site=? AND q.selected=1 AND q.type=1
          UNION ALL
          SELECT p.attrs,q.attrs,q.reasons,q.intents,u.attrs,p.id AS postid
          FROM posts q JOIN posts p ON p.site=q.site AND p.parent=q.id AND p.type=2
          LEFT JOIN users u ON u.site=p.site AND u.id=p.author
          WHERE q.site=? AND q.selected=1 AND q.type=1 ORDER BY postid'''
        for packed_attrs, packed_parent, reasons, intents, user_attrs, _ in db.execute(query, (sid,sid)):
            attrs, parent = unpacked(packed_attrs), unpacked(packed_parent)
            record = make_record(root, site, attrs, parent, json.loads(reasons), json.loads(intents),
                                 unpacked(user_attrs) if user_attrs else {}, receipt)
            if not record:
                counts['excluded_empty_text'] += 1
                continue
            previous_id = (site['domain'], record['native_id']) in ids
            previous_text = record['text_sha256'] in hashes
            record['prior_library_overlap'] = {'same_site_post_id': previous_id, 'same_text_hash': previous_text}
            if previous_id or previous_text:
                counts['existing_library_overlap'] += 1
            else:
                counts['new_against_prior_libraries'] += 1
            counts['selected_documents'] += 1
            counts[record['body_scope']] += 1
            counts[record['evidence_scope']] += 1
            per_site[record['body_scope']] += 1
            per_site[record['evidence_scope']] += 1
            if record['author']['attribution_state'] != 'named': counts['unresolved_author'] += 1
            if not record['content_license']: counts['license_missing_on_post'] += 1
            rows.append(record)
        site_counts[sid] = {'retained_all_posts': db.execute('SELECT COUNT(*) FROM posts WHERE site=?', (sid,)).fetchone()[0],
                            **dict(per_site)}
    # Content identity is measured independently from post identity; duplicate texts remain attributed.
    parent_ids = {row['document_id'] for row in rows if row['body_scope'] == 'full_question'}
    if any(row['original_question']['document_id'] not in parent_ids
           for row in rows if row['body_scope'] == 'full_answer'):
        raise ValueError('Selected answer lacks its retained question')
    counts['unique_selected_texts'] = len({row['text_sha256'] for row in rows})
    counts['duplicate_selected_texts'] = len(rows) - counts['unique_selected_texts']
    for sid in site_counts:
        group = [r for r in rows if r['source'] == 'stackexchange-' + sid and r['body_scope'] == 'full_question']
        # Deterministic spread for a manually auditable scope sample.
        for row in sorted(group, key=lambda r: sha256(r['document_id'].encode()).hexdigest())[:12]:
            samples.append({k: row[k] for k in ['document_id','title','url','tags','retrieval_intents','selection_reasons']})
    atomic_json(root/'records.json', rows)
    new_export = export_new_records(root,rows)
    atomic_json(root/'scope-samples.json', samples)
    summary = {'at': now(), 'counts': dict(counts), 'sites': site_counts,
               'retained_all_posts': db.execute('SELECT COUNT(*) FROM posts').fetchone()[0],
               'source_snapshot': config['publication_date'], 'publisher_role': config['publisher_role'],
               'corpus_role': 'public_writing_advice_candidates',
               'accepted_experiences_added': 0, 'approved_patterns_added': 0,
               'comparison': ['question-knowledge-v1/records.json','writing-foundations-v1/open-guides/records.json'],
               'records_path': str(root/'records.json'), 'new_records_export':new_export,
               'complete_posts_sqlite': str(root/'corpus.sqlite3')}
    atomic_json(root/'status.json', summary)
    atomic_json(root/'export-checkpoint.json', {'signature':signature,'at':now(),
                'records_sha256':sha256((root/'records.json').read_bytes()).hexdigest()})
    return summary


def main(argv=None):
    global ROOT_FOR_STATUS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--download-only', action='store_true')
    args = parser.parse_args(argv)
    root = checked_root(args.root)
    root.mkdir(parents=True, exist_ok=True)
    ROOT_FOR_STATUS = root
    for sub in ('archives','receipts','bodies'): (root/sub).mkdir(exist_ok=True)
    config = load(args.config, {})
    session = requests.Session()
    session.headers.update({'User-Agent': UA})
    with single_writer(root):
        robots, robots_receipt = small_read(root, 'https://archive.org/robots.txt', session)
        robot = RobotFileParser(); robot.parse(robots.decode().splitlines())
        metadata = {}
        for item in (config['archive_item'], config['official_comparison_item']):
            raw, receipt = small_read(root, 'https://archive.org/metadata/' + item, session)
            metadata[item] = json.loads(raw)
            atomic_json(root/(item+'-metadata.json'), {'metadata': metadata[item], 'receipt': receipt})
        # Record first-party access state without using any session credentials.
        access_url = 'https://meta.stackexchange.com/users/data-dump-access/current'
        try:
            _, access = small_read(root, access_url, session)
            atomic_json(root/'first-party-access.json', {'receipt': access,
                'state': 'login_required' if '/login' in access['final_url'] else 'inspect_required',
                'credentials_used': False})
        except (ValueError,requests.RequestException) as error:
            atomic_json(root/'first-party-access.json', {'state':'held', 'reason':str(error), 'credentials_used':False})
        files = metadata[config['archive_item']]['files']
        receipts = {}
        failures = []
        db = connect(root)
        for site in config['sites']:
            matches = [f for f in files if f['name'].endswith('/'+site['archive_domain']+'.7z')]
            if len(matches) != 1: raise ValueError('Ambiguous archive member for '+site['id'])
            try:
                archive, receipt = download(root, config['archive_item'], matches[0], session, robot)
                receipts[site['id']] = receipt
                atomic_json(root/'download-progress.json', {'at':now(),'site':site['id'],'state':'verified_archive','receipt':receipt})
                if not args.download_only:
                    result = ingest(db, site, archive, receipt, config)
                    print(json.dumps(result), flush=True)
            except (ValueError, requests.RequestException, etree.XMLSyntaxError) as error:
                failures.append({'site':site['id'],'at':now(),'reason':str(error)})
                atomic_json(root/'failures.json', failures)
        if not args.download_only:
            rescope(root,db,config)
        summary = export(root, db, config, receipts) if not args.download_only else {'downloads':len(receipts)}
        summary['failures'] = failures
        atomic_json(root/'status.json', summary)
        print(json.dumps(summary), flush=True)
        db.close()


if __name__ == '__main__':
    main()
