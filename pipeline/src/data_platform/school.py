"""Discover public POSTECH funding pages from the university's official directory.

Bounded HTML traversal is an inventory, not proof that a department has no support.
Authentication walls, image-only notices and unvisited pages remain coverage gaps.
"""
from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urldefrag

from bs4 import BeautifulSoup

from .http import SafeHttpClient, document_kind
from .parsers import ParsedOpportunity, _soup, _text, _infer_benefit_type

DIRECTORY = "https://www.postech.ac.kr/kor/education/department.do"
FUNDING = re.compile(r"장학|재정지원|재정\s*지원|financial\s*(aid|support)|scholarship|fellowship", re.I)
BRIDGE = re.compile(r"입학|admission|학생지원|student\s*support", re.I)

# Some department sites keep an older policy page beside the current page.
# Collection uses the current page as the canonical source, while PostgreSQL
# keeps the older raw documents for audit history.
SCHOOL_URL_ALIASES = {
    "https://gift.postech.ac.kr/sub4_2.php":
        "https://gift.postech.ac.kr/sub4_2_a.php",
}


def canonical_school_url(url: str) -> str:
    return SCHOOL_URL_ALIASES.get(url, url)


def directory_entries(body: bytes, url: str) -> list[dict[str, str]]:
    entries: dict[tuple[str, str], dict[str, str]] = {}
    soup = _soup(body)
    for item in soup.select("li"):
        name, anchor = item.select_one(".depart-title"), item.select_one("a.homepage[href]")
        if not name or not anchor:
            continue
        homepage = urljoin(url, str(anchor["href"]))
        host = urlsplit(homepage).hostname or ""
        if urlsplit(homepage).scheme not in {"http", "https"} or not host.endswith(".postech.ac.kr"):
            continue
        # Official legacy HTTP links are attempted over TLS, never downgrade.
        homepage = "https:" + homepage.split(":", 1)[1]
        title = _text(name.get_text(" ", strip=True))
        entries[(title, homepage)] = {"department": title, "homepage": homepage, "code": host.split(".")[0]}
    if not entries:
        raise ValueError("official department directory changed")
    return list(entries.values())


def funding_links(body: bytes, url: str, host: str, include_bridge: bool = False) -> list[str]:
    links: list[tuple[int, str]] = []
    soup = _soup(body)
    page_funding = bool(FUNDING.search(urlsplit(url).path) or any(FUNDING.search(node.get_text()) for node in soup.select("h1,h2,h3")))
    for anchor in soup.select("a[href]"):
        target = canonical_school_url(
            urldefrag(urljoin(url, str(anchor["href"])))[0]
        )
        text = _text(anchor.get_text(" ", strip=True))
        parts = urlsplit(target)
        if parts.scheme != "https" or parts.hostname != host or target == url:
            continue
        if re.search(r"login|logout|signin|(?:^|/)write(?:[./?]|$)|\.zip(?:[?&]|$)", target, re.I):
            continue
        document = bool(re.search(r"\.(?:pdf|hwpx|hwp)(?:[?&\s]|$)", target + " " + text, re.I))
        if FUNDING.search(text) or FUNDING.search(parts.path) or (document and page_funding):
            links.append((0, target))
        elif include_bridge and BRIDGE.search(text):
            links.append((1, target))
    return list(dict.fromkeys(target for _, target in sorted(links)))


