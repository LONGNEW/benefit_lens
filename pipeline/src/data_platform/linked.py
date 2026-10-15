"""Bounded public evidence traversal. Authentication is never attempted."""
from __future__ import annotations

import os
import base64
import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, unquote, urldefrag, urljoin, urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from .http import document_kind
from .parsers import ParsedOpportunity, _application_window, _enrich_notice, _soup, _text

DOCUMENT_TOOLS_CONFIG = Path(__file__).resolve().parents[2] / "config" / "document_tools.json"


def _ocr_runtime() -> tuple[str, dict[str, str] | None]:
    """Use explicit/system tools first, then a trusted local configuration."""
    explicit = os.environ.get("BENEFITLENS_OCR_COMMAND")
    installed = shutil.which("tesseract")
    if explicit or installed:
        return explicit or installed, None
    if not DOCUMENT_TOOLS_CONFIG.is_file():
        raise RuntimeError("OCR engine unavailable; configure local config/document_tools.json")
    try:
        if DOCUMENT_TOOLS_CONFIG.stat().st_size > 65536:
            raise ValueError("configuration exceeds safe size")
        config = json.loads(DOCUMENT_TOOLS_CONFIG.read_text(encoding="utf-8"))
        values = config.get("ocr", {})
        paths = {}
        for key in ("command", "library_path", "tessdata_prefix"):
            value = values.get(key)
            if not isinstance(value, str) or not value or not Path(value).is_absolute():
                raise ValueError(f"{key} must be an absolute local path")
            paths[key] = Path(value).resolve(strict=True)
        command, library, tessdata = (paths[key] for key in ("command", "library_path", "tessdata_prefix"))
        if not command.is_file() or not os.access(command, os.X_OK):
            raise ValueError("OCR command is not an executable file")
        if not library.is_dir() or not tessdata.is_dir():
            raise ValueError("OCR library and tessdata paths must be directories")
        for language in ("kor", "eng"):
            model = tessdata / f"{language}.traineddata"
            if not model.is_file() or not os.access(model, os.R_OK):
                raise ValueError(f"OCR language model unavailable: {language}")
    except (OSError, ValueError, AttributeError, TypeError) as error:
        raise RuntimeError(f"invalid local OCR configuration: {error}") from error
    environment = os.environ.copy()
    previous_library = environment.get("LD_LIBRARY_PATH", "")
    environment["LD_LIBRARY_PATH"] = str(library) + (os.pathsep + previous_library if previous_library else "")
    environment["TESSDATA_PREFIX"] = str(tessdata)
    return str(command), environment


def merge_prompts(existing, incoming, original_url: str, incoming_url: str):
    result = []
    seen = set()
    for prompts, fallback in ((existing, original_url), (incoming, incoming_url)):
        for prompt in prompts:
            source = str(prompt.get("source_url") or fallback)
            text = str(prompt.get("prompt_text") or "").strip()
            signature = (source, text)
            if not text or signature in seen:
                continue
            seen.add(signature)
            key = "prompt-" + hashlib.sha256((source + "\n" + text).encode()).hexdigest()[:20]
            result.append({**prompt, "prompt_key": key, "source_url": source})
    return result


def _hwpx_text(body: bytes) -> str:
    """Read paragraph text without extracting ZIP paths or expanding unsafe XML."""
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        entries = [item for item in archive.infolist() if re.fullmatch(r"Contents/section\d+\.xml", item.filename, re.I)]
        if not entries or len(entries) > 200 or sum(item.file_size for item in entries) > 16 * 1024 * 1024:
            raise ValueError("HWPX sections missing or exceed safe document size")
        lines = []
        for item in sorted(entries, key=lambda item: int(re.search(r"section(\d+)", item.filename, re.I)[1])):
            if item.file_size > 4 * 1024 * 1024 or item.file_size > max(item.compress_size, 1) * 200:
                raise ValueError("HWPX section exceeds safe expansion limit")
            xml = archive.read(item)
            if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", xml.replace(b"\x00", b""), re.I):
                raise ValueError("HWPX XML declarations are not supported")
            root = ElementTree.fromstring(xml)
            for node in root.iter():
                if node.tag.rsplit("}", 1)[-1] == "p":
                    text = " ".join("".join(child.itertext()) for child in node.iter() if child.tag.rsplit("}", 1)[-1] == "t")
                    if text.strip():
                        lines.append(_text(text))
    return "\n".join(lines)


