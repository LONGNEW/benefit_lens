import pytest
from data_platform.review import validate_candidate
from data_platform.benefitlens import _normalization_coverage
from data_platform.parsers import ParsedOpportunity

def candidate(kind, quote):
    return {"field_kind": kind, "source_quote": quote}

def test_literal_provenance_is_required():
    with pytest.raises(ValueError, match="stored original"):
        validate_candidate(candidate("eligibility", "1~2학년 학부생"), "3학년 재학생", "1~2학년 학부생")

def test_quote_must_exist_even_when_value_exists():
    with pytest.raises(ValueError, match="quote"):
        validate_candidate(candidate("eligibility", "가짜 인용"), "1~2학년 학부생", "1~2학년 학부생")

def test_year_range_can_be_promoted():
    result = validate_candidate(candidate("eligibility", "1~2학년"), "1~2학년 학부생", "대상: 1~2학년 학부생")
    assert result["rules"] >= 2 and not result["unresolved"]

@pytest.mark.parametrize("text", ["자기소개서 양식은 접수시스템에서 확인", "AI역량검사는 무엇이며, 언제 진행이 되나요?", "필수제출: 지원서, 자기소개서"])
def test_non_questions_cannot_be_promoted(text):
    with pytest.raises(ValueError, match="application questions"):
        validate_candidate(candidate("application_prompt", text), text, text)

def test_document_obligation_is_not_eligibility():
    text = "학생 본인 및 지도교수님 서명 필수"
    assert validate_candidate(candidate("required_document", text), text, text)["field"] == "required_document"

def test_unsupported_text_requires_explicit_model_work():
    text = "기관이 별도로 정한 기준"
    with pytest.raises(ValueError, match="canonical"):
        validate_candidate(candidate("eligibility", text), text, text)

def test_coverage_checks_semantics_not_raw_presence():
    record = ParsedOpportunity("test", "title", "", "https://example.com", "unknown", "", eligibility_rules=["기관이 별도로 정한 기준"])
    result = _normalization_coverage([record])
    assert result["review_required_records"] == 1 and result["structured_records"] == 0
