from data_platform.criteria import CRITERIA_VERSION, normalize_eligibility


def test_age_nationality_and_attendance_are_canonical() -> None:
    result = normalize_eligibility(
        [
            "만 19~34세 대한민국 청년 누구나",
            "3주간 온라인 교육 자율 수강 가능한 분",
        ]
    )
    assert CRITERIA_VERSION == "eligibility-1.6.0"
    assert [(rule.criterion_code, rule.operator, rule.value) for rule in result.rules] == [
        ("age", "between", [19, 34]),
        ("nationality", "eq", "KR"),
        ("online_learning_available", "eq", True),
    ]
    assert result.unresolved_rules == []
    assert result.match_mode == "structured"
    assert [(rule.criterion_code, rule.condition_kind) for rule in result.rules] == [
        ("age", "eligibility"),
        ("nationality", "eligibility"),
        ("online_learning_available", "requirement"),
    ]


def test_participation_definition_matches_database_migration() -> None:
    from pathlib import Path
    from data_platform.criteria import CRITERIA_DEFINITIONS, PARTICIPATION_CRITERIA

    sql = (Path(__file__).parents[1] / "migrations" / "010_referenced_eligibility.sql").read_text(encoding="utf-8")
    assert len(PARTICIPATION_CRITERIA) == 8
    for code in PARTICIPATION_CRITERIA:
        assert code in CRITERIA_DEFINITIONS
        assert f"'{code}'" in sql
    assert "training_card_eligible" in PARTICIPATION_CRITERIA
    assert "fulltime_student" not in PARTICIPATION_CRITERIA
    assert "employment_preparation" in PARTICIPATION_CRITERIA


def test_team_opportunity_is_fully_structured() -> None:
    result = normalize_eligibility(
        [
            "모집대상 (3인 이상 5인 이하 대학생팀)",
            "개인 지원은 불가하며 팀 단위로만 지원할 수 있음 (전공 및 학년 제한 없이 지원가능)",
            "사업 종료 시점까지 재학생 신분인 자 (휴학생 가능, 졸업생 불가)",
        ]
    )
    assert result.match_mode == "structured"
    assert {rule.criterion_code for rule in result.rules} == {
        "team_size",
        "academic_level",
        "enrollment_status",
        "enrollment_commitment",
    }


def test_graduate_paths_preserve_nested_or_and_logic() -> None:
    result = normalize_eligibility(
        [
            "① 석사 : 1-3학기 재학생",
            "② 박사 : Coursework 수료자",
            "Coursework 수료기준은 성적표 상 학점이 더 이상 기입되지 않는 시점",
        ]
    )
    groups = {group.group_key: group for group in result.groups}
    assert groups["graduate_path"].combinator == "or"
    assert groups["master_path"].parent_group_key == "graduate_path"
    assert groups["doctoral_path"].parent_group_key == "graduate_path"
    assert len(result.rules) == 4
    assert result.match_mode == "structured"


def test_residence_paths_preserve_self_or_ancestor_alternative() -> None:
    result = normalize_eligibility(
        [
            "신청대상: 다음 대상 중 하나에 해당하는 자",
            "① 지원일 현재 본인이 영주시에 주민등록주소를 둔 대학생 또는 휴학생",
            "② 지원일 현재 직계존속이 영주시에 주민등록주소를 둔 대학생 또는 휴학생",
        ]
    )
    groups = {group.group_key: group for group in result.groups}
    assert groups["residence_path"].combinator == "or"
    assert {rule.criterion_code for rule in result.rules} == {
        "residence_city",
        "ancestor_residence_city",
        "academic_level",
        "enrollment_status",
    }
    assert len(result.rules) == 6
    assert result.match_mode == "structured"


def test_unknown_text_is_never_treated_as_failure_or_structured_rule() -> None:
    result = normalize_eligibility(["기관이 별도로 정한 기준을 충족한 자"])
    assert result.rules == []
    assert result.unresolved_rules == ["기관이 별도로 정한 기준을 충족한 자"]
    assert result.match_mode == "review_required"


def test_academic_year_range_variants_preserve_both_limits() -> None:
    for source in ("1~2학년 학부생", "1 ∼ 2학년 학부생", "1학년-2학년 학부 재학생"):
        result = normalize_eligibility([source])
        year = next(rule for rule in result.rules if rule.criterion_code == "academic_year")
        assert (year.operator, year.value) == ("between", [1, 2])
        assert year.source_text == source
        assert any(rule.criterion_code == "academic_level" and rule.value == "undergraduate" for rule in result.rules)
        assert result.match_mode == "structured"


def test_academic_year_range_does_not_consume_another_qualification() -> None:
    result = normalize_eligibility(["1~2학년 학부생, 지도교수 추천 필수"])
    assert any(rule.criterion_code == "academic_year" for rule in result.rules)
    assert result.unresolved_rules == [", 지도교수 추천 필수"]
    assert result.match_mode == "partial"


def test_gpa_without_scale_or_basis_is_extracted_but_not_comparison_ready() -> None:
    result = normalize_eligibility(["평점 3.5이상"])
    grade = next(rule for rule in result.rules if rule.criterion_code == "gpa")
    assert grade.value == 3.5
    assert grade.unit == "gpa_scale"
    assert grade.context == {}
    assert any("만점 미확인" in text for text in result.unresolved_rules)
    assert any("평가 기준 미확인" in text for text in result.unresolved_rules)
    assert result.match_mode == "partial"


