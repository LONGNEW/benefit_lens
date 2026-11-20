import json
from copy import deepcopy
from datetime import date
from hashlib import sha256
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_application_period import verify_application_period, extract_dates
from run_benefitlens_batch import validate_open_cohort

DAY = date(2026, 11, 9)


def record(tmp_path, quote='Application deadline: 30 November 2026', kind='fixed_deadline', closing='2026-11-30', locale=None):
    raw = tmp_path / 'original.json'
    text = tmp_path / 'full.txt'
    data = {'body': quote, 'id': 'one', 'language': {'code': locale}}
    raw.write_text(json.dumps(data)); text.write_text(quote + '\n')
    source = {'raw_path':str(raw), 'raw_sha256':sha256(raw.read_bytes()).hexdigest(),
              'text_path':str(text), 'text_sha256':sha256(text.read_bytes()).hexdigest(),
              'fetched_at':'2026-11-09T02:00:00+00:00', 'official_url':'https://example.org/one'}
    period = {'kind':kind, 'verification_state':'verified', 'source':dict(source),
              'closes_on':closing, 'quote':quote, 'locator':'L1',
              'semantics':'explicit_application_deadline' if kind=='fixed_deadline' else 'explicit_application_rolling'}
    return source | {'closes_on':closing, 'application_period':period}


def test_official_deadline_and_sql_period_evidence_are_required(tmp_path):
    row = record(tmp_path)
    verify_application_period(row, DAY, {})
    round_ = {'program_id':'p','round_id':'r','opportunity_id':'p-r','status':'open',
              'checked_on':str(DAY),'closes_on':row['closes_on'],'application_url':'https://example.org/apply'}
    tables = {'opportunity_round':[round_]}
    with pytest.raises(ValueError, match='application-period evidence'):
        validate_open_cohort(tables, {'p-r':'source_backed'}, DAY)
    tables.update({'claim':[{'program_id':'p','round_id':'r','claim_id':'c','review_state':'reviewed',
                   'field':'verified_application_period','value':{'posting':row,'application_period':row['application_period']}}],
                   'evidence':[{'evidence_id':'e','source_role':'official_notice'}],
                   'claim_evidence':[{'claim_id':'c','evidence_id':'e'}]})
    validate_open_cohort(tables, {'p-r':'source_backed'}, DAY)


@pytest.mark.parametrize('quote', ['Applications will be reviewed on an ongoing basis.',
    'This position will be posted until filled.', 'You must have ongoing studies in engineering.'])
def test_review_publication_and_study_are_not_rolling_acceptance(tmp_path, quote):
    row = record(tmp_path, quote, 'explicit_rolling', None)
    with pytest.raises(ValueError, match='rolling application acceptance'):
        verify_application_period(row, DAY, {})


@pytest.mark.parametrize('quote', ['Applications will be accepted on an ongoing basis.',
    'This role will remain open until filled.',
    'We will continue accepting applications until we receive a sufficient number or select a candidate.'])
def test_explicit_rolling_acceptance(tmp_path, quote):
    verify_application_period(record(tmp_path, quote, 'explicit_rolling', None), DAY, {})


def test_unpublished_period_is_not_secured(tmp_path):
    row = record(tmp_path)
    row.pop('application_period')
    with pytest.raises(ValueError, match='retain as candidate'):
        verify_application_period(row, DAY, {})


def test_ambiguous_date_requires_actual_source_locale(tmp_path):
    row = record(tmp_path, 'Application deadline: 01/11/2027', closing='2027-11-01', locale='en-GB')
    with pytest.raises(ValueError, match='exact source date spelling'):
        verify_application_period(row, DAY, {})
    row['application_period'].update(date_order='DMY', date_order_evidence={
        'source_pointer':'/application_period/source','json_pointer':'/language/code','value':'en-GB'})
    verify_application_period(row, DAY, {})
    row['application_period']['date_order_evidence']['value']='en-US'
    with pytest.raises(ValueError, match='explicit locale'):
        verify_application_period(row, DAY, {})


