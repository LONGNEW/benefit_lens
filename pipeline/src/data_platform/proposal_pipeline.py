"""Additive source-native normalization and reviewed LLM annotations for the demo.

Never turns missing extraction into eligibility. Original source rows and the old
registry remain untouched. Annotation bundles identify their actual extractor.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import unicodedata
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

from .db import connect, migrate
from .discovery import robots_allowed
from .http import SafeHttpClient, document_kind
from .keyword_search import safe_source_url
from .linked import extract_document
from .proposal_conditions import applicant_codes, grant_recipient, grant_applicant_check, GRANTS_APPLICANTS, title_category, degree_check

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = 'proposal-demo-1'


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def normalized(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value)).replace('\u00b7', ',')).strip()


def anchor_key(value):
    return re.sub(r'\s*([()/])\s*', r'\1', normalized(value)).casefold()


def date_value(value, fmt=None):
    if not value or str(value) in {'0', '00000000'}:
        return None
    try:
        if fmt:
            return datetime.strptime(str(value).strip(), fmt).date().isoformat()
        return datetime.fromisoformat(str(value).strip()[:10]).date().isoformat()
    except ValueError:
        return None


def lifecycle(application, today):
    end, start = application.get('closes_on'), application.get('opens_on')
    if end and end < today:
        return 'closed'
    if start and start > today:
        return 'upcoming'
    if start and end and start <= today <= end:
        return 'open'
    return 'rolling' if application.get('rolling') else 'unknown'


def source_native(row):
    key, title, url, family, issuer_name, issuer_kind, country, original = row
    p = original.get('source_payload') or {}
    reference = {'id': 'native', 'url': url, 'candidate_key': key, 'locator': 'source_payload',
                 'evidence_sha256': original.get('evidence_sha256')}
    out = {'schema_version': SCHEMA, 'opportunity_id': key,
           'program_id': 'source:' + digest([family, issuer_name, title])[:28],
           'round_id': None, 'title': title, 'category': 'other', 'recipient_type': 'unknown',
           'issuer': {'name': issuer_name, 'type': issuer_kind, 'country': country},
           'eligibility': None, 'additional_checks': [], 'benefits': [], 'requirements': [],
           'application': {'url': url}, 'source_refs': [reference],
           'explanation': '원천 필드를 옮긴 자료이며 추가 원문 조건의 해석이 필요합니다.',
           'extraction_status': 'partial', 'resolution_status': 'source_record_not_semantic_round',
           'extractor': 'source_native_fields_v1', 'source_family': family}
    if family == 'grants_gov_xml':
        f = p.get('fields', {})
        if not f.get('OpportunityID'):
            return None
        out['category'] = 'research_grant'
        codes = applicant_codes(f.get('EligibleApplicants'))
        out['recipient_type'] = grant_recipient(codes)
        out['applicant_types'] = [{'code': code, 'label': GRANTS_APPLICANTS.get(code, '미정의 코드')} for code in codes]
        out['program_id'] = 'grants-gov:' + str(f['OpportunityID'])
        out['round_id'] = str(f.get('OpportunityNumber') or f['OpportunityID'])
        out['resolution_status'] = 'official_source_id'
        out['application'].update(opens_on=date_value(f.get('PostDate'), '%m%d%Y'),
                                  closes_on=date_value(f.get('CloseDate'), '%m%d%Y'))
        floor, ceiling = f.get('AwardFloor'), f.get('AwardCeiling')
        def money(value):
            return float(value) if value and re.fullmatch(r'\d+(?:\.\d+)?', str(value)) else None
        out['benefits'] = [{'type': 'research_fund', 'min_amount': money(floor),
                            'max_amount': money(ceiling), 'currency': 'USD', 'amount_kind': 'range',
                            'evidence_ref': 'native'}]
        out['additional_checks'] = [grant_applicant_check(codes),
            {'check_id': 'native_eligibility', 'question': normalized(f.get('AdditionalInformationOnEligibility') or '상세 자격 문서의 조건 추출이 필요합니다.'),
             'answer_type': 'source_review', 'evidence_ref': 'native'}]
        reference['locator'] = 'OpportunityID=' + str(f['OpportunityID'])
        out['explanation'] = '공식 신청 주체 코드를 비교합니다. NOFO의 국적, 소속, 연구 단계 등 상세 자격은 추가 해석 전입니다.'
    elif family == 'kosaf_catalog':
        is_scholarship = p.get('상품구분') == '장학금'
        out.update(category='scholarship' if is_scholarship else 'other', recipient_type='individual')
        out['application'].update(opens_on=date_value(p.get('모집시작일')), closes_on=date_value(p.get('모집종료일')))
        out['application']['url'] = safe_source_url(p.get('홈페이지 주소', '')) or url
        out['benefits'] = [{'type': 'unspecified' if is_scholarship else 'education_finance', 'amount': None, 'currency': 'KRW', 'amount_kind': 'unspecified',
                            'description': normalized(p.get('지원내역 상세내용', '')), 'evidence_ref': 'native'}]
        for field in ['대학구분', '학과구분', '학년구분', '성적기준 상세내용', '소득기준 상세내용', '자격제한 상세내용', '특정자격 상세내용', '지역거주여부 상세내용', '추천필요여부 상세내용']:
            value = normalized(p.get(field, ''))
            if value and value != '해당없음':
                out['additional_checks'].append({'check_id': field, 'question': value, 'answer_type': 'source_review', 'evidence_ref': 'native'})
        degree_text = str(p.get('학년구분', ''))
        degrees = []
        if '석사' in degree_text:
            degrees += ['masters']
        if '박사' in degree_text:
            degrees += ['phd']
        if '대학원' in degree_text and not degrees:
            degrees += ['masters', 'phd']
        if re.search(r'대학(?:[1-9]|신입)', degree_text):
            degrees += ['bachelors']
        if degrees and '제한없음' not in degree_text:
            out['eligibility'] = {'field': 'education.degree', 'op': 'in', 'value': degrees, 'at': 'application', 'evidence_ref': 'native'}
            out['additional_checks'].insert(0, degree_check(degrees))
        if p.get('제출서류 상세내용'):
            out['requirements'] = [{'description': normalized(p['제출서류 상세내용']), 'evidence_ref': 'native'}]
        reference['locator'] = '번호=' + str(p.get('번호', '')).strip()
    elif family == 'youthcenter_policies':
        out['recipient_type'] = 'individual'
        out['category'] = title_category(title)
        out['application'].update(url=safe_source_url(p.get('APLY_URL_ADDR', '')) or url,
                                  opens_on=date_value(p.get('APLY_PRD_BGNG_YMD'), '%Y%m%d'),
                                  closes_on=date_value(p.get('APLY_PRD_END_YMD'), '%Y%m%d'))
        # A catalog's rolling label is not evidence for every track's deadline.
        out['application']['catalog_period_label'] = p.get('APLY_PRD_SE_CD')
        out['benefits'] = [{'type': 'unspecified', 'description': normalized(p.get('PLCY_SPRT_CN', '')), 'amount': None, 'evidence_ref': 'native'}]
        for field in ['ADD_APLY_QLFC_CND_CN', 'PTCP_PRP_TRGT_CN', 'QLFC_ACBG_NM', 'EMPM_STTS_NM']:
            value = normalized(p.get(field, ''))
            if value and value != '제한없음':
                out['additional_checks'].append({'check_id': field, 'question': value, 'answer_type': 'source_review', 'evidence_ref': 'native'})
        if p.get('SBMSN_DCMNT_CN'):
            out['requirements'] = [{'description': normalized(p['SBMSN_DCMNT_CN']), 'evidence_ref': 'native'}]
        reference['locator'] = 'DOCID=' + str(p.get('DOCID', ''))
    else:
        return None
    return out


UPSERT = '''INSERT INTO benefitlens.opportunities
 (opportunity_id,program_id,round_id,title,category,recipient_type,issuer,eligibility,
 additional_checks,benefits,application,requirements,source_refs,explanation,
 extraction_status,resolution_status,extractor,version_hash,payload)
 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
 ON CONFLICT(opportunity_id) DO UPDATE SET
 program_id=EXCLUDED.program_id,round_id=EXCLUDED.round_id,title=EXCLUDED.title,
 category=EXCLUDED.category,recipient_type=EXCLUDED.recipient_type,issuer=EXCLUDED.issuer,
 eligibility=EXCLUDED.eligibility,additional_checks=EXCLUDED.additional_checks,
 benefits=EXCLUDED.benefits,application=EXCLUDED.application,requirements=EXCLUDED.requirements,
 source_refs=EXCLUDED.source_refs,explanation=EXCLUDED.explanation,
 extraction_status=EXCLUDED.extraction_status,resolution_status=EXCLUDED.resolution_status,
 extractor=EXCLUDED.extractor,version_hash=EXCLUDED.version_hash,payload=EXCLUDED.payload,
 updated_at=now() WHERE opportunities.version_hash <> EXCLUDED.version_hash'''


def store_opportunities(conn, records):
    values, revisions = [], []
    for p in records:
        version = digest(p)
        values.append(tuple(p.get(k) for k in ['opportunity_id','program_id','round_id','title','category','recipient_type']) +
                      tuple(Jsonb(p.get(k)) for k in ['issuer','eligibility','additional_checks','benefits','application','requirements','source_refs']) +
                      tuple(p[k] for k in ['explanation','extraction_status','resolution_status','extractor']) +
                      (version, Jsonb(p)))
        revisions.append((p['opportunity_id'], version, Jsonb(p)))
    with conn.cursor() as cur:
        cur.executemany(UPSERT, values)
        cur.executemany('INSERT INTO benefitlens.opportunity_revision(opportunity_id,version_hash,payload) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING', revisions)


def normalize_corpus(output):
    migrate(ROOT / 'migrations/020_proposal_pipeline.sql')
    with connect() as conn:
        conn.execute('''INSERT INTO benefitlens.pipeline_work_item(candidate_key,source_family,status,reason)
         SELECT candidate_key,source_family,'pending','원문 분류와 조건 해석 대기'
         FROM benefitlens.support_discovery_record ON CONFLICT DO NOTHING''')
    counts = Counter()
    with connect() as reader, connect() as writer:
        with reader.cursor(name='proposal_source_stream') as cur:
            cur.itersize = 500
            cur.execute('''SELECT candidate_key,title,source_url,source_family,issuer_name,issuer_kind,source_country,payload
             FROM benefitlens.support_discovery_record
             WHERE source_family IN ('grants_gov_xml','kosaf_catalog','youthcenter_policies') ORDER BY candidate_key''')
            batch = []
            for row in cur:
                item = source_native(row)
                if item:
                    batch.append(item)
                    counts[row[3]] += 1
                if len(batch) == 500:
                    store_opportunities(writer, batch)
                    with writer.cursor() as wc:
                        wc.executemany("UPDATE benefitlens.pipeline_work_item SET status='source_fields_normalized',reason='원천 필드 정규화, 상세 자격 해석은 남음',updated_at=now() WHERE candidate_key=%s", [(p['opportunity_id'],) for p in batch])
                    writer.commit()
                    batch = []
            if batch:
                store_opportunities(writer, batch)
                with writer.cursor() as wc:
                    wc.executemany("UPDATE benefitlens.pipeline_work_item SET status='source_fields_normalized',reason='원천 필드 정규화, 상세 자격 해석은 남음',updated_at=now() WHERE candidate_key=%s", [(p['opportunity_id'],) for p in batch])
                writer.commit()
    report = {'source_native_normalized': dict(counts), 'total': sum(counts.values()), 'semantic_dedup_complete': False,
              'detailed_eligibility_complete': False,
              'applicant_code_reference': 'https://grants.gov/help/xml-extract/'}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'native-normalization.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


class SourceCollector:
    def __init__(self, output):
        self.output = output.resolve()
        self.robots = {}
        self.lock = threading.Lock()
        (self.output / 'raw').mkdir(parents=True, exist_ok=True)
        (self.output / 'text').mkdir(exist_ok=True)

    def fetch(self, source):
        url = source['url']
        host = urlsplit(url).hostname
        client = SafeHttpClient(allowed_hosts={host}, user_agent='BenefitLensCourseDemo/1.0',
                                timeout_seconds=20, max_body_bytes=20_000_000, allow_public_redirects=True)
        def guard(target):
            parts = urlsplit(target)
            origin = 'https://' + parts.netloc
            with self.lock:
                if origin not in self.robots:
                    try:
                        robots = client.get(origin + '/robots.txt')
                        self.robots[origin] = robots.body.decode('utf-8', errors='replace')
                    except Exception as error:
                        if 'HTTP 404' in str(error):
                            self.robots[origin] = ''
                        else:
                            raise RuntimeError('robots check failed: ' + type(error).__name__) from error
                policy = self.robots[origin]
            if not robots_allowed(policy, target, 'BenefitLensCourseDemo')[0]:
                raise RuntimeError('robots_disallowed')
        result = dict(source)
        try:
            fetched = client.get(url, request_guard=guard)
            kind = document_kind(fetched.body, fetched.content_type, fetched.final_url)
            if kind == 'html':
                soup = BeautifulSoup(fetched.body, 'html.parser')
                for node in soup.select('script,style,nav,header,footer,noscript,svg'):
                    node.decompose()
                content = soup.select_one('main,article') or soup
                text = '\n'.join(line.strip() for line in content.get_text('\n',strip=True).splitlines() if line.strip())
            elif kind in {'pdf','hwp','hwpx','image'}:
                text = extract_document(fetched.body, kind)
            else:
                raise ValueError('unsupported document kind')
            if len(text.strip()) < 60:
                raise ValueError('no usable source text')
            raw = self.output / 'raw' / (fetched.sha256 + '.' + kind)
            raw.write_bytes(fetched.body)
            path = self.output / 'text' / (source['id'] + '.txt')
            path.write_text(text)
            result.update(status='collected', final_url=fetched.final_url, kind=kind,
                          document_id=digest([url,fetched.sha256]), sha256=fetched.sha256,
                          text_sha256=digest(text.encode()), raw_path=str(raw), text_path=str(path),
                          text_characters=len(text), fetched_at=datetime.now(timezone.utc).isoformat())
        except Exception as error:
            result.update(status='failed', error=normalized(str(error))[:220])
        return result


def collect_sources(output):
    migrate(ROOT / 'migrations/020_proposal_pipeline.sql')
    sources = json.loads((ROOT / 'config/proposal_demo_sources.json').read_text())['sources']
    collector = SourceCollector(output)
    prior = {}
    if (output / 'collection.json').exists():
        for item in json.loads((output / 'collection.json').read_text())['documents']:
            if (item['status'] == 'collected' and Path(item['raw_path']).is_file()
                    and Path(item['text_path']).is_file()
                    and digest(Path(item['raw_path']).read_bytes()) == item['sha256']):
                prior[item['url']] = item
    def fetch_or_cached(source):
        previous = prior.get(source['url'])
        if previous:
            return {**previous, **source, 'reused_batch_cache': True}
        return collector.fetch(source)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(fetch_or_cached, sources))
    with connect() as conn:
        for p in results:
            if p['status'] == 'collected':
                conn.execute('''INSERT INTO benefitlens.pipeline_document
                 (document_id,canonical_url,content_sha256,text_sha256,kind,raw_path,text_path,fetched_at,metadata)
                 VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                 ON CONFLICT(canonical_url,content_sha256) DO UPDATE SET metadata=EXCLUDED.metadata''',
                 (p['document_id'],p['url'],p['sha256'],p['text_sha256'],p['kind'],p['raw_path'],p['text_path'],p['fetched_at'],Jsonb(p)))
    report = {'documents': results, 'counts': dict(Counter(p['status'] for p in results)), 'robots_checked': sorted(collector.robots)}
    (output / 'collection.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return {'counts': report['counts'], 'failures': [p for p in results if p['status'] == 'failed']}


def load_annotations(output, bundle_path):
    bundle = json.loads(bundle_path.read_text())
    collection = json.loads((output / 'collection.json').read_text())
    docs = {p['id']: p for p in collection['documents'] if p['status'] == 'collected'}
    texts = {k: Path(v['text_path']).read_text() for k,v in docs.items()}
    def resolve_refs(item):
        for ref in item['source_refs']:
            doc = docs.get(ref['source_id'])
            if not doc:
                raise ValueError('source unavailable: ' + ref['source_id'])
            if anchor_key(ref['anchor']) not in anchor_key(texts[ref['source_id']]):
                raise ValueError('source anchor missing: ' + ref['source_id'] + ': ' + ref['anchor'])
            ref.update(url=doc['final_url'], document_id=doc['document_id'], content_sha256=doc['sha256'])
        refs = {p['id'] for p in item['source_refs']}
        def walk(value):
            if isinstance(value, dict):
                if 'evidence_ref' in value and value['evidence_ref'] not in refs:
                    raise ValueError('unresolved evidence reference')
                for child in value.values():
                    walk(child)
            elif isinstance(value,list):
                for child in value:
                    walk(child)
        walk(item)
    accepted, errors = [], []
    for item in bundle['opportunities']:
        try:
            resolve_refs(item)
            item['extractor'] = bundle['extractor']
            item['schema_version'] = SCHEMA
            accepted.append(item)
        except ValueError as error:
            errors.append({'id': item['opportunity_id'], 'error': str(error)})
    knowledge = []
    for item in bundle['program_knowledge']:
        cases = []
        case_errors = []
        for case in item['experience_cases']:
            try:
                resolve_refs(case)
                cases.append(case)
            except ValueError as error:
                case_errors.append({'id': case['case_id'], 'error': str(error)})
        errors.extend(case_errors)
        if case_errors:
            # A temporary source failure must not erase the last good bundle.
            continue
        valid_ids = {p['case_id'] for p in cases}
        patterns = [p for p in item['writing_pattern'] if set(p['supporting_case_ids']) <= valid_ids]
        knowledge.append({**item, 'experience_cases': cases, 'writing_pattern': patterns, 'extractor': bundle['extractor']})
    with connect() as conn:
        store_opportunities(conn, accepted)
        for item in knowledge:
            conn.execute('''INSERT INTO benefitlens.program_knowledge
             (program_id,experience_cases,writing_pattern,version_hash,payload) VALUES(%s,%s,%s,%s,%s)
             ON CONFLICT(program_id) DO UPDATE SET experience_cases=EXCLUDED.experience_cases,
             writing_pattern=EXCLUDED.writing_pattern,version_hash=EXCLUDED.version_hash,payload=EXCLUDED.payload,
             updated_at=now() WHERE program_knowledge.version_hash <> EXCLUDED.version_hash''',
             (item['program_id'],Jsonb(item['experience_cases']),Jsonb(item['writing_pattern']),digest(item),Jsonb(item)))
    report = {'opportunities_loaded':len(accepted), 'programs': len(knowledge),
              'experience_cases':sum(len(p['experience_cases']) for p in knowledge),
              'patterns':sum(len(p['writing_pattern']) for p in knowledge), 'errors':errors,
              'extractor':bundle['extractor'], 'unattended_llm_batch':False}
    (output / 'annotation-load.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def store_search_waves(output):
    bundle = json.loads((ROOT / 'config/proposal_experience_waves.json').read_text())
    documents = {p['id']:p for p in json.loads((output/'collection.json').read_text())['documents'] if p['status']=='collected'}
    seen = set()
    for wave in bundle['waves']:
        for term in wave['accepted_terms']:
            document = documents[term['source_id']]
            text = Path(document['text_path']).read_text()
            if anchor_key(term['anchor']) not in anchor_key(text):
                raise ValueError('dictionary evidence missing: '+term['term'])
            term['normalized_key'] = anchor_key(term['term'])
            term['source_url'] = document['final_url']
            term['new_in_this_run'] = (term['program_id'],term['normalized_key']) not in seen
            seen.add((term['program_id'],term['normalized_key']))
        wave['run_id'] = bundle['run_id']
        wave['response_sha256'] = digest((output/wave['response_file']).read_bytes())
        wave['result_urls_scope'] = 'reviewed_subset_of_combined_batch_response'
    with connect() as conn:
        for wave in bundle['waves']:
            conn.execute('''INSERT INTO benefitlens.pipeline_search_wave(run_id,wave,payload) VALUES(%s,%s,%s)
             ON CONFLICT(run_id,wave) DO UPDATE SET payload=EXCLUDED.payload''', (bundle['run_id'],wave['wave'],Jsonb(wave)))
    (output/'search-waves.json').write_text(json.dumps(bundle,ensure_ascii=False,indent=2))
    return {'waves':len(bundle['waves']),'queries':sum(len(w['queries']) for w in bundle['waves']), 'saturation_confirmed':False}


def audit(output):
    with connect() as conn:
        report = {
            'source_records':conn.execute('select count(*) from benefitlens.support_discovery_record').fetchone()[0],
            'work_items':dict(conn.execute('select status,count(*) from benefitlens.pipeline_work_item group by status').fetchall()),
            'opportunities':dict(conn.execute('select extraction_status,count(*) from benefitlens.opportunities group by extraction_status').fetchall()),
            'extractors':dict(conn.execute('select extractor,count(*) from benefitlens.opportunities group by extractor').fetchall()),
            'programs_with_knowledge':conn.execute('select count(*) from benefitlens.program_knowledge').fetchone()[0],
            'experience_cases':conn.execute('select coalesce(sum(jsonb_array_length(experience_cases)),0) from benefitlens.program_knowledge').fetchone()[0],
            'revisions':conn.execute('select count(*) from benefitlens.opportunity_revision').fetchone()[0],
            'legacy_candidates':conn.execute('select count(*) from benefitlens.latest_candidate_opportunity').fetchone()[0],
        }
    output.mkdir(parents=True,exist_ok=True)
    (output/'db-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['normalize','collect','load','audit','search-waves'])
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--annotations',type=Path)
    args = p.parse_args()
    if args.action == 'normalize': result = normalize_corpus(args.output)
    elif args.action == 'collect': result = collect_sources(args.output)
    elif args.action == 'load': result = load_annotations(args.output,args.annotations)
    elif args.action == 'search-waves': result = store_search_waves(args.output)
    else: result = audit(args.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
