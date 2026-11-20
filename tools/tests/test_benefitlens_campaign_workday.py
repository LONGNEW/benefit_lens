from datetime import date
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_campaign_adapters import listing_jobs, parse_workday, student_quote, workday_period
from benefitlens_source_storage import store_source_bytes


AS_OF = date(2026, 11, 10)
HOST = 'thales.wd3.myworkdayjobs.com'
ENDPOINT = f'https://{HOST}/wday/cxs/thales/Careers'
ITEM_PATH = '/job/Noida/Finance-Intern_R0342877-1'
APPLY_URL = f'https://{HOST}/Careers{ITEM_PATH}'


def capture(tmp_path, url, data):
    blob = store_source_bytes(json.dumps(data).encode(), tmp_path, suffix='.json')
    return {'url': url, 'request_url': url, 'final_url': url, 'status': 200,
            'fetched_at': '2026-11-10T01:00:00+00:00',
            'raw_path': blob['path'], 'raw_sha256': blob['sha256'], 'raw_storage': blob['storage']}


def workday_case(tmp_path, *, detail_native='R0342877', path=ITEM_PATH, public_url=APPLY_URL,
                 fields=None, description=None):
    item = {'title': 'Finance Intern', 'externalPath': path,
            'bulletFields': fields if fields is not None else ['Intern/Trainee (Fixed Term) (Trainee)', 'R0342877']}
    listing = capture(tmp_path, ENDPOINT+'/jobs', {'jobPostings': [item], 'total': 1})
    source = {'adapter': 'workday', 'endpoint': ENDPOINT, 'issuer': 'Thales', 'tenant': 'thales'}
    candidates, _ = listing_jobs(source, listing, {'body': {'offset': 0, 'limit': 20}})
    data = {'jobPostingInfo': {'jobReqId': detail_native, 'title': item['title'], 'externalUrl': public_url,
                              'canApply': True, 'posted': True, 'endDate': '2027-01-10',
                              'jobDescription': description if description is not None else (
                                  '<p>What we need to see:</p>'
                                  '<p>Must be actively enrolled in a university pursuing a B.S. degree.</p>'
                                  '<p>Application Deadline:</p><p>January 10, 2027</p>')}}
    detail = capture(tmp_path, ENDPOINT+ITEM_PATH, data)
    return detail, candidates[0]


def test_non_identity_bullet_field_does_not_mask_real_requisition(tmp_path):
    detail, candidate = workday_case(tmp_path)
    assert candidate['native_id'] == 'R0342877'
    # Existing queued rows have the old misleading hint. Recheck the source bytes.
    candidate['native_id'] = 'Intern/Trainee (Fixed Term) (Trainee)'
    row = parse_workday(detail, candidate, AS_OF, tmp_path)
    assert row['native_id'] == 'R0342877'
    assert row['closes_on'] == '2027-01-10'
    assert row['student_quote'] == 'Must be actively enrolled in a university pursuing a B.S. degree.'


@pytest.mark.parametrize('kwargs,reason', [
    ({'detail_native': 'R034287'}, 'identity mismatch'),
    ({'fields': ['R0342877-1', 'R0342877']}, 'identity mismatch'),
    ({'public_url': f'https://{HOST}/Careers/job/Elsewhere/Finance-Intern_R0342877-1'}, 'listed public tenant detail'),
])
def test_inconsistent_workday_detail_cannot_be_promoted(tmp_path, kwargs, reason):
    detail, candidate = workday_case(tmp_path, **kwargs)
    with pytest.raises(ValueError, match=reason):
        parse_workday(detail, candidate, AS_OF, tmp_path)


def test_requisition_in_a_different_list_item_is_not_enough(tmp_path):
    detail, candidate = workday_case(tmp_path, path='/job/Elsewhere/Finance-Intern_R0342877-1')
    candidate['url'] = ENDPOINT+ITEM_PATH
    with pytest.raises(ValueError, match='Exact Workday detail path absent'):
        parse_workday(detail, candidate, AS_OF, tmp_path)


def test_exact_candidate_url_is_bound_to_the_capture(tmp_path):
    detail, candidate = workday_case(tmp_path)
    detail['request_url'] = ENDPOINT+'/job/Noida/Other-Intern_R0342877-1'
    with pytest.raises(ValueError, match='does not match the listed detail URL'):
        parse_workday(detail, candidate, AS_OF, tmp_path)


def test_regional_notice_cannot_attest_a_general_rolling_application_window():
    lines = ['State-Specific Notices:', 'Colorado:', 'Application Window Notice',
             'Applications will be accepted on an ongoing basis.']
    with pytest.raises(ValueError, match='region-specific notice'):
        workday_period(lines, AS_OF)
    _, closing, _ = workday_period(['Application Deadline:', 'January 10, 2027', *lines], AS_OF)
    assert closing == date(2027, 1, 10)


@pytest.mark.parametrize('heading,line', [
    ('What You Need to Succeed', 'Currently pursuing a Bachelor’s degree in computer science.'),
    ('Program Eligibility Requirements', 'Expected continued enrollment in an accredited college or university following the internship program.'),
    ('MINIMUM REQUIREMENTS', 'Education: Current UVA undergraduate student.'),
    ('Other qualifying skills:', 'Indigenous Undergraduate student currently enrolled at McGill in Engineering.'),
])
def test_observed_mandatory_qualification_clauses(heading, line):
    assert student_quote([heading, line]) == line


@pytest.mark.parametrize('lines', [
    ['Job responsibilities:', 'Currently enrolled students at the university are your customers.'],
    ['Required qualifications:', 'Customers must be currently enrolled in a college.'],
    ['Required qualifications:', 'Currently enrolled students at the university are your customers.'],
    ['Preferred Qualifications:', 'Currently pursuing a Bachelor’s degree.'],
    ['Minimum Requirements:', 'You are currently enrolled in the company health plan.'],
    ['What You Need to Succeed', 'Currently enrolled in a degree program or', 'equivalent experience is required.'],
    ['Required Qualifications:', 'Currently pursuing or have completed a Bachelor’s degree.'],
    ['Minimum Requirements:', 'Demonstrated communication skills.', 'Responsibilities:', 'Currently enrolled in a degree program.'],
    ['Minimum Requirements:', 'Pursuing business with a university is part of your role.'],
])
def test_student_mentions_and_nonstudent_alternatives_are_held(lines):
    with pytest.raises(ValueError, match='enrollment clause'):
        student_quote(lines)


def test_new_enrollment_clause_does_not_turn_backend_date_into_deadline(tmp_path):
    detail, candidate = workday_case(tmp_path, description=(
        '<p>What You Need to Succeed:</p><p>Currently enrolled in a bachelor’s degree.</p>'))
    with pytest.raises(ValueError, match='No explicit body application deadline'):
        parse_workday(detail, candidate, AS_OF, tmp_path)
