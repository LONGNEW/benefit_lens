"""Source-grounded presentation and amount contract; no inferred eligibility."""
from __future__ import annotations

import re
import json
from dataclasses import replace
from decimal import Decimal
from typing import Any

CONTRACT_VERSION = "opportunity-1.1.0"
SECTION_KEYS = ("eligibility", "exclusions", "preferences", "requirements", "selection", "benefits", "specialTerms", "applicationPeriod", "activityPeriod")


def unique_lines(values: list[str]) -> list[str]:
    return list(dict.fromkeys(re.sub(r"\s+", " ", value).strip() for value in values if value and value.strip()))


_OBJECTIVE_CONDITION = re.compile(
    r"만\s*\d+\s*세|\d+\s*[~～-]\s*\d+\s*세|대상\s*연령|연령\s*(?:제한|예외)|"
    r"국적|대한민국|한국인|외국인|미취업|취업자|재직자|고용보험|"
    r"학부생|대학원생|[1234]\s*학년|학사\s*학위|석사\s*(?:학위|과정)|박사\s*(?:학위|과정)|"
    r"졸업\s*(?:예정|학점)|재학생|재학\s*중|휴학생|수료생|"
    r"소속\s*(?:학교|학과)|성적|평점|학점|소득|수급자|차상위|"
    r"제재|부정\s*(?:수급|행위)|반환\s*명령|중복\s*(?:수혜|지원)|"
    r"타\s*(?:장학금|지원금).*수혜|지원\s*제외|제외\s*대상"
)
_DOCUMENT = r"(?:서명|날인|서류|증명서|추천서|자기소개서|학업계획서|신청서|지원서|동의서|서약서|사본)"
_DOCUMENT_ACTION = r"(?:제출|첨부|발급|작성|필수|준비|지참|필요|구비)"


def semantic_role(text: str) -> str:
    """Move only explicit procedural obligations; ambiguous mixed clauses stay.

    Existing source text is retained verbatim in the returned sections. A sentence
    combining a qualification and an obligation is deliberately not reduced to
    the latter: doing so could silently drop age, degree or exclusion rules.
    """
    if re.match(r"^(?:[-*■●\s]|\d+[.)])*\s*(?:우대\s*사항|우대\s*조건|가점\s*(?:대상|항목))\s*[:：]?", text):
        return "preferences"
    if re.search(r"(?:우대|가점)\s*(?:함|합니다)?[.!]?\s*$", text) and not re.search(r"[,;]|필수|지원\s*(?:자격|대상|가능)|신청\s*가능|이며", text):
        return "preferences"
    # In this specific continuing-scholar obligation, '1학년' identifies the
    # cohort to which the document requirement applies, not a new entry gate.
    # Remove only that explicit scope for the safety check, retaining the whole
    # source sentence when any additional objective qualification is present.
    document_scope = r"['’]?\s*(?:20)?\d{2}\s*년\s*1\s*학년\s*신규\s*선발자\s*부터"
    if (re.search(r"이공계열\s*활동\s*내역서\s*제출", text)
            and re.search(document_scope, text)
            and re.search(r"적용|중간\s*평가", text)
            and not _OBJECTIVE_CONDITION.search(re.sub(document_scope, "", text))):
        return "document"
    if re.fullmatch(r"[-*ㅇ■●\s]*자세한\s*내용은\s*업무\s*처리\s*기준을\s*참고하시기\s*바랍니다[.!]?\s*", text):
        return "requirements"
    if re.fullmatch(r"[-*ㅇ■●\s]*학기\s*및\s*방학\s*중\s*운영[.!]?\s*", text):
        return "activityPeriod"
    if _OBJECTIVE_CONDITION.search(text):
        return "eligibility"
    if re.search(_DOCUMENT + r".*" + _DOCUMENT_ACTION + "|" + _DOCUMENT_ACTION + r".*" + _DOCUMENT, text):
        return "document"
    if re.search(r"(?:선발|심사|평가|면접)\s*(?:방법|절차|과정|기준)|서류\s*심사|최종\s*선발", text):
        return "selection"
    if re.search(r"(?:온라인|홈페이지|이메일|전자우편|우편|방문).*?(?:접수|신청)|(?:접수|신청).*?(?:온라인|홈페이지|이메일|전자우편|우편|방문)", text):
        return "requirements"
    if re.search(r"(?:참여|참석|수강|출석|이수|활동|교육|훈련|해커톤|입과|시험).*?(?:가능|의무|필수|해야|예정|진행|운영|수료|준수)|(?:직무|취업|코딩|알고리즘).*?(?:희망|준비|관심|복습)", text):
        return "requirements"
    return "eligibility"