def discover_school_sources(http: dict, output: Path) -> list[dict]:
    def client(hosts: set[str]) -> SafeHttpClient:
        return SafeHttpClient(allowed_hosts=hosts, user_agent=str(http["user_agent"]), timeout_seconds=min(15, int(http["timeout_seconds"])), max_body_bytes=int(http["max_body_bytes"]))

    entries = directory_entries(client({"www.postech.ac.kr"}).get(DIRECTORY).body, DIRECTORY)

    def inspect(entry: dict[str, str]) -> tuple[dict, list[dict]]:
        host = urlsplit(entry["homepage"]).hostname or ""
        fetcher = client({host})
        queue = [(entry["homepage"], 0)]
        visited: set[str] = set()
        sources: list[dict] = []
        failures: list[dict[str, str]] = []
        pages: list[str] = []
        # Homepage + up to five explicitly linked pages, at most two links deep.
        while queue and len(visited) < 6:
            url, depth = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                fetched = fetcher.get(url)
                pages.append(fetched.final_url)
                kind = document_kind(fetched.body, fetched.content_type, fetched.final_url, fetched.headers)
                if kind in {"pdf", "hwp", "hwpx"}:
                    if parse_school_document(fetched.body, fetched.final_url):
                        identity = hashlib.sha256(url.encode()).hexdigest()[:16]
                        sources.append({
                            "source_id": f"postech-internal-{entry['code']}-{identity}",
                            "provider_name": "POSTECH", "provider_kind": "university",
                            "source_group": "school", "source_kind": "official_page",
                            "url": url, "parser": "postech_department_funding_document",
                            "scope_department": entry["department"], "scope_code": entry["code"],
                            "rights_status": "review_required", "complete_snapshot": False,
                            "enabled": True, "contract_version": "3",
                        })
                    continue
                if kind != "html":
                    raise ValueError(f"non-HTML funding document requires extraction: {kind}")
                soup = _soup(fetched.body)
                heading = " ".join(x.get_text(" ", strip=True) for x in soup.select("h1,h2,h3,.title-txt,.sub-tit,.hide_tit"))
                parser = "postech_department_funding"
                if host == "cse.postech.ac.kr" and "scholarship.do" in url:
                    parser = "postech_cse_internal"
                elif host == "semi.postech.ac.kr" and "scholarship.do" in url:
                    parser = "postech_semi_internal"
                # Navigation alone is not evidence of a funding program.
                is_listing = bool(soup.select("table a[href*='articleNo'], table a[href*='wr_id']"))
                is_notice = bool(soup.select(".board_view, #bo_v, #VIEW_MCONTENT")) or "mode=view" in url or "wr_id=" in url
                if depth > 0 and not is_listing and not is_notice and (FUNDING.search(heading) or FUNDING.search(urlsplit(url).path)):
                    try:
                        if parse_school_page(parser, fetched.body, fetched.final_url):
                            identity = hashlib.sha256(url.encode()).hexdigest()[:16]
                            sources.append({
                                "source_id": f"postech-internal-{entry['code']}-{identity}",
                                "provider_name": "POSTECH", "provider_kind": "university",
                                "source_group": "school", "source_kind": "official_page",
                                "url": url, "parser": parser, "scope_department": entry["department"],
                                "scope_code": entry["code"], "rights_status": "review_required",
                                "complete_snapshot": False, "enabled": True, "contract_version": "3",
                            })
                    except ValueError:
                        failures.append({"url": url, "reason": "funding content requires a dedicated parser or non-HTML extraction"})
                if depth < 2:
                    links = funding_links(fetched.body, fetched.final_url, host, include_bridge=depth == 0)
                    queue.extend((link, depth + 1) for link in links if link not in visited)
                    queue.sort(key=lambda item: (not bool(FUNDING.search(item[0])), item[1]))
            except Exception as error:
                failures.append({"url": url, "reason": str(error)[:300]})
        return {
            **entry, "status": "public_funding_found" if sources else "access_failed" if not pages else "not_found_in_bounded_scan",
            "visited": pages, "fundingPages": [x["url"] for x in sources], "failures": failures,
            "bounded": True, "unvisitedLinks": len(queue),
        }, sources

    with ThreadPoolExecutor(max_workers=4) as pool:
        findings = list(pool.map(inspect, entries))
    sources = {source["source_id"]: source for _, items in findings for source in items}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"directory": DIRECTORY, "checkedAt": datetime.now(timezone.utc).isoformat(), "departments": [item for item, _ in findings], "limits": "Public HTML and explicitly linked PDF/HWP/HWPX only, 6 documents per department, depth 2. Unvisited or unsupported sources remain coverage gaps."}, ensure_ascii=False, indent=2), encoding="utf-8")
    return list(sources.values())


