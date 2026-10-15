"""Build the public 10-program snapshot from a read-only local database."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import project


def rule(field, op, value):
    return {'field': field, 'op': op, 'value': value, 'at': 'application', 'evidence_ref': 'official'}


def spec(key, title, label, issuer, kind, eligibility, benefits, requirements=()):
    return {'key': key, 'legacy_title': title, 'title': label, 'issuer': issuer,
            'category': kind, 'eligibility': eligibility, 'benefits': benefits, 'requirements': requirements}


def extra_programs():
    nationality = rule('person.nationalities', 'in', ['KR'])
    return [
        spec('postech-ax-first-semester', 'AX 전일제 대학원생 첫 학기 재정지원',
             'POSTECH AX 전일제 대학원생 첫 학기 재정지원', 'POSTECH', 'scholarship', {'all': [
                 rule('education.institution_id', 'eq', 'postech'), rule('education.department_id', 'eq', 'ax'),
                 rule('education.degree', 'in', ['masters', 'phd']), rule('education.status', 'eq', 'enrolled'),
                 rule('education.full_time', 'eq', True), rule('education.semester', 'eq', 1)]},
             [{'type': 'tuition', 'description': '첫 학기 대학에서 공표한 기준 장학금 이상 지급'}]),
        spec('postech-k-top-tier', 'POSTECH K-탑티어 석, 박사 프로젝트',
             'POSTECH K-탑티어 석박사 프로젝트', 'POSTECH', 'fellowship', {'all': [
                 rule('education.institution_id', 'eq', 'postech'), rule('education.degree', 'in', ['phd', 'integrated']),
                 rule('education.status', 'eq', 'admitted')]},
             [{'type': 'research_fund', 'amount': 50000000, 'currency': 'KRW', 'period': 'year',
               'description': '기존 제도 안내의 연간 지원액. 박사 3년, 통합 5년이며 실제 회차 선발은 별도다.'}]),
        spec('posco-overseas-2026', '2026 포스코해외유학장학 선발공고(~4.30)',
             '포스코 해외유학장학 2026', '포스코청암재단', 'scholarship', {'all': [nationality,
                 rule('education.degree', 'eq', 'phd'), rule('education.status', 'eq', 'admitted'),
                 rule('education.country', 'not_in', ['KR']), rule('education.entry_month', 'eq', '2026-09')]},
             [{'type': 'living_expenses', 'amount': 30000, 'currency': 'USD', 'period': 'year',
               'description': '기존 공고의 연간 생활비, 최대 5년'},
              {'type': 'cash', 'amount': 1500, 'currency': 'USD', 'description': '입학축하금'}],
             ['재단 온라인 접수 페이지에서 작성하고 제출 서류를 준비한다.',
              '수혜 후 성적 유지, 학업 보고와 연장 심사가 있다.']),
        spec('posco-science-18', '제18기 포스코사이언스펠로십 선발공고(~5.31)',
             '제18기 포스코사이언스펠로십', '포스코청암재단', 'fellowship', {'all': [nationality,
                 rule('employment.status', 'eq', 'faculty'), rule('employment.institution_country', 'eq', 'KR'),
                 rule('employment.first_appointment_months', 'lte', 36)]},
             [{'type': 'research_fund', 'description': '연구 지원 프로그램. 이 예제에는 지원 금액을 전사하지 않았다.'}],
             ['임용 예정자도 별도 경로가 있다. 국내 대학 임용 관련 서류와 해당 회차 기준을 확인한다.',
              '연구계획서와 단계별 추천 및 발표 심사를 준비한다.']),
        spec('kosaf-presidential-2026', '대통령과학장학금', '대통령과학장학금 2026 안내',
             '한국장학재단', 'scholarship', {'all': [nationality,
                 rule('education.degree', 'eq', 'bachelors'), rule('education.major_group', 'in', ['science', 'engineering'])]},
             [{'type': 'tuition', 'description': '등록금과 유형별 학업장려비 및 생활비 지원. 금액은 선발 유형별로 다르다.'}],
             ['신입학 또는 국내 3학년 등 선발 유형에 맞는 성적, 추천과 학적 서류를 준비한다.',
              '이 예제의 기본 조건은 공통 조건 일부이며 유형별 성적과 제외 조건을 모두 대체하지 않는다.']),
        spec('kosaf-hope-ladder-2026-s2', '중소기업 취업연계 장학금(희망사다리Ⅰ유형)',
             '희망사다리 I유형 2026년 2학기', '한국장학재단', 'scholarship', {'all': [nationality,
                 rule('education.status', 'eq', 'enrolled'), {'any': [
                     {'all': [rule('education.degree', 'eq', 'bachelors'), rule('education.year', 'gte', 3)]},
                     {'all': [rule('education.degree', 'eq', 'associate'), rule('education.year', 'gte', 2)]}]},
                 rule('education.previous_semester_percentile', 'gte', 70)]},
             [{'type': 'tuition', 'description': '수혜 학기 등록금 전액. 중복지원 여부에 따라 조정된다.'},
              {'type': 'cash', 'amount': 2000000, 'currency': 'KRW', 'period': 'semester', 'description': '취창업지원금'}],
             ['사업 참여대학과 최소이수학점 등 남은 자격을 확인하고 온라인 사전교육을 이수한다.',
              '졸업 후 수혜 학기 수에 비례한 중소기업 취창업 의무와 기초교육 요구가 있다.']),
    ]


def main():
    import psycopg
    from psycopg.conninfo import make_conninfo
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-database', help='Optional preserved local source DB; always read-only.')
    args = parser.parse_args()
    output = project.ROOT / 'data/sample'
    output.mkdir(parents=True, exist_ok=True)
    dsn = project.environment()['DATABASE_URL']
    if args.source_database:
        dsn = make_conninfo(dsn, dbname=args.source_database)
    with psycopg.connect(dsn) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        opportunities = [row[0] for row in conn.execute("SELECT payload FROM benefitlens.opportunities WHERE extractor='assistant_source_reading_review_v1' ORDER BY opportunity_id")]
        if len(opportunities) != 4:
            raise ValueError('Expected the four existing source-reviewed examples.')
        knowledge = [row[0] for row in conn.execute('SELECT payload FROM benefitlens.program_knowledge ORDER BY program_id')]
        for item in opportunities:
            item['sample_scope'] = 'existing_source_reviewed_snapshot'
        for item in extra_programs():
            row = conn.execute('''SELECT c.official_url,c.application_url,c.opens_at,c.closes_at,v.record_kind
                 FROM benefitlens.latest_candidate_opportunity c JOIN benefitlens.opportunity_version v USING(opportunity_version_id)
                 WHERE c.title=%s''', (item['legacy_title'],)).fetchone()
            if row is None:
                raise ValueError(item['legacy_title'])
            url, application, opens, closes, record_kind = row
            opportunities.append({'opportunity_id': item['key'], 'program_id': item['key'],
                'round_id': '2026-snapshot', 'title': item['title'], 'category': item['category'],
                'recipient_type': 'individual', 'issuer': {'name': item['issuer'], 'country': 'KR',
                    'type': 'school' if item['issuer']=='POSTECH' else 'national' if item['issuer']=='한국장학재단' else 'foundation'},
                'eligibility': item['eligibility'], 'benefits': [{**b, 'evidence_ref': 'official'} for b in item['benefits']],
                'application': {'url': application if application and application.startswith('https://') else url, 'opens_on': opens.date().isoformat() if opens else None,
                                'closes_on': closes.date().isoformat() if closes else None},
                'additional_checks': [], 'requirements': [{'description': text, 'evidence_ref': 'official'} for text in item['requirements']],
                'source_refs': [{'id': 'official', 'url': url, 'locator': '기존에 수집한 공식 안내의 자격과 혜택 구역'}],
                'record_kind': record_kind, 'sample_scope': 'selected_conditions_not_complete_eligibility',
                'extraction_status': 'public_demo_subset', 'resolution_status': 'historical_snapshot',
                'explanation': '공식 원문을 연결한 공개 예제다. 저장된 공통 조건 일부를 비교하며 현재 모집 및 전체 자격 확인을 대신하지 않는다.'})
    for item in opportunities:
        item['extractor'] = 'public_demo_fixture_v1'
        item.pop('version_hash', None)
        for ref in item.get('source_refs', []):
            for key in ['document_id', 'raw_path', 'text_path']:
                ref.pop(key, None)
    for program in knowledge:
        program['extractor'] = 'public_demo_fixture_v1'
        for case in program['experience_cases']:
            case.pop('person_id', None)
            case['name'] = '공개 경험 사례 ' + case['case_id']
            for ref in case.get('source_refs', []):
                for key in ['document_id', 'raw_path', 'text_path']:
                    ref.pop(key, None)
    bundle = {'format': 'benefitlens-public-sample-v1', 'as_of': '2026-10-15',
              'rights': 'Public source URLs and project-authored summaries only; no original applications, full source documents or private profiles.',
              'opportunities': opportunities, 'program_knowledge': knowledge,
              'search_waves': [{'wave': 1, 'queries': [{'q': 'Google PhD Fellowship successful application research proposal'}],
                   'accepted_terms': [{'term': 'successful application'}, {'term': 'research proposal'}],
                   'status': 'illustrative_search_plan_not_executed_by_sample_loader'}]}
    serialized = json.dumps(bundle, ensure_ascii=False, indent=2) + '\n'
    if '/home/' in serialized or '/Users/' in serialized:
        raise ValueError('Machine-specific path remains in sample.')
    (output / 'programs.json').write_text(serialized)
    manifest = {'format': bundle['format'], 'file': 'programs.json', 'bytes': len(serialized.encode()),
                'sha256': hashlib.sha256(serialized.encode()).hexdigest(), 'opportunities': len(opportunities),
                'program_knowledge': len(knowledge), 'experience_summaries': sum(len(p['experience_cases']) for p in knowledge),
                'raw_documents': 0, 'private_profiles': 0}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
