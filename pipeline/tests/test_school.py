from data_platform.school import canonical_school_url, directory_entries, funding_links, parse_central, parse_cse, parse_generic, parse_semi, parse_graduate, parse_mse
from data_platform.criteria import normalize_eligibility


def test_directory_requires_official_host_and_keeps_legacy_tls_target():
    html = b'<li><p class="depart-title">Dept</p><a class="homepage" href="http://dese.postech.ac.kr">Home</a></li><li><p class="depart-title">Bad</p><a class="homepage" href="https://postech.ac.kr.evil.test">Home</a></li>'
    assert directory_entries(html, "https://www.postech.ac.kr")[0]["homepage"] == "https://dese.postech.ac.kr"
    assert len(directory_entries(html, "https://www.postech.ac.kr")) == 1


def test_links_do_not_cross_hosts_or_authentication():
    html = '<a href="/scholarship">장학</a><a href="/login">장학</a><a href="https://external.test">장학</a><a href="/file.pdf">장학</a>'.encode()
    assert funding_links(html, "https://cse.postech.ac.kr/", "cse.postech.ac.kr") == ["https://cse.postech.ac.kr/file.pdf", "https://cse.postech.ac.kr/scholarship"]


def test_funding_page_discovers_labeled_downloads_and_safe_document_links():
    html = '<main><h2>재정지원 안내</h2><a href="/download?id=1">지원안내.hwpx</a><a href="/rules.pdf">규정</a><a href="/login?file=guide.pdf">장학금</a><a href="https://external.test/guide.pdf">장학금</a></main>'.encode()
    assert funding_links(html, "https://cse.postech.ac.kr/funding", "cse.postech.ac.kr") == ["https://cse.postech.ac.kr/download?id=1", "https://cse.postech.ac.kr/rules.pdf"]


def test_discovery_registers_pdf_document_parser_without_reading_pdf_as_html(monkeypatch, tmp_path):
    from data_platform.http import FetchResult
    from data_platform.school import DIRECTORY, discover_school_sources
    def get(self, url):
        if url == DIRECTORY:
            body = b'<li><p class="depart-title">Dept</p><a class="homepage" href="https://cse.postech.ac.kr">Home</a></li>'
        elif url == "https://cse.postech.ac.kr":
            body = '<main><a href="/guide.pdf">장학 안내</a></main>'.encode()
        else:
            body = b"%PDF-1.7\nmock"
        return FetchResult(url, url, 200, "text/html", {}, body)
    monkeypatch.setattr("data_platform.school.SafeHttpClient.get", get)
    monkeypatch.setattr("data_platform.school.parse_school_document", lambda body, url: [object()])
    monkeypatch.setattr("data_platform.school.parse_school_page", lambda *args: (_ for _ in ()).throw(AssertionError("PDF entered HTML parser")))
    sources = discover_school_sources({"user_agent": "test", "timeout_seconds": 1, "max_body_bytes": 1000}, tmp_path / "coverage.json")
    assert len(sources) == 1
    assert sources[0]["parser"] == "postech_department_funding_document"
    assert sources[0]["url"] == "https://cse.postech.ac.kr/guide.pdf"


def test_school_document_text_retains_document_evidence(monkeypatch):
    from data_platform.school import parse_school_document
    monkeypatch.setattr("data_platform.linked.extract_document", lambda body, kind: "대학원 재정지원\n지원 대상은 전일제 박사과정 대학원 재학생입니다.\n장학금으로 등록금 전액과 연구 생활비를 지원합니다.")
    records = parse_school_document(b"%PDF-1.7\nmock", "https://cse.postech.ac.kr/guide.pdf")
    assert len(records) == 1
    assert records[0].evidence_sources[0]["kind"] == "pdf"
    assert "전일제 박사과정" in records[0].eligibility_rules[0]


def test_current_school_policy_url_replaces_legacy_alias():
    html = '<a href="/sub4_2.php">재정지원</a><a href="/sub4_2_a.php">재정지원</a>'.encode()
    assert canonical_school_url("https://gift.postech.ac.kr/sub4_2.php") == "https://gift.postech.ac.kr/sub4_2_a.php"
    assert funding_links(html, "https://gift.postech.ac.kr/", "gift.postech.ac.kr") == ["https://gift.postech.ac.kr/sub4_2_a.php"]


