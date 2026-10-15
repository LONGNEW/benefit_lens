"""Typed source fields. A catalog code never substitutes for NOFO eligibility."""
from __future__ import annotations

import html
import re

# https://grants.gov/help/xml-extract/ : Eligible Applicants
GRANTS_APPLICANTS = {
    '00': '주 정부', '01': '카운티 정부', '02': '시 또는 타운 정부',
    '04': '특별구 정부', '05': '독립 교육구', '06': '공립 대학',
    '07': '연방 인정 원주민 부족 정부', '08': '공공 주택 기관',
    '11': '기타 원주민 부족 단체', '12': '501(c)(3) 비영리 단체, 대학 제외',
    '13': '기타 비영리 단체, 대학 제외', '20': '사립 대학',
    '21': '개인', '22': '중소기업 외 영리 조직', '23': '중소기업',
    '25': '기타, 상세 자격에 따라 결정', '99': '신청 주체 제한 없음, 상세 자격 적용',
}


def applicant_codes(value):
    values = value if isinstance(value, list) else [value] if value is not None else []
    return list(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))


def grant_recipient(codes):
    if '21' in codes:
        return 'individual' if len(codes) == 1 else 'individual_or_organization'
    if not codes or {'25', '99'} & set(codes) or any(c not in GRANTS_APPLICANTS for c in codes):
        return 'unknown'
    return 'organization'


def grant_applicant_check(codes):
    return {
        'check_id': 'applicant-type', 'field': 'applicant.entity_type',
        'question': '신청 주체는 어떤 유형인가요?', 'answer_type': 'select',
        'options': [{'value': code, 'label': label} for code, label in GRANTS_APPLICANTS.items()],
        'accepted_when': {'op': 'in', 'value': [c for c in codes if c not in {'25', '99'}]},
        # 25 and 99 do not establish that every individual is eligible.
        'unmatched_result': 'unknown' if grant_recipient(codes) == 'unknown' else 'fail',
        'at': 'application', 'evidence_ref': 'native',
        'source_value': codes, 'scope_label': '공식 신청 주체 코드만 비교',
    }


def title_category(title, fallback='other'):
    text = html.unescape(title).casefold()
    for category, pattern in [
        ('fellowship', r'fellowship|펠로[우]?십|펠로쉽'),
        ('internship', r'internship|\bintern\b|인턴'),
        ('scholarship', r'scholarship|bursary|장학'),
        ('travel_grant', r'travel grant|학회.*지원|학술.*여비'),
        ('competition', r'경진대회|공모전|competition'),
        ('training', r'직업훈련|교육훈련|연수 지원'),
    ]:
        if re.search(pattern, text):
            return category
    return fallback


def degree_check(degrees):
    return {
        'check_id': 'degree-category', 'field': 'education.degree',
        'question': '현재 학위 과정은 무엇인가요?', 'answer_type': 'select',
        'options': [{'value': value, 'label': label} for value, label in [
            ('bachelors', '학부'), ('masters', '석사'), ('phd', '박사'), ('other', '그 외')]],
        'accepted_when': {'op': 'in', 'value': degrees},
        'at': 'application', 'evidence_ref': 'native',
        'scope_label': '학위 구분만 비교, 학기와 세부 조건은 별도',
    }
