from data_platform.ingest_originals import opportunity_identity, record_from_document, sections

CONFIG={"providers":[{"host":"issuer.test","name":"Issuer","kind":"company"},{"host":"postech.ac.kr","name":"POSTECH","kind":"university"}]}

def doc(url="https://issuer.test/fellowship/2026"):
    return {"url":url,"final_url":url,"title":"Issuer PhD Fellowship 2026","status":"collected","capture_mode":"internal_research","kind":"html","role":"notice_or_policy","sha256":"a"*64,"pg_raw_document_id":7}

TEXT="""Issuer PhD Fellowship 2026
Applicant qualification
Applicant must be a full-time PhD student.
Scholarship reward
Cash award of USD 5000.
Application submission
Submit a research proposal and signed rules.
Submission matters
Submission due date done
"""

def test_only_actual_primary_originals_can_become_candidate_records():
    primary=record_from_document(doc(),TEXT,CONFIG,[])
    assert primary and primary.lifecycle_status=="closed"
    assert primary.official_url=="https://issuer.test/fellowship/2026"
    assert "Applicant must be a full-time PhD student." in primary.eligibility_rules
    assert primary.evidence_sources[0]["sha256"]=="a"*64
    assert record_from_document(doc("https://relay.test/notice"),TEXT,CONFIG,[]) is None

def test_metadata_titles_and_profiles_never_promote_to_opportunities():
    assert record_from_document(doc(),"Search snippet only",CONFIG,[]) is None
    profile={**doc(),"role":"experience_reference","capture_mode":"metadata_only"}
    assert record_from_document(profile,TEXT,CONFIG,[]) is None

def test_postech_relays_do_not_misidentify_external_provider_as_school():
    relay={**doc("https://postech.ac.kr/notice/7"),"title":"Issuer PhD Fellowship 안내"}
    assert record_from_document(relay,TEXT,CONFIG,[]) is None

def test_asset_evidence_retains_exact_original_source_and_hash():
    asset={"final_url":"https://issuer.test/rules.pdf","kind":"pdf","title":"Rules","text":"Required documents\nResearch proposal required.","sha256":"b"*64,"pg_raw_document_id":8}
    record=record_from_document(doc(),TEXT,CONFIG,[asset])
    assert record.evidence_sources[1]["url"]==asset["final_url"]
    assert record.evidence_sources[1]["sha256"]==asset["sha256"]

def test_requirements_are_source_sections_not_new_personal_eligibility_facts():
    result=sections(TEXT)
    assert any("proposal" in line for line in result["requirements"])
    assert not any("proposal" in line for line in result["eligibility"])


def test_attachment_can_supply_conditions_missing_from_short_notice():
    notice={**doc(),"role":"listing_or_reference"}
    body="Issuer PhD Fellowship 2026\nOfficial announcement and attached program rules. "*3
    asset={"final_url":"https://issuer.test/rules.pdf","kind":"pdf","title":"Program rules","text":TEXT,"sha256":"b"*64,"pg_raw_document_id":8}
    record=record_from_document(notice,body,CONFIG,[asset])
    assert record and record.eligibility_rules
    assert "5000" in record.benefit_description


def test_incidental_scholarship_chapter_and_faq_are_reference_only():
    handbook={**doc(),"kind":"pdf"}
    assert record_from_document(handbook,"Admission handbook\n"+"Admission information "*100+TEXT,CONFIG,[]) is None
    faq={**doc(),"final_url":"https://issuer.test/fellowship-faqs.pdf","kind":"pdf"}
    assert record_from_document(faq,TEXT,CONFIG,[]) is None


def test_program_navigation_aliases_preserve_program_and_round_identity():
    assert opportunity_identity("https://www.kosaf.go.kr/ko/scholar.do?pg=scholarship05_15_01&ttab1=1&naviParam=nav")==opportunity_identity("https://www.kosaf.go.kr/ko/scholar.do?pg=scholarship05_15_01&ttab1=2")
    assert opportunity_identity("https://www.kosaf.go.kr/ko/notice.do?mode=view&seqNo=20888&page=1")!=opportunity_identity("https://www.kosaf.go.kr/ko/notice.do?mode=view&seqNo=20889&page=1")
    assert opportunity_identity("https://pacmsmain02.postech.ac.kr/kor/college-life/scholarship_information.do")==opportunity_identity("https://www.postech.ac.kr/kor/college-life/scholarship_information.do")


def test_faq_question_is_not_a_nationality_requirement_and_slide_order_is_not_prose():
    text="[guide.DOC]\n가. 지원자 자격\n국내 대학원 풀타임 석박사 과정\n다. 시상 내용\n선정 논문당 400만원\nQualcomm Fellowship FAQ\n지원자격 관련\nQ : 한국 국적이 아니어도 지원할 수 있나요?\n지원 가능합니다.\n[manual.pptx]\nApplicant qualification\n한국 국적\n[rules.docx]\nEligibility\nFull-time graduate students."
    fields=sections(text)
    assert "국내 대학원 풀타임 석박사 과정" in fields["eligibility"]
    assert not any("한국 국적" in line for line in fields["eligibility"])
    assert "선정 논문당 400만원" in fields["benefits"]


def test_one_explicit_deadline_is_a_close_not_an_open():
    body="[guide.DOC]\nQualcomm Fellowship 2026\n가. 지원자 자격\n풀타임 대학원생\n다. 시상 내용\n400만원 장학금\n라. 일정\n논문 접수 마감 : 2026년 8월 15일까지\n"*2
    item={**doc(),"kind":"zip","role":"guideline_or_form"}
    record=record_from_document(item,body,CONFIG,[])
    assert record.record_kind=="notice" and record.opens_at is None
    assert record.closes_at.startswith("2026-08-15T23:59:59")