def test_today_without_exact_cutoff_is_held(tmp_path):
    row = record(tmp_path, 'Application deadline: 9 November 2026', closing='2026-11-09')
    with pytest.raises(ValueError, match='Same-day deadline'):
        verify_application_period(row, DAY, {})


def test_talent_pool_is_not_a_live_offer_even_if_applications_accepted(tmp_path):
    row = record(tmp_path, 'This role is part of our talent pipeline, which means it is not currently open. Applications will be accepted on an ongoing basis.', 'explicit_rolling', None)
    with pytest.raises(ValueError, match='Future talent pool'):
        verify_application_period(row, DAY, {})


def test_date_parser_does_not_silently_guess_between_us_and_uk():
    assert extract_dates('01/11/2027') == set()
    assert extract_dates('01/11/2027', 'DMY') == {date(2027,11,1)}
    assert extract_dates('01/11/2027', 'MDY') == {date(2027,1,11)}
    assert extract_dates('27/11/2026') == {date(2026,11,27)}


def test_publication_end_and_actual_sql_date_mismatch_are_rejected(tmp_path):
    row = record(tmp_path)
    row['application_period']['kind'] = 'publication_end'
    with pytest.raises(ValueError, match='publication-only'):
        verify_application_period(row, DAY, {})
    row['application_period']['kind'] = 'fixed_deadline'
    row['closes_on'] = '2026-12-30'
    with pytest.raises(ValueError, match='SQL closing date differs'):
        verify_application_period(row, DAY, {})


def test_same_day_past_instant_cannot_be_saved_as_open(tmp_path):
    row = record(tmp_path, 'Application deadline: 9 November 2026', closing='2026-11-09')
    row['application_period']['deadline_at'] = '2026-11-09T01:00:00+00:00'
    with pytest.raises(ValueError, match='deadline instant has passed'):
        verify_application_period(row, DAY, {})


def test_korean_application_range_inherits_only_its_explicit_year(tmp_path):
    quote = '신청기간: 2026년 10월 22일(목) 9시 ~ 11월 12일(목) 18시'
    assert extract_dates(quote) == {date(2026, 10, 22), date(2026, 11, 12)}
    verify_application_period(record(tmp_path, quote, closing='2026-11-12'), DAY, {})
    quote = '서류 제출 마감: 2027년 1월 5일(화)'
    verify_application_period(record(tmp_path, quote, closing='2027-01-05'), DAY, {})


@pytest.mark.parametrize('quote', ['11월 12일', '2026년 공고입니다. 신청기간 11월 12일',
    '2026년 10월 22일. 다음 접수는 11월 12일',
    '2026년 10월 22일 이후에는 별도 공고 ~ 11월 12일',
    '2026년 12월 22일 ~ 1월 12일', '2026년 2월 30일 ~ 3월 12일'])
def test_korean_missing_year_does_not_use_run_year_or_unrelated_context(quote):
    assert date(2026, 11, 12) not in extract_dates(quote)
    assert date(2027, 1, 12) not in extract_dates(quote)
    if '2월 30일' in quote: assert extract_dates(quote) == set()


def test_modification_date_is_parseable_but_cannot_be_an_application_period(tmp_path):
    assert extract_dates('최종 수정일: 2026.11.05') == {date(2026, 11, 5)}
    row = record(tmp_path, '최종 수정일: 2026.11.30', closing='2026-11-30')
    with pytest.raises(ValueError, match='application deadline'):
        verify_application_period(row, DAY, {})


@pytest.mark.parametrize('quote,closing', [
    ("Date limite de candidature : jusqu'au 10 décembre 2026", '2026-12-10'),
    ('Date limite de candidature : 16 novembre 2026', '2026-11-16')])
def test_french_explicit_application_deadline(tmp_path, quote, closing):
    verify_application_period(record(tmp_path, quote, closing=closing), DAY, {})