def prepare_record(record: Any) -> Any:
    """Pure canonical preparation shared by enrichment and every DB write.

    The source evidence and candidate review metadata are never rewritten. Bad
    legacy prompt candidates are quarantined with all metadata instead of being
    displayed as official writing questions. Repeated preparation is idempotent.
    """
    from .parsers import is_application_prompt
    from .criteria import normalize_eligibility

    presentation = {key: list(value) for key, value in record.presentation.items()}
    normalized = normalize_eligibility(record.eligibility_rules)
    parsed_requirements = {rule.source_text for rule in normalized.rules if rule.condition_kind == "requirement"}
    eligibility, documents = [], list(record.required_documents)
    requirements = list(presentation.get("requirements", []))
    preferences = list(presentation.get("preferences", []))
    selection = list(presentation.get("selection", []))
    activity_period = list(presentation.get("activityPeriod", []))
    # A separate newline is a source-provided clause boundary, not an inferred
    # AND/OR split. Keep comma-separated/mixed qualification sentences intact.
    for raw in record.eligibility_rules:
        for text in [part.strip() for part in raw.splitlines() if part.strip()]:
            role = semantic_role(text)
            if role == "eligibility" and text in parsed_requirements and not _OBJECTIVE_CONDITION.search(text):
                role = "requirements"
            if role == "document":
                documents.append(text)
                requirements.append(text)
            elif role == "requirements":
                requirements.append(text)
            elif role == "preferences":
                preferences.append(text)
            elif role == "selection":
                selection.append(text)
            elif role == "activityPeriod":
                activity_period.append(text)
            else:
                eligibility.append(text)
    for text in presentation.get("conditions", []):
        role = semantic_role(text)
        if role == "preferences":
            preferences.append(text)
        elif role == "document":
            documents.append(text)
            requirements.append(text)
        elif role == "selection":
            selection.append(text)
        elif role == "activityPeriod":
            activity_period.append(text)

    prompts, rejected = [], []
    evidence = dict(record.evidence)
    if evidence.get("rejected_application_prompts"):
        try:
            rejected = json.loads(evidence["rejected_application_prompts"])
            if not isinstance(rejected, list):
                rejected = []
        except (ValueError, TypeError):
            rejected = []
    for prompt in record.application_prompts:
        text = str(prompt.get("prompt_text") or "").strip()
        trusted = prompt.get("review_status") == "approved" or prompt.get("source_kind") == "official_form"
        if is_application_prompt(text, allow_title=trusted):
            prompts.append(dict(prompt))
        else:
            if prompt not in rejected:
                rejected.append(dict(prompt))
            if semantic_role(text) == "document":
                documents.append(text)
                requirements.append(text)
    if rejected:
        evidence["rejected_application_prompts"] = json.dumps(rejected, ensure_ascii=False, sort_keys=True)
    presentation.update(eligibility=unique_lines(eligibility), requirements=unique_lines(requirements),
                        preferences=unique_lines(preferences), selection=unique_lines(selection),
                        activityPeriod=unique_lines(activity_period))
    return replace(record, eligibility_rules=unique_lines(eligibility), required_documents=unique_lines(documents),
                   application_prompts=prompts, presentation=presentation, evidence=evidence)