def _policy(title: str, source_url: str, benefits: list[str], eligibility: list[str] | None, conditions: list[str] | None = None, terms: list[str] | None = None) -> ParsedOpportunity:
    source_url = canonical_school_url(source_url)
    key = hashlib.sha256(f"{source_url}:{title}".encode()).hexdigest()[:24]
    eligibility = eligibility if eligibility is not None else ["세부 지원자격은 학과 공지 확인 필요"]
    evidence = {"title": title, "benefit": " | ".join(benefits), "eligibility": " | ".join(eligibility)}
    benefit_type = _infer_benefit_type(" ".join(benefits))
    if benefit_type == "unknown" and any(re.search(r"\d[\d,~\-]*\s*만?원", line) for line in benefits):
        benefit_type = "cash"
    return ParsedOpportunity(
        canonical_key=f"postech-policy:{key}", title=title, summary="교내 지원제도 안내. 모집 회차와 기간은 별도 확인해야 합니다.",
        official_url=source_url, benefit_type=benefit_type,
        benefit_description=" / ".join(benefits) or "지원 내용 확인 필요",
        eligibility_rules=eligibility,
        evidence=evidence, record_kind="policy", lifecycle_status="unknown",
        presentation={"eligibility": eligibility, "conditions": conditions or [], "benefits": benefits, "specialTerms": terms or [], "period": ["제도 안내, 모집 기간 별도 확인"]},
    )


def parse_central(body: bytes, source_url: str) -> list[ParsedOpportunity]:
    records: list[ParsedOpportunity] = []
    for table in _soup(body).select("table"):
        caption = table.select_one("caption")
        label = caption.get_text(" ", strip=True) if caption else ""
        if "국가장학금" in label:
            continue  # National programs have their own official collection sources.
        carries: dict[int, tuple[int, str]] = {}
        for row in table.select("tbody tr"):
            cells: dict[int, str] = {col: value for col, (_, value) in carries.items()}
            next_carries = {col: (remaining - 1, value) for col, (remaining, value) in carries.items() if remaining > 1}
            col = 0
            direct = row.find_all(["td", "th"], recursive=False)
            if not direct or (len(direct) == 1 and int(direct[0].get("colspan", 1)) > 1):
                carries = next_carries
                continue
            for cell in direct:
                while col in cells:
                    col += 1
                value = _text(cell.get_text(" ", strip=True))
                span, rows = int(cell.get("colspan", 1)), int(cell.get("rowspan", 1))
                for offset in range(span):
                    cells[col + offset] = value
                    if rows > 1:
                        next_carries[col + offset] = (rows - 1, value)
                col += span
            carries = next_carries
            grouped = "등록금 외" in label and "교내장학금" in label
            name_col = 1 if grouped else 0
            title, benefit = cells.get(name_col, ""), cells.get(name_col + 1, "")
            if not title or not benefit:
                continue
            requirements = list(dict.fromkeys(cells[k] for k in sorted(cells) if k > name_col + 1 and cells[k]))
            scope = ["소속 학교: POSTECH"]
            if "외국인학생" in label:
                scope += ["외국 국적 학부생"]
            elif "등록금 지원" in label:
                scope += ["학부 재학생"]
            elif not any("대학원" in text for text in requirements):
                scope += ["학부 재학생"]
            records.append(_policy(title, source_url, [benefit], scope + requirements, terms=["최신 선발 공지 우선, 자동 수혜 확정 아님"]))
    if not records:
        raise ValueError("central scholarship table changed")
    return records


def parse_cse(body: bytes, source_url: str) -> list[ParsedOpportunity]:
    records: list[ParsedOpportunity] = []
    for box in _soup(body).select(".list-box"):
        heading = box.select_one(".title-txt")
        if not heading:
            continue
        fields: dict[str, list[str]] = {}
        for li in box.select("li"):
            label, content = li.find("span", recursive=False), li.find("p", recursive=False)
            if label and content:
                fields.setdefault(_text(label.get_text()), []).append(_text(content.get_text(" ", strip=True)))
        title = _text(heading.get_text())
        rules = [x for k, values in fields.items() if re.search(r"대상|자격", k) for x in values]
        benefits = [x for k, values in fields.items() if re.search(r"금액|혜택", k) for x in values]
        conditions = [f"{k}: {x}" for k, values in fields.items() if re.search(r"선발|사용기간", k) for x in values]
        records.append(_policy(title, source_url, benefits, rules, conditions, ["학과 선발 필요, 별도 모집 기간 미확인"]))
    if not records:
        raise ValueError("CSE scholarship cards changed")
    return records


