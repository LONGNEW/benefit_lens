from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, time, timezone, timedelta
from typing import Callable
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class ParsedOpportunity:
    canonical_key: str
    title: str
    summary: str
    official_url: str
    benefit_type: str
    benefit_description: str
    additional_benefits: list[dict[str, str]] = field(default_factory=list)
    eligibility_rules: list[str] = field(default_factory=list)
    required_documents: list[str] = field(default_factory=list)
    application_prompts: list[dict[str, object]] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)
    lifecycle_status: str = "unknown"
    application_url: str | None = None
    opens_at: str | None = None
    closes_at: str | None = None
    provider_name: str | None = None
    provider_kind: str | None = None
    provider_url: str | None = None
    record_kind: str = "notice"
    presentation: dict[str, list[str]] = field(default_factory=dict)
    evidence_sources: list[dict[str, object]] = field(default_factory=list)
    extraction_candidates: list[dict[str, object]] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        from .contract import CONTRACT_VERSION
        data = asdict(self)
        data["contract_version"] = CONTRACT_VERSION
        data["evidence_sources"] = [{key: value for key, value in source.items() if key != "rawDocumentId"} for source in self.evidence_sources]
        data.pop("extraction_candidates", None)
        for key in ("provider_name", "provider_kind", "provider_url"):
            if data[key] is None:
                data.pop(key)
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse(parser_name: str, body: bytes, source_url: str) -> list[ParsedOpportunity]:
    from .contract import prepare_record
    if parser_name == "postech_department_funding_document":
        from .school import parse_school_document
        return [prepare_record(record) for record in parse_school_document(body, source_url)]
    if parser_name == "postech_ax_funding":
        from .linked import extract_document
        from .school import _policy
        text = extract_document(body, "pdf")
        match = re.search(r"대학원 재정 지원제도의 기본 방향.*?(?=\n\s*12\s*\n|인공지능융합전공\s*\n\s*1\.)", text, re.S)
        if not match or "전일제 학생 기준" not in match[0]:
            raise ValueError("AX financial-support section changed")
        lines = [_text(x) for x in match[0].splitlines() if _text(x)]
        record = _policy("AX 전일제 대학원생 첫 학기 재정지원", source_url, ["첫 학기 대학에서 공표한 기준 장학금 이상 지급"], ["소속 학교: POSTECH", "소속 학과: 인공지능융합전공 [ax]", "석사 또는 박사과정 재학생", "전일제 대학원생", "대학원 현재 1학기"], ["두 번째 학기부터 개인 성과에 따라 지급액이 달라질 수 있음"], ["이중 수혜 허용, 개인별 지급 상한 없음. 정부정책에 따른 일부 제한 가능", "학과별 지급액과 실제 지급 방식은 별도, 확정 수혜액 아님"])
        return [prepare_record(replace(record, evidence_sources=[{"url": source_url, "kind": "pdf", "status": "collected", "label": "AX 공식 모집요강 11쪽 재정지원", "text": text, "sha256": hashlib.sha256(body).hexdigest()}], evidence={"financial_support": " ".join(lines)}))]
    if parser_name in {"postech_school_internal", "postech_cse_internal", "postech_semi_internal", "postech_department_funding"}:
        from .school import parse_school_page
        return [prepare_record(record) for record in parse_school_page(parser_name, body, source_url)]
    parser = PARSERS.get(parser_name)
    if parser is None:
        raise ValueError(f"unknown parser: {parser_name}")
    return [prepare_record(record) for record in parser(body, source_url)]


def enrich(
    parser_name: str,
    record: ParsedOpportunity,
    body: bytes,
    source_url: str,
) -> ParsedOpportunity:
    enricher = DETAIL_ENRICHERS.get(parser_name)
    if enricher is None:
        raise ValueError(f"unknown detail parser: {parser_name}")
    return enricher(record, body, source_url)


def _soup(body: bytes) -> BeautifulSoup:
    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return soup


def _text(value: str) -> str:
    return " ".join(value.replace("\u00b7", ", ").split())


def _visible_text(body: bytes) -> str:
    return _text(_soup(body).get_text(" ", strip=True))


def _infer_benefit_type(text: str) -> str:
    if re.search(r"임대|주거|월세|보증|이자|대출", text):
        return "housing_finance"
    if re.search(r"노트북|기기|장비|물품|식사|교통권|생필품|도시락", text):
        return "goods"
    if re.search(r"생활비|활동비|정착금|수당|장려금|지원금|포인트", text):
        return "cash"
    if re.search(r"등록금|장학금|교육비|시험료|학비", text):
        return "education_cost"
    if re.search(r"교육|특강|멘토링|상담|의료|법률|공간|돌봄|포트폴리오", text):
        return "service"
    if re.search(r"인턴|연구|창업|공모|네트워크|연수", text):
        return "opportunity"
    return "unknown"


def _bracketed_provider(title: str) -> tuple[str | None, str | None]:
    """Extract an explicit operator from titles such as ``[현대모비스] 모집``.

    A university board is the discovery source, not necessarily the organization
    operating the opportunity. Only an explicit leading bracket is trusted.
    """
    match = re.match(r"^\[([^\]]{2,60})\]\s*", title)
    if not match:
        return None, None
    name = _text(match.group(1)).strip()
    if re.search(r"진흥원|공단|공사|정부|부처|청$|원$", name):
        kind = "public_agency"
    elif re.search(r"^\(사\)|협회", name):
        kind = "association_nonprofit"
    elif "재단" in name:
        kind = "corporate_foundation"
    else:
        kind = "company"
    return name, kind


def _gov24_lifecycle(value: str) -> str:
    if re.search(r"상시|수시|연중", value):
        return "rolling"
    return "unknown"


