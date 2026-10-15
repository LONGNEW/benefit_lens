from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


CRITERIA_VERSION = "eligibility-1.6.0"

# Participation commitments are displayed as application requirements.
PARTICIPATION_CRITERIA = frozenset({
    "online_learning_available", "hackathon_available", "activity_attendance",
    "fulltime_training_available", "coding_study_interest",
    "employment_preparation", "training_card_eligible", "enrollment_commitment",
})


CRITERIA_DEFINITIONS: dict[str, dict[str, Any]] = {
    "codetree_paid": {"profile_field": "codetreePaid", "value_type": "boolean", "unit": None, "operators": ["eq"]},
    "codetree_subscribed": {"profile_field": "codetreeSubscribed", "value_type": "boolean", "unit": None, "operators": ["eq"]},
    "codetree_prior_challenge": {"profile_field": "codetreePriorChallenge", "value_type": "boolean", "unit": None, "operators": ["eq"]},
    "codetree_bc3_free_pass": {"profile_field": "codetreeBc3FreePass", "value_type": "boolean", "unit": None, "operators": ["eq"]},
    "remaining_study_months": {"profile_field": "remainingStudyMonths", "value_type": "integer", "unit": "month", "operators": ["lte"]},
    "expected_graduation_month": {"profile_field": "expectedGraduationMonth", "value_type": "string", "unit": "calendar_month", "operators": ["eq"]},
    "employment_preparation": {"profile_field": "employmentPreparation", "value_type": "boolean", "unit": None, "operators": ["eq"]},
    "fulltime_student": {"profile_field": "fullTimeStudent", "value_type": "boolean", "unit": None, "operators": ["eq"]},
    "online_learning_available": {"profile_field": "onlineLearningAvailable", "value_type": "boolean", "unit": None, "operators": ["eq"]},
    "hackathon_available": {"profile_field": "hackathonAvailable", "value_type": "boolean", "unit": None, "operators": ["eq"]},
    "activity_attendance": {"profile_field": "activityAttendance", "value_type": "boolean", "unit": None, "operators": ["eq"]},
    "training_card_eligible": {"profile_field": "trainingCardEligible", "value_type": "boolean", "unit": None, "operators": ["eq"]},
    "fulltime_training_available": {"profile_field": "fullTimeTrainingAvailable", "value_type": "boolean", "unit": None, "operators": ["eq"]},
    "coding_study_interest": {"profile_field": "codingStudyInterest", "value_type": "boolean", "unit": None, "operators": ["eq"]},
    "institution": {"profile_field": "institution", "value_type": "enum", "unit": None, "operators": ["eq", "in"]},
    "department": {"profile_field": "department", "value_type": "enum", "unit": None, "operators": ["eq", "in"]},
    "enrollment_commitment": {"profile_field": "enrollmentCommitment", "value_type": "boolean", "unit": None, "operators": ["eq"]},
    "nationality": {
        "profile_field": "nationality",
        "value_type": "enum",
        "unit": None,
        "operators": ["eq", "in", "neq", "not_in"],
    },
    "age": {
        "profile_field": "age",
        "value_type": "integer",
        "unit": "year",
        "operators": ["gte", "lte", "between"],
    },
    "academic_level": {
        "profile_field": "academicLevel",
        "value_type": "enum",
        "unit": None,
        "operators": ["eq", "in", "neq", "not_in"],
    },
    "highest_degree_level": {
        "profile_field": "highestDegreeLevel",
        "value_type": "enum",
        "unit": None,
        "operators": ["eq", "in"],
    },
    "enrollment_status": {
        "profile_field": "enrollmentStatus",
        "value_type": "enum",
        "unit": None,
        "operators": ["eq", "in", "neq", "not_in"],
    },
    "academic_year": {
        "profile_field": "year",
        "value_type": "integer",
        "unit": "academic_year",
        "operators": ["eq", "gte", "lte", "between"],
    },
    "graduate_semester": {
        "profile_field": "graduateSemester",
        "value_type": "integer",
        "unit": "semester",
        "operators": ["eq", "gte", "lte", "between"],
    },
    "coursework_completed": {
        "profile_field": "courseworkCompleted",
        "value_type": "boolean",
        "unit": None,
        "operators": ["eq"],
    },
    "previous_semester_credits": {
        "profile_field": "previousSemesterCredits",
        "value_type": "number",
        "unit": "credit",
        "operators": ["gte", "lte", "between"],
    },
    "gpa": {
        "profile_field": "gpa",
        "value_type": "number",
        "unit": "gpa_scale",
        "operators": ["gte", "lte", "between"],
    },
    "income_band": {
        "profile_field": "incomeBand",
        "value_type": "integer",
        "unit": "kosaf_band",
        "operators": ["gte", "lte", "between"],
    },
    "residence_city": {
        "profile_field": "residenceCity",
        "value_type": "string",
        "unit": "kr_admin_name",
        "operators": ["eq", "in", "neq", "not_in"],
    },
    "ancestor_residence_city": {
        "profile_field": "ancestorResidenceCity",
        "value_type": "string",
        "unit": "kr_admin_name",
        "operators": ["eq", "in", "neq", "not_in"],
    },
    "residence_months": {
        "profile_field": "residenceMonths",
        "value_type": "integer",
        "unit": "month",
        "operators": ["gte", "lte", "between"],
    },
    "regional_high_school": {
        "profile_field": "regionalHighSchool",
        "value_type": "boolean",
        "unit": None,
        "operators": ["eq"],
    },
    "topik_level": {
        "profile_field": "topikLevel",
        "value_type": "integer",
        "unit": "topik_level",
        "operators": ["eq", "gte", "lte", "between"],
    },
    "team_size": {
        "profile_field": "teamSize",
        "value_type": "integer",
        "unit": "person",
        "operators": ["eq", "gte", "lte", "between"],
    },
    "employment_status": {
        "profile_field": "employmentStatus",
        "value_type": "enum",
        "unit": None,
        "operators": ["eq", "in", "neq", "not_in"],
    },
    "industry_employee": {
        "profile_field": "industryEmployee",
        "value_type": "boolean",
        "unit": None,
        "operators": ["eq"],
    },
}


