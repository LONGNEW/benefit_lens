from data_platform.parsers import ParsedOpportunity, enrich_postech_cse_notice, _section_lines, _application_window
from data_platform.linked import linked_targets, _primary_notice, enrich_linked, extract_document, _ocr_runtime
from data_platform.http import FetchResult, document_kind
import io
import json
import zipfile
import pytest
from dataclasses import replace
from data_platform.criteria import normalize_eligibility


def record():
    return ParsedOpportunity(canonical_key="test", title="공고", summary="", official_url="https://cse.postech.ac.kr/detail", benefit_type="service", benefit_description="공고")


def test_long_section_keeps_last_attendance_and_preference():
    lines = ["지원 자격", "만 19~34세 대한민국 청년 누구나", "3주간 온라인 교육 자율 수강 가능한 분", "11월 28일~29일 해커톤 일정에 참여 가능한 분", "※ 우대사항: 차기 과정 입과 가능한 분", "신청방법", "온라인 신청"]
    captured = _section_lines(lines, ("지원 자격",))
    assert captured == lines[:5]
    result = normalize_eligibility(captured)
    assert {r.criterion_code for r in result.rules} == {"age", "nationality", "online_learning_available", "hackathon_available"}
    # Preferences are not a mandatory eligibility barrier.
    assert result.unresolved_rules == ["※ 우대사항: 차기 과정 입과 가능한 분"]


def test_education_target_is_not_lost_and_benefit_is_not_title():
    html = '<div class="board_view"><p>교육대상:</p><p>국민내일배움카드 발급 가능자</p><p>AI 분야 취업 희망자</p><p>교육혜택</p><p>교육비 전액 무료</p></div>'.encode()
    result = enrich_postech_cse_notice(record(), html, record().official_url)
    assert "국민내일배움카드 발급 가능자" in result.presentation["requirements"]
    assert "AI 분야 취업 희망자" in result.presentation["requirements"]
    assert "국민내일배움카드 발급 가능자" not in result.eligibility_rules
    assert "교육비 전액 무료" not in result.eligibility_rules
    assert result.evidence_sources[0]["status"] == "collected"


def test_linked_sources_are_allowlisted_and_pdf_precedes_poster():
    html = '<main><img src="/poster.jpg"><a href="/file.pdf">규정</a><a href="https://private.invalid/detail">공식</a><a href="/login">로그인</a></main>'.encode()
    targets = linked_targets(html, "https://public.test/detail", {"public.test"})
    assert targets == [("https://public.test/file.pdf", "pdf"), ("https://public.test/poster.jpg", "image")]


def test_third_residence_path_is_not_discarded_as_structured():
    lines = ["신청대상: 다음 대상 중 하나에 해당하는 자", "① 본인이 영주시에 주민등록주소를 둔 대학생 또는 휴학생", "② 직계존속이 영주시에 주민등록주소를 둔 대학생 또는 휴학생", "③ 영주시 소재 고등교육기관 재학생이며 1년 이상 거주"]
    result = normalize_eligibility(lines)
    assert lines[-1] in result.unresolved_rules
    assert result.match_mode == "partial"


def test_doctoral_and_fulltime_first_semester_have_distinct_fields():
    result = normalize_eligibility(["박사과정 재학생", "전일제 대학원생", "대학원 현재 1학기"])
    assert {r.profile_field for r in result.rules} == {"academicLevel", "enrollmentStatus", "fullTimeStudent", "graduateSemester"}
    assert result.match_mode == "structured"


def test_recommendation_is_not_silently_a_passable_selection_rule():
    assert normalize_eligibility(["학과 추천 후 위원회가 별도로 선발"]).match_mode == "review_required"


def test_global_brochure_is_not_a_notice_attachment():
    html = '<header><a href="/brochure.pdf">학교 소개.pdf</a></header><main><p>본문</p></main><aside><a href="/notice.pdf">선발공고.pdf</a></aside>'.encode()
    assert linked_targets(html, "https://public.test/detail", {"public.test"}) == [("https://public.test/notice.pdf", "pdf")]


def test_published_date_can_be_the_real_application_opening_date():
    notice = replace(record(), title="모집 (~10.14 09시)", evidence={"published_at": "2026.09.28"})
    opens, closes, _, _ = _application_window(["모집기간", "2026.09.28 ~ 2026.10.14", "교육기간", "2026.10.25 ~ 2026.11.15"], notice)
    assert opens.startswith("2026-09-28")
    assert closes.startswith("2026-10-14T09:00:00")