def _gov24_condition_rules(condition: dict[str, object]) -> list[str]:
    labels = {
        "JA0101": "남성",
        "JA0102": "여성",
        "JA0201": "중위소득 0~50%",
        "JA0202": "중위소득 51~75%",
        "JA0203": "중위소득 76~100%",
        "JA0204": "중위소득 101~200%",
        "JA0205": "중위소득 200% 초과",
        "JA0301": "예비부모 또는 난임",
        "JA0302": "임산부",
        "JA0303": "출산 또는 입양",
        "JA0313": "농업인",
        "JA0314": "어업인",
        "JA0315": "축산업인",
        "JA0316": "임업인",
        "JA0317": "초등학생",
        "JA0318": "중학생",
        "JA0319": "고등학생",
        "JA0320": "대학생 또는 대학원생",
        "JA0326": "근로자 또는 직장인",
        "JA0327": "구직자 또는 실업자",
        "JA0328": "장애인",
        "JA0329": "국가보훈대상자",
        "JA0330": "질병 또는 질환자",
        "JA0401": "다문화가족",
        "JA0402": "북한이탈주민",
        "JA0403": "한부모가정 또는 조손가정",
        "JA0404": "1인가구",
        "JA0411": "다자녀가구",
        "JA0412": "무주택세대",
        "JA0413": "신규전입",
        "JA0414": "확대가족",
        "JA1101": "예비창업자",
        "JA1102": "영업중",
        "JA1103": "생계곤란 또는 폐업예정자",
        "JA2101": "중소기업",
        "JA2102": "사회복지시설",
        "JA2103": "기관 또는 단체",
    }
    rules: list[str] = []
    start = condition.get("JA0110")
    end = condition.get("JA0111")
    if start not in (None, "") or end not in (None, ""):
        rules.append(f"대상연령: {start or '제한 없음'}~{end or '제한 없음'}세")
    for code, label in labels.items():
        value = condition.get(code)
        if value not in (None, "", "N", "0", 0, False):
            rules.append(label)
    return rules


def parse_gov24_records(
    service_rows: list[dict[str, object]],
    detail_rows: list[dict[str, object]],
    condition_rows: list[dict[str, object]],
) -> list[ParsedOpportunity]:
    details = {
        str(row.get("서비스ID") or ""): row
        for row in detail_rows
        if row.get("서비스ID")
    }
    conditions = {
        str(row.get("서비스ID") or ""): row
        for row in condition_rows
        if row.get("서비스ID")
    }
    records: list[ParsedOpportunity] = []
    for row in service_rows:
        service_id = _text(str(row.get("서비스ID") or ""))
        title = _text(str(row.get("서비스명") or ""))
        if not service_id or not title:
            continue
        detail = details.get(service_id, {})
        condition = conditions.get(service_id, {})
        provider = _text(
            str(detail.get("소관기관명") or row.get("소관기관명") or "행정안전부")
        )
        summary = _text(
            str(
                detail.get("서비스목적")
                or row.get("서비스목적요약")
                or title
            )
        )
        benefit = _text(
            str(detail.get("지원내용") or row.get("지원내용") or "지원 내용 확인 필요")
        )
        target = _text(str(detail.get("지원대상") or row.get("지원대상") or ""))
        criteria = _text(str(detail.get("선정기준") or row.get("선정기준") or ""))
        deadline = _text(str(detail.get("신청기한") or row.get("신청기한") or ""))
        official_url = _text(
            str(
                detail.get("온라인신청사이트URL")
                or row.get("상세조회URL")
                or "https://www.gov.kr/portal/rcvfvrSvc/main"
            )
        )
        rules = [item for item in (target, criteria) if item]
        rules.extend(_gov24_condition_rules(condition))
        rules = list(dict.fromkeys(rules))
        support_type = _text(str(row.get("지원유형") or ""))
        records.append(
            ParsedOpportunity(
                canonical_key=f"gov24:{service_id}",
                title=title,
                summary=summary,
                official_url=official_url,
                application_url=_text(str(detail.get("온라인신청사이트URL") or "")) or None,
                benefit_type=_infer_benefit_type(
                    f"{title} {support_type} {benefit} {summary}"
                ),
                benefit_description=benefit,
                eligibility_rules=rules,
                evidence={
                    "title": title,
                    "summary": summary,
                    "benefit": benefit,
                    "eligibility": " ".join(rules),
                    "deadline": deadline,
                },
                lifecycle_status=_gov24_lifecycle(deadline),
                provider_name=provider,
                provider_kind="public_agency",
                provider_url=official_url,
            )
        )
    if not records:
        raise ValueError("Gov24 benefit records were not found")
    return records


def _ontong_provider_kind(name: str) -> str:
    if re.search(r"(특별시|광역시|특별자치시|특별자치도|도|시|군|구)$", name):
        return "local_government"
    if re.search(r"(부|처|청|위원회)$", name):
        return "central_government"
    return "public_agency"


def _ontong_period(
    value: str, period_code: str = ""
) -> tuple[str | None, str | None, str]:
    normalized = _text(value)
    if period_code == "0057002" or re.search(r"상시|수시|연중", normalized):
        return None, None, "rolling"
    if period_code == "0057003":
        return None, None, "closed"
    matches = re.findall(
        r"(20\d{2})[.\-/년 ]?\s*(\d{1,2})[.\-/월 ]?\s*(\d{1,2})",
        normalized,
    )
    if len(matches) < 2:
        compact = re.findall(r"(20\d{6})", normalized)
        matches = [
            (item[:4], item[4:6], item[6:8])
            for item in compact
        ]
    if len(matches) < 2:
        return None, None, "unknown"
    try:
        start = date(*map(int, matches[0]))
        end = date(*map(int, matches[1]))
    except ValueError:
        return None, None, "unknown"
    kst = timezone(timedelta(hours=9))
    today = datetime.now(kst).date()
    status = "upcoming" if today < start else "closed" if today > end else "open"
    return (
        datetime.combine(start, time.min, kst).isoformat(),
        datetime.combine(end, time(23, 59, 59), kst).isoformat(),
        status,
    )


