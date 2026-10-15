from data_platform.proposal_pipeline import source_native, date_value, lifecycle, anchor_key, digest


def row(family,payload):
    return ('candidate','Sample','https://example.org/source',family,'Issuer','government','US',{'source_payload':payload})


def test_source_grants_are_not_assumed_personal_scholarships():
    item=source_native(row('grants_gov_xml',{'fields':{'OpportunityID':'123','OpportunityNumber':'ABC',
         'AwardFloor':'1000','AwardCeiling':'5000','PostDate':'01012026','CloseDate':'12312026',
         'EligibleApplicants':'25','AdditionalInformationOnEligibility':'Eligible organizations'}}))
    assert item['category']=='research_grant'
    assert item['recipient_type']=='unknown'
    assert item['eligibility'] is None
    assert item['application']['closes_on']=='2026-12-31'
    assert item['benefits'][0]['max_amount']==5000
    assert item['source_refs'][0]['locator']=='OpportunityID=123'


def test_kosaf_uses_degree_labels_and_keeps_raw_constraints():
    item=source_native(row('kosaf_catalog',{'학년구분':'대학원석사대학원박사',
          '성적기준 상세내용':'3.5 이상','지원내역 상세내용':'학기당 지원','학과구분':'공학계열'}))
    assert item['eligibility']['value']==['masters','phd']
    assert any(c['question']=='3.5 이상' for c in item['additional_checks'])
    assert item['benefits'][0]['amount'] is None
    assert item['extraction_status']=='partial'


def test_missing_conditions_are_not_empty_pass_trees():
    item=source_native(row('kosaf_catalog',{}))
    assert item['eligibility'] is None


def test_education_loans_are_not_relabelled_as_scholarships():
    item=source_native(row('kosaf_catalog',{'상품구분':'학자금','상품명':'학자금대출'}))
    assert item['category']=='other'
    assert item['benefits'][0]['type']=='education_finance'


def test_mixed_degree_labels_do_not_exclude_graduate_students():
    item=source_native(row('kosaf_catalog',{'학년구분':'대학2학기박사과정석사2학기이상'}))
    assert set(item['eligibility']['value'])=={'bachelors','masters','phd'}
    unrestricted=source_native(row('kosaf_catalog',{'학년구분':'대학2학기제한없음'}))
    assert unrestricted['eligibility'] is None


def test_youth_rolling_catalog_notices_may_have_track_specific_dates():
    item=source_native(row('youthcenter_policies',{'APLY_PRD_SE_CD':'상시','APLY_PRD_END_YMD':'0',
                           'ADD_APLY_QLFC_CND_CN':'세부 트랙별 상이'}))
    assert item['application']['closes_on'] is None
    assert not item['application'].get('rolling')
    assert item['additional_checks']


def test_unstructured_sources_remain_pending():
    assert source_native(row('opportunitydesk',{'text':'scholarship'})) is None


def test_source_quote_whitespace_normalization():
    assert anchor_key('4.0(4.5 만점기준 )/3.8(4.3 만점기준 )')==anchor_key('4.0(4.5 만점기준)/3.8(4.3 만점기준)')


def test_dates_and_lifecycle():
    assert date_value('invalid') is None
    assert date_value('0','%Y%m%d') is None
    assert lifecycle({'opens_on':'2026-09-30','closes_on':'2026-10-30'},'2026-10-15')=='open'
    assert lifecycle({'closes_on':'2026-04-30'},'2026-10-15')=='closed'
    assert lifecycle({},'2026-10-15')=='unknown'


def test_hash_is_order_independent():
    assert digest({'a':1,'b':2})==digest({'b':2,'a':1})