@dataclass(frozen=True)
class RuleGroup:
    group_key: str
    parent_group_key: str | None
    combinator: str
    label: str
    position: int


@dataclass(frozen=True)
class CanonicalRule:
    rule_key: str
    group_key: str
    criterion_code: str
    profile_field: str
    operator: str
    value: Any
    value_type: str
    unit: str | None
    label: str
    source_text: str
    position: int
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def condition_kind(self) -> str:
        return "requirement" if self.criterion_code in PARTICIPATION_CRITERIA else "eligibility"


@dataclass
class NormalizedEligibility:
    groups: list[RuleGroup] = field(
        default_factory=lambda: [RuleGroup("root", None, "and", "전체 필수 조건", 0)]
    )
    rules: list[CanonicalRule] = field(default_factory=list)
    unresolved_rules: list[str] = field(default_factory=list)
    criteria_version: str = CRITERIA_VERSION

    @property
    def match_mode(self) -> str:
        if not self.rules:
            return "review_required"
        if self.unresolved_rules:
            return "partial"
        return "structured"


def _clean(value: str) -> str:
    return " ".join(value.split()).strip()


def _is_heading(value: str) -> bool:
    stripped = re.sub(r"[^가-힣A-Za-z]", "", value)
    return stripped in {"지원자격", "지원대상", "지원대상및내용", "모집대상", "신청대상", "교육대상", "참여대상", "중간평가성적기준", "이공계열활동내역서제출"}


def _is_not_eligibility(value: str) -> bool:
    return bool(
        re.search(r"교육장소|선발혜택|지원내용|교육혜택|장학금 지급|입사 기회|Coursework 수료기준|자세한 사항", value, re.IGNORECASE)
    )


def _definition(code: str) -> dict[str, Any]:
    return CRITERIA_DEFINITIONS[code]


def _fully_consumed(line: str, *patterns: str) -> bool:
    remaining = line
    for pattern in patterns:
        remaining = re.sub(pattern, " ", remaining)
    remaining = re.sub(r"지원대상|지원자격|모집대상|신청대상|[\s:(),./\-①②③④⑤]", "", remaining)
    return not remaining


