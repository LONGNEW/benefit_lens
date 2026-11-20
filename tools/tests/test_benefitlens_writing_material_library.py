from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_benefitlens_writing_material_library import (
    canonical_url, deduplicate, material_coverage, select_examples, text_key,
)


def row(key, url, text):
    return {'source_key': key, 'url': url, 'text': text,
            'normalized_text_sha256': text_key(text), 'program_ids': []}


def test_syndication_and_url_variants_merge_transitively():
    rows = [row('a','https://x.test/a?utm_source=feed','first version'),
            row('b','https://x.test/a','second version'),
            row('c','https://y.test/b','second\nversion')]
    assert len(deduplicate(rows)) == 1


def test_several_sections_in_one_text_are_one_material():
    doc = row('a','https://x.test/one.pdf','Research statement\nLeadership statement')
    assert len(deduplicate([doc])) == 1


def test_separate_documents_do_not_imply_separate_people():
    docs = [row('a','https://x.test/research.pdf','research'),
            row('b','https://x.test/personal.pdf','personal')]
    assert len(deduplicate(docs)) == 2
    assert 'independent_applicants' not in docs[0]


def test_coverage_does_not_promote_a_global_ratio_to_per_notice_completion():
    report = material_coverage(6, 18, 3)
    assert report['global_ratio_met'] is True
    assert report['per_notice_relevance_verified'] is False
    assert report['completion_met'] is False


def test_quote_must_exist_in_complete_stored_body():
    data = row('a','https://x.test/a','a source with actual advice')
    spec = {'key':'a','quote':'invented sentence'}
    with pytest.raises(ValueError): select_examples([data],[spec])


def test_same_original_cannot_fill_two_showcase_positions():
    data = row('a','https://x.test/a','a source with actual advice')
    spec = {'key':'a','quote':'actual advice'}
    with pytest.raises(ValueError,match='duplicate'): select_examples([data],[spec,spec])


def test_drive_tracking_keeps_document_identity():
    assert canonical_url('https://drive.google.com/file/d/abc/view?usp=sharing') == 'https://drive.google.com/file/d/abc/view'
    assert canonical_url('https://docs.test/open?id=abc') != canonical_url('https://docs.test/open?id=def')
