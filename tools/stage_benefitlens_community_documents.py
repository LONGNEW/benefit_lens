"""Validate archive-derived posts and stage them for the common document schema."""
import argparse
from collections import Counter
import gzip
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
import zlib

from benefitlens_source_storage import read_source_bytes, source_fetched_at
from collect_benefitlens_community_bulk import plain
from run_benefitlens_campaign import CACHE, KST, atomic_json, checked_root, single_writer
from datetime import datetime


def file_hash(path):
    checksum = sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024**2), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def checked_ref(ref):
    if not Path(ref['path']).resolve().is_relative_to(CACHE.resolve()):
        raise ValueError('Community source must remain in the shared dataset cache')
    raw = read_source_bytes(ref['path'], storage=ref.get('storage'))
    if sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('Community document checksum mismatch')
    return raw


def stage_record(row, db, verified_archives):
    body = checked_ref(row['original_body_html'])
    text = checked_ref(row['text_ref'])
    if plain(body.decode()).encode() != text:
        raise ValueError('Archived HTML and projected text differ')
    locator = row['source_row_locator']
    if locator['table'] != 'posts':
        raise ValueError('Unsupported source row locator')
    key = locator['key']
    found = db.execute('SELECT attrs FROM posts WHERE site=? AND id=?',
                       (key['site'], key['id'])).fetchone()
    if not found:
        raise ValueError('Archived source row is absent')
    attrs = json.loads(zlib.decompress(found[0]))
    checksum = sha256(json.dumps(attrs, sort_keys=True).encode()).hexdigest()
    if checksum != row['original_row_attributes_sha256'] or attrs.get('Body', '').encode() != body:
        raise ValueError('Archive row and preserved post body differ')
    receipt = row['raw_response']
    archive = Path(receipt['path'])
    if not archive.resolve().is_relative_to(CACHE.resolve()):
        raise ValueError('Archive must remain in the shared dataset cache')
    if str(archive) not in verified_archives:
        verified_archives[str(archive)] = file_hash(archive)
    if verified_archives[str(archive)] != receipt['sha256']:
        raise ValueError('Preserved archive checksum mismatch')
    author = row.get('author') or {}
    person = ({'key': 'stackexchange:' + key['site'] + ':' + str(author['user_id']),
               'name': author.get('display_name'), 'link': author.get('link')}
              if author.get('user_id') else None)
    original = dict(row['original_body_html'], url=receipt['url'],
        at=source_fetched_at(receipt), capture_mode='archive_record_body_field',
        archive_receipt=receipt, source_row_locator=locator,
        original_row_attributes_sha256=checksum)
    return {'state': 'body_staged', 'url': row['url'], 'kind': 'html',
        'original': original, 'text': row['text_ref'], 'native_id': row['document_id'],
        'material_only': True,
        'source_definition': {'id': row['source']},
        'document_role': row['evidence_scope'] + ':' + row['source_role'],
        'author': person, 'author_display_name': author.get('display_name'),
        'author_evidence': 'archived_post_owner; cross_site_identity_unconfirmed',
        'reuse_license': row.get('content_license'),
        'document_finality': 'public_community_post; applicant_submission_unconfirmed',
        'discovery': {key: row.get(key) for key in (
            'document_id', 'original_question', 'source_row_locator',
            'retrieval_intents', 'selection_reasons', 'evidence_scope',
            'publisher_role', 'raw_response', 'license_basis', 'prior_library_overlap',
            'community_answer_accepted', 'community_score', 'limitations')},
        'accepted_experience': False, 'approved_pattern': False}


def run(root):
    root = checked_root(root)
    target = root/'staging'
    target.mkdir(exist_ok=True)
    with single_writer(target):
        export = json.loads((root/'new-export.json').read_text())
        refs = [export, export['required_existing_parent_contexts']]
        rows, seen = [], set()
        for ref in refs:
            path = Path(ref['path'])
            if not path.resolve().is_relative_to(root.resolve()) or file_hash(path) != ref['sha256']:
                raise ValueError('New-record export checksum or location mismatch')
            count = 0
            with gzip.open(path, 'rt', encoding='utf8') as stream:
                for line in stream:
                    record = json.loads(line)
                    if record['document_id'] in seen:
                        raise ValueError('Duplicate source identity in staging input')
                    seen.add(record['document_id'])
                    rows.append(record)
                    count += 1
            if count != ref['records']:
                raise ValueError('Export row count differs from receipt')
        for row in rows:
            if row['original_question']['document_id'] not in seen:
                raise ValueError('A staged answer has no question context')
            if urlsplit(row['url']).hostname not in (
                    'academia.stackexchange.com', 'writing.stackexchange.com',
                    'writers.stackexchange.com', 'workplace.stackexchange.com'):
                raise ValueError('Unexpected community document host')
        archives = {}
        index = []
        with sqlite3.connect('file:' + str(root/'corpus.sqlite3') + '?mode=ro', uri=True) as db:
            for number, row in enumerate(rows, 1):
                index.append(stage_record(row, db, archives))
                if number % 2000 == 0:
                    print(json.dumps({'state': 'validating', 'documents': number}), flush=True)
        identifiers = {row['discovery']['document_id']: 'experience-doc-' + sha256(
            (row['url'] + '\n' + row['original']['sha256']).encode()).hexdigest()[:32]
            for row in index}
        for row in index:
            question = row['discovery']['original_question']
            question['pipeline_document_id'] = identifiers[question['document_id']]
        atomic_json(target/'index.json', index)
        status = {'at_kst': datetime.now(KST).isoformat(), 'state': 'ready_for_document_handoff',
                  'index': str(target/'index.json'), 'staged_documents': len(index),
                  'new_document_records': export['records'],
                  'existing_question_context_records': refs[1]['records'],
                  'verified_archives': archives,
                  'roles': dict(Counter(row['document_role'] for row in index)),
                  'accepted_experiences_added': 0, 'approved_patterns_added': 0,
                  'network_requests': 0, 'model_called': False}
        atomic_json(target/'status.json', status)
        print(json.dumps(status, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=CACHE/'community-bulk-20261119')
    run(parser.parse_args().root)