def _ontong_eligibility_rules(row: dict[str, object]) -> list[str]:
    rules: list[str] = []
    minimum_age = _text(str(row.get("sprtTrgtMinAge") or ""))
    maximum_age = _text(str(row.get("sprtTrgtMaxAge") or ""))
    if row.get("sprtTrgtAgeLmtYn") != "N" and (
        minimum_age not in ("", "0") or maximum_age not in ("", "0")
    ):
        rules.append(
            f"대상연령: {minimum_age or '제한 없음'}~{maximum_age or '제한 없음'}세"
        )
    income = _text(str(row.get("earnEtcCn") or ""))
    if income:
        rules.append(f"소득조건: {income}")
    else:
        minimum_income = _text(str(row.get("earnMinAmt") or ""))
        maximum_income = _text(str(row.get("earnMaxAmt") or ""))
        if minimum_income or maximum_income:
            rules.append(
                f"소득금액: {minimum_income or '제한 없음'}~{maximum_income or '제한 없음'}"
            )
    fields = {
        "zipCd": "거주지역 코드",
        "schoolCd": "학력요건 코드",
        "plcyMajorCd": "전공요건 코드",
        "jobCd": "취업요건 코드",
        "sbizCd": "특화요건 코드",
        "mrgSttsCd": "혼인상태 코드",
    }
    for field_name, label in fields.items():
        value = _text(str(row.get(field_name) or ""))
        if value:
            rules.append(f"{label}: {value}")
    for field_name, label in (
        ("addAplyQlfcCndCn", "추가 신청자격"),
        ("ptcpPrpTrgtCn", "참여 제한대상"),
    ):
        value = _text(str(row.get(field_name) or ""))
        if value:
            rules.append(f"{label}: {value}")
    return list(dict.fromkeys(rules))


def parse_ontong_records(
    policy_rows: list[dict[str, object]],
) -> list[ParsedOpportunity]:
    records: list[ParsedOpportunity] = []
    for row in policy_rows:
        policy_id = _text(str(row.get("plcyNo") or ""))
        title = _text(str(row.get("plcyNm") or ""))
        if not policy_id or not title:
            continue
        summary = _text(str(row.get("plcyExplnCn") or title))
        benefit = _text(str(row.get("plcySprtCn") or summary or "지원 내용 확인 필요"))
        application_url = _text(str(row.get("aplyUrlAddr") or "")) or None
        official_url = (
            "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/"
            f"ythPlcyDetail/{policy_id}"
        )
        provider = _text(
            str(
                row.get("operInstCdNm")
                or row.get("sprvsnInstCdNm")
                or row.get("rgtrInstCdNm")
                or "한국고용정보원"
            )
        )
        rules = _ontong_eligibility_rules(row)
        period = _text(str(row.get("aplyYmd") or ""))
        period_code = _text(str(row.get("aplyPrdSeCd") or ""))
        opens_at, closes_at, lifecycle = _ontong_period(period, period_code)
        category = " ".join(
            _text(str(row.get(key) or ""))
            for key in ("lclsfNm", "mclsfNm", "plcyKywdNm")
        )
        records.append(
            ParsedOpportunity(
                canonical_key=f"ontong:{policy_id}",
                title=title,
                summary=summary,
                official_url=official_url,
                application_url=application_url,
                benefit_type=_infer_benefit_type(
                    f"{title} {category} {benefit} {summary}"
                ),
                benefit_description=benefit,
                eligibility_rules=rules,
                evidence={
                    "title": title,
                    "summary": summary,
                    "benefit": benefit,
                    "eligibility": " ".join(rules),
                    "deadline": _text(f"{period_code} {period}"),
                    "category": category,
                },
                lifecycle_status=lifecycle,
                opens_at=opens_at,
                closes_at=closes_at,
                provider_name=provider,
                provider_kind=_ontong_provider_kind(provider),
                provider_url=official_url,
            )
        )
    if not records:
        raise ValueError("Ontong youth policy records were not found")
    return records


def _slice_between(text: str, start: str, end: str, limit: int = 1200) -> str:
    start_pos = text.find(start)
    if start_pos < 0:
        return ""
    start_pos += len(start)
    end_pos = text.find(end, start_pos)
    if end_pos < 0:
        end_pos = min(len(text), start_pos + limit)
    return _text(text[start_pos:end_pos])[:limit]


def _json_string_parameter(raw: str, parameter_name: str) -> dict[str, object]:
    marker = f'"{parameter_name}":'
    pos = raw.find(marker)
    if pos < 0:
        raise ValueError(f"embedded parameter not found: {parameter_name}")
    decoder = json.JSONDecoder()
    encoded, _ = decoder.raw_decode(raw, pos + len(marker))
    if not isinstance(encoded, str):
        raise ValueError(f"embedded parameter is not a string: {parameter_name}")
    value = json.loads(encoded)
    if not isinstance(value, dict):
        raise ValueError(f"embedded parameter is not an object: {parameter_name}")
    return value


def _html_fragment_text(value: object) -> str:
    if not value:
        return ""
    return _text(BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True))


def _query_id(url: str, name: str) -> str:
    values = parse_qs(urlsplit(url).query).get(name, [])
    return _text(values[0]) if values else ""


def _element_lines(element: object) -> list[str]:
    if not hasattr(element, "get_text"):
        return []
    lines = [
        _text(line)
        for line in str(element.get_text("\n", strip=True)).splitlines()
        if _text(line)
    ]
    return list(dict.fromkeys(lines))


def _semantic_lines(element: object) -> list[str]:
    """Inline bold/span markup must not split dates, headings or rule sentences."""
    tags = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}
    blocks = [node for node in element.find_all(tags) if not any(parent.name in tags for parent in node.parents if parent is not element)]
    lines = [_text(node.get_text(" ", strip=True)) for node in blocks]
    return list(dict.fromkeys(line for line in lines if line)) if lines else _element_lines(element)


def _select_content_lines(soup: BeautifulSoup, selectors: tuple[str, ...]) -> list[str]:
    for selector in selectors:
        element = soup.select_one(selector)
        lines = _element_lines(element)
        if lines:
            return lines
    return []


def _matching_lines(
    lines: list[str], patterns: tuple[str, ...], *, following: int = 1
) -> list[str]:
    selected: list[str] = []
    for index, line in enumerate(lines):
        if not any(re.search(pattern, line, re.IGNORECASE) for pattern in patterns):
            continue
        selected.extend(lines[index : index + following + 1])
    return list(dict.fromkeys(selected))


