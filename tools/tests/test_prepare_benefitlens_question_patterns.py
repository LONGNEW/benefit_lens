from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benefitlens_question_evidence import locate_quote, source_text_sha256, QuestionEvidenceError
from prepare_benefitlens_question_patterns import evidence_context, prepare_inputs


def example(text='Describe your own contribution.\nExplain the result.'):
    quote='Describe your own contribution.'
    docs=[{'document_id':'answer-1','url':'https://example.org/answers/1',
        'text_ref':'/fixture/source.txt','text_sha256':source_text_sha256(text),
        'body_scope':'full_answer','publisher_role':'community'}]
    row={'evidence_id':'advice-1','document_id':'answer-1','question_intent':'personal_contribution',
        'source_role':'public_advice','claim_type':'recommendation','quote':quote,
        'locator':locate_quote(text,quote),'original_question':{
            'id':'question-1','text':'What should I describe?','context':'A community writing question'},
        'exact_prompt':None,'applicability':{'program_ids':[],'round_ids':[],'stages':[],
            'limitations':['General advice; not an official application prompt.']},
        'inference':{'label':'source_explicit','statement':'Describe the applicant contribution.'},
        'semantic_review':{'state':'pending'}}
    return {'version':'writing-question-evidence-v1','source_documents':docs,'question_evidence':[row]}, {'answer-1':text}


def test_question_without_program_round_outcome_or_whole_application_is_prepared():
    bundle,texts=example()
    inputs,summary=prepare_inputs(bundle,texts)
    assert summary['source_question_threads']==1
    assert len(inputs)==1
    assert inputs[0]['question_intent']=='personal_contribution'
    assert inputs[0]['whole_application_required'] is False
    assert inputs[0]['live_application_drafting_permitted'] is False
    assert inputs[0]['source_contexts'][0]['text']==texts['answer-1']


def test_rejected_advice_is_not_sent_for_pattern_extraction():
    bundle,texts=example();bundle['question_evidence'][0]['semantic_review']['state']='rejected'
    inputs,_=prepare_inputs(bundle,texts)
    assert inputs==[]


def test_stored_source_hash_must_still_match():
    bundle,texts=example();texts['answer-1']+=' changed'
    with pytest.raises(QuestionEvidenceError,match='hash mismatch'):
        prepare_inputs(bundle,texts)


def test_batch_boundaries_preserve_all_evidence_records():
    bundle,texts=example()
    for i in range(2,5):
        row=deepcopy(bundle['question_evidence'][0]);row['evidence_id']='advice-'+str(i)
        bundle['question_evidence'].append(row)
    inputs,_=prepare_inputs(bundle,texts,max_characters=2200)
    assert len(inputs)>1
    assert [r['evidence_id'] for x in inputs for r in x['question_evidence']]==['advice-1','advice-2','advice-3','advice-4']


def test_large_unbroken_context_fails_instead_of_silently_truncating():
    bundle,texts=example('Describe your own contribution. '+'context '*2000)
    with pytest.raises(ValueError,match='complete evidence context'):
        prepare_inputs(bundle,texts,max_characters=4000)


def test_adjacent_context_uses_offsets_into_complete_source():
    text=('A long earlier paragraph. '*600)+'\n\nPrior context.\n\nDescribe your own contribution.\n\nNext context.\n\nLater context.'
    bundle,_=example(text);row=bundle['question_evidence'][0]
    context=evidence_context(text,row)
    assert text[context['source_char_start']:context['source_char_end']]==context['text']
    assert row['quote'] in context['text'] and 'Next context.' in context['text']
    assert context['context_scope']=='quote_and_adjacent_paragraphs'
