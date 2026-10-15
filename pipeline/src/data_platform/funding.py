"""Versioned, multi-label financial aid classification from saved benefit evidence."""
from __future__ import annotations

import re
from typing import Any, Iterable

FUNDING_VERSION = "funding-1.0.0"
FUNDING_LABELS = {
    "cash": "현금성 지원",
    "scholarship": "장학금",
    "tuition": "등록금",
    "living_expenses": "생활비",
}


def operator_group(provider_kind: str) -> str:
    if provider_kind == "university":
        return "school"
    if provider_kind in {"corporate_foundation", "public_foundation"}:
        return "foundation"
    if provider_kind in {"central_government", "local_government", "public_agency"}:
        return "national"
    if provider_kind == "company":
        return "company"
    return "other"


def classify_funding(
    title: str,
    benefits: Iterable[str],
    presentation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """No eligibility, dates or arbitrary full-page text is treated as a benefit.

    A scholarship title proves its category, not its payment amount or use.
    Loans and loan interest are kept in the registry but outside the default scope.
    """
    sources = [("benefit", str(text)) for text in benefits if text]
    sources += [("presentation.benefits", str(text)) for text in (presentation or {}).get("benefits", []) if text]
    sources.append(("title", title))
    evidence: dict[str, dict[str, str]] = {}
    loan_program = bool(re.search(r"대출|대여장학|융자|학자금\s*이자|발생\s*이자", title))
    for field, text in sources:
        for raw in re.split(r"\s+/\s+|[\r\n]|;\s*", text):
            quote = raw.strip()
            if not quote or len(quote) > 1500 or re.search(r"<[^>]+>|data:[^,]+;base64", quote):
                continue
            if loan_program or re.search(r"대출|발생\s*이자|이자\s*(?:지원|감면)", quote):
                continue
            if re.search(r"본인\s*부담|자비\s*부담|(?:장학금|지원금).{0,16}(?:수혜자\s*제외|지원\s*불가|지급\s*불가)|지원하지\s*않|지급하지\s*않|지급되지\s*않", quote):
                continue
            tags: list[str] = []
            if re.search(r"장학|펠로우?십|fellowship", quote, re.I):
                tags.append("scholarship")
            if field == "title":
                if re.search(r"생활비|생계비", quote) and re.search(r"지원|장학|지급|보조", quote):
                    tags.append("living_expenses")
                if re.search(r"등록금|학비", quote) and re.search(r"지원|장학|감면|면제|보전", quote):
                    tags.append("tuition")
                if re.search(r"지원금|훈련\s*장려금|조교\s*수당|연구\s*수당", quote):
                    tags.append("cash")
            if field != "title":
                action = bool(re.search(r"지원|지급|보전|보조|감면|면제|전액|일부|원|만원|USD|달러", quote))
                if action and re.search(r"등록금|학비", quote):
                    tags.append("tuition")
                if action and re.search(r"생활비|생계비", quote):
                    tags.append("living_expenses")
                if re.search(r"활동비|정착금|훈련\s*장려금|조교\s*수당|연구\s*수당|현금\s*(?:지원|지급)|지원금|수당", quote):
                    tags.append("cash")
                elif (re.search(r"\d[\d,.]*\s*(?:만\s*)?원", quote) or re.fullmatch(r"시급", quote)) and not re.search(r"상금|경품|구독|이용권|쿠폰|기기|물품|모니터|교육비|등록금|학비|생활비|생계비|연구비", quote):
                    tags.append("cash")
            for tag in tags:
                # Prefer the unambiguous scholarship title over incidental
                # references such as suspension or duplicate-award clauses.
                if tag not in evidence or (tag == "scholarship" and field == "title"):
                    evidence[tag] = {"tag": tag, "text": quote[:700], "field": field}
    tags = [tag for tag in FUNDING_LABELS if tag in evidence]
    return {
        "version": FUNDING_VERSION,
        "tags": tags,
        "evidence": [evidence[tag] for tag in tags],
        "status": "targeted" if tags else "unconfirmed",
        "reason": "분류 근거 확인" if tags else "기본 금전 지원 범위의 근거를 확인하지 못함",
    }
