"""Private, checksummed PostgreSQL and raw-file bundles with an empty-target restore."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import quote

import project

FORMAT = 'benefitlens-private-bundle-v1'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def write_json(path: Path, data) -> None:
    with path.open('x', encoding='utf-8') as out:
        json.dump(data, out, ensure_ascii=False, indent=2)
        out.write('\n')


def table_counts(conn) -> dict[str, int]:
    from psycopg import sql
    tables = conn.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname IN ('platform','benefitlens') ORDER BY 1,2").fetchall()
    return {f'{schema}.{table}': conn.execute(sql.SQL('SELECT count(*) FROM {}.{}').format(
        sql.Identifier(schema), sql.Identifier(table))).fetchone()[0] for schema, table in tables}


def pg_dump(container: str, snapshot: str, output: Path, user: str, database: str, schema_only: bool = False) -> None:
    command = ['docker', 'exec', container, 'pg_dump', '-U', user, '-d', database,
               '--no-owner', '--no-acl', '--snapshot', snapshot]
    command += ['--schema-only'] if schema_only else ['--format=custom', '--compress=1']
    with output.open('xb') as stream:
        subprocess.run(command, stdout=stream, check=True)


class HashReader:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        block = self.stream.read(size)
        self.digest.update(block)
        return block


def archive_data(source: Path, output: Path) -> dict:
    data = source / 'pipeline/data'
    if not data.is_dir():
        raise ValueError('Source pipeline/data is missing.')
    paths, skipped = [], []
    for path in sorted(data.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Symlink requires explicit review: {path.relative_to(source)}')
        if not path.is_file():
            continue
        if path.name.endswith(('-wal', '-shm', '.lock', '.part', '.tmp')):
            skipped.append(path.relative_to(source).as_posix())
        else:
            paths.append(path)
    total_bytes = sum(path.stat().st_size for path in paths)
    free = shutil.disk_usage(output).free
    if free < total_bytes * 1.1 + 4 * 1024**3:
        raise ValueError('Not enough space for a conservative uncompressed archive plus SQLite snapshots.')
    write_json(output / 'capture-scope.json', {'file_enumeration_finished_at': now(),
               'files_at_enumeration': len(paths), 'bytes_at_enumeration': total_bytes,
               'skipped_transient_files': skipped,
               'consistency': 'PostgreSQL is one exported snapshot. SQLite uses online backup per database. Files are captured afterward, not a global cross-store transaction.'})
    count = size_sum = 0
    last_log = time.monotonic()
    with (output / 'files.tar.zst').open('xb') as compressed, \
            (output / 'files.jsonl').open('x', encoding='utf-8') as inventory, \
            tempfile.TemporaryDirectory(prefix='sqlite-snapshot-', dir=output) as temporary:
        process = subprocess.Popen(['zstd', '-1', '-T2', '-q', '-c'], stdin=subprocess.PIPE, stdout=compressed)
        try:
            with tarfile.open(fileobj=process.stdin, mode='w|') as archive:
                for original in paths:
                    name = original.relative_to(source).as_posix()
                    path = original
                    sqlite_snapshot = False
                    if original.suffix in {'.sqlite', '.sqlite3', '.db'}:
                        with original.open('rb') as header:
                            sqlite_snapshot = header.read(16) == b'SQLite format 3\x00'
                    if sqlite_snapshot:
                        path = Path(temporary) / f'{count}.sqlite'
                        with sqlite3.connect(f'file:{quote(str(original))}?mode=ro', uri=True) as live, sqlite3.connect(path) as copy:
                            live.backup(copy, pages=4096, sleep=0.05)
                    before = path.stat()
                    info = tarfile.TarInfo(name)
                    info.size, info.mode, info.mtime = before.st_size, 0o600, int(before.st_mtime)
                    with path.open('rb') as stream:
                        reader = HashReader(stream)
                        archive.addfile(info, reader)
                    after = path.stat()
                    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                        raise ValueError(f'File changed during capture; retry a new bundle: {name}')
                    item = {'path': name, 'bytes': before.st_size, 'sha256': reader.digest.hexdigest(),
                            'capture': 'sqlite_online_backup' if sqlite_snapshot else 'file', 'captured_at': now()}
                    inventory.write(json.dumps(item, ensure_ascii=False) + '\n')
                    count += 1
                    size_sum += before.st_size
                    if sqlite_snapshot:
                        path.unlink()
                    if time.monotonic() - last_log >= 10:
                        print(json.dumps({'archived_files': count, 'total_files': len(paths), 'bytes': size_sum}), flush=True)
                        last_log = time.monotonic()
            process.stdin.close()
            if process.wait() != 0:
                raise ValueError('zstd compression failed.')
        except BaseException:
            process.kill()
            process.wait()
            raise
    return {'files': count, 'bytes_before_compression': size_sum, 'transient_files_skipped': len(skipped)}


def backup(source: Path, container: str, output: Path) -> dict:
    import psycopg
    source, output = source.resolve(), output.resolve()
    if output == source or output.is_relative_to(source):
        raise ValueError('Keep bundles outside the live source tree.')
    if not container:
        raise ValueError('Specify the exact source container.')
    if source == project.ROOT:
        dsn = project.environment()['DATABASE_URL']
    else:
        dsn = os.environ.get('SOURCE_DATABASE_URL')
        if not dsn:
            raise ValueError('Set SOURCE_DATABASE_URL privately for the live source database.')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    started = now()
    with psycopg.connect(dsn) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        snapshot = conn.execute('SELECT pg_export_snapshot()').fetchone()[0]
        source_id = conn.execute('SELECT system_identifier::text FROM pg_control_system()').fetchone()[0]
        # Prevent silently dumping another Docker container than the counted connection.
        user, database = conn.info.user, conn.info.dbname
        target_id = subprocess.run(['docker', 'exec', container, 'psql', '-U', user, '-d', database,
                                    '-Atc', 'SELECT system_identifier::text FROM pg_control_system()'],
                                   capture_output=True, text=True, check=True).stdout.strip()
        if source_id != target_id:
            raise ValueError('Source connection and source container are different PostgreSQL clusters.')
        counts = table_counts(conn)
        write_json(output / 'table-counts.json', counts)
        pg_dump(container, snapshot, output / 'database.dump', user, database)
        pg_dump(container, snapshot, output / 'schema.sql', user, database, schema_only=True)
        print(json.dumps({'postgres_snapshot_finished_at': now(), 'tables': len(counts), 'rows': sum(counts.values())}), flush=True)
    files = archive_data(source, output)
    names = ['database.dump', 'schema.sql', 'table-counts.json', 'capture-scope.json', 'files.tar.zst', 'files.jsonl']
    artifacts = {name: {'bytes': (output / name).stat().st_size, 'sha256': sha(output / name)} for name in names}
    manifest = {'format': FORMAT, 'started_at': started, 'completed_at': now(),
                'source_root': str(source), 'source_database': database, 'source_role': user,
                'postgres_major': 16, 'private_material': True, 'files': files, 'artifacts': artifacts}
    write_json(output / 'manifest.json', manifest)
    print(json.dumps({'complete': True, 'bundle': str(output), **files}, ensure_ascii=False), flush=True)
    return manifest


def safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if '\\' in name or path.is_absolute() or '..' in path.parts or path.parts[:2] != ('pipeline', 'data') or len(path.parts) < 3:
        raise ValueError(f'Unsafe archive path: {name}')
    return path


def validate(bundle: Path) -> tuple[dict, dict]:
    manifest = json.loads((bundle / 'manifest.json').read_text())
    if manifest['format'] != FORMAT or not manifest.get('completed_at'):
        raise ValueError('Incomplete or unsupported bundle.')
    required = {'database.dump', 'schema.sql', 'table-counts.json', 'capture-scope.json', 'files.tar.zst', 'files.jsonl'}
    if set(manifest['artifacts']) != required:
        raise ValueError('Unexpected bundle artifact list.')
    for name, expected in manifest['artifacts'].items():
        path = bundle / name
        if path.is_symlink() or path.stat().st_size != expected['bytes'] or sha(path) != expected['sha256']:
            raise ValueError(f'Checksum mismatch: {name}')
    files = {}
    with (bundle / 'files.jsonl').open() as inventory:
        for line in inventory:
            item = json.loads(line)
            safe_member(item['path'])
            if item['path'] in files:
                raise ValueError('Duplicate inventory path.')
            files[item['path']] = item
    if len(files) != manifest['files']['files']:
        raise ValueError('File inventory count mismatch.')
    return manifest, files


def read_archive(bundle: Path, files: dict, destination: Path | None = None) -> dict:
    seen, total, last_log = set(), 0, time.monotonic()
    process = subprocess.Popen(['zstd', '-d', '-q', '-c', str(bundle / 'files.tar.zst')], stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
            for member in archive:
                safe_member(member.name)
                if not member.isfile() or member.name in seen or member.name not in files:
                    raise ValueError(f'Unexpected archive member: {member.name}')
                expected = files[member.name]
                if member.size != expected['bytes']:
                    raise ValueError(f'Member size mismatch: {member.name}')
                target = destination / member.name if destination else None
                if target:
                    # Resolve before creating anything, rejecting existing symlink ancestors.
                    if not target.resolve().is_relative_to(destination.resolve()) or any(p.is_symlink() for p in [target, *target.parents]):
                        raise ValueError('Restore path escapes the destination.')
                    target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                source = archive.extractfile(member)
                output = target.open('xb') if target else io.BytesIO()
                with source, output:
                    while block := source.read(1024 * 1024):
                        digest.update(block)
                        if target:
                            output.write(block)
                if digest.hexdigest() != expected['sha256']:
                    raise ValueError(f'Member checksum mismatch: {member.name}')
                if target:
                    target.chmod(0o600)
                seen.add(member.name)
                total += member.size
                if time.monotonic() - last_log >= 10:
                    print(json.dumps({'checked_files': len(seen), 'total_files': len(files), 'bytes': total}), flush=True)
                    last_log = time.monotonic()
        # Consume tar padding so the decompressor does not block on a full pipe.
        while process.stdout.read(1024 * 1024):
            pass
        if process.wait() != 0 or seen != set(files):
            raise ValueError('Truncated archive or missing inventory members.')
    except BaseException:
        process.kill()
        process.wait()
        raise
    return {'verified_files': len(seen), 'verified_bytes': total}


def relocate(value, old: str, new: str):
    if isinstance(value, str):
        return new + value[len(old):] if value.startswith(old + '/') else value
    if isinstance(value, list):
        return [relocate(v, old, new) for v in value]
    if isinstance(value, dict):
        return {key: relocate(val, old, new) for key, val in value.items()}
    return value


def relocate_postgres(conn, old: str, new: str) -> int:
    from psycopg import sql
    from psycopg.types.json import Jsonb, Json
    changed = 0
    columns = conn.execute("""SELECT table_schema,table_name,column_name,udt_name
      FROM information_schema.columns c JOIN information_schema.tables t USING(table_schema,table_name)
      WHERE table_schema IN ('platform','benefitlens') AND t.table_type='BASE TABLE'
      AND udt_name IN ('jsonb','json','text') AND is_generated='NEVER'""").fetchall()
    for schema, table, column, kind in columns:
        relation = sql.Identifier(schema, table)
        name = sql.Identifier(column)
        rows = conn.execute(sql.SQL('SELECT ctid::text,{} FROM {} WHERE strpos({}::text,%s)>0').format(
            name, relation, name), (old,)).fetchall()
        for row_id, value in rows:
            converted = relocate(value, old, new)
            if converted != value:
                replacement = Jsonb(converted) if kind == 'jsonb' else Json(converted) if kind == 'json' else converted
                conn.execute(sql.SQL('UPDATE {} SET {}=%s WHERE ctid=%s::tid').format(relation, name), (replacement, row_id))
                changed += 1
    return changed


def relocate_local(data: Path, old: str, new: str) -> dict:
    changed = 0
    for path in data.rglob('*'):
        if not path.is_file() or any(part in {'raw', 'text'} for part in path.relative_to(data).parts[:-1]):
            continue
        if path.suffix == '.json':
            value = json.loads(path.read_text())
            converted = relocate(value, old, new)
            if converted != value:
                path.write_text(json.dumps(converted, ensure_ascii=False, indent=2) + '\n')
                changed += 1
        elif path.suffix in {'.sqlite', '.sqlite3', '.db'}:
            with path.open('rb') as stream:
                is_sqlite = stream.read(16) == b'SQLite format 3\x00'
            if not is_sqlite:
                continue
            with sqlite3.connect(path) as conn:
                tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                for table in tables:
                    quoted_table = '"' + table.replace('"', '""') + '"'
                    columns = [r[1] for r in conn.execute(f'PRAGMA table_info({quoted_table})') if r[2].upper() in {'TEXT', 'JSON'}]
                    for column in columns:
                        quoted_column = '"' + column.replace('"', '""') + '"'
                        for rowid, value in conn.execute(f'SELECT rowid,{quoted_column} FROM {quoted_table} WHERE instr({quoted_column},?)>0', (old,)).fetchall():
                            try:
                                parsed = json.loads(value)
                                converted = relocate(parsed, old, new)
                                replacement = json.dumps(converted, ensure_ascii=False) if converted != parsed else value
                            except (json.JSONDecodeError, TypeError):
                                replacement = relocate(value, old, new)
                            if replacement != value:
                                conn.execute(f'UPDATE {quoted_table} SET {quoted_column}=? WHERE rowid=?', (replacement, rowid))
                                changed += 1
    return {'relocated_local_values': changed}


def restore(bundle: Path, database_only: bool) -> dict:
    manifest, files = validate(bundle)
    config = project.settings()
    container = project.container()
    data = project.ROOT / 'pipeline/data'
    if not database_only and data.exists() and any(data.iterdir()):
        raise ValueError('Restore requires an empty pipeline/data directory.')
    with project.connect() as conn:
        tables = conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')").fetchone()[0]
        if tables:
            raise ValueError('Restore refuses a nonempty target DB. No tables were dropped.')
    if not database_only:
        need = manifest['files']['bytes_before_compression']
        if shutil.disk_usage(project.ROOT).free < need * 1.1:
            raise ValueError('Insufficient disk space for full restore.')
        read_archive(bundle, files, project.ROOT)
    with (bundle / 'database.dump').open('rb') as stream:
        subprocess.run(['docker', 'exec', '-i', container, 'pg_restore', '-U', config['POSTGRES_USER'],
                        '-d', config['POSTGRES_DB'], '--no-owner', '--no-acl', '--exit-on-error', '--single-transaction'],
                       stdin=stream, check=True)
    with project.connect() as conn:
        actual = table_counts(conn)
        expected = json.loads((bundle / 'table-counts.json').read_text())
        if actual != expected:
            raise ValueError('Restored PostgreSQL table counts do not match the exported snapshot.')
        relocated = relocate_postgres(conn, manifest['source_root'], str(project.ROOT)) if not database_only else 0
    local = relocate_local(data, manifest['source_root'], str(project.ROOT)) if not database_only else {}
    report = {'completed_at': now(), 'database_tables_verified': len(actual), 'row_counts_match': True,
              'mode': 'database_only_no_raw_files' if database_only else 'database_and_files',
              'relocated_postgres_values': relocated, **local, 'bundle': str(bundle)}
    receipt = project.ROOT / '.local' / ('db-restore.json' if database_only else 'restore.json')
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('backup')
    create.add_argument('--source-root', required=True, type=Path)
    create.add_argument('--source-container', required=True)
    create.add_argument('--output', required=True, type=Path)
    check = sub.add_parser('verify')
    check.add_argument('--bundle', required=True, type=Path)
    load = sub.add_parser('restore')
    load.add_argument('--bundle', required=True, type=Path)
    load.add_argument('--database-only', action='store_true')
    args = parser.parse_args()
    if args.action == 'backup':
        backup(args.source_root, args.source_container, args.output)
    elif args.action == 'verify':
        _, files = validate(args.bundle)
        print(json.dumps(read_archive(args.bundle, files), ensure_ascii=False), flush=True)
    else:
        print(json.dumps(restore(args.bundle, args.database_only), ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