def test_gpa_explicit_scale_and_basis_are_preserved() -> None:
    for source in ("직전 학기 평점 3.5이상/4.5만점", "직전 학기 4.5만점 기준 평점 3.5 이상"):
        result = normalize_eligibility([source])
        grade = next(rule for rule in result.rules if rule.criterion_code == "gpa")
        assert (grade.operator, grade.value, grade.unit) == ("gte", 3.5, "gpa_4.5")
        assert grade.context == {"basis": "previous_semester"}
        assert result.match_mode == "structured"


def test_gpa_explicit_calendar_semester_is_kept() -> None:
    result = normalize_eligibility(["2026학년도 1학기 누적 평점 3.3 이상/4.3만점"])
    assert result.rules[0].context == {"basis": "cumulative", "evaluationPeriod": "2026-1"}
    assert result.match_mode == "structured"


def test_gpa_dual_scales_are_alternatives_not_both_required() -> None:
    result = normalize_eligibility(["직전 학기 평점 3.50이상/4.5만점(3.30이상/4.3만점)"])
    assert result.match_mode == "structured"
    group = next(group for group in result.groups if group.group_key.startswith("gpa_scale_paths"))
    assert group.combinator == "or"
    assert [(rule.value, rule.unit) for rule in result.rules] == [(3.5, "gpa_4.5"), (3.3, "gpa_4.3")]
    assert all(rule.group_key == group.group_key and rule.context["scaleAlternative"] for rule in result.rules)


def test_saved_national_grade_window_keeps_exceptions_and_unmodeled_alternative() -> None:
    source = "직전 2년간(1~2학년) 4개 학기(5년제는 5개학기)성적이 종합평균 백분위 87점 이상 또는 평점 3.50이상/4.5만점(3.30이상/4.3만점)"
    result = normalize_eligibility([source])
    assert {rule.criterion_code for rule in result.rules} == {"gpa"}
    assert all(rule.context["basis"] == "specified_window" and rule.context["requiresReview"] for rule in result.rules)
    assert all(rule.context["windowText"] == source for rule in result.rules)
    assert any(group.combinator == "or" for group in result.groups)
    assert any(group.group_key.startswith("unresolved_grade_path") for group in result.groups)
    assert any("5년제는 5개학기" in text and "백분위 87점" in text for text in result.unresolved_rules)
    assert result.match_mode == "partial"


def test_saved_credit_ratio_remains_unresolved_instead_of_becoming_absolute_credits() -> None:
    source = "'10년도 이후 선발 장학생 : 총 2년 동안의 취득 이수학점이 소속대학 졸업이수학점의 40% 이상 충족"
    result = normalize_eligibility([source])
    assert result.rules == []
    assert result.unresolved_rules == [source]


def test_topik_extracts_level_without_erasing_one_time_support_limit() -> None:
    source = "TOPIK 4급 이상 취득자 재학 중 1회 지원"
    result = normalize_eligibility([source])
    assert [(rule.criterion_code, rule.operator, rule.value) for rule in result.rules] == [("topik_level", "gte", 4)]
    assert result.rules[0].source_text == source
    assert result.unresolved_rules == ["재학 중 1회 지원"]
    assert result.match_mode == "partial"


def test_topik_equal_and_upper_limit_have_distinct_operators() -> None:
    for source, operator in (("TOPIK 4급", "eq"), ("TOPIK 4급 이하", "lte")):
        result = normalize_eligibility([source])
        assert result.rules[0].operator == operator
        assert result.match_mode == "structured"


def test_residence_duration_converts_years_to_months() -> None:
    for source, months in (("연속 거주 12개월 이상", 12), ("연속 거주 2년 이상", 24)):
        result = normalize_eligibility([source])
        assert [(rule.criterion_code, rule.value, rule.unit) for rule in result.rules] == [("residence_months", months, "month")]
        assert result.match_mode == "structured"


def test_residence_parent_alternative_is_not_flattened_into_self_restriction() -> None:
    source = "본인 또는 부모가 포항시에 12개월 이상 거주"
    result = normalize_eligibility([source])
    assert result.rules == []
    assert result.unresolved_rules == [source]


def test_unknown_alternative_is_retained_beside_known_gpa_path() -> None:
    result = normalize_eligibility(["직전 학기 평점 3.5이상/4.5만점 또는 지도교수 추천"])
    grade = next(rule for rule in result.rules if rule.criterion_code == "gpa")
    parent = next(group for group in result.groups if group.group_key == grade.group_key)
    assert parent.combinator == "or"
    assert any(group.parent_group_key == parent.group_key for group in result.groups)
    assert any("지도교수 추천" in text for text in result.unresolved_rules)


def test_mixed_age_sentence_does_not_erase_an_unrecognized_qualification() -> None:
    source = "만 19~34세 대한민국 청년, 기관 추천 필수"
    result = normalize_eligibility([source])
    assert {rule.criterion_code for rule in result.rules} == {"age", "nationality"}
    assert result.unresolved_rules == [source]
    assert result.match_mode == "partial"


def test_mixed_team_sentence_does_not_erase_an_unrecognized_qualification() -> None:
    source = "모집대상 (3인 이상 5인 이하 대학생팀), 지역 추천 필수"
    result = normalize_eligibility([source])
    assert {rule.criterion_code for rule in result.rules} == {"team_size", "academic_level"}
    assert result.unresolved_rules == [source]
    assert result.match_mode == "partial"