def _append_rule(
    result: NormalizedEligibility,
    *,
    code: str,
    operator: str,
    value: Any,
    label: str,
    source_text: str,
    group_key: str = "root",
    unit: str | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    definition = _definition(code)
    if operator not in definition["operators"]:
        raise ValueError(f"unsupported operator {operator} for {code}")
    if any(
        rule.group_key == group_key
        and rule.criterion_code == code
        and rule.operator == operator
        and rule.value == value
        and rule.unit == (unit or definition["unit"])
        and rule.context == (context or {})
        for rule in result.rules
    ):
        return
    result.rules.append(
        CanonicalRule(
            rule_key=f"{group_key}:{code}:{len(result.rules) + 1}",
            group_key=group_key,
            criterion_code=code,
            profile_field=str(definition["profile_field"]),
            operator=operator,
            value=value,
            value_type=str(definition["value_type"]),
            unit=unit or definition["unit"],
            label=label,
            source_text=source_text,
            position=len(result.rules),
            context=context or {},
        )
    )


def _append_group(
    result: NormalizedEligibility,
    key: str,
    parent: str,
    combinator: str,
    label: str,
) -> None:
    existing = next((group for group in result.groups if group.group_key == key), None)
    if existing:
        if (existing.parent_group_key, existing.combinator) != (parent, combinator):
            raise ValueError("conflicting eligibility group: " + key)
        return
    result.groups.append(
        RuleGroup(key, parent, combinator, label, len(result.groups))
    )


def _normalize_hyundai_graduate_paths(
    lines: list[str], result: NormalizedEligibility
) -> set[int]:
    master_index = next(
        (index for index, line in enumerate(lines) if re.search(r"석사\s*:\s*1\s*[-~]\s*3학기", line)),
        None,
    )
    doctoral_index = next(
        (index for index, line in enumerate(lines) if re.search(r"박사\s*:\s*Coursework\s*수료", line, re.IGNORECASE)),
        None,
    )
    if master_index is None or doctoral_index is None:
        return set()
    _append_group(result, "graduate_path", "root", "or", "석사 또는 박사 경로")
    _append_group(result, "master_path", "graduate_path", "and", "석사 경로")
    _append_group(result, "doctoral_path", "graduate_path", "and", "박사 경로")
    _append_rule(
        result,
        code="academic_level",
        operator="eq",
        value="master",
        label="석사과정",
        source_text=lines[master_index],
        group_key="master_path",
    )
    _append_rule(
        result,
        code="graduate_semester",
        operator="between",
        value=[1, 3],
        label="석사 1학기에서 3학기",
        source_text=lines[master_index],
        group_key="master_path",
    )
    _append_rule(
        result,
        code="academic_level",
        operator="eq",
        value="doctoral",
        label="박사과정",
        source_text=lines[doctoral_index],
        group_key="doctoral_path",
    )
    _append_rule(
        result,
        code="coursework_completed",
        operator="eq",
        value=True,
        label="Coursework 수료",
        source_text=lines[doctoral_index],
        group_key="doctoral_path",
    )
    return {master_index, doctoral_index}


def _normalize_residence_alternatives(
    lines: list[str], result: NormalizedEligibility
) -> set[int]:
    marker_index = next(
        (index for index, line in enumerate(lines) if "다음 대상 중 하나" in line),
        None,
    )
    if marker_index is None:
        return set()
    self_index = next(
        (index for index, line in enumerate(lines) if "본인이 영주시" in line),
        None,
    )
    ancestor_index = next(
        (index for index, line in enumerate(lines) if "직계존속이 영주시" in line),
        None,
    )
    if self_index is None or ancestor_index is None:
        return set()
    # A third route cannot be silently dropped, and root-level numeric rules
    # must not narrow the already scoped alternatives.
    numbered_paths = {index for index, line in enumerate(lines) if re.match(r"^[①②③④⑤]", line)}
    _append_group(result, "residence_path", "root", "or", "본인 또는 직계존속 거주 경로")
    _append_group(result, "self_residence", "residence_path", "and", "본인 거주 경로")
    _append_group(result, "ancestor_residence", "residence_path", "and", "직계존속 거주 경로")
    additional_paths = numbered_paths - {self_index, ancestor_index}
    for path_index in sorted(additional_paths):
        _append_group(result, f"unresolved_residence_{path_index}", "residence_path", "and", "추가 거주 자격 경로, 해석 보완 필요")
        result.unresolved_rules.append(lines[path_index])
    for group_key, code, source_index in (
        ("self_residence", "residence_city", self_index),
        ("ancestor_residence", "ancestor_residence_city", ancestor_index),
    ):
        _append_rule(
            result,
            code=code,
            operator="eq",
            value="영주시",
            label="영주시 주민등록",
            source_text=lines[source_index],
            group_key=group_key,
        )
        _append_rule(
            result,
            code="academic_level",
            operator="eq",
            value="undergraduate",
            label="대학생",
            source_text=lines[source_index],
            group_key=group_key,
        )
        _append_rule(
            result,
            code="enrollment_status",
            operator="in",
            value=["enrolled", "leave"],
            label="재학생 또는 휴학생",
            source_text=lines[source_index],
            group_key=group_key,
        )
    return {marker_index, self_index, ancestor_index, *additional_paths}


def _numeric_context(line: str) -> dict[str, Any]:
    """Retain the GPA basis; a named multi-semester window needs manual mapping."""
    context: dict[str, Any] = {}
    window = re.search(r"(?:직전\s*)?\d+\s*년간.*?\d+\s*개?\s*학기", line)
    if window:
        context.update(basis="specified_window", windowText=line, requiresReview=True)
    elif re.search(r"직전\s*학기|지난\s*학기", line):
        context["basis"] = "previous_semester"
    elif re.search(r"누적|전체\s*학기|전\s*학년|총\s*평점", line):
        context["basis"] = "cumulative"
    period = re.search(r"(20\d{2})\s*(?:학년도|년)\s*([12])\s*학기", line)
    if period:
        context["evaluationPeriod"] = f"{period[1]}-{period[2]}"
    return context


def _normalize_numeric_conditions(line: str, result: NormalizedEligibility) -> bool:
    """Extract known numeric clauses and retain every remaining qualification."""
    remaining = line
    matched = False

    def consume(fragment: str) -> None:
        nonlocal remaining
        remaining = remaining.replace(fragment, " ", 1)

    # These alternatives require scoped logic, rather than root-level AND leaves.
    # GPA scale alternatives are handled explicitly below.
    if re.search(r"또는|혹은|부모|직계존속", line) and not re.search(r"평점|GPA", line, re.I):
        return False

    year = re.search(r"([1-6])\s*(?:학년)?\s*[~∼～-]\s*([1-6])\s*학년", line)
    if year and not re.search(r"년간|백분위|평점|GPA", line, re.I):
        start, end = int(year[1]), int(year[2])
        if start > end:
            return False
        _append_rule(result, code="academic_year", operator="between", value=[start, end], label=f"{start}학년에서 {end}학년", source_text=line)
        if re.search(r"학부|대학생", line):
            _append_rule(result, code="academic_level", operator="eq", value="undergraduate", label="학부생", source_text=line)
        if "재학생" in line:
            _append_rule(result, code="enrollment_status", operator="eq", value="enrolled", label="재학", source_text=line)
        consume(year[0])
        remaining = re.sub(r"학부생|학부|대학생|재학생", " ", remaining)
        matched = True

    gpa = re.search(r"(?:평점(?:\s*평균)?|GPA)\s*([0-4](?:\.\d+)?)\s*점?\s*(이상|이하)", line, re.I)
    if gpa:
        basis = _numeric_context(line)
        # Both source formats occur: 3.50이상/4.5만점 and 4.5만점 기준 평점 3.5이상.
        scale_pairs = list(re.finditer(r"([0-4](?:\.\d+)?)\s*점?\s*(이상|이하)\s*[/／]\s*(4\.[035])\s*만점", line))
        scales = re.findall(r"(4\.[035])\s*(?:점\s*)?만점", line)
        if scale_pairs:
            has_other_alternative = bool(re.search(r"(?:또는|혹은)", line) and (len(scale_pairs) == 1 or re.search(r"백분위|추천|수혜|이수학점", line)))
            parent = "root"
            suffix = len(result.groups)
            if has_other_alternative:
                parent = f"grade_paths_{suffix}"
                _append_group(result, parent, "root", "or", "평점 또는 다른 성적 경로")
                _append_group(result, f"unresolved_grade_path_{suffix}", parent, "and", "다른 자격 경로, 해석 보완 필요")
            if len(scale_pairs) > 1:
                group = f"gpa_scale_paths_{suffix}"
                _append_group(result, group, parent, "or", "성적 만점별 평점 기준")
                parent = group
            for pair in scale_pairs:
                threshold, direction, scale = pair.groups()
                context = {**basis, "scaleAlternative": True} if len(scale_pairs) > 1 else basis
                _append_rule(result, code="gpa", operator="gte" if direction == "이상" else "lte", value=float(threshold), label=f"{scale} 만점 평점 {threshold} {direction}", source_text=line, group_key=parent, unit=f"gpa_{scale}", context=context)
                consume(pair[0])
        else:
            explicit_scale = scales[0] if len(set(scales)) == 1 else None
            parent = "root"
            if re.search(r"또는|혹은", line):
                parent = f"grade_paths_{len(result.groups)}"
                _append_group(result, parent, "root", "or", "평점 또는 다른 자격 경로")
                _append_group(result, parent + "_unresolved", parent, "and", "다른 자격 경로, 해석 보완 필요")
            _append_rule(result, code="gpa", operator="gte" if gpa[2] == "이상" else "lte", value=float(gpa[1]), label=gpa[0], source_text=line, unit=f"gpa_{explicit_scale}" if explicit_scale else "gpa_scale", context=basis, group_key=parent)
            consume(gpa[0])
            if not explicit_scale:
                result.unresolved_rules.append("평점 만점 미확인: " + line)
        if not basis.get("basis"):
            result.unresolved_rules.append("성적 평가 기준 미확인: " + line)
        if basis.get("requiresReview"):
            result.unresolved_rules.append("성적 평가 기간과 예외 경로 해석 필요: " + line)
        remaining = re.sub(r"평점(?:\s*평균)?|GPA|직전\s*학기|지난\s*학기|누적|전체\s*학기|전\s*학년|총\s*평점|4\.[035]\s*(?:점\s*)?만점|기준|20\d{2}\s*(?:학년도|년)\s*[12]\s*학기", " ", remaining, flags=re.I)
        matched = True

    topik = re.search(r"TOPIK\s*([1-6])\s*급\s*(이상|이하)?(?:\s*취득자)?", line, re.I)
    if topik:
        _append_rule(result, code="topik_level", operator="lte" if topik[2] == "이하" else "gte" if topik[2] == "이상" else "eq", value=int(topik[1]), label=topik[0], source_text=line)
        consume(topik[0])
        matched = True

    residence = re.search(r"(\d+)\s*(개월|년)\s*(이상|이하)", line)
    if residence and re.search(r"거주|주민등록", line) and not re.search(r"부모|직계존속|또는|혹은", line):
        if re.match(r"^[①②③④⑤]", line):
            return False
        months = int(residence[1]) * (12 if residence[2] == "년" else 1)
        _append_rule(result, code="residence_months", operator="gte" if residence[3] == "이상" else "lte", value=months, label=f"거주 기간 {months}개월 {residence[3]}", source_text=line)
        consume(residence[0])
        city = re.search(r"본인(?:이|의)?\s*(?:주민등록(?:주소|지)?(?:가|를|는)?\s*)?([가-힣]{2,}시)(?:에|에서)?", line)
        if city:
            _append_rule(result, code="residence_city", operator="eq", value=city[1], label=f"본인 주민등록 또는 거주 지역 {city[1]}", source_text=line)
            consume(city[0])
        remaining = re.sub(r"연속|계속|거주|주민등록|기간|본인|현재", " ", remaining)
        matched = True

    if not matched:
        return False
    # A matched fragment cannot erase another criterion (one-time support, ratio,
    # nomination or other clauses). Preserve the residual verbatim for review.
    remaining = re.sub(r"^\s*(?:[①②③④⑤\-＊*ㅇㅁ]+|\d+[).]\s*)", "", remaining).strip()
    if re.sub(r"[\s,:;()/／\[\]{}.-]|지원자격|지원대상|모집대상|신청대상|대상자|대상|인\s*자$", "", remaining):
        result.unresolved_rules.append(line if residence else _clean(remaining))
    return True


def normalize_eligibility(rule_texts: list[str]) -> NormalizedEligibility:
    result = NormalizedEligibility()
    lines = [_clean(value) for value in rule_texts if _clean(value)]
    handled = _normalize_hyundai_graduate_paths(lines, result)
    handled.update(_normalize_residence_alternatives(lines, result))
    military_extension = any("군필자의 연령 예외" in line for line in lines)
    nc_graduation = next((i for i, line in enumerate(lines) if re.fullmatch(r"27년\s*2월\s*졸업\s*예정자", line)), None)
    nc_leave = next((i for i, line in enumerate(lines) if re.fullmatch(r"휴학생\s*\(학년 무관\)", line)), None)
    nc_job = next((i for i, line in enumerate(lines) if re.fullmatch(r"취업\s*준비생", line)), None)
    if nc_graduation is not None and nc_leave is not None and nc_job is not None:
        _append_group(result, "nc_participant_path", "root", "or", "취업 준비생, 졸업예정자 또는 휴학생")
        _append_rule(result, code="employment_preparation", operator="eq", value=True, label="취업 준비 목적의 참여", source_text=lines[nc_job], group_key="nc_participant_path")
        _append_rule(result, code="expected_graduation_month", operator="eq", value="2027-02", label="2027년 2월 졸업 예정", source_text=lines[nc_graduation], group_key="nc_participant_path")
        _append_rule(result, code="enrollment_status", operator="eq", value="leave", label="휴학생, 학년 무관", source_text=lines[nc_leave], group_key="nc_participant_path")
        handled.update({nc_graduation, nc_leave, nc_job})

    for index, line in enumerate(lines):
        if line == "양을 구하러 가는 건 한 사람당 한 번뿐이에요.":
            _append_rule(result, code="codetree_prior_challenge", operator="eq", value=False, label="동일 챌린지 참여 이력 없음", source_text=line)
            continue
        if line == "이전에 결제한 적이 있고 현재 구독 중이 아닌 경우에는 참여 대상에 포함되지 않아요.":
            _append_group(result, "codetree_payment_path", "root", "or", "첫 결제 전 또는 현재 구독 중")
            _append_rule(result, code="codetree_paid", operator="eq", value=False, label="기존 유료 결제 이력 없음", source_text=line, group_key="codetree_payment_path")
            _append_rule(result, code="codetree_subscribed", operator="eq", value=True, label="현재 유료 구독 중", source_text=line, group_key="codetree_payment_path")
            continue
        if line == "지난 청약통장 챌린지를 완주해 8월 31일까지 무료 학습권을 받은 분은 참여 대상에 포함되지 않아요.":
            _append_rule(result, code="codetree_bc3_free_pass", operator="eq", value=False, label="이전 청약통장 챌린지 무료 학습권 수혜 없음", source_text=line)
            continue
        if line == "카드 발급 제외: 만 75세 이상인 사람":
            _append_rule(result, code="age", operator="lte", value=74, label="카드 발급 연령: 만 75세 미만", source_text="만 75세 이상인 사람")
            continue
        if line == "카드 발급 학생 기준: 졸업까지 남은 수업연한이 2년 이내인 사람":
            _append_group(result, "card_student_path", "root", "or", "졸업자 또는 학생 수업연한 기준")
            _append_rule(result, code="enrollment_status", operator="eq", value="graduated", label="학교 졸업, 재학생 제한 비대상", source_text="졸업까지 남은 수업연한이 2년 이내인 사람", group_key="card_student_path")
            _append_rule(result, code="remaining_study_months", operator="lte", value=24, label="학생의 남은 정규 수업연한 24개월 이내", source_text="졸업까지 남은 수업연한이 2년 이내인 사람", group_key="card_student_path")
            continue
        if line == "소속 학교: POSTECH":
            _append_rule(result, code="institution", operator="eq", value="POSTECH", label="POSTECH 소속", source_text=line)
            continue
        scope = re.fullmatch(r"소속 학과: (.+) \[([a-z0-9-]+)\]", line)
        if scope:
            _append_rule(result, code="department", operator="eq", value=scope[2], label=f"{scope[1]} 소속", source_text=line)
            continue
        if re.search(r"산업체\s*재직자는?.*수혜\s*대상이\s*아님", line):
            _append_rule(
                result,
                code="industry_employee",
                operator="eq",
                value=False,
                label="산업체 재직자 제외",
                source_text=line,
            )
            continue
        if line.startswith("카드 발급 추가 제외 기준:"):
            result.unresolved_rules.append(line)
            continue
        if index in handled or _is_heading(line) or _is_not_eligibility(line):
            continue
        clean = re.sub(r"^[①②③④⑤\d)\-＊*ㅇㅁ\s]+", "", line).strip()
        if "군필자의 연령 예외" in line:
            continue  # Encoded in the age rule unit, not ignored.
        if re.fullmatch(r"하반기 취업, 인턴십 코딩테스트를 준비하는 학생|자료구조, 알고리즘 수업 내용을 문제 풀이로 복습하려는 학생|코딩테스트 공부를 처음 시작하거나, 꾸준한 문제 풀이 습관을 만들고 싶은 학생", clean):
            _append_rule(result, code="coding_study_interest", operator="eq", value=True, label="공고의 코딩 학습 대상 중 하나에 해당", source_text=line)
            continue
        if re.search(r"^고등학교 졸업 이상 취업을 준비하는 청년 누구나", clean):
            _append_rule(result, code="highest_degree_level", operator="in", value=["high_school", "associate", "bachelor", "master", "doctoral"], label="고등학교 졸업 이상", source_text=line)
            _append_rule(result, code="employment_preparation", operator="eq", value=True, label="취업 준비 목적의 참여", source_text=line)
            continue
        if re.search(r"^전공무관.*비전공자도 참여 가능", clean):
            continue  # Explicitly permits non-majors, rather than excluding them.
        if "국민내일배움카드" in line and re.search(r"발급\s*가능", line) and re.search(r"취업.*희망", line):
            _append_rule(result, code="training_card_eligible", operator="eq", value=True, label="국민내일배움카드 발급 절차", source_text=line)
        if ("국민내일배움카드" in line and re.search(r"학사학위 이상\(졸업예정자\)", line)) or re.fullmatch(r"학사학위 이상 소지자\(졸업예정자 포함\)", clean):
            if "국민내일배움카드" in line:
                _append_rule(result, code="training_card_eligible", operator="eq", value=True, label="국민내일배움카드 발급 가능", source_text=line)
            _append_group(result, "degree_path", "root", "or", "학사 이상 취득 또는 학사 졸업예정")
            _append_rule(result, code="highest_degree_level", operator="in", value=["bachelor", "master", "doctoral"], label="학사학위 이상 취득", source_text=line, group_key="degree_path")
            _append_group(result, "bachelor_expected", "degree_path", "and", "학사 졸업예정")
            _append_rule(result, code="academic_level", operator="eq", value="undergraduate", label="학부 과정", source_text=line, group_key="bachelor_expected")
            _append_rule(result, code="enrollment_status", operator="eq", value="expected_graduation", label="졸업예정", source_text=line, group_key="bachelor_expected")
            continue
        if re.search(r"(?:AI,?\s*SW\s*분야|임베디드\s*SW\s*직무\s*관련).*취업(?:을)?\s*희망(?:하는)?\s*자$", line):
            _append_rule(result, code="employment_preparation", operator="eq", value=True, label="공고의 AI 또는 SW 직무 취업 희망", source_text=line)
            continue
        if re.search(r"중간평가 대상자.*3학년.*재학생", clean):
            _append_rule(result, code="academic_level", operator="eq", value="undergraduate", label="기존 국가우수장학생 학부생", source_text=line)
            result.unresolved_rules.append(line)  # Prior selection and advancement still matter.
            continue
        if clean == "전일제 대학원생":
            _append_rule(result, code="fulltime_student", operator="eq", value=True, label=line, source_text=line)
            continue
        semester = re.fullmatch(r"대학원 현재 (\d+)학기", clean)
        if semester:
            _append_rule(result, code="graduate_semester", operator="eq", value=int(semester[1]), label=line, source_text=line)
            continue
        declarations = (
            (r"(?:3주간|3주 동안).*온라인.*(?:수강|교육|부트캠프).*가능", "online_learning_available", "온라인 사전교육 3주 자율 수강 가능"),
            (r"11월\s*28일.*29일.*해커톤.*가능", "hackathon_available", "2026년 11월 28~29일 해커톤 참여 가능"),
            (r"모든 활동에 참여 가능한", "activity_attendance", "공고에 명시된 모든 활동 일정 참여 가능"),
            (r"국민내일배움카드\s*발급\s*가능", "training_card_eligible", "국민내일배움카드 발급 가능"),
            (r"교육 기간 중 온전히 교육 참여가 가능한", "fulltime_training_available", "공고의 평일 전일 교육 참여 가능"),
            (r"평일 09:00~18:00.*주 5일", "fulltime_training_available", "광주 교육장에서 평일 09~18시, 주 5일 참여 가능"),
        )
        declaration = next(((code, label) for pattern, code, label in declarations if re.search(pattern, line)), None)
        if declaration:
            code, label = declaration
            _append_rule(result, code=code, operator="eq", value=True, label=label, source_text=line)
            # A mixed line with additional qualifications is not fully consumed.
            if code == "training_card_eligible" and re.search(r"학사|취업\s*희망", line):
                result.unresolved_rules.append(line)
            continue
        if re.search(r"대학.*대학원 재학 중인 자 지원 불가.*졸업예정자는 가능", line):
            _append_rule(result, code="enrollment_status", operator="in", value=["leave", "expected_graduation", "graduated"], label="재학 중 지원 불가, 휴학 또는 졸업예정 예외", source_text=line)
            continue
        if re.search(r"신청일 및 수강일 기준 휴학 상태이면 수강 가능", line):
            continue  # Exception included in the preceding enrollment rule.
        if _normalize_numeric_conditions(line, result):
            continue
        level = None
        if re.fullmatch(r"(?:학부|학부생)(?:\s*재학생)?", clean): level = ["undergraduate"]
        elif re.fullmatch(r"박사과정(?:\s*대학원생|\s*재학생)?", clean): level = ["doctoral"]
        elif re.fullmatch(r"석사과정(?:\s*대학원생|\s*재학생)?", clean): level = ["master"]
        elif re.fullmatch(r"석사 또는 박사과정 재학생", clean): level = ["master", "doctoral"]
        elif re.fullmatch(r"외국 국적 학부생", clean):
            _append_rule(result, code="nationality", operator="eq", value="FOREIGN", label="외국 국적", source_text=line)
            level = ["undergraduate"]
        if level:
            _append_rule(result, code="academic_level", operator="in", value=level, label="학위 과정 " + ", ".join(level), source_text=line)
            if "재학생" in clean:
                _append_rule(result, code="enrollment_status", operator="eq", value="enrolled", label="재학", source_text=line)
            continue
        year = re.search(r"(\d)학년\s*(이상)?\s*재학생", line)
        if year:
            _append_rule(result, code="academic_level", operator="eq", value="undergraduate", label="학부생", source_text=line)
            _append_rule(result, code="academic_year", operator="gte" if year[2] else "eq", value=int(year[1]), label=year[0], source_text=line)
            _append_rule(result, code="enrollment_status", operator="eq", value="enrolled", label="재학", source_text=line)
            if not re.fullmatch(r"\d학년\s*(?:이상)?\s*재학생", clean): result.unresolved_rules.append(line)
            continue
        credits = re.fullmatch(r"(\d+)학점\s*이상\s*이수", clean)
        if credits:
            _append_rule(result, code="previous_semester_credits", operator="gte", value=int(credits[1]), label=line, source_text=line)
            continue
        income = re.fullmatch(r"학자금지원구간\s*(\d+)구간\s*이내", clean)
        if income:
            _append_rule(result, code="income_band", operator="lte", value=int(income[1]), label=line, source_text=line)
            continue
        # Equivalent GPA in parentheses has no explicit scale in this source.
        # Preserve it for review rather than invent a conversion or averaging period.
        if clean == "성적 기준 적용하지 않음": continue
        mapped = False
        fully_covered = False

        age_range = re.search(r"만\s*(\d{1,2})\s*세?\s*[~∼-]\s*(\d{1,2})\s*세", line)
        if not age_range:
            age_range = re.search(
                r"만\s*(\d{1,2})\s*세?\s*이상\s*[~∼-]\s*(\d{1,2})\s*세?\s*이하",
                line,
            )
        if age_range:
            start, end = map(int, age_range.groups())
            _append_rule(
                result,
                code="age",
                operator="between",
                value=[start, end],
                label=f"만 {start}세에서 {end}세" + (", 군필은 복무기간 연장, 최대 39세" if military_extension else ""),
                source_text=line,
                unit="year_military_extension_39" if military_extension else None,
            )
            mapped = True

        if re.search(r"대한민국\s*청년|대한민국\s*국적|한국\s*국적", line):
            _append_rule(
                result,
                code="nationality",
                operator="eq",
                value="KR",
                label="대한민국 국적",
                source_text=line,
            )
            mapped = True

        if re.search(r"미취업\s*청년|취업준비생|구직자", line):
            _append_rule(
                result,
                code="employment_status",
                operator="eq",
                value="unemployed",
                label="미취업 또는 구직 상태",
                source_text=line,
            )
            mapped = True

        team_range = re.search(r"(\d+)인\s*이상\s*(\d+)인\s*이하", line)
        if team_range:
            start, end = map(int, team_range.groups())
            _append_rule(
                result,
                code="team_size",
                operator="between",
                value=[start, end],
                label=f"팀원 {start}명에서 {end}명",
                source_text=line,
            )
            mapped = True
            fully_covered = _fully_consumed(line, re.escape(team_range[0]))
            if "대학생팀" in line:
                _append_rule(
                    result,
                    code="academic_level",
                    operator="eq",
                    value="undergraduate",
                    label="대학생",
                    source_text=line,
                )
                fully_covered = _fully_consumed(line, re.escape(team_range[0]), "대학생팀")

        if "재학생 신분" in line and "휴학생 가능" in line and "졸업생 불가" in line:
            _append_rule(
                result,
                code="enrollment_status",
                operator="in",
                value=["enrolled", "leave"],
                label="재학생 또는 휴학생, 졸업생 제외",
                source_text=line,
            )
            mapped = True
            fully_covered = _fully_consumed(line, r"(?:사업 종료 시점까지|참여부터 종료(?:까지)?)", "재학생 신분인 자", "휴학생 가능", "졸업생 불가")
            if "참여부터 종료" in line or "사업 종료 시점까지" in line:
                _append_rule(result, code="enrollment_commitment", operator="eq", value=True, label="활동 종료까지 재학 또는 휴학 신분 유지 가능", source_text=line)

        if "학사학위 이상" in line:
            _append_rule(
                result,
                code="highest_degree_level",
                operator="in",
                value=["bachelor", "master", "doctoral"],
                label="학사학위 이상",
                source_text=line,
            )
            mapped = True

        if "안동시에 주민등록" in line:
            _append_rule(
                result,
                code="residence_city",
                operator="eq",
                value="안동시",
                label="안동시 주민등록",
                source_text=line,
            )
            _append_rule(
                result,
                code="academic_level",
                operator="eq",
                value="undergraduate",
                label="국내 대학생",
                source_text=line,
            )
            _append_rule(
                result,
                code="enrollment_status",
                operator="in",
                value=["enrolled", "leave"],
                label="재학생 또는 휴학생",
                source_text=line,
            )
            mapped = True

        if re.search(r"전공.*제한\s*없이|학년.*제한\s*없이", line):
            fully_covered = True
        if re.search(r"팀\s*단위로만|개인\s*지원은\s*불가", line):
            if any(rule.criterion_code == "team_size" for rule in result.rules):
                fully_covered = True

        if age_range and re.search(r"대한민국\s*청년", line):
            fully_covered = _fully_consumed(line, re.escape(age_range[0]), r"대한민국\s*청년", "누구나")
        elif age_range and re.search(r"미취업\s*청년", line):
            fully_covered = _fully_consumed(line, re.escape(age_range[0]), r"미취업\s*청년", "누구나")

        if not mapped and not fully_covered:
            result.unresolved_rules.append(line)
        elif mapped and not fully_covered:
            result.unresolved_rules.append(line)

    result.unresolved_rules = list(dict.fromkeys(result.unresolved_rules))
    return result
