import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benefitlens_paragraph_patterns as patterns
import link_benefitlens_writing_patterns as links
import run_benefitlens_codex_corpus as cli


class ParagraphPatternsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.text = 'I built a bridge. It served ten people.\n\nThis taught me to listen.'
        source = self.root/'source.txt'
        source.write_text(self.text)
        self.doc = {'document_id': 'doc1', 'text_sha256': cli.digest(self.text), 'source_url': 'https://example.test/story',
                    'text_ref': {'path': str(source), 'sha256': cli.digest(self.text)}}
        first_end = self.text.index('\n\n')
        self.paragraphs = {
            'p1': {'paragraph_id': 'p1', 'document_id': 'doc1', 'text_sha256': self.doc['text_sha256'],
                   'paragraph_char_start': 0, 'paragraph_char_end': first_end,
                   'paragraph_structure_kind': 'blank_line_block_candidate', 'previous_paragraph_id': None, 'next_paragraph_id': 'p2'},
            'p2': {'paragraph_id': 'p2', 'document_id': 'doc1', 'text_sha256': self.doc['text_sha256'],
                   'paragraph_char_start': first_end+2, 'paragraph_char_end': len(self.text),
                   'paragraph_structure_kind': 'blank_line_block_candidate', 'previous_paragraph_id': 'p1', 'next_paragraph_id': None}}
        self.task_id, self.task = links.make_task(self.paragraphs['p1'], self.paragraphs, self.doc, self.text,
                                                 {'version': 'test-framework'}, 'application', 'public_application_document')

    def tearDown(self):
        self.tmp.cleanup()

    def span(self):
        return {'source': 'focal', 'quote': self.text[:17]}

    def result(self, task_id=None):
        row = {'group_id': task_id or self.task_id, 'relevance': 'relevant'}
        row.update({key: {'basis': 'not_observed', 'interpretation': None, 'spans': []} for key in patterns.FACETS})
        row['paragraph_purpose'] = {'basis': 'inferred', 'interpretation': 'Action followed by reported result', 'spans': [self.span()]}
        row['sentence_roles'] = [{'role': 'action', 'basis': 'observed', 'interpretation': 'Reports building a bridge', 'span': self.span()}]
        row['criterion_assessments'] = [{'criterion_id': 'grounds', 'assessment': 'supported_with_scope',
                                         'basis': 'inferred', 'interpretation': 'Author reports an action', 'spans': [self.span()]}]
        row['limits'] = ['Self-report only']
        return row

    def populate(self, tasks):
        db = links.connect_output(self.root/'links.sqlite3')
        for ident, task in tasks:
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',
                       (ident, 'p1', 'doc1', task['context_sha256'], cli.digest(cli.encoded(task)), 1, 0, cli.encoded(task), cli.utc()))
        db.commit()
        db.close()

    def fake_cli(self, folder, executable, timeout):
        batch = cli.read(folder/'request.json')
        output = {'version': patterns.VERSION, 'batch_id': batch['batch_id'], 'input_sha256': batch['input_sha256'],
                  'groups': [self.result(task['group_id']) for task in batch['groups']]}
        if len(output['groups']) > 1:
            output['groups'][1]['sentence_roles'][0]['span']['quote'] = 'Fabricated quote'
        cli.save(folder/'final-response.json', output)
        events = [{'type': 'thread.started', 'thread_id': 'fake-thread'},
                  {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': cli.encoded(output)}},
                  {'type': 'turn.completed', 'usage': {'input_tokens': 100, 'output_tokens': 100}}]
        (folder/'events.jsonl').write_text('\n'.join(cli.encoded(e) for e in events)+'\n')
        (folder/'stderr.txt').write_text('')
        execution = {'cli_invoked': True, 'exit_code': 0}
        cli.save(folder/'execution.json', execution)
        return execution

    def test_source_context_versions_and_quote_validation(self):
        self.assertTrue(patterns.verify_task(self.task))
        row = patterns.validate_group(self.result(), self.task_id, self.task)
        self.assertFalse(row['approved_pattern'])
        self.assertEqual(row['paragraph_purpose']['spans'][0]['char_start'], 0)
        self.assertEqual(row['paragraph_purpose']['spans'][0]['char_end'], 17)
        changed = dict(self.task, source_role='different_scope')
        self.assertNotEqual(cli.digest(cli.encoded(changed)), cli.digest(cli.encoded(self.task)))
        row = self.result()
        row['paragraph_purpose']['spans'][0]['quote'] = 'An invented sentence'
        with self.assertRaisesRegex(ValueError, 'exact submitted'):
            patterns.validate_group(row, self.task_id, self.task)

    def test_adjacent_relation_needs_both_sources_and_missing_neighbor_held(self):
        row = self.result()
        row['relation_to_next'] = {'basis': 'inferred', 'interpretation': 'Reflection develops the action', 'spans': [self.span()]}
        with self.assertRaisesRegex(ValueError, 'both paragraphs'):
            patterns.validate_group(row, self.task_id, self.task)
        row = self.result()
        row['relation_to_previous'] = {'basis': 'uncertain', 'interpretation': None, 'spans': []}
        with self.assertRaisesRegex(ValueError, 'Missing neighbor'):
            patterns.validate_group(row, self.task_id, self.task)

    def test_duplicate_quote_is_held_without_fuzzy_resolution(self):
        task = json.loads(cli.encoded(self.task))
        task['sources']['focal']['text'] += ' ' + self.text[:17]
        with self.assertRaisesRegex(ValueError, 'unique exact'):
            patterns.validate_group(self.result(), self.task_id, task)

    def test_provider_limit_stops_and_requires_explicit_clear(self):
        self.populate([(self.task_id, self.task)])
        calls = []
        def limited(folder, executable, timeout):
            calls.append(folder)
            (folder/'events.jsonl').write_text(cli.encoded({'type': 'turn.failed', 'error': {'message': 'rate_limit 429'}})+'\n')
            (folder/'stderr.txt').write_text('rate limit')
            cli.save(folder/'execution.json', {'cli_invoked': True, 'exit_code': 1})
        result = patterns.run(self.root, cli_runner=limited)
        self.assertEqual(result['task_states']['waiting_provider'], 1)
        self.assertEqual(result['provider_waiting']['reason'], 'provider_limit')
        patterns.run(self.root, cli_runner=limited)
        self.assertEqual(len(calls), 1)
        result = patterns.run(self.root, cli_runner=self.fake_cli, clear_provider_wait=True)
        self.assertEqual(result['successful_full_paragraph_analyses'], 1)
        self.assertIsNone(result['provider_waiting'])

    def test_partial_batch_keeps_valid_sibling_and_reuses_completion(self):
        changed = dict(self.task, source_role='advice')
        second_id = 'second-task'
        self.populate([(self.task_id, self.task), (second_id, changed)])
        calls = []
        def runner(*args):
            calls.append(args)
            return self.fake_cli(*args)
        result = patterns.run(self.root, batch_size=20, max_batches=1, cli_runner=runner)
        self.assertEqual(result['successful_full_paragraph_analyses'], 1)
        self.assertEqual(result['task_states']['held'], 1)
        self.assertEqual(result['actual_model_calls'], 1)
        receipt = cli.read(next((self.root/'batches').glob('*/receipt.json')))
        self.assertTrue(receipt['model_called'])
        self.assertTrue(receipt['events_ref']['sha256'])
        patterns.run(self.root, batch_size=20, max_batches=1, cli_runner=runner)
        self.assertEqual(len(calls), 1)
        summary = self.root/'pattern-summary.json'
        before = summary.stat().st_mtime_ns
        patterns.status(self.root)
        self.assertEqual(before, summary.stat().st_mtime_ns)

    def test_source_change_and_budget_hold_prevent_calls(self):
        self.populate([(self.task_id, self.task)])
        Path(self.task['text_ref']['path']).write_text('Tampered source')
        result = patterns.run(self.root, max_batches=1, cli_runner=lambda *_: self.fail('Source mismatch must prevent model execution'))
        self.assertEqual(result['task_states']['held_source_binding'], 1)
        self.assertEqual(result['actual_model_calls'], 0)

    def test_oversized_sources_are_held_without_truncation(self):
        self.populate([(self.task_id, self.task)])
        result = patterns.run(self.root, max_input_characters=50, cli_runner=lambda *_: self.fail('Oversized task must not run'))
        self.assertEqual(result['task_states']['held_oversized'], 1)

    def test_linker_reads_real_document_column_contract_and_preserves_versions(self):
        import sqlite3
        base = self.root/'base'
        quality_dir = base/'team-expansion-20261120/experience-personalization/quality'
        registry_dir = base/'team-expansion-20261120/writing-validation'
        (base/'compact').mkdir(parents=True)
        quality_dir.mkdir(parents=True)
        registry_dir.mkdir(parents=True)
        quality = sqlite3.connect(quality_dir/'quality.sqlite')
        quality.executescript('CREATE TABLE documents(document_id TEXT,text_sha256 TEXT,payload TEXT);CREATE TABLE paragraphs(paragraph_id TEXT,payload TEXT);CREATE TABLE zones(document_id TEXT,auto_excludable INTEGER,char_start INTEGER,char_end INTEGER);')
        payload = {k: v for k, v in self.doc.items() if k != 'text_sha256'}
        quality.execute('INSERT INTO documents VALUES(?,?,?)', ('doc1', self.doc['text_sha256'], cli.encoded(payload)))
        for pid, paragraph in self.paragraphs.items():
            quality.execute('INSERT INTO paragraphs VALUES(?,?)', (pid, cli.encoded(paragraph)))
        quality.commit()
        quality.close()
        registry = sqlite3.connect(registry_dir/'registry.sqlite3')
        registry.executescript('CREATE TABLE case_boundary(case_key TEXT,spec_json TEXT);CREATE TABLE registry(registry_id TEXT,spec_json TEXT,record_type TEXT);')
        registry.execute('INSERT INTO case_boundary VALUES(?,?)', ('case1', '{}'))
        registry.execute('INSERT INTO registry VALUES(?,?,?)', ('content-chain:claim', '{}', 'content_review_stage'))
        registry.execute('INSERT INTO registry VALUES(?,?,?)', ('content-chain:grounds', '{}', 'content_review_stage'))
        registry.commit()
        registry.close()
        queue = sqlite3.connect(base/'compact/queue.sqlite')
        queue.executescript('CREATE TABLE groups(group_id TEXT,kind TEXT,state TEXT,result_json TEXT,representative_json TEXT);CREATE TABLE occurrences(occurrence_id TEXT,group_id TEXT,document_id TEXT,text_sha256 TEXT,char_start INTEGER,char_end INTEGER,context_start INTEGER,context_end INTEGER,record_key TEXT,genre TEXT,source_role TEXT);')
        queue.execute('INSERT INTO groups VALUES(?,?,?,?,?)', ('g1', 'experience', 'analyzed', cli.encoded({'relevance': 'relevant', 'claim_evidence_reasoning': {'claim': 'Reported bridge building'}}), cli.encoded({'text': self.text[:17], 'context_text': self.text[:38]})))
        queue.execute('INSERT INTO occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?)', ('o1', 'g1', 'doc1', self.doc['text_sha256'], 0, 17, 0, 38, 'case1', 'application', 'public_application_document'))
        queue.commit()
        queue.close()
        with patch.object(links, 'observation_execution', return_value={'model_called': True}):
            result = links.build(base, self.root/'linked')
        self.assertEqual(result['counts']['source_bound_occurrence_links'], 1)
        self.assertEqual(result['counts']['paragraph_case_linked_observations'], 1)
        self.assertEqual(result['active_full_paragraph_tasks'], 1)
        self.assertEqual(result['paragraph_analysis']['successful_full_paragraph_analyses'], 0)
        with patch.object(links, 'observation_execution', return_value={'model_called': True}):
            result = links.build(base, self.root/'linked')
        self.assertEqual(result['total_versioned_tasks'], 1)
        patterns.run(self.root/'linked', cli_runner=self.fake_cli)
        with patch.object(links, 'observation_execution', return_value={'model_called': True}):
            result = links.build(base, self.root/'linked')
        self.assertEqual(result['counts']['source_verified_full_paragraph_analyses_materialized'], 1)
        self.assertEqual(result['counts']['actual_semantic_criterion_assessment_links'], 1)
        self.assertEqual(result['counts']['actual_pattern_source_occurrence_case_links'], 1)
        self.assertEqual(result['counts']['actual_pattern_linked_compact_observations'], 1)


if __name__ == '__main__':
    unittest.main()
