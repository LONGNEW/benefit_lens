/** Pure, local demonstration comparator. Thresholds and date windows come from
 * a reviewed source config. No GPA conversion, expiry inference or award decision.
 */
export type ComparisonResult = "pass" | "fail" | "unknown";
export type EducationPath = "undergraduate" | "graduate";
export type SourcePath = "undergraduate_enrolled" | "graduate_new" | "graduate_enrolled" | "undergraduate" | "graduate";
export type GradeMetric = "GPA" | "percentile";
export type EnglishTest = "TOEIC" | "TOEFL_iBT" | "IELTS" | "NEW_TEPS" | "TOEIC_SPEAKING" | "OPIc" | "TEPS_SPEAKING";
export type Applicability = "required" | "not_applicable" | "unknown";

export interface SourceEvidence { id: string; locator: string; quote: string; [key: string]: unknown }
export interface AcademicSource {
  source_id: string; title: string; url: string; document_version: string; published_at: string | null;
  evidence: SourceEvidence[]; [key: string]: unknown;
}
export interface GradeFact {
  metric: GradeMetric; score: number; scale: number; period: string; context: string;
  status?: "confirmed" | "unknown" | "invalid"; [key: string]: unknown;
}
export interface EnglishFact {
  test_type: EnglishTest; score: number | string; test_date: string; score_scale?: number | string;
  status?: "confirmed" | "unknown" | "invalid"; [key: string]: unknown;
}
export interface AcademicProfile {
  education_path?: EducationPath; source_path?: SourcePath;
  grades?: GradeFact[]; english?: EnglishFact[]; english_inventory_complete?: boolean;
}
export interface ScoreDomain { minimum?: number; maximum?: number; step?: number; evidence_ids?: string[]; [key: string]: unknown }
export interface GradeAlternative {
  id: string; metric: GradeMetric; minimum_score: number; scale: number; period: string; context: string;
  evidence_ids: string[]; supported?: boolean; [key: string]: unknown;
}
export interface ExplicitDateWindow { from: string; to: string; evidence_ids: string[] }
export interface EnglishAlternative {
  id: string; test_type: EnglishTest; minimum_score: number | string;
  score_kind: "numeric" | "ordered_enum"; explicit_date_window?: ExplicitDateWindow;
  score_domain?: ScoreDomain; score_scale?: number | string; ordered_values?: string[];
  supported?: boolean; evidence_ids: string[]; [key: string]: unknown;
}
export interface GradeRequirement { applicability?: Applicability; alternatives: GradeAlternative[]; evidence_ids?: string[]; reason?: string }
export interface EnglishRequirement { applicability: Applicability; alternatives: EnglishAlternative[]; evidence_ids?: string[]; reason?: string }
export interface UncomparedRequirement { id: string; text: string; role: string; scope?: string; locator?: string; evidence_ids: string[]; [key: string]: unknown }
export interface PathRequirements {
  education_path: EducationPath; grade: GradeRequirement; english: EnglishRequirement;
  uncompared_requirements: UncomparedRequirement[]; [key: string]: unknown;
}
export interface AcademicRuleSet {
  rule_version: string; source: AcademicSource; paths: Partial<Record<SourcePath, PathRequirements>>;
  uncompared_requirements?: UncomparedRequirement[]; semantic_limits?: string[]; [key: string]: unknown;
}
export interface ComparisonTrace {
  rule_id: string; result: ComparisonResult; reason: string; evidence_ids: string[];
  input_index?: number; actual?: unknown; expected?: unknown; records?: ComparisonTrace[];
}
export interface RequirementComparison {
  applicability: Applicability; result: ComparisonResult; reasons: string[]; traces: ComparisonTrace[];
  evidence_ids?: string[]; source_reason?: string;
}
export interface AcademicComparison {
  label: "성적, 어학 요건 비교"; comparison_result: ComparisonResult;
  overall_eligibility: "not_evaluated"; selection_and_payment: "not_evaluated";
  scope: string; education_path: string | null; source_path: string | null;
  rule_version: string | null; source: AcademicSource | null;
  grade: RequirementComparison; english: RequirementComparison;
  uncompared_requirements: UncomparedRequirement[]; semantic_limits: string[]; validation_errors: string[];
}