def _section_lines(lines: list[str], headings: tuple[str, ...]) -> list[str]:
    """Keep the entire relevant section, never a fixed number of following lines."""
    result: list[str] = []
    active = False
    boundary = re.compile(r"^(?:[-*■●📌✅✏️🗓️ㅁㅇ\s]|\d+[.)]|[가-힣]\.)*\s*(?:지원자격|신청자격|지원대상|신청대상|모집대상|참여 대상|교육대상|대상:|우대사항|우대조건|가점|선발혜택|교육혜택|참여혜택|혜택|지원내용|시상 및 혜택|교육기간|교육장소|모집인원|신청방법|지원방법|선발규모|선발방법|선발절차|장학생 의무|활동의무|참여의무|모집일정|신청 기간|접수기간|신청 및 문의|선발 일정|제출서류|유의사항|문의사항|문의|활동기간|활동일정|주제|교육신청|프로그램 소개|관문별|참여 방법|구 분)", re.I)
    for line in lines:
        starts = any(re.search(pattern, line, re.I) for pattern in headings)
        if starts:
            active = True
        elif active and (re.search(boundary.pattern.replace(" ", ""), re.sub(r"\s+", "", line), re.I) or re.match(r"^(?:이전글|다음글|목록)(?:\s|$)", line) or line == "감사합니다."):
            active = False
        if active:
            result.append(line)
    return list(dict.fromkeys(result))


def _published_date(record: ParsedOpportunity) -> date | None:
    value = record.evidence.get("published_at", "")
    match = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", value)
    if not match:
        return None
    try:
        return date(*map(int, match.groups()))
    except ValueError:
        return None


def _full_dates(value: str) -> list[date]:
    results: list[date] = []
    pattern = re.compile(
        r"[’']?(?P<year>20\d{2}|\d{2})\s*[.\-/년]\s*"
        r"(?P<month>\d{1,2})\s*[.\-/월]\s*(?P<day>\d{1,2})"
    )
    for match in pattern.finditer(value):
        year = int(match.group("year"))
        if year < 100:
            year += 2000
        try:
            candidate = date(year, int(match.group("month")), int(match.group("day")))
        except ValueError:
            continue
        if candidate not in results:
            results.append(candidate)
    return results


def _partial_close_date(value: str, year: int) -> date | None:
    matches = re.findall(
        r"(?:~|～|까지|마감|접수)\s*\(?\s*(\d{1,2})\s*[./월]\s*(\d{1,2})",
        value,
    )
    if not matches:
        matches = re.findall(r"~\s*(\d{1,2})\s*[./]\s*(\d{1,2})", value)
    if not matches:
        return None
    try:
        month, day = map(int, matches[-1])
        return date(year, month, day)
    except ValueError:
        return None


def _partial_close_time(value: str) -> time | None:
    matches = re.findall(
        r"(?:~|～|까지|마감|접수)\s*\(?\s*(?:(?:20)?\d{2}\s*[.년/-]\s*)?\d{1,2}\s*[./월]\s*\d{1,2}"
        r"(?!\d)(?:\s*[.일])?(?:\([^)]*\))?\s+(\d{1,2})(?::(\d{2})(?:\s*시)?|\s*시)(?:까지)?",
        value,
    )
    if not matches:
        return None
    hour_text, minute_text = matches[-1]
    try:
        return time(int(hour_text), int(minute_text or 0))
    except ValueError:
        return None


def _application_window(
    lines: list[str], record: ParsedOpportunity
) -> tuple[str | None, str | None, str, str]:
    deadline_patterns = (
            r"모집\s*기간",
            r"신청\s*기간",
            r"지원\s*기간",
            r"접수\s*기간",
            r"신청\s*마감",
            r"접수\s*마감",
            r"온라인\s*지원",
            r"^[-*\s]*신청\s*:",
    )
    deadline_lines = []
    for index, line in enumerate(lines):
        if not any(re.search(pattern, line) for pattern in deadline_patterns):
            continue
        deadline_lines.append(re.split(r"(?:역량\s*검사|코딩\s*테스트|면접\s*전형)", line, maxsplit=1)[0])
        for following in lines[index + 1:index + 5]:
            if re.match(r"^(?:[-*■●\s]|\d+[.)])*\s*(?:교육\s*(?:기간|장소|대상)|지원\s*자격|혜택|제출\s*서류|선발|활동\s*기간|관문별|학습\s*지원|역량\s*검사|코딩\s*테스트|면접)", following):
                break
            deadline_lines.append(following)
    deadline_text = " ".join(deadline_lines)
    parsed_dates = _full_dates(deadline_text)
    published = _published_date(record)
    start = parsed_dates[0] if parsed_dates else None
    end = parsed_dates[1] if len(parsed_dates) > 1 else None
    reference_year = (published or datetime.now(timezone(timedelta(hours=9))).date()).year
    title_end = _partial_close_date(record.title, reference_year)
    if title_end and published and title_end < published:
        if (published - title_end).days > 180:
            title_end = title_end.replace(year=reference_year + 1)
    if title_end is not None:
        end = title_end
        if start is not None and start > end:
            start = None
    elif end is None:
        end = _partial_close_date(deadline_text, reference_year)
    if start is None and end is not None and published and published <= end:
        start = published
    kst = timezone(timedelta(hours=9))
    open_moment = datetime.combine(start, time.min, kst) if start else None
    close_clock = (_partial_close_time(record.title) if title_end else None) or _partial_close_time(deadline_text)
    if close_clock is None and record.closes_at and end:
        previous = datetime.fromisoformat(record.closes_at).astimezone(kst)
        if previous.date() == end:
            close_clock = previous.time()
    close_moment = (
        datetime.combine(end, close_clock or time(23, 59, 59), kst) if end else None
    )
    opens_at = open_moment.isoformat() if open_moment else None
    closes_at = close_moment.isoformat() if close_moment else None
    now = datetime.now(kst)
    if open_moment and now < open_moment:
        lifecycle = "upcoming"
    elif close_moment and now > close_moment:
        lifecycle = "closed"
    elif open_moment or close_moment:
        lifecycle = "open"
    else:
        lifecycle = record.lifecycle_status
    return opens_at, closes_at, lifecycle, deadline_text


def _attachment_names(element: object) -> list[str]:
    if not hasattr(element, "select"):
        return []
    names: list[str] = []
    for anchor in element.select("a"):
        name = _text(anchor.get_text(" ", strip=True))
        if re.search(r"\.(pdf|hwp|hwpx|docx?|xlsx?|zip)(?:\s|$|\()", name, re.I):
            names.append(name[:500])
    return list(dict.fromkeys(names))


