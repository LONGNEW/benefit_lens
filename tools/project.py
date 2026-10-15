"""Repository-local commands; never manage the live legacy container or its volume."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]


def settings() -> dict[str, str]:
    path = ROOT / '.env'
    if not path.is_file():
        raise ValueError('Run make init first.')
    result = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip()
    if result.get('COMPOSE_PROJECT_NAME') in {'unlock', 'benefitlens'}:
        raise ValueError('Use a separate Compose project, not the live legacy project.')
    if result.get('BENEFITLENS_POSTGRES_PORT') == '55432':
        raise ValueError('Port 55432 is reserved for the live legacy database.')
    if result.get('POSTGRES_PASSWORD') in {'', 'GENERATED_BY_MAKE_INIT', None}:
        raise ValueError('Generate a local password with make init.')
    return result


def environment() -> dict[str, str]:
    config = settings()
    env = {**os.environ, **config, 'PYTHONPATH': str(ROOT / 'pipeline/src')}
    env['DATABASE_URL'] = (f"postgresql://{quote(config['POSTGRES_USER'], safe='')}:"
                           f"{quote(config['POSTGRES_PASSWORD'], safe='')}@127.0.0.1:"
                           f"{config['BENEFITLENS_POSTGRES_PORT']}/{quote(config['POSTGRES_DB'], safe='')}")
    return env


def compose(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    config = settings()
    return subprocess.run(['docker', 'compose', '--project-name', config['COMPOSE_PROJECT_NAME'],
                           '--env-file', str(ROOT / '.env'), '-f', str(ROOT / 'compose.yaml'), *args],
                          cwd=ROOT, env=environment(), check=True, text=True,
                          capture_output=capture)


def container() -> str:
    value = compose('ps', '-q', 'postgres', capture=True).stdout.strip()
    if not value:
        raise ValueError('Run make db-up first.')
    return value


def connect():
    import psycopg
    return psycopg.connect(environment()['DATABASE_URL'])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'doctor', 'db-up', 'db-stop', 'schema', 'demo', 'status'])
    action = parser.parse_args().action
    if action == 'init':
        path = ROOT / '.env'
        with path.open('x', encoding='utf-8') as out:
            out.write((ROOT / '.env.example').read_text().replace('GENERATED_BY_MAKE_INIT', secrets.token_urlsafe(30)))
        path.chmod(0o600)
        print('Created private .env. Existing files are never overwritten.')
    elif action == 'doctor':
        print(json.dumps({'python': sys.version.split()[0],
                          'tools': {name: bool(shutil.which(name)) for name in
                                    ['docker', 'git', 'make', 'zstd', 'pdftotext', 'tesseract', 'hwp5txt', 'node']}}, indent=2))
    elif action == 'db-up':
        compose('up', '-d', '--wait', 'postgres')
    elif action == 'db-stop':
        compose('stop', 'postgres')
    elif action == 'schema':
        import psycopg
        with psycopg.connect(environment()['DATABASE_URL'], autocommit=True) as conn:
            if conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname IN ('platform','benefitlens')").fetchone()[0]:
                raise ValueError('Schema bootstrap requires an empty target; restore a bundle before bootstrap, not after it.')
            for path in sorted((ROOT / 'pipeline/migrations').glob('*.sql')):
                conn.execute(path.read_text())
                print(path.name, flush=True)
    elif action == 'demo':
        config = settings()
        subprocess.run([sys.executable, '-m', 'data_platform.pipeline_demo', '--port',
                        config['BENEFITLENS_DEMO_PORT']], cwd=ROOT, env=environment(), check=True)
    else:
        with connect() as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            counts = {}
            for name in ['support_discovery_record', 'opportunities', 'pipeline_document',
                         'experience_discovery_case', 'program_knowledge']:
                counts[name] = conn.execute('SELECT count(*) FROM benefitlens.' + name).fetchone()[0]
        print(json.dumps(counts, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, FileExistsError) as exc:
        raise SystemExit(str(exc))