def test_sk_primary_preferences_and_navigation_do_not_become_requirements():
    html = '''<body><nav>지원자격 메뉴</nav><div class="recruit-eligibility"><h3>지원자격</h3><ul>
    <li>고등학교 졸업 이상 취업을 준비하는 청년 누구나 (대학교/대학원 재학생 및 휴학생 포함)</li>
    <li>3주간의 온라인 AI 부트캠프에 참여 가능한 자</li><li>11월 28일~29일 AI 해커톤에 참여 가능한 자</li>
    </ul><strong>우대사항</strong><ul><li>27년 SKALA 입과 가능자(現 대학교 4학년)</li></ul></div><footer>개인정보</footer></body>'''.encode()
    result, _ = _primary_notice(record(), html, "https://2026pre-skala.co.kr/")
    criteria = normalize_eligibility(result.eligibility_rules)
    assert criteria.match_mode == "structured"
    assert "27년 SKALA 입과 가능자(現 대학교 4학년)" in result.presentation["preferences"]
    assert "27년 SKALA 입과 가능자(現 대학교 4학년)" not in result.presentation["conditions"]
    assert not any(r.profile_field == "year" for r in criteria.rules)


def test_nc_alternative_paths_and_military_extension_are_preserved():
    criteria = normalize_eligibility(["만 15세~ 34세 미취업 청년", "취업 준비생", "27년 2월 졸업 예정자", "휴학생 (학년 무관)", "군필자의 연령 예외는 의무복무기간과 함께 판정해야 함", "평일 09:00~18:00, 1일 8시간씩 주 5일 운영될 예정입니다."])
    assert criteria.match_mode == "structured"
    assert any(g.combinator == "or" for g in criteria.groups)
    assert next(r for r in criteria.rules if r.criterion_code == "age").unit == "year_military_extension_39"
    assert any(r.profile_field == "fullTimeTrainingAvailable" for r in criteria.rules)


def test_coding_campaign_alternatives_are_one_explicit_personal_declaration():
    criteria = normalize_eligibility(["하반기 취업, 인턴십 코딩테스트를 준비하는 학생", "자료구조, 알고리즘 수업 내용을 문제 풀이로 복습하려는 학생"])
    assert criteria.match_mode == "structured"
    assert len(criteria.rules) == 1
    assert criteria.rules[0].profile_field == "codingStudyInterest"


def test_training_degree_and_career_interest_are_not_discarded_as_free_text():
    criteria = normalize_eligibility(["지원자격: 국민내일배움카드 발급 가능한 학사학위 이상(졸업예정자) 누구나", "교육대상: LIG D&A 등 방산 임베디드SW 직무 관련 취업 희망자"])
    assert criteria.match_mode == "structured"
    assert any(g.combinator == "or" for g in criteria.groups)
    assert {r.profile_field for r in criteria.rules} >= {"trainingCardEligible", "highestDegreeLevel", "employmentPreparation"}


def test_hwp_and_hwpx_and_explicit_same_host_notice_are_traversed():
    html = '<main><a href="/download?id=1">모집안내.hwpx</a><a href="/guide.hwp">신청서</a><a href="/detail?id=2">상세 지원자격</a></main>'.encode()
    assert linked_targets(html, "https://public.test/notice", {"public.test"}) == [
        ("https://public.test/download?id=1", "hwpx"),
        ("https://public.test/guide.hwp", "hwp"),
        ("https://public.test/detail?id=2", "html"),
    ]


def test_embedded_pdf_uses_explicit_asset_url_and_host_allowlist():
    html = b'<main><iframe src="/guide.pdf#page=2"></iframe><object data="https://unapproved.test/private.pdf"></object></main>'
    assert linked_targets(html, "https://public.test/notice", {"public.test"}) == [("https://public.test/guide.pdf", "pdf")]


def test_actual_image_response_never_enters_html_parser(monkeypatch):
    class Client:
        allowed_hosts = {"public.test", "cdn.test"}
        def get(self, url):
            return FetchResult(url, url, 200, "image/png", {}, b"\x89PNG\r\n\x1a\nposter")
    monkeypatch.setattr("data_platform.linked.extract_document", lambda body, kind: "지원자격\n박사과정 재학생\n혜택\n등록금과 생활비를 지원하는 공개 선발 프로그램입니다.")
    monkeypatch.setattr("data_platform.linked._primary_notice", lambda *args: pytest.fail("image parsed as HTML"))
    html = '<main><a href="https://cdn.test/download?id=1">공식 안내</a></main>'.encode()
    updated = enrich_linked(record(), html, "https://public.test/notice", Client(), lambda fetched: 42)
    assert updated.evidence_sources[0]["kind"] == "image"
    assert updated.evidence_sources[0]["status"] == "ocr_review"
    assert updated.evidence_sources[0]["rawDocumentId"] == 42


