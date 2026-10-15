import type { EligibilityGroup, EligibilityNode, Opportunity, ProfileField, Rule } from "./opportunity-data";

export type Profile = Record<ProfileField, string> & { gpaScale: string; gpaBasis?: string; gpaEvaluationPeriod?: string; birthYear?: string; militaryServiceMonths?: string; conditionAnswers?: Record<string, string> };
export type CriterionResult = "pass" | "fail" | "unknown";
export type RuleResult = Rule & { result: CriterionResult };
export type MatchStatus =
  | "possible"
  | "candidate"
  | "applicable"
  | "policy"
  | "profile"
  | "review"
  | "period"
  | "unmet"
  | "closed"
  | "upcoming";
export type Match = Opportunity & { status: MatchStatus; comparisonStatus?: MatchStatus; verificationStatus?: "approved" | "candidate"; eligibilityResult: CriterionResult; ruleResults: RuleResult[]; eligibilityCriteria: EligibilityNode | null; requirements: Rule[]; derivedFacts: Array<{ text: string; sourceUrl: string }> };

export function isApprovedOpportunity(opportunity: Opportunity): boolean {
  return opportunity.publicationStatus === "reviewed" && opportunity.rightsStatus === "approved" && opportunity.productionAllowed === true;
}

// Legacy rows use the same canonical classification as newly collected rows.
const participationFields = new Set<ProfileField>([
  "onlineLearningAvailable", "hackathonAvailable", "activityAttendance",
  "fullTimeTrainingAvailable", "codingStudyInterest",
  "employmentPreparation", "trainingCardEligible", "enrollmentCommitment",
]);

export function isParticipationRequirement(rule: Rule): boolean {
  return rule.conditionKind === "requirement" || participationFields.has(rule.field);
}

export function requirementText(rule: Rule): string {
  if (rule.field === "codingStudyInterest") return "공고에서 안내한 코딩 학습과 문제 풀이에 참여";
  if (rule.field === "employmentPreparation") return /AI|SW|직무/.test(rule.label) ? "공고의 AI 또는 SW 직무 교육과 취업 연계 과정 참여" : "취업 준비와 역량 개발을 위한 프로그램 참여";
  if (rule.field === "trainingCardEligible") return "수강 전 국민내일배움카드 발급 신청과 고용센터 심사 완료";
  if (rule.field === "enrollmentCommitment") return "활동 종료까지 재학 또는 휴학 신분 유지";
  return rule.label.replace(/\s*가능$/, "");
}

export function separateConditions(opportunity: Opportunity): { eligibilityCriteria: EligibilityNode | null; requirements: Rule[] } {
  const requirements = new Map((opportunity.requirements ?? []).map(rule => [rule.id, rule]));
  for (const rule of opportunity.rules) if (isParticipationRequirement(rule)) requirements.set(rule.id, rule);
  function project(node: EligibilityNode): EligibilityNode | null {
    if (node.kind === "rule") {
      if (!isParticipationRequirement(node)) return node;
      requirements.set(node.id, node);
      return null;
    }
    if (node.children.length === 0) return node;
    const children = node.children.map(project);
    // An alternative with no applicant restrictions remains an unrestricted path.
    if (node.operator === "or" && children.some(child => child === null)) return null;
    const retained = children.filter((child): child is EligibilityNode => child !== null);
    return retained.length ? {...node, children: retained} : null;
  }
  const criteria = opportunity.criteria ?? fallbackCriteria(opportunity.rules);
  return {eligibilityCriteria: criteria ? project(criteria) : null, requirements: [...requirements.values()]};
}

function scalar(profile: Profile, field: ProfileField): string | number | boolean | null {
  const raw = profile[field];
  if (typeof raw !== "string" || raw.trim() === "") return null;
  if (["codetreePaid", "codetreeSubscribed", "codetreePriorChallenge", "codetreeBc3FreePass"].includes(field)) return raw === "true" ? true : raw === "false" ? false : null;
  if (["employmentPreparation", "courseworkCompleted", "regionalHighSchool", "enrollmentCommitment", "onlineLearningAvailable", "hackathonAvailable", "activityAttendance", "trainingCardEligible", "fullTimeTrainingAvailable", "codingStudyInterest", "fullTimeStudent", "industryEmployee"].includes(field)) return raw === "true" ? true : raw === "false" ? false : null;
  if ([
    "age", "year", "graduateSemester", "previousSemesterCredits", "gpa", "incomeBand",
    "residenceMonths", "topikLevel", "teamSize", "remainingStudyMonths",
  ].includes(field)) {
    const value = Number(raw);
    if (!Number.isFinite(value) || value < 0) return null;
    if (["age", "year", "graduateSemester", "incomeBand", "residenceMonths", "topikLevel", "teamSize", "remainingStudyMonths"].includes(field) && !Number.isInteger(value)) return null;
    if (field === "gpa" && value > Number(profile.gpaScale)) return null;
    return value;
  }
  return raw.trim();
}