def parse_semi(body: bytes, source_url: str) -> list[ParsedOpportunity]:
    records: list[ParsedOpportunity] = []
    for heading in _soup(body).select(".hide_tit"):
        box = heading.parent
        title = _text(heading.get_text())
        lines = list(dict.fromkeys(_text(x.get_text(" ", strip=True)) for x in box.select("li")))
        if not lines:
            lines = [_text(x.get_text(" ", strip=True)) for x in box.select("p") if x != heading]
        terms = [_text(x.get_text(" ", strip=True)) for x in box.select(".mark")]
        if title == "기본장학금, 삼성대여장학금":
            for name, years, text in (("기본장학금", "1~2학년 학부생", lines[0]), ("삼성대여장학금", "3~4학년 학부생", lines[1])):
                records.append(_policy(name, source_url, ["등록금과 기숙사비 지원, 기숙사비 최대 100만원/학기"], [years], ["삼성전자 대여 장학생 선발 시험 합격 필요"] if name.startswith("삼성") else [], terms))
        else:
            terms += ["원문에 연간 720만원과 월 90~125만원이 함께 표기되어 지급액 확인 필요"] if title == "특별장학금" else []
            records.append(_policy(title, source_url, lines, ["반도체공학과 학생, 상세 선발 기준 확인 필요"], [], terms))
    if not records:
        raise ValueError("semiconductor scholarship cards changed")
    return records


def parse_mse(body: bytes, source_url: str) -> list[ParsedOpportunity]:
    soup = _soup(body)
    content = soup.select_one(".text-content")
    if not content:
        raise ValueError("MSE content missing")
    lines = [_text(x) for x in content.get_text("\n", strip=True).splitlines() if _text(x)]
    records: list[ParsedOpportunity] = []
    for index, line in enumerate(lines):
        name = re.fullmatch(r"[가-바]\.\s*(.+?장학금)\s*\(([^)]+)\)", line)
        if not name:
            continue
        requirements = ["학부 재학생", lines[index + 1].lstrip(": ") if index + 1 < len(lines) else "선발 기준 확인 필요"]
        terms = ["학과 심사와 선발 필요, 별도 모집 기간 미확인"]
        if re.search(r"성적|학업성취", name[1]):
            requirements += ["해당학기 이수학점 15학점 이상", "시상식 개최일 현재 졸업생과 수료자 제외"]
        records.append(_policy(name[1], source_url, [name[2]], requirements, ["학과별 성적 또는 생활 지원 심사"], terms))
    if not records:
        raise ValueError("MSE department awards changed")
    return records


def parse_generic(body: bytes, source_url: str) -> list[ParsedOpportunity]:
    soup = _soup(body)
    content = next((soup.select_one(sel) for sel in ("#cms-content", "#jwxe_main_content", ".board_view", ".text-content", ".s_body", ".sub-contents", ".sub_cont", ".contents", "main", "article") if soup.select_one(sel)), None)
    if content is None:
        raise ValueError("no semantic content container; manual parser needed")
    for item in content.select("nav,header,footer,.menu,.gnb,.lnb,.breadcrumb"):
        item.decompose()
    lines = list(dict.fromkeys(_text(x) for x in content.get_text("\n", strip=True).splitlines() if _text(x)))
    substantive = [x for x in lines if FUNDING.search(x) and (len(x) > 35 or re.search(r"만원|원/|등록금|tuition|stipend|KRW", x, re.I))]
    if not substantive:
        raise ValueError("no textual funding details, navigation is not a program")
    title = next((x.get_text(" ", strip=True) for x in content.select("h1,h2,h3") if FUNDING.search(x.get_text())), "학과 재정지원 안내")
    # No guesses about a round or eligibility from generic descriptive pages.
    benefits = [x for x in lines if re.search(r"만원|등록금.*(전액|지원|보전)|생활비|stipend|tuition", x, re.I)][:10] or substantive[:8]
    eligibility = [
        x for x in lines
        if re.search(r"수혜\s*대상이\s*아님|지원\s*대상|지원\s*자격", x)
        and len(x) > 12
    ][:8]
    terms = [
        x for x in lines
        if x not in eligibility
        and re.search(r"중복|이중수혜|제외|반환|변동|재직자|규정|변경", x)
    ][:8]
    record = _policy(
        title[:100],
        source_url,
        benefits,
        eligibility,
        conditions=[x for x in lines if re.search(r"선발|조교|성과|참여", x)][:6],
        terms=terms,
    )
    return [record]


