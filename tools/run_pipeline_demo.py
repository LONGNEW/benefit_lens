"""Replay the ten-program pipeline with real originals, PostgreSQL and HTTP.

No search, source download, model invocation or private profile publication.
Only publish a receipt after every execution gate passes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from jsonschema import Draft202012Validator

import project
import sample_data

sys.path.insert(0, str(project.ROOT / 'pipeline/src'))
from data_platform.demo_contract import build_outline, validate_notice, verify_document
from data_platform.pipeline_demo import Handler
from data_platform.proposal_pipeline import anchor_key, digest, extract_source_text
from data_platform.writing_patterns import (fingerprint, prepare_program, validate_cached_receipt,
                                            knowledge_from_result, LEGACY_PROMPT_VERSION)


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n')
    temporary.replace(path)


def db_state(conn):
    return {'opportunities': [list(r) for r in conn.execute(
        'SELECT opportunity_id,version_hash,updated_at::text FROM benefitlens.opportunities ORDER BY 1')],
        'knowledge': [list(r) for r in conn.execute(
        'SELECT program_id,version_hash,updated_at::text FROM benefitlens.program_knowledge ORDER BY 1')],
        'revisions': conn.execute('SELECT count(*) FROM benefitlens.opportunity_revision').fetchone()[0]}


def validate_sample_knowledge(knowledge, context, receipt):
    """Compare model-derived knowledge exactly, allowing only new v2 labels.

    Missing compatibility labels may be filled with their precise conservative
    values. Existing labels, text, ownership, refs, comparisons and provenance
    are never discarded or ignored to make replay pass. This does not approve
    a legacy interpretation under the v3 scope contract.
    """
    checked = validate_cached_receipt(receipt, context)
    rebuilt = knowledge_from_result(knowledge, context, checked)
    comparable = deepcopy(knowledge)
    added = []
    if checked.get('prompt_version') == LEGACY_PROMPT_VERSION:
        def fill(obj, expected, key, path):
            if key not in obj:
                obj[key] = deepcopy(expected[key])
                added.append(path)
        fill(comparable, rebuilt, 'comparison_decision', 'comparison_decision')
        fill(comparable['pattern_extraction'], rebuilt['pattern_extraction'],
             'compatibility', 'pattern_extraction.compatibility')
        for index, (old, new) in enumerate(zip(comparable['writing_pattern'], rebuilt['writing_pattern'])):
            fill(old, new, 'scope_validation', f'writing_pattern[{index}].scope_validation')
        for index, (old, new) in enumerate(zip(comparable['outcome_comparisons'], rebuilt['outcome_comparisons'])):
            fill(old, new, 'comparison_scope', f'outcome_comparisons[{index}].comparison_scope')
    if comparable != rebuilt:
        raise ValueError('Sample knowledge differs from the validated model result beyond compatibility labels')
    return checked, added


def revalidate_saved_pattern_run(knowledge, context, cache_dir):
    """Read only an existing receipt. Never fall through to an extraction call."""
    provenance = knowledge.get('pattern_extraction', {})
    key = provenance.get('input_sha256', '')
    if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key):
        raise ValueError('Saved pattern input hash is missing or invalid')
    program = context['program_id']
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', program):
        raise ValueError('Invalid cache program ID')
    path = Path(cache_dir) / (program + '-' + key[:16] + '.json')
    receipt = json.loads(path.read_text())
    if receipt.get('input_sha256') != key:
        raise ValueError('Pattern cache does not match the saved sample provenance')
    checked, added = validate_sample_knowledge(knowledge, context, receipt)
    legacy = checked.get('prompt_version') == LEGACY_PROMPT_VERSION
    return {'program_id': program, 'input_sha256': checked['input_sha256'],
            'prepared_context_sha256': fingerprint(context),
            'prompt_version': checked['prompt_version'], 'extractor': checked.get('extractor'),
            'scope_validation': 'legacy_v2_requires_scope_review' if legacy else 'v3_scope_bound',
            'approved_handoff': False, 'semantic_approval_executed': False,
            'compatibility_labels_added': added,
            'model': checked['model'], 'original_model_run_at': checked['created_at'],
            'model_called_this_run': False, 'documents': len(context['documents']),
            'characters': sum(len(d['text']) for d in context['documents']),
            'cases': len(context['cases']), 'patterns': len(checked['result']['writing_pattern'])}


def replay(collection_path):
    started = now()
    stages = []
    tick = time.monotonic()

    def stage(key, mode, **counts):
        nonlocal tick
        stages.append({'stage': key, 'mode': mode, 'seconds': round(time.monotonic() - tick, 3), **counts})
        tick = time.monotonic()

    sample = sample_data.read_sample()
    collection = json.loads(collection_path.read_text())
    sources = json.loads((project.ROOT / 'pipeline/config/proposal_demo_sources.json').read_text())
    collected = {d['id']: d for d in collection['documents'] if d['status'] == 'collected'}
    # Select only originals actually used by the active notices and patterns.
    notice_ids = {r['source_id'] for p in sample['opportunities'] for r in p['source_refs'] if r.get('source_id')}
    experience_ids = {d['source_id'] for k in sample['program_knowledge'] for d in k['pattern_extraction']['input_documents']}
    selected = notice_ids | experience_ids
    for key in sorted(selected):
        if key not in collected:
            raise ValueError('Required original is unavailable: ' + key)
        verify_document(collected[key])
        original = collected[key]
        extracted = extract_source_text(Path(original['raw_path']).read_bytes(), original['kind'])
        if digest(extracted.encode()) != original['text_sha256']:
            raise ValueError('Re-extracted text differs; review before re-analysis: ' + key)
    source_failures = sum(d['status'] != 'collected' for d in collection['documents'])
    stage('source_integrity', 'raw_to_text_replayed', notices=len(notice_ids),
          experience_documents=len(experience_ids), required_documents=len(selected),
          text_reextracted=len(selected),
          extraction_kinds=dict(Counter(collected[key]['kind'] for key in selected)),
          historical_failed_sources=source_failures)

    notices = []
    for item in sample['opportunities']:
        contract = validate_notice(item)
        for ref in item['source_refs']:
            if ref.get('source_id') and ref.get('anchor'):
                original = collected[ref['source_id']]
                if ref.get('content_sha256') != original['sha256']:
                    raise ValueError('Notice reference hash differs: ' + ref['id'])
                if anchor_key(ref['anchor']) not in anchor_key(Path(original['text_path']).read_text()):
                    raise ValueError('Notice quote is absent: ' + ref['id'])
        notices.append({'opportunity_id': item['opportunity_id'], **contract})
    stage('notice_contract', 'reviewed_snapshot_validated', opportunities=len(notices),
          condition_trees=sum(n['eligibility_available'] for n in notices),
          condition_leaves=sum(n['condition_leaves'] for n in notices))

    runs = []
    for knowledge in sample['program_knowledge']:
        context = prepare_program(sample, collection, sources, knowledge['program_id'])
        runs.append(revalidate_saved_pattern_run(knowledge, context, project.ROOT / '.local/pattern-runs'))
    stage('experience_patterns', 'prior_llm_results_revalidated', programs=len(runs),
          documents=sum(r['documents'] for r in runs), cases=sum(r['cases'] for r in runs),
          patterns=sum(r['patterns'] for r in runs),
          scope_states=dict(Counter(r['scope_validation'] for r in runs)),
          extractors=dict(Counter(r['extractor'] for r in runs)))

    # Refuse a legacy/broad DB before either pass. All writes are transactional.
    with project.connect() as conn:
        sample_data.require_sample_target(conn, sample)
        before = db_state(conn)
        sample_data.install(conn, sample)
    with project.connect() as conn:
        first = db_state(conn)
        sample_data.install(conn, sample)
    with project.connect() as conn:
        second = db_state(conn)
        if first != second:
            raise ValueError('Identical replay changed hashes, revisions or timestamps')
        actual = dict(conn.execute('SELECT opportunity_id,version_hash FROM benefitlens.opportunities'))
        if actual != {p['opportunity_id']: digest(p) for p in sample['opportunities']}:
            raise ValueError('Stored payload hashes differ from the input')
        if any(digest(payload) != version for payload, version in conn.execute(
                'SELECT payload,version_hash FROM benefitlens.opportunities')):
            raise ValueError('Stored opportunity payload differs from its version hash')
        if any(digest(payload) != version for payload, version in conn.execute(
                'SELECT payload,version_hash FROM benefitlens.program_knowledge')):
            raise ValueError('Stored program knowledge differs from its version hash')
    stage('database', 'executed_two_upserts', opportunities=len(actual),
          revision_count_before=before['revisions'], revision_count_after=second['revisions'],
          repeated_write_unchanged=True)

    os.environ['DATABASE_URL'] = project.environment()['DATABASE_URL']
    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = 'http://127.0.0.1:' + str(server.server_port)
            run = subprocess.run(['node', str(project.ROOT / 'web/scripts/run-pipeline-scenarios.mjs'), base],
                                 capture_output=True, text=True, check=True, timeout=45)
            online = json.loads(run.stdout)
        finally:
            server.shutdown()
            thread.join(timeout=10)
    stage('online_matching', 'postgres_http_browser_engine_executed', examples=len(online['examples']),
          scenarios=len(online['scenarios']), passed=sum(s['passed'] for s in online['scenarios']))

    knowledge = next(k for k in sample['program_knowledge'] if k['program_id'] == 'nvidia-graduate-fellowship')
    pattern = next(p for p in knowledge['writing_pattern'] if p['pattern_id'] == 'research-arc')
    outline = {'program_id': knowledge['program_id'], 'pattern_id': pattern['pattern_id'],
               'supporting_case_ids': pattern['supporting_case_ids'], 'text': build_outline(pattern),
               'status': 'outline_only_user_facts_required', 'model_called_this_run': False}
    stage('application_preparation', 'outline_executed', outlines=1, personalized_drafts=0)

    results = Counter('match' if e['result'] is True else 'mismatch' if e['result'] is False else 'unknown'
                      for e in online['examples'])
    return {'version': 'pipeline-replay-v1', 'status': 'snapshot_passed_with_gaps',
            'started_at': started, 'finished_at': now(), 'data_as_of': sample['as_of'],
            'sample_sha256': digest((project.ROOT / 'data/sample/programs.json').read_bytes()),
            'network_collection_executed': False, 'model_calls_this_run': 0,
            'private_profile_persisted': False, 'stages': stages, 'model_reuse': runs,
            'matching_counts': dict(results), 'examples': online['examples'],
            'scenarios': online['scenarios'], 'outline': outline,
            'gaps': ['six_notices_summary_only', 'gates_eligibility_missing',
                     'notice_llm_extraction_not_executed', 'personalized_generation_not_connected',
                     'refresh_scheduler_not_executed', 'historical_collection_failures_retained']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection', type=Path, default=project.ROOT / '.local/experience-originals/collection.json')
    args = parser.parse_args()
    private = project.ROOT / '.local/pipeline-replay'
    try:
        result = replay(args.collection)
        schema = json.loads((project.ROOT / 'pipeline/schemas/pipeline-run.schema.json').read_text())
        Draft202012Validator(schema).validate(result)
    except Exception as error:
        save(private / 'last-failure.json', {'status': 'failed', 'at': now(),
             'error_type': type(error).__name__, 'error': str(error)[:1500]})
        raise
    save(private / 'latest.json', result)
    # Public receipt contains only public source summaries and a synthetic test.
    save(project.ROOT / 'web/pipeline-demo/pipeline-run.json', result)
    print(json.dumps({k: result[k] for k in ('status', 'finished_at', 'matching_counts', 'stages', 'gaps')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
