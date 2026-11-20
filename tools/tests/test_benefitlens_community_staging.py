from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import zlib

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stage_benefitlens_community_documents as module
from benefitlens_source_storage import store_source_bytes


def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'CACHE', tmp_path)
    body = '<p>How should I explain my research experience?</p>'
    original = store_source_bytes(body.encode(), tmp_path, suffix='.html')
    text = store_source_bytes(module.plain(body).encode(), tmp_path, suffix='.txt')
    archive = tmp_path/'source.7z'
    archive.write_bytes(b'verified archive bytes')
    receipt = {'path': str(archive), 'sha256': sha256(archive.read_bytes()).hexdigest(),
               'url': 'https://archive.org/download/release/site.7z', 'at': '2026-11-19T00:00:00Z'}
    attrs = {'Id': '1', 'Body': body, 'PostTypeId': '1'}
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE posts(site TEXT,id INTEGER,attrs BLOB)')
    db.execute('INSERT INTO posts VALUES(?,?,?)', ('academia', 1, zlib.compress(json.dumps(attrs).encode())))
    row = {'document_id': 'stackexchange:academia:question:1',
           'source': 'stackexchange-academia', 'url': 'https://academia.stackexchange.com/questions/1',
           'source_role': 'question_context', 'evidence_scope': 'application_document_advice_candidate',
           'original_body_html': original, 'text_ref': text, 'raw_response': receipt,
           'source_row_locator': {'table': 'posts', 'key': {'site': 'academia', 'id': 1}},
           'original_row_attributes_sha256': sha256(json.dumps(attrs, sort_keys=True).encode()).hexdigest(),
           'author': {'user_id': '2', 'display_name': 'A student'}, 'content_license': 'CC BY-SA 4.0'}
    return row, db


def test_staging_keeps_archive_origin_license_and_question_role(tmp_path, monkeypatch):
    row, db = fixture(tmp_path, monkeypatch)
    result = module.stage_record(row, db, {})
    assert result['original']['url'].startswith('https://archive.org/')
    assert result['original']['capture_mode'] == 'archive_record_body_field'
    assert result['url'] == row['url'] and result['material_only']
    assert result['reuse_license'] == 'CC BY-SA 4.0'
    assert result['author']['key'] == 'stackexchange:academia:2'
    assert result['discovery']['document_id'] == row['document_id']
    assert not result['accepted_experience'] and not result['approved_pattern']


def test_staging_rejects_body_that_differs_from_original_xml_row(tmp_path, monkeypatch):
    row, db = fixture(tmp_path, monkeypatch)
    attrs = {'Id': '1', 'Body': '<p>A different application.</p>', 'PostTypeId': '1'}
    db.execute('UPDATE posts SET attrs=?', (zlib.compress(json.dumps(attrs).encode()),))
    with pytest.raises(ValueError, match='Archive row'):
        module.stage_record(row, db, {})


def test_staging_rejects_changed_archive_even_with_valid_post_blobs(tmp_path, monkeypatch):
    row, db = fixture(tmp_path, monkeypatch)
    Path(row['raw_response']['path']).write_bytes(b'changed archive bytes')
    with pytest.raises(ValueError, match='archive checksum'):
        module.stage_record(row, db, {})
