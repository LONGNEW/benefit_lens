import json
from pathlib import Path

from data_platform.parsers import (
    ParsedOpportunity,
    enrich,
    parse,
    parse_gov24_records,
    parse_ontong_records,
)


GOLD = Path(__file__).parent / "gold" / "notice_expectations.json"


def test_bokjiro_embedded_json_parser() -> None:
    payload = {
        "wlfareInfoId": "WLF1",
        "wlfareInfoNm": "청년 주거 지원",
        "wlfareInfoOutlCn": "청년에게 공공임대주택을 제공합니다.",
        "wlbzslTcdnm": "현물지급",
        "wlfareSprtTrgtCn": "<p>대학생과 청년</p>",
        "wlfareSprtTrgtSlcrCn": "<p>무주택자</p>",
    }
    encoded = json.dumps(json.dumps(payload, ensure_ascii=False), ensure_ascii=False)
    body = f'<script>initParameter({{"dmWlfareInfo":{encoded}}})</script>'.encode()
    records = parse("bokjiro_embedded_json", body, "https://example.test")
    assert len(records) == 1
    assert records[0].title == "청년 주거 지원"
    assert records[0].benefit_type == "housing_finance"
    assert "무주택자" in records[0].eligibility_rules


def test_postech_table_parser_extracts_multiple_rows() -> None:
    body = """
    <table>
      <tr><th>종류</th><th>지원 내역</th><th>요건</th></tr>
      <tr><td>지곡장학금</td><td>등록금 전액</td><td>3.0 이상</td></tr>
      <tr><td>생활비 장학금</td><td>매월 50만원</td><td>성적 무관</td></tr>
    </table>
    """.encode()
    records = parse(
        "postech_scholarship_table", body, "https://example.test/scholarship"
    )
    assert [record.title for record in records] == ["지곡장학금", "생활비 장학금"]
    assert records[0].benefit_type == "education_cost"


def test_beautiful_fund_parser_deduplicates_titles() -> None:
    body = """
    <a href="/a">2026 주거위기청년 지원사업 (연중 상시 접수)</a>
    <a href="/a">2026 주거위기청년 지원사업 (연중 상시 접수)</a>
    """.encode()
    records = parse("beautiful_fund_calls", body, "https://example.test/apply/")
    assert len(records) == 1
    assert records[0].lifecycle_status == "rolling"
    assert records[0].benefit_type == "housing_finance"


def test_postech_living_allowance_is_cash() -> None:
    body = """
    <table><tr><td>생활비 장학금</td><td>매월 50만원</td><td>성적 무관</td></tr></table>
    """.encode()
    record = parse("postech_scholarship_table", body, "https://example.test")[0]
    assert record.benefit_type == "cash"


def test_sk_newschool_keeps_service_and_cash_benefits() -> None:
    body = """
    <h1>SK 뉴스쿨</h1>
    <p>교육비 전액 무료, 최대 90만원 장학금까지 지원</p>
    <p>고등학교 졸업 이상 청년이라면 누구나 지원할 수 있습니다.</p>
    <h2>모집 인원</h2>
    """.encode()
    record = parse("sk_newschool_2026", body, "https://example.test") [0]
    assert record.benefit_type == "service"
    assert record.additional_benefits == [
        {"benefit_type": "cash", "description": "최대 90만원 장학금 지원"}
    ]


def test_gov24_records_merge_list_detail_and_conditions() -> None:
    rows = [
        {
            "서비스ID": "SVC-1",
            "서비스명": "청년 주거비 지원",
            "서비스목적요약": "청년의 주거비 부담 완화",
            "지원내용": "월세 지원금 지급",
            "소관기관명": "테스트시",
            "상세조회URL": "https://www.gov.kr/test/SVC-1",
        }
    ]
    details = [
        {
            "서비스ID": "SVC-1",
            "서비스명": "청년 주거비 지원",
            "서비스목적": "주거 안정",
            "지원내용": "월세 20만원 지원",
            "지원대상": "무주택 청년",
            "신청기한": "연중 신청",
            "소관기관명": "테스트시",
        }
    ]
    conditions = [
        {"서비스ID": "SVC-1", "JA0110": 19, "JA0111": 34, "JA0412": "Y"}
    ]
    record = parse_gov24_records(rows, details, conditions)[0]
    assert record.canonical_key == "gov24:SVC-1"
    assert record.provider_name == "테스트시"
    assert record.benefit_type == "housing_finance"
    assert record.lifecycle_status == "rolling"
    assert "대상연령: 19~34세" in record.eligibility_rules
    assert "무주택세대" in record.eligibility_rules