export function derivedStudyMonths(profile: Profile, rule: Rule): number | null {
  if (rule.field !== "remainingStudyMonths" || !rule.derivation || profile.institution !== rule.derivation.institution) return null;
  if (!["enrolled", "leave", "expected_graduation"].includes(profile.enrollmentStatus)) return null;
  if (!["master", "doctoral"].includes(profile.academicLevel)) return null;
  const duration = rule.derivation.semestersByLevel[profile.academicLevel as "master" | "doctoral"];
  const semester = Number(profile.graduateSemester);
  if (!duration || !Number.isInteger(semester) || semester < 1 || semester > 30) return null;
  // MOEL includes the current semester in the remaining formal course duration.
  return Math.max(0, duration - semester + 1) * 6;
}

function comparable(profile: Profile, rule: Rule): { actual: string | number | boolean; expected: Rule["value"] } | null {
  const actual = derivedStudyMonths(profile, rule) ?? scalar(profile, rule.field);
  if (actual === null) return null;
  if (rule.criterionCode === "gpa" && typeof actual === "number") {
    const profileScale = Number(profile.gpaScale);
    const ruleScale = Number(rule.unit?.match(/^gpa_(\d+(?:\.\d+)?)$/)?.[1]);
    // Different GPA scales do not have a justified proportional conversion.
    if (![4, 4.3, 4.5].includes(profileScale) || ![4, 4.3, 4.5].includes(ruleScale) || profileScale !== ruleScale) return null;
    const basis = rule.context?.basis;
    if (rule.context?.requiresReview || !basis || basis === "specified_window" || basis !== profile.gpaBasis) return null;
    if (rule.context?.evaluationPeriod && rule.context.evaluationPeriod !== profile.gpaEvaluationPeriod) return null;
    const thresholds = Array.isArray(rule.value) ? rule.value : [rule.value];
    if (!thresholds.length || thresholds.some(value => typeof value !== "number" || value < 0 || value > ruleScale)) return null;
    return { actual, expected: rule.value };
  }
  return { actual, expected: rule.value };
}

export function evaluateRule(profile: Profile, rule: Rule, now = new Date()): CriterionResult {
  if (rule.answerKey) profile = {...profile, [rule.field]: profile.conditionAnswers?.[rule.answerKey] ?? ""};
  if (rule.field === "age" && !profile.age?.trim() && profile.birthYear?.trim()) {
    const birthYear = Number(profile.birthYear);
    const year = Number(new Intl.DateTimeFormat("en", {year: "numeric", timeZone: "Asia/Seoul"}).format(now));
    if (!Number.isInteger(birthYear) || birthYear < year - 100 || birthYear > year) return "unknown";
    const results = [year - birthYear - 1, year - birthYear].map(age => evaluateRule({...profile, age: String(age)}, rule, now));
    return results[0] === results[1] ? results[0] : "unknown";
  }
  // This guard belongs only to source-specified scale alternatives. A single
  // threshold on an incompatible GPA scale remains unknown, never converted.
  if (rule.criterionCode === "gpa" && rule.context?.scaleAlternative) {
    const actual = scalar(profile, rule.field);
    const profileScale = Number(profile.gpaScale);
    const ruleScale = Number(rule.unit?.match(/^gpa_(\d+(?:\.\d+)?)$/)?.[1]);
    if (typeof actual === "number" && [4, 4.3, 4.5].includes(profileScale) && [4, 4.3, 4.5].includes(ruleScale) && profileScale !== ruleScale) return "fail";
  }
  const values = comparable(profile, rule);
  if (!values) return "unknown";
  const { actual, expected } = values;
  if (rule.unit === "year_military_extension_39" && typeof actual === "number" && Array.isArray(expected)) {
    const [lower, upper] = expected.map(Number);
    if (actual < lower || actual > 39) return "fail";
    if (actual <= upper) return "pass";
    if (!profile.militaryServiceMonths?.trim()) return "unknown";
    const months = Number(profile.militaryServiceMonths);
    if (!Number.isInteger(months) || months < 0 || months > 120) return "unknown";
    // Keep the exact proportional upper bound; do not invent rounding of partial years.
    return actual <= Math.min(39, upper + months / 12) ? "pass" : "fail";
  }
  if (rule.operator === "eq") return actual === expected ? "pass" : "fail";
  if (rule.operator === "neq") return actual !== expected ? "pass" : "fail";
  if (rule.operator === "in" || rule.operator === "not_in") {
    if (!Array.isArray(expected)) return "unknown";
    const included = expected.includes(actual);
    return rule.operator === "in" ? (included ? "pass" : "fail") : (included ? "fail" : "pass");
  }
  if (rule.operator === "between") {
    if (typeof actual !== "number" || !Array.isArray(expected) || expected.length !== 2) return "unknown";
    const [start, end] = expected;
    if (typeof start !== "number" || typeof end !== "number") return "unknown";
    return actual >= start && actual <= end ? "pass" : "fail";
  }
  if (typeof actual !== "number" || typeof expected !== "number") return "unknown";
  if (rule.operator === "gte") return actual >= expected ? "pass" : "fail";
  if (rule.operator === "lte") return actual <= expected ? "pass" : "fail";
  return "unknown";
}

