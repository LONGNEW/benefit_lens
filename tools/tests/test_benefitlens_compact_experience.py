"""Compact source-locator and partial-batch checks, with no model calls."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benefitlens_compact_experience as compact


def passage(text, *, index=1, before='', after='', source_role='participation_review'):
    context=before+text+after
    return {'passage_id':'p'+str(index),'kind':'experience','document_id':'doc'+str(index),
        'text_sha256':sha256(context.encode()).hexdigest(),'char_start':100+len(before),
        'char_end':100+len(before)+len(text),'text':text,'context_start':100,
        'context_end':100+len(context),'context_text':context,'case_key':'case'+str(index),
        'source_url':'https://example.test/review/'+str(index),'source_role':source_role,
        'genre':'review','sentence_index':index,'paragraph_position':'middle'}


def annotation(group):
    return {'group_id':group['group_id'],'relevance':'relevant','event_roles':['reflection'],
        'actor':'self','writing_move':'본인 행동 뒤에 배운 점을 연결한다.',
        'material_role':'성찰 재료','claim_evidence_reasoning':{'claim':None,'evidence':None,'reasoning':None},
        'opening_or_closing_function':{'opening':None,'closing':None},
        'local_connection':{'kind':'not_observed','description':None},'limits':['보이는 구절만 해석했다.'],
        'support_quote':None}


class CompactExperienceTests(unittest.TestCase):
    def setUp(self):
        self.first=passage('I learned to explain the result after I tested the prototype.')
        self.second=passage('I decided to revise my application after the interview.',index=2)
        self.groups=compact.group_candidates([self.first,self.second])
        self.batch=compact.build_batch(self.groups)

    def test_lexical_hits_keep_exact_source_offsets(self):
        candidate=compact.tag_passage(self.first)
        self.assertIn('reflection',candidate['tags'])
        self.assertEqual(candidate['text'],self.first['text'])
        for hit in candidate['tag_hits']:
            a,b=hit['char_start']-self.first['char_start'],hit['char_end']-self.first['char_start']
            self.assertEqual(self.first['text'][a:b],hit['text'])
        self.assertIn('not_semantic',candidate['tag_status'])

    def test_header_email_page_and_publisher_metadata_are_excluded(self):
        for text in ('Subject: Application interview\nTo: example@example.test',
                     'Name [email]        Wed, Aug 27, 2025 at 1:22 PM\nTo: [email]',
                     'fus-26-27-02-statement-of-grant-purpose2',
                     'Fulbright U.S. Student Program','Privacy policy and application cookies'):
            self.assertIsNone(compact.tag_passage(passage(text)),text)

    def test_personal_narrative_is_not_dropped_merely_for_an_email_address(self):
        value=passage('I worked with a mentor and emailed mentor@example.test after the interview.')
        self.assertIsNotNone(compact.tag_passage(value))

    def test_safe_grouping_preserves_negation_numbers_and_different_context(self):
        a=passage('I learned three methods.')
        b=passage('I  learned three methods.',index=2)
        self.assertEqual(compact.group_key(a),compact.group_key(b))
        self.assertNotEqual(compact.group_key(a),compact.group_key(passage('I did not learn three methods.')))
        self.assertNotEqual(compact.group_key(a),compact.group_key(passage('I learned four methods.')))
        self.assertNotEqual(compact.group_key(a),compact.group_key(passage(a['text'],before='Team context. ')))
        self.assertNotEqual(compact.group_key(a),compact.group_key(passage(a['text'],source_role='official_guidance')))

    def test_wrong_source_offsets_are_not_silently_tagged(self):
        changed=deepcopy(self.first);changed['char_end']+=1
        with self.assertRaisesRegex(ValueError,'source offsets'):
            compact.tag_passage(changed)

    def test_long_source_has_an_explicit_bounded_view_without_completion_claim(self):
        value=passage('x'*2000+' I learned to test carefully. '+'y'*2000)
        batch=compact.build_batch(compact.group_candidates([value]),max_context_characters=400)
        view=batch['groups'][0]['view']
        self.assertEqual(len(view['text']),400)
        self.assertFalse(view['focus_complete_in_view'])
        self.assertFalse(view['context_complete_in_view'])
        self.assertEqual(view['text'],value['context_text'][view['char_start']-100:view['char_end']-100])
        self.assertNotIn('document_id',json.loads(batch['request']['user'])['groups'][0])

    def test_partial_result_keeps_valid_sibling_and_missing_group_unresolved(self):
        result=compact.validate_result({'groups':[annotation(self.groups[0])]},self.batch)
        self.assertEqual(result['counts']['interpreted_passage_groups'],1)
        self.assertEqual(result['unresolved_groups'][0]['reason'],'missing_group')
        self.assertEqual(result['document_analysis_completions'],0)
        self.assertEqual(result['case_analysis_completions'],0)

    def test_invalid_group_never_forces_valid_sibling_regeneration(self):
        good,bad=map(annotation,self.groups);bad['actor']='invented_actor'
        result=compact.validate_result({'groups':[good,bad]},self.batch)
        self.assertEqual(len(result['groups']),1)
        self.assertEqual(result['groups'][0]['group_id'],good['group_id'])
        self.assertEqual(result['unresolved_groups'][0]['group_id'],bad['group_id'])

    def test_uncertain_annotation_is_retained_as_unresolved_without_losing_sibling(self):
        good,uncertain=map(annotation,self.groups);uncertain['relevance']='uncertain'
        result=compact.validate_result({'groups':[good,uncertain]},self.batch)
        self.assertEqual(len(result['groups']),1)
        self.assertEqual(result['counts']['uncertain_passage_groups'],1)
        self.assertEqual(result['unresolved_groups'][0]['reason'],'semantic_uncertain')
        self.assertEqual(result['unresolved_groups'][0]['annotation']['group_id'],uncertain['group_id'])

    def test_optional_quote_is_not_required_but_supplied_quote_must_match(self):
        row=annotation(self.groups[0]);row['support_quote']='I learned to explain the result'
        checked=compact.validate_group(row,self.batch['groups'][0])
        self.assertEqual(checked['support_evidence']['char_start'],self.first['char_start'])
        self.assertFalse(checked['nonrepresentative_quotes_validated'])
        row['support_quote']='A fabricated achievement'
        with self.assertRaisesRegex(ValueError,'absent or ambiguous'):
            compact.validate_group(row,self.batch['groups'][0])

    def test_duplicate_and_foreign_groups_are_quarantined_individually(self):
        good=annotation(self.groups[0]);other=annotation(self.groups[1]);foreign=deepcopy(good);foreign['group_id']='foreign'
        result=compact.validate_result({'groups':[good,good,other,foreign]},self.batch)
        self.assertEqual([x['group_id'] for x in result['groups']],[other['group_id']])
        self.assertEqual({x['reason'] for x in result['unresolved_groups']},{'duplicate_group','unexpected_or_missing_group_id'})
        with self.assertRaisesRegex(ValueError,'validation unresolved'):
            compact.validate_result({'groups':[]},self.batch,allow_partial=False)

    def test_sentence_identity_count_is_separate_from_expression_occurrences(self):
        group=deepcopy(self.groups[0]);ref=compact.source_ref(self.first)
        duplicate=dict(ref,char_start=ref['char_start']+1,char_end=ref['char_end']+1)
        group.update(source_refs=[ref,duplicate],occurrence_count=2)
        batch=compact.build_batch([group]);result=compact.validate_result({'groups':[annotation(group)]},batch)
        self.assertEqual(result['counts']['linked_occurrences'],2)
        self.assertEqual(result['counts']['linked_unique_source_sentences'],1)


if __name__=='__main__':
    unittest.main()