def extract_document(body: bytes, kind: str) -> str:
    if kind == "hwpx":
        text = _hwpx_text(body)
        if len(re.sub(r"\s", "", text)) < 30:
            raise ValueError("no usable HWPX text; protected or image-only document requires review")
        return text
    if kind not in {"pdf", "image", "hwp"}:
        raise ValueError(f"unsupported document kind: {kind}")
    with tempfile.TemporaryDirectory(prefix="benefitlens-document-") as folder:
        path = Path(folder) / {"pdf": "source.pdf", "image": "source.image", "hwp": "source.hwp"}[kind]
        path.write_bytes(body)
        process_options = {}
        if kind == "pdf":
            command = [shutil.which("pdftotext") or "pdftotext", "-layout", str(path), "-"]
        elif kind == "hwp":
            binary = shutil.which("hwp5txt")
            if not binary:
                raise RuntimeError("HWP text converter unavailable; install hwp5txt or use public PDF alternative")
            command = [binary, str(path)]
        else:
            binary, environment = _ocr_runtime()
            if environment is not None:
                process_options["env"] = environment
            command = [binary, str(path), "stdout", "-l", "kor+eng", "--psm", "6"]
        process = subprocess.run(command, capture_output=True, timeout=45, check=True, **process_options)
        text = process.stdout.decode("utf-8", errors="replace")
        if len(re.sub(r"\s", "", text)) < 30:
            raise ValueError("no usable document text; OCR or manual evidence review required")
        return text


def _target_kind(url: str, label: str = "") -> str:
    match = re.search(r"\.(pdf|hwpx|hwp|png|jpe?g|gif|webp|tiff?)(?:[?&#\s]|$)", unquote(url) + " " + label, re.I)
    if not match:
        return "html"
    return match[1].lower() if match[1].lower() in {"pdf", "hwpx", "hwp"} else "image"


def _inline_notice_body(soup: BeautifulSoup, title: str) -> list[str]:
    """Use explicit public initial state, not guessed endpoints or script execution."""
    texts = []
    permitted = {"Article", "NewsArticle", "JobPosting", "Scholarship", "EducationalOccupationalProgram", "Course"}
    notice = re.compile(r"장학|모집|지원\s*(자격|대상)|재정지원|scholarship|fellowship|recruit", re.I)

    def walk(value, is_jsonld: bool, depth: int = 0):
        if depth > 20:
            return
        if isinstance(value, dict):
            types = value.get("@type", [])
            types = [types] if isinstance(types, str) else types if isinstance(types, list) else []
            heading = str(value.get("headline") or value.get("title") or value.get("name") or "")
            matching = bool(heading and ((len(title) >= 4 and title in heading) or notice.search(heading)))
            if (is_jsonld and any(item in permitted for item in types)) or matching:
                for key in ("articleBody", "description", "content", "body"):
                    text = value.get(key)
                    if isinstance(text, str) and len(text.strip()) >= 30 and (matching or notice.search(text)):
                        if heading:
                            texts.append(heading)
                        # Preserve semantic line boundaries in embedded HTML or plain text.
                        fragment = BeautifulSoup(text, "html.parser")
                        for unsafe in fragment.select("script,style,nav,header,footer"):
                            unsafe.decompose()
                        texts.extend(line.strip() for line in fragment.get_text("\n", strip=True).splitlines() if line.strip())
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child, is_jsonld, depth + 1)
        elif isinstance(value, list):
            for child in value[:200]:
                walk(child, is_jsonld, depth + 1)

    for script in soup.select("script[type='application/ld+json'], script#__NEXT_DATA__[type='application/json'], script#__NUXT_DATA__[type='application/json']"):
        raw = script.string or script.get_text()
        if not raw or len(raw) > 2 * 1024 * 1024:
            continue
        try:
            walk(json.loads(raw), script.get("type") == "application/ld+json")
        except (ValueError, TypeError, RecursionError):
            continue
    return list(dict.fromkeys(texts))