def test_ontong_records_map_policy_and_eligibility() -> None:
    rows = [
        {
            "plcyNo": "POLICY-1",
            "plcyNm": "청년 월세 지원",
            "plcyExplnCn": "청년의 주거비 부담을 줄입니다.",
            "plcySprtCn": "월 20만원의 월세 지원금을 지급합니다.",
            "lclsfNm": "주거",
            "mclsfNm": "주거비 지원",
            "sprvsnInstCdNm": "테스트시",
            "sprtTrgtAgeLmtYn": "Y",
            "sprtTrgtMinAge": "19",
            "sprtTrgtMaxAge": "34",
            "earnEtcCn": "중위소득 150% 이하",
            "zipCd": "47110",
            "aplyYmd": "20260101 ~ 20991231",
            "aplyUrlAddr": "https://example.test/apply",
        }
    ]
    record = parse_ontong_records(rows)[0]
    assert record.canonical_key == "ontong:POLICY-1"
    assert record.provider_name == "테스트시"
    assert record.provider_kind == "local_government"
    assert record.benefit_type == "housing_finance"
    assert record.lifecycle_status in {"upcoming", "open", "closed"}
    assert record.opens_at == "2026-01-01T00:00:00+09:00"
    assert "대상연령: 19~34세" in record.eligibility_rules
    assert "소득조건: 중위소득 150% 이하" in record.eligibility_rules


def test_ontong_period_code_maps_rolling_without_dates() -> None:
    record = parse_ontong_records(
        [
            {
                "plcyNo": "POLICY-ROLLING",
                "plcyNm": "청년 상시 상담",
                "plcySprtCn": "진로 상담을 제공합니다.",
                "aplyPrdSeCd": "0057002",
                "aplyYmd": "",
            }
        ]
    )[0]
    assert record.lifecycle_status == "rolling"


def test_notice_list_parsers_match_gold_identifiers() -> None:
    expected = json.loads(GOLD.read_text(encoding="utf-8"))
    postech = parse(
        "postech_cse_notice_list",
        """
        <table><tr>
          <td>117</td><td>장학</td>
          <td><a href="?mode=view&amp;articleNo=49671&amp;srCategoryId=320">[SK그룹] Pre-SKALA 참여자 모집 (~10/14 09시까지)</a></td>
          <td>2026.09.28</td><td>10</td>
        </tr></table>
        """.encode(),
        "https://cse.postech.ac.kr/csepostech/news/information.do?mode=list",
    )
    posco = parse(
        "posco_notice_list",
        """
        <table>
          <tr><td>공지</td><td><a href="/news/notice/281">2026 포스코사이언스펠로십 선발 (~5.31)</a></td><td>2026.05.01</td></tr>
          <tr><td>공지</td><td><a href="/news/notice/281">2026 포스코사이언스펠로십 선발 (~5.31)</a></td><td>2026.05.01</td></tr>
        </table>
        """.encode(),
        "https://www.postf.org/news/notice",
    )
    kosaf = parse(
        "kosaf_notice_list",
        """
        <table><tr><td>1</td><td><a href="/ko/notice.do?mode=view&amp;seqNo=9876">안동시 학자금대출 이자지원 신청 안내</a></td><td>2026.09.29</td></tr></table>
        """.encode(),
        "https://www.kosaf.go.kr/ko/notice.do",
    )
    actual = {
        "postech": [postech[0].canonical_key, postech[0].title],
        "posco": [posco[0].canonical_key, posco[0].title],
        "kosaf": [kosaf[0].canonical_key, kosaf[0].title],
    }
    assert actual == expected
    assert len(posco) == 1
    assert postech[0].provider_name == "SK그룹"
    assert postech[0].provider_kind == "company"


