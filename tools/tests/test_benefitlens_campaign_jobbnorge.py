import json
from datetime import date
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_campaign_jobbnorge import LIST_URL, TEMPLATE_URL, listing_jobs, parse_detail, verify_candidate
from benefitlens_source_storage import store_source_bytes

DAY = date(2026, 11, 9)
NATIVE = '310313'
APPLY = 'https://www.jobbnorge.no/jobseeker/#/application/apply/310313'
DETAIL = 'https://id.jobbnorge.no/api/joblisting?jobId=310313&languageId=2'
INFO = 'https://id.jobbnorge.no/api/joblisting/job-info?jobId=310313&languageId=2'
PAGE = 'https://www.jobbnorge.no/ledige-stillinger/stilling/310313'
SOURCE = {'id': 'jobbnorge-phd', 'adapter': 'jobbnorge', 'url': LIST_URL}


def capture(tmp_path, url, contents):
    raw = contents.encode() if isinstance(contents, str) else json.dumps(contents).encode()
    original = store_source_bytes(raw, tmp_path, suffix='.raw')
    projection = store_source_bytes(raw, tmp_path, suffix='.txt')
    return {'url': url, 'request_url': url, 'final_url': url, 'method': 'GET', 'status': 200,
            'http_status': 200, 'fetched_at': '2026-11-09T09:00:00+00:00',
            'raw_path': original['path'], 'raw_sha256': original['sha256'], 'raw_storage': original['storage'],
            'text_path': projection['path'], 'text_sha256': projection['sha256'], 'text_storage': projection['storage']}


def html_apply(app=APPLY, native=NATIVE):
    return f"""<script>const configOptions = {{ job: {{
        id: '{native}',
        applyUrl: '{app}',
        canApplyOnline: 'True' === 'True'
    }} }};</script>"""


@pytest.fixture
def observed(tmp_path):
    listing = capture(tmp_path, LIST_URL, [{'id': int(NATIVE), 'link': PAGE, 'title': 'PhD Research Fellow in Statistics', 'employer': 'Test University'}])
    candidates, pages = listing_jobs(SOURCE, listing, {'url': LIST_URL, 'method': 'GET'})
    assert len(candidates) == 1 and not pages
    detail = capture(tmp_path, DETAIL, {'isPublished': True, 'positionCount': 4, 'components': [
        {'heading': 'PhD Research Fellow in Statistics', 'text': '<p>The successful candidate must be admitted to the PhD programme.</p>'}]})
    info = capture(tmp_path, INFO, {'id': int(NATIVE), 'employerName': 'Test University', 'deadlineOn': "$date('2026-12-06 00:00:00')"})
    page = capture(tmp_path, PAGE, html_apply())
    template = capture(tmp_path, TEMPLATE_URL, """<div class="partial deadline"><h3 data-jn-localize="Søknadsfrist"></h3>
        <span data-ng-bind="jobInfo.deadlineOn | jnDateToISO | date:'shortDate'"></span></div>""")
    return detail, candidates[0], {'info': info, 'page': page, 'template': template}


def test_same_day_public_evidence_passes_and_does_not_multiply_vacancies(observed, tmp_path):
    detail, candidate, support = observed
    assert candidate['url'] == DETAIL and candidate['support_requests']['page']['url'] == PAGE
    row, held = parse_detail(SOURCE, detail, candidate, DAY, tmp_path, support)
    assert held is None and row['status'] == 'open' and row['application_url'] == APPLY
    assert row['closes_on'] == '2026-12-06' and row['source_fields']['raw_position_count'] == 4
    assert row['source_fields']['position_count_counted_as_opportunities'] is False
    verify_candidate(row, DAY, {})


@pytest.mark.parametrize('key', ['detail', 'listing', 'info', 'page', 'template'])
def test_stale_capture_is_not_counted(observed, tmp_path, key):
    detail, candidate, support = observed
    target = detail if key == 'detail' else candidate['listing'] if key == 'listing' else support[key]
    target['fetched_at'] = '2026-11-08T09:00:00+00:00'
    with pytest.raises(ValueError, match='same KST verification day'):
        parse_detail(SOURCE, detail, candidate, DAY, tmp_path, support)


def test_wrong_native_or_unattested_apply_route_is_held(observed, tmp_path):
    detail, candidate, support = observed
    support['page'] = capture(tmp_path, PAGE, html_apply('https://example.org/apply/310313'))
    with pytest.raises(ValueError, match='Unattested'):
        parse_detail(SOURCE, detail, candidate, DAY, tmp_path, support)
    support['page'] = capture(tmp_path, PAGE, html_apply(APPLY, '310314'))
    with pytest.raises(ValueError, match='does not match the posting'):
        parse_detail(SOURCE, detail, candidate, DAY, tmp_path, support)


def test_ambiguous_online_apply_flags_are_held(observed, tmp_path):
    detail, candidate, support = observed
    altered = html_apply().replace("'True' === 'True'", "'True' === 'True',\n        canApplyOnline: false")
    support['page'] = capture(tmp_path, PAGE, altered)
    with pytest.raises(ValueError, match='does not match the posting'):
        parse_detail(SOURCE, detail, candidate, DAY, tmp_path, support)


def test_published_job_without_actual_deadline_binding_is_held(observed, tmp_path):
    detail, candidate, support = observed
    support['template'] = capture(tmp_path, TEMPLATE_URL, '<p>Publication end date</p>')
    with pytest.raises(ValueError, match='does not bind deadlineOn'):
        parse_detail(SOURCE, detail, candidate, DAY, tmp_path, support)


def test_reverification_rejects_forged_enrollment_projection(observed, tmp_path):
    detail, candidate, support = observed
    row, _ = parse_detail(SOURCE, detail, candidate, DAY, tmp_path, support)
    replacement = store_source_bytes(b'Invented applicant requirement\n', tmp_path, suffix='.txt')
    row.update(text_path=replacement['path'], text_sha256=replacement['sha256'], text_storage=replacement['storage'])
    with pytest.raises(ValueError, match='not derived from the original'):
        verify_candidate(row, DAY, {})


def test_listing_cannot_synthesize_pagination_or_cross_join(tmp_path):
    invalid = capture(tmp_path, LIST_URL, [{'id': int(NATIVE), 'link': PAGE.replace(NATIVE, '310314'), 'title': 'PhD Fellow'}])
    with pytest.raises(ValueError, match='native link mismatch'):
        listing_jobs(SOURCE, invalid, {'url': LIST_URL})
    with pytest.raises(ValueError, match='observed bounded Jobbnorge listing'):
        listing_jobs(SOURCE, invalid, {'url': LIST_URL + '&page=2'})