def _application_link(element: object, source_url: str) -> str | None:
    if not hasattr(element, "select"):
        return None
    source_host = urlsplit(source_url).hostname
    for anchor in element.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:")):
            continue
        target = urljoin(source_url, href)
        target_host = urlsplit(target).hostname
        if target_host and target_host != source_host:
            return target
    return None


_WRITING_TOPIC = re.compile(
    r"지원\s*동기|자기\s*소개|학업\s*계획|활동\s*계획|연구\s*(?:계획|주제|목표)|"
    r"장래\s*계획|진로\s*계획|경험|성취|도전|역량|비전|사회\s*기여|서술\s*문항|"
    r"personal\s*statement|statement\s*of\s*purpose|research\s*plan", re.I
)


def is_application_prompt(text: str, *, allow_title: bool = False) -> bool:
    """Require an actual writing task, not a form's existence or a FAQ topic."""
    if not text or not _WRITING_TOPIC.search(text):
        return False
    if re.search(r"FAQ|자주\s*묻는|제출\s*서류|준비\s*서류|첨부\s*서류|작성\s*(?:유의|주의)\s*사항|다운로드|첨부\s*파일|양식\s*(?:참고|참조|다운|첨부)|제출\s*방법", text, re.I):
        return False
    if re.search(r"(?:제출|발급|준비|접수|양식|형식|분량|파일).*?(?:나요|습니까|가능|어떻게|무엇|언제|방법|확인)", text):
        return False
    # Information about a provider's test or selection procedure is a FAQ,
    # even when '역량' makes it look like a personal-statement topic. Questions
    # about the applicant's own experience ('면접 경험', '본인의 역량') retain
    # their different grammatical subject and continue to the writing checks.
    if re.search(
        r"(?:(?:AI\s*)?(?:역량|적성|인성|코딩)\s*(?:검사|테스트)|"
        r"면접|선발\s*(?:전형|절차)|심사\s*(?:전형|절차)|교육\s*일정|접수\s*일정)"
        r"(?:은|는|란|이|가|의\s*일정).*?(?:무엇|언제|어디|어떻게|진행|일정).*?(?:나요|습니까|\?)",
        text, re.I,
    ):
        return False
    writing_instruction = bool(re.search(
        r"(?:작성|서술|기술|설명|논하)(?:\s*(?:하|해|바랍니다|할\s*것))|"
        r"(?:작성|서술|기술|설명)\s*[.!]?\s*$|\b(?:describe|explain|discuss|write)\b", text, re.I
    ))
    if re.search(r"제출|첨부|구비|발급", text) and not writing_instruction:
        return False
    if writing_instruction:
        return True
    if re.search(r"(?:무엇|어떤|왜|어떻게|what|why|how).*?(?:입니까|인가요|나요|습니까|\?)|(?:무엇|어떤|왜|어떻게).*\?", text, re.I):
        return True
    # A stated limit or a numbered writing-item title is sufficient. File
    # names and a bare '자기소개서' in a document checklist are not item titles.
    if re.search(r"\d{2,5}\s*(?:자|글자|단어|words?)", text, re.I):
        return True
    if re.match(r"^\s*(?:문항|질문|항목|Q)?\s*\d+\s*[.)번:：]\s*", text, re.I):
        return not bool(re.search(r"(?:제출|첨부|필수|증명서|\.pdf|\.hwp|\.docx)", text, re.I))
    return allow_title and not bool(re.search(r"제출|첨부|필수|\.pdf|\.hwp|\.docx", text, re.I))


def is_application_question(text: str) -> bool:
    """Public strict predicate used when promoting a reviewed extraction."""
    return is_application_prompt(text)


def _application_prompts(lines: list[str]) -> list[dict[str, object]]:
    prompts: list[dict[str, object]] = []
    for line in lines:
        if not is_application_prompt(line):
            continue
        limit_match = re.search(r"(\d{2,5})\s*(?:자|글자)", line)
        prompts.append(
            {
                "prompt_key": f"prompt-{len(prompts) + 1}",
                "prompt_text": line[:2000],
                "character_limit": int(limit_match.group(1)) if limit_match else None,
                "required_elements": [],
            }
        )
    return prompts


