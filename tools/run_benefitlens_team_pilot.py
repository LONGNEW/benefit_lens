"""Reproducible offline integration of the six team execution contracts.

Inputs are existing historical snapshots and a synthetic profile. This runner
does not fetch sources, call a model, write a database, or update services.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess

from benefitlens_notice_refresh import build_manifest
from benefitlens_source_storage import read_source_bytes
from benefitlens_writing_plan import build_paragraph_plan, validate_plan

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path('/home/longnew/.cache/datasets/benefitlens/scale50k-20261109')
WORK = CACHE / 'team-execution-20261119'
CODE_INPUTS = [
    'tools/run_benefitlens_team_pilot.py',
    'tools/benefitlens_notice_refresh.py',
    'tools/benefitlens_writing_plan.py',
    'tools/benefitlens_source_storage.py',
    'web/scripts/run-team-execution-pilot.mjs',
    'web/scripts/execution-profile.mjs',
    'web/pipeline-demo/match.mjs',
    'web/lib/eligibility.ts', 'web/lib/profile-assistance.ts', 'web/lib/draft.ts',
    'web/lib/browser-profile.ts',
    'pipeline/config/team_execution_v1.json',
    'pipeline/config/notice_refresh_policy_v1.json',
    'pipeline/config/writing_execution_policy_v1.json',
    'pipeline/config/paragraph_writing_templates_v1.json',
    'pipeline/config/writing_quality_rubric_v1.json',
    'pipeline/config/writing_principles_v1.json',
    'pipeline/schemas/opportunity.schema.json',
    'pipeline/schemas/common-fields.schema.json',
]


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def verify_source_files(profile, refresh_records):
    """Bind replay reuse to actual retained source bytes, including gzip blobs."""
    references = []
    prompt = profile['writing_task']['source_ref']
    references.extend([(prompt['raw_path'], prompt['raw_sha256']),
                       (prompt['path'], prompt['text_sha256'])])
    for record in refresh_records:
        for document in record.get('original_checks', []):
            if document.get('hashes_valid'):
                references.extend([(document['raw_path'], document['raw_sha256']),
                                   (document['text_path'], document['text_sha256'])])
    verified = {}
    for path, expected in references:
        actual = sha256(read_source_bytes(path)).hexdigest()
        if actual != expected:
            raise ValueError('retained_source_hash_mismatch:' + path)
        verified[str(Path(path).resolve())] = actual
    return verified


def check_sample(sample_path, manifest_path):
    manifest = read_json(manifest_path)
    if digest(sample_path) != manifest['sha256']:
        raise ValueError('sample_manifest_hash_mismatch')
    sample = read_json(sample_path)
    if len(sample.get('opportunities', [])) != manifest['opportunities']:
        raise ValueError('sample_manifest_count_mismatch')
    return sample


def _ids(rows, key):
    values = [row[key] for row in rows]
    if len(set(values)) != len(values):
        raise ValueError('duplicate_identity:' + key)
    return set(values)


def validate_integration(sample, refresh, personal):
    """Reject mixed identity sets and promotion of historical/synthetic inputs."""
    expected = _ids(sample['opportunities'], 'opportunity_id')
    errors = []
    if _ids(refresh['records'], 'record_id') != expected:
        errors.append('refresh_notice_identity_mismatch')
    if _ids(personal['records'], 'opportunity_id') != expected:
        errors.append('personalization_notice_identity_mismatch')
    if refresh.get('current_recruitment_verification_performed') is not False:
        errors.append('unexpected_current_verification_claim')
    if any(row.get('scope') != 'historical_demo' or row.get('current_open_verified') is not False
           for row in refresh['records']):
        errors.append('historical_notice_promoted_to_current')
    for key in ('network_requests', 'model_calls', 'database_writes'):
        if personal.get(key) != 0:
            errors.append('unexpected_side_effect:' + key)
    if personal.get('as_of') != sample.get('as_of'):
        errors.append('historical_matching_date_changed')
    bridge = personal.get('bridge') or {}
    task = bridge.get('task_spec') or {}
    eligible_scope = [row for row in sample['opportunities']
                      if row.get('program_id') == task.get('program_id')
                      and row.get('round_id') == task.get('round_id')]
    if not eligible_scope:
        errors.append('writing_task_program_round_mismatch')
    if task.get('opportunity_id') and task['opportunity_id'] not in {
            row['opportunity_id'] for row in eligible_scope}:
        errors.append('writing_task_notice_mismatch')
    if (task.get('source_ref') or {}).get('status') == 'verified_current':
        errors.append('historical_task_promoted_to_current')
    facts = bridge.get('confirmed_facts') or []
    if not facts or any(fact.get('source', {}).get('kind') != 'synthetic_confirmed_fact'
                        for fact in facts):
        errors.append('pilot_profile_not_explicitly_synthetic')
    return {'valid': not errors, 'errors': errors,
            'scope': 'technical_contract_check_not_semantic_approval'}


def build_result(sample, refresh, personal):
    validation = validate_integration(sample, refresh, personal)
    if not validation['valid']:
        raise ValueError('integration_failed:' + ','.join(validation['errors']))
    bridge = personal['bridge']
    task, facts = bridge['task_spec'], bridge['confirmed_facts']
    plan = build_paragraph_plan(task, facts, bridge['template_id'], bridge.get('style_profile'))
    plan_validation = validate_plan(plan, task, facts)
    if not plan_validation['valid']:
        raise ValueError('paragraph_plan_failed:' + ','.join(plan_validation['errors']))
    # Held facts must stay visible. A technically valid plan can still need input.
    return {
        'contract_version': 'team-execution-result-v1',
        'scope': 'historical_demo_with_synthetic_profile',
        'matching_as_of': sample['as_of'],
        'refresh_evaluated_at': refresh['at'],
        'counts': {'notice_records': len(personal['records']),
                   'basic_conditions': dict(Counter(row.get('decision', 'unspecified')
                                                    for row in personal['records'])),
                   'refresh_due': refresh['counts']['refresh_due'],
                   'paragraphs': len(plan['paragraphs']),
                   'confirmed_synthetic_facts': len(facts),
                   'missing_writing_fields': len(plan['need_info']),
                   'held_writing_facts': len(plan['held_facts']),
                   'evaluated_quality_scores': sum(d['score'] is not None
                                                   for d in plan['quality_diagnostics'])},
        'integration_validation': validation,
        'paragraph_plan_validation': plan_validation,
        'paragraph_plan': plan,
        'technical_contract_passed': True,
        'real_application_ready': False,
        'release_holds': ['current_recruitment_not_reverified',
                         'synthetic_user_profile',
                         'semantic_quality_and_submission_checks_not_completed'],
        'source_refresh': refresh,
        'personalization': personal,
        'side_effects': {'network_requests': 0, 'model_calls': 0, 'database_writes': 0,
                         'service_changes': 0},
    }


def node_runtime(explicit=None):
    """Use already installed Node with native TypeScript stripping support."""
    candidates = [explicit] if explicit else [
        '/home/longnew/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node',
        shutil.which('node')]
    if not explicit:
        candidates += sorted(Path('/home/longnew/.npm/_npx').glob('*/node_modules/node/bin/node'))
    for item in candidates:
        if not item:
            continue
        if not Path(item).is_file():
            continue
        probe = subprocess.run([str(item), '--input-type=module', '-e',
            'import {stripTypeScriptTypes} from "node:module"; process.exit(typeof stripTypeScriptTypes === "function" ? 0 : 1)'],
            capture_output=True, text=True, timeout=10)
        if probe.returncode == 0:
            return Path(item).resolve()
    raise RuntimeError('A cached Node runtime with stripTypeScriptTypes is required; no installation attempted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample', type=Path, default=ROOT/'data/sample/programs.json')
    parser.add_argument('--sample-manifest', type=Path, default=ROOT/'data/sample/manifest.json')
    parser.add_argument('--refresh-input', type=Path, default=WORK/'notice-refresh/input-records.json')
    parser.add_argument('--profile', type=Path, default=ROOT/'evaluation/spec_examples/team-execution-profile.synthetic.json')
    parser.add_argument('--now', default=datetime.now(timezone.utc).isoformat())
    parser.add_argument('--output-root', type=Path, default=WORK/'integrated')
    parser.add_argument('--node', type=Path)
    args = parser.parse_args()
    sample = check_sample(args.sample, args.sample_manifest)
    refresh_records = read_json(args.refresh_input)
    retained_sources = verify_source_files(read_json(args.profile), refresh_records)
    inputs = {str(path): digest(path) for path in
              [args.sample, args.sample_manifest, args.refresh_input, args.profile]}
    dependencies = {relative: digest(ROOT/relative) for relative in CODE_INPUTS}
    node = node_runtime(args.node)
    runtime_version = subprocess.check_output([str(node), '--version'], text=True).strip()
    identity = {'inputs': inputs, 'dependencies': dependencies, 'now': args.now,
                'retained_source_files': retained_sources,
                'node_runtime': {'path': str(node), 'version': runtime_version}}
    run_id = fingerprint(identity)
    output = args.output_root.resolve()/run_id
    if not output.is_relative_to(CACHE.resolve()):
        raise ValueError('Reusable pilot data must be written under the shared dataset cache')
    output.mkdir(parents=True, exist_ok=True)
    result_path = output/'result.json'
    receipt_path = output/'receipt.json'
    if result_path.exists():
        receipt = read_json(receipt_path)
        if receipt.get('run_id') != run_id or receipt.get('result_sha256') != digest(result_path):
            raise ValueError('Existing execution artifact failed receipt verification')
        print(json.dumps({'run_id': run_id, 'result': str(result_path), 'reused': True}))
        return
    personal_path = output/'personalization.json'
    command = [str(node), str(ROOT/'web/scripts/run-team-execution-pilot.mjs'),
               '--opportunities', str(args.sample.resolve()), '--profile', str(args.profile.resolve()),
               '--output', str(personal_path)]
    process = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=120)
    if process.returncode:
        raise RuntimeError('Personalization execution failed: ' + process.stderr[-3000:])
    personal = read_json(personal_path)
    refresh = build_manifest(refresh_records, now=args.now)
    result = build_result(sample, refresh, personal)
    result['run_id'] = run_id
    result['input_lineage'] = identity
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    receipt = {'run_id': run_id, 'result_sha256': digest(result_path),
               'personalization_sha256': digest(personal_path), 'command': command,
               'technical_contract_passed': True, 'real_application_ready': False,
               'created_at': datetime.now(timezone.utc).isoformat()}
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    (args.output_root.resolve()/'latest.json').write_text(json.dumps({
        'run_id': run_id, 'result': str(result_path), 'receipt': str(receipt_path)}, indent=2)+'\n')
    print(json.dumps({'run_id': run_id, 'result': str(result_path),
                      'counts': result['counts'], 'reused': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