def condition_sections(record: Any) -> dict[str, list[str]]:
    record = prepare_record(record)
    presentation = record.presentation
    result = {key: [] for key in SECTION_KEYS}
    for text in unique_lines(record.eligibility_rules):
        key = "exclusions" if re.search(r"제외|지원\s*불가|수혜\s*대상.*아님|포함되지", text) else "eligibility"
        result[key].append(text)
    result["preferences"] = unique_lines(presentation.get("preferences", []))
    result["requirements"] = unique_lines(presentation.get("requirements", []))
    result["selection"] = unique_lines(presentation.get("selection", []))
    result["activityPeriod"] = unique_lines(presentation.get("activityPeriod", []))
    for text in unique_lines(presentation.get("conditions", [])):
        role = semantic_role(text)
        if role == "preferences":
            result["preferences"].append(text)
        elif role == "selection":
            result["selection"].append(text)
        elif role == "activityPeriod":
            result["activityPeriod"].append(text)
        else:
            result["requirements"].append(text)
        if re.search(r"교육\s*기간|활동\s*기간|교육\s*일정|활동\s*일정", text):
            result["activityPeriod"].append(text)
    result["requirements"].extend(record.required_documents)
    result["benefits"] = unique_lines(presentation.get("benefits", []) or [record.benefit_description])
    result["specialTerms"] = unique_lines(presentation.get("specialTerms", []))
    result["applicationPeriod"] = unique_lines(presentation.get("period", []))
    return {key: unique_lines(value) for key, value in result.items()}


def parse_amount(description: str) -> dict[str, Any]:
    """Only one explicit monetary expression may populate a benefit amount.

    Compound summaries and relative tuition rates keep null amounts. Approximate
    and upper-bound wording is retained rather than turned into guaranteed cash.
    """
    result: dict[str, Any] = {"amount_min": None, "amount_max": None, "currency": None,
                              "recurrence": None, "amount_qualifier": None}
    if re.search(r"상당|본인\s*부담|자부담|납부|환수|반환", description):
        return result
    pattern = r"(?P<prefix>USD\s*)?(?P<low>\d[\d,]*(?:\.\d+)?)\s*(?P<low_scale>억|만)?\s*(?:[~～]\s*(?P<high>\d[\d,]*(?:\.\d+)?)\s*)?(?P<scale>억|만)?\s*(?P<unit>원|달러|(?<=\d)\s*/(?=년|월))"
    matches = list(re.finditer(pattern, description, re.I))
    # USD 30,000/year also appears without a Korean currency suffix.
    if not matches:
        matches = list(re.finditer(r"(?P<prefix>USD)\s*(?P<low>\d[\d,]*(?:\.\d+)?)(?P<scale>)(?P<unit>)", description, re.I))
    if len(matches) != 1:
        return result
    match = matches[0]
    groups = match.groupdict()
    if groups.get("unit", "").strip() == "/" and not groups.get("prefix"):
        return result
    scale = {None: 1, "": 1, "만": 10000, "억": 100000000}
    lower = Decimal(groups["low"].replace(",", "")) * scale[groups.get("low_scale") or groups.get("scale")]
    upper = Decimal(groups["high"].replace(",", "")) * scale[groups.get("scale")] if groups.get("high") else lower
    if lower > upper:
        return result
    qualifier = "approximate" if re.search(r"내외|약\s*\d|가량", description) else "up_to" if "최대" in description else "range" if groups.get("high") else "fixed"
    result.update(amount_min=None if qualifier in {"approximate", "up_to"} else float(lower),
                  amount_max=float(upper), currency="USD" if groups.get("prefix") or groups.get("unit") == "달러" else "KRW",
                  amount_qualifier=qualifier)
    for pattern, value in ((r"매월|월\s*\d|/\s*월|월당", "month"), (r"연간|매년|연\s*\d|/\s*년", "year"), (r"학기당|/\s*학기|학기별", "semester")):
        if re.search(pattern, description):
            result["recurrence"] = value
            break
    return result


def benefit_entries(record: Any) -> list[dict[str, Any]]:
    from .parsers import _infer_benefit_type
    lines = unique_lines(record.presentation.get("benefits", []))
    lines = [line for line in lines if not re.fullmatch(r"[\d.)\-\s■]*\s*(?:교육|선발|참여)?\s*혜택\s*[:：]?", line)]
    if not lines:
        lines = [record.benefit_description]
    entries = [{"benefit_type": _infer_benefit_type(line) if _infer_benefit_type(line) != "unknown" else record.benefit_type, "description": line} for line in lines]
    for item in record.additional_benefits:
        if item["description"] not in {entry["description"] for entry in entries}:
            entries.append(dict(item))
    return [{**entry, **parse_amount(entry["description"])} for entry in entries]
