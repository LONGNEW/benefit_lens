"""Regression checks for source scope, dates and catalogue navigation."""
from datetime import datetime
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect_benefitlens_kstartup import parse_detail, parse_listing
from discover_benefitlens_university_boards_v2 import infer_board, navigation, public_redirect, NavigationEvidence
from run_benefitlens_campaign import KST
from collect_benefitlens_crossborder import project_original, project_hwp_xml
from expand_benefitlens_public_boards import discover, STUDENT_ROLE
from run_benefitlens_korea import available_sources, Held


def test_catalogue_carousel_never_becomes_filtered_results():
    raw = b'''<div class="slide"><a href="javascript:go_view(999);">Pinned company ad</a></div>
    <div class="middle"><a href="javascript:go_view(12);"><p class="tit">Student award</p></a></div>
    <div class="paginate"><a class="active">1</a><a onclick="fn_egov_link_page(2); return false;">2</a></div>'''
    rows, pages, current = parse_listing(raw)
    assert set(rows) == {'12'}
    assert pages == {2} and current == 1


def notice(period='2026.11.01 09:00 ~ 2026.11.30 18:00', target='대학생',
           eligibility='전국 대학생 개인 또는 팀', exclusion='', benefit='상금 100만원'):
    fields = {'대상': target, '신청대상': eligibility, '제외대상': exclusion, '신청기간': period}
    html = '<div class="app_notice_details-wrap"><h3 id="scrTitle">공모전</h3>'
    html += ''.join(f'<div class="table_inner"><p class="tit">{k}</p><p class="txt">{v}</p></div>'
                    for k, v in fields.items())
    html += f'<div class="information_list"><p class="title">지원내용</p>{benefit}</div>'
    html += '<a href="https://example.org/apply">접수 바로가기</a></div>'
    return html.encode()


@pytest.mark.parametrize('period,state', [
    ('2026.11.01 09:00 ~ 2026.11.15 15:00', 'not_currently_open'),
    ('2026.11.01 09:00 ~ 2026.11.15 18:00', 'awaiting_review'),
    ('2026.11.01 ~ 2026.11.15', 'closing_time_unresolved'),
    ('2026.11.16 ~ 2026.11.30', 'not_currently_open'),
    ('2026.11.15 18:00 ~ 2026.11.30 18:00', 'not_currently_open'),
    ('2026.11.01 ~ 2026.11.30', 'awaiting_review'),
])
def test_application_window_uses_time_and_both_boundaries(period, state):
    row = parse_detail(notice(period), 'https://example.org/notice', datetime(2026, 11, 15, 16, tzinfo=KST))
    assert row['state'] == state


def test_student_search_tag_does_not_override_eligibility_or_missing_benefit():
    now = datetime(2026, 11, 15, 16, tzinfo=KST)
    row = parse_detail(notice(exclusion='대학생 제외'), 'https://example.org/n', now)
    assert row['state'] == 'student_excluded'
    row = parse_detail(notice(benefit='네트워킹'), 'https://example.org/n', now)
    assert row['state'] == 'financial_benefit_unresolved'
    row = parse_detail(notice(target='기업', eligibility='설립 7년 이내 법인'), 'https://example.org/n', now)
    assert row['state'] == 'individual_eligibility_unresolved'


def test_board_id_is_navigation_and_article_id_is_not():
    html = ('<a href="/list.do?boardId=7">공지사항</a>'
            '<a href="/view.do?nttId=12">장학 공고</a>'
            '<a href="https://external.ac.kr/list">장학 공지</a>').encode()
    rows = navigation(html, 'https://school.ac.kr/', 'school.ac.kr')
    assert [(u, label) for u, label, _ in rows] == [('https://school.ac.kr/list.do?boardId=7', '공지사항')]


def test_post_identity_varies_while_board_identity_is_constant():
    html = ('<a href="/view.do?boardId=7&amp;nttId=11">장학 모집</a>'
            '<a href="/view.do?boardId=7&amp;nttId=12">서포터즈 모집</a>').encode()
    rule = infer_board(html, 'https://school.ac.kr/list.do', 'Test School')
    assert rule['identity'] == 'nttId'
    assert rule['query_detail_path'] == '/view.do'