def test_postech_notice_keeps_discovery_source_separate_from_operator() -> None:
    records = parse(
        "postech_cse_notice_list",
        """
        <table>
          <tr><td>1</td><td>장학</td><td><a href="?articleNo=1">[한국인터넷진흥원] 교육생 모집</a></td><td>2026.09.28</td></tr>
          <tr><td>2</td><td>장학</td><td><a href="?articleNo=2">교내 장학금 모집</a></td><td>2026.09.28</td></tr>
        </table>
        """.encode(),
        "https://cse.postech.ac.kr/csepostech/news/information.do",
    )
    assert records[0].provider_name == "한국인터넷진흥원"
    assert records[0].provider_kind == "public_agency"
    assert records[1].provider_name is None
    assert records[1].provider_kind is None


def test_notice_detail_enrichment_extracts_evidence_and_deadline() -> None:
    base = ParsedOpportunity(
        canonical_key="postech-cse:49671",
        title="[SK그룹] Pre-SKALA 참여자 모집 (~10/14 09시까지)",
        summary="모집",
        official_url="https://cse.postech.ac.kr/detail",
        application_url=None,
        benefit_type="unknown",
        benefit_description="확인 필요",
        evidence={"published_at": "2026.09.28"},
    )
    body = """
    <div class="board_view">
      <p>모집기간: 2026.09.28 ~ 2026.10.14</p>
      <p>지원 자격: 포항공대 학부생, 학점 3.0 이상</p>
      <p>혜택: 교육비 전액 지원</p>
      <p>자기소개와 지원동기를 500자로 작성</p>
      <a href="/files/application.hwp">지원서.hwp</a>
      <a href="https://apply.example.org/form">신청</a>
    </div>
    """.encode()
    record = enrich(
        "postech_cse_notice_detail",
        base,
        body,
        "https://cse.postech.ac.kr/detail",
    )
    assert record.lifecycle_status in {"upcoming", "open", "closed"}
    assert record.opens_at == "2026-09-28T00:00:00+09:00"
    assert record.closes_at == "2026-10-14T09:00:00+09:00"
    assert any("학점 3.0" in rule for rule in record.eligibility_rules)
    assert "지원서.hwp" in record.required_documents
    assert record.application_url == "https://apply.example.org/form"
    assert record.application_prompts[0]["character_limit"] == 500


def test_notice_title_deadline_wins_over_following_training_period() -> None:
    base = ParsedOpportunity(
        canonical_key="postech-cse:training-period",
        title="임베디드SW 스쿨 모집 (~9/28)",
        summary="모집",
        official_url="https://cse.postech.ac.kr/detail",
        benefit_type="service",
        benefit_description="직무 교육",
        evidence={"published_at": "2026.09.16"},
    )
    body = """
    <div class="board_view">
      <p>모집기간: 홈페이지 참조</p>
      <p>교육기간: 2026.10.20 ~ 2027.04.16</p>
    </div>
    """.encode()
    record = enrich(
        "postech_cse_notice_detail",
        base,
        body,
        "https://cse.postech.ac.kr/detail",
    )
    assert record.opens_at == "2026-09-16T00:00:00+09:00"
    assert record.closes_at == "2026-09-28T23:59:59+09:00"
    assert record.lifecycle_status == "closed"


def test_fingerprint_is_stable_but_changes_with_content() -> None:
    base = ParsedOpportunity(
        canonical_key="source:1",
        title="기회",
        summary="설명",
        official_url="https://example.test/1",
        benefit_type="cash",
        benefit_description="100만원",
    )
    same = ParsedOpportunity(**base.__dict__)
    changed = ParsedOpportunity(**{**base.__dict__, "closes_at": "2026-12-31T23:59:59+09:00"})
    assert base.fingerprint == same.fingerprint
    assert base.fingerprint != changed.fingerprint
