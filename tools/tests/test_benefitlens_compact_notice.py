"""Compact retrieval contracts, grouping safety and group-local failure gates."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import benefitlens_compact_notice as compact


def record(text,key='real-fixture'):
    return {'record_type':'production_candidate','candidate_key':key,'scope':'group_representative_original',
        'source_url':'https://example.test/notice','original_ref':{'document_id':'source-'+key,
            'text_sha256':sha256(text.encode()).hexdigest()}}


def groups_for(text,key='real-fixture'):
    grouped={}
    for occurrence in compact.extract_contexts(record(text,key),source_text=text):
        gid=occurrence['group_id']
        if gid not in grouped:grouped[gid]={'group_id':gid,'representative':occurrence,'occurrence_count':0}
        grouped[gid]['occurrence_count']+=1
    return list(grouped.values())


def mapping_for(group):
    rep=group['representative'];field=rep['tags'][0]
    return {'field_id':field,'program_specific_key':'unmapped_qualification' if field=='program_specific.requirement' else None,
        'operator':'unknown','value_extraction_method':'unresolved','value_quote':None,'unit':None,
        'reference_phase':'unknown','statement_type':'eligibility_requirement','subject':'applicant',
        'modality':'required','logic_cues':[],'evidence_quote':rep['text'],
        'ambiguities':['기준 시점과 값 추출 방식을 확인해야 한다.']}


def response_for(batch):
    return {k:batch[k] for k in ('version','batch_id','input_sha256')}|{'groups':[
        {'group_id':g['group_id'],'mappings':[mapping_for(g)],'skip_reason':None} for g in batch['source_groups']]}


class CompactNoticeTests(unittest.TestCase):
    def test_source_offsets_include_multiline_gpa_and_match_original(self):
        text='지원 요건\nGSU\nprogram\nGPA\nof 3.0 or higher.\nApplicants must submit a transcript.'
        rows=compact.extract_contexts(record(text),source_text=text)
        self.assertTrue(any('education.gpa' in r['tags'] for r in rows))
        for row in rows:
            self.assertEqual(text[row['char_start']:row['char_end']],row['text'])
            self.assertEqual(text[row['context_start']:row['context_end']],row['context_text'])
            self.assertEqual(row['text_sha256'],sha256(text.encode()).hexdigest())
        with self.assertRaisesRegex(ValueError,'hash'):
            compact.extract_contexts(record(text),source_text=text+'tampered')

    def test_numeric_negation_subject_modality_and_time_changes_never_merge(self):
        variants=['Applicants must have GPA of 3.0.','Applicants must have GPA of 3.5.',
            'Applicants must not have GPA of 3.0.','Institutions must have GPA of 3.0.',
            'Applicants preferably have GPA of 3.0.','Applicants must have GPA of 3.0 before starting.']
        keys=[next(r['group_key'] for r in compact.extract_contexts(record(t),source_text=t) if 'education.gpa' in r['tags']) for t in variants]
        self.assertEqual(len(keys),len(set(keys)))

    def test_layout_whitespace_only_can_share_a_group(self):
        a='Required\nGPA 3.0.\nApply now.';b='Required\nGPA\t  3.0.\nApply now.'
        first=next(r for r in compact.extract_contexts(record(a),source_text=a) if 'education.gpa' in r['tags'])
        second=next(r for r in compact.extract_contexts(record(b),source_text=b) if 'education.gpa' in r['tags'])
        self.assertEqual(first['group_key'],second['group_key'])
        self.assertNotEqual(first['occurrence_id'],second['occurrence_id'])

    def test_institution_dependent_phrases_keep_notice_scope(self):
        text='Applicants from participating universities may apply.'
        a=compact.extract_contexts(record(text,'one'),source_text=text)
        b=compact.extract_contexts(record(text,'two'),source_text=text)
        self.assertNotEqual(a[0]['group_key'],b[0]['group_key'])

    def test_heading_required_and_preferred_changes_group(self):
        a=groups_for('Required\nGPA of 3.0.');b=groups_for('Preferred\nGPA of 3.0.')
        ga=next(g for g in a if 'education.gpa' in g['representative']['tags'])
        gb=next(g for g in b if 'education.gpa' in g['representative']['tags'])
        self.assertNotEqual(ga['group_id'],gb['group_id'])

    def test_partial_failure_preserves_other_groups_and_linked_sentence_counts(self):
        groups=groups_for("Applicants must hold a bachelor's degree.\n\nGPA of 3.0 or higher is required.")
        self.assertGreaterEqual(len(groups),2)
        batch=compact.build_batch(groups[:2]);raw=response_for(batch)
        raw['groups'][1]['mappings'][0]['field_id']='invented.field'
        checked=compact.validate_result(raw,batch)
        self.assertEqual(len(checked['groups']),1)
        self.assertEqual(len(checked['issues']),1)
        self.assertEqual(checked['counts']['normalized_groups'],1)
        self.assertEqual(checked['counts']['linked_sentence_occurrences'],1)
        with self.assertRaises(Exception):compact.validate_result(raw,batch,allow_partial=False)

    def test_missing_group_is_unresolved_without_retrying_good_group(self):
        groups=groups_for("Applicants must hold a bachelor's degree.\nGPA of 3.0 or higher is required.")
        batch=compact.build_batch(groups[:2]);raw=response_for(batch);raw['groups']=raw['groups'][:1]
        checked=compact.validate_result(raw,batch)
        self.assertEqual(len(checked['groups']),1);self.assertEqual(len(checked['issues']),1)

    def test_reversible_whitespace_binding_preserves_literal_original_span(self):
        groups=groups_for('Minimum GPA\n3.0 is required.')
        group=next(g for g in groups if 'education.gpa' in g['representative']['tags'])
        batch=compact.build_batch([group]);raw=response_for(batch)
        raw['groups'][0]['mappings'][0].update(evidence_quote='GPA 3.0',value_quote='3.0')
        checked=compact.validate_result(raw,batch)
        self.assertFalse(checked['issues'])
        evidence=checked['groups'][0]['source_bindings'][0]
        self.assertEqual(evidence['exact_source_quote'],'GPA\n3.0')
        self.assertEqual(evidence['lookup'],'reversible_whitespace_only')

    def test_paraphrased_quote_is_a_group_issue(self):
        batch=compact.build_batch(groups_for('GPA of 3.0 is required.'))
        raw=response_for(batch);raw['groups'][0]['mappings'][0]['evidence_quote']='Good grades are necessary.'
        checked=compact.validate_result(raw,batch)
        self.assertEqual(len(checked['groups']),0);self.assertEqual(len(checked['issues']),1)

    def test_unknown_reason_and_subject_invariants_are_retained(self):
        batch=compact.build_batch(groups_for('GPA of 3.0 is required.'))
        raw=response_for(batch);raw['groups'][0]['mappings'][0]['ambiguities']=[]
        self.assertTrue(compact.validate_result(raw,batch)['issues'])
        raw=response_for(batch);raw['groups'][0]['mappings'][0]['subject']='institution'
        self.assertTrue(compact.validate_result(raw,batch)['issues'])

    def test_source_binding_or_batch_identity_cannot_be_changed(self):
        batch=compact.build_batch(groups_for('GPA of 3.0 is required.'));raw=response_for(batch)
        changed=deepcopy(batch);changed['source_groups'][0]['representative']['char_start']=12
        with self.assertRaisesRegex(ValueError,'source occurrence'):
            compact.validate_result(raw,changed)
        raw['input_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'foreign batch'):
            compact.validate_result(raw,batch)

    def test_batch_limit_and_schema_are_well_formed(self):
        Draft202012Validator.check_schema(compact.result_schema())
        with self.assertRaises(ValueError):compact.build_batch([])
        groups=groups_for('GPA of 3.0 is required.')
        with self.assertRaises(ValueError):compact.build_batch(groups*51,batch_size=51)
        batch=compact.build_batch(groups)
        Draft202012Validator(compact.result_schema()).validate(response_for(batch))


if __name__=='__main__':unittest.main()
