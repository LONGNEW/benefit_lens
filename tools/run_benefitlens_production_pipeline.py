"""Process the real retained inventory and validate five-team handoff artifacts.

No demo replay, synthetic profile, semantic approval, HTTP request, production
database write or service update is performed by this coordinator.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path('/home/longnew/.cache/datasets/benefitlens/scale50k-20261109')
DEFAULT_ROOT = CACHE/'team-production-20261119'
CATALOG = ROOT/'pipeline/config/shared_condition_catalog_v1.json'
ORGANIZATION = ROOT/'pipeline/config/team_production_v2.json'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def checksum(path):
    digest = sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def rows(path):
    opener = gzip.open if Path(path).suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def checked_root(path):
    path = Path(path).resolve()
    if not path.is_relative_to(CACHE.resolve()):
        raise ValueError('Output must be under the shared dataset cache')
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_condition_rows(records, catalog):
    fields = catalog['fields']
    counts, reasons, seen = Counter(), Counter(), set()
    for record in records:
        ident = record['condition_id']
        if ident in seen:
            raise ValueError('Duplicate condition identity: '+ident)
        seen.add(ident)
        if record['field_id'] not in fields:
            raise ValueError('Condition field is outside the shared catalog: '+record['field_id'])
        if record['status'] not in catalog['condition_contract']['status_values']:
            raise ValueError('Unreviewed projection acquired a semantic approval')
        for key in catalog['condition_contract']['required']:
            if key not in record:
                raise ValueError('Incomplete shared condition: '+key)
        counts[record['field_id']] += 1
        reasons.update(record['unknown_reasons'])
    return {'conditions': len(seen), 'field_counts': dict(counts), 'unknown_reason_counts': dict(reasons)}


def validate_personalization_rows(records, catalog_sha):
    count, seen = 0, set()
    for record in records:
        ident = record['candidate_key']
        if ident in seen:
            raise ValueError('Duplicate notice in personalization: '+ident)
        seen.add(ident)
        if record.get('catalog_sha256') != catalog_sha:
            raise ValueError('Notice and personalization catalog fingerprints differ')
        if record.get('synthetic') or record.get('record_type') == 'sample':
            raise ValueError('Demo or synthetic input leaked into production reporting')
        count += 1
    return {'notice_requirements': count, 'same_condition_catalog': True}


def execute_stages(root):
    steps = [
        ('writing', [sys.executable, 'tools/build_benefitlens_writing_evidence_map_v2.py', '--root', str(root/'writing')]),
        ('notice', [sys.executable, 'tools/audit_benefitlens_notice_production.py', '--root', str(root/'notice')]),
        ('experience', [sys.executable, 'tools/audit_benefitlens_experience_production.py', '--root', str(root/'experience'), '--notice-root', str(root/'notice')]),
        ('structure-validation', [sys.executable, 'tools/tests/validate_benefitlens_experience_production.py', '--root', str(root/'experience')]),
    ]
    logs = root/'operation-logs'
    logs.mkdir(exist_ok=True)
    result = []
    for stage, command in steps:
        started = time.monotonic()
        with (logs/(stage+'.log')).open('w') as stream:
            process = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        result.append({'stage': stage, 'returncode': process.returncode,
                       'elapsed_seconds': round(time.monotonic()-started, 3),
                       'log': str(logs/(stage+'.log')), 'command': command})
        print(json.dumps({'stage': stage, 'returncode': process.returncode}), flush=True)
        if process.returncode:
            raise RuntimeError('Production stage failed; inspect '+str(logs/(stage+'.log')))
    return result


def make_manifest(root, executions=None):
    root = checked_root(root)
    paths = {
        'organization': ORGANIZATION, 'condition_catalog': CATALOG,
        'refresh_policy': ROOT/'pipeline/config/notice_refresh_policy_v2.json',
        'notice_summary': root/'notice/summary.json',
        'notice_checks': root/'notice/audit-checks.json',
        'notices': root/'notice/notices.jsonl.gz',
        'conditions': root/'notice/conditions.jsonl.gz',
        'notice_queue': root/'notice/next-processing-queue.jsonl.gz',
        'experience_summary': root/'experience/summary.json',
        'experience_structure_validation': root/'experience/structural-validation.json',
        'cases': root/'experience/cases.jsonl.gz',
        'documents': root/'experience/documents.jsonl.gz',
        'segments': root/'experience/segments.jsonl.gz',
        'experience_queue': root/'experience/next-processing-queue.jsonl.gz',
        'personalization_requirements': root/'experience/personalization-requirements.jsonl.gz',
        'condition_demand': root/'experience/condition-demand-distribution.json',
        'personalization_contract': ROOT/'pipeline/config/personalization_production_v2.json',
        'writing_framework': ROOT/'pipeline/config/writing_evidence_framework_v2.json',
        'writing_summary': root/'writing/evidence-summary.json',
        'writing_evidence_map': root/'writing/evidence-map.sqlite3',
        'legacy_eight_audit': root/'writing/legacy-eight-audit.json',
        'primary_writing_records': root/'writing/primary-records.json',
        'source_content_reviews': root/'writing/manual-content-reviews.json',
    }
    for name in ('run_benefitlens_production_pipeline.py', 'audit_benefitlens_notice_production.py',
                 'audit_benefitlens_experience_production.py', 'build_benefitlens_writing_evidence_map_v2.py',
                 'benefitlens_source_storage.py', 'tests/validate_benefitlens_experience_production.py'):
        paths['code:'+name] = ROOT/'tools'/name
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise ValueError('Missing real-inventory handoff artifacts: '+','.join(missing))
    lineage = {name: {'path': str(path), 'sha256': checksum(path), 'bytes': path.stat().st_size}
               for name, path in paths.items()}
    catalog = read_json(CATALOG)
    catalog_sha = lineage['condition_catalog']['sha256']
    notice_summary, experience_summary = read_json(paths['notice_summary']), read_json(paths['experience_summary'])
    if notice_summary['catalog_sha256'] != catalog_sha or experience_summary['condition_catalog']['sha256'] != catalog_sha:
        raise ValueError('A team summary was created against another shared catalog')
    checks = read_json(paths['notice_checks'])
    if not checks['all_non_sample_rows_processed'] or checks['invalid_evidence_spans']:
        raise ValueError('Full notice projection coverage or literal evidence validation failed')
    structure = read_json(paths['experience_structure_validation'])
    if structure['state'] != 'all_checks_passed' or structure['summary_sha256'] != lineage['experience_summary']['sha256']:
        raise ValueError('Experience structural validation is incomplete or belongs to another snapshot')
    conditions = validate_condition_rows(rows(paths['conditions']), catalog)
    personalization = validate_personalization_rows(rows(paths['personalization_requirements']), lineage['condition_catalog']['sha256'])
    notice_ids = set()
    for record in rows(paths['notices']):
        ident = record['candidate_key']
        if ident in notice_ids or record.get('record_type') == 'sample':
            raise ValueError('Repeated or sample notice in the real inventory')
        notice_ids.add(ident)
    if personalization['notice_requirements'] != len(notice_ids):
        raise ValueError('Personalization demand is not projected for the full notice inventory')
    if len(notice_ids) != notice_summary['counts']['candidates']:
        raise ValueError('Notice projection does not match its full-inventory count')
    case_keys = set()
    for case in rows(paths['cases']):
        key = case['case_key']
        if key in case_keys:
            raise ValueError('Duplicate production experience identity')
        case_keys.add(key)
    if len(case_keys) != experience_summary['case_inventory']['production_scope_rows']:
        raise ValueError('Experience projection does not match its full-inventory count')
    identity = sha256(json.dumps(lineage, sort_keys=True).encode()).hexdigest()
    run = root/'runs'/identity
    run.mkdir(parents=True, exist_ok=True)
    target = run/'manifest.json'
    if target.exists():
        existing = read_json(target)
        queue = existing['cross_team_queue']
        if existing['lineage'] != lineage or checksum(queue['path']) != queue['sha256']:
            raise ValueError('Previously saved handoff lineage or queue was modified')
        (root/'latest-manifest.json').write_text(json.dumps({'run_id': identity, 'manifest': str(target)}, indent=2)+'\n')
        return existing, target
    queue_counts, queue_actions, skipped_counts = Counter(), Counter(), Counter()
    queue_seen = set()
    queue_path = run/'cross-team-work-queue.jsonl.gz'
    with gzip.open(queue_path, 'wt', encoding='utf-8') as out:
        for domain, owner, path in [('notice', 'T1', paths['notice_queue']), ('experience', 'T2', paths['experience_queue'])]:
            for ordinal, record in enumerate(rows(path)):
                work_id = sha256(json.dumps([domain, record], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if work_id in queue_seen:
                    raise ValueError('Repeated work record in the cross-team queue')
                queue_seen.add(work_id)
                action = record.get('next_step') or record.get('next_stage') or 'unspecified'
                skipped = action.startswith('skip_')
                item = {'work_id': work_id, 'domain': domain, 'owner': owner,
                        'source_queue': str(path), 'source_ordinal': ordinal,
                        'work_state': 'excluded_inactive' if skipped else 'pending', 'payload': record}
                item['handoff_context'] = {
                    'condition_catalog': lineage['condition_catalog'],
                    'writing_framework': lineage['writing_framework'],
                    'next_consumers': ['T4'] if domain == 'notice' else ['T3', 'T4'],
                    'notice_projection': lineage['notices']['path'],
                    'personalization_requirements': lineage['personalization_requirements']['path'],
                    'writing_source_map': lineage['writing_evidence_map']['path'],
                    'review_gate': 'content_owner_reviews_before_eligibility_or_personal_drafting',
                }
                out.write(json.dumps(item, ensure_ascii=False)+'\n')
                queue_counts[owner] += 1
                queue_actions[owner+':'+action] += 1
                if skipped:
                    skipped_counts[owner] += 1
    manifest = {
        'version': 'production-handoff-manifest-v2', 'run_id': identity,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'full_retained_inventory_without_demo_or_synthetic_applicants',
        'teams': ['T1', 'T2', 'T3', 'T4', 'T6'], 'technical_validation_owner': 'T6',
        'content_validation_owner': {'notice': 'T1', 'experience': 'T2', 'writing': 'T3', 'personalization': 'T4'},
        'lineage': lineage, 'condition_projection_validation': conditions,
        'personalization_binding_validation': personalization,
        'notice_records_projected': len(notice_ids), 'queue_counts': dict(queue_counts),
        'queue_actions': dict(queue_actions), 'queue_skipped_inactive_counts': dict(skipped_counts),
        'cross_team_queue': {'path': str(queue_path), 'sha256': checksum(queue_path)},
        'team_summaries': {team: read_json(paths[name]) for team, name in [
            ('T1', 'notice_summary'), ('T2_T4', 'experience_summary'), ('T3', 'writing_summary')]},
        'executions': executions or [],
        'execution_mode': 'run_local_inventory_stages' if executions else 'assemble_existing_full_inventory_runs',
        'semantic_approval_created': False,
        'whole_corpus_semantic_processing_complete': False,
        'database_writes': 0, 'service_changes': 0,
        'limitations': ['Typed mentions and paragraph boundaries remain candidates until semantic review.',
                        'Manifest validation establishes wiring, coverage and lineage, not completed content interpretation.'],
    }
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    (root/'latest-manifest.json').write_text(json.dumps({'run_id': identity, 'manifest': str(target)}, indent=2)+'\n')
    return manifest, target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--execute', action='store_true', help='Run local full-inventory stages before constructing the manifest')
    args = parser.parse_args()
    root = checked_root(args.root)
    executions = execute_stages(root) if args.execute else []
    manifest, path = make_manifest(root, executions)
    print(json.dumps({'manifest': str(path), 'notice_records': manifest['notice_records_projected'],
                      'conditions': manifest['condition_projection_validation']['conditions'],
                      'personalization': manifest['personalization_binding_validation'],
                      'queue_counts': manifest['queue_counts'], 'semantic_approval_created': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
