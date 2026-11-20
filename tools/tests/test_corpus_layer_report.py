import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from report_benefitlens_corpus_layers import collect, source_concentration, task_stage_states


class CorpusLayerReportTests(unittest.TestCase):
    def test_missing_evidence_is_unknown_and_does_not_create_source_database(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = collect(root)
            self.assertTrue(all(m['value'] is None for m in result['metrics']))
            self.assertFalse(result['queue']['available'])
            self.assertEqual(list(root.iterdir()), [])

    def test_unit_and_timestamp_boundaries_survive_large_writing_corpus(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def put(rel, value):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value))
            put('experience-scale-v1/coverage.json', {
                'at': '2026-11-19T12:00:00Z', 'experience_records_active': 30000,
                'counted_experience_episodes': 29000,
                'evidence_role_counts_overlap': {'participation': 28000, 'application': 4000}})
            put('writing-foundations-v1/audit.json', {
                'at_kst': '2026-11-20T10:00:00+09:00',
                'source_counts': {'revision_pairs': 200000}})
            result = collect(root)
            metrics = {m['metric']: m for m in result['metrics']}
            self.assertEqual(metrics['counted_experience_episodes']['value'], 29000)
            self.assertEqual(metrics['counted_experience_episodes']['unit'], 'counted_experience_episodes')
            self.assertEqual(result['foundations']['source_counts']['revision_pairs'], 200000)
            self.assertEqual(result['evidence']['coverage']['source_as_of'], '2026-11-19T12:00:00Z')
            self.assertFalse(result['aggregation_policy']['layers_are_one_to_one'])

    def test_queue_is_read_only_and_states_are_not_merged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'team-production-20261119/compact/queue.sqlite'
            path.parent.mkdir(parents=True)
            with sqlite3.connect(path) as cx:
                cx.execute('CREATE TABLE groups (kind TEXT,state TEXT)')
                cx.executemany('INSERT INTO groups VALUES (?,?)',
                               [('experience', 'analyzed'), ('experience', 'pending'), ('notice', 'analyzed')])
            before = path.read_bytes()
            result = collect(root)
            self.assertTrue(result['queue']['available'])
            self.assertEqual(len(result['queue']['rows']), 3)
            self.assertEqual(path.read_bytes(), before)

    def test_concentration_uses_source_case_rows_and_retains_tail_count(self):
        counts = {'main': 80, **{f'tail-{i}': 1 for i in range(20)}}
        result = source_concentration(counts)
        self.assertEqual(result['largest_share'], .8)
        self.assertEqual(result['source_case_rows'], 100)
        self.assertEqual(result['remaining_rows'], 6)

    def test_reference_material_and_indexing_are_not_renamed_as_failures(self):
        result = task_stage_states({
            'a': {'detail': {'reference_material': 10, 'accepted': 3},
                  'index': {'indexed': 2, 'policy_held': 4}},
            'b': {'detail': {'reference_material': 1, 'needs_review': 8}},
        })
        self.assertEqual(result['by_stage_and_state']['detail']['reference_material'], 11)
        self.assertEqual(result['by_stage_and_state']['index']['policy_held'], 4)
        self.assertNotIn('failed', result['by_stage_and_state']['detail'])
        self.assertTrue(result['state_labels_are_not_root_cause_diagnoses'])


if __name__ == '__main__':
    unittest.main()
