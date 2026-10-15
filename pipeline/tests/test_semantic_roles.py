import json

import pytest

from data_platform.contract import CONTRACT_VERSION, condition_sections, prepare_record
from data_platform.parsers import ParsedOpportunity, _application_prompts, enrich_postech_cse_notice


def make_record(**values):
    return ParsedOpportunity(canonical_key="semantic-test", title="지원 공고", summary="",
                             official_url="https://official.test/notice", benefit_type="education_cost",
                             benefit_description="교육비 지원", **values)


@pytest.mark.parametrize("text", [
    "- 학생 본인 및 지도교수님 서명 필수",
    "신청서 및 재학증명서 제출 필수",
    "지원서 발급 및 준비 후 제출",
    "교육기간 중 출석 의무",
    "해커톤 참여 가능한 분",
    "방산 직무 관련 취업 희망자",
    "온라인 홈페이지에서 접수해야 합니다",
])
def test_procedural_obligations_do_not_become_eligibility(text):
    record = make_record(eligibility_rules=["박사과정 재학생", text], evidence={"eligibility": text})
    prepared = prepare_record(record)
    assert text not in prepared.eligibility_rules
    assert text in condition_sections(prepared)["requirements"]
    assert prepared.evidence["eligibility"] == text
    assert record.eligibility_rules == ["박사과정 재학생", text]


@pytest.mark.parametrize("text", [
    "만 75세 이상인 사람은 지원 제외",
    "부정수급 제재 이력 보유자는 지원 불가",
    "재직자는 교육 참여가 가능해도 지원 제외",
    "타 장학금 중복 수혜자는 지원 불가",
    "박사과정 재학생이며 지도교수 서명 필수",
    "만 19~34세 미취업자로 온라인 교육 참여 가능한 사람",
    "학사학위 이상이며 국민내일배움카드 발급 가능자",
    "박사과정 재학생 (우대사항: 장애인)",
    "지원자격: 박사과정 재학생이며 연구경험 보유자 우대",
    "박사과정 재학생이며 이공계열 활동내역서 제출('22년 1학년 신규선발자부터 적용)",
    "만 75세 이상 지원 제외, 이공계열 활동내역서 제출('22년 1학년 신규선발자부터 적용)",
])
def test_exclusions_and_mixed_qualification_clauses_are_not_dropped(text):
    prepared = prepare_record(make_record(eligibility_rules=[text]))
    assert text in prepared.eligibility_rules
    assert text not in prepared.presentation["requirements"]


def test_separate_source_lines_split_without_losing_original_quote():
    quote = "박사과정 재학생\n학생 본인 및 지도교수님 서명 필수"
    prepared = prepare_record(make_record(eligibility_rules=[quote], evidence={"eligibility": quote}))
    assert prepared.eligibility_rules == ["박사과정 재학생"]
    assert prepared.required_documents == ["학생 본인 및 지도교수님 서명 필수"]
    assert prepared.evidence["eligibility"] == quote


@pytest.mark.parametrize("text", [
    "ㅇ 이공계열 활동내역서 제출('22년 1학년 신규선발자부터 적용)",
    "- '22년 1학년 신규선발자부터 중간평가(2+2)시 장학생의 우수성과 및 이공계 진출 유도를 위해 이공계열 활동내역서 제출 필수(미제출 시 중간평가(2+2) 학생신청 불가함)",
])
def test_continuing_scholar_document_scope_is_not_an_entry_qualification(text):
    record = make_record(eligibility_rules=[text], evidence={"eligibility": text})
    prepared = prepare_record(record)
    assert prepared.eligibility_rules == []
    assert prepared.required_documents == [text]
    assert condition_sections(prepared)["requirements"] == [text]
    assert prepared.evidence["eligibility"] == text
    assert prepare_record(prepared) == prepared


def test_procedural_reference_and_operating_period_keep_their_original_quotes():
    reference = "* 자세한 내용은 업무처리기준을 참고하시기 바랍니다."
    period = "학기 및 방학 중 운영"
    original_quote = reference + "\n" + period
    prepared = prepare_record(make_record(eligibility_rules=[reference, period],
                                         evidence={"eligibility": original_quote}))
    assert prepared.eligibility_rules == []
    assert condition_sections(prepared)["requirements"] == [reference]
    assert condition_sections(prepared)["activityPeriod"] == [period]
    assert prepared.evidence["eligibility"] == original_quote
    assert prepare_record(prepared) == prepared