def test_central_rowspan_preserves_requirements_for_sa_ra():
    html = '<table><caption>등록금 외 지원 장학금(교내장학금)</caption><tbody><tr><th rowspan="3">근로</th><td>SMP장학금</td><td>25만원</td><td rowspan="3" colspan="3">3학년 이상 재학생</td></tr><tr><td>SA장학금</td><td>25만원</td></tr><tr><td>RA장학금</td><td>25만원</td></tr></tbody></table>'.encode()
    records = parse_central(html, "https://www.postech.ac.kr/funding")
    assert len(records) == 3
    assert all("3학년 이상 재학생" in r.eligibility_rules for r in records)
    assert all(r.record_kind == "policy" and r.lifecycle_status == "unknown" for r in records)


def test_cse_separates_selection_from_benefit():
    html = '<div class="list-box"><p class="title-txt">GLP</p><li><span>대상</span><p>학부 3학년</p></li><li><span>장학금액</span><p>800만원</p></li><li><span>선발방법</span><p>위원회 선정</p></li></div>'.encode()
    record = parse_cse(html, "https://cse.postech.ac.kr/funding")[0]
    assert record.presentation["benefits"] == ["800만원"]
    assert record.presentation["conditions"] == ["선발방법: 위원회 선정"]


def test_semiconductor_hiding_box_includes_terms():
    html = '<div class="hiding_box"><p class="hide_tit">입학 장려금</p><li>230만원<p class="mark">휴대전화 제외</p></li></div>'.encode()
    record = parse_semi(html, "https://semi.postech.ac.kr/funding")[0]
    assert record.presentation["specialTerms"] == ["휴대전화 제외"]


def test_generic_navigation_does_not_become_a_program():
    import pytest
    with pytest.raises(ValueError):
        parse_generic('<main><nav>장학금 800만원</nav><h2>학교 소개</h2></main>'.encode(), "https://cse.postech.ac.kr/")


def test_generic_policy_extracts_audience_rule_without_placeholder():
    html = '<main><h2>재정지원 제도란?</h2><p>재정지원으로 등록금 보전과 생활비 지원을 제공하며 금액은 2026학년도 기준임</p><p>모든 전공생 중 산업체 재직자는 재정지원제도의 수혜 대상이 아님</p></main>'.encode()
    record = parse_generic(html, "https://gift.postech.ac.kr/sub4_2.php")[0]
    assert record.official_url == "https://gift.postech.ac.kr/sub4_2_a.php"
    assert record.eligibility_rules == ["모든 전공생 중 산업체 재직자는 재정지원제도의 수혜 대상이 아님"]
    assert all("상세 대상과 선발 기준" not in line for line in record.eligibility_rules)


def test_school_department_scope_are_exact_categories():
    rules = normalize_eligibility(["소속 학교: POSTECH", "소속 학과: 컴퓨터공학과 [cse]", "위원회 선발 기준 확인 필요"])
    assert [(x.profile_field, x.value) for x in rules.rules] == [("institution", "POSTECH"), ("department", "cse")]
    assert rules.match_mode == "partial"


def test_industry_employee_exclusion_is_a_common_boolean_rule():
    source = "모든 전공생 중 산업체 재직자는 재정지원제도의 수혜 대상이 아님"
    rules = normalize_eligibility([source])
    assert [(rule.profile_field, rule.operator, rule.value, rule.label) for rule in rules.rules] == [
        ("industryEmployee", "eq", False, "산업체 재직자 제외")
    ]
    assert rules.unresolved_rules == []


def test_graduate_table_keeps_residence_obligation_separate():
    html = '<table><thead><th>장학금명(재단)</th></thead><tbody><tr><td>K-탑티어</td><td><li>박사과정 대학원생</li><li>선발인원: 20명/년</li></td><td><li>지원금액: 5,000만원/년</li><li>졸업 후 경북 내 정주 의무</li></td></tr></tbody></table>'.encode()
    record = parse_graduate(html, "https://adm-g.postech.ac.kr/funding")[0]
    assert record.presentation["specialTerms"] == ["졸업 후 경북 내 정주 의무"]
    assert record.presentation["benefits"] == ["지원금액: 5,000만원/년"]


def test_mse_individual_awards_keep_payment_units():
    html = '<div class="text-content"><p>가. 성적최우수 장학금(200만원/인)</p><p>: 각 학년별 1~2명</p><p>나. 성적우수 장학금(100만원/인)</p><p>: 각 학년별 2~3명</p></div>'.encode()
    records = parse_mse(html, "https://mse.postech.ac.kr/funding")
    assert len(records) == 2
    assert records[0].benefit_description == "200만원/인"
    assert "해당학기 이수학점 15학점 이상" in records[0].eligibility_rules


def test_duration_qualification_requires_an_explicit_commitment():
    result = normalize_eligibility(["사업 참여부터 종료 시점까지 재학생 신분인 자 (휴학생 가능, 졸업생 불가)"])
    assert any(rule.criterion_code == "enrollment_commitment" and rule.value is True for rule in result.rules)
