"""Small offline contract fixtures; not live MCF recruitment claims."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benefitlens_source_storage import store_source_bytes,read_source_bytes
from benefitlens_mcf_period import verify_mcf_deadline,mcf_apply_url,student_eligibility_quote
ASOF=date(2026,11,9);NATIVE='1234567890abcdef1234567890abcdef'
UI=' '.join(['expiryDate:r.metadata.expiryDate','job-details-info-job-expiry-date','Closing on','Closed on','job-details-apply-button','concat(t,"/apply")','isClosed:r.status&&!(r.status.id===u.Pm.OPEN||r.status.id===u.Pm.RE_OPEN)','formatJobUrl({jobTitle:n.title,company:n.hiringEmployerName||n.companyName,uuid:n.uuid})','Log in to Apply'])
FORMATTER=r't.CUSTOM_WORD_LIST=["pte","ltd"];t.STOP_WORDS=o(o([],t.CUSTOM_WORD_LIST,!0),["the","a"],!1);t.formatJobUrl=function(e);t.processStringToUrlFormat=function(e);return[t.removeWordsInBracket,t.removeStopWords,t.removePunctuations,t.removeExcessWhitespaces,t.joinWords,t.removeRepeatedHyphens,encodeURIComponent];e.replace(/\(.*?\)/gi,"")'


def capture(tmp_path,label,raw,url,text=None):
    original=store_source_bytes(raw,tmp_path,suffix='.json' if label=='record' else '.js')
    plain=store_source_bytes((text or raw.decode()).encode(),tmp_path,suffix='.txt')
    return {'url':url,'request_url':url,'fetched_at':'2026-11-09T10:00:00+00:00','raw_path':original['path'],'raw_sha256':original['sha256'],'raw_storage':original['storage'],'text_path':plain['path'],'text_sha256':plain['sha256'],'text_storage':plain['storage']}


def fixture(tmp_path,*,status=102,closing='2026-11-30',description=None,direct=False):
    student='Applicants must be currently enrolled in a university degree.'
    record={'uuid':NATIVE,'title':'The Research Intern (Summer)','status':{'id':status,'jobStatus':'Open' if status==102 else 'Closed'},'postedCompany':{'name':'The Sample Pte Ltd'},'hiringCompany':None,'description':'<p>'+student+'</p>' if description is None else description,'otherRequirements':None,'numberOfVacancies':5,'metadata':{'expiryDate':closing,'newPostingDate':'2026-11-01','jobDetailsUrl':'https://www.mycareersfuture.gov.sg/job/research-intern-sample-'+NATIVE}}
    raw=json.dumps(record if direct else {'results':[record]}).encode();text='\n'.join([NATIVE,record['status']['jobStatus'],closing,student])+'\n';api='https://api.mycareersfuture.gov.sg/v2/jobs'+('/'+NATIVE if direct else '?search=intern')
    source=capture(tmp_path,'record',raw,api,text)
    sources=[capture(tmp_path,'ui',UI.encode(),'https://www.mycareersfuture.gov.sg/app-fixture.js'),capture(tmp_path,'constants',b'OPEN:102,RE_OPEN:103,CLOSED:9,DRAFT:83','https://www.mycareersfuture.gov.sg/app-status.js'),capture(tmp_path,'formatter',FORMATTER.encode(),'https://www.mycareersfuture.gov.sg/npm.mcf-fixture.js')]
    pointer='' if direct else '/results/0';row={**source,'source_family':'Singapore/MyCareersFuture','native_id':NATIVE,'record_pointer':pointer,'issuer':'The Sample Pte Ltd','official_url':record['metadata']['jobDetailsUrl'],'application_url':mcf_apply_url(record,FORMATTER),'application_requires_login':True,'opens_on':'2026-11-01','closes_on':closing,'student_quote':student,'student_locator':'L4','status_quote':record['status']['jobStatus'],'status_locator':'L2'}
    period={'source':source,'semantics':'mycareersfuture_application_deadline','record_pointer':pointer,'field_pointer':'/metadata/expiryDate','quote':closing,'locator':'L3','closes_on':closing,'opens_on':'2026-11-01','field_semantics_sources':sources}
    return row,period,raw,record


def test_exact_compressed_bulk_record_status_period_and_apply(tmp_path):
    row,period,raw,record=fixture(tmp_path)
    assert row['raw_path'].endswith('.gz')
    assert verify_mcf_deadline(row,period,raw,ASOF,{})==record
    assert mcf_apply_url(record,FORMATTER)=='https://www.mycareersfuture.gov.sg/job/research-intern-sample-'+NATIVE+'/apply'


def test_direct_api_record_contract(tmp_path):
    row,period,raw,record=fixture(tmp_path,direct=True)
    assert verify_mcf_deadline(row,period,raw,ASOF,{})==record


def test_native_uuid_cannot_borrow_another_record(tmp_path):
    row,period,raw,_=fixture(tmp_path);row['native_id']='a'*32
    with pytest.raises(ValueError,match='native public'):verify_mcf_deadline(row,period,raw,ASOF,{})


def test_pointer_mismatch_is_rejected(tmp_path):
    row,period,raw,_=fixture(tmp_path);row['record_pointer']='/results/1'
    with pytest.raises(ValueError,match='pointers'):verify_mcf_deadline(row,period,raw,ASOF,{})


def test_closed_record_is_not_open_because_row_says_so(tmp_path):
    row,period,raw,_=fixture(tmp_path,status=9)
    with pytest.raises(ValueError,match='open/re-open'):verify_mcf_deadline(row,period,raw,ASOF,{})


def test_same_day_without_end_instant_is_held(tmp_path):
    row,period,raw,_=fixture(tmp_path,closing='2026-11-09')
    with pytest.raises(ValueError,match='same-day'):verify_mcf_deadline(row,period,raw,ASOF,{})


def test_portal_closing_cannot_be_changed_in_normalization(tmp_path):
    row,period,raw,_=fixture(tmp_path);row['closes_on']='2026-12-30';period['closes_on']='2026-12-30'
    with pytest.raises(ValueError,match='mismatched'):verify_mcf_deadline(row,period,raw,ASOF,{})


def test_missing_official_ui_definition_is_not_expiry_evidence(tmp_path):
    row,period,raw,_=fixture(tmp_path);period['field_semantics_sources']=period['field_semantics_sources'][:1]
    with pytest.raises(ValueError,match='source captures'):verify_mcf_deadline(row,period,raw,ASOF,{})


def test_wrong_actual_application_route_is_rejected(tmp_path):
    row,period,raw,_=fixture(tmp_path);row['application_url']='https://example.org/apply'
    with pytest.raises(ValueError,match='Apply button'):verify_mcf_deadline(row,period,raw,ASOF,{})


def test_body_student_service_is_not_applicant_student_scope(tmp_path):
    row,period,raw,record=fixture(tmp_path,description='<p>Assist with student records and track students academic progress.</p>')
    assert student_eligibility_quote(record) is None
    with pytest.raises(ValueError,match='student evidence'):verify_mcf_deadline(row,period,raw,ASOF,{})


def test_external_plain_text_application_instruction_is_preserved(tmp_path):
    description='<p>Applicants must be currently enrolled in a university degree.</p><p>Please apply through our career portal: https://jobs.example.org/job/123</p>'
    row,period,raw,record=fixture(tmp_path,description=description)
    with pytest.raises(ValueError,match='external application'):verify_mcf_deadline(row,period,raw,ASOF,{})
    row['application_url']='https://jobs.example.org/job/123';row['application_requires_login']=None
    assert verify_mcf_deadline(row,period,raw,ASOF,{})==record


def test_future_header_does_not_override_past_body_application_date(tmp_path):
    description='<p>Applicants must be currently enrolled in a university degree.</p><p>Application deadline 1 October 2026</p>'
    row,period,raw,_=fixture(tmp_path,description=description)
    with pytest.raises(ValueError,match='conflicts'):verify_mcf_deadline(row,period,raw,ASOF,{})


def test_decoded_original_hash_is_verified(tmp_path):
    row,period,raw,_=fixture(tmp_path);period['source']['raw_sha256']='0'*64
    with pytest.raises(ValueError,match='Hash mismatch'):verify_mcf_deadline(row,period,raw,ASOF,{})


def test_expiry_field_pointer_is_mandatory(tmp_path):
    row,period,raw,_=fixture(tmp_path);period['field_pointer']='/metadata/createdAt'
    with pytest.raises(ValueError,match='field pointer'):verify_mcf_deadline(row,period,raw,ASOF,{})


def test_actual_beneficiary_counterexample_bc27_is_not_applicant_scope(tmp_path):
    # This clause is from the public bc27d704... body; it describes programme beneficiaries.
    description='<p>We design and run a variety of enrichment and CCA programmes for primary, secondary and tertiary education students, focusing on areas such as:</p>'
    row,period,raw,record=fixture(tmp_path,description=description)
    assert student_eligibility_quote(record) is None
    with pytest.raises(ValueError,match='student evidence'):verify_mcf_deadline(row,period,raw,ASOF,{})


def test_customer_quote_cannot_replace_an_available_applicant_clause(tmp_path):
    description='<p>We teach undergraduate students.</p><p>Applicants must be currently enrolled in a university degree.</p>'
    row,period,raw,record=fixture(tmp_path,description=description)
    assert student_eligibility_quote(record)=='Applicants must be currently enrolled in a university degree.'
    row['student_quote']='We teach undergraduate students.'
    # The literal also fails because this new quote was never the LF-bound applicant evidence.
    with pytest.raises(ValueError):verify_mcf_deadline(row,period,raw,ASOF,{})


def test_previous_sentence_support_is_not_a_student_beneficiary(tmp_path):
    # Actual c704daa9 pattern: support targets the team, the next sentence targets applicants.
    description='<p>We are seeking an intern to support our R&amp;D team. This role is ideal for a student majoring in Food Science, Chemistry, or a related field.</p>'
    _,_,_,record=fixture(tmp_path,description=description)
    assert student_eligibility_quote(record) is not None


def test_inline_html_split_does_not_turn_an_into_a_n(tmp_path):
    description='<p>Currently pursuing a<span>n undergraduate</span> Banking degree.</p>'
    row,period,raw,record=fixture(tmp_path,description=description)
    text='\n'.join([NATIVE,'Open','2026-11-30','Currently pursuing an undergraduate Banking degree.'])+'\n'
    source=capture(tmp_path,'record',raw,row['request_url'],text)
    row.update(source);period['source']=source
    row['student_quote']='Currently pursuing an undergraduate Banking degree.'
    assert verify_mcf_deadline(row,period,raw,ASOF,{})==record