def linked_targets(body: bytes, source_url: str, allowed_hosts: set[str], notice_title: str = "") -> list[tuple[str, str]]:
    soup = _soup(body)
    content = next((soup.select_one(s) for s in (".nxb-view__contents", ".board_view", "#VIEW_MCONTENT", "main", "#content") if soup.select_one(s)), soup)
    targets: list[tuple[str, str]] = []
    for anchor in soup.select("a[href]"):
        url = urljoin(source_url, str(anchor["href"]))
        label = anchor.get_text(" ", strip=True)
        in_content = anchor is content or content in anchor.parents
        if anchor.find_parent(["header", "nav", "footer"]):
            continue
        if not in_content and (not re.search(r"\.(?:pdf|hwpx|hwp)", label, re.I) or re.search(r"브로슈어|브로셔|홍보|brochure", label, re.I)):
            continue
        kind = _target_kind(url, label)
        if kind in {"pdf", "hwpx", "hwp"}:
            targets.append((url, kind))
    for embedded in content.select("iframe[src], embed[src], object[data]"):
        target = urljoin(source_url, str(embedded.get("src") or embedded.get("data") or ""))
        kind = _target_kind(target, str(embedded.get("title") or ""))
        if kind in {"pdf", "hwpx", "hwp"}:
            targets.append((target, kind))
    for image in content.select("img[src]"):
        src = str(image["src"])
        label = src + " " + str(image.get("alt", ""))
        notice_body = bool(content.get("class") and "board_view" in content.get("class", []))
        if (re.search(r"포스터|선발|장학|poster|fellow", label, re.I) or (notice_body and re.search(r"\.(?:jpg|png)|download", src, re.I))) and not re.search(r"바로가기|logo|icon|bg-|field-preview", label, re.I):
            targets.append((urljoin(source_url, src), "image"))
    # Honor explicit primary-site links, including URLs provided as plain text.
    for anchor in content.select("a[href]"):
        url = urljoin(source_url, str(anchor["href"]))
        if urlsplit(url).hostname in {"stibee.com", "event.stibee.com"}:
            # A public newsletter link encodes its destination, not authentication.
            encoded = parse_qs(urlsplit(url).query).get("url", [""])[0] or urlsplit(url).path.rsplit("/", 1)[-1]
            try:
                url = base64.b64decode(unquote(encoded) + "===").decode()
            except (ValueError, UnicodeError):
                continue
        if urlsplit(url).hostname != urlsplit(source_url).hostname and not re.search(r"forms\.gle|stibee|kakao|mailto:", url):
            targets.append((url, "html"))
        elif urlsplit(url).hostname == "edu.rapa.or.kr" and "/camp_board/" in urlsplit(url).path:
            label = anchor.get_text(" ", strip=True)
            round_match = re.search(r"\d+기", label)
            if "FAQ" in label or (round_match and round_match[0] in notice_title):
                targets.append((url, "html"))
        elif urlsplit(url).hostname == urlsplit(source_url).hostname and re.search(r"상세\s*(지원자격|공고|안내)|모집\s*공고|신청\s*안내|선발\s*공고", anchor.get_text(" ", strip=True)):
            targets.append((url, _target_kind(url, anchor.get_text(" ", strip=True))))
    for raw in re.findall(r"https://[^\s<>\"\)]+", content.get_text(" ", strip=True)):
        raw = raw.rstrip(".,")
        targets.append((raw, _target_kind(raw)))
    dedup: dict[str, str] = {}
    for url, kind in targets:
        url = urldefrag(url)[0]
        parts = urlsplit(url)
        if parts.scheme == "https" and parts.hostname in allowed_hosts and not re.search(r"login|logout|signin|Member|write|qna", parts.path + "?" + parts.query, re.I) and url != source_url:
            if url not in dedup or dedup[url] == "html":
                dedup[url] = kind
    # Prefer machine-readable PDF over poster duplicates.
    return sorted(dedup.items(), key=lambda item: {"pdf": 0, "hwpx": 1, "hwp": 2, "html": 3, "image": 4}[item[1]])[:8]


