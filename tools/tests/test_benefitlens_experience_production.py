"""Boundary checks for immutable source spans and shared-catalog consumption."""
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit_benefitlens_experience_production as audit


class ProductionInputTests(unittest.TestCase):
    def test_unicode_offsets_cover_every_non_whitespace_character(self):
        text = '첫 문단의 주장입니다.\n앞뒤 연결이 필요합니다.\n\nSecond paragraph!\n' + '긴문장'*1700
        spans = audit.structural_segments(text, max_characters=100)
        covered = set()
        for span in spans:
            start, end = span['char_start'], span['char_end']
            self.assertLessEqual(end-start, 100)
            self.assertEqual(span['segment_text_sha256'], audit.digest(text[start:end]))
            covered.update(range(start, end))
        self.assertTrue(all(i in covered for i, char in enumerate(text) if not char.isspace()))
        self.assertTrue(all('semantic' not in s['kind'] for s in spans))

    def test_dom_paragraphs_are_aligned_to_original_not_regenerated(self):
        text = 'Header\nFirst paragraph with\nline break.\nA second one.\nFooter'
        spans = audit.structural_segments(text, '<main><p>First paragraph with line break.</p><p>A second one.</p></main>')
        paragraphs = [s for s in spans if s['kind'] == 'source_dom_paragraph_aligned']
        self.assertEqual(len(paragraphs), 2)
        self.assertEqual(text[paragraphs[0]['char_start']:paragraphs[0]['char_end']], 'First paragraph with\nline break.')
        self.assertEqual(paragraphs[0]['line_start'], 2)
        self.assertEqual(paragraphs[0]['line_end'], 3)

    def test_missing_second_document_blocks_complete_source_stage(self):
        row = {'case_key': 'real-1', 'program_id': 'unknown', 'round_id': None,
               'outcome': 'unknown', 'source_url': 'https://example.test/x',
               'person_name_present': False, 'evidence_status': 'public_original_collected',
               'payload': {'document_ids': ['exists', 'missing']}}
        doc = {'document_id': 'exists', 'text_ref': {'sha256': 'x'}, 'segments_ref': {},
               'source_verification': {'state': 'raw_and_text_hash_verified'}}
        result, job = audit.case_projection(row, {'exists': doc}, {'writing_experience_channel': {'optional_event_slots': []}, 'contract_version': 'test'})
        self.assertFalse(result['analysis_stages']['original_integrity_verified'])
        self.assertFalse(result['analysis_stages']['structure_preprocessed'])
        self.assertEqual(job['next_stage'], 'repair_source_inputs')
        self.assertFalse(result['new_semantic_judgment'])

    def test_higherin_answer_div_restores_exact_boundary_without_duplicate_p(self):
        answer = 'I receive a lot of support\nsuch as check ups and asking if I need anything.'
        second = 'The employer helped me develop my communication skills.'
        text = 'Rating\n4/5\n' + answer + '\n' + second
        page = ('<main><p>Rating</p><p>4/5</p>'
                '<div class="p-4 bg-web-white-smoke rounded-lg break-words whitespace-pre-line">'
                '<span>I receive a lot of support such as check ups and asking if I need anything.</span>'
                '</div><div class="bg-web-white-smoke break-words whitespace-pre-line">'
                '<p>' + second + '</p></div></main>')
        spans = audit.structural_segments(text, page, source_url='https://higherin.com/review/55398/bagnalls/apprentice')
        boundaries = [(s['char_start'], s['char_end']) for s in spans
                      if s['kind'] == 'source_dom_paragraph_aligned']
        self.assertEqual([text[a:b] for a, b in boundaries], ['Rating', '4/5', answer, second])
        for original in [answer, second]:
            a = text.index(original)
            self.assertEqual(sum(start <= a and a+len(original) <= end for start, end in boundaries), 1)
        from expand_benefitlens_experience_quality import paragraph_registry
        for index, span in enumerate(spans):
            span.update(document_id='review-fixture', text_sha256=audit.digest(text),
                        segment_id=str(index), source_span_hash_verified=True)
        registered = paragraph_registry(spans)
        start = text.index(answer)
        self.assertEqual(sum(p['paragraph_char_start'] <= start and
                             start+len(answer) <= p['paragraph_char_end'] for p in registered), 1)
        self.assertTrue(all(p['semantic_paragraph_approval'] is False for p in registered))
        foreign = audit.structural_segments(text, page, source_url='https://example.test/review')
        self.assertNotIn(answer, [text[s['char_start']:s['char_end']] for s in foreign
                                 if s['kind'] == 'source_dom_paragraph_aligned'])

    def test_dynamic_catalog_field_is_preserved_without_interpretation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            c = {'condition_id': 'c1', 'candidate_key': 'n1', 'field_id': 'future.new_field', 'status': 'candidate'}
            catalog = {'fields': {'future.new_field': {'applicant_input_stage': 'program_followup'}},
                       'condition_contract': {'required': list(c), 'status_values': ['candidate']}}
            for name, records in [('notices.jsonl.gz', [{'candidate_key': 'n1', 'conditions': [c]}]), ('conditions.jsonl.gz', [c])]:
                with gzip.open(root/name, 'wt') as stream:
                    for row in records:
                        stream.write(json.dumps(row)+'\n')
            summary = audit.personalization_requirements(root, root, catalog, 'catalog-hash')
            result = list(audit.jsonl(root/'personalization-requirements.jsonl.gz'))[0]
            self.assertEqual(result['input_channels']['program_followup'], [c])
            self.assertEqual(result['decision'], 'unknown')
            self.assertEqual(result['user_questions_issued'], 0)
            self.assertEqual(summary['candidate_counts_by_field']['future.new_field'], 1)
            c['status'] = 'approved'
            with gzip.open(root/'notices.jsonl.gz', 'wt') as stream:
                stream.write(json.dumps({'candidate_key': 'n1', 'conditions': [c]})+'\n')
            with self.assertRaisesRegex(ValueError, 'outside the shared candidate contract'):
                audit.personalization_requirements(root, root, catalog, 'catalog-hash')

    def test_legacy_and_synthetic_are_not_production_metrics(self):
        self.assertIsNone(audit.excluded_case({'case_key': 'real', 'payload': {}}))
        self.assertIsNotNone(audit.excluded_case({'case_key': 'old', 'payload': {'legacy_case_id': 'old-case'}}))
        self.assertIsNotNone(audit.excluded_case({'case_key': 'generated', 'payload': {'example_kind': 'synthetic'}}))


if __name__ == '__main__':
    unittest.main()
