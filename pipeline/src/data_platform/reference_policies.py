"""Collect public rules referenced by notices; never ask applicants to interpret them."""
from __future__ import annotations

import re
from dataclasses import replace

from .parsers import ParsedOpportunity, _soup, _text

CARD_LAW = "https://www.law.go.kr/LSW/admRulLsInfoP.do?admRulSeq=2100000279150"
CARD_BODY = "https://www.law.go.kr/LSW/admRulLsInfoR.do"
CARD_GUIDE = "https://www.work24.go.kr/cm/c/f/1100/selecSystInfo.do?systId=SI00000351"
STUDY_RULE = "https://www.postech.ac.kr/app/board/regulations/pdf/reg_3193.do"


def _cached_get(client, url, **kwargs):
    cache = getattr(client, "reference_cache", None)
    if cache is None:
        cache = client.reference_cache = {}
    key = (url, tuple(sorted(kwargs.get("params", {}).items())))
    if key not in cache:
        cache[key] = client.get(url, **kwargs)
    return cache[key]


def parse_study_duration(text: str) -> dict[str, object]:
    # Parse the formal course duration, not the maximum registration duration.
    normalized = _text(text)
    match = re.search(r"제20조\(수업\s*연한\s*및\s*재학\s*연한\).*?석사과정\s*및\s*박사과정의\s*수업\s*연한은\s*각\s*각\s*(\d+)년", normalized)
    if not match:
        raise ValueError("POSTECH article 20 course duration not found")
    end = normalized.find("②", match.start())
    quote = normalized[match.start():end if end > match.end() else match.end()]
    return {"institution": "POSTECH", "semestersByLevel": {"master": int(match[1]) * 2, "doctoral": int(match[1]) * 2}, "sourceText": quote, "sourceUrl": STUDY_RULE}


def card_exclusion_clauses(text: str) -> list[str]:
    start, end = text.find("제4조(지원 및 지원제외)"), text.find("제5조(계좌발급의 절차)")
    if not 0 <= start < end:
        raise ValueError("card support scope not found")
    return [part.strip() for part in re.split(r"(?<!\d)(?=\d+(?:의\d+)?\.\s)", text[start:end]) if part.strip()]


def apply_card_exclusions(record: ParsedOpportunity, text: str) -> ParsedOpportunity:
    clauses = card_exclusion_clauses(text)
    rules = [*record.eligibility_rules, "카드 발급 제외: 만 75세 이상인 사람", "카드 발급 학생 기준: 졸업까지 남은 수업연한이 2년 이내인 사람"]
    # Only age and remaining-study clauses currently have executable rules.
    unknown = [clause for clause in clauses if re.match(r"\d+(?:의\d+)?\.", clause) and "삭제" not in clause and not re.search(r"만 75세|졸업까지 남은 수업연한", clause)]
    rules += ["카드 발급 추가 제외 기준: " + clause for clause in unknown]
    presentation = {key: list(value) for key, value in record.presentation.items()}
    presentation["eligibility"] = list(dict.fromkeys(rules))
    presentation["specialTerms"] = [line for line in presentation.get("specialTerms", []) if not re.match(r"(?:제4조|\d+\.)", line)] + clauses
    return replace(record, eligibility_rules=list(dict.fromkeys(rules)), presentation=presentation)


def enrich_reference_policies(record: ParsedOpportunity, client, store_raw) -> ParsedOpportunity:
    # A provider's explicit waiver takes precedence over a generic card guide.
    qualifications = " ".join(record.eligibility_rules)
    if not re.search(r"국민내일배움카드\s*발급\s*가능", qualifications):
        return record
    from .linked import extract_document

    sources = list(record.evidence_sources)
    rules = list(record.eligibility_rules)
    presentation = {key: list(value) for key, value in record.presentation.items()}
    try:
        shell = _cached_get(client, CARD_LAW)
        raw_id = store_raw(shell)
        html = shell.body.decode("utf-8", errors="replace")
        # This is the public body request exposed by the law page itself.
        param = re.search(r"var param\s*=\s*\{([^}]+)\}", html)
        if not param or '"admRulLsInfoR.do"' not in html:
            raise ValueError("public regulation body request changed")
        params = dict(re.findall(r'(\w+)\s*:\s*"([^"]*)"', param[1]))
        fetched = _cached_get(client, CARD_BODY, params=params)
        raw_id = store_raw(fetched)
        text = _text(_soup(fetched.body).get_text(" ", strip=True))
        if "졸업까지 남은 수업연한이 2년 이내인 사람" not in text or "만 75세 이상인 사람" not in text:
            raise ValueError("required card exclusion clauses not found")
        card = apply_card_exclusions(replace(record, eligibility_rules=rules, presentation=presentation), text)
        rules, presentation = card.eligibility_rules, card.presentation
        sources.append({"url": CARD_LAW, "kind": "regulation", "status": "collected", "label": "국민내일배움카드 운영규정 제4조", "text": text, "sha256": fetched.sha256, "rawDocumentId": raw_id, "bodyUrl": fetched.final_url})
    except Exception as error:
        rules.append("국민내일배움카드 발급 제한 규정 수집 실패")
        sources.append({"url": CARD_LAW, "kind": "regulation", "status": "failed", "label": "카드 발급 규정", "error": str(error)[:220]})

    for url, kind, label in [(CARD_GUIDE, "html", "고용24 카드 신청 절차"), (STUDY_RULE, "pdf", "POSTECH 대학원 학칙 제20조")]:
        try:
            fetched = _cached_get(client, url)
            raw_id = store_raw(fetched)
            text = extract_document(fetched.body, kind) if kind == "pdf" else _text(_soup(fetched.body).get_text(" ", strip=True))
            source = {"url": fetched.final_url, "kind": kind, "status": "collected", "label": label, "text": text, "sha256": fetched.sha256, "rawDocumentId": raw_id}
            if kind == "pdf":
                source["studyDuration"] = parse_study_duration(text)
            else:
                if "구직 등록" not in text and "구직등록" not in text:
                    raise ValueError("unemployed applicant registration procedure not found")
                presentation.setdefault("conditions", []).extend([
                    "미취업자는 고용24 구직 등록 후 국민내일배움카드 발급 신청",
                    "고용센터의 지원 제외 대상 심사 후 카드를 수령하고 교육과정 수강 신청",
                    "140시간 이상 과정은 수강 전 훈련 진단과 상담 진행",
                ])
            sources.append(source)
        except Exception as error:
            sources.append({"url": url, "kind": kind, "status": "failed", "label": label, "error": str(error)[:220]})
    presentation["eligibility"] = list(dict.fromkeys(rules))
    return replace(record, eligibility_rules=list(dict.fromkeys(rules)), presentation=presentation, evidence_sources=sources)