const educationPaths = new Set<unknown>(["undergraduate", "graduate"]);
const sourcePaths = new Set<unknown>(["undergraduate_enrolled", "graduate_new", "graduate_enrolled", "undergraduate", "graduate"]);
const tests = new Set<unknown>(["TOEIC", "TOEFL_iBT", "IELTS", "NEW_TEPS", "TOEIC_SPEAKING", "OPIc", "TEPS_SPEAKING"]);
const applicability = new Set<unknown>(["required", "not_applicable", "unknown"]);
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const nonempty = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const number = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const clone = <T>(value: T): T => value === undefined ? value : JSON.parse(JSON.stringify(value));
const array = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const confirmed = (value: Record<string, unknown>) => value.status === undefined || value.status === "confirmed";

/** Date-only comparison: never accepts an overflow such as 2025-02-30. */
export function validIsoDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/u.test(value)) return false;
  const parsed = Date.parse(`${value}T00:00:00.000Z`);
  return Number.isFinite(parsed) && new Date(parsed).toISOString().slice(0, 10) === value;
}
export function combineResults(operator: "and" | "or", results: ComparisonResult[]): ComparisonResult {
  if (!results.length) return "unknown";
  if (operator === "or") return results.includes("pass") ? "pass" : results.every(result => result === "fail") ? "fail" : "unknown";
  return results.includes("fail") ? "fail" : results.every(result => result === "pass") ? "pass" : "unknown";
}
const unavailable = (reason: string, state: Applicability = "unknown"): RequirementComparison => ({ applicability: state, result: "unknown", reasons: [reason], traces: [] });
const trace = (rule: { id: string; evidence_ids: string[] }, result: ComparisonResult, reason: string, actual?: unknown, expected?: unknown): ComparisonTrace => ({ rule_id: rule.id, result, reason, evidence_ids: [...rule.evidence_ids], actual: clone(actual), expected: clone(expected) });