def _primary_notice(record: ParsedOpportunity, body: bytes, url: str) -> tuple[ParsedOpportunity, str]:
    """Use provider-owned semantic sections, not navigation or marketing text."""
    soup = _soup(body)
    host = urlsplit(url).hostname
    if host == "edu.rapa.or.kr" and "/camp_board/" in urlsplit(url).path:
        more = _enrich_notice(record, body, url, selectors=(".xe_content", ".document_content", ".rhymix_content"))
        text = more.evidence_sources[0]["text"]
        content = soup.select_one(".xe_content") or soup.select_one(".rhymix_content")
        from .parsers import _semantic_lines
        lines = _semantic_lines(content)
        more = replace(more, presentation={**more.presentation, "conditions": lines, "specialTerms": [line for line in lines if re.search(r"자동 탈락|모두 완료|졸업자|중복지원|출석인정|상황.*변경", line)]})
        return more, text
    elif host == "edu.rapa.or.kr":
        values = {}
        for row in soup.select("table.extra tr"):
            key, value = row.select_one("th"), row.select_one("td")
            if key and value:
                values[_text(key.get_text(" ", strip=True))] = _text(value.get_text(" ", strip=True))
        required = values.get("모집개요 - 신청자격 내용", "")
        if not required or not values.get("기수") or values["기수"] not in record.title:
            raise ValueError("provider recruitment round or qualifications unavailable")
        qualifications = [line for line in re.split(r"(?=국민내일배움카드|학사학위|장애인/)", required) if line and not re.search("우대", line)]
        preferences = [line for line in re.split(r"(?=장애인/)", required) if "우대" in line]
        period = [values.get("신청기간", "")]
        benefits = [value for key, value in values.items() if re.fullmatch(r"혜택\d+ 내용", key) and value]
        conditions = [values.get("모집개요 - 교육대상 내용", ""), "교육기간: " + values.get("교육일정", ""), "교육장소: " + values.get("모집개요 - 교육장소 내용", "")]
        special = [values.get("신청기간 추가 설명", ""), "커리큘럼과 프로젝트는 운영 상황에 따라 변경될 수 있음"]
        presentation = {"eligibility": qualifications, "conditions": preferences + conditions, "benefits": benefits, "specialTerms": special, "period": period}
        opens, closes, lifecycle, _ = _application_window(["신청기간", *period], record)
        more = replace(record, eligibility_rules=list(dict.fromkeys(record.eligibility_rules + qualifications)), presentation=presentation,
                       benefit_description=" / ".join(benefits), opens_at=opens or record.opens_at, closes_at=closes or record.closes_at,
                       lifecycle_status=lifecycle)
        # Do not retain the public one-to-one question board's applicant names.
        return more, _text(" ".join(str(v) for k, v in values.items() if not re.search(r"링크|이미지|컬러|카테고리|제목_", k)))
    elif host == "2026pre-skala.co.kr":
        section = soup.select_one(".recruit-eligibility")
        if section is None or section.select_one("ul") is None:
            raise ValueError("SK qualification section unavailable")
        qualifications = [_text(li.get_text(" ", strip=True)) for li in section.select_one("ul").select("li")]
        preferences = [_text(li.get_text(" ", strip=True)) for ul in section.select("ul")[1:] for li in ul.select("li")]
        journey = soup.select_one(".recruit-info")
        benefits = [_text(e.get_text(" ", strip=True)) for e in soup.select(".reward-card__item")]
        # Primary provider resolves the contradictory lodging wording in relayed notices.
        special = [_text(e.get_text(" ", strip=True)) for e in journey.select("li,p") if re.search(r"무박|숙소|3년간", e.get_text())] if journey else []
        journey_text = _text(journey.get_text(" ", strip=True)) if journey else ""
        if not special:
            special = re.findall(r"무박 2일|숙소 미제공|SKALA 입과 혜택 3년간 유효", journey_text)
        presentation = {"eligibility": qualifications, "preferences": preferences, "conditions": [journey_text] if journey_text else [],
                        "benefits": benefits, "specialTerms": special, "period": [_text(e.get_text(" ", strip=True)) for e in soup.select(".recruit-period")[:1]]}
    elif host == "ncai.ndko.co.kr":
        section = soup.select_one("#condition")
        if section is None:
            raise ValueError("NC qualification section unavailable")
        qualifications = [_text(section.select_one("span").get_text(" ", strip=True))] + [_text(e.get_text(" ", strip=True)) for e in section.select("li")]
        benefits_section = soup.select_one("#benefits")
        benefits = [_text(e.get_text(" ", strip=True)) for e in benefits_section.select("h3,p")] if benefits_section else []
        faq = soup.select_one("#faq")
        faq_text = _text(faq.get_text(" ", strip=True)) if faq else ""
        # Read actual provider declarations; do not infer that unemployment implies availability.
        conditions = re.findall(r"평일 09:00~18:00[^.]*\.|30명까지 모집[^.]*\.", faq_text)
        qualifications += [line for line in conditions if line.startswith("평일 09:00~18:00")]
        special = [_text(e.get_text(" ", strip=True)) for e in section.select("p") if "증빙" in e.get_text()]
        special += re.findall(r"국민내일배움카드 발급 여부[^.]*\.|수료 기준\s*:\s*수업 일수의 80% 이상 출석 완료", faq_text)
        # Military extension is not converted into a blanket age exclusion.
        military = [line for line in qualifications if "군필" in line]
        qualifications = [line for line in qualifications if line not in military]
        special += military
        if military:
            qualifications += ["군필자의 연령 예외는 의무복무기간과 함께 판정해야 함"]
        period_match = re.search(r"~\s*10월\s*12일\s*\(월\)", soup.get_text(" ", strip=True))
        period = ["접수 기간: " + period_match[0]] if period_match else []
        location = soup.select_one("#location")
        if location:
            conditions += [_text(location.get_text(" ", strip=True))]
        presentation = {"eligibility": qualifications, "conditions": conditions, "benefits": benefits, "specialTerms": special, "period": period}
    else:
        # The normal visible-text parser intentionally removes script tags.
        structured = _inline_notice_body(BeautifulSoup(body, "html.parser"), record.title)
        if structured:
            content = soup.select_one("main")
            if content is None:
                content = soup.new_tag("main")
                (soup.body or soup).append(content)
            for line in structured:
                paragraph = soup.new_tag("p")
                paragraph.string = line
                content.append(paragraph)
            body = str(soup).encode("utf-8")
        visible = soup.select_one("main") or soup.body or soup
        if len(_text(visible.get_text(" ", strip=True))) < 30:
            raise ValueError("public HTML contains no notice body; client-rendered detail unavailable")
        more = _enrich_notice(record, body, url, selectors=("main", "body"))
        return more, _text(soup.get_text(" ", strip=True))
    # Retain explicit school-only age/nationality constraints for SK. For NC, primary rules
    # replace the abbreviated relay, with omitted/ambiguous alternatives kept unresolved.
    rules = qualifications + (record.eligibility_rules if host == "2026pre-skala.co.kr" else [])
    opens, closes, lifecycle, _ = _application_window(presentation["period"], record)
    more = replace(record, eligibility_rules=list(dict.fromkeys(rules)), presentation=presentation,
                   benefit_description=" / ".join(presentation["benefits"]),
                   opens_at=record.opens_at or opens, closes_at=closes or record.closes_at,
                   lifecycle_status=lifecycle if closes else record.lifecycle_status)
    return more, _text(soup.get_text(" ", strip=True))


