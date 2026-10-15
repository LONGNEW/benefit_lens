from dataclasses import replace
from data_platform.criteria import normalize_eligibility
from data_platform.parsers import ParsedOpportunity, enrich_postech_cse_notice
from data_platform.reference_policies import parse_study_duration
from data_platform.linked import linked_targets
from data_platform.public_bundle import codetree_declarations


def test_formal_duration_does_not_use_maximum_registration_period():
    data = parse_study_duration("제20조(수업 연한 및 재학 연한) ① 대학원의 석사과정 및 박사과정의 수업 연한은 각각 2년, 통합과정의 수업 연한은 4년으로 한다. ② 박사과정 재학 연한 6년")
    assert data["semestersByLevel"] == {"master": 4, "doctoral": 4}
    assert "재학 연한 6년" not in data["sourceText"]


def test_card_policy_has_objective_age_and_student_alternatives():
    result = normalize_eligibility(["카드 발급 제외: 만 75세 이상인 사람", "카드 발급 학생 기준: 졸업까지 남은 수업연한이 2년 이내인 사람", "국민내일배움카드 발급 가능자"])
    assert result.match_mode == "structured"
    assert [(r.criterion_code, r.operator, r.value) for r in result.rules] == [("age", "lte", 74), ("enrollment_status", "eq", "graduated"), ("remaining_study_months", "lte", 24), ("training_card_eligible", "eq", True)]
    assert result.rules[-1].condition_kind == "requirement"
    assert any(g.combinator == "or" for g in result.groups)


def test_inline_markup_keeps_full_mission_and_real_application_period():
    notice = ParsedOpportunity(canonical_key="test", title="POSTECH 학생 챌린지", summary="", official_url="https://public.test/detail", benefit_type="service", benefit_description="", evidence={"published_at": "2026.09.17"})
    html = '<div class="board_view"><p>2. 참여 대상</p><p>코딩테스트 공부를 처음 시작하거나, 꾸준한 문제 풀이 습관을 만들고 싶은 학생</p><p>3. 신청 기간 및 학습 지원</p><p><b>신청</b> 기간: 2026년 9월 1일 ~ 10월 31일.</p><p>코드트리의 유료 학습 과정을 무료로 이용할 수 있습니다.</p><p>4. 관문별 학습 미션</p><p>4관문: 15~21일차에 7일 중 3일 이상 학습 → 28일차까지 이용</p><p>5. 참여 방법</p><p>갭체크 완료 후 4일 안에 참여 신청을 마칩니다.</p><p>6. 신청 및 문의</p></div>'.encode()
    result = enrich_postech_cse_notice(notice, html, notice.official_url)
    assert result.opens_at.startswith("2026-09-01")
    assert result.closes_at.startswith("2026-10-31")
    assert any("4관문" in x for x in result.presentation["conditions"])
    assert any("4일 안" in x for x in result.presentation["conditions"])
    assert any("무료" in x for x in result.presentation["benefits"])
    assert "소속 학교: POSTECH" in result.eligibility_rules
    assert normalize_eligibility(result.eligibility_rules).match_mode == "structured"


def test_round_link_traversal_excludes_other_rounds_and_private_questions():
    html = '<div id="content"><a href="/camp_board/1">5기 일정안내</a><a href="/camp_board/2">4기 일정안내</a><a href="/camp_board/3">스쿨 FAQ</a><a href="/qna/4">5기 지원 질문</a></div>'.encode()
    assert linked_targets(html, "https://edu.rapa.or.kr/recruitment/11921", {"edu.rapa.or.kr"}, "LIG 5기") == [("https://edu.rapa.or.kr/camp_board/1", "html"), ("https://edu.rapa.or.kr/camp_board/3", "html")]


def test_public_bundle_keeps_subscription_exception_and_prior_benefit_restriction():
    script = 'joined:{title:`중복`,body:`양을 구하러 가는 건 한 사람당 한 번뿐이에요.`},paid:{title:`결제`,body:`이전에 결제한 적이 있고 현재 구독 중이 아닌 경우에는\\n참여 대상에 포함되지 않아요.`},bc3:{title:`수혜`,body:`지난 청약통장 챌린지를 완주해 8월 31일까지\\n무료 학습권을 받은 분은 참여 대상에 포함되지 않아요.`},closed:{title:`마감`,body:`10월 31일까지만 신청을 받았어요.`}'
    declarations = codetree_declarations(script)
    result = normalize_eligibility([declarations[key] for key in ("joined", "paid", "bc3")])
    assert result.match_mode == "structured"
    assert any(g.group_key == "codetree_payment_path" and g.combinator == "or" for g in result.groups)
    assert {r.criterion_code for r in result.rules} == {"codetree_paid", "codetree_subscribed", "codetree_prior_challenge", "codetree_bc3_free_pass"}