/** Config syntax and evidence references only. This is not source review approval. */
export function validateAcademicConfig(config: unknown): string[] {
  const errors: string[] = [];
  const check = (condition: unknown, message: string) => { if (!condition) errors.push(message); };
  if (!record(config)) return ["config_missing"];
  check(nonempty(config.rule_version), "rule_version_missing");
  const source = config.source;
  check(record(source), "source_missing");
  const evidenceIds = new Set<unknown>();
  if (record(source)) {
    for (const key of ["source_id", "title", "url", "document_version"]) check(nonempty(source[key]), `source_${key}_missing`);
    check(typeof source.url === "string" && /^https:\/\//u.test(source.url), "source_url_must_be_https");
    check(Object.hasOwn(source, "published_at") && (source.published_at === null || nonempty(source.published_at)), "source_published_at_missing");
    check(Array.isArray(source.evidence) && source.evidence.length > 0, "source_evidence_missing");
    for (const item of array(source.evidence)) {
      if (!record(item)) { errors.push("evidence_invalid"); continue; }
      check(nonempty(item.id) && !evidenceIds.has(item.id), "evidence_id_missing_or_duplicate");
      evidenceIds.add(item.id);
      check(nonempty(item.locator) && nonempty(item.quote), `evidence_${item.id}_locator_or_quote_missing`);
    }
  }
  const refs = (value: unknown, label: unknown) => check(Array.isArray(value) && value.length > 0 && value.every(id => typeof id === "string" && evidenceIds.has(id)), `${label}_evidence_unresolved`);
  check(record(config.paths) && Object.keys(config.paths).length > 0, "paths_missing");
  if (!record(config.paths)) return errors;
  for (const [key, path] of Object.entries(config.paths)) {
    const ids = new Set<unknown>();
    check(sourcePaths.has(key), `unsupported_source_path:${key}`);
    if (!record(path)) { errors.push(`path_invalid:${key}`); continue; }
    check(educationPaths.has(path.education_path), `education_path_invalid:${key}`);
    check(Array.isArray(path.uncompared_requirements) && path.uncompared_requirements.every(item => record(item) && nonempty(item.id) && nonempty(item.text) && nonempty(item.role)), `uncompared_requirements_missing:${key}`);
    for (const item of array(path.uncompared_requirements)) if (record(item)) refs(item.evidence_ids, `${key}_uncompared_${item.id}`);
    for (const stage of ["grade", "english"] as const) {
      const requirement = path[stage];
      if (!record(requirement)) { errors.push(`${stage}_requirement_missing:${key}`); continue; }
      const applicable = stage === "grade" ? requirement.applicability ?? "required" : requirement.applicability;
      check(applicability.has(applicable), `${stage}_applicability_invalid:${key}`);
      check(Array.isArray(requirement.alternatives), `${stage}_alternatives_missing:${key}`);
      const alternativeLength = (requirement.alternatives as {length: number} | null | undefined)?.length;
      if (applicable === "required") check((alternativeLength ?? 0) > 0, `${stage}_required_without_rules:${key}`);
      if (applicable === "not_applicable") {
        if (requirement.evidence_ids !== undefined) refs(requirement.evidence_ids, `${key}_${stage}_not_applicable`);
        check(alternativeLength === 0, `${key}_${stage}_not_applicable_has_rules`);
      }
      for (const rule of array(requirement.alternatives)) {
        if (!record(rule)) { errors.push(`${stage}_rule_invalid:${key}`); continue; }
        check(nonempty(rule.id) && !ids.has(rule.id), `rule_id_missing_or_duplicate:${rule.id}`); ids.add(rule.id);
        refs(rule.evidence_ids, rule.id);
        if (rule.supported !== undefined) check(typeof rule.supported === "boolean", `rule_supported_invalid:${rule.id}`);
        if (stage === "grade") {
          check(["GPA", "percentile"].includes(rule.metric as string), `grade_metric_invalid:${rule.id}`);
          check(number(rule.scale) && rule.scale > 0, `grade_scale_invalid:${rule.id}`);
          check(number(rule.minimum_score) && rule.minimum_score >= 0 && rule.minimum_score <= (rule.scale as number), `grade_threshold_invalid:${rule.id}`);
          check(nonempty(rule.period) && nonempty(rule.context), `grade_period_context_missing:${rule.id}`);
          if (rule.metric === "percentile") check(rule.scale === 100, `percentile_scale_must_be_100:${rule.id}`);
        } else {
          check(tests.has(rule.test_type), `english_test_type_invalid:${rule.id}`);
          check(["numeric", "ordered_enum"].includes(rule.score_kind as string), `score_kind_invalid:${rule.id}`);
          if (rule.score_kind === "numeric") check(number(rule.minimum_score) && rule.minimum_score >= 0 && rule.test_type !== "OPIc", `numeric_threshold_invalid:${rule.id}`);
          if (rule.score_kind === "ordered_enum") {
            check(nonempty(rule.minimum_score) && rule.test_type === "OPIc", `ordered_threshold_invalid:${rule.id}`);
            if (rule.ordered_values !== undefined) check(Array.isArray(rule.ordered_values) && rule.ordered_values.length > 0 && rule.ordered_values.every(nonempty) && new Set(rule.ordered_values).size === rule.ordered_values.length && rule.ordered_values.includes(rule.minimum_score as string), `ordered_values_invalid:${rule.id}`);
          }
          if (rule.score_scale !== undefined) check(nonempty(rule.score_scale) || (number(rule.score_scale) && rule.score_scale > 0), `english_scale_invalid:${rule.id}`);
          if (rule.score_domain !== undefined) {
            check(record(rule.score_domain) && rule.score_kind === "numeric", `score_domain_invalid:${rule.id}`);
            if (record(rule.score_domain)) {
              if (rule.score_domain.evidence_ids !== undefined) refs(rule.score_domain.evidence_ids, `${rule.id}_domain`);
              for (const member of ["minimum", "maximum"]) if (rule.score_domain[member] !== undefined) check(number(rule.score_domain[member]) && rule.score_domain[member] >= 0, `score_domain_bound_invalid:${rule.id}`);
              if (rule.score_domain.step !== undefined) check(number(rule.score_domain.step) && rule.score_domain.step > 0, `score_domain_step_invalid:${rule.id}`);
              if (rule.score_domain.minimum !== undefined && rule.score_domain.maximum !== undefined) check((rule.score_domain.minimum as number) <= (rule.score_domain.maximum as number), `score_domain_reversed:${rule.id}`);
            }
          }
          if (rule.explicit_date_window !== undefined) {
            const window = rule.explicit_date_window;
            check(record(window) && validIsoDate(window.from) && validIsoDate(window.to) && window.from <= window.to, `explicit_date_window_invalid:${rule.id}`);
            refs(record(window) ? window.evidence_ids : undefined, `${rule.id}_date_window`);
          }
        }
      }
    }
  }
  return errors;
}

function gradeRule(facts: unknown[], rule: GradeAlternative, alternatives: GradeAlternative[]): ComparisonTrace {
  const expected = { metric: rule.metric, minimum_score: rule.minimum_score, scale: rule.scale, period: rule.period, context: rule.context };
  if (rule.supported === false) return trace(rule, "unknown", "configured_out_of_demo_scope", null, expected);
  const matching = facts.filter((fact): fact is Record<string, unknown> => record(fact) && fact.metric === rule.metric && fact.period === rule.period && fact.context === rule.context);
  if (!matching.length) return trace(rule, "unknown", "grade_missing_or_context_mismatch", null, expected);
  if (matching.some(fact => !confirmed(fact) || !number(fact.score) || !number(fact.scale) || fact.scale <= 0 || fact.score < 0 || fact.score > fact.scale)) return trace(rule, "unknown", "invalid_or_unconfirmed_grade_fact", matching, expected);
  const onScale = matching.filter(fact => fact.scale === rule.scale);
  if (!onScale.length) {
    // An explicitly configured alternative on the known reported scale is a
    // branch guard, not a conversion. Without that alternative remain unknown.
    const explicitOtherScale = matching.every(fact => alternatives.some(other => other.metric === rule.metric && other.period === rule.period && other.context === rule.context && other.scale === fact.scale));
    return trace(rule, explicitOtherScale ? "fail" : "unknown", explicitOtherScale ? "different_configured_scale_branch" : "scale_mismatch_no_conversion", matching, expected);
  }
  if (new Set(onScale.map(fact => fact.score)).size !== 1) return trace(rule, "unknown", "conflicting_grade_facts", onScale, expected);
  return trace(rule, (onScale[0].score as number) >= rule.minimum_score ? "pass" : "fail", "compared_exact_metric_scale_period_context", onScale[0], expected);
}
export function compareGrade(profile: AcademicProfile, requirement: GradeRequirement): RequirementComparison {
  const state = requirement.applicability ?? "required";
  if (state !== "required") return { ...unavailable(state === "not_applicable" ? "not_applicable" : "grade_requirement_unresolved", state), evidence_ids: clone(requirement.evidence_ids ?? []), source_reason: requirement.reason };
  const traces = requirement.alternatives.map(rule => gradeRule(array(profile.grades), rule, requirement.alternatives));
  return { applicability: "required", result: combineResults("or", traces.map(item => item.result)), reasons: [...new Set(traces.filter(item => item.result === "unknown").map(item => item.reason))], traces };
}

function englishRecord(fact: Record<string, unknown>, index: number, rule: EnglishAlternative): ComparisonTrace {
  const expected = { test_type: rule.test_type, minimum_score: rule.minimum_score, score_kind: rule.score_kind, explicit_date_window: rule.explicit_date_window ?? null, score_scale: rule.score_scale ?? null, score_domain: rule.score_domain ?? null, ordered_values: rule.ordered_values ?? null };
  const result = (value: ComparisonResult, reason: string) => ({ ...trace(rule, value, reason, fact, expected), input_index: index });
  if (!confirmed(fact)) return result("unknown", "unconfirmed_english_fact");
  if (!validIsoDate(fact.test_date)) return result("unknown", "missing_or_invalid_test_date");
  if (rule.supported === false) return result("unknown", "configured_out_of_demo_scope");
  if (rule.score_scale !== undefined && fact.score_scale !== rule.score_scale) return result("unknown", "score_scale_missing_or_mismatch_no_conversion");
  let meetsScore: boolean;
  if (rule.score_kind === "numeric") {
    if (!number(fact.score) || fact.score < 0) return result("unknown", "invalid_numeric_score");
    const domain = rule.score_domain;
    if (domain && ((domain.minimum !== undefined && fact.score < domain.minimum) || (domain.maximum !== undefined && fact.score > domain.maximum) || (domain.step !== undefined && Math.abs((fact.score - (domain.minimum ?? 0)) / domain.step - Math.round((fact.score - (domain.minimum ?? 0)) / domain.step)) > 1e-9))) return result("unknown", "invalid_fact_domain");
    meetsScore = fact.score >= (rule.minimum_score as number);
  } else {
    if (!nonempty(fact.score) || !rule.ordered_values) return result("unknown", "ordered_score_mapping_unavailable");
    const actual = rule.ordered_values.indexOf(fact.score), minimum = rule.ordered_values.indexOf(rule.minimum_score as string);
    if (actual < 0 || minimum < 0) return result("unknown", "invalid_ordered_score_domain");
    meetsScore = actual >= minimum;
  }
  const date = rule.explicit_date_window;
  if (date && (fact.test_date < date.from || fact.test_date > date.to)) return result("fail", "outside_explicit_inclusive_date_window");
  return result(meetsScore ? "pass" : "fail", date ? "compared_score_and_explicit_date_window" : "compared_score_without_inferred_expiry");
}
export function compareEnglish(profile: AcademicProfile, requirement: EnglishRequirement): RequirementComparison {
  if (requirement.applicability !== "required") return { ...unavailable(requirement.applicability === "not_applicable" ? "not_applicable" : "english_requirement_unresolved", requirement.applicability), evidence_ids: clone(requirement.evidence_ids ?? []), source_reason: requirement.reason };
  const facts = array(profile.english);
  const traces = requirement.alternatives.map(rule => {
    const matching = facts.map((fact, index) => ({ fact, index })).filter((entry): entry is { fact: Record<string, unknown>; index: number } => record(entry.fact) && entry.fact.test_type === rule.test_type);
    if (!matching.length) return trace(rule, profile.english_inventory_complete === true ? "fail" : "unknown", profile.english_inventory_complete === true ? "absent_from_declared_complete_comparison_inventory" : "english_fact_missing_inventory_incomplete", null, { test_type: rule.test_type, minimum_score: rule.minimum_score });
    const records = matching.map(({ fact, index }) => englishRecord(fact, index, rule));
    const result = combineResults("or", records.map(item => item.result));
    return { ...trace(rule, result, "or_over_entered_records_of_same_test", null, { test_type: rule.test_type, minimum_score: rule.minimum_score }), records };
  });
  // Unknown test names are not silently treated as evidence of no accepted test.
  const unrecognized = facts.some(fact => !record(fact) || !tests.has(fact.test_type));
  const results = traces.map(item => item.result);
  if (unrecognized) results.push("unknown");
  return { applicability: "required", result: combineResults("or", results), reasons: [...new Set([...traces.filter(item => item.result === "unknown").flatMap(item => item.records ? item.records.filter(child => child.result === "unknown").map(child => child.reason) : [item.reason]), ...(unrecognized ? ["unrecognized_test_type"] : [])])], traces };
}

export function evaluateAcademicRequirements(profile: AcademicProfile | null | undefined, config: AcademicRuleSet): AcademicComparison {
  const validation = validateAcademicConfig(config);
  const input = record(profile) ? profile as AcademicProfile : {};
  const base = {
    label: "성적, 어학 요건 비교" as const, overall_eligibility: "not_evaluated" as const, selection_and_payment: "not_evaluated" as const,
    scope: "입력한 성적과 어학, 해당 공고의 지정 요건만 비교합니다. 전체 지원자격, 선발과 지급은 평가하지 않습니다.",
    education_path: nonempty(input.education_path) ? input.education_path : null, source_path: nonempty(input.source_path) ? input.source_path : null,
    rule_version: nonempty(config?.rule_version) ? config.rule_version : null, source: record(config?.source) ? clone(config.source) : null,
    uncompared_requirements: clone(config?.uncompared_requirements ?? []), semantic_limits: clone(config?.semantic_limits ?? []), validation_errors: validation,
  };
  if (validation.length) return { ...base, comparison_result: "unknown", grade: unavailable("invalid_rule_config"), english: unavailable("invalid_rule_config") };
  if (!educationPaths.has(input.education_path ?? "")) return { ...base, comparison_result: "unknown", grade: unavailable("education_path_missing_or_invalid"), english: unavailable("education_path_missing_or_invalid") };
  const pathKey = input.source_path ?? input.education_path;
  const path = pathKey ? config.paths[pathKey] : undefined;
  if (!path || path.education_path !== input.education_path) return { ...base, comparison_result: "unknown", grade: unavailable("source_path_missing_or_inconsistent"), english: unavailable("source_path_missing_or_inconsistent") };
  const grade = compareGrade(input, path.grade), english = compareEnglish(input, path.english);
  const applicableResults = [grade, english].filter(value => value.applicability !== "not_applicable").map(value => value.result);
  return { ...base, source_path: pathKey ?? null, uncompared_requirements: clone(path.uncompared_requirements), grade, english, comparison_result: combineResults("and", applicableResults) };
}