def test_french_rolling_requires_its_exact_application_field(tmp_path):
    row = record(tmp_path, 'Date limite de candidature : Offre ouverte en continu', 'explicit_rolling', None)
    verify_application_period(row, DAY, {})
    for quote in ('Offre ouverte en continu', 'Date de publication : Offre ouverte en continu',
                  'Études en continu', 'Examen des candidatures en continu'):
        with pytest.raises(ValueError, match='rolling application acceptance'):
            verify_application_period(record(tmp_path, quote, 'explicit_rolling', None), DAY, {})


def jobbnorge_record(tmp_path, *, info=None, binding=None):
    info = info or {'id':310313, 'deadlineOn':"$date('2026-12-06 00:00:00')"}
    raw, text = tmp_path/'job-info.json', tmp_path/'job-info.txt'
    raw.write_text(json.dumps(info)); text.write_text(info.get('deadlineOn', info.get('publicationEnd'))+'\n')
    source = {'raw_path':str(raw),'raw_sha256':sha256(raw.read_bytes()).hexdigest(),
              'text_path':str(text),'text_sha256':sha256(text.read_bytes()).hexdigest(),
              'fetched_at':'2026-11-09T02:00:00+00:00',
              'request_url':'https://id.jobbnorge.no/api/joblisting/job-info?jobId=310313&languageId=2'}
    html = tmp_path/'template.html'
    html.write_text(binding or '''<div class="partial deadline"><h3 data-jn-localize="Søknadsfrist"></h3>
<span data-ng-bind="jobInfo.deadlineOn | jnDateToISO | date:'d. M yyyy' | jnDateFormat"></span></div>''')
    proof = {'raw_path':str(html),'raw_sha256':sha256(html.read_bytes()).hexdigest(),
             'text_path':str(html),'text_sha256':sha256(html.read_bytes()).hexdigest(),
             'fetched_at':source['fetched_at'], 'scope':'Official application deadline field rendering',
             'url':'https://www.jobbnorge.no/Client/packages/clients/web/views/jobListing.html'}
    row = source | {'official_url':'https://www.jobbnorge.no/en/available-jobs/job/310313/phd',
                    'native_id':'310313','closes_on':'2026-12-06'}
    row['application_period'] = {'kind':'fixed_deadline','verification_state':'verified','source':source,
        'closes_on':row['closes_on'],'quote':text.read_text().strip(),'locator':'L1',
        'semantics':'jobbnorge_application_deadline','field_semantics_source':proof}
    return row


def test_registered_json_deadline_requires_actual_publisher_ui_field_binding(tmp_path):
    row = jobbnorge_record(tmp_path)
    verify_application_period(row, DAY, {})
    # A parseable date and an accepted interpretation are insufficient without
    # the publisher's UI establishing what this non-visible JSON key means.
    row['application_period'].pop('field_semantics_source')
    with pytest.raises(ValueError, match='official UI source'):
        verify_application_period(row, DAY, {})


@pytest.mark.parametrize('change', ['wrong_job','publication_field','wrong_binding','wrong_label','wrong_publisher'])
def test_json_deadline_cannot_borrow_another_job_or_a_publication_field(tmp_path, change):
    if change == 'publication_field':
        row = jobbnorge_record(tmp_path,info={'id':310313,'publicationEnd':"$date('2026-12-06 00:00:00')"})
    else:
        binding = '<div class="partial deadline"><h3 data-jn-localize="Søknadsfrist"></h3><span data-ng-bind="jobInfo.publicationEnd | jnDateToISO | date:format"></span></div>' if change=='wrong_binding' else None
        if change=='wrong_label': binding = '<div class="partial deadline"><h3 data-jn-localize="Published"></h3><span data-ng-bind="jobInfo.deadlineOn | jnDateToISO | date:format"></span></div>'
        row = jobbnorge_record(tmp_path,binding=binding)
        if change=='wrong_job': row['native_id']='310314'
        if change=='wrong_publisher': row['application_period']['source']['request_url']='https://example.org/api/joblisting/job-info?jobId=310313'
    with pytest.raises(ValueError): verify_application_period(row, DAY, {})
