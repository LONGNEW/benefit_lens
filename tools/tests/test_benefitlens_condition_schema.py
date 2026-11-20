"""Isolated synthetic receipt fixtures never invoke or report real model work."""
from collections import Counter
from copy import deepcopy
import fcntl
import gzip
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benefitlens_compact_notice as compact
import link_benefitlens_condition_schema as connector
from expand_benefitlens_personalization import question_projection, rebound_representative


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False)+'\n')
    return {'path': str(path), 'sha256': sha256(path.read_bytes()).hexdigest()}


def write_lines(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, 'wt', encoding='utf8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False)+'\n')


def setup_fixture(root):
    base = root/'source'
    queue_path = base/'compact/queue.sqlite'
    queue_path.parent.mkdir(parents=True)
    personal = base/'team-expansion-20261120/experience-personalization/personalization'
    personal.mkdir(parents=True)
    config = json.loads(connector.CONFIG.read_text())
    catalog = json.loads(connector.CATALOG.read_text())
    catalog_sha = sha256(connector.CATALOG.read_bytes()).hexdigest()
    notices, occurrences, groups, texts = [], [], {}, {}
    text = ('You must be currently enrolled.\n'
            'You must hold a local safety license.\n'
            'The institution must nominate applicants.\n'
            'Required information about our organization.')
    for index in range(2):
        candidate, did = 'candidate-'+str(index), 'doc-'+str(index)
        source = ('' if index == 0 else 'Preserved historical version.\n')+text
        path = base/(did+'.txt')
        path.write_text(source)
        ref = {'document_id': did, 'text_path': str(path), 'text_sha256': sha256(source.encode()).hexdigest()}
        notice = {'candidate_key': candidate, 'record_type': 'production_candidate',
            'source_url': 'https://example.test/notices/'+str(index), 'original_ref': ref}
        notices.append(notice)
        texts[did] = source
        for occurrence in compact.extract_contexts(notice, source, context_sentences=0):
            occurrences.append(occurrence)
            group = groups.setdefault(occurrence['group_id'], {'group_id': occurrence['group_id'],
                'representative': occurrence, 'occurrence_count': 0})
            group['occurrence_count'] += 1
    assert len(groups) == 4
    batch = compact.build_batch(list(groups.values()))
    raw = []
    for group in groups.values():
        focal = group['representative']['text']
        mapping = {'field_id': 'program_specific.requirement', 'program_specific_key': 'safety_license',
            'operator': 'exists', 'value_extraction_method': 'literal_text', 'value_quote': None,
            'unit': None, 'reference_phase': 'application', 'statement_type': 'eligibility_requirement',
            'subject': 'applicant', 'modality': 'required', 'logic_cues': [], 'evidence_quote': focal,
            'ambiguities': []}
        if 'currently' in focal:
            mapping.update(field_id='education.status', program_specific_key=None,
                           operator='eq', value_quote='currently enrolled')
        elif 'institution' in focal:
            mapping.update(program_specific_key='institution_nomination', statement_type='institution_requirement', subject='institution')
        result = {'group_id': group['group_id'], 'mappings': [] if 'organization' in focal else [mapping],
                  'skip_reason': 'Synthetic organization description.' if 'organization' in focal else None}
        raw.append(result)
    checked = [compact.validate_group(result, groups[result['group_id']]) for result in raw]
    request_ref = save(base/'request.json', batch)
    result_ref = save(base/'result.json', {'groups': raw})
    completion = {'agent_name': '/root/synthetic_test_fixture', 'invocation_id': 'synthetic-test-only',
        'input_sha256': batch['input_sha256'], 'response_sha256': result_ref['sha256'],
        'status': 'completed', 'completion_message': 'Synthetic contract fixture, not a real model execution.'}
    receipt = {'provider': 'codex_subagent', 'kind': 'notice', 'agent_name': completion['agent_name'],
        'model_called': True, 'invocation_id': completion['invocation_id'], 'input_sha256': batch['input_sha256'],
        'request_ref': request_ref, 'response_ref': result_ref, 'result_ref': result_ref,
        'completion_message_ref': save(base/'completion.json', completion),
        'usage': None, 'independent_usage_telemetry_available': False,
        'group_results': [{'group_id': result['group_id'], 'result_sha256': connector.digest(result),
            'raw_result_sha256': connector.digest(raw[index]), 'result_pointer': '/groups/'+str(index)}
            for index, result in enumerate(checked)]}
    receipt_path = base/'receipt.json'
    save(receipt_path, receipt)
    with sqlite3.connect(queue_path) as queue:
        queue.executescript('''CREATE TABLE groups(group_id TEXT PRIMARY KEY,kind TEXT,group_key TEXT,
            representative_json TEXT,occurrence_count INTEGER,state TEXT,result_json TEXT,receipt_path TEXT);
            CREATE TABLE occurrences(occurrence_id TEXT PRIMARY KEY,group_id TEXT,kind TEXT,
            record_key TEXT,document_id TEXT,text_sha256 TEXT,char_start INTEGER,char_end INTEGER,
            context_start INTEGER,context_end INTEGER,source_url TEXT,source_role TEXT,genre TEXT,
            sentence_index INTEGER,paragraph_position TEXT,model_representative INTEGER);''')
        for result in checked:
            group = groups[result['group_id']]
            queue.execute('INSERT INTO groups VALUES(?,?,?,?,?,?,?,?)', (result['group_id'], 'notice',
                group['representative']['group_key'], connector.encoded(group['representative']),
                group['occurrence_count'], 'analyzed', connector.encoded(result), str(receipt_path)))
        for occurrence in occurrences:
            keys = ('occurrence_id', 'group_id', 'record_key', 'document_id', 'text_sha256', 'char_start',
                    'char_end', 'context_start', 'context_end', 'source_url', 'source_role', 'genre',
                    'sentence_index', 'paragraph_position')
            values = [occurrence[key] for key in keys]
            values.insert(2, 'notice')
            values.append(1)
            queue.execute('INSERT INTO occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', values)
    mapping_records = []
    requirements = {}
    for occurrence in occurrences:
        annotation = next(row for row in checked if row['group_id'] == occurrence['group_id'])
        rep = rebound_representative(occurrence, groups[occurrence['group_id']]['representative'], texts[occurrence['document_id']])
        bound = compact.validate_group({'group_id': annotation['group_id'], 'mappings': annotation['mappings'],
            'skip_reason': annotation['skip_reason']}, {'group_id': annotation['group_id'], 'representative': rep, 'occurrence_count': 1})
        for index, mapping in enumerate(bound['mappings']):
            mid = 'requirement-'+sha256(connector.encoded([occurrence['occurrence_id'], index, mapping]).encode()).hexdigest()[:28]
            q = question_projection(mapping, mid, catalog, config)
            record = {'mapping_id': mid, 'candidate_key': occurrence['record_key'], 'group_id': occurrence['group_id'],
                'occurrence_id': occurrence['occurrence_id'], 'document_id': occurrence['document_id'],
                'text_sha256': occurrence['text_sha256'], 'source_spans': {key: occurrence[key] for key in
                    ('char_start', 'char_end', 'context_start', 'context_end', 'sentence_index')},
                'mapping': mapping, 'bindings': [b for b in bound['source_bindings'] if b['mapping_index'] == index],
                'input_requirement': q, 'catalog_sha256': catalog_sha, 'reference_receipt': str(receipt_path),
                'semantic_review': 'unreviewed', 'personal_eligibility_decision': 'unknown'}
            mapping_records.append(record)
            parent = requirements.setdefault(occurrence['record_key'], {'candidate_key': occurrence['record_key'],
                'catalog_sha256': catalog_sha, 'input_channels': {}})
            parent['input_channels'].setdefault(q['route'], []).append(q)
    requirements_path = personal/'requirements.sqlite'
    with sqlite3.connect(requirements_path) as cx:
        cx.executescript('CREATE TABLE mappings(mapping_id TEXT PRIMARY KEY,candidate_key TEXT,group_id TEXT,field_id TEXT,route TEXT,subject TEXT,payload TEXT);CREATE TABLE requirements(candidate_key TEXT PRIMARY KEY,payload TEXT);')
        for record in mapping_records:
            cx.execute('INSERT INTO mappings VALUES(?,?,?,?,?,?,?)', (record['mapping_id'], record['candidate_key'],
                record['group_id'], record['mapping']['field_id'], record['input_requirement']['route'],
                record['mapping']['subject'], connector.encoded(record)))
        for candidate, parent in requirements.items():
            cx.execute('INSERT INTO requirements VALUES(?,?)', (candidate, connector.encoded(parent)))
    save(personal/'summary.json', {'reference_semantic_notice_groups': len(groups),
        'catalog': {'sha256': catalog_sha}, 'counters': {'field_mapping_instances':
            dict(Counter(r['mapping']['field_id'] for r in mapping_records)),
            'notice_projection': {'all_notices': len(notices)}}})
    write_lines(base/'notice/notices.jsonl.gz', notices)
    return base, root/'links', requirements_path


def output_db(output):
    cx = sqlite3.connect(output/'conditions.sqlite')
    cx.row_factory = sqlite3.Row
    return cx


def alter_mapping(path, transform):
    with sqlite3.connect(path) as cx:
        mid, payload = cx.execute('SELECT mapping_id,payload FROM mappings ORDER BY mapping_id LIMIT 1').fetchone()
        record = json.loads(payload)
        transform(record)
        cx.execute('UPDATE mappings SET payload=? WHERE mapping_id=?', (connector.encoded(record), mid))
    return mid


def test_actual_groups_routes_and_program_specific_scopes_are_separate(tmp_path):
    base, output, _ = setup_fixture(tmp_path)
    summary = connector.run(base, output)
    counts = summary['counts']
    assert counts['actual_llm_analyzed_distinct_notice_groups'] == 4
    assert counts['actual_llm_relevant_distinct_notice_groups'] == 3
    assert counts['verified_relevant_mapping_instances'] == 6
    assert counts['verified_linked_unique_candidate_notices'] == 2
    assert counts['common_field_mapping_instances'] == 2
    assert counts['program_specific_mapping_instances'] == 4
    assert counts['source_verified_question_route_links'] == 4
    assert counts['content_approved_canonical_rules'] == 0
    assert summary['by_requirement_route'] == {'common_profile': 2, 'program_followup': 2, 'external_or_program_information': 2}
    assert not summary['freshness']['upstream_projection_may_lag_live_queue']
    with output_db(output) as db:
        assert not list(db.execute('PRAGMA foreign_key_check'))
        assert db.execute('SELECT count(*) FROM condition_schema_links').fetchone()[0] == 6
        assert db.execute('SELECT count(*) FROM mappings WHERE executable OR canonical_rule_id IS NOT NULL').fetchone()[0] == 0
        sources = list(db.execute('SELECT l.candidate_key,l.document_id,o.char_start FROM condition_schema_links l JOIN occurrences o USING(occurrence_id) WHERE l.field_id=? ORDER BY l.candidate_key', ('education.status',)))
        assert sources[0]['document_id'] != sources[1]['document_id']
        assert sources[0]['char_start'] != sources[1]['char_start']


def test_unchanged_refresh_reuses_validation_and_keeps_atomic_generation(tmp_path, monkeypatch):
    base, output, _ = setup_fixture(tmp_path)
    first = connector.run(base, output)
    target = (output/'current').readlink()
    def unexpected(*args, **kwargs):
        raise AssertionError('Unchanged verified groups must be reused')
    monkeypatch.setattr(connector, 'verify_group', unexpected)
    second = connector.run(base, output)
    assert second == first
    assert (output/'current').readlink() == target
    assert connector.file_hash(output/'conditions.sqlite') == first['database']['sha256']
    assert not list((output/'generations').glob('.build-*'))


@pytest.mark.parametrize('tamper,reason', [
    ('quote', 'mapping_source_quote_bindings_mismatch'),
    ('route', 'requirement_route_or_projection_mismatch'),
    ('offset', 'mapping_source_offsets_mismatch'),
    ('typed_value', 'mapping_not_in_actual_model_result'),
    ('catalog', 'mapping_catalog_mismatch'),
])
def test_tampered_mapping_never_counts_as_complete(tmp_path, tamper, reason):
    base, output, path = setup_fixture(tmp_path)
    def change(record):
        if tamper == 'quote':
            record['bindings'][0]['exact_source_quote'] = 'invented quotation'
        elif tamper == 'route':
            record['input_requirement']['recipient'] = 'referee'
        elif tamper == 'offset':
            record['source_spans']['char_start'] += 1
        elif tamper == 'typed_value':
            record['mapping']['typed_value'] = True
        else:
            record['catalog_sha256'] = '0'*64
    alter_mapping(path, change)
    summary = connector.run(base, output)
    assert summary['counts']['verified_relevant_mapping_instances'] == 5
    assert summary['integrity']['reason_counts']['mapping:'+reason] == 1


def test_missing_or_stale_personalization_is_fail_closed_and_preserves_output(tmp_path):
    base, output, path = setup_fixture(tmp_path)
    summary = connector.run(base, output)
    with sqlite3.connect(path) as cx:
        cx.execute('DELETE FROM mappings WHERE mapping_id=(SELECT mapping_id FROM mappings LIMIT 1)')
    with pytest.raises(connector.DependencyError, match='incomplete_personalization_mapping_snapshot'):
        connector.run(base, output)
    assert json.loads((output/'summary.json').read_text()) == summary
    path.unlink()
    with pytest.raises(connector.DependencyError, match='missing_dependency'):
        connector.run(base, output)


def test_changed_source_hash_invalidates_previously_verified_links(tmp_path):
    base, output, _ = setup_fixture(tmp_path)
    connector.run(base, output)
    (base/'doc-1.txt').write_text('Changed preserved text invalidates its old offsets.')
    summary = connector.run(base, output)
    assert summary['counts']['actual_llm_analyzed_distinct_notice_groups'] == 4
    assert summary['counts']['verified_relevant_mapping_instances'] == 3
    assert summary['counts']['verified_linked_unique_candidate_notices'] == 1
    assert summary['integrity']['reason_counts']['mapping:source_text_hash_mismatch'] == 3


def test_tampered_receipt_invalidates_cache_and_actual_model_count(tmp_path):
    base, output, _ = setup_fixture(tmp_path)
    connector.run(base, output)
    (base/'completion.json').write_text('{}\n')
    summary = connector.run(base, output)
    assert summary['counts']['actual_llm_analyzed_distinct_notice_groups'] == 0
    assert summary['counts']['verified_relevant_mapping_instances'] == 0
    assert summary['integrity']['unresolved_groups'] == 4


def test_live_queue_lag_is_explicit_and_does_not_multiply_notice_counts(tmp_path):
    base, output, path = setup_fixture(tmp_path)
    p = path.parent/'summary.json'
    upstream = json.loads(p.read_text())
    upstream['reference_semantic_notice_groups'] = 3
    save(p, upstream)
    summary = connector.run(base, output)
    assert summary['freshness']['upstream_projection_may_lag_live_queue']
    assert summary['freshness']['analyzed_groups_since_personalization_snapshot'] == 1
    assert summary['counts']['verified_linked_unique_candidate_notices'] == 2


def test_output_or_upstream_locks_refuse_concurrent_refresh(tmp_path):
    base, output, path = setup_fixture(tmp_path)
    for lock in (output/'.refresh.lock', base/'compact/handoff/.refresh.lock', path.parent/'campaign.lock'):
        with connector.exclusive_lock(lock):
            with pytest.raises(BlockingIOError):
                connector.run(base, output)
    with connector.exclusive_lock(base/'compact/handoff/.refresh.lock') as fd:
        assert connector.run(base, output, artifact_lock_fd=fd)['counts']['verified_relevant_mapping_instances'] == 6


def test_connector_opens_sources_readonly(tmp_path):
    base, output, path = setup_fixture(tmp_path)
    original = {p: connector.file_hash(p) for p in (base/'compact/queue.sqlite', path)}
    connector.run(base, output)
    assert {p: connector.file_hash(p) for p in original} == original
    with connector.readonly(path) as cx:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            cx.execute('DELETE FROM mappings')


def test_parent_requirement_change_creates_fresh_generation_without_inventing_values(tmp_path):
    base, output, path = setup_fixture(tmp_path)
    first = connector.run(base, output)
    with sqlite3.connect(path) as cx:
        candidate, payload = cx.execute('SELECT candidate_key,payload FROM requirements LIMIT 1').fetchone()
        parent = json.loads(payload)
        parent['upstream_scope_note'] = 'Corrected source projection metadata.'
        cx.execute('UPDATE requirements SET payload=? WHERE candidate_key=?', (connector.encoded(parent), candidate))
    second = connector.run(base, output)
    assert first['generation'] != second['generation']
    assert first['counts'] == second['counts']
    assert second['counts']['content_approved_canonical_rules'] == 0


def test_analyzed_label_without_valid_receipt_and_lexical_candidate_are_not_completion(tmp_path):
    base, output, _ = setup_fixture(tmp_path)
    with sqlite3.connect(base/'compact/queue.sqlite') as cx:
        cx.execute("UPDATE groups SET receipt_path=NULL WHERE group_id=(SELECT group_id FROM groups WHERE json_array_length(json_extract(result_json,'$.mappings'))>0 LIMIT 1)")
        cx.execute("INSERT INTO groups VALUES('lexical-only','notice','lexical','{}',1,'pending',NULL,NULL)")
    summary = connector.run(base, output)
    assert summary['counts']['actual_llm_analyzed_distinct_notice_groups'] == 3
    assert summary['counts']['verified_relevant_mapping_instances'] == 4
    assert summary['counts']['lexical_only_candidates_counted_as_semantic_complete'] == 0
