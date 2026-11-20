"""Connect actual compact observations to preserved paragraphs and generic review.

Reads live input databases through read-only snapshots. The derived links and
immutable, source/context-versioned full-paragraph tasks are separate from all
upstream queues. This command never calls a model or approves content.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from functools import lru_cache
import json
from pathlib import Path
import sqlite3
import unicodedata

from benefitlens_source_storage import read_source_bytes
from jsonschema.exceptions import ValidationError
from run_benefitlens_codex_corpus import encoded, digest, inspect_events, ref, save, utc

BASE = Path('/home/longnew/.cache/datasets/benefitlens/scale50k-20261109/team-production-20261119')
REPO = Path(__file__).resolve().parents[1]
VERSION = 'source-paragraph-connections-v1'


def readonly(path):
    db = sqlite3.connect('file:' + str(Path(path).resolve()) + '?mode=ro', uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    return db


def normalized(text):
    return ' '.join(unicodedata.normalize('NFC', text).split())


def checked_source(document):
    reference = document['text_ref']
    try:
        raw = read_source_bytes(reference['path'], storage=reference.get('storage'))
    except ValueError:
        raw = read_source_bytes(reference['path'])
    if digest(raw) != reference['sha256'] or reference['sha256'] != document['text_sha256']:
        raise ValueError('Source document checksum mismatch')
    return raw.decode('utf8')


def observation_execution(row, result):
    from build_benefitlens_compact_handoff import verify_group
    _, execution = verify_group(dict(row), RECEIPT_CACHE)
    return dict(execution, receipt_ref=ref(row['receipt_path']), model_called=True)


RECEIPT_CACHE = {}


def occurrence_verified(occurrence, representative, text):
    a, b, ca, cb = (occurrence[k] for k in ('char_start', 'char_end', 'context_start', 'context_end'))
    return (digest(text) == occurrence['text_sha256'] and 0 <= ca <= a < b <= cb <= len(text)
            and normalized(text[a:b]) == normalized(representative['text'])
            and normalized(text[ca:cb]) == normalized(representative['context_text']))


def fragment(paragraph, text):
    if paragraph is None:
        return None
    a, b = paragraph['paragraph_char_start'], paragraph['paragraph_char_end']
    if not 0 <= a < b <= len(text) or paragraph['text_sha256'] != digest(text):
        raise ValueError('Paragraph source binding failed')
    return {'paragraph_id': paragraph['paragraph_id'], 'char_start': a, 'char_end': b,
            'text': text[a:b], 'span_sha256': digest(text[a:b]),
            'boundary_basis': paragraph['paragraph_structure_kind'],
            'semantic_boundary_approval': False}


def make_task(paragraph, paragraphs, document, text, framework, genre, role):
    from benefitlens_paragraph_patterns import SYSTEM, result_schema, VERSION as analysis_version
    sources = {'focal': fragment(paragraph, text),
               'previous': fragment(paragraphs.get(paragraph.get('previous_paragraph_id')), text),
               'next': fragment(paragraphs.get(paragraph.get('next_paragraph_id')), text)}
    context_hash = digest(encoded(sources))
    payload = {'version': analysis_version, 'document_id': document['document_id'],
               'text_sha256': document['text_sha256'], 'source_url': document.get('source_url'),
               'text_ref': document['text_ref'], 'genre': genre, 'source_role': role,
               'sources': sources, 'context_sha256': context_hash,
               'scope': 'complete_source_paragraph_candidate_and_immediate_stored_neighbors',
               'framework_sha256': digest(encoded(framework)),
               'prompt_sha256': digest(SYSTEM), 'schema_sha256': digest(encoded(result_schema()))}
    task_id = 'paragraph-task-' + digest(encoded(payload))[:40]
    return task_id, payload


def connect_output(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''CREATE TABLE IF NOT EXISTS observations(
        group_id TEXT PRIMARY KEY,relevance TEXT,source_result_sha256 TEXT,execution_json TEXT,result_json TEXT);
        CREATE TABLE IF NOT EXISTS links(link_id TEXT PRIMARY KEY,group_id TEXT,occurrence_id TEXT,
        document_id TEXT,paragraph_id TEXT,case_key TEXT,state TEXT,payload TEXT);
        CREATE INDEX IF NOT EXISTS writing_links_paragraph ON links(paragraph_id);
        CREATE TABLE IF NOT EXISTS criterion_links(link_id TEXT,criterion_id TEXT,basis_json TEXT,
        PRIMARY KEY(link_id,criterion_id));
        CREATE TABLE IF NOT EXISTS pattern_connections(pattern_connection_id TEXT PRIMARY KEY,
        task_id TEXT,group_id TEXT,occurrence_id TEXT,paragraph_id TEXT,case_key TEXT,result_sha256 TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS semantic_criterion_links(task_id TEXT,criterion_id TEXT,
        assessment TEXT,basis TEXT,payload TEXT,PRIMARY KEY(task_id,criterion_id));
        CREATE TABLE IF NOT EXISTS tasks(task_id TEXT PRIMARY KEY,paragraph_id TEXT,document_id TEXT,
        context_sha256 TEXT,input_sha256 TEXT,active INTEGER,priority INTEGER,payload TEXT,created_at TEXT);
        CREATE INDEX IF NOT EXISTS writing_active_tasks ON tasks(active,priority,task_id);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value_json TEXT);''')
    return db


def materialize_patterns(output, out, criteria):
    """Replay actual completions and source checks before publishing semantic edges."""
    from benefitlens_paragraph_patterns import validate_group, verify_task
    counts = Counter({key: 0 for key in ('source_verified_full_paragraph_analyses_materialized',
                      'actual_semantic_criterion_assessment_links', 'actual_pattern_source_occurrence_case_links',
                      'actual_pattern_linked_compact_observations', 'held_full_paragraph_completion_revalidation')})
    out.execute('DELETE FROM pattern_connections')
    out.execute('DELETE FROM semantic_criterion_links')
    path = output/'patterns.sqlite3'
    if not path.exists():
        out.commit()
        return counts
    patterns = readonly(path)
    receipts = {}
    for row in patterns.execute("SELECT * FROM results WHERE state='analyzed'"):
        task_row = out.execute('SELECT * FROM tasks WHERE task_id=? AND active=1', (row['task_id'],)).fetchone()
        if not task_row:
            continue
        try:
            task = json.loads(task_row['payload'])
            if row['input_sha256'] != task_row['input_sha256'] or digest(encoded(task)) != row['input_sha256']:
                raise ValueError('Full paragraph result input identity mismatch')
            verify_task(task)
            if row['receipt_path'] not in receipts:
                receipt = json.loads(Path(row['receipt_path']).read_bytes())
                for name in ('request_ref', 'prompt_ref', 'domain_schema_ref', 'actual_batch_schema_ref',
                             'events_ref', 'final_response_ref', 'execution_ref'):
                    reference = receipt[name]
                    if digest(Path(reference['path']).read_bytes()) != reference['sha256']:
                        raise ValueError('Full paragraph execution artifact checksum mismatch: ' + name)
                events = inspect_events(Path(receipt['events_ref']['path']))
                if not events['model_called'] or events['waiting_reason'] or events['parse_error_lines']:
                    raise ValueError('Full paragraph execution has no verified completed model turn')
                execution = json.loads(Path(receipt['execution_ref']['path']).read_bytes())
                if execution.get('exit_code') != 0:
                    raise ValueError('Full paragraph CLI did not complete successfully')
                final = json.loads(Path(receipt['final_response_ref']['path']).read_bytes())
                if digest(encoded(final)) not in {m['parsed_json_sha256'] for m in events['agent_message_events']}:
                    raise ValueError('Full paragraph final response differs from actual events')
                request = json.loads(Path(receipt['request_ref']['path']).read_bytes())
                submitted = json.loads(request['request']['user'])
                submitted_id, submitted_sha = submitted.pop('batch_id'), submitted.pop('input_sha256')
                if (digest(encoded(submitted)) != request['input_sha256'] or submitted_sha != request['input_sha256']
                        or submitted_id != request['batch_id'] or final['input_sha256'] != submitted_sha
                        or final['batch_id'] != submitted_id or receipt['input_sha256'] != submitted_sha):
                    raise ValueError('Full paragraph actual batch input binding failed')
                prompt = Path(receipt['prompt_ref']['path']).read_text()
                if request['request']['user'] not in prompt or request['request']['system'] not in prompt:
                    raise ValueError('Full paragraph submitted prompt binding failed')
                receipts[row['receipt_path']] = receipt, final, request
            receipt, final, request = receipts[row['receipt_path']]
            sources = [g for g in request['groups'] if g['group_id'] == row['task_id']]
            returned = [g for g in final['groups'] if g['group_id'] == row['task_id']]
            if len(sources) != 1 or len(returned) != 1 or encoded({k: v for k, v in sources[0].items() if k != 'group_id'}) != encoded(task):
                raise ValueError('Full paragraph submitted task is absent, duplicated or changed')
            result = validate_group(returned[0], row['task_id'], task)
            result_sha = digest(encoded(result))
            record = [g for g in receipt['group_results'] if g['group_id'] == row['task_id']]
            if (encoded(result) != row['result_json'] or len(record) != 1 or record[0]['result_sha256'] != result_sha
                    or record[0]['source_input_sha256'] != row['input_sha256']):
                raise ValueError('Full paragraph persisted semantic fields differ from actual completion')
            counts['source_verified_full_paragraph_analyses_materialized'] += 1
            for criterion in result['criterion_assessments']:
                criterion_id = 'content-chain:' + criterion['criterion_id']
                if criterion_id not in criteria:
                    continue
                payload = {'assessment': criterion, 'criterion_ref': criterion_id,
                           'criterion_source_reviews': criteria[criterion_id].get('source_section_reviews', []),
                           'receipt_ref': ref(row['receipt_path']), 'result_sha256': result_sha,
                           'generic_only': True, 'content_approved': False, 'semantic_review': result['semantic_review']}
                out.execute('INSERT INTO semantic_criterion_links VALUES(?,?,?,?,?)',
                            (row['task_id'], criterion_id, criterion['assessment'], criterion['basis'], encoded(payload)))
                counts['actual_semantic_criterion_assessment_links'] += 1
            for link in out.execute("SELECT * FROM links WHERE state='paragraph_linked' AND json_extract(payload,'$.full_paragraph_task_id')=?", (row['task_id'],)).fetchall():
                linked = json.loads(link['payload'])
                connection_id = 'semantic-pattern-link-' + digest(encoded([row['task_id'], link['link_id']]))[:36]
                payload = {'analysis': result, 'source_context_sha256': task['context_sha256'],
                           'source_occurrence': linked['source_occurrence'], 'case_binding': linked['case_binding'],
                           'receipt_ref': ref(row['receipt_path']), 'result_sha256': result_sha,
                           'program_round_binding': 'requires_reviewed_current_task_mapping',
                           'content_approved': False, 'source_person_facts_reusable_as_applicant_facts': False}
                out.execute('INSERT INTO pattern_connections VALUES(?,?,?,?,?,?,?,?)',
                            (connection_id, row['task_id'], link['group_id'], link['occurrence_id'], link['paragraph_id'], link['case_key'], result_sha, encoded(payload)))
                counts['actual_pattern_source_occurrence_case_links'] += 1
        except (OSError, ValueError, KeyError, TypeError, ValidationError):
            counts['held_full_paragraph_completion_revalidation'] += 1
    patterns.close()
    out.commit()
    counts['actual_pattern_linked_compact_observations'] = out.execute('SELECT count(DISTINCT group_id) FROM pattern_connections').fetchone()[0]
    return counts


def build(base=BASE, output=None):
    base = Path(base)
    output = Path(output or base/'team-expansion-20261120/semantic-connections/writing')
    output.mkdir(parents=True, exist_ok=True)
    paths = {'compact': base/'compact/queue.sqlite',
             'quality': base/'team-expansion-20261120/experience-personalization/quality/quality.sqlite',
             'registry': base/'team-expansion-20261120/writing-validation/registry.sqlite3',
             'framework': REPO/'pipeline/config/writing_evidence_framework_v2.json'}
    framework = json.loads(paths['framework'].read_bytes())
    queue, quality, registry = (readonly(paths[k]) for k in ('compact', 'quality', 'registry'))
    out = connect_output(output/'links.sqlite3')
    docs = {r['document_id']: dict(json.loads(r['payload']), text_sha256=r['text_sha256']) for r in quality.execute('SELECT * FROM documents')}
    paragraphs = {r['paragraph_id']: json.loads(r['payload']) for r in quality.execute('SELECT * FROM paragraphs')}
    by_doc = defaultdict(list)
    for paragraph in paragraphs.values():
        by_doc[paragraph['document_id']].append(paragraph)
    cases = {r['case_key']: json.loads(r['spec_json']) for r in registry.execute('SELECT case_key,spec_json FROM case_boundary')}
    criteria = {r['registry_id']: json.loads(r['spec_json']) for r in registry.execute("SELECT registry_id,spec_json FROM registry WHERE record_type='content_review_stage'")}
    counters = Counter()
    source_states = {r['state']: r['n'] for r in queue.execute("SELECT state,count(*) n FROM groups WHERE kind='experience' GROUP BY state")}
    errors = []

    @lru_cache(maxsize=256)
    def source(did):
        return checked_source(docs[did])

    out.execute('BEGIN IMMEDIATE')
    out.execute('DELETE FROM observations')
    out.execute('DELETE FROM criterion_links')
    out.execute('DELETE FROM links')
    out.execute('UPDATE tasks SET active=0')
    try:
        for row in queue.execute("SELECT * FROM groups WHERE kind='experience' AND state='analyzed' AND result_json IS NOT NULL ORDER BY group_id"):
            result, representative = json.loads(row['result_json']), json.loads(row['representative_json'])
            counters['analyzed_compact_observations_seen'] += 1
            counters['relevance:' + result['relevance']] += 1
            if result['relevance'] != 'relevant':
                continue
            try:
                execution = observation_execution(row, result)
            except (OSError, ValueError, KeyError) as error:
                counters['held_observation_execution_binding'] += 1
                if len(errors) < 20:
                    errors.append({'group_id': row['group_id'], 'reason': str(error)})
                continue
            counters['verified_relevant_compact_observations'] += 1
            out.execute('INSERT INTO observations VALUES(?,?,?,?,?)',
                        (row['group_id'], result['relevance'], digest(encoded(result)), encoded(execution), encoded(result)))
            occurrences = queue.execute('SELECT * FROM occurrences WHERE group_id=? ORDER BY occurrence_id', (row['group_id'],)).fetchall()
            for occurrence in occurrences:
                occ = dict(occurrence)
                counters['relevant_source_occurrences_seen'] += 1
                case = cases.get(occ['record_key'])
                document = docs.get(occ['document_id'])
                paragraph, task_id = None, None
                issue = None
                try:
                    if not document:
                        raise ValueError('Source document is absent from current quality structure snapshot')
                    text = source(occ['document_id'])
                    if not occurrence_verified(occ, representative, text):
                        raise ValueError('Exact source occurrence and compact context differ')
                    counters['source_bound_occurrence_links'] += 1
                    matches = [p for p in by_doc[occ['document_id']] if
                               p['paragraph_char_start'] <= occ['char_start'] < occ['char_end'] <= p['paragraph_char_end']]
                    if len(matches) == 1:
                        paragraph = matches[0]
                        # Excluded structural containers never enter the semantic queue.
                        noise = quality.execute('SELECT 1 FROM zones WHERE document_id=? AND auto_excludable=1 AND char_start<=? AND char_end>=? LIMIT 1',
                                                (occ['document_id'], paragraph['paragraph_char_start'], paragraph['paragraph_char_end'])).fetchone()
                        if noise:
                            issue = 'held_structural_noise_paragraph'
                        else:
                            task_id, payload = make_task(paragraph, paragraphs, document, text, framework, occ['genre'], occ['source_role'])
                            priority = 0 if occ['genre'] == 'application' else 1 if 'writing_advice' in result.get('event_roles', []) else 2
                            out.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET active=1,priority=min(priority,excluded.priority)',
                                        (task_id, paragraph['paragraph_id'], occ['document_id'], payload['context_sha256'], digest(encoded(payload)), 1, priority, encoded(payload), utc()))
                            counters['paragraph_bound_occurrence_links'] += 1
                    else:
                        issue = 'held_no_unique_containing_source_paragraph'
                except (OSError, ValueError, KeyError) as error:
                    issue = 'held_source_binding:' + str(error)
                state = 'paragraph_linked' if task_id else issue
                counters['link_state:' + state] += 1
                link_id = 'writing-link-' + digest(encoded([row['group_id'], occ['occurrence_id']]))[:36]
                linked = {'observation_scope': 'compact_visible_passage_only', 'execution': execution,
                          'source_occurrence': occ, 'paragraph_id': paragraph['paragraph_id'] if paragraph else None,
                          'full_paragraph_task_id': task_id, 'case_key': occ['record_key'],
                          'case_binding': case, 'case_binding_resolved': case is not None,
                          'source_person_facts_reusable_as_applicant_facts': False,
                          'program_round_binding': 'requires_reviewed_current_task_mapping',
                          'content_approved': False, 'state': state}
                out.execute('INSERT INTO links VALUES(?,?,?,?,?,?,?,?)',
                            (link_id, row['group_id'], occ['occurrence_id'], occ['document_id'], linked['paragraph_id'], occ['record_key'], state, encoded(linked)))
                if case and state == 'paragraph_linked':
                    counters['paragraph_case_linked_occurrences'] += 1
                # These are review inputs derived from semantic schema fields.
                # They are neither lexical word matches nor criterion verdicts.
                cer = result.get('claim_evidence_reasoning') or {}
                fields = {'claim': cer.get('claim'), 'grounds': cer.get('evidence'), 'warrant': cer.get('reasoning'),
                          'paragraph': result.get('opening_or_closing_function'), 'document': result.get('local_connection')}
                if state != 'paragraph_linked':
                    continue
                for stage, value in fields.items():
                    criterion = 'content-chain:' + stage
                    if criterion not in criteria or value is None:
                        continue
                    if isinstance(value, dict) and not any(v is not None and v != 'not_observed' for v in value.values()):
                        continue
                    basis = {'state': 'generic_review_input_not_criterion_verdict', 'observed_compact_field': value,
                             'full_paragraph_task_id': task_id, 'criterion_ref': criterion,
                             'criterion_source_reviews': criteria[criterion].get('source_section_reviews', []),
                             'framework_sha256': digest(encoded(framework)), 'program_specific': False}
                    out.execute('INSERT INTO criterion_links VALUES(?,?,?)', (link_id, criterion, encoded(basis)))
                    counters['generic_criterion_input_links'] += 1
        out.commit()
    except BaseException:
        out.rollback()
        raise
    active_tasks = out.execute('SELECT count(*) FROM tasks WHERE active=1').fetchone()[0]
    counters['paragraph_linked_observations'] = out.execute("SELECT count(DISTINCT group_id) FROM links WHERE state='paragraph_linked'").fetchone()[0]
    counters['paragraph_case_linked_observations'] = out.execute("SELECT count(DISTINCT group_id) FROM links WHERE state='paragraph_linked' AND json_extract(payload,'$.case_binding_resolved')=1").fetchone()[0]
    counters['generic_criterion_linked_observations'] = out.execute('SELECT count(DISTINCT l.group_id) FROM links l JOIN criterion_links c ON c.link_id=l.link_id').fetchone()[0]
    counters['source_bound_observations'] = out.execute("SELECT count(DISTINCT group_id) FROM links WHERE state='paragraph_linked' OR state='held_no_unique_containing_source_paragraph' OR state='held_structural_noise_paragraph'").fetchone()[0]
    counters.update(materialize_patterns(output, out, criteria))
    manifest = {'version': VERSION, 'generated_at': utc(), 'inputs': {k: str(v.resolve()) for k, v in paths.items()},
                'framework_ref': ref(paths['framework']), 'builder_ref': ref(__file__),
                'analysis_module_ref': ref(REPO/'tools/benefitlens_paragraph_patterns.py'),
                'compact_experience_state_snapshot': source_states,
                'compact_snapshot_identity': digest(encoded([(r['group_id'], r['source_result_sha256']) for r in out.execute('SELECT group_id,source_result_sha256 FROM observations ORDER BY group_id')])),
                'quality_paragraph_snapshot_identity': digest(encoded(sorted(paragraphs.values(), key=lambda p: p['paragraph_id']))),
                'criterion_snapshot_identity': digest(encoded(criteria)),
                'task_manifest_sha256': digest(encoded([list(r) for r in out.execute('SELECT task_id,input_sha256 FROM tasks WHERE active=1 ORDER BY task_id')]))}
    save(output/'input-manifest.json', manifest)
    summary = {'version': VERSION, 'generated_at': utc(), 'output_root': str(output.resolve()),
               'source_state_snapshot': source_states, 'counts': dict(counters), 'active_full_paragraph_tasks': active_tasks,
               'total_versioned_tasks': out.execute('SELECT count(*) FROM tasks').fetchone()[0],
               'source_paragraph_locators': len(paragraphs), 'model_calls_this_link_run': 0,
               'program_round_bound_patterns': 0, 'program_specific_approved_patterns': 0, 'content_approved_patterns': 0,
               'current_task_binding': 'unavailable_generic_review_only',
               'compact_observation_is_full_paragraph_pattern': False,
               'generic_criterion_input_links_are_approvals': False,
               'incompleteness': 'Only relevant, receipt-verified completed compact observations with unique source paragraph bindings enter paragraph analysis. Remaining corpus, uncertain relevance, missing paragraph boundaries and unreviewed task bindings remain held or pending.',
               'held_examples': errors, 'manifest_ref': ref(output/'input-manifest.json')}
    from benefitlens_paragraph_patterns import status
    summary['paragraph_analysis'] = status(output, write=False)
    save(output/'summary.json', summary)
    out.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('summary', encoded(summary)))
    out.commit()
    out.close()
    for db in (queue, quality, registry):
        db.close()
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=BASE)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or args.base/'team-expansion-20261120/semantic-connections/writing'
    output.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (output/'.link-writer.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(encoded(build(args.base, output)))


if __name__ == '__main__':
    main()