def parse_graduate(body: bytes, source_url: str) -> list[ParsedOpportunity]:
    records: list[ParsedOpportunity] = []
    for table in _soup(body).select("table"):
        headers = [x.get_text(" ", strip=True) for x in table.select("thead th")]
        for row in table.select("tbody tr"):
            cells = row.find_all("td", recursive=False)
            if "등록금 보전" in headers and len(cells) == 4:
                degree = _text(cells[0].get_text())
                benefits = [f"{headers[index]} {cells[index].get_text(strip=True)}원/월" for index in (1, 2, 3)]
                records.append(_policy(f"대학원 학생조교수당 {degree}", source_url, benefits, ["소속 학교: POSTECH", degree + " 재학생"], ["교육, 연구 또는 행정업무 참여에 따른 조교수당"], ["2026년도 기준금액은 가이드라인, 교수 재량과 학과에 따라 달라질 수 있음", "첫 학기 이후 개인 성과에 따라 지급액 변동 가능"]))
            elif "장학금명(재단)" in headers and len(cells) == 3:
                title = _text(cells[0].get_text())
                qualifications = [_text(x.get_text()) for x in cells[1].select("li")]
                details = [_text(x.get_text()) for x in cells[2].select("li")]
                eligibility = [x for x in qualifications if not re.search(r"모집|선발인원", x)]
                conditions = [x for x in qualifications if re.search(r"모집|선발인원", x)]
                benefits = [x for x in details if re.search(r"지원금액|장학기간", x)]
                terms = [x for x in details if re.search(r"정주|반환|의무|중복", x)]
                records.append(_policy(title.replace("\u00b7", ", "), source_url, benefits, ["소속 학교: POSTECH"] + eligibility, conditions, terms))
    if not records:
        raise ValueError("graduate financial tables changed")
    return records


def parse_school_page(parser: str, body: bytes, source_url: str) -> list[ParsedOpportunity]:
    if "adm-g.postech.ac.kr/graduate-info/support-policy/" in source_url:
        return parse_graduate(body, source_url)
    if "mse.postech.ac.kr" in source_url and source_url.endswith("/scholarship.php"):
        return parse_mse(body, source_url)
    return {"postech_school_internal": parse_central, "postech_cse_internal": parse_cse, "postech_semi_internal": parse_semi, "postech_department_funding": parse_generic}[parser](body, source_url)


def parse_school_document(body: bytes, source_url: str) -> list[ParsedOpportunity]:
    """Extract a public attachment before applying the generic school policy rules."""
    from dataclasses import replace
    from .linked import extract_document
    kind = document_kind(body, url=source_url)
    if kind not in {"pdf", "hwp", "hwpx"}:
        raise ValueError(f"expected school funding PDF/HWP/HWPX, received {kind}")
    text = extract_document(body, kind)
    fragment = BeautifulSoup("<main></main>", "html.parser")
    lines = [_text(line) for line in text.splitlines() if _text(line)]
    for index, line in enumerate(lines):
        node = fragment.new_tag("h2" if index == 0 and FUNDING.search(line) else "p")
        node.string = line
        fragment.main.append(node)
    records = parse_generic(str(fragment).encode("utf-8"), source_url)
    evidence = {"url": source_url, "kind": kind, "status": "collected", "label": f"학교 공식 첨부 {kind.upper()}", "text": text, "sha256": hashlib.sha256(body).hexdigest()}
    return [replace(record, evidence_sources=[evidence]) for record in records]