def test_failed_attachment_retains_raw_id_but_next_fetch_failure_does_not(monkeypatch):
    class Client:
        allowed_hosts = {"public.test"}
        def get(self, url):
            if url.endswith("2.pdf"):
                raise RuntimeError("connection unavailable")
            return FetchResult(url, url, 200, "text/html", {}, b"<html>login required</html>")
    html = b'<main><a href="/1.pdf">1</a><a href="/2.pdf">2</a></main>'
    updated = enrich_linked(record(), html, "https://public.test/notice", Client(), lambda fetched: 42)
    assert updated.evidence_sources[0]["status"] == "failed"
    assert updated.evidence_sources[0]["rawDocumentId"] == 42
    assert "received HTML" in updated.evidence_sources[0]["error"]
    assert "rawDocumentId" not in updated.evidence_sources[1]


def hwpx(xml: bytes, compress=zipfile.ZIP_STORED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compress) as archive:
        archive.writestr("Contents/section0.xml", xml)
    return stream.getvalue()


def test_hwpx_xml_keeps_paragraph_boundaries_without_extracting_files():
    body = hwpx('''<section xmlns:hp="urn:hp"><hp:p><hp:run><hp:t>지원자격</hp:t></hp:run></hp:p>
    <hp:p><hp:run><hp:t>박사과정 재학생이 신청할 수 있습니다.</hp:t></hp:run></hp:p>
    <hp:p><hp:run><hp:t>혜택은 등록금과 연구 생활비 지원입니다.</hp:t></hp:run></hp:p></section>'''.encode())
    assert document_kind(body, "application/octet-stream") == "hwpx"
    assert extract_document(body, "hwpx").splitlines() == ["지원자격", "박사과정 재학생이 신청할 수 있습니다.", "혜택은 등록금과 연구 생활비 지원입니다."]


def test_hwpx_xml_entities_and_extreme_expansion_are_rejected():
    body = hwpx(b'<!DOCTYPE s [<!ENTITY x "unsafe">]><s><p><t>&x;</t></p></s>')
    with pytest.raises(ValueError, match="declarations"):
        extract_document(body, "hwpx")
    body = hwpx('<!DOCTYPE s [<!ENTITY x "unsafe">]><s><p><t>&x;</t></p></s>'.encode("utf-16"))
    with pytest.raises(ValueError, match="declarations"):
        extract_document(body, "hwpx")
    body = hwpx(b"<s>" + b" " * 1000000 + b"</s>", zipfile.ZIP_DEFLATED)
    with pytest.raises(ValueError, match="expansion"):
        extract_document(body, "hwpx")