function combine(operator: EligibilityGroup["operator"], results: CriterionResult[]): CriterionResult {
  if (results.length === 0) return "unknown";
  if (operator === "not") {
    const result = results[0] ?? "unknown";
    return result === "pass" ? "fail" : result === "fail" ? "pass" : "unknown";
  }
  if (operator === "or") {
    if (results.includes("pass")) return "pass";
    return results.every((result) => result === "fail") ? "fail" : "unknown";
  }
  if (results.includes("fail")) return "fail";
  return results.every((result) => result === "pass") ? "pass" : "unknown";
}

function evaluateNode(profile: Profile, node: EligibilityNode, output: RuleResult[], now: Date): CriterionResult {
  if (node.kind === "rule") {
    const result = evaluateRule(profile, node, now);
    output.push({ ...node, result });
    return result;
  }
  return combine(node.operator, node.children.map((child) => evaluateNode(profile, child, output, now)));
}

function fallbackCriteria(rules: Rule[]): EligibilityGroup | null {
  if (rules.length === 0) return null;
  return { kind: "group", id: "root", operator: "and", label: "전체 필수 조건", children: rules };
}

function hasEmptyGroup(node: EligibilityNode | null): boolean {
  if (!node || node.kind === "rule") return false;
  return node.children.length === 0 || node.children.some(hasEmptyGroup);
}

export function evaluateOpportunity(profile: Profile, opportunity: Opportunity, now = new Date()): Match {
  const ruleResults: RuleResult[] = [];
  const {eligibilityCriteria, requirements} = separateConditions(opportunity);
  const eligibilityResult = eligibilityCriteria ? evaluateNode(profile, eligibilityCriteria, ruleResults, now) : requirements.length > 0 ? "pass" : "unknown";
  let status: MatchStatus;
  const opens = opportunity.opensAt ? Date.parse(opportunity.opensAt) : NaN;
  const closes = opportunity.closesAt ? Date.parse(opportunity.closesAt) : NaN;
  const expired = Number.isFinite(closes) && now.getTime() > closes;
  const future = Number.isFinite(opens) && now.getTime() < opens;
  const validWindow = Number.isFinite(opens) && Number.isFinite(closes) && opens <= closes && !expired && !future;
  const unresolved = (opportunity.unresolvedCriteria?.length ?? 0) > 0 || (opportunity.unresolvedRuleCount ?? 0) > 0;
  const sourceIncomplete = opportunity.matchMode !== "structured" || unresolved
    || (!eligibilityCriteria && requirements.length === 0) || hasEmptyGroup(eligibilityCriteria);
  if (opportunity.recordKind === "policy") {
    // A policy has no application window. Evaluate its known audience scope without
    // presenting the result as a currently open recruitment or a selection promise.
    if (eligibilityResult === "fail") status = "unmet";
    else if (eligibilityResult === "pass") status = "applicable";
    else status = "policy";
  } else if (expired || opportunity.lifecycleStatus === "closed") status = "closed";
  else if (eligibilityResult === "fail") status = "unmet";
  else if (future || (opportunity.lifecycleStatus === "upcoming" && !validWindow)) status = "upcoming";
  else if (!validWindow && opportunity.lifecycleStatus !== "rolling") status = "period";
  else if (sourceIncomplete) status = "review";
  else if (eligibilityResult !== "pass") status = "profile";
  else status = "possible";
  const derivedFacts = ruleResults.flatMap(rule => {
    const months = derivedStudyMonths(profile, rule);
    return months !== null && rule.derivation ? [{text: `학교 학칙의 정규 수업연한 ${rule.derivation.semestersByLevel[profile.academicLevel as "master" | "doctoral"]}학기, 현재 ${profile.graduateSemester}학기: 남은 수업연한 ${months}개월`, sourceUrl: rule.derivation.sourceUrl}] : [];
  });
  const comparisonStatus = status;
  const verificationStatus = isApprovedOpportunity(opportunity) ? "approved" : "candidate";
  if (verificationStatus === "candidate" && (status === "possible" || status === "applicable")) status = "candidate";
  return { ...opportunity, status, comparisonStatus, verificationStatus, eligibilityResult, ruleResults, eligibilityCriteria, requirements, derivedFacts };
}
