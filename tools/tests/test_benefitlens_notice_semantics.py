"""Synthetic boundary fixtures, not corpus model-execution results."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
import sqlite3

from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benefitlens_notice_semantics as semantics
from benefitlens_source_storage import store_source_bytes


def empty_model_result(task):
    return {'analysis_version':semantics.VERSION, 'task_id':task['task_id'],
        'input_sha256':task['input_sha256'], 'chunk_id':task['chunk']['chunk_id'],
        'source_text_sha256':task['source']['text_ref']['sha256'], 'conditions':[],
        'logic_groups':[], 'root_logic_ids':[], 'root_list_semantics':'not_implicitly_conjoined',
        'exceptions':[], 'official_prompts':[], 'unknowns':[{'reason_code':'source_scope',
            'detail':'원문 범위를 해석한 검증용 결과다.', 'affected_ids':[], 'evidence':[]}]}


def condition(task, ident='c1', quote='Applicants must be enrolled in a PhD program.'):
    return {'condition_id':ident, 'field_id':'education.degree', 'program_specific_key':None,
        'statement_type':'eligibility_requirement', 'subject':'applicant', 'modality':'required',
        'operator':'eq', 'value_type':'enum', 'value':'doctorate', 'unit':None,
        'at':{'phase':'application', 'date':None, 'original_text':None},
        'qualifiers':{'gpa_scale':None, 'currency':None, 'frequency':None,
            'jurisdiction':None, 'original_value_text':None},
        'evidence':[{'document_id':task['source']['document_id'], 'quote':quote,
            'char_start':None, 'char_end':None}], 'unknown_reasons':[]}


class NoticeSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.text = ('Applicants must be enrolled in a PhD program.\n'
            'At least 2 years of experience or a masters degree is required.\n'
            'Institutions must nominate students.\n'
            'The award is KRW 6000000 per semester.\n'
            'Describe your research goals in 500 words.\n'
            'The closing date is 2026-12-01.\n'
            'The position involves managing a team.\n')
        self.record = self.record_for(self.text)
        self.bundle = semantics.build_input(self.record)
        self.task = self.bundle['tasks'][0]
        self.result = empty_model_result(self.task)
        self.result['conditions'] = [condition(self.task)]
        self.result['root_logic_ids'] = ['c1']

    def tearDown(self):
        self.tmp.cleanup()

    def record_for(self, text):
        raw = store_source_bytes(('<article>'+text+'</article>').encode(), self.root, suffix='.html')
        source = store_source_bytes(text.encode(), self.root, suffix='.txt')
        return {'candidate_key':'production-fixture', 'record_type':'production_candidate',
            'scope':'group_representative_original', 'source_url':'https://example.test/notice',
            'title':'연구 지원', 'original_ref':{'document_id':'d1', 'raw_path':raw['path'],
                'text_path':source['path'], 'content_sha256':raw['sha256'],
                'text_sha256':source['sha256'], 'fetched_at':'2026-11-19T00:00:00Z'}}

    def write_json(self, name, value):
        path = self.root/name
        path.write_text(json.dumps(value, ensure_ascii=False))
        return path

    def receipt(self, task, result, name='one'):
        # Deliberately labelled fixtures: never included in production manifests.
        paths = {key:self.write_json(name+'-'+key+'.json', value) for key,value in {
            'request':task['request'], 'response':{'structured_output':{'results':[
                {'task_id':task['task_id'], 'analysis':result['source_model_result']}]}},
            'result':result, 'input':task, 'validation':{'valid':True}}.items()}
        digest = lambda path:sha256(path.read_bytes()).hexdigest()
        return {'task_id':task['task_id'], 'input_sha256':task['input_sha256'],
            'model_called':True, 'exit_code':0, 'model':'test-fixture', 'models':['test-fixture'],
            'provider':'claude_cli', 'invocation_id':'synthetic-not-production',
            'request_ref':{'path':str(paths['request']), 'sha256':digest(paths['request'])},
            'response_ref':{'path':str(paths['response']), 'sha256':digest(paths['response'])},
            'result_sha256':semantics.fingerprint(result),
            'raw_result_sha256':result['raw_result_sha256'],
            'result_path':str(paths['result']), 'output_sha256':digest(paths['result']),
            'input_path':str(paths['input']), 'input_file_sha256':digest(paths['input']),
            'validation_path':str(paths['validation'])}

    def test_full_unicode_source_chunks_have_no_loss_or_overlap(self):
        text = '\n'.join('이것은 전체 원문의 문단이다. '+str(i) for i in range(700))
        bundle = semantics.build_input(self.record_for(text), max_chunk_characters=1000)
        self.assertGreater(len(bundle['tasks']), 1)
        self.assertEqual(''.join(t['chunk']['text'] for t in bundle['tasks']), text)
        for task in bundle['tasks']:
            semantics._verify_task(task)
            self.assertEqual(json.loads(task['request']['user'])['chunk'], task['chunk'])

    def test_missing_original_and_demo_do_not_become_completed(self):
        record = deepcopy(self.record); record['original_ref'] = None
        blocked = semantics.build_input(record)
        self.assertEqual(blocked['state'], 'blocked_source')
        self.assertEqual(blocked['tasks'], [])
        with self.assertRaisesRegex(ValueError, 'Missing original'):
            semantics.finalize_notice(blocked, [], [])
        record = deepcopy(self.record); record['scope'] = 'historical_demo'
        with self.assertRaisesRegex(ValueError, 'real production'):
            semantics.build_input(record)

    def test_damaged_original_hash_is_rejected_before_model_call(self):
        self.record['original_ref']['content_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'checksum'):
            semantics.build_input(self.record)

    def test_exact_quotes_resolve_and_stay_unreviewed(self):
        checked = semantics.validate_result(self.result, self.task)
        ev = checked['conditions'][0]['evidence'][0]
        self.assertEqual(self.text[ev['char_start']:ev['char_end']], ev['quote'])
        self.assertFalse(checked['meaning_approved'])
        self.assertFalse(checked['current_open_verified'])
        self.assertEqual(semantics.validate_result(checked, self.task), checked)

    def test_unique_original_expression_outside_short_quote_gets_separate_literal_evidence(self):
        c = self.result['conditions'][0]
        c['evidence'][0]['quote'] = 'Applicants must be enrolled'
        c['qualifiers']['original_value_text'] = 'a PhD program.'
        raw_before = deepcopy(self.result)
        checked = semantics.validate_result(self.result, self.task)
        self.assertEqual(self.result,raw_before)
        self.assertEqual(checked['source_model_result'],raw_before)
        self.assertEqual(len(checked['conditions'][0]['evidence']),2)
        evidence = checked['conditions'][0]['evidence'][1]
        self.assertEqual(evidence['quote'],'a PhD program.')
        self.assertEqual(self.text[evidence['char_start']:evidence['char_end']],evidence['quote'])
        self.assertEqual(checked['validation_added_evidence_bindings'][0]['evidence_index'],1)
        self.assertFalse(checked['meaning_approved'])
        self.assertEqual(semantics.validate_result(checked,self.task),checked)

    def test_whitespace_reconstruction_and_paraphrase_are_not_bound(self):
        task = semantics.build_input(self.record_for('Applicants must have a\nGSU\nprogram\nGPA\nof 3.0 or higher.'))['tasks'][0]
        result = empty_model_result(task)
        c = condition(task, quote='of 3.0 or higher.')
        c.update(field_id='education.gpa',value_type='number',value=3.0,operator='gte',unit='gpa_point')
        result['conditions'] = [c]
        for expression in ('GSU program GPA','Applicants need a GPA of 3.0'):
            c['qualifiers']['original_value_text'] = expression
            with self.assertRaisesRegex(ValueError,'Original expression differs'):
                semantics.validate_result(result,task)

    def test_ambiguous_or_unsubmitted_original_expression_is_not_bound(self):
        text = 'Applicants must be enrolled in a PhD program.\nA second PhD is mentioned.'
        task = semantics.build_input(self.record_for(text))['tasks'][0]
        result = empty_model_result(task);c = condition(task,quote='Applicants must be enrolled')
        c['qualifiers']['original_value_text'] = 'PhD';result['conditions']=[c]
        with self.assertRaisesRegex(ValueError,'Original expression differs'):
            semantics.validate_result(result,task)
        text = 'Applicants must be enrolled in a PhD program.\n'+('source context\n'*180)+'Unique final expression.'
        task = semantics.build_input(self.record_for(text),max_chunk_characters=1000)['tasks'][0]
        result = empty_model_result(task);c=condition(task,quote='Applicants must be enrolled')
        c['qualifiers']['original_value_text'] = 'Unique final expression.';result['conditions']=[c]
        with self.assertRaisesRegex(ValueError,'Original expression differs'):
            semantics.validate_result(result,task)

    def test_original_expression_binding_rechecks_source_hash(self):
        self.result['conditions'][0]['evidence'][0]['quote']='Applicants must be enrolled'
        self.result['conditions'][0]['qualifiers']['original_value_text']='a PhD program.'
        path=Path(self.task['source']['text_ref']['path'])
        path.write_bytes(b'Altered original text')
        with self.assertRaises(ValueError):
            semantics.validate_result(self.result,self.task)

    def test_fabricated_quote_wrong_document_and_wrong_offsets_fail(self):
        for ev_update in ({'quote':'Everyone receives funding.'}, {'document_id':'foreign'},
                {'char_start':1, 'char_end':44}):
            result = deepcopy(self.result)
            result['conditions'][0]['evidence'][0].update(ev_update)
            with self.assertRaises(ValueError):
                semantics.validate_result(result, self.task)

    def test_repeated_quote_requires_correct_explicit_offsets(self):
        bundle = semantics.build_input(self.record_for(self.text+self.text))
        task = bundle['tasks'][0]
        result = empty_model_result(task); result['conditions'] = [condition(task)]
        with self.assertRaisesRegex(ValueError, 'exactly once'):
            semantics.validate_result(result, task)
        ev = result['conditions'][0]['evidence'][0]
        ev.update(char_start=len(self.text), char_end=len(self.text)+len(ev['quote']))
        semantics.validate_result(result, task)

    def test_request_truncation_or_foreign_task_identity_fails(self):
        changed = deepcopy(self.task); changed['chunk']['text'] = 'summary'
        with self.assertRaisesRegex(ValueError, 'checksum'):
            semantics.validate_result(self.result, changed)
        result = deepcopy(self.result); result['input_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'identity'):
            semantics.validate_result(result, self.task)

    def test_numeric_boolean_enum_and_unit_types_are_checked(self):
        for field, vtype, value, unit in (('education.degree','enum','graduate',None),
                ('person.age','number',True,'year'), ('education.full_time','boolean',1,None),
                ('experience.years','number',2,'month')):
            result = deepcopy(self.result)
            result['conditions'][0].update(field_id=field, value_type=vtype, value=value, unit=unit)
            with self.assertRaises(ValueError):
                semantics.validate_result(result, self.task)

    def test_range_operand_does_not_change_shared_numeric_field_type(self):
        c = self.result['conditions'][0]
        c.update(condition_id='experience-range', field_id='experience.years', value_type='array',
            operator='between', value=[0,3], unit='year')
        self.result['root_logic_ids'] = ['experience-range']
        with self.assertRaises(ValueError) as error:
            semantics.validate_result(self.result, self.task)
        message = str(error.exception)
        for fragment in ('experience-range', 'experience.years', '"expected":"number"',
                '"actual":"array"', '"operator":"between"'):
            self.assertIn(fragment, message)
        c['value_type'] = 'number'
        checked = semantics.validate_result(self.result, self.task)
        self.assertEqual(checked['conditions'][0]['value'], [0,3])

    def test_exists_does_not_invent_a_numeric_threshold(self):
        bundle = semantics.build_input(self.record_for('Applicants must provide a GPA.'))
        task = bundle['tasks'][0]
        result = empty_model_result(task)
        c = condition(task, quote='Applicants must provide a GPA.')
        result['conditions'] = [c]
        c.update(field_id='education.gpa', value_type='number', operator='exists',
            value=None, unit='gpa_point')
        checked = semantics.validate_result(result, task)
        self.assertIsNone(checked['conditions'][0]['value'])
        c['operator'] = 'gte'
        with self.assertRaisesRegex(ValueError, 'Missing value'):
            semantics.validate_result(result, task)

    def test_or_not_exception_references_and_cycles_are_checked(self):
        second = condition(self.task, 'c2', 'At least 2 years of experience or a masters degree is required.')
        second.update(field_id='experience.years', operator='gte', value_type='number', value=2, unit='year')
        self.result['conditions'].append(second)
        evidence = deepcopy(second['evidence'])
        self.result['logic_groups'] = [{'group_id':'g1', 'operator':'any', 'members':['c1','c2'],
            'statement_type':'eligibility_requirement', 'evidence':evidence}]
        self.result['root_logic_ids'] = ['g1']
        self.result['exceptions'] = [{'exception_id':'e1', 'applies_to':['g1'], 'condition_ids':['c2'],
            'effect':'unclear', 'explanation':'예외의 정확한 범위 확인이 필요하다.',
            'unknown_reason':'scope_unclear', 'evidence':evidence}]
        semantics.validate_result(self.result, self.task)
        for mutation in ('missing', 'cycle', 'not_two', 'exception_missing'):
            result = deepcopy(self.result)
            if mutation == 'missing': result['logic_groups'][0]['members'] = ['absent']
            if mutation == 'cycle': result['logic_groups'][0]['members'] = ['g1']
            if mutation == 'not_two': result['logic_groups'][0]['operator'] = 'not'
            if mutation == 'exception_missing': result['exceptions'][0]['applies_to'] = ['absent']
            with self.assertRaises(ValueError):
                semantics.validate_result(result, self.task)

    def test_preferences_and_institution_obligations_cannot_be_applicant_filter(self):
        for update in ({'subject':'institution'}, {'modality':'preferred'},
                {'statement_type':'institution_requirement','subject':'applicant'}):
            result = deepcopy(self.result); result['conditions'][0].update(update)
            with self.assertRaises(ValueError):
                semantics.validate_result(result, self.task)

    def test_program_specific_typed_extension_keeps_its_own_key(self):
        c = self.result['conditions'][0]
        c.update(field_id='program_specific.requirement', program_specific_key='research_topic_overlap',
            value_type='boolean', value=True)
        checked = semantics.validate_result(self.result, self.task)
        self.assertEqual(checked['conditions'][0]['program_specific_key'], 'research_topic_overlap')
        c['program_specific_key'] = None
        with self.assertRaisesRegex(ValueError, 'specific key'):
            semantics.validate_result(self.result, self.task)

    def test_original_semester_amount_is_preserved_without_monthly_conversion(self):
        c = condition(self.task, quote='The award is KRW 6000000 per semester.')
        c.update(field_id='benefit.amount', statement_type='benefit_description', subject='program',
            modality='informational', value_type='number', value=6000000, unit='currency')
        c['qualifiers'].update(currency='KRW', frequency='per_semester', original_value_text='6000000 per semester')
        self.result['conditions'] = [c]
        checked = semantics.validate_result(self.result, self.task)
        self.assertEqual(checked['conditions'][0]['qualifiers']['frequency'], 'per_semester')
        self.assertEqual(checked['conditions'][0]['value'], 6000000)

    def test_official_prompt_is_literal_and_not_invented_for_another_subject(self):
        text = 'Describe your research goals in 500 words.'
        ev = {'document_id':'d1','quote':text,'char_start':None,'char_end':None}
        prompt = {'prompt_id':'p1', 'text':text, 'instruction_kind':'writing_prompt',
            'subject':'applicant', 'response_form':'short_answer', 'required':'required',
            'limit':{'min':None,'max':500,'unit':'words','scope_note':None},
            'evidence':[ev], 'unknown_reasons':[]}
        self.result['official_prompts'] = [prompt]
        semantics.validate_result(self.result, self.task)
        prompt['text'] = 'Write an inspiring story about yourself.'
        with self.assertRaisesRegex(ValueError, 'copied'):
            semantics.validate_result(self.result, self.task)
        prompt.update(text=text, subject='referee')
        with self.assertRaisesRegex(ValueError, 'Another subject'):
            semantics.validate_result(self.result, self.task)

    def test_invalid_dates_and_out_of_range_gpa_are_rejected(self):
        c = self.result['conditions'][0]
        c['at']['date'] = '2026-02-30'
        with self.assertRaisesRegex(ValueError, 'date'):
            semantics.validate_result(self.result, self.task)
        c['at']['date'] = None
        c.update(field_id='education.gpa',value_type='number',value=4.6,unit='gpa_point')
        c['qualifiers']['gpa_scale'] = 4.5
        with self.assertRaisesRegex(ValueError, 'GPA'):
            semantics.validate_result(self.result, self.task)

    def test_every_chunk_and_real_receipt_bindings_required_for_finalization(self):
        checked = semantics.validate_result(self.result, self.task)
        receipt = self.receipt(self.task, checked)
        final = semantics.finalize_notice(self.bundle, [checked], [receipt])
        self.assertEqual(final['status'], 'llm_analyzed_machine_validated')
        self.assertFalse(final['meaning_approved'])
        self.assertFalse(final['current_open_verified'])
        self.assertEqual(final['conditions'][0]['condition_id'], self.task['task_id']+'::c1')
        self.assertEqual(final['root_logic_ids'], [self.task['task_id']+'::c1'])
        with self.assertRaisesRegex(ValueError, 'Every original chunk'):
            semantics.finalize_notice(self.bundle, [], [])
        for key, value in (('model_called',False), ('input_sha256','0'*64),
                ('result_sha256','0'*64), ('raw_result_sha256','0'*64)):
            changed = deepcopy(receipt); changed[key] = value
            with self.assertRaises(ValueError):
                semantics.finalize_notice(self.bundle, [checked], [changed])

    def test_actual_response_cannot_be_swapped_for_different_raw_analysis(self):
        checked = semantics.validate_result(self.result, self.task)
        receipt = self.receipt(self.task, checked)
        path = Path(receipt['response_ref']['path'])
        value = json.loads(path.read_text()); value['structured_output']['results'][0]['analysis']['unknowns'] = []
        path.write_text(json.dumps(value))
        receipt['response_ref']['sha256'] = sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'Actual model response'):
            semantics.finalize_notice(self.bundle, [checked], [receipt])

    def test_multi_chunk_finalization_namespaces_local_ids(self):
        bundle = semantics.build_input(self.record_for('\n'.join(str(i)+' '+self.text for i in range(10))),
            max_chunk_characters=1000)
        results = [semantics.validate_result(empty_model_result(t), t) for t in bundle['tasks']]
        receipts = [self.receipt(t, r, str(i)) for i,(t,r) in enumerate(zip(bundle['tasks'], results))]
        final = semantics.finalize_notice(bundle, results, receipts)
        self.assertEqual(final['coverage']['chunks'],len(bundle['tasks']))
        self.assertEqual(final['coverage']['source_characters'],bundle['characters'])
        with self.assertRaisesRegex(ValueError, 'Every original chunk'):
            semantics.finalize_notice(bundle, results[:-1], receipts[:-1])

    def test_generation_grammar_preserves_base_schema_and_input_identity(self):
        base_bytes = semantics.SCHEMA.read_bytes()
        before = semantics.result_schema()
        task_hash = self.task['input_sha256']
        generated = semantics.generation_schema()
        Draft202012Validator.check_schema(generated)
        self.assertEqual(semantics.SCHEMA.read_bytes(), base_bytes)
        self.assertEqual(semantics.result_schema(), before)
        self.assertEqual(semantics.build_input(self.record)['tasks'][0]['input_sha256'],task_hash)
        self.assertNotIn('allOf',before['$defs']['condition'])
        self.assertTrue(Draft202012Validator(generated).is_valid(self.result))

    def test_generation_grammar_rejects_numeric_range_container_type(self):
        generated = Draft202012Validator(semantics.generation_schema())
        c = self.result['conditions'][0]
        c.update(field_id='experience.years',value_type='array',operator='between',value=[0,3],unit='year')
        self.assertTrue(Draft202012Validator(semantics.result_schema()).is_valid(self.result))
        self.assertFalse(generated.is_valid(self.result))
        c['value_type'] = 'number'
        self.assertTrue(generated.is_valid(self.result))

    def test_generation_grammar_requires_unknown_explanations(self):
        generated = Draft202012Validator(semantics.generation_schema())
        for update in ({'operator':'unknown'}, {'at':{'phase':'unknown','date':None,'original_text':None}}):
            result = deepcopy(self.result); result['conditions'][0].update(update)
            self.assertFalse(generated.is_valid(result))
            result['conditions'][0]['unknown_reasons'] = ['not_explicit_in_source']
            self.assertTrue(generated.is_valid(result))
            result['conditions'][0]['unknown_reasons'] = ['   ']
            self.assertFalse(generated.is_valid(result))

    def test_generation_grammar_rejects_subject_and_modality_promotions(self):
        generated = Draft202012Validator(semantics.generation_schema())
        for update in ({'subject':'institution'}, {'modality':'preferred'},
                {'statement_type':'institution_requirement','subject':'applicant'},
                {'statement_type':'selection_preference','modality':'required'}):
            result = deepcopy(self.result);result['conditions'][0].update(update)
            self.assertFalse(generated.is_valid(result))
        result = deepcopy(self.result)
        result['conditions'][0].update(field_id='program_specific.requirement',
            program_specific_key='research_area',value_type='boolean',value=True)
        self.assertTrue(generated.is_valid(result))

    def test_preserved_real_model_outputs_against_generation_grammar_when_available(self):
        root = Path('/home/longnew/.cache/datasets/benefitlens/scale50k-20261109/team-production-20261119/semantic')
        queue = root/'queue.sqlite'
        diagnostic = root/'diagnostics/notice-task-861defdc4b912c514b4bb28874ae.repair-feedback.json'
        if not queue.exists() or not diagnostic.exists():
            self.skipTest('Preserved real CLI execution cache is unavailable')
        cx = sqlite3.connect('file:'+str(queue)+'?mode=ro', uri=True)
        try:
            rows = cx.execute("SELECT result_path,input_path FROM tasks WHERE kind='notice' AND state='completed'").fetchall()
        finally:
            cx.close()
        if not rows:
            self.skipTest('No successfully validated actual notice result is preserved')
        generated = Draft202012Validator(semantics.generation_schema())
        for result_path,input_path in rows:
            checked = json.loads(Path(result_path).read_bytes())
            raw = checked['source_model_result']
            generated.validate(raw)
            # Adding a generation schema must not alter raw or checked canonical output.
            task = json.loads(Path(input_path).read_bytes())
            self.assertEqual(semantics.fingerprint(semantics.validate_result(raw,task)),
                semantics.fingerprint(checked))
        failed_ref = json.loads(diagnostic.read_text())['raw_analysis_ref']
        failed_bytes = Path(failed_ref['path']).read_bytes()
        self.assertEqual(sha256(failed_bytes).hexdigest(),failed_ref['sha256'])
        failed = json.loads(failed_bytes)
        self.assertTrue(Draft202012Validator(semantics.result_schema()).is_valid(failed))
        self.assertFalse(generated.is_valid(failed))


if __name__ == '__main__':
    unittest.main()
