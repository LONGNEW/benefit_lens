"""Copy source into an independent repository without touching a live workspace."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

SKIP = {'.git', '.venv', '.deps', '__pycache__', '.pytest_cache', 'node_modules',
        '.next', '.wrangler', '.vinext', '.sites-runtime', '.openai', 'dist'}


def export(source: Path, destination: Path) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if source == destination or destination.is_relative_to(source):
        raise ValueError('Use an independent destination outside the live source.')
    files = []
    for folder in ('pipeline', 'web', 'docs', 'proposal'):
        for path in sorted((source / folder).rglob('*')):
            relative = path.relative_to(source)
            if (path.is_symlink() or not path.is_file()
                    or any(part in SKIP for part in relative.parts)
                    or relative.parts[:2] == ('pipeline', 'data')
                    or path.name.startswith('.env') or path.suffix in {'.pyc', '.tsbuildinfo'}
                    or relative == Path('pipeline/config/document_tools.json')):
                continue
            target = destination / relative
            if target.exists():
                raise FileExistsError(f'Refusing to overwrite: {relative}')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            with target.open('rb') as stream:
                checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
            files.append({'path': relative.as_posix(), 'bytes': target.stat().st_size, 'sha256': checksum})
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
              'source_root': str(source), 'destination': str(destination), 'files': files,
              'data': 'Excluded here; use tools/data_bundle.py backup for PostgreSQL and pipeline/data.'}
    out = destination / '.local/export-origin.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return {'copied_files': len(files), 'bytes': sum(f['bytes'] for f in files), 'receipt': str(out)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(export(args.source, args.destination), ensure_ascii=False, indent=2))
