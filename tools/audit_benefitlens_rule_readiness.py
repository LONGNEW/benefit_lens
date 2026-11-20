"""Read a frozen conditions generation and write offline parsing candidates.

No network/model calls, operational SQLite writes, canonical promotion, source
text reopen, or personal eligibility decisions. Four dimensions are separate.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import time

from benefitlens_condition_values import CONTRACT, assess, contract

REPO = Path(__file__).resolve().parents[1]
VERSION = 'rule-readiness-offline-v2'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def file_hash(path):
    h = sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def ref(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': file_hash(path), 'bytes': path.stat().st_size}


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def source_binding_reuse(row, record, bindings):
    """Check stored references agree; do not claim a new source-text replay."""
    issues = []
    if record.get('mapping_id') != row['mapping_id'] or record.get('candidate_key') != row['candidate_key']:
        issues.append('record_identity_mismatch')
    existing = record.get('bindings', [])
    if sorted(encoded(b) for b in existing) != sorted(encoded(b) for b in bindings):
        issues.append('stored_binding_record_mismatch')
    if not any(b.get('property') == 'evidence_quote' for b in bindings):
        issues.append('stored_evidence_binding_missing')
    for b in bindings:
        if b.get('document_id') != row['document_id']:
            issues.append('stored_binding_document_mismatch')
        if b.get('text_sha256') not in (None, row['text_sha256']):
            issues.append('stored_binding_source_version_mismatch')
        if not (isinstance(b.get('char_start'), int) and isinstance(b.get('char_end'), int)
                and 0 <= b['char_start'] < b['char_end']
                and isinstance(b.get('exact_source_quote'), str) and b['exact_source_quote']):
            issues.append('stored_binding_shape_invalid')
    for key in ('field_id', 'operator', 'value_extraction_method', 'value_quote', 'unit', 'subject',
                'statement_type', 'modality', 'reference_phase', 'program_specific_key'):
        if record['mapping'].get(key) != row[key]:
            issues.append('mapping_record_column_mismatch:' + key)
    return sorted(set(issues))


def run(evidence_path, output):
    started, tick = datetime.now(timezone.utc).isoformat(), time.monotonic()
    evidence_path = Path(evidence_path).resolve()
    evidence = json.loads(evidence_path.read_text())
    source = Path(evidence['conditions']['resolved_database']).resolve()
    if source.parent.parent.name != 'generations':
        raise ValueError('Input must name a frozen generation, not a moving symlink')
    output = Path(output).expanduser().resolve()
    # The BenefitLens subtree is a mounted shared-cache symlink; resolving only
    # its parent would incorrectly reject the authorized storage mount.
    cache_root = Path('/home/longnew/.cache/datasets/benefitlens/scale50k-20261109').resolve()
    if not output.is_relative_to(cache_root):
        raise ValueError('Batch output must stay under the shared dataset cache')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Refusing to overwrite an existing audit run')
    output.mkdir(parents=True, exist_ok=True)
    space = shutil.disk_usage(output)
    if space.free < 512 * 1024 * 1024:
        raise ValueError('Insufficient shared-cache space')
    source_before = ref(source)
    upstream_path = source.parent / 'summary.json'
    upstream = json.loads(upstream_path.read_text())
    if upstream['database']['sha256'] != source_before['sha256']:
        raise ValueError('Frozen source database hash differs from its generation summary')
    files = [Path(__file__), REPO/'tools/benefitlens_condition_values.py', CONTRACT]
    code_refs = [ref(p) for p in files]
    protected = [REPO/'pipeline/config/shared_condition_catalog_v1.json',
                 REPO/'tools/link_benefitlens_condition_schema.py',
                 REPO/'tools/build_benefitlens_profile_projection.py',
                 REPO/'tools/benefitlens_compact_notice.py']
    protected_before = {str(p): file_hash(p) for p in protected}
    cx = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    cx.row_factory = sqlite3.Row
    cx.execute('PRAGMA query_only=ON')
    cx.execute('BEGIN')
    metadata = {r[0]: json.loads(r[1]) for r in cx.execute('SELECT * FROM metadata')}
    expected = evidence['conditions']['tables']['mappings']
    if metadata['generation'] != source.parent.name or cx.execute('SELECT count(*) FROM mappings').fetchone()[0] != expected:
        raise ValueError('Input generation or row count does not match frozen evidence')
    if cx.execute('SELECT count(*) FROM catalog_fields').fetchone()[0] != 19:
        raise ValueError('Unexpected operational field contract')
    catalog_sha = file_hash(protected[0])
    if metadata['source_inputs']['catalog']['sha256'] != catalog_sha:
        raise ValueError('Operational catalog changed from the frozen generation')
    groups = {}
    for row in cx.execute('SELECT group_id,receipt_path,receipt_sha256,execution_json FROM analyzed_groups'):
        execution = json.loads(row['execution_json'])
        groups[row['group_id']] = {'provider': execution.get('provider'), 'receipt_path': row['receipt_path'],
                                   'receipt_sha256': row['receipt_sha256'],
                                   'source_binding_validation': execution.get('source_binding_validation')}
    bindings = defaultdict(list)
    for row in cx.execute('SELECT mapping_id,binding_json FROM source_bindings ORDER BY binding_id'):
        bindings[row['mapping_id']].append(json.loads(row['binding_json']))
    totals, holds, provenance, methods = Counter(), Counter(), Counter(), Counter()
    fields = {f: {'field_id': f, 'rows': 0, 'dimensions': Counter(), 'hold_reasons': Counter()}
              for f in contract()['field_definitions']}
    examples, selected = defaultdict(dict), []
    lineage_holds = Counter()
    known_ids = set()
    query = '''SELECT m.*,o.document_id,o.text_sha256,o.char_start,o.char_end,o.context_start,o.context_end,
       c.source_url FROM mappings m JOIN occurrences o USING(occurrence_id)
       JOIN candidate_sources c ON c.candidate_key=m.candidate_key ORDER BY m.mapping_id'''
    candidate_path = output / 'candidates.jsonl.gz'
    with gzip.open(candidate_path, 'wt', encoding='utf8') as stream:
        for row in cx.execute(query):
            row = dict(row)
            mid = row['mapping_id']
            if mid in known_ids:
                raise ValueError('Duplicate mapping identity')
            known_ids.add(mid)
            record = json.loads(row['record_json'])
            assessment = assess(record['mapping'], row['semantic_review'])
            binding_issues = source_binding_reuse(row, record, bindings.get(mid, []))
            for issue in binding_issues:
                lineage_holds[issue] += 1
            upstream_group = groups[row['group_id']]
            origin = ('stored_llm_mapping_with_deterministic_selection_and_rebinding'
                      if upstream_group['provider'] in ('codex_cli', 'codex_subagent')
                      else 'upstream_provider_unidentified')
            provenance[origin] += 1
            methods[row['value_extraction_method']] += 1
            fstat = fields[row['field_id']]
            fstat['rows'] += 1
            totals['rows'] += 1
            for dimension in contract()['readiness_dimensions']:
                totals[dimension] += int(assessment[dimension])
                fstat['dimensions'][dimension] += int(assessment[dimension])
            for reason in assessment['local_attribute_holds']:
                holds[reason] += 1
                fstat['hold_reasons'][reason] += 1
            totals['additional_type_proposals'] += len(assessment['additional_type_proposals'])
            totals['stored_source_binding_reuse_consistent'] += int(not binding_issues)
            result = {'mapping_id': mid, 'contract_version': contract()['contract_version'],
                      **assessment, 'raw_mapping': record['mapping'],
                      'context': {k: row[k] for k in ('candidate_key', 'group_id', 'occurrence_id', 'document_id',
                                  'text_sha256', 'source_url', 'char_start', 'char_end', 'context_start', 'context_end')},
                      'evidence': {'bindings': bindings.get(mid, []), 'validation_fingerprint': row['validation_fingerprint'],
                                   'mode': 'reuse_existing_stored_source_binding_validation_not_new_full_text_check',
                                   'stored_binding_reference_issues': binding_issues},
                      'upstream_execution': upstream_group, 'extraction_provenance': origin}
            stream.write(encoded(result) + '\n')
            sample = {'mapping_id': mid, 'field_id': row['field_id'], 'quote': record['mapping']['evidence_quote'],
                      'value_quote': row['value_quote'], 'candidate': assessment['candidate'],
                      'type_parse_success': assessment['type_parse_success'],
                      'required_attributes_complete_local': assessment['required_attributes_complete_local'],
                      'holds': assessment['local_attribute_holds'],
                      'additional_type_proposals': assessment['additional_type_proposals']}
            slot = 'parsed' if assessment['type_parse_success'] else 'unparsed'
            examples[row['field_id']].setdefault(slot, sample)
            if assessment['additional_type_proposals'] and len(selected) < 20:
                selected.append(sample)
    foreign_keys = len(cx.execute('PRAGMA foreign_key_check').fetchall())
    cx.close()
    if totals['rows'] != expected:
        raise ValueError('Not every frozen mapping was emitted')
    source_after = ref(source)
    if source_after != source_before:
        raise ValueError('Frozen input changed during the run')
    protected_after = {str(p): file_hash(p) for p in protected}
    if protected_after != protected_before:
        raise ValueError('A protected operational code/contract changed during the run')
    field_path = output/'field-statistics.jsonl'
    with field_path.open('w') as stream:
        for item in fields.values():
            stream.write(encoded(item) + '\n')
    example_path = output/'field-examples.jsonl'
    with example_path.open('w') as stream:
        for f, slots in examples.items():
            for slot, sample in slots.items():
                stream.write(encoded({'selection': 'first_mapping_id_by_parse_state', 'slot': slot, **sample}) + '\n')
    save(output/'additional-type-examples.json', selected[:6])
    summary = {'version': VERSION, 'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
               'scope': 'frozen_selected_notice_mapping_instances_not_unique_programs_or_people',
               'source_generation': metadata['generation'], 'counts': dict(totals),
               'dimensions_not_additive': True,
               'attribute_dimension_scope': 'local_selected_mapping_properties_not_full_notice_readiness',
               'human_precision': {'status': 'not_measured', 'reviewed_units': 0, 'precision': None},
               'extraction_provenance_mapping_counts': dict(provenance),
               'value_extraction_recipe_mapping_counts': dict(methods),
               'new_parser_method': 'deterministic_bounded_quote_parser_after_stored_llm_mapping',
               'method_scope': 'Only this frozen 63887-mapping path; no project-wide regex/LLM proportion inferred.',
               'source_binding_check': {'mode': 'stored_reference_consistency_and_prior_validation_reuse',
                                        'new_source_text_replays': 0, 'issues': dict(lineage_holds)},
               'foreign_key_violations': foreign_keys, 'top_local_holds': dict(holds.most_common(18)),
               'model_calls': 0, 'network_requests': 0, 'source_database_writes': 0,
               'canonical_writes': 0, 'personal_decisions': 0,
               'meaning_approvals_created': 0, 'elapsed_seconds': round(time.monotonic()-tick, 3)}
    save(output/'summary.json', summary)
    artifacts = [ref(p) for p in (candidate_path, field_path, example_path,
                                  output/'additional-type-examples.json', output/'summary.json')]
    receipt = {'version': VERSION, 'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
               'input_evidence': ref(evidence_path), 'source_database_before': source_before,
               'source_database_after': source_after, 'source_summary': ref(upstream_path),
               'source_read_mode': 'mode=ro, query_only=ON, BEGIN; frozen realpath',
               'code_and_contract': code_refs, 'protected_operational_files_unchanged': protected_after,
               'output_realpath': str(output), 'available_bytes_before': space.free,
               'artifacts': artifacts, 'processed_mapping_instances': totals['rows'],
               'source_text_reopened': False, 'new_model_or_network_calls': 0,
               'source_or_canonical_db_writes': 0}
    save(output/'execution-receipt.json', receipt)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.evidence, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
