from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deduplicate_benefitlens_inventory import canonical_url, group_rows


def row(i, title, issuer='Example', url=None, **extra):
    return {'record_id': str(i), 'title': title, 'issuer': issuer,
            'url': url or f'https://example.org/{i}', **extra}


def test_pagination_and_tracking_do_not_change_notice_but_download_does():
    a = 'https://school.ac.kr/notice?articleNo=12&mode=view&article.offset=10&utm_source=x'
    b = 'https://school.ac.kr/notice?mode=view&articleNo=12&article.offset=30'
    assert canonical_url(a) == canonical_url(b)
    assert canonical_url(a) != canonical_url(b.replace('view', 'download'))
    assert canonical_url(a) != canonical_url(b.replace('12', '13'))


def test_tongyeong_reposts_share_program_despite_different_reposting_schools():
    rows = [row(1, '2026년도 통영시 저소득 대학생 장학금 지원 공고', 'A University'),
            row(2, '2026년 통영시 저소득 대학생 장학금', 'B University')]
    groups = group_rows(rows)
    assert len(groups) == 1 and len(groups[0]['members']) == 2
    assert len(groups[0]['notice_urls']) == 2


def test_same_employer_role_groups_locations_but_keeps_variants():
    rows = [row(1, 'Software Engineering Intern - London', kind='company', location='London', country='UK'),
            row(2, 'Software Engineering Intern - Berlin', kind='company', location='Berlin', country='DE')]
    groups = group_rows(rows)
    assert len(groups) == 1 and groups[0]['countries'] == ['DE', 'UK']


def test_published_city_parenthesis_merges_but_role_specialization_survives():
    rows = [row(1, 'Software Engineer Intern (London, United Kingdom) (Summer 2027)',
                kind='company', location={'name': 'London, England'}),
            row(2, 'Software Engineer Intern (Summer 2027)', kind='company'),
            row(3, 'Software Engineer Intern (Machine Learning) (2027)', kind='company')]
    groups = group_rows(rows)
    assert len(groups) == 2
    assert sorted(len(g['members']) for g in groups) == [1, 2]


def test_generic_application_portal_does_not_merge_different_programs():
    rows = [row(1, 'Biology Scholarship', application_url='https://example.org/apply'),
            row(2, 'Engineering Scholarship', application_url='https://example.org/apply')]
    assert len(group_rows(rows)) == 2


def test_catalogue_homepage_and_post_detail_address_are_not_notice_identity():
    rows = [row(1, 'Merit Scholarship', url='https://example.org/'),
            row(2, 'Living Cost Scholarship', url='https://example.org/'),
            row(3, 'Recruiting Intern', url='https://example.org/detail.do',
                native='100', native_scope='youth'),
            row(4, 'Recruiting Intern', url='https://example.org/detail.do',
                native='200', native_scope='youth')]
    assert len(group_rows(rows)) == 4


def test_unrelated_institutions_and_unnamed_records_remain_distinct():
    rows = [row(1, 'Research Intern', 'First'), row(2, 'Research Intern', 'Second'),
            row(3, '', '', url=''), row(4, '', '', url='')]
    assert len(group_rows(rows)) == 4


def test_deduplication_does_not_promote_candidate_to_verified():
    groups = group_rows([row(1, 'Biology Scholarship', state='held'),
                         row(2, 'Biology Scholarship', state='awaiting_review')])
    assert len(groups) == 1
    assert groups[0]['historically_reviewed'] is False
    assert {r['state'] for r in groups[0]['members']} == {'held', 'awaiting_review'}
