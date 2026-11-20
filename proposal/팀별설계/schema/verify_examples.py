"""Run focused checks on saved sources and existing pure functions. No DB or network."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess

from data_platform.contract import parse_amount

HERE = Path(__file__).resolve().parent
REPO = Path('/home/longnew/Desktop/course_work/26-2 학기/데이터엔지니어링/benefit_lens')
ROOT = Path('/home/longnew/Desktop/course_work')
CORPUS = ROOT / 'tmp/benefitlens_auto_dictionary_20261018/run-v3'
ARCHIVE = REPO.parent / 'benefitlens/pipeline/data/proposal_demo/2026-10-15'
SAMPLE = REPO / 'data/sample/programs.json'
INGESTION = REPO / 'web/public/original-ingestion.json'

rows = json.loads(SAMPLE.read_text())['opportunities']
google = next(r for r in rows if r['opportunity_id'] == 'google-east-asia-2026')
mobis = next(r for r in rows if '모비스' in r['title'])
nvidia = next(r for r in rows if r['opportunity_id'] == 'nvidia-2027-2028')
ikef = next(r for r in rows if r['program_id'] == 'ikef-domestic-undergraduate')
anchor = lambda row, key: next(x['anchor'] for x in row['source_refs'] if x['id'] == key)


def archived_quote(row, evidence_id, first, last, expected):
    source = next(x for x in row['source_refs'] if x['id'] == evidence_id)
    raw = ARCHIVE / 'raw' / (source['content_sha256'] + '.html')
    text = ARCHIVE / 'text' / (source['source_id'] + '.txt')
    assert sha256(raw.read_bytes()).hexdigest() == source['content_sha256']
    span = '\n'.join(text.read_text().splitlines()[first - 1:last])
    assert expected in span
    return {'text_path': str(text), 'lines': [first, last], 'raw_sha256_matched': True,
            'text_sha256': sha256(text.read_bytes()).hexdigest(), 'quote': span}

out = {'scope': 'existing_pure_functions_and_local_saved_evidence', 'observations': {}}
o = out['observations']

# Source label of the monthly example: a saved POSTECH announcement link, not a new round.
pay_sentence = '박사과정생 월 200만 원, 석사과정생 월 150만 원이 지급되며, 최대 4학기까지 수혜가 가능하다'
duration_span = '석사과정생 월 150만 원이 지급되며, 최대 4학기까지 수혜가 가능하다'
assert pay_sentence in INGESTION.read_text().splitlines()[156032]
flat_monthly = parse_amount('매월 50만원 지원')
slash_monthly = parse_amount('500,000원/월 지원')
assert flat_monthly == slash_monthly and flat_monthly['recurrence'] == 'month'
o['format_alias_same_unit'] = {'inputs': ['매월 50만원 지원', '500,000원/월 지원'], 'output': flat_monthly, 'basis': 'controlled_format_pair'}

term_anchor = anchor(ikef, 'I6')
term = next(x for x in ikef['benefits'] if x['evidence_ref'] == 'I6')
assert term_anchor == '600 만원' and term['period'] == 'semester'
assert parse_amount(term_anchor)['recurrence'] is None
o['period_not_in_short_anchor'] = {'input': term_anchor, 'function_output': parse_amount(term_anchor), 'saved_record': {k: term[k] for k in ['amount', 'currency', 'period']}, 'source': str(SAMPLE) + ':503'}
o['period_not_in_short_anchor']['archived_evidence'] = archived_quote(ikef, 'I6', 105, 113, '학기당 금액')

compound = parse_amount(pay_sentence)
phd_monthly = parse_amount('박사과정생 월 200만 원')
master_monthly = parse_amount('석사과정생 월 150만 원')
duration_joined = parse_amount(duration_span)
assert compound['amount_max'] is None
assert phd_monthly['amount_max'] == 2000000 and master_monthly['amount_max'] == 1500000
assert phd_monthly['recurrence'] == master_monthly['recurrence'] == 'month'
assert duration_joined['amount_qualifier'] == 'up_to' and master_monthly['amount_qualifier'] == 'fixed'
o['multiple_scopes_and_duration_bug'] = {'input': pay_sentence, 'compound_output': compound, 'phd': phd_monthly, 'masters': master_monthly, 'duration_input': duration_span, 'duration_output': duration_joined, 'expected_semantics': 'fixed monthly award, duration ceiling of 4 semesters', 'finding': 'qualifier scope defect reproduced; production impact not checked', 'source': str(INGESTION) + ':156033'}

js = """
import fs from 'node:fs';
import {evaluateEligibility,evaluateAdditional} from './web/pipeline-demo/match.mjs';
const rows=JSON.parse(fs.readFileSync('data/sample/programs.json')).opportunities;
const google=rows.find(x=>x.opportunity_id==='google-east-asia-2026');
const mobis=rows.find(x=>x.title.includes('모비스'));
const profile={'education.degree':'phd','education.coursework_completed':false};
const check=google.additional_checks.find(x=>x.check_id==='coursework');
console.log(JSON.stringify({google:evaluateAdditional([check],{},profile)[0].result,mobis:evaluateEligibility(mobis.eligibility,profile).result,profile}));
"""
fit = json.loads(subprocess.run(['node','--input-type=module','-'], cwd=REPO, input=js, text=True, check=True, capture_output=True).stdout)
assert anchor(google, 'G3') == 'completed most of their graduate coursework'
assert anchor(mobis, 'official') == '② 박사 : Coursework 수료자'
assert fit['google'] == 'unknown' and fit['mobis'] is False
o['coursework_non_equivalence'] = {**fit, 'google_anchor': anchor(google, 'G3'), 'mobis_anchor': anchor(mobis, 'official'), 'scope': 'coded subset, not a full eligibility verdict', 'source': str(SAMPLE)}
o['coursework_non_equivalence']['archived_google_evidence'] = archived_quote(google, 'G3', 159, 159, 'by the academic award year when the Fellowship begins')

assert nvidia['category'] == 'fellowship' and anchor(nvidia, 'N7') == 'summer internship'
activity = next(x['description'] for x in nvidia['requirements'] if x['evidence_ref'] == 'N7')
assert '대면 여름 인턴십을 수행해야' in activity
o['opportunity_type_not_activity'] = {'category': nvidia['category'], 'anchor': anchor(nvidia, 'N7'), 'activity': activity, 'source': str(SAMPLE) + ':728'}
o['opportunity_type_not_activity']['archived_evidence'] = archived_quote(nvidia, 'N7', 34, 34, 'required in-person summer internship before the award year')

batch = json.loads((HERE / 'batch_example.proposed.json').read_text())
e = batch['request']['entries'][0]
raw = CORPUS / 'raw' / (e['source']['content_sha256'] + '.html')
txt = CORPUS / 'text' / (e['source']['content_sha256'] + '.txt')
assert sha256(raw.read_bytes()).hexdigest() == e['source']['content_sha256']
assert sha256(txt.read_bytes()).hexdigest() == e['source']['text_sha256']
lines = txt.read_text().splitlines()
line = e['evidence']['locator']['line']
assert e['evidence']['exact_quote'] in lines[line - 1]
assert e['evidence']['context_line_before'] == lines[line - 2]
assert batch['acknowledgment']['receipt_state'] == 'received'
decision = batch['decisions'][0]
assert decision['candidate_id'] == e['candidate_id'] and decision['approved_notice_version_id'] is None
o['guide_quote_and_batch_shape'] = {'line': lines[line - 1], 'line_number': line, 'path': str(txt), 'hashes_matched': True, 'assertion_checked_by_person': 'term present, no direct payment provision in this span', 'proposed_reply': 'received receipt and rejected benefit mapping'}

out.update(verification_kind='6 focused comparisons, including 1 known defect reproduction', full_extraction_accuracy_measured=False, runtime_code_changed=False, database_writes=0, network_requests=0)
(HERE / 'checks.json').write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'comparisons': len(o), 'reproduced_scope_defects': 1, 'database_writes': 0}, ensure_ascii=False))