def _enrich_notice(
    record: ParsedOpportunity,
    body: bytes,
    source_url: str,
    *,
    selectors: tuple[str, ...],
) -> ParsedOpportunity:
    from .contract import prepare_record, semantic_role
    soup = _soup(body)
    content = None
    for selector in selectors:
        content = soup.select_one(selector)
        if content is not None:
            break
    if content is None:
        raise ValueError("notice detail content was not found")
    lines = _semantic_lines(content)
    detail_text = " ".join(lines)
    has_detail_asset = bool(content.select("img, a[href]"))
    if len(detail_text) < 30 and not has_detail_asset:
        raise ValueError("notice detail content is too short")
    benefit_lines = _matching_lines(
        lines,
        (
            r"지원\s*내용",
            r"장학금",
            r"지원금",
            r"등록금",
            r"생활비",
            r"교육비",
            r"상금",
            r"혜택",
            r"학습\s*지원",
            r"유료\s*학습",
        ),
        following=1,
    )
    benefit_sections = _section_lines(lines, (r"^(?:[-*■●\s]|\d+[.)])*\s*(?:교육\s*혜택|선발\s*혜택|참여\s*혜택|혜택|지원\s*내용|학습\s*지원)\s*[:：]?",))
    if benefit_sections:
        benefit_lines = benefit_sections
    eligibility = _section_lines(
        lines,
        (
            r"지원\s*자격",
            r"신청\s*자격",
            r"신청\s*대상",
            r"모집\s*대상",
            r"지원\s*대상",
            r"대상\s*학과",
            r"참여\s*대상",
            r"교육\s*대상",
            r"참가\s*조건",
            r"신청\s*자격",
            r"^ㅁ\s*대상:",
            r"중간평가\s*대상자\s*:",
        ),
    )
    eligibility += [line for line in lines if re.search(r"지원\s*불가|대학원생\s*제외|휴학\s*상태.*가능", line)]
    preferences = _section_lines(lines, (r"^(?:[-*■●\s]|\d+[.)])*\s*(?:우대\s*사항|우대\s*조건|가점)\s*[:：]?",))
    preferences += [line for line in eligibility if semantic_role(line) == "preferences"]
    eligibility = [line for line in eligibility if line not in preferences]
    documents = list(dict.fromkeys([
        *record.required_documents,
        *_attachment_names(content),
        *_matching_lines(
            lines,
            (r"제출\s*서류", r"신청서", r"재학증명서", r"성적증명서", r"동의서"),
            following=1,
        ),
    ]))
    from .linked import merge_prompts
    prompts = merge_prompts(record.application_prompts, _application_prompts(lines), record.official_url, source_url)
    opens_at, closes_at, lifecycle, deadline = _application_window(lines, record)
    benefit = " ".join(benefit_lines)[:1200] or record.benefit_description
    rules = list(dict.fromkeys([*record.eligibility_rules, *eligibility]))
    if re.search(r"POSTECH\s*학생", record.title):
        rules = list(dict.fromkeys([*rules, "소속 학교: POSTECH"]))
    evidence = {
        **record.evidence,
        "benefit": benefit,
        "eligibility": " ".join(rules)[:4000],
    }
    if detail_text:
        evidence["detail"] = detail_text[:4000]
    if deadline:
        evidence["deadline"] = deadline[:1000]
    if documents:
        evidence["required_documents"] = " | ".join(documents)[:4000]
    return prepare_record(replace(
        record,
        summary=detail_text[:1500] or record.summary,
        application_url=_application_link(content, source_url) or record.application_url,
        benefit_type=_infer_benefit_type(f"{record.title} {benefit} {detail_text}"),
        benefit_description=benefit,
        eligibility_rules=rules,
        required_documents=documents,
        application_prompts=prompts,
        evidence=evidence,
        lifecycle_status=lifecycle,
        opens_at=opens_at or record.opens_at,
        closes_at=closes_at or record.closes_at,
        presentation={
            **record.presentation,
            "eligibility": rules,
            "preferences": list(dict.fromkeys([*record.presentation.get("preferences", []), *preferences])),
            "conditions": list(dict.fromkeys([*record.presentation.get("conditions", []), *_section_lines(lines, (r"선발\s*(방법|절차|과정)", r"제출\s*서류", r"참여\s*(조건|의무|방법)", r"활동\s*(내용|의무|일정|기간)", r"관문별\s*학습\s*미션", r"교육\s*(기간|장소)"))])),
            "benefits": benefit_lines if benefit_lines else [record.benefit_description] if record.benefit_description != record.title else [],
            "specialTerms": _matching_lines(lines, (r"중복", r"반환", r"의무", r"제외", r"유의\s*사항", r"제한", r"자[비]?\s*부담|본인\s*부담", r"미션을\s*달성하지\s*못", r"채용\s*(조건|연계)"), following=1),
            "period": [deadline] if deadline else [],
        },
        evidence_sources=[*record.evidence_sources, {"url": source_url, "kind": "html", "status": "collected", "label": "공식 상세 본문", "text": detail_text}],
    ))


def _dated_list_record(record: ParsedOpportunity) -> ParsedOpportunity:
    opens_at, closes_at, lifecycle, deadline = _application_window([], record)
    evidence = dict(record.evidence)
    if deadline:
        evidence["deadline"] = deadline
    return replace(
        record,
        opens_at=opens_at,
        closes_at=closes_at,
        lifecycle_status=lifecycle,
        evidence=evidence,
    )


def parse_postech_cse_notices(
    body: bytes, source_url: str
) -> list[ParsedOpportunity]:
    soup = _soup(body)
    records: dict[str, ParsedOpportunity] = {}
    for row in soup.select("table tr"):
        cells = [_text(cell.get_text(" ", strip=True)) for cell in row.select("td")]
        anchor = row.select_one("a[href*='articleNo=']")
        if not cells or anchor is None or "장학" not in cells:
            continue
        title = _text(anchor.get_text(" ", strip=True))
        detail_url = urljoin(source_url, str(anchor.get("href") or ""))
        article_no = _query_id(detail_url, "articleNo")
        published = next(
            (value for value in cells if re.fullmatch(r"20\d{2}[.\-/]\d{2}[.\-/]\d{2}", value)),
            "",
        )
        if not article_no or not title:
            continue
        key = f"postech-cse:{article_no}"
        provider_name, provider_kind = _bracketed_provider(title)
        records[key] = _dated_list_record(
            ParsedOpportunity(
                canonical_key=key,
                title=title,
                summary=title,
                official_url=detail_url,
                application_url=detail_url,
                benefit_type=_infer_benefit_type(title),
                benefit_description=title,
                evidence={"title": title, "published_at": published},
                provider_name=provider_name,
                provider_kind=provider_kind,
            )
        )
    if not records:
        raise ValueError("POSTECH CSE scholarship notices were not found")
    return list(records.values())


def parse_posco_notices(body: bytes, source_url: str) -> list[ParsedOpportunity]:
    soup = _soup(body)
    records: dict[str, ParsedOpportunity] = {}
    for row in soup.select("table tr"):
        cells = [_text(cell.get_text(" ", strip=True)) for cell in row.select("td")]
        anchor = row.select_one("a[href*='/news/notice/']")
        if not cells or anchor is None:
            continue
        title = _text(anchor.get_text(" ", strip=True))
        combined = " ".join(cells)
        if not re.search(r"장학|펠로십|fellowship", f"{title} {combined}", re.I):
            continue
        if re.search(r"수상|심사|결과\s*발표|재단\s*소식", title):
            continue
        detail_url = urljoin(source_url, str(anchor.get("href") or ""))
        match = re.search(r"/news/notice/(\d+)", urlsplit(detail_url).path)
        published = next(
            (value for value in cells if re.fullmatch(r"20\d{2}[.\-/]\d{2}[.\-/]\d{2}", value)),
            "",
        )
        if not match or not title:
            continue
        key = f"posco-foundation:notice:{match.group(1)}"
        records[key] = _dated_list_record(
            ParsedOpportunity(
                canonical_key=key,
                title=title,
                summary=title,
                official_url=detail_url,
                application_url=detail_url,
                benefit_type=_infer_benefit_type(title),
                benefit_description=title,
                evidence={"title": title, "published_at": published},
            )
        )
    if not records:
        raise ValueError("POSCO Foundation opportunity notices were not found")
    return list(records.values())


