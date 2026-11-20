from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect_benefitlens_writing_corpus import outcome, parse_registry, index_cases, source_case, Collector, receipt_document, check_case_bindings
from import_benefitlens_writing_corpus import prepare_case, prepare_document
from hashlib import sha256
import pytest


def test_public_registry_keeps_outcomes_distinct_and_unwraps_original_links():
    cells = ['Alice', '2025a', 'Physics', 'First Year Grad', 'Honorable Mention',
             '<a href="https://www.google.com/url?q=https%3A%2F%2Fa.example%2Fresearch.pdf&amp;sa=D">Essay</a>',
             '', '', '']
    raw = ('<table><tr>' + ''.join('<td>'+c+'</td>' for c in cells) + '</tr></table>').encode()
    cases, docs = parse_registry(raw, 'https://example.org/registry')
    assert cases[0]['outcome'] == 'honorable_mention'
    assert cases[0]['cycle'] == '2025a'
    assert docs[0]['url'] == 'https://a.example/research.pdf'
    assert docs[0]['role'] == 'research_statement'
    assert outcome('Rejected') == 'not_selected'
    assert outcome('Winner!') == 'selected'
    assert outcome('Has a strong CV') == 'unknown'


def test_two_statements_are_one_case_and_other_year_is_another_case():
    base = {'program_id': 'nsf-grfp', 'person_name': 'Alice', 'cycle': '2025'}
    a = source_case(base)
    b = source_case(base | {'cycle': '2026'})
    docs = [{'document_id': 'personal', 'status': 'collected', 'role': 'personal_statement', 'case_bindings': a},
            {'document_id': 'research', 'status': 'collected', 'role': 'research_statement', 'case_bindings': a},
            {'document_id': 'next', 'status': 'collected', 'role': 'personal_statement', 'case_bindings': b}]
    cases = index_cases(docs)
    assert len(cases) == 2
    assert sorted(len(c['document_ids']) for c in cases) == [1, 2]
    assert len({c['person_name'] for c in cases}) == 1


def test_unreadable_document_does_not_increase_collected_case_count():
    assert index_cases([{'document_id': 'x', 'status': 'held', 'role': 'personal_statement',
                         'case_bindings': source_case({'person_name': 'Alice'})}]) == []


def test_pdf_link_returning_html_is_held():
    class Ledger:
        root = Path('/tmp')
        def request(self, url, **kwargs):
            return b'<html><body>Login required, document unavailable</body></html>', {
                'url': url, 'status': 200, 'headers': {}}
    result = Collector(Ledger()).capture({'url': 'https://example.org/research.pdf', 'role': 'research_statement'})
    assert result['status'] == 'held'
    assert 'returned HTML' in result['reason']


def test_archived_receipt_validates_both_hashes_and_case_quote(tmp_path):
    raw, text = b'<html>Public archive</html>', b'I was awarded the fellowship. ' * 8
    rp, tp = tmp_path/'raw.html', tmp_path/'text.txt'
    rp.write_bytes(raw); tp.write_bytes(text)
    receipt = {'source_id':'archive-a', 'url':'https://example.org/a',
               'access_scope':'public','capture_mode':'full_document',
               'raw_path':str(rp),'raw_sha256':sha256(raw).hexdigest(),
               'text_path':str(tp),'text_sha256':sha256(text).hexdigest(),
               'fetched_at':'2026-11-09T00:00:00Z'}
    spec = {'url':receipt['url'],'program_id':'p','person_name':'Alice','role':'experience_review',
            'binding_evidence':{'source_id':'archive-a','exact_quote':'I was awarded the fellowship.'}}
    doc = receipt_document(spec, receipt)
    assert doc['raw']['at'] == receipt['fetched_at']
    assert prepare_document(doc)['metadata']['writing_corpus']['full_text'] == text.decode()
    with pytest.raises(ValueError, match='quote is absent'):
        receipt_document(spec | {'binding_evidence':{'source_id':'archive-a','exact_quote':'I was rejected.'}}, receipt)
    tp.write_bytes(text + b' changed')
    with pytest.raises(ValueError, match='hash mismatch'):
        receipt_document(spec, receipt)


def test_case_loader_keeps_rejection_and_requires_collected_original():
    case = {'case_id':'a','program_id':'p','person_name':'Alice','cycle':'2020',
            'outcome':'not_selected','document_ids':['doc-a']}
    item = prepare_case(case, {'doc-a':{'url':'https://example.org/a'}}, {'doc-a':'postgres-doc-a'})
    assert item['outcome_claim'] == 'not_selected'
    assert item['payload']['document_ids'] == ['postgres-doc-a']
    assert item['payload']['approved_pattern'] is False
    with pytest.raises(ValueError, match='unavailable original'):
        prepare_case(case, {}, {})


def test_multi_person_interview_keeps_people_and_documents_separate():
    bindings = source_case({'program_id':'p','case_specs':[
        {'person_name':'Alice','cycle':'2020','outcome':'selected'},
        {'person_name':'Bob','cycle':None,'outcome':'unknown'}]})
    cases = index_cases([{'document_id':'interview','status':'collected',
                          'role':'official_interview','case_bindings':bindings}])
    assert len(cases) == 2
    assert {c['outcome'] for c in cases} == {'selected','unknown'}


def test_cross_document_case_claim_requires_the_cited_original(tmp_path):
    text = b'I was selected in 2020.'
    p = tmp_path/'index.txt'; p.write_bytes(text)
    binding = {'case_id':'a','binding_evidence':{'document_id':'index',
               'text_sha256':sha256(text).hexdigest(),'exact_quote':'I was selected in 2020.'}}
    essay = {'document_id':'essay','text':{'path':str(p)},'case_bindings':[binding]}
    issues = check_case_bindings([essay])
    assert len(issues) == 1
    assert essay['case_bindings'] == []
    essay['case_bindings'] = [binding]
    assert check_case_bindings([essay, {'document_id':'index','text':{'path':str(p)},'case_bindings':[]}]) == []


def test_official_same_name_and_award_round_are_one_case_across_two_stories():
    subject = {'program_id':'p','person_name':'Alice','cycle':'2016','outcome':'selected',
               'case_scope':'post_award_experience','outcome_basis':'official_program_grantee_story'}
    docs = [{'document_id':'story-1','status':'collected','role':'experience_review',
             'case_bindings':[{'case_id':'a',**subject}]},
            {'document_id':'story-2','status':'collected','role':'experience_review',
             'case_bindings':[{'case_id':'b',**subject}]}]
    cases = index_cases(docs)
    assert len(cases) == 1
    assert cases[0]['document_ids'] == ['story-1','story-2']
    assert cases[0]['merged_case_ids'] == ['b']
    docs[1]['case_bindings'][0]['cycle'] = '2020'
    assert len(index_cases(docs)) == 2
