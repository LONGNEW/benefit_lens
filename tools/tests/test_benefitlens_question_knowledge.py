from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect_benefitlens_question_knowledge import collect_stackexchange


def test_image_only_answer_is_held_without_stalling_other_answers_or_resume(tmp_path):
    class Probe:
        def __init__(self, source):
            self.receipts, self.paused = [], False
        def read(self, url):
            receipt = {'url': url, 'status': 200}
            self.receipts.append(receipt)
            if '/search/advanced?' in url:
                items = [{'question_id': 1, 'body': '<p>How should I explain my research?</p>',
                          'title': 'Application question', 'link': 'https://academia.stackexchange.com/q/1'}]
            else:
                items = [{'answer_id': 11, 'question_id': 1, 'body': '<img src="example.png">'},
                         {'answer_id': 12, 'question_id': 1, 'body': '<p>Connect the observation to your own decision.</p>'}]
            return {'items': items, 'has_more': False, 'quota_remaining': 250}, receipt
    records, checkpoint = {}, {}
    config = {'site': 'academia', 'question_queries': {'research_motivation': ['research statement']}}
    result = collect_stackexchange(tmp_path, config, records, checkpoint,
                                  pagesize=100, pages_per_query=1, request_limit=10, probe_factory=Probe)
    assert result['stop'] is None
    assert set(records) == {'stackexchange-question:1', 'stackexchange-answer:12'}
    held = checkpoint['held_api_records']['answer:11']
    assert not held['counted_as_document'] and Path(held['original_body_html']['path']).exists()
    assert all(batch['done'] for batch in checkpoint['answer_batches'].values())
    again = collect_stackexchange(tmp_path, config, records, checkpoint,
                                 pagesize=100, pages_per_query=1, request_limit=10, probe_factory=Probe)
    assert again['requests'] == 0 and len(records) == 2
