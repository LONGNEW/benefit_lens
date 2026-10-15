"""Install the small public fixture, refusing to modify a non-sample database."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import project

sys.path.insert(0, str(project.ROOT / 'pipeline/src'))
from data_platform.proposal_pipeline import digest, store_opportunities
from psycopg.types.json import Jsonb

SAMPLE = project.ROOT / 'data/sample'


def read_sample(folder: Path = SAMPLE):
    manifest = json.loads((folder / 'manifest.json').read_text())
    raw = (folder / 'programs.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['sha256'] or len(raw) != manifest['bytes']:
        raise ValueError('Sample checksum mismatch.')
    data = json.loads(raw)
    if data['format'] != 'benefitlens-public-sample-v1':
        raise ValueError('Unsupported sample format.')
    ids = [p['opportunity_id'] for p in data['opportunities']]
    if len(ids) != 10 or len(set(ids)) != 10:
        raise ValueError('Expected exactly ten distinct example opportunities.')
    programs = {p['program_id'] for p in data['opportunities']}
    for knowledge in data['program_knowledge']:
        if knowledge['program_id'] not in programs:
            raise ValueError('Experience program is not connected to a sample opportunity.')
        cases = {case['case_id'] for case in knowledge['experience_cases']}
        for pattern in knowledge['writing_pattern']:
            if not set(pattern['supporting_case_ids']) <= cases or pattern.get('causal_success_claim') is not False:
                raise ValueError('Writing pattern has invalid source cases or a causal success claim.')
    return data


def require_sample_target(conn, data):
    rows = conn.execute('SELECT opportunity_id,extractor FROM benefitlens.opportunities').fetchall()
    allowed = {p['opportunity_id'] for p in data['opportunities']}
    if any(key not in allowed or extractor != 'public_demo_fixture_v1' for key, extractor in rows):
        raise ValueError('Refusing a non-sample DB. Use a separate empty database; no existing data was changed.')
    for table in ['support_discovery_record', 'pipeline_work_item']:
        rows = conn.execute('SELECT candidate_key FROM benefitlens.' + table).fetchall()
        if any(key not in allowed for (key,) in rows):
            raise ValueError('Existing discovery data is not the sample. No existing data was changed.')
    programs = {p['program_id'] for p in data['program_knowledge']}
    if any(key not in programs for (key,) in conn.execute('SELECT program_id FROM benefitlens.program_knowledge')):
        raise ValueError('Existing program knowledge is not the sample.')


def install(conn, data):
    require_sample_target(conn, data)
    store_opportunities(conn, data['opportunities'])
    for item in data['opportunities']:
        conn.execute('''INSERT INTO benefitlens.support_discovery_record
          (candidate_key,source_url,title,title_basis,source_family,record_type,capture_status,
           issuer_name,payload,first_seen_at,last_seen_at,rights_status)
          VALUES(%s,%s,%s,'public_snapshot','public_demo_fixture_v1','sample','summary_only',%s,%s,now(),now(),'summary_only')
          ON CONFLICT(candidate_key) DO UPDATE SET source_url=EXCLUDED.source_url,title=EXCLUDED.title,payload=EXCLUDED.payload
          WHERE support_discovery_record.payload<>EXCLUDED.payload''',
          (item['opportunity_id'],item['application']['url'],item['title'],item['issuer']['name'],Jsonb(item)))
        conn.execute('''INSERT INTO benefitlens.pipeline_work_item(candidate_key,source_family,status,reason)
          VALUES(%s,'public_demo_fixture_v1','sample_loaded','Public historical fixture; selected conditions only')
          ON CONFLICT(candidate_key) DO NOTHING''', (item['opportunity_id'],))
    for item in data['program_knowledge']:
        conn.execute('''INSERT INTO benefitlens.program_knowledge(program_id,experience_cases,writing_pattern,version_hash,payload)
          VALUES(%s,%s,%s,%s,%s) ON CONFLICT(program_id) DO UPDATE SET
          experience_cases=EXCLUDED.experience_cases,writing_pattern=EXCLUDED.writing_pattern,
          version_hash=EXCLUDED.version_hash,payload=EXCLUDED.payload
          WHERE program_knowledge.version_hash<>EXCLUDED.version_hash''',
          (item['program_id'],Jsonb(item['experience_cases']),Jsonb(item['writing_pattern']),digest(item),Jsonb(item)))
    for wave in data['search_waves']:
        conn.execute('''INSERT INTO benefitlens.pipeline_search_wave VALUES('public-sample',%s,%s)
          ON CONFLICT(run_id,wave) DO NOTHING''', (wave['wave'], Jsonb(wave)))
    return {'opportunities':len(data['opportunities']), 'knowledge_programs':len(data['program_knowledge']),
            'experience_summaries':sum(len(p['experience_cases']) for p in data['program_knowledge']),
            'raw_documents':0, 'network_collection_executed':False}


def main():
    data = read_sample()
    with project.connect() as conn:
        exists = conn.execute("SELECT to_regclass('benefitlens.opportunities')").fetchone()[0]
        tables = conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname IN ('platform','benefitlens')").fetchone()[0]
    if not exists:
        if tables:
            raise ValueError('Partial schema exists; finish migrations in a separate target first.')
        subprocess.run([sys.executable,str(project.ROOT/'tools/project.py'),'schema'],check=True)
    with project.connect() as conn:
        result = install(conn, data)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
