from data_platform.contract import benefit_entries, condition_sections, parse_amount
from data_platform.linked import merge_prompts
from data_platform.parsers import ParsedOpportunity, enrich_postech_cse_notice
from data_platform.reference_policies import apply_card_exclusions, card_exclusion_clauses
from data_platform.criteria import normalize_eligibility
from data_platform.reprocess import saved_school_scopes


def record(**values):
    return ParsedOpportunity(canonical_key="test", title="공고", summary="", official_url="https://official.test/main", benefit_type="education_cost", benefit_description="교육비 지원", **values)


def test_preference_following_heading_is_not_mandatory():
    html = '<div class="board_view"><p>지원자격</p><p>석사 또는 박사과정 재학생</p><p>우대사항</p><p>전일제 대학원생</p><p>교육혜택</p><p>교육비 전액 무료</p></div>'.encode()
    parsed = enrich_postech_cse_notice(record(), html, record().official_url)
    assert "전일제 대학원생" not in parsed.eligibility_rules
    assert "전일제 대학원생" in parsed.presentation["preferences"]


def test_participation_has_only_requirement_presentation():
    result = condition_sections(record(eligibility_rules=["박사과정 재학생", "3주간 온라인 교육 자율 수강 가능한 분"]))
    assert "3주간 온라인 교육 자율 수강 가능한 분" not in result["eligibility"]
    assert "3주간 온라인 교육 자율 수강 가능한 분" in result["requirements"]


def test_explicit_monetary_bounds_and_qualifiers():
    assert parse_amount("매월 50만원 지원") == dict(amount_min=500000.0, amount_max=500000.0, currency="KRW", recurrence="month", amount_qualifier="fixed")
    assert parse_amount("최대 월 50만원 지원")["amount_min"] is None
    assert parse_amount("연간 100만원 내외")["amount_qualifier"] == "approximate"
    assert parse_amount("100~200만원 지원")["amount_max"] == 2000000
    assert parse_amount("USD 30,000/년")["currency"] == "USD"
    for text in ("등록금 50%", "등록금 100만원, 생활비 50만원", "308만원 상당 구독권", "본인부담금 최대 60만원"):
        assert parse_amount(text)["amount_max"] is None


def test_benefit_rows_keep_independent_types():
    items = benefit_entries(record(presentation={"benefits": ["교육비 전액 무료", "훈련장려금 최대 300만원", "최신 AI 개발 환경 및 장비 지원", "현직자 특강"]}))
    assert [item["benefit_type"] for item in items] == ["education_cost", "cash", "goods", "service"]


def test_prompt_collisions_and_source_preservation():
    first = [{"prompt_key": "prompt-1", "prompt_text": "지원 동기", "character_limit": 800}]
    second = [{"prompt_key": "prompt-1", "prompt_text": "학업 계획", "character_limit": 500}]
    result = merge_prompts(first, second, "https://official.test/main", "https://official.test/form.pdf")
    assert len({item["prompt_key"] for item in result}) == 2
    assert result[0]["source_url"].endswith("main")
    assert result[1]["source_url"].endswith("form.pdf")
    assert merge_prompts(result, second, "https://wrong.test", "https://official.test/form.pdf") == result


def test_multi_digit_exclusion_number_is_not_split_and_stays_unresolved():
    text = "제4조(지원 및 지원제외) 1. 만 75세 이상인 사람 10. 반환명령 미이행자 11. 다른 정부지원 훈련 참여자 제5조(계좌발급의 절차)"
    clauses = card_exclusion_clauses(text)
    assert any(clause.startswith("10.") for clause in clauses)
    assert any(clause.startswith("11.") for clause in clauses)
    result = apply_card_exclusions(record(), text)
    normalized = normalize_eligibility(result.eligibility_rules)
    assert any("반환명령 미이행자" in line for line in normalized.unresolved_rules)


def test_offline_school_scope_is_preserved_from_saved_official_inventory(tmp_path):
    import json
    config_path = tmp_path / "config" / "sources.json"
    coverage = tmp_path / "data" / "exports" / "school_coverage.json"
    coverage.parent.mkdir(parents=True)
    coverage.write_text(json.dumps({"departments": [{"department": "컴퓨터공학과", "code": "cse", "fundingPages": ["https://cse.postech.ac.kr/scholarship"]}]}), encoding="utf-8")
    result = saved_school_scopes(config_path, {"school_directory": {"coverage_report": "data/exports/school_coverage.json"}})
    assert result["https://cse.postech.ac.kr/scholarship"] == ["소속 학교: POSTECH", "소속 학과: 컴퓨터공학과 [cse]"]
