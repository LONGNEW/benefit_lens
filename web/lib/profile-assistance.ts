import type { PersonalProfile } from "./browser-profile";
import type { Match, RuleResult } from "./eligibility";
import type { EligibilityNode } from "./opportunity-data";

export type PublicDepartment = { code: string; department: string };
export type QuestionField = keyof PersonalProfile | "affiliation";
export type ProfileQuestion = { field: QuestionField; reasons: string[] };

export function withAffiliation(profile: PersonalProfile, value: string, departments: PublicDepartment[]): PersonalProfile {
  if (value.startsWith("department:")) {
    const code = value.slice("department:".length);
    const department = departments.find(item => item.code === code);
    if (!department) return profile;
    // The user explicitly chose a POSTECH-labelled public department option.
    // No degree, enrollment or other personal attributes follow from this choice.
    return {...profile, institution: "POSTECH", department: code, graduateSchool: department.department.match(/^(.+?대학원)/)?.[1] ?? ""};
  }
  if (!["", "POSTECH", "OTHER"].includes(value)) return profile;
  return {...profile, institution: value, department: "", graduateSchool: ""};
}

function fieldsForRule(profile: PersonalProfile, rule: RuleResult): QuestionField[] {
  if (rule.field === "institution" || rule.field === "department") return ["affiliation"];
  if (rule.answerKey) return []; // Legacy per-notice answers are not global facts.
  if (rule.criterionCode === "gpa") {
    // Missing source interpretation cannot be repaired by asking for more
    // personal data. Request only facts needed by a comparison-ready rule.
    if (!rule.context?.basis || rule.context.requiresReview || rule.context.basis === "specified_window" || !/^gpa_\d+(?:\.\d+)?$/.test(rule.unit ?? "")) return [];
    const fields: QuestionField[] = [];
    if (!profile.gpa?.trim()) fields.push("gpa");
    else if (!profile.gpaScale?.trim()) fields.push("gpaScale");
    if (profile.gpaBasis !== rule.context.basis) fields.push("gpaBasis");
    if (rule.context.evaluationPeriod && profile.gpaEvaluationPeriod !== rule.context.evaluationPeriod) fields.push("gpaEvaluationPeriod");
    return fields;
  }
  if (rule.field === "age" && rule.unit === "year_military_extension_39" && profile.age.trim() && Array.isArray(rule.value) && Number(profile.age) > Number(rule.value[1])) return ["militaryServiceMonths"];
  if (rule.field === "remainingStudyMonths" && rule.derivation
    && profile.institution === rule.derivation.institution
    && ["master", "doctoral"].includes(profile.academicLevel)
    && rule.derivation.semestersByLevel[profile.academicLevel as "master" | "doctoral"]
    && ["enrolled", "leave", "expected_graduation"].includes(profile.enrollmentStatus)) return ["graduateSemester"];
  return [rule.field];
}

export function questionsForMatch(profile: PersonalProfile, match: Match) {
  const byId = new Map(match.ruleResults.map(rule => [rule.id, rule]));
  type Plan = { state: "pass" | "fail" | "unknown"; rules: RuleResult[]; alternative: boolean };
  function plan(node: EligibilityNode): Plan {
    if (node.kind === "rule") {
      const rule = byId.get(node.id);
      const state = rule?.result ?? "unknown";
      return {state, rules: state === "unknown" && rule ? [rule] : [], alternative: false};
    }
    const children = node.children.map(plan);
    if (!children.length) return {state: "unknown", rules: [], alternative: false};
    if (node.operator === "not") {
      const child = children[0];
      return {...child, state: child.state === "pass" ? "fail" : child.state === "fail" ? "pass" : "unknown"};
    }
    if (node.operator === "or") {
      if (children.some(child => child.state === "pass")) return {state: "pass", rules: [], alternative: false};
      const candidates = children.filter(child => child.state === "unknown");
      if (!candidates.length) return {state: "fail", rules: [], alternative: false};
      const cost = (item: Plan) => new Set(item.rules.flatMap(rule => fieldsForRule(profile, rule))).size;
      candidates.sort((a, b) => cost(a) - cost(b));
      return {...candidates[0], alternative: candidates.length > 1 || candidates[0].alternative};
    }
    if (children.some(child => child.state === "fail")) return {state: "fail", rules: [], alternative: false};
    return {state: children.every(child => child.state === "pass") ? "pass" : "unknown",
      rules: children.flatMap(child => child.rules), alternative: children.some(child => child.alternative)};
  }
  const result = match.eligibilityCriteria ? plan(match.eligibilityCriteria) : null;
  const questions = new Map<QuestionField, ProfileQuestion>();
  for (const rule of result?.rules ?? []) for (const field of fieldsForRule(profile, rule)) {
    const existing = questions.get(field) ?? {field, reasons: []};
    if (!existing.reasons.includes(rule.label)) existing.reasons.push(rule.label);
    questions.set(field, existing);
  }
  return {questions: [...questions.values()], alternative: result?.alternative ?? false};
}

export function prioritizeMatches(matches: Match[], profile: PersonalProfile): Match[] {
  if (!Object.entries(profile).some(([field, value]) => field !== "gpaScale" && typeof value === "string" && value.trim())) return matches;
  const ranks: Record<Match["status"], number> = {possible: 0, candidate: 1, applicable: 1, profile: 2, policy: 2, review: 3, period: 4, upcoming: 5, closed: 6, unmet: 7};
  return [...matches].sort((a, b) => ranks[a.status] - ranks[b.status]
    || b.ruleResults.filter(rule => rule.result === "pass").length - a.ruleResults.filter(rule => rule.result === "pass").length);
}
