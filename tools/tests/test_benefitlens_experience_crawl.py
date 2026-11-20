from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_experience_crawl import experience_content, discover_experience_links, literal_experience_passages, official_alumni_binding
from run_benefitlens_korea import soup, Held
from benefitlens_experience_crawl import attribute_case_passages
from hashlib import sha256


def test_navigation_text_cannot_substitute_for_an_experience_body():
    document = soup(('<nav>' + 'application story '*100 + '</nav><article><div class="entry-content">Image only</div></article>').encode())
    with pytest.raises(Held, match='Experience body missing'):
        experience_content(document, {'content_selector':'article .entry-content'})


def test_nested_content_selectors_do_not_duplicate_the_body():
    text = 'I revised my application after feedback. '*20
    document = soup(('<article><div class="entry-content">'+text+'</div></article>').encode())
    result = experience_content(document, {'content_selector':'article,.entry-content'})
    assert result == text.strip()


def test_index_discovery_is_same_publisher_and_does_not_invent_case_outcomes():
    document = soup(b'''<article><a href="/alice/#top">Alice</a><a href="/alice/">Alice</a>
        <a href="https://other.example/bob">Bob</a><a href="/login/">Login</a>
        <a href="/cases/page/2/">2</a></article>''')
    spec = {'program_id':'p','person_name':'Index editor','outcome':'selected',
            'follow_experience_links':{'selector':'article a','subject_from_anchor':True,
            'pagination_pattern':r'/cases/page/\d+/','child_content_selector':'article .entry-content'}}
    found = discover_experience_links(document, spec, 'https://example.org/cases/')
    assert [s['url'] for s in found] == ['https://example.org/alice/','https://example.org/cases/page/2/']
    assert found[0]['discovered_subject_name'] == 'Alice'
    assert 'outcome' not in found[0] and 'person_name' not in found[0]
    assert found[1]['role'] == 'experience_index'
    assert found[1]['follow_experience_links'] == spec['follow_experience_links']


def test_passages_are_literal_lines_with_no_inferred_pattern():
    text = 'Heading\nI revised my personal statement after feedback.\nMy application was rejected.\n'
    records = literal_experience_passages(text, 'doc-a', 'https://example.org/a')
    assert records[0]['line_start'] == 2
    assert records[0]['exact_quote'] == text.splitlines()[1]
    assert records[1]['exact_quote'] == 'My application was rejected.'
    assert all(r['interpretation_status'] == 'literal_passage_unreviewed' for r in records)


def test_published_docs_are_discovered_but_private_edit_links_are_not():
    document = soup(b'''<article>
        <a href="https://docs.google.com/document/d/e/public-id/pub">Published proposal</a>
        <a href="https://docs.google.com/document/d/private-id/edit">Private editor</a>
        </article>''')
    found = discover_experience_links(document, {'program_id':'p','follow_experience_links':
        {'selector':'article a','allow_published_google_docs':True}}, 'https://author.example/index')
    assert len(found) == 1
    assert found[0]['role'] == 'application_statement'
    assert found[0]['url'].endswith('/pub')


def test_official_story_keeps_conflicting_years_unknown_and_is_not_a_writing_sample():
    body = '홍길동 (Gil Dong Hong)\n2023 Graduate Student Program\nI received support.'
    document = soup(('<article class="category-gsp tag-2018-fulbright-graduate-student-program">'+body+'</article>').encode())
    spec = {'program_id':'fulbright-korea-gsp','discovered_subject_name':'Gil Dong Hong',
            'discovery':{'parent_url':'https://www.fulbright.or.kr/alumni-stories/gsp/'}}
    item = official_alumni_binding(document,spec,'https://www.fulbright.or.kr/gil-dong-hong/','a'*64,body,body)
    assert item['cycle'] is None
    assert {c['value'] for c in item['cycle_conflicts']} == {'2018','2023'}
    assert item['case_scope'] == 'post_award_experience'
    assert 'submitted_application_not_collected' in item['experience_profile']['material_availability']
    assert item['binding_evidence']['exact_quote'] == body.splitlines()[0]
    assert official_alumni_binding(document,spec,'https://other.example/gil-dong-hong/','a'*64,body,body) is None


def test_multi_person_passages_need_a_source_bound_span():
    text = 'Alice revised her application.\nBob was rejected after interview.'
    docs = {'d':{'url':'https://example.org/a','text':text}}
    cases = [{'case_id':'a','person_name':'Alice','document_ids':['d']},
             {'case_id':'b','person_name':'Bob','document_ids':['d']}]
    attribute_case_passages(cases, docs, lambda d:d['text'])
    assert all(c['experience_passages'] == [] for c in cases)
    cases[0]['case_text_span'] = {'document_id':'d','line_start':1,'line_end':1,
        'text_sha256':sha256(text.encode()).hexdigest()}
    attribute_case_passages(cases, docs, lambda d:d['text'])
    assert cases[0]['experience_passages'][0]['exact_quote'] == text.splitlines()[0]
    assert cases[1]['experience_passages'] == []
    cases[0]['case_text_span']['text_sha256'] = 'incorrect'
    attribute_case_passages(cases, docs, lambda d:d['text'])
    assert cases[0]['experience_passages'] == []


def test_single_subject_preserves_literal_evidence_and_audit_state():
    cases = [{'case_id':'a','document_ids':['d']}]
    attribute_case_passages(cases, {'d':{'url':'https://example.org/a','text':'I revised my application.'}}, lambda d:d['text'])
    assert cases[0]['experience_passages'][0]['line_start'] == 1
    assert cases[0]['quality_audit']['semantic_review'] == 'unreviewed'