def parse_kosaf_notices(body: bytes, source_url: str) -> list[ParsedOpportunity]:
    soup = _soup(body)
    records: dict[str, ParsedOpportunity] = {}
    for row in soup.select("table tr"):
        cells = [_text(cell.get_text(" ", strip=True)) for cell in row.select("td")]
        anchor = row.select_one("a[href*='seqNo=']")
        if not cells or anchor is None:
            continue
        title = _text(anchor.get_text(" ", strip=True))
        if not re.search(r"장학|학자금|지원금", title):
            continue
        if re.search(
            r"결과|지급\s*일정|서류\s*제출|매뉴얼|오류|중단|환수|공시송달|근로기관",
            title,
        ):
            continue
        detail_url = urljoin(source_url, str(anchor.get("href") or ""))
        sequence = _query_id(detail_url, "seqNo")
        published = next(
            (value for value in cells if re.fullmatch(r"20\d{2}[.\-/]\d{2}[.\-/]\d{2}", value)),
            "",
        )
        if not sequence or not title:
            continue
        key = f"kosaf:notice:{sequence}"
        records[key] = _dated_list_record(
            ParsedOpportunity(
                canonical_key=key,
                title=title,
                summary=title,
                official_url=detail_url,
                application_url=detail_url,
                benefit_type=_infer_benefit_type(title),
                benefit_description=title,
                evidence={"title": title, "published_at": published},
            )
        )
    if not records:
        raise ValueError("KOSAF application notices were not found")
    return list(records.values())


def enrich_postech_cse_notice(
    record: ParsedOpportunity, body: bytes, source_url: str
) -> ParsedOpportunity:
    return _enrich_notice(record, body, source_url, selectors=(".board_view",))


def enrich_posco_notice(
    record: ParsedOpportunity, body: bytes, source_url: str
) -> ParsedOpportunity:
    return _enrich_notice(
        record,
        body,
        source_url,
        selectors=(".nxb-view__contents",),
    )


def enrich_kosaf_notice(
    record: ParsedOpportunity, body: bytes, source_url: str
) -> ParsedOpportunity:
    return _enrich_notice(record, body, source_url, selectors=("#VIEW_MCONTENT",))


def parse_bokjiro(body: bytes, source_url: str) -> list[ParsedOpportunity]:
    raw = body.decode("utf-8", errors="replace")
    data = _json_string_parameter(raw, "dmWlfareInfo")
    title = _text(str(data.get("wlfareInfoNm") or ""))
    if not title:
        raise ValueError("Bokjiro opportunity title is missing")
    summary = _text(str(data.get("wlfareInfoOutlCn") or ""))
    target = _html_fragment_text(data.get("wlfareSprtTrgtCn"))
    selection = _html_fragment_text(data.get("wlfareSprtTrgtSlcrCn"))
    benefit = _text(str(data.get("wlbzslTcdnm") or "지원 내용 확인 필요"))
    opportunity_id = str(data.get("wlfareInfoId") or title)
    return [
        ParsedOpportunity(
            canonical_key=f"bokjiro:{opportunity_id}",
            title=title,
            summary=summary,
            official_url=source_url,
            benefit_type=_infer_benefit_type(f"{title} {benefit} {summary}"),
            benefit_description=f"{benefit}: {summary}",
            eligibility_rules=[item for item in (target, selection) if item],
            evidence={
                "title": title,
                "benefit": f"{benefit}: {summary}",
                "eligibility": target or selection,
            },
        )
    ]


def parse_kosaf(body: bytes, source_url: str) -> list[ParsedOpportunity]:
    text = _visible_text(body)
    anchor = "국가장학금Ⅰ유형(학생직접지원형)"
    if anchor not in text:
        anchor = "국가장학금 I유형(학생직접지원형)"
    if anchor not in text:
        raise ValueError("KOSAF national scholarship anchor is missing")
    summary_match = re.search(r"소득수준에 연계[^.。]{10,220}", text)
    eligibility_match = re.search(
        r"대한민국 국적[^.。]{20,500}?학생", text
    )
    schedule_match = re.search(r"신청일정\s*:?\s*20\d{2}[^가-힣]{0,30}[가-힣0-9().~:\s]+18시", text)
    summary = _text(summary_match.group(0)) if summary_match else anchor
    eligibility = _text(eligibility_match.group(0)) if eligibility_match else "공식 지원자격 확인 필요"
    benefit = "소득수준과 등록금 범위에 따른 국가장학금"
    evidence = {"title": anchor, "benefit": benefit, "eligibility": eligibility}
    if schedule_match:
        evidence["schedule"] = _text(schedule_match.group(0))
    return [
        ParsedOpportunity(
            canonical_key="kosaf:national-scholarship-i",
            title=anchor,
            summary=summary,
            official_url=source_url,
            benefit_type="education_cost",
            benefit_description=benefit,
            eligibility_rules=[eligibility],
            evidence=evidence,
        )
    ]


def parse_postech(body: bytes, source_url: str) -> list[ParsedOpportunity]:
    soup = _soup(body)
    records: list[ParsedOpportunity] = []
    for row in soup.select("table tr"):
        cells = [_text(cell.get_text(" ", strip=True)) for cell in row.select("td")]
        if not cells:
            continue
        name_index = next(
            (index for index, cell in enumerate(cells[:2]) if "장학금" in cell),
            None,
        )
        if name_index is None:
            continue
        title = cells[name_index]
        if title.startswith("이 외") or len(title) > 60:
            continue
        remaining = cells[name_index + 1 :]
        benefit_index = next(
            (
                index
                for index, value in enumerate(remaining)
                if re.search(
                    r"등록금|만원|지원분야|장애 정도|단체에 따라|피해|전액|일부",
                    value,
                )
            ),
            None,
        )
        if benefit_index is None and title == "지역인재장학금":
            benefit = "등록금 지원"
            eligibility = remaining
        else:
            benefit_index = benefit_index if benefit_index is not None else 0
            benefit = remaining[benefit_index] if remaining else "지원 내용 확인 필요"
            eligibility = [
                value for index, value in enumerate(remaining) if index != benefit_index
            ]
        summary = " ".join(item for item in remaining if item)[:1000]
        key = re.sub(r"\s+", "-", title.strip().lower())
        records.append(
            ParsedOpportunity(
                canonical_key=f"postech:{key}",
                title=title,
                summary=summary,
                official_url=source_url,
                benefit_type=_infer_benefit_type(f"{title} {benefit}"),
                benefit_description=benefit,
                eligibility_rules=[item for item in eligibility if item],
                evidence={
                    "title": title,
                    "benefit": benefit,
                    "eligibility": " ".join(eligibility),
                },
            )
        )
    if not records:
        raise ValueError("POSTECH scholarship rows were not found")
    return records