def enrich_linked(record: ParsedOpportunity, body: bytes, source_url: str, client, store_raw) -> ParsedOpportunity:
    sources = list(record.evidence_sources)
    pdf_found = False
    queue = [(url, kind, 0) for url, kind in linked_targets(body, source_url, client.allowed_hosts, record.title)]
    visited = {source_url}
    for url, kind, depth in queue:
        if url in visited:
            continue
        visited.add(url)
        if kind == "image" and pdf_found:
            sources.append({"url": url, "kind": kind, "status": "duplicate", "label": "포스터, PDF 원문 우선"})
            continue
        raw_id = None
        try:
            fetched = client.get(url)
            raw_id = store_raw(fetched)
            actual_kind = document_kind(fetched.body, fetched.content_type, fetched.final_url, fetched.headers)
            if kind in {"pdf", "hwp", "hwpx", "image"} and actual_kind == "html":
                raise ValueError(f"expected {kind} attachment, received HTML page")
            kind = actual_kind
            if kind == "unsupported":
                raise ValueError("unsupported binary attachment; raw document retained for review")
            if kind == "html":
                if urlsplit(fetched.final_url).hostname == "www.codetree.ai":
                    from .public_bundle import codetree_notice
                    more, text, extra_sources = codetree_notice(record, fetched.body, fetched.final_url, client, store_raw)
                    sources.extend(extra_sources)
                else:
                    more, text = _primary_notice(record, fetched.body, fetched.final_url)
            else:
                text = extract_document(fetched.body, kind)
                fragment = BeautifulSoup("<main></main>", "html.parser")
                for line in text.splitlines():
                    p = fragment.new_tag("p")
                    p.string = _text(line)
                    fragment.main.append(p)
                more = _enrich_notice(record, str(fragment).encode(), fetched.final_url, selectors=("main",))
                pdf_found = pdf_found or kind == "pdf"
            # Public sources remain separate; do not erase original criteria or terms.
            primary = kind == "html" and urlsplit(fetched.final_url).hostname in {"2026pre-skala.co.kr", "ncai.ndko.co.kr"}
            merged = more.presentation if primary else {key: list(dict.fromkeys(record.presentation.get(key, []) + more.presentation.get(key, []))) for key in {**record.presentation, **more.presentation}}
            record = replace(record, eligibility_rules=more.eligibility_rules if primary else list(dict.fromkeys(record.eligibility_rules + more.eligibility_rules)), presentation=merged,
                             benefit_description=" / ".join(merged.get("benefits", [])) or record.benefit_description,
                             opens_at=more.opens_at or record.opens_at, closes_at=more.closes_at or record.closes_at,
                             lifecycle_status=more.lifecycle_status,
                             required_documents=list(dict.fromkeys(record.required_documents + more.required_documents)),
                             application_prompts=merge_prompts(record.application_prompts, more.application_prompts, record.official_url, fetched.final_url),
                             evidence={**record.evidence, **more.evidence})
            label = {"image": "이미지 OCR 근거", "pdf": "공식 첨부 PDF", "hwp": "공식 첨부 HWP", "hwpx": "공식 첨부 HWPX"}.get(kind, "연결된 공식 상세")
            sources.append({"url": fetched.final_url, "kind": kind, "status": "ocr_review" if kind == "image" else "collected", "label": label, "text": text, "sha256": fetched.sha256, "rawDocumentId": raw_id})
            if kind == "html" and depth < 1:
                children = linked_targets(fetched.body, fetched.final_url, client.allowed_hosts, record.title)
                additions = [(child, child_kind, depth + 1) for child, child_kind in children if child not in visited and child not in {target[0] for target in queue}]
                queue.extend(additions[:max(0, 12 - len(queue))])
        except Exception as error:
            sources.append({"url": url, "kind": kind, "status": "failed", "label": "연결 자료 수집 실패", "error": str(error)[:220], **({"rawDocumentId": raw_id} if raw_id is not None else {})})
    if any(x.get("status") == "ocr_review" for x in sources):
        # OCR does not prove the absence of missed conditions.
        record = replace(record, eligibility_rules=record.eligibility_rules + ["이미지 OCR로 추출한 공고, 원본과 조건 대조 검토 필요"])
    from .reference_policies import enrich_reference_policies
    return enrich_reference_policies(replace(record, evidence_sources=sources), client, store_raw)