def test_public_landing_redirect_is_literal_same_school_and_not_login():
    base = 'https://school.ac.kr/'
    assert public_redirect(b"<script>location.href='/main/index.do';</script>", base,
                           'school.ac.kr') == base + 'main/index.do'
    assert public_redirect(b"<script>location.href='https://external.ac.kr/';</script>",
                           base, 'school.ac.kr') is None
    assert public_redirect(b"<script>location.href='/sso/login';</script>",
                           base, 'school.ac.kr') is None
    assert public_redirect(b'<script>location.href=decrypt(x);</script>',
                           base, 'school.ac.kr') is None


def test_greenhouse_full_board_is_readable_and_prospect_pool_is_excluded(tmp_path):
    import json
    raw = json.dumps({'jobs': [
        {'id': 11, 'internal_job_id': 4, 'title': 'Research Intern',
         'absolute_url': 'https://job-boards.greenhouse.io/example/jobs/11',
         'content': '&lt;p&gt;Enrolled PhD student. $40 per hour.&lt;/p&gt;'},
        {'id': 12, 'internal_job_id': None, 'title': 'Join our talent pool',
         'absolute_url': 'https://job-boards.greenhouse.io/example/jobs/12',
         'content': 'Future opportunities'},
    ]}).encode()
    _, text = project_original(raw, tmp_path)
    assert 'Enrolled PhD student. $40 per hour.' in text
    assert 'Future opportunities' not in text


def test_public_board_expansion_uses_observed_post_and_complete_company_role_boundary():
    boards = discover({'a': 'https://job-boards.greenhouse.io/test/jobs/11',
                       'b': 'https://job-boards.greenhouse.io/test/jobs/12',
                       'bad': 'https://unrelated.example/test/jobs/13'})
    assert list(boards) == ['test']
    assert len(boards['test']['observed_post_urls']) == 2
    assert STUDENT_ROLE.search('PhD Research Intern')
    assert not STUDENT_ROLE.search('Senior Internal Auditor')


def test_hwp_table_exclusions_are_preserved_and_metadata_is_not_body():
    raw = ('<HwpDoc><DocInfo><Text>Metadata</Text></DocInfo><BodyText><Paragraph>'
           '<Text>지원자격</Text><TableControl><TableCell><Paragraph>'
           '<Text>제외대상: 대학원생, 휴학생</Text></Paragraph></TableCell></TableControl>'
           '</Paragraph></BodyText></HwpDoc>').encode()
    text = project_hwp_xml(raw)
    assert '제외대상: 대학원생, 휴학생' in text
    assert 'Metadata' not in text


def test_published_email_is_an_application_route():
    raw = notice().decode().replace('<h3 id="scrTitle">',
        '<div class="table_inner"><p class="tit">신청방법</p>'
        '<p class="txt">이메일 접수: apply@example.org</p></div><h3 id="scrTitle">')
    raw = raw.replace('<a href="https://example.org/apply">접수 바로가기</a>', '')
    row = parse_detail(raw.encode(), 'https://example.org/n', datetime(2026, 11, 15, 16, tzinfo=KST))
    assert row['state'] == 'awaiting_review'
    assert row['application_emails'] == ['apply@example.org']


def test_new_discovery_feeds_daily_collection_without_duplicating_same_board(tmp_path):
    import json
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'sources': [{'id': 'base', 'url': 'https://school.ac.kr/list'}]}))
    folder = tmp_path / 'university-discovery-v2'
    folder.mkdir()
    folder.joinpath('status.json').write_text(json.dumps({'sites': {'school': {'boards': [
        {'source_config': {'id': 'same', 'url': 'https://school.ac.kr/list'}},
        {'source_config': {'id': 'new', 'url': 'https://another.ac.kr/list'}},
    ]}}}))
    assert {r['id'] for r in available_sources(tmp_path, config)} == {'base', 'new'}


def test_malformed_hwp_projection_is_held():
    with pytest.raises(Held, match='malformed'):
        project_hwp_xml(b'<HwpDoc><broken>')


def test_same_school_public_redirect_is_followed_but_login_is_held(monkeypatch):
    from run_benefitlens_korea import Evidence
    target = '/public/notice'
    def response(self, url, **kwargs):
        if url.endswith('/start'):
            return b'', {'url': url, 'status': 302, 'headers': {'location': target}}
        return b'<p>Notice body</p>', {'url': url, 'status': 200, 'headers': {}}
    monkeypatch.setattr(Evidence, 'request', response)
    ledger = object.__new__(NavigationEvidence)
    raw, meta = ledger.request('https://school.ac.kr/start')
    assert raw == b'<p>Notice body</p>' and meta['url'].endswith('/public/notice')
    target = '/sso/login'
    with pytest.raises(Held, match='public school'):
        ledger.request('https://school.ac.kr/start')