def test_hwp_uses_installed_text_converter_and_fails_explicitly_if_unavailable(monkeypatch):
    monkeypatch.setattr("data_platform.linked.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="HWP text converter unavailable"):
        extract_document(b"hwp bytes", "hwp")


def test_hwp_text_converter_is_invoked_without_shell(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("data_platform.linked.shutil.which", lambda name: "/usr/bin/hwp5txt" if name == "hwp5txt" else None)
    def run(command, **kwargs):
        assert command[0] == "/usr/bin/hwp5txt"
        assert command[1].endswith("source.hwp")
        assert "shell" not in kwargs
        assert kwargs["timeout"] == 45
        return SimpleNamespace(stdout="지원자격은 박사과정 재학생입니다. 장학금으로 연구 생활비와 등록금을 지원합니다.".encode())
    monkeypatch.setattr("data_platform.linked.subprocess.run", run)
    assert "박사과정 재학생" in extract_document(b"hwp bytes", "hwp")


def test_ocr_explicit_and_system_commands_precede_local_configuration(monkeypatch, tmp_path):
    monkeypatch.setattr("data_platform.linked.DOCUMENT_TOOLS_CONFIG", tmp_path / "missing.json")
    monkeypatch.setattr("data_platform.linked.shutil.which", lambda name: "/usr/bin/tesseract")
    monkeypatch.setenv("BENEFITLENS_OCR_COMMAND", "/explicit/ocr")
    assert _ocr_runtime() == ("/explicit/ocr", None)
    monkeypatch.delenv("BENEFITLENS_OCR_COMMAND")
    assert _ocr_runtime() == ("/usr/bin/tesseract", None)


def test_ocr_local_config_is_validated_and_scoped_to_image_process(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import os
    monkeypatch.delenv("BENEFITLENS_OCR_COMMAND", raising=False)
    monkeypatch.setattr("data_platform.linked.shutil.which", lambda name: "/usr/bin/hwp5txt" if name == "hwp5txt" else None)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    binary = runtime / "tesseract"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o700)
    library = runtime / "lib"
    library.mkdir()
    tessdata = runtime / "tessdata"
    tessdata.mkdir()
    for language in ("eng", "kor"):
        (tessdata / f"{language}.traineddata").write_bytes(b"mock model")
    config = tmp_path / "document_tools.json"
    config.write_text(json.dumps({"ocr": {"command": str(binary), "library_path": str(library), "tessdata_prefix": str(tessdata)}}))
    monkeypatch.setattr("data_platform.linked.DOCUMENT_TOOLS_CONFIG", config)
    original_tessdata = os.environ.get("TESSDATA_PREFIX")
    original_library = os.environ.get("LD_LIBRARY_PATH")
    command, environment = _ocr_runtime()
    assert command == str(binary)
    assert environment["TESSDATA_PREFIX"] == str(tessdata)
    assert environment["LD_LIBRARY_PATH"].startswith(str(library))
    calls = []
    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(stdout="지원자격은 박사과정 재학생입니다. 장학금으로 연구 생활비와 등록금을 지원합니다.".encode())
    monkeypatch.setattr("data_platform.linked.subprocess.run", run)
    extract_document(b"image", "image")
    extract_document(b"pdf", "pdf")
    extract_document(b"hwp", "hwp")
    assert calls[0][1]["env"]["TESSDATA_PREFIX"] == environment["TESSDATA_PREFIX"]
    assert "env" not in calls[1][1]
    assert "env" not in calls[2][1]
    assert os.environ.get("TESSDATA_PREFIX") == original_tessdata
    assert os.environ.get("LD_LIBRARY_PATH") == original_library


def test_ocr_local_config_rejects_missing_or_relative_paths(monkeypatch, tmp_path):
    monkeypatch.delenv("BENEFITLENS_OCR_COMMAND", raising=False)
    monkeypatch.setattr("data_platform.linked.shutil.which", lambda name: None)
    config = tmp_path / "document_tools.json"
    monkeypatch.setattr("data_platform.linked.DOCUMENT_TOOLS_CONFIG", config)
    with pytest.raises(RuntimeError, match="OCR engine unavailable"):
        _ocr_runtime()
    config.write_text(json.dumps({"ocr": {"command": "relative/ocr"}}))
    with pytest.raises(RuntimeError, match="absolute local path"):
        _ocr_runtime()
    config.write_text(json.dumps({"ocr": {"command": str(tmp_path / "missing")}}))
    with pytest.raises(RuntimeError, match="invalid local OCR configuration"):
        _ocr_runtime()


def test_public_jsonld_article_supplies_client_rendered_notice_body():
    payload = {"@context": "https://schema.org", "@type": "NewsArticle", "headline": "연구 장학금 모집", "articleBody": "<p>지원자격</p><p>박사과정 재학생</p><p>지원내용</p><p>등록금과 생활비 지원, 선발 인원은 별도 공고에 따릅니다.</p>"}
    html = ('<body><main id="app"></main><script type="application/ld+json">' + json.dumps(payload, ensure_ascii=False) + '</script></body>').encode()
    updated, text = _primary_notice(record(), html, "https://public.test/detail")
    assert "박사과정 재학생" in updated.eligibility_rules
    assert "등록금과 생활비 지원" in text


def test_public_next_initial_state_requires_notice_context():
    payload = {"props": {"pageProps": {"article": {"title": "연구 장학금 모집", "content": "지원자격\n박사과정 재학생\n지원내용\n등록금과 연구 생활비를 지원하며 선발 인원은 별도 공고에 따릅니다."}}}}
    html = ('<body><div id="app"></div><script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload, ensure_ascii=False) + '</script></body>').encode()
    updated, _ = _primary_notice(record(), html, "https://public.test/detail")
    assert "박사과정 재학생" in updated.eligibility_rules
    unrelated = b'<body><main></main><script id="__NEXT_DATA__" type="application/json">{"profile":{"name":"User","description":"a long private-looking profile value unrelated to recruitment"}}</script></body>'
    with pytest.raises(ValueError, match="client-rendered detail unavailable"):
        _primary_notice(record(), unrelated, "https://public.test/detail")