def test_preference_and_selection_are_preserved_separately():
    prepared = prepare_record(make_record(eligibility_rules=["전일제 대학원생 우대", "서류 심사 후 최종 선발"],
                                         presentation={"conditions": ["선발 방법: 서류 심사 및 면접", "활동기간: 3주"]}))
    sections = condition_sections(prepared)
    assert prepared.eligibility_rules == []
    assert sections["preferences"] == ["전일제 대학원생 우대"]
    assert "서류 심사 후 최종 선발" in sections["selection"]
    assert "선발 방법: 서류 심사 및 면접" in sections["selection"]
    assert "활동기간: 3주" in sections["activityPeriod"]


@pytest.mark.parametrize("text", [
    "제출서류: 신청서, 자기소개서 및 학업계획서",
    "자기소개서를 제출해야 하나요?",
    "FAQ: 지원동기는 어떻게 작성하나요?",
    "지원동기는 어떤 형식으로 작성해야 하나요?",
    "자기소개서 작성 유의사항",
    "지원서 및 자기소개서 양식 다운로드",
    "자기소개서.pdf",
    "학업계획서 제출 필수",
    "자기소개서 (1000자)를 제출해야 합니다",
    "자기소개서 문항",
    "AI역량검사는 무엇이며, 언제 진행이 되나요?",
    "AI 역량 검사는 어떤 방식으로 진행되나요?",
    "지원동기 면접은 어떻게 진행되나요?",
])
def test_document_lists_and_faq_are_not_writing_questions(text):
    assert _application_prompts([text]) == []


@pytest.mark.parametrize("text,limit", [
    ("자기소개와 지원동기를 500자로 작성", 500),
    ("앞으로의 학업 계획은 무엇인가요?", None),
    ("지원 동기를 설명해 주세요.", None),
    ("1. 지원 동기", None),
    ("문항 2: 연구 계획 (1000자 이내)", 1000),
    ("본인이 직면했던 도전과 경험을 서술하시오.", None),
    ("본인에게 가장 중요한 역량은 무엇인가요?", None),
    ("면접 경험에서 배운 것은 무엇인가요?", None),
    ("AI역량검사에서 본인이 보인 강점을 경험과 함께 설명해 주세요.", None),
])
def test_actual_instructions_limits_and_item_titles_are_questions(text, limit):
    prompts = _application_prompts([text])
    assert len(prompts) == 1
    assert prompts[0]["prompt_text"] == text
    assert prompts[0]["character_limit"] == limit


def test_legacy_false_prompt_is_quarantined_with_review_and_source_metadata():
    bad = {"prompt_key": "old-1", "prompt_text": "학업계획서 제출 필수", "source_url": "https://official.test/form.pdf",
           "review_status": "pending", "document_version": "2026-2"}
    good = {"prompt_key": "actual-1", "prompt_text": "지원 동기를 설명해 주세요.", "source_url": "https://official.test/form.pdf",
            "review_status": "pending", "document_version": "2026-2"}
    record = make_record(application_prompts=[bad, good], evidence_sources=[{"url": "https://official.test/form.pdf", "status": "collected"}],
                         extraction_candidates=[{"review_status": "pending", "version": "2026-2"}])
    prepared = prepare_record(record)
    assert prepared.application_prompts == [good]
    assert json.loads(prepared.evidence["rejected_application_prompts"]) == [bad]
    assert bad["prompt_text"] in prepared.required_documents
    assert prepared.evidence_sources == record.evidence_sources
    assert prepared.extraction_candidates == record.extraction_candidates
    assert prepare_record(prepared) == prepared
    assert CONTRACT_VERSION == "opportunity-1.1.0"


def test_enrichment_and_preparation_use_the_same_roles_and_keep_previous_requirements():
    base = make_record(presentation={"requirements": ["선발 후 활동보고서 제출 필수"]})
    html = """<div class="board_view"><p>지원자격</p><p>박사과정 재학생</p>
        <p>- 학생 본인 및 지도교수님 서명 필수</p><p>우대사항</p><p>전일제 대학원생</p>
        <p>제출서류: 자기소개서 및 학업계획서</p><p>지원동기는 어떤 형식으로 작성해야 하나요?</p>
        <p>지원 동기를 500자로 작성해 주세요.</p></div>""".encode()
    prepared = enrich_postech_cse_notice(base, html, base.official_url)
    assert "- 학생 본인 및 지도교수님 서명 필수" in prepared.required_documents
    assert "- 학생 본인 및 지도교수님 서명 필수" not in prepared.eligibility_rules
    assert [item["prompt_text"] for item in prepared.application_prompts] == ["지원 동기를 500자로 작성해 주세요."]
    assert "전일제 대학원생" in prepared.presentation["preferences"]
    assert "선발 후 활동보고서 제출 필수" in prepared.presentation["requirements"]
    assert prepare_record(prepared) == prepared