def parse_posco(body: bytes, source_url: str) -> list[ParsedOpportunity]:
    text = _visible_text(body)
    title = "포스코한국유학장학"
    if title not in text:
        raise ValueError("POSCO fellowship anchor is missing")
    eligibility = _slice_between(text, "지원 자격", "선발인원")
    benefit = _slice_between(text, "장학혜택", "선발과정")
    return [
        ParsedOpportunity(
            canonical_key="posco-foundation:korea-fellowship",
            title=title,
            summary="글로벌 미래리더 육성을 위한 한국 유학 장학 프로그램",
            official_url=source_url,
            benefit_type="education_cost",
            benefit_description=benefit or "장학금, 생활비와 정착 지원",
            eligibility_rules=[eligibility] if eligibility else [],
            evidence={"title": title, "benefit": benefit, "eligibility": eligibility},
        )
    ]


def parse_samsung(body: bytes, source_url: str) -> list[ParsedOpportunity]:
    soup = _soup(body)
    records: list[ParsedOpportunity] = []
    for heading in soup.select("h3.tit-sub"):
        title = _text(heading.get_text(" ", strip=True))
        if "장학금" not in title:
            continue
        description_tag = heading.find_next("p")
        description = _text(description_tag.get_text(" ", strip=True)) if description_tag else title
        key = re.sub(r"\s+", "-", title.lower())
        records.append(
            ParsedOpportunity(
                canonical_key=f"samsung-dream:{key}",
                title=title,
                summary=description,
                official_url=source_url,
                benefit_type=_infer_benefit_type(f"{title} {description}"),
                benefit_description=description,
                evidence={"title": title, "benefit": description},
            )
        )
    if not records:
        raise ValueError("Samsung Dream scholarship programs were not found")
    return records


def parse_beautiful_fund(body: bytes, source_url: str) -> list[ParsedOpportunity]:
    soup = _soup(body)
    records: list[ParsedOpportunity] = []
    seen: set[str] = set()
    for anchor in soup.select("a"):
        title = _text(anchor.get_text(" ", strip=True))
        if not re.match(r"^20\d{2}\s", title):
            continue
        if title in seen:
            continue
        seen.add(title)
        href = anchor.get("href")
        official_url = urljoin(source_url, str(href)) if href else source_url
        key = hashlib.sha256(title.encode("utf-8")).hexdigest()[:20]
        closes_at: str | None = None
        if "연중 상시" in title or "수시접수" in title:
            lifecycle = "rolling"
        else:
            date_matches = re.findall(r"(20\d{2})[.]\s*(\d{1,2})[.]\s*(\d{1,2})", title)
            if date_matches:
                year, month, day = map(int, date_matches[-1])
                close_date = date(year, month, day)
                kst = timezone(timedelta(hours=9))
                closes_at = datetime.combine(close_date, time(23, 59, 59), kst).isoformat()
                lifecycle = "open" if close_date >= datetime.now(kst).date() else "closed"
            else:
                lifecycle = "unknown"
        records.append(
            ParsedOpportunity(
                canonical_key=f"beautiful-fund:{key}",
                title=title,
                summary=title,
                official_url=official_url,
                application_url=official_url,
                benefit_type=_infer_benefit_type(title),
                benefit_description=title,
                lifecycle_status=lifecycle,
                closes_at=closes_at,
                evidence={"title": title, "benefit": title},
            )
        )
    if not records:
        raise ValueError("Beautiful Foundation calls were not found")
    return records


def parse_sk_newschool(body: bytes, source_url: str) -> list[ParsedOpportunity]:
    text = _visible_text(body)
    title = "SK 뉴스쿨 2026년 신입생 모집"
    required = ["교육비 전액 무료", "최대 90만원 장학금", "고등학교 졸업"]
    missing = [item for item in required if item not in text]
    if missing:
        raise ValueError(f"SK New School anchors are missing: {missing}")
    eligibility = _slice_between(
        text, "고등학교 졸업 이상", "모집 인원", limit=1200
    )
    benefit = "전문 직업교육 과정 교육비 전액 무료"
    scholarship = "최대 90만원 장학금 지원"
    return [
        ParsedOpportunity(
            canonical_key="sk-happiness:newschool-2026",
            title=title,
            summary="청년 대상 실무 직업교육과 취업 연계 프로그램",
            official_url=source_url,
            benefit_type="service",
            benefit_description=benefit,
            additional_benefits=[
                {"benefit_type": "cash", "description": scholarship}
            ],
            eligibility_rules=[eligibility] if eligibility else [],
            evidence={
                "title": title,
                "benefit": f"{benefit}, {scholarship}",
                "eligibility": eligibility,
            },
            lifecycle_status="closed",
            closes_at="2025-12-14T23:59:59+09:00",
        )
    ]


PARSERS: dict[str, Callable[[bytes, str], list[ParsedOpportunity]]] = {
    "postech_cse_notice_list": parse_postech_cse_notices,
    "posco_notice_list": parse_posco_notices,
    "kosaf_notice_list": parse_kosaf_notices,
    "bokjiro_embedded_json": parse_bokjiro,
    "kosaf_national_scholarship": parse_kosaf,
    "postech_scholarship_table": parse_postech,
    "posco_korea_fellowship": parse_posco,
    "samsung_dream_programs": parse_samsung,
    "beautiful_fund_calls": parse_beautiful_fund,
    "sk_newschool_2026": parse_sk_newschool,
}


DETAIL_ENRICHERS: dict[
    str, Callable[[ParsedOpportunity, bytes, str], ParsedOpportunity]
] = {
    "postech_cse_notice_detail": enrich_postech_cse_notice,
    "posco_notice_detail": enrich_posco_notice,
    "kosaf_notice_detail": enrich_kosaf_notice,
}