def test_card_application_clock_is_not_replaced_by_midnight():
    from data_platform.parsers import _application_window
    notice = ParsedOpportunity(canonical_key="test", title="LIG 5기", summary="", official_url="https://public.test/detail", benefit_type="service", benefit_description="")
    opens, closes, _, _ = _application_window(["신청기간", "2026. 9. 16. ~ 9. 28. 09:00"], notice)
    assert opens.startswith("2026-09-16")
    assert closes.startswith("2026-09-28T09:00:00")


def test_relay_and_provider_degree_conditions_do_not_duplicate_group_keys():
    result = normalize_eligibility(["지원자격: 국민내일배움카드 발급 가능한 학사학위 이상(졸업예정자) 누구나", "학사학위 이상 소지자(졸업예정자 포함)"])
    keys = [g.group_key for g in result.groups]
    assert len(keys) == len(set(keys))
    assert result.match_mode == "structured"


def test_education_period_cannot_become_the_application_deadline():
    from data_platform.parsers import _application_window
    notice = ParsedOpportunity(canonical_key="test", title="LIG 5기", summary="", official_url="https://public.test/detail", benefit_type="service", benefit_description="")
    _, closes, _, _ = _application_window(["신청기간: 2026. 9. 16. ~ 9. 28. 09:00", "교육기간: 2026.10.20 ~ 2027.04.16"], notice)
    assert closes.startswith("2026-09-28T09:00:00")


def test_provider_clock_survives_day_only_relay_title():
    from data_platform.parsers import _application_window
    notice = ParsedOpportunity(canonical_key="test", title="LIG 5기 (~9/28(월))", summary="", official_url="https://public.test/detail", benefit_type="service", benefit_description="")
    _, closes, _, _ = _application_window(["신청기간", "2026. 9. 16. ~ 9. 28. 09:00"], notice)
    assert closes.startswith("2026-09-28T09:00:00")


def test_navigation_and_decorative_photos_do_not_create_eligibility_holds():
    from data_platform.parsers import _section_lines
    lines = ["지원자격", "만 19~34세 대한민국 청년 누구나", "이전글 다른 공고", "다음글 다른 공고"]
    assert _section_lines(lines, ("지원자격",)) == lines[:2]
    html = '<main><img src="/second/bg-program-info.jpg"><img src="/second/field-preview-1.jpg"><img src="/poster.jpg"></main>'.encode()
    assert linked_targets(html, "https://public.test/", {"public.test"}) == [("https://public.test/poster.jpg", "image")]


def test_followup_faq_preserves_provider_clock_and_section_number_is_not_a_time():
    from data_platform.parsers import _application_window, _partial_close_time
    notice = ParsedOpportunity(canonical_key="test", title="LIG 5기 (~9/28(월))", summary="", official_url="https://public.test/detail", benefit_type="service", benefit_description="", closes_at="2026-09-28T09:00:00+09:00", evidence={"published_at": "2026.09.16"})
    _, closes, _, _ = _application_window([], notice)
    assert closes.startswith("2026-09-28T09:00:00")
    assert _partial_close_time("신청 기간 ~10/9 2. 교육혜택") is None


def test_mixed_card_and_job_interest_line_retains_both_procedures():
    result = normalize_eligibility(["-교육대상 : 국민내일배움카드 발급 가능자 AI, SW 분야 취업 희망자"])
    assert {r.criterion_code for r in result.rules} == {"training_card_eligible", "employment_preparation"}
    assert all(r.condition_kind == "requirement" for r in result.rules)
    assert result.unresolved_rules == []


def test_assessment_clock_in_same_paragraph_is_not_the_application_clock():
    from data_platform.parsers import _application_window
    notice = ParsedOpportunity(canonical_key="test", title="LIG 5기 (~9/28(월))", summary="", official_url="https://public.test/detail", benefit_type="service", benefit_description="", evidence={"published_at": "2026.09.16"})
    _, closes, _, _ = _application_window(["- 신청 마감 : ~26년 9월 28일(월) 09:00 * 역량 검사(필수) : ~26년 9월 28일(월) 11:59"], notice)
    assert closes.startswith("2026-09-28T09:00:00")
