"""Question-level acquisition must preserve provenance and resume safely."""
from hashlib import sha256
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from benefitlens_source_storage import read_source_text
from collect_benefitlens_question_knowledge import collect_stackexchange, saved_record, status, official_text
from run_benefitlens_korea import soup


class FakeProbe:
    def __init__(self, source):
        self.receipts, self.paused = [], False

    def read(self, url):
        page = int(parse_qs(urlsplit(url).query)['page'][0])
        receipt = {'status': 200, 'url': url}
        self.receipts.append(receipt)
        if '/search/' in url:
            item = {'question_id': page, 'title': 'How to explain my research?',
                    'body': '<p>How do I describe my own contribution?</p>',
                    'link': 'https://academia.stackexchange.com/questions/' + str(page),
                    'owner': {'display_name': 'Question Author'}, 'content_license': 'CC BY-SA 4.0'}
            return {'items': [item], 'has_more': page == 1, 'quota_remaining': 50}, receipt
        ids = urlsplit(url).path.split('/')[3].split(';')
        items = [{'answer_id': int(q) + 100, 'question_id': int(q),
                  'body': '<p>State your own action.</p><p>Then explain the result.</p>',
                  'owner': {'display_name': 'Answer Author'}, 'content_license': 'CC BY-SA 4.0',
                  'is_accepted': True} for q in ids]
        return {'items': items, 'has_more': False, 'quota_remaining': 49}, receipt


def test_resumes_question_pages_and_retains_full_answer_parent(tmp_path):
    config = {'site': 'academia', 'question_queries': {'personal_contribution': ['research contribution']}}
    rows, checkpoint = {}, {}
    collect_stackexchange(tmp_path, config, rows, checkpoint, pagesize=5,
                          pages_per_query=1, request_limit=20, probe_factory=FakeProbe)
    first = rows['stackexchange-answer:101']
    text = read_source_text(first['text_ref']['path'], storage=first['text_ref']['storage'])
    assert text == 'State your own action.\nThen explain the result.'
    assert first['text_sha256'] == sha256(text.encode()).hexdigest()
    assert first['original_question']['id'] == '1'
    assert first['content_license'] == 'CC BY-SA 4.0'
    assert first['approved_pattern'] is False
    collect_stackexchange(tmp_path, config, rows, checkpoint, pagesize=5,
                          pages_per_query=1, request_limit=20, probe_factory=FakeProbe)
    assert len([r for r in rows.values() if r['body_scope'] == 'full_question']) == 2
    assert len([r for r in rows.values() if r['body_scope'] == 'full_answer']) == 2
    assert all(s['done'] for s in checkpoint['searches'].values())
    assert status(rows, checkpoint)['approved_patterns'] == 0
    assert status(rows, checkpoint)['accepted_experiences_added'] == 0


def test_answer_requires_original_question_and_body(tmp_path):
    item = {'answer_id': 1, 'question_id': 9, 'body': '<p>My advice</p>'}
    with pytest.raises(ValueError, match='original question'):
        saved_record(tmp_path, item, 'answer', ['program_fit'], {})
    with pytest.raises(ValueError, match='body missing'):
        saved_record(tmp_path, {'question_id': 9, 'body': ''}, 'question', ['program_fit'], {})


def test_durable_provider_pause_prevents_request(tmp_path):
    rows = {}
    checkpoint = {'source_pause': {'reason': 'provider_backoff', 'resume_after_epoch': 10**15}}
    result = collect_stackexchange(tmp_path, {'question_queries': {}}, rows, checkpoint,
                                   pagesize=5, pages_per_query=1, request_limit=20,
                                   probe_factory=FakeProbe)
    assert result == {'requests': 0, 'stop': 'provider_backoff'}


def test_cross_intent_duplicate_keeps_one_document(tmp_path):
    config = {'site': 'academia', 'question_queries': {
        'personal_contribution': ['contribution'], 'program_fit': ['fit']}}
    rows, checkpoint = {}, {}
    collect_stackexchange(tmp_path, config, rows, checkpoint, pagesize=5,
                          pages_per_query=1, request_limit=20, probe_factory=FakeProbe)
    assert len(rows) == 2
    assert rows['stackexchange-answer:101']['retrieval_intents'] == ['personal_contribution', 'program_fit']


def test_answers_only_does_not_expand_completed_search_window(tmp_path):
    config = {'site': 'academia', 'question_queries': {'program_fit': ['fit']}}
    rows, checkpoint = {}, {}
    collect_stackexchange(tmp_path, config, rows, checkpoint, pagesize=5,
                          pages_per_query=1, request_limit=20, probe_factory=FakeProbe)
    before = json.loads(json.dumps(checkpoint['searches']))
    collect_stackexchange(tmp_path, config, rows, checkpoint, pagesize=5,
                          pages_per_query=3, request_limit=20, probe_factory=FakeProbe, answers_only=True)
    assert checkpoint['searches'] == before
    assert status(rows, checkpoint)['unique_questions'] == 1


def test_official_main_includes_accordion_but_excludes_award_picker():
    evidence = 'Describe specific modules and first choice course. ' * 10
    page = soup(('<main><section id="accordion">' + evidence + '</section>'
                 '<div class="landing-content-inner">AI policy.</div>'
                 '<section class="feed-award-outer">Country picker irrelevant.</section></main>').encode())
    text = official_text(page, {'selectors': ['main'], 'exclude_selectors': ['.feed-award-outer']})
    assert evidence.strip() in text
    assert 'AI policy.' in text
    assert 'Country picker' not in text
