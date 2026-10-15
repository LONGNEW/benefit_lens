from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import data_bundle as bundle
import project


def fake_bundle(tmp_path):
    source = tmp_path / 'live'
    (source / 'pipeline/data/raw').mkdir(parents=True)
    (source / 'pipeline/data/raw/example.bin').write_bytes(b'original source\x00\x01')
    (source / 'pipeline/data/collector.lock').write_text('running')
    (source / 'pipeline/data/status.json').write_text(json.dumps({'path': str(source / 'pipeline/data/raw/example.bin')}))
    with sqlite3.connect(source / 'pipeline/data/frontier.sqlite') as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('CREATE TABLE document(payload TEXT)')
        conn.execute('INSERT INTO document VALUES(?)', (json.dumps({'raw_path': str(source / 'pipeline/data/raw/example.bin')}),))
        conn.commit()
        output = tmp_path / 'bundle'
        output.mkdir()
        summary = bundle.archive_data(source, output)
    for name in ['database.dump', 'schema.sql', 'table-counts.json']:
        (output / name).write_text('{}')
    names = ['database.dump', 'schema.sql', 'table-counts.json', 'capture-scope.json', 'files.tar.zst', 'files.jsonl']
    manifest = {'format': bundle.FORMAT, 'completed_at': bundle.now(), 'source_root': str(source), 'files': summary,
                'artifacts': {name: {'bytes': (output / name).stat().st_size, 'sha256': bundle.sha(output / name)} for name in names}}
    bundle.write_json(output / 'manifest.json', manifest)
    return source, output


@pytest.mark.parametrize('name', ['/etc/passwd', '../outside', 'pipeline/data/../../../outside', 'web/a', 'pipeline/data', 'pipeline\\data\\bad'])
def test_archive_paths_cannot_escape(name):
    with pytest.raises(ValueError):
        bundle.safe_member(name)


def test_archive_roundtrip_wal_snapshot_and_relocation(tmp_path):
    source, output = fake_bundle(tmp_path)
    manifest, inventory = bundle.validate(output)
    assert not any(name.endswith(('-wal', '-shm', '.lock')) for name in inventory)
    assert inventory['pipeline/data/frontier.sqlite']['capture'] == 'sqlite_online_backup'
    restored = tmp_path / 'restored'
    restored.mkdir()
    result = bundle.read_archive(output, inventory, restored)
    assert result['verified_files'] == 3
    assert (restored / 'pipeline/data/raw/example.bin').read_bytes() == b'original source\x00\x01'
    bundle.relocate_local(restored / 'pipeline/data', str(source), str(restored))
    with sqlite3.connect(restored / 'pipeline/data/frontier.sqlite') as conn:
        value = json.loads(conn.execute('SELECT payload FROM document').fetchone()[0])
        assert value['raw_path'] == str(restored / 'pipeline/data/raw/example.bin')
    assert json.loads((restored / 'pipeline/data/status.json').read_text())['path'].startswith(str(restored))
    # The source is untouched by relocation.
    assert json.loads((source / 'pipeline/data/status.json').read_text())['path'].startswith(str(source))


def test_hash_tampering_is_rejected(tmp_path):
    _, output = fake_bundle(tmp_path)
    (output / 'database.dump').write_text('tampered')
    with pytest.raises(ValueError, match='Checksum'):
        bundle.validate(output)


def test_existing_files_are_never_overwritten(tmp_path):
    _, output = fake_bundle(tmp_path)
    _, inventory = bundle.validate(output)
    restored = tmp_path / 'restored'
    restored.mkdir()
    bundle.read_archive(output, inventory, restored)
    with pytest.raises(FileExistsError):
        bundle.read_archive(output, inventory, restored)


def test_symlink_sources_are_rejected(tmp_path):
    source = tmp_path / 'source'
    (source / 'pipeline/data').mkdir(parents=True)
    (source / 'pipeline/data/link').symlink_to('/etc/passwd')
    output = tmp_path / 'bundle'
    output.mkdir()
    with pytest.raises(ValueError, match='Symlink'):
        bundle.archive_data(source, output)


def test_relocation_only_changes_path_values():
    value = {'path': '/old project/pipeline/data/a', 'url': 'https://example.com/old project/a',
             'quote': 'Read /old project/a', 'nested': ['/old project/b', '/old project-other/a']}
    result = bundle.relocate(value, '/old project', '/new project')
    assert result['path'] == '/new project/pipeline/data/a'
    assert result['url'] == value['url']
    assert result['quote'] == value['quote']
    assert result['nested'] == ['/new project/b', '/old project-other/a']


def test_restore_refuses_nonempty_database(tmp_path, monkeypatch):
    _, output = fake_bundle(tmp_path)
    monkeypatch.setattr(project, 'settings', lambda: {'POSTGRES_USER': 'test', 'POSTGRES_DB': 'test'})
    monkeypatch.setattr(project, 'container', lambda: 'isolated-test-container')
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, *args): return self
        def fetchone(self): return [1]
    monkeypatch.setattr(project, 'connect', Conn)
    with pytest.raises(ValueError, match='nonempty target DB'):
        bundle.restore(output, database_only=True)


def test_legacy_port_is_reserved(tmp_path, monkeypatch):
    monkeypatch.setattr(project, 'ROOT', tmp_path)
    (tmp_path / '.env').write_text('COMPOSE_PROJECT_NAME=benefit-lens\nBENEFITLENS_POSTGRES_PORT=55432\nPOSTGRES_PASSWORD=test\n')
    with pytest.raises(ValueError, match='reserved'):
        project.settings()
