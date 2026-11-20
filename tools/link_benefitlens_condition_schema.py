"""Incremental, source-bound notice mappings into a derived SQL link contract.

No model, network, source database or canonical database writes. A completed
source/schema link preserves an unreviewed extraction recipe, not a typed rule.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import fcntl
from functools import lru_cache
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from jsonschema.exceptions import ValidationError

from audit_benefitlens_experience_production import jsonl
from benefitlens_compact_notice import validate_group
from benefitlens_source_storage import read_source_bytes
from build_benefitlens_compact_handoff import verify_group, verify_inherited_lock_fd
from expand_benefitlens_personalization import question_projection, rebound_representative

REPO = Path(__file__).resolve().parents[1]
BASE = Path('/home/longnew/.cache/datasets/benefitlens/scale50k-20261109/team-production-20261119')
VERSION = 'condition-schema-links-v1'
CONFIG = REPO/'pipeline/config/personalization_expansion_v1.json'
CATALOG = REPO/'pipeline/config/shared_condition_catalog_v1.json'
CODE_INPUTS = [Path(__file__), REPO/'tools/build_benefitlens_compact_handoff.py',
    REPO/'tools/benefitlens_compact_notice.py', REPO/'tools/expand_benefitlens_personalization.py',
    REPO/'tools/benefitlens_source_storage.py', CONFIG, CATALOG,
    REPO/'pipeline/schemas/common-fields.schema.json', REPO/'pipeline/schemas/opportunity.schema.json',
    REPO/'pipeline/schemas/team-handoff.schema.json']


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return sha256(value if isinstance(value, bytes) else encoded(value).encode()).hexdigest()


def file_hash(path):
    state = sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            state.update(block)
    return state.hexdigest()


def readonly(path):
    cx = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)
    cx.row_factory = sqlite3.Row
    cx.execute('PRAGMA query_only=ON')
    cx.execute('BEGIN')
    return cx


class DependencyError(ValueError):
    pass


@contextmanager
def exclusive_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield handle.fileno()


SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE dependencies(path TEXT PRIMARY KEY,sha256 TEXT NOT NULL,role TEXT NOT NULL);
CREATE TABLE catalog_fields(field_id TEXT PRIMARY KEY,field_scope TEXT NOT NULL
 CHECK(field_scope IN ('common_field','program_specific')),definition_json TEXT NOT NULL,
 definition_sha256 TEXT NOT NULL,catalog_sha256 TEXT NOT NULL,target_contract TEXT NOT NULL);
CREATE TABLE analyzed_groups(group_id TEXT PRIMARY KEY,group_fingerprint TEXT NOT NULL,
 result_sha256 TEXT NOT NULL,receipt_path TEXT NOT NULL,receipt_sha256 TEXT NOT NULL,
 relevant_mapping_count INTEGER NOT NULL,semantic_review TEXT NOT NULL CHECK(semantic_review='unreviewed'),
 result_json TEXT NOT NULL,execution_json TEXT NOT NULL);
CREATE TABLE documents(document_id TEXT,text_sha256 TEXT,text_path TEXT NOT NULL,
 PRIMARY KEY(document_id,text_sha256));
CREATE TABLE candidate_sources(candidate_key TEXT PRIMARY KEY,document_id TEXT NOT NULL,
 text_sha256 TEXT NOT NULL,source_url TEXT NOT NULL,
 FOREIGN KEY(document_id,text_sha256) REFERENCES documents(document_id,text_sha256));
CREATE TABLE occurrences(occurrence_id TEXT PRIMARY KEY,group_id TEXT NOT NULL REFERENCES analyzed_groups,
 candidate_key TEXT NOT NULL REFERENCES candidate_sources,document_id TEXT NOT NULL,text_sha256 TEXT NOT NULL,
 char_start INTEGER NOT NULL,char_end INTEGER NOT NULL,context_start INTEGER NOT NULL,context_end INTEGER NOT NULL,
 sentence_index INTEGER,FOREIGN KEY(document_id,text_sha256) REFERENCES documents(document_id,text_sha256),
 CHECK(0<=context_start AND context_start<=char_start AND char_start<char_end AND char_end<=context_end));
CREATE TABLE mappings(mapping_id TEXT PRIMARY KEY,occurrence_id TEXT NOT NULL REFERENCES occurrences,
 group_id TEXT NOT NULL REFERENCES analyzed_groups,candidate_key TEXT NOT NULL REFERENCES candidate_sources,
 field_id TEXT NOT NULL REFERENCES catalog_fields,mapping_index INTEGER NOT NULL,program_specific_key TEXT,
 statement_type TEXT NOT NULL,subject TEXT NOT NULL,modality TEXT NOT NULL,reference_phase TEXT NOT NULL,
 operator TEXT NOT NULL,value_extraction_method TEXT NOT NULL,unit TEXT,value_quote TEXT,
 semantic_review TEXT NOT NULL CHECK(semantic_review='unreviewed'),
 executable INTEGER NOT NULL DEFAULT 0 CHECK(executable=0),canonical_rule_id TEXT CHECK(canonical_rule_id IS NULL),
 validation_fingerprint TEXT NOT NULL,record_json TEXT NOT NULL);
CREATE INDEX mapping_candidate ON mappings(candidate_key);
CREATE INDEX mapping_group ON mappings(group_id);
CREATE TABLE requirement_routes(mapping_id TEXT PRIMARY KEY REFERENCES mappings,
 candidate_key TEXT NOT NULL REFERENCES candidate_sources,route TEXT NOT NULL,
 parent_requirement_sha256 TEXT NOT NULL,requirement_sha256 TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE source_bindings(binding_id TEXT PRIMARY KEY,mapping_id TEXT NOT NULL REFERENCES mappings,
 document_id TEXT NOT NULL,text_sha256 TEXT NOT NULL,property TEXT NOT NULL,
 char_start INTEGER NOT NULL,char_end INTEGER NOT NULL,exact_quote TEXT NOT NULL,
 binding_json TEXT NOT NULL,FOREIGN KEY(document_id,text_sha256) REFERENCES documents(document_id,text_sha256),
 CHECK(0<=char_start AND char_start<char_end));
CREATE TABLE unresolved(entity_kind TEXT,entity_id TEXT,reason TEXT NOT NULL,
 PRIMARY KEY(entity_kind,entity_id));
CREATE VIEW condition_schema_links AS SELECT m.mapping_id,m.group_id,m.occurrence_id,m.candidate_key,
 m.field_id,f.field_scope,m.program_specific_key,f.definition_sha256,f.catalog_sha256,f.target_contract,
 r.route AS requirement_route,r.parent_requirement_sha256,r.requirement_sha256,
 m.statement_type,m.subject,m.modality,m.reference_phase,m.operator,m.value_extraction_method,m.unit,m.value_quote,
 o.document_id,o.text_sha256,c.source_url,g.receipt_path,g.receipt_sha256,
 m.semantic_review,m.executable,m.canonical_rule_id,m.validation_fingerprint
 FROM mappings m JOIN analyzed_groups g USING(group_id) JOIN occurrences o USING(occurrence_id)
 JOIN catalog_fields f USING(field_id) JOIN requirement_routes r USING(mapping_id)
 JOIN candidate_sources c ON c.candidate_key=m.candidate_key;
'''


def _receipt_lineage(path, dependencies, cache):
    """Rehash every receipt artifact once per refresh before reusing validation."""
    if path in cache:
        return cache[path]
    receipt = json.loads(Path(path).read_bytes())
    hashes = [(str(Path(path)), file_hash(path))]
    dependencies[str(Path(path))] = (hashes[0][1], 'execution_receipt')

    def refs(item):
        if isinstance(item, dict):
            if 'path' in item and 'sha256' in item:
                yield item
            else:
                for value in item.values():
                    yield from refs(value)
        elif isinstance(item, list):
            for value in item:
                yield from refs(value)

    for ref in refs(receipt):
        refpath = str(Path(ref['path']))
        actual = dependencies.get(refpath, (None,))[0] or file_hash(refpath)
        if actual != ref['sha256']:
            raise ValueError('receipt_artifact_hash_mismatch')
        dependencies[refpath] = (actual, 'execution_artifact')
        hashes.append((refpath, actual))
    value = digest(sorted(hashes)), hashes[0][1]
    cache[path] = value
    return value


def _previous(output):
    """Never trust a damaged derived cache as proof of prior validation."""
    summary_path = output/'summary.json'
    database = output/'conditions.sqlite'
    if not summary_path.exists() or not database.exists():
        return None
    try:
        summary = json.loads(summary_path.read_text())
        if summary.get('version') != VERSION or file_hash(database) != summary['database']['sha256']:
            return None
        cx = readonly(database)
        if cx.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            cx.close()
            return None
        return cx
    except (ValueError, KeyError, OSError, sqlite3.Error):
        return None


def _prepare_inputs(base, requirements_path, notice_path):
    summary_path = requirements_path.parent/'summary.json'
    for path in (base/'compact/queue.sqlite', requirements_path, summary_path, notice_path):
        if not path.is_file():
            raise DependencyError('missing_dependency: '+str(path))
    summary = json.loads(summary_path.read_bytes())
    notice_refs = {}
    for row in jsonl(notice_path):
        # No lexical conditions or candidate inventory are copied to the output.
        notice_refs[row['candidate_key']] = {
            'original_ref': row.get('original_ref'), 'source_url': row.get('source_url')}
    return summary_path, summary, notice_refs


def _verify_input_snapshot(cx, summary, catalog_sha, notice_refs):
    if summary.get('catalog', {}).get('sha256') != catalog_sha:
        raise DependencyError('stale_catalog_in_personalization')
    counters = summary.get('counters', {})
    count = cx.execute('SELECT count(*) FROM mappings').fetchone()[0]
    if count != sum(counters.get('field_mapping_instances', {}).values()):
        raise DependencyError('incomplete_personalization_mapping_snapshot')
    requirements = cx.execute('SELECT count(*) FROM requirements').fetchone()[0]
    if requirements != counters.get('notice_projection', {}).get('all_notices') or requirements != len(notice_refs):
        raise DependencyError('incomplete_personalization_requirement_snapshot')
    return count


def _insert_link(db, record, group, occurrence, source_ref, source_url):
    m = record['mapping']
    mid = record['mapping_id']
    did, textsha = occurrence['document_id'], occurrence['text_sha256']
    db.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?)', (did, textsha, source_ref['text_path']))
    db.execute('INSERT OR IGNORE INTO candidate_sources VALUES(?,?,?,?)',
        (record['candidate_key'], did, textsha, source_url))
    db.execute('INSERT OR IGNORE INTO occurrences VALUES(?,?,?,?,?,?,?,?,?,?)', tuple(occurrence[k] for k in (
        'occurrence_id', 'group_id', 'record_key', 'document_id', 'text_sha256',
        'char_start', 'char_end', 'context_start', 'context_end', 'sentence_index')))
    db.execute('INSERT INTO mappings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
        mid, occurrence['occurrence_id'], group['group_id'], record['candidate_key'], m['field_id'],
        record['mapping_index'], m['program_specific_key'], m['statement_type'], m['subject'], m['modality'],
        m['reference_phase'], m['operator'], m['value_extraction_method'], m['unit'], m['value_quote'],
        'unreviewed', 0, None, record['validation_fingerprint'], encoded(record)))
    q = record['input_requirement']
    db.execute('INSERT INTO requirement_routes VALUES(?,?,?,?,?,?)', (mid, record['candidate_key'], q['route'],
        record['parent_requirement_sha256'], digest(q), encoded(q)))
    for index, binding in enumerate(record['bindings']):
        db.execute('INSERT INTO source_bindings VALUES(?,?,?,?,?,?,?,?,?)',
            (mid+':'+str(index), mid, did, textsha, binding['property'], binding['char_start'],
             binding['char_end'], binding['exact_source_quote'], encoded(binding)))


def build(base, output, requirements_path=None, notice_path=None):
    """Caller must hold shared artifact and personalization locks and output lock."""
    base, output = Path(base), Path(output)
    requirements_path = requirements_path or base/'team-expansion-20261120/experience-personalization/personalization/requirements.sqlite'
    notice_path = notice_path or base/'notice/notices.jsonl.gz'
    config = json.loads(CONFIG.read_bytes())
    catalog_raw = CATALOG.read_bytes()
    catalog, catalog_sha = json.loads(catalog_raw), sha256(catalog_raw).hexdigest()
    dependencies = {str(p): (file_hash(p), 'connector_contract') for p in CODE_INPUTS}
    contract_hash = digest(dependencies)
    summary_path, upstream_summary, notice_refs = _prepare_inputs(base, requirements_path, notice_path)
    dependencies[str(summary_path)] = (file_hash(summary_path), 'personalization_summary')
    dependencies[str(notice_path)] = (file_hash(notice_path), 'notice_source_inventory')
    initial_files = {str(p): (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns)
                     for p in (requirements_path, summary_path, notice_path)}
    generations = output/'generations'
    generations.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.build-', dir=generations))
    previous = _previous(output)
    db = queue = requirements = None
    try:
        queue, requirements = readonly(base/'compact/queue.sqlite'), readonly(requirements_path)
        raw_count = _verify_input_snapshot(requirements, upstream_summary, catalog_sha, notice_refs)
        db = sqlite3.connect(temporary/'conditions.sqlite')
        db.executescript(SCHEMA)
        for field, definition in catalog['fields'].items():
            scope = 'program_specific' if field == 'program_specific.requirement' else 'common_field'
            db.execute('INSERT INTO catalog_fields VALUES(?,?,?,?,?,?)',
                (field, scope, encoded(definition), digest(definition), catalog_sha,
                 'shared-condition-catalog-v1/condition_contract; opportunity.schema.json#/$defs/condition (review-gated)'))
        stats, reasons = Counter(), Counter()
        group_refs, receipt_cache, lineage_cache = {}, {}, {}
        queue_states = dict(queue.execute("SELECT state,count(*) FROM groups WHERE kind='notice' GROUP BY state"))
        group_stream = sha256()
        for row in queue.execute("SELECT * FROM groups WHERE kind='notice' AND state='analyzed' ORDER BY group_id"):
            row = dict(row)
            group_stream.update((encoded(row)+'\n').encode())
            try:
                if not row['receipt_path']:
                    raise ValueError('model_receipt_missing')
                lineage_sha, receipt_sha = _receipt_lineage(row['receipt_path'], dependencies, lineage_cache)
                fingerprint = digest([row, lineage_sha, contract_hash])
                old = previous.execute('SELECT * FROM analyzed_groups WHERE group_id=?', (row['group_id'],)).fetchone() if previous else None
                if old and old['group_fingerprint'] == fingerprint:
                    result, execution = json.loads(old['result_json']), json.loads(old['execution_json'])
                    stats['reused_verified_groups'] += 1
                else:
                    result, execution = verify_group(row, receipt_cache)
                    stats['newly_verified_groups'] += 1
                # Validation replays the preserved model result; review is never promoted.
                if result.get('semantic_review', 'unreviewed') != 'unreviewed':
                    raise ValueError('unsupported_semantic_review_state')
                if not isinstance(result.get('mappings'), list):
                    raise ValueError('missing_semantic_mappings')
                group_refs[row['group_id']] = {
                    'group_id': row['group_id'], 'result': result, 'fingerprint': fingerprint,
                    'representative': json.loads(row['representative_json']), 'receipt_path': row['receipt_path']}
                db.execute('INSERT INTO analyzed_groups VALUES(?,?,?,?,?,?,?,?,?)', (
                    row['group_id'], fingerprint, digest(result), row['receipt_path'], receipt_sha,
                    len(result['mappings']), 'unreviewed', encoded(result), encoded(execution)))
            except (ValueError, KeyError, TypeError, OSError, ValidationError) as error:
                reason = str(error)[:240] or type(error).__name__
                reasons['group:'+reason] += 1
                db.execute('INSERT INTO unresolved VALUES(?,?,?)', ('group', row['group_id'], reason))

        @lru_cache(maxsize=8)
        def text_for(did, textsha, path):
            raw = read_source_bytes(path)
            if sha256(raw).hexdigest() != textsha:
                raise ValueError('source_text_hash_mismatch')
            # The decoded source hash, not a compressed blob hash, binds offsets.
            return raw.decode('utf8')

        used_parent_hashes = {}

        @lru_cache(maxsize=2)
        def parent_for(candidate):
            row = requirements.execute('SELECT payload FROM requirements WHERE candidate_key=?', (candidate,)).fetchone()
            if not row:
                raise ValueError('parent_requirement_missing')
            parent = json.loads(row['payload'])
            if parent.get('candidate_key') != candidate or parent.get('catalog_sha256') != catalog_sha:
                raise ValueError('parent_requirement_scope_or_catalog_mismatch')
            lookup = {}
            for route, questions in parent['input_channels'].items():
                for question in questions:
                    key = question['mapping_id']
                    if key in lookup or question['route'] != route:
                        raise ValueError('parent_requirement_duplicate_or_wrong_route')
                    lookup[key] = question
            parent_sha = digest(parent)
            used_parent_hashes[candidate] = parent_sha
            return parent_sha, lookup

        @lru_cache(maxsize=4)
        def occurrence_for(oid):
            row = queue.execute('SELECT * FROM occurrences WHERE occurrence_id=?', (oid,)).fetchone()
            if not row:
                raise ValueError('source_occurrence_missing')
            return dict(row)

        @lru_cache(maxsize=4)
        def bound_for(oid):
            occurrence = occurrence_for(oid)
            group = group_refs[occurrence['group_id']]
            notice = notice_refs[occurrence['record_key']]
            ref = notice['original_ref']
            if not ref or (ref['document_id'], ref['text_sha256']) != (occurrence['document_id'], occurrence['text_sha256']):
                raise ValueError('candidate_source_version_mismatch')
            if occurrence['source_url'] != notice['source_url']:
                raise ValueError('candidate_source_url_mismatch')
            text = text_for(occurrence['document_id'], occurrence['text_sha256'], ref['text_path'])
            representative = rebound_representative(occurrence, group['representative'], text)
            raw = {'group_id': group['group_id'], 'mappings': group['result']['mappings'],
                   'skip_reason': group['result']['skip_reason']}
            return validate_group(raw, {'group_id': group['group_id'], 'representative': representative, 'occurrence_count': 1})

        mapping_stream = sha256()
        for row in requirements.execute('SELECT * FROM mappings ORDER BY candidate_key,group_id,mapping_id'):
            row = dict(row)
            mapping_stream.update((encoded(row)+'\n').encode())
            try:
                record = json.loads(row['payload'])
                for key in ('mapping_id', 'candidate_key', 'group_id', 'field_id', 'subject', 'route'):
                    expected = (record.get('mapping') or {}).get(key) if key in ('field_id', 'subject') else (record.get('input_requirement') or {}).get(key) if key == 'route' else record.get(key)
                    if row[key] != expected:
                        raise ValueError('mapping_row_payload_identity_mismatch')
                group = group_refs.get(row['group_id'])
                if group is None:
                    raise ValueError('mapping_group_has_no_verified_model_completion')
                m = record['mapping']
                if record.get('semantic_review') != 'unreviewed' or record.get('personal_eligibility_decision') != 'unknown':
                    raise ValueError('upstream_review_or_eligibility_boundary_changed')
                if record.get('catalog_sha256') != catalog_sha or m['field_id'] not in catalog['fields']:
                    raise ValueError('mapping_catalog_mismatch')
                if record.get('reference_receipt') != group['receipt_path']:
                    raise ValueError('mapping_receipt_mismatch')
                occurrence = occurrence_for(record['occurrence_id'])
                if occurrence['kind'] != 'notice' or occurrence['group_id'] != row['group_id'] or occurrence['record_key'] != row['candidate_key']:
                    raise ValueError('mapping_occurrence_scope_mismatch')
                if any(record['source_spans'][key] != occurrence[key] for key in ('char_start', 'char_end', 'context_start', 'context_end', 'sentence_index')):
                    raise ValueError('mapping_source_offsets_mismatch')
                if (record['document_id'], record['text_sha256']) != (occurrence['document_id'], occurrence['text_sha256']):
                    raise ValueError('mapping_document_version_mismatch')
                notice = notice_refs[row['candidate_key']]
                source_ref = notice['original_ref']
                if not source_ref or (source_ref['document_id'], source_ref['text_sha256']) != (occurrence['document_id'], occurrence['text_sha256']):
                    raise ValueError('candidate_source_version_mismatch')
                if notice['source_url'] != occurrence['source_url']:
                    raise ValueError('candidate_source_url_mismatch')
                text_for(occurrence['document_id'], occurrence['text_sha256'], source_ref['text_path'])
                indices = [i for i, value in enumerate(group['result']['mappings'])
                    if value == m and 'requirement-'+sha256(encoded([occurrence['occurrence_id'], i, value]).encode()).hexdigest()[:28] == row['mapping_id']]
                if len(indices) != 1:
                    raise ValueError('mapping_not_in_actual_model_result')
                index = indices[0]
                q = question_projection(m, row['mapping_id'], catalog, config)
                parent_sha, parent_questions = parent_for(row['candidate_key'])
                if q != record['input_requirement'] or parent_questions.get(row['mapping_id']) != q:
                    raise ValueError('requirement_route_or_projection_mismatch')
                fingerprint = digest([row, group['fingerprint'], occurrence, source_ref, notice['source_url'], parent_sha, contract_hash])
                old = previous.execute('SELECT validation_fingerprint,record_json FROM mappings WHERE mapping_id=?', (row['mapping_id'],)).fetchone() if previous else None
                if old and old['validation_fingerprint'] == fingerprint:
                    linked = json.loads(old['record_json'])
                    stats['reused_verified_mappings'] += 1
                else:
                    bound = bound_for(occurrence['occurrence_id'])
                    bindings = [b for b in bound['source_bindings'] if b['mapping_index'] == index]
                    if bindings != record['bindings']:
                        raise ValueError('mapping_source_quote_bindings_mismatch')
                    linked = {'mapping_id': row['mapping_id'], 'candidate_key': row['candidate_key'],
                        'mapping_index': index, 'mapping': m, 'bindings': bindings, 'input_requirement': q,
                        'parent_requirement_sha256': parent_sha, 'validation_fingerprint': fingerprint}
                    stats['newly_verified_mappings'] += 1
                _insert_link(db, linked, group, occurrence, source_ref, notice['source_url'])
            except (ValueError, KeyError, TypeError, OSError, ValidationError) as error:
                reason = str(error)[:240] or type(error).__name__
                reasons['mapping:'+reason] += 1
                db.execute('INSERT INTO unresolved VALUES(?,?,?)', ('mapping', row['mapping_id'], reason))

        fk = list(db.execute('PRAGMA foreign_key_check'))
        if fk:
            raise ValueError('derived_foreign_key_violation')
        count = lambda sql: db.execute(sql).fetchone()[0]
        count_mappings = count('SELECT count(*) FROM mappings')
        linked_groups = count('SELECT count(DISTINCT group_id) FROM mappings')
        actual_groups = count('SELECT count(*) FROM analyzed_groups')
        relevant_groups = count('SELECT count(*) FROM analyzed_groups WHERE relevant_mapping_count>0')
        counts = {
            'actual_llm_analyzed_distinct_notice_groups': actual_groups,
            'actual_llm_relevant_distinct_notice_groups': relevant_groups,
            'actual_llm_relevant_reference_mappings': count('SELECT coalesce(sum(relevant_mapping_count),0) FROM analyzed_groups'),
            'mapping_instances_in_requirements_input': raw_count,
            'verified_relevant_mapping_instances': count_mappings,
            'verified_linked_unique_candidate_notices': count('SELECT count(*) FROM candidate_sources'),
            'verified_linked_distinct_notice_groups': linked_groups,
            'verified_linked_source_occurrences': count('SELECT count(*) FROM occurrences'),
            'verified_source_quote_bindings': count('SELECT count(*) FROM source_bindings'),
            'common_field_mapping_instances': count("SELECT count(*) FROM mappings WHERE field_id!='program_specific.requirement'"),
            'program_specific_mapping_instances': count("SELECT count(*) FROM mappings WHERE field_id='program_specific.requirement'"),
            'source_schema_link_complete_mapping_instances': count_mappings,
            'source_verified_question_route_links': count("SELECT count(*) FROM requirement_routes WHERE route IN ('common_profile','program_followup','optional_followup')"),
            'content_approved_canonical_rules': 0,
            'lexical_only_candidates_counted_as_semantic_complete': 0,
            'independent_content_reviews': 0}
        for path, (sha, role) in sorted(dependencies.items()):
            db.execute('INSERT INTO dependencies VALUES(?,?,?)', (path, sha, role))
        source_inputs = {
            'compact_read_mode': 'read_only_sqlite_transaction',
            'compact_database': str(base/'compact/queue.sqlite'),
            'analyzed_notice_group_rows_sha256': group_stream.hexdigest(),
            'queue_notice_states_at_read': queue_states,
            'requirements_database': str(requirements_path),
            'requirements_mapping_rows_sha256': mapping_stream.hexdigest(),
            'linked_parent_requirement_rows_sha256': digest(used_parent_hashes),
            'requirements_summary': {'path': str(summary_path), 'sha256': dependencies[str(summary_path)][0]},
            'catalog': {'path': str(CATALOG), 'sha256': catalog_sha, 'fields': len(catalog['fields'])},
            'connector_contract_sha256': contract_hash,
            'dependency_manifest_sha256': digest(dependencies)}
        generation = digest([source_inputs, counts, sorted(reasons.items())])
        summary = {'version': VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
            'state': 'source_schema_links_complete' if not reasons else 'source_schema_links_partial',
            'generation': generation, 'counts': counts, 'incremental': dict(stats),
            'freshness': {
                'personalization_reference_notice_groups': upstream_summary['reference_semantic_notice_groups'],
                'queue_analyzed_notice_groups_at_read': queue_states.get('analyzed', 0),
                'analyzed_groups_since_personalization_snapshot': max(0, queue_states.get('analyzed', 0)-upstream_summary['reference_semantic_notice_groups']),
                'verified_relevant_groups_without_derived_mapping': relevant_groups-linked_groups,
                'upstream_projection_may_lag_live_queue': queue_states.get('analyzed', 0) != upstream_summary['reference_semantic_notice_groups']},
            'integrity': {'sqlite_foreign_key_violations': len(fk),
                'missing_requirement_routes': count_mappings-count('SELECT count(*) FROM requirement_routes'),
                'complete_route_view_rows': count('SELECT count(*) FROM condition_schema_links'),
                'unresolved_groups': count("SELECT count(*) FROM unresolved WHERE entity_kind='group'"),
                'unresolved_mapping_instances': count("SELECT count(*) FROM unresolved WHERE entity_kind='mapping'"),
                'reason_counts': dict(sorted(reasons.items()))},
            'source_inputs': source_inputs,
            'by_field': dict(db.execute('SELECT field_id,count(*) FROM mappings GROUP BY field_id')),
            'by_requirement_route': dict(db.execute('SELECT route,count(*) FROM requirement_routes GROUP BY route')),
            'by_field_scope_and_requirement_route': [dict(zip(('field_scope','route','mapping_instances'), row))
                for row in db.execute('SELECT f.field_scope,r.route,count(*) FROM mappings m JOIN catalog_fields f USING(field_id) JOIN requirement_routes r USING(mapping_id) GROUP BY f.field_scope,r.route')],
            'database': {'path': str(output/'conditions.sqlite'), 'sha256': None},
            'model_calls': 0, 'network_requests': 0, 'source_database_writes': 0, 'canonical_database_writes': 0,
            'current_open_claims_created': 0, 'personal_eligibility_decisions_issued': 0,
            'completion_scope': 'Verified stored model group, exact source quote, catalog field and requirement route; selected expressions only.',
            'canonical_rule_state': 'held_unreviewed_recipe_not_executable_no_typed_value_or_round_identity_created'}
        for key, value in {'version': VERSION, 'generation': generation, 'source_inputs': source_inputs}.items():
            db.execute('INSERT INTO metadata VALUES(?,?)', (key, encoded(value)))
        db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('derived_sqlite_integrity_check_failed')
        db.close()
        db = None
        for path, stamp in initial_files.items():
            p = Path(path)
            if (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) != stamp:
                raise DependencyError('dependency_changed_during_read: '+path)
        summary['database']['sha256'] = file_hash(temporary/'conditions.sqlite')
        (temporary/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
        destination = generations/generation
        if destination.exists():
            # Identical inputs keep the original complete generation and counts.
            old_summary = json.loads((destination/'summary.json').read_text())
            if file_hash(destination/'conditions.sqlite') != old_summary['database']['sha256']:
                raise ValueError('existing_generation_database_damaged')
            shutil.rmtree(temporary)
            summary = old_summary
        else:
            temporary.rename(destination)
        for name, target in [('current', 'generations/'+generation),
                             ('summary.json', 'current/summary.json'), ('conditions.sqlite', 'current/conditions.sqlite')]:
            temp_link = output/('.'+name+'.new')
            temp_link.unlink(missing_ok=True)
            temp_link.symlink_to(target)
            os.replace(temp_link, output/name)
        return summary
    finally:
        for connection in (db, queue, requirements, previous):
            if connection is not None:
                connection.close()
        if temporary.exists():
            shutil.rmtree(temporary)


def run(base=BASE, output=None, requirements_path=None, notice_path=None, artifact_lock_fd=None):
    base = Path(base)
    output = Path(output) if output else base/'team-expansion-20261120/semantic-connections/conditions'
    requirements_path = Path(requirements_path) if requirements_path else base/'team-expansion-20261120/experience-personalization/personalization/requirements.sqlite'
    artifact_lock = base/'compact/handoff/.refresh.lock'
    with ExitStack() as stack:
        if artifact_lock_fd is None:
            stack.enter_context(exclusive_lock(artifact_lock))
        else:
            verify_inherited_lock_fd(artifact_lock_fd, artifact_lock)
            fcntl.flock(artifact_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stack.enter_context(exclusive_lock(requirements_path.parent/'campaign.lock'))
        stack.enter_context(exclusive_lock(output/'.refresh.lock'))
        return build(base, output, requirements_path, Path(notice_path) if notice_path else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=BASE)
    parser.add_argument('--output-root', type=Path)
    parser.add_argument('--requirements-db', type=Path)
    parser.add_argument('--notice-inventory', type=Path)
    parser.add_argument('--artifact-lock-fd', type=int)
    args = parser.parse_args()
    try:
        result = run(args.base, args.output_root, args.requirements_db, args.notice_inventory, args.artifact_lock_fd)
    except BlockingIOError:
        print(encoded({'state': 'refresh_skipped_lock_busy', 'model_calls': 0}))
        return 75
    except (DependencyError, OSError, ValueError, sqlite3.Error) as error:
        print(encoded({'state': 'condition_schema_link_refresh_failed', 'error': str(error)[:500], 'model_calls': 0}))
        return 1
    print(encoded(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
