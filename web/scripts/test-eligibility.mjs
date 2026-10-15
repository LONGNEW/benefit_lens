import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
const require = createRequire(import.meta.url);
const ts = require("typescript");
function moduleFrom(path, overrides = {}) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const code = ts.transpile(source, { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX });
  const exports = {};
  new Function("exports", "require", code)(exports, name => overrides[name] ?? require(name));
  return exports;
}
const eligibility = moduleFrom("../lib/eligibility.ts");
const { evaluateOpportunity, evaluateRule, separateConditions, requirementText } = eligibility;
const { fetchRegistry } = moduleFrom("../lib/registry.ts");
const { createEmptyProfile, loadSavedProfile, saveProfile, deleteSavedProfile, PROFILE_STORAGE_KEY } = moduleFrom("../lib/browser-profile.ts");
const now = new Date("2026-09-30T12:00:00+09:00");
const profile = { institution: "POSTECH", department: "cse", age: "22", gpa: "3.5", gpaScale: "4.3", gpaBasis: "previous_semester" };
const rule = { kind: "rule", id: "age", criterionCode: "age", field: "age", operator: "gte", value: 19, valueType: "integer", label: "19세 이상" };
const opportunity = { id: "a", name: "test", rules: [rule], matchMode: "structured", lifecycleStatus: "open", opensAt: "2026-09-01T00:00:00+09:00", closesAt: "2026-09-30T23:59:59+09:00", recordKind: "notice", publicationStatus: "reviewed", rightsStatus: "approved", productionAllowed: true };
let count = 0;
function test(name, run) { run(); count++; console.log(`PASS ${name}`); }
test("complete profile in confirmed window passes", () => assert.equal(evaluateOpportunity(profile, opportunity, now).status, "possible"));
test("unreviewed or missing rights are comparison candidates not confirmed applications", () => {
  for (const fields of [{publicationStatus: "needs_review"}, {rightsStatus: "review_required"}, {productionAllowed: false}, {rightsStatus: undefined}]) {
    const match = evaluateOpportunity(profile, {...opportunity, ...fields}, now);
    assert.equal(match.status, "candidate"); assert.equal(match.comparisonStatus, "possible"); assert.equal(match.eligibilityResult, "pass");
  }
});
test("empty profile is identified separately", () => assert.equal(evaluateOpportunity({}, opportunity, now).status, "profile"));
test("missing value is not zero", () => assert.equal(evaluateRule({age: " "}, rule), "unknown"));
test("negative or fractional age is invalid", () => { assert.equal(evaluateRule({age: "-1"}, rule), "unknown"); assert.equal(evaluateRule({age: "22.5"}, rule), "unknown"); });
test("period unknown is identified separately", () => assert.equal(evaluateOpportunity(profile, {...opportunity, opensAt: undefined, closesAt: undefined}, now).status, "period"));
test("expired at runtime overrides stale open", () => assert.equal(evaluateOpportunity(profile, {...opportunity, closesAt: "2026-09-29T23:59:59+09:00"}, now).status, "closed"));
test("future opens is not possible", () => assert.equal(evaluateOpportunity(profile, {...opportunity, opensAt: "2026-10-01T00:00:00+09:00", closesAt: "2026-10-30T23:59:59+09:00"}, now).status, "upcoming"));
test("inverted window is invalid", () => assert.equal(evaluateOpportunity(profile, {...opportunity, opensAt: "2026-09-29T00:00:00+09:00", closesAt: "2026-09-01T00:00:00+09:00"}, now).status, "closed"));
test("deadline inclusive", () => assert.equal(evaluateOpportunity(profile, opportunity, new Date(opportunity.closesAt)).status, "possible"));
test("policy audience can match without pretending recruitment is open", () => assert.equal(evaluateOpportunity(profile, {...opportunity, recordKind: "policy"}, now).status, "applicable"));
test("policy with missing audience input remains an informational policy", () => assert.equal(evaluateOpportunity({}, {...opportunity, recordKind: "policy"}, now).status, "policy"));
test("policy unresolved selection detail does not erase a known audience match", () => assert.equal(evaluateOpportunity(profile, {...opportunity, recordKind: "policy", unresolvedCriteria: ["선발 기준 확인"]}, now).status, "applicable"));
test("industry employee exclusion is evaluated as a common boolean condition", () => {
  const exclusion = {...rule, id: "industry", criterionCode: "industry_employee", field: "industryEmployee", operator: "eq", value: false, valueType: "boolean", label: "산업체 재직자 제외"};
  assert.equal(evaluateOpportunity({...profile, industryEmployee: "false"}, {...opportunity, recordKind: "policy", rules: [exclusion]}, now).status, "applicable");
  assert.equal(evaluateOpportunity({...profile, industryEmployee: "true"}, {...opportunity, recordKind: "policy", rules: [exclusion]}, now).status, "unmet");
});
test("policy explicit audience mismatch still fails", () => assert.equal(evaluateOpportunity({age: "15"}, {...opportunity, recordKind: "policy"}, now).status, "unmet"));
test("unresolved notice text is identified as source review", () => assert.equal(evaluateOpportunity(profile, {...opportunity, unresolvedCriteria: ["선발 기준 확인"]}, now).status, "review"));
test("explicit rolling with profile passes", () => assert.equal(evaluateOpportunity(profile, {...opportunity, lifecycleStatus: "rolling", opensAt: undefined, closesAt: undefined}, now).status, "possible"));
test("wrong department fails", () => assert.equal(evaluateOpportunity(profile, {...opportunity, rules: [{...rule, criterionCode: "department", field: "department", operator: "eq", value: "semi"}]}, now).status, "unmet"));
test("OR permits one known passing branch", () => assert.equal(evaluateOpportunity(profile, {...opportunity, criteria: {kind: "group", id: "or", operator: "or", children: [rule, {...rule, id: "dept", field: "department", operator: "eq", value: "semi"}]}}, now).status, "possible"));
test("AND blocks one failing branch", () => assert.equal(evaluateOpportunity(profile, {...opportunity, criteria: {kind: "group", id: "and", operator: "and", children: [rule, {...rule, id: "dept", field: "department", operator: "eq", value: "semi"}]}}, now).status, "unmet"));
test("GPA scale conversion without official mapping is unknown", () => assert.equal(evaluateRule({...profile, gpaScale: "4.5"}, {...rule, criterionCode: "gpa", field: "gpa", unit: "gpa_4.3", value: 3.0}), "unknown"));
test("unspecified GPA scale is unknown", () => assert.equal(evaluateRule(profile, {...rule, criterionCode: "gpa", field: "gpa", unit: "gpa_scale", value: 3.0}), "unknown"));
const gpaRule = {...rule, criterionCode: "gpa", field: "gpa", unit: "gpa_4.3", value: 3.5, valueType: "number", context: {basis: "previous_semester"}};
test("GPA on same scale and same basis compares", () => assert.equal(evaluateRule(profile, gpaRule), "pass"));
test("legacy GPA with no evaluation basis remains unknown", () => assert.equal(evaluateRule(profile, {...gpaRule, context: undefined}), "unknown"));
test("missing or different GPA basis never reuses another grade", () => {
  assert.equal(evaluateRule({...profile, gpaBasis: ""}, gpaRule), "unknown");
  assert.equal(evaluateRule({...profile, gpaBasis: "cumulative"}, gpaRule), "unknown");
});
test("GPA explicit evaluation semester must match", () => {
  const dated = {...gpaRule, context: {...gpaRule.context, evaluationPeriod: "2026-1"}};
  assert.equal(evaluateRule(profile, dated), "unknown");
  assert.equal(evaluateRule({...profile, gpaEvaluationPeriod: "2025-2"}, dated), "unknown");
  assert.equal(evaluateRule({...profile, gpaEvaluationPeriod: "2026-1"}, dated), "pass");
});
test("GPA equality boundary is inclusive without scale conversion", () => {
  assert.equal(evaluateRule({...profile, gpa: "3.5"}, gpaRule), "pass");
  assert.equal(evaluateRule({...profile, gpa: "3.49"}, gpaRule), "fail");
  assert.equal(evaluateRule({...profile, gpaScale: "4.5"}, gpaRule), "unknown");
});
test("GPA interval obeys scale and basis gates too", () => {
  const between = {...gpaRule, operator: "between", value: [3.0, 3.5]};
  assert.equal(evaluateRule(profile, between), "pass");
  assert.equal(evaluateRule({...profile, gpaScale: "4.5"}, between), "unknown");
  assert.equal(evaluateRule(profile, {...between, context: undefined}), "unknown");
});
test("invalid GPA threshold is not treated as an automatic pass", () => assert.equal(evaluateRule(profile, {...gpaRule, value: 5}), "unknown"));
test("specified multi-semester window is not replaced with generic cumulative GPA", () => {
  const window = {...gpaRule, context: {basis: "specified_window", windowText: "직전 2년간 4개 학기(5년제는 5개학기)", requiresReview: true}};
  assert.equal(evaluateRule({...profile, gpaBasis: "specified_window", gpa: "4.0"}, window), "unknown");
});
test("GPA source-specified scale alternatives select exactly one matching scale", () => {
  const scale43 = {...gpaRule, id: "gpa43", value: 3.3, context: {...gpaRule.context, scaleAlternative: true}};
  const scale45 = {...scale43, id: "gpa45", unit: "gpa_4.5", value: 3.5};
  const item = {...opportunity, rules: [scale43, scale45], criteria: {kind: "group", id: "grades", operator: "or", children: [scale43, scale45]}};
  assert.equal(evaluateOpportunity({...profile, gpa: "3.3"}, item, now).status, "possible");
  assert.equal(evaluateOpportunity({...profile, gpa: "3.29"}, item, now).status, "unmet");
  assert.equal(evaluateOpportunity({...profile, gpaScale: "4.5", gpa: "3.5"}, item, now).status, "possible");
  assert.equal(evaluateOpportunity({...profile, gpaScale: "4.5", gpa: "3.49"}, item, now).status, "unmet");
  assert.equal(evaluateOpportunity({...profile, gpaScale: ""}, item, now).status, "profile");
});
test("unmodeled alternative does not turn a known GPA failure into total rejection", () => {
  const item = {...opportunity, rules: [gpaRule], matchMode: "partial", unresolvedCriteria: ["백분위 또는 평점"], criteria: {kind: "group", id: "grades", operator: "or", children: [gpaRule, {kind: "group", id: "percentile", operator: "and", children: []}]}};
  const match = evaluateOpportunity({...profile, gpa: "1.0"}, item, now);
  assert.equal(match.eligibilityResult, "unknown");
  assert.equal(match.status, "review");
});
test("academic year range has inclusive lower and upper limits", () => {
  const year = {...rule, criterionCode: "academic_year", field: "year", operator: "between", value: [1, 2]};
  assert.equal(evaluateRule({year: "1"}, year), "pass");
  assert.equal(evaluateRule({year: "2"}, year), "pass");
  assert.equal(evaluateRule({year: "3"}, year), "fail");
  assert.equal(evaluateRule({year: ""}, year), "unknown");
});
test("TOPIK and residence duration retain equality boundaries", () => {
  const topik = {...rule, criterionCode: "topik_level", field: "topikLevel", value: 4};
  const months = {...rule, criterionCode: "residence_months", field: "residenceMonths", value: 12};
  assert.equal(evaluateRule({topikLevel: "4"}, topik), "pass");
  assert.equal(evaluateRule({topikLevel: "3"}, topik), "fail");
  assert.equal(evaluateRule({residenceMonths: "12"}, months), "pass");
  assert.equal(evaluateRule({residenceMonths: "11"}, months), "fail");
});
test("future enrollment commitment is explicit", () => { const commitment = {...rule, criterionCode: "enrollment_commitment", field: "enrollmentCommitment", operator: "eq", value: true}; assert.equal(evaluateRule({}, commitment), "unknown"); assert.equal(evaluateRule({enrollmentCommitment: "false"}, commitment), "fail"); assert.equal(evaluateRule({enrollmentCommitment: "true"}, commitment), "pass"); });
test("stale upcoming transitions when actual window opens", () => assert.equal(evaluateOpportunity(profile, {...opportunity, lifecycleStatus: "upcoming"}, now).status, "possible"));
const savedValues = new Map();
const storage = { getItem: key => savedValues.get(key) ?? null, setItem: (key, value) => savedValues.set(key, value), removeItem: key => savedValues.delete(key) };
test("public defaults contain no personal history or invented eligibility", () => { const empty = createEmptyProfile(); assert.equal(empty.highestDegreeInstitution, ""); assert.equal(empty.academicLevel, ""); assert.equal(empty.department, ""); assert.equal(empty.age, ""); assert.equal(empty.gpaScale, ""); assert.equal(empty.gpaBasis, ""); });
test("browser profile round trip preserves current and former schools separately", () => { const personal = {...createEmptyProfile(), institution: "POSTECH", department: "ax", academicLevel: "doctoral", highestDegreeLevel: "master", graduateSemester: "1", highestDegreeInstitution: "former university"}; saveProfile(storage, personal); assert.deepEqual(loadSavedProfile(storage), personal); assert.equal(loadSavedProfile(storage).nationality, ""); });
test("malformed browser storage falls back without inventing data", () => { storage.setItem(PROFILE_STORAGE_KEY, "not json"); assert.equal(loadSavedProfile(storage), null); storage.setItem(PROFILE_STORAGE_KEY, JSON.stringify({version: 2, profile: profile})); assert.equal(loadSavedProfile(storage), null); });
test("browser profile uses allowlisted string fields and ignores credentials", () => { storage.setItem(PROFILE_STORAGE_KEY, JSON.stringify({version: 1, profile: {age: 22, academicLevel: "invented", department: " ax ", accessToken: "ignored"}})); const restored = loadSavedProfile(storage); assert.equal(restored.age, ""); assert.equal(restored.academicLevel, ""); assert.equal(restored.department, "ax"); assert.equal("accessToken" in restored, false); });
test("clearing current inputs does not erase the saved default", () => { saveProfile(storage, {...createEmptyProfile(), academicLevel: "doctoral"}); const cleared = createEmptyProfile(); assert.equal(cleared.academicLevel, ""); assert.equal(loadSavedProfile(storage).academicLevel, "doctoral"); });
test("delete only removes the profile key", () => { storage.setItem("other", "keep"); deleteSavedProfile(storage); assert.equal(loadSavedProfile(storage), null); assert.equal(storage.getItem("other"), "keep"); });
test("storage failures are surfaced, never replaced with a server fallback", () => { assert.throws(() => saveProfile({setItem: () => { throw new Error("blocked"); }}, createEmptyProfile()), /blocked/); });
test("doctoral profile does not satisfy an undergraduate-only rule", () => assert.equal(evaluateRule({...createEmptyProfile(), academicLevel: "doctoral"}, {...rule, field: "academicLevel", operator: "eq", value: "undergraduate"}), "fail"));
test("birth year passes only when both possible ages satisfy the rule", () => {
  const age = {...rule, operator: "between", value: [19, 34]};
  assert.equal(evaluateRule({birthYear: "1999"}, age, now), "pass");
  assert.equal(evaluateRule({birthYear: "1999"}, {...age, operator: "lte", value: 26}, now), "unknown");
  assert.equal(evaluateRule({birthYear: "2015"}, age, now), "fail");
});
test("military age exception requires service duration above normal upper bound", () => {
  const age = {...rule, operator: "between", value: [15, 34], unit: "year_military_extension_39"};
  assert.equal(evaluateRule({age: "36"}, age, now), "unknown");
  assert.equal(evaluateRule({age: "36", militaryServiceMonths: "24"}, age, now), "pass");
  assert.equal(evaluateRule({age: "37", militaryServiceMonths: "24"}, age, now), "fail");
  assert.equal(evaluateRule({age: "40", militaryServiceMonths: "60"}, age, now), "fail");
});
test("obsolete card answers cannot create a hold on another notice", () => {
  const card = {...rule, id: "card", criterionCode: "training_card_eligible", field: "trainingCardEligible", operator: "eq", value: true};
  const input = {...profile, trainingCardEligible: "true", conditionAnswers: {"a:card": "true"}};
  assert.equal(evaluateOpportunity(input, {...opportunity, rules: [card]}, now).status, "possible");
  assert.equal(evaluateOpportunity(input, {...opportunity, id: "b", rules: [card]}, now).status, "possible");
});
const participation = {...rule, id: "attend", criterionCode: "hackathon_available", field: "hackathonAvailable", operator: "eq", value: true, label: "11월 28~29일 해커톤 참여 가능", sourceText: "11월 28~29일 해커톤에 참여 가능한 분", sourceUrl: "https://official.test/hackathon"};
test("unanswered participation is an informational requirement, not a hold", () => {
  const match = evaluateOpportunity(profile, {...opportunity, rules: [rule, participation]}, now);
  assert.equal(match.status, "possible");
  assert.equal(match.ruleResults.length, 1);
  assert.deepEqual(match.requirements, [participation]);
  assert.equal(requirementText(participation), "11월 28~29일 해커톤 참여");
});
test("saved unavailable participation never fails applicant eligibility", () => {
  const input = {...profile, hackathonAvailable: "false", conditionAnswers: {"a:attend": "false"}};
  assert.equal(evaluateOpportunity(input, {...opportunity, rules: [rule, participation]}, now).status, "possible");
});
test("each supported activity is a requirement", () => {
  for (const field of ["onlineLearningAvailable", "hackathonAvailable", "activityAttendance", "fullTimeTrainingAvailable", "codingStudyInterest", "employmentPreparation", "trainingCardEligible", "enrollmentCommitment"]) {
    const match = evaluateOpportunity(profile, {...opportunity, rules: [rule, {...participation, field}]}, now);
    assert.equal(match.status, "possible");
    assert.equal(match.requirements.length, 1);
  }
});
test("missing identity and failing identity still block with requirements", () => {
  const item = {...opportunity, rules: [rule, participation]};
  assert.equal(evaluateOpportunity({}, item, now).status, "profile");
  assert.equal(evaluateOpportunity({age: "15"}, item, now).status, "unmet");
});
test("requirement-only structured notice has no applicant restriction", () => {
  const match = evaluateOpportunity({}, {...opportunity, rules: [participation]}, now);
  assert.equal(match.status, "possible");
  assert.equal(match.eligibilityCriteria, null);
});
test("empty, partial and unresolved notices stay conservative", () => {
  assert.equal(evaluateOpportunity({}, {...opportunity, rules: []}, now).status, "review");
  assert.equal(evaluateOpportunity({}, {...opportunity, rules: [participation], matchMode: "review_required"}, now).status, "review");
  assert.equal(evaluateOpportunity(profile, {...opportunity, rules: [rule, participation], unresolvedCriteria: ["기관의 추가 신청 제한"]}, now).status, "review");
});
test("full-time student status is eligibility, not training attendance", () => {
  const student = {...rule, id: "full-time", field: "fullTimeStudent", operator: "eq", value: true};
  assert.equal(evaluateOpportunity(profile, {...opportunity, rules: [student, participation]}, now).status, "profile");
  assert.equal(evaluateOpportunity({...profile, fullTimeStudent: "false"}, {...opportunity, rules: [student, participation]}, now).status, "unmet");
});
test("nested degree OR survives activity removal", () => {
  const dept = {...rule, id: "dept", field: "department", operator: "eq", value: "semi"};
  const criteria = {kind: "group", id: "root", operator: "and", children: [
    {kind: "group", id: "paths", operator: "or", children: [rule, dept]}, participation,
  ]};
  const match = evaluateOpportunity(profile, {...opportunity, criteria}, now);
  assert.equal(match.status, "possible");
  assert.equal(match.eligibilityCriteria.children[0].operator, "or");
  assert.equal(match.ruleResults.length, 2);
});
test("unrestricted alternative does not turn into an eligibility restriction", () => {
  const criteria = {kind: "group", id: "paths", operator: "or", children: [participation, rule]};
  assert.equal(evaluateOpportunity({age: "15"}, {...opportunity, criteria}, now).status, "possible");
});
test("explicit rule classification and sources are retained", () => {
  const obligation = {...rule, conditionKind: "requirement"};
  const match = evaluateOpportunity(profile, {...opportunity, rules: [obligation]}, now);
  assert.equal(match.ruleResults.length, 0);
  assert.equal(match.requirements.length, 1);
  const item = {...opportunity, rules: [rule, participation]};
  const split = separateConditions(item);
  assert.deepEqual(separateConditions({...item, ...split}), split);
  assert.equal(split.requirements[0].sourceUrl, participation.sourceUrl);
});
test("empty OR is not upgraded to a passing path by unrelated requirements", () => {
  const criteria = {kind: "group", id: "root", operator: "and", children: [{kind: "group", id: "empty", operator: "or", children: []}, participation]};
  assert.equal(evaluateOpportunity(profile, {...opportunity, criteria}, now).status, "review");
});
const { ResultCard, OpportunityPreview } = moduleFrom("../app/benefitlens/benefitlens-dashboard.tsx", {
  "@/lib/application-package": moduleFrom("../lib/application-package.ts", {"./writing-resources.json": {default: JSON.parse(readFileSync(new URL('../lib/writing-resources.json', import.meta.url), 'utf8'))}}),
  "@/lib/local-demo-profile": moduleFrom("../lib/local-demo-profile.ts", {"./browser-profile": moduleFrom("../lib/browser-profile.ts")}),
  "@/lib/eligibility": eligibility,
  "@/lib/browser-profile": moduleFrom("../lib/browser-profile.ts"),
  "@/lib/registry": moduleFrom("../lib/registry.ts"),
  "@/lib/funding": moduleFrom("../lib/funding.ts"),
  "@/lib/profile-assistance": moduleFrom("../lib/profile-assistance.ts"),
  "./assisted-profile": {AssistedProfile: () => null},
  "./profile-import": {default: () => null},
  "./draft-workbench": {default: () => null},
});
const {createElement} = require("react");
const {renderToStaticMarkup} = require("react-dom/server");
test("canonical empty requirements do not restore legacy mixed prose", () => {
  const match = evaluateOpportunity(profile, {...opportunity, conditionSections: {requirements: [], preferences: ["우대 원문"], benefits: ["공통 계약 혜택"]}, presentation: {conditions: ["과거 혼합 조건"]}}, now);
  const html = renderToStaticMarkup(createElement(ResultCard, {match, selected: false, onSelect: () => {}}));
  assert.doesNotMatch(html, /과거 혼합 조건/); assert.match(html, /우대 원문/); assert.match(html, /공통 계약 혜택/);
});
test("financial card exposes multiple classifications and exact benefit evidence", () => {
  const match = evaluateOpportunity(profile, {...opportunity, sourceGroup: "school", operatorGroup: "foundation", fundingTags: ["scholarship", "living_expenses"], fundingEvidence: [{tag: "living_expenses", text: "생활비 연 300만원 지급", field: "benefit"}]}, now);
  const html = renderToStaticMarkup(createElement(ResultCard, {match, selected: false, onSelect: () => {}}));
  assert.match(html, /<span class="category-tag">재단<\/span><span class="category-tag">장학금<\/span><span class="category-tag">생활비<\/span>/);
  assert.match(html, /금전 지원 분류 근거/);
  assert.match(html, /생활비 연 300만원 지급/);
});
test("browse preview separates amount, qualification and detail navigation", () => {
  const match = evaluateOpportunity(profile, {...opportunity, organization: "POSTECH", fundingTags: ["scholarship", "living_expenses"], presentation: {benefits: ["장학금 연 300만원 / 생활비 월 20만원"]}}, now);
  const html = renderToStaticMarkup(createElement(OpportunityPreview, {match}));
  assert.match(html, /<strong>어떤 지원인가요\?<\/strong><ul><li>장학금 연 300만원<\/li><li>생활비 월 20만원<\/li><\/ul>/);
  assert.match(html, /<strong>누가 대상인가요\?<\/strong><ul><li>19세 이상<\/li><\/ul>/);
  assert.match(html, /href="\/benefitlens\/opportunity\?id=a"/);
  assert.doesNotMatch(html, /전체 조건과 판정 근거|장학금 연 300만원 \/ 생활비/);
});
test("rendered policy card exposes audience applicability and selection caveat", () => {
  const match = evaluateOpportunity(profile, {...opportunity, recordKind: "policy", unresolvedCriteria: ["세부 선발 기준"]}, now);
  const html = renderToStaticMarkup(createElement(ResultCard, {match, selected: false, onSelect: () => {}}));
  assert.match(html, /제도 대상 가능/);
  assert.match(html, /추가 선발 기준 1개/);
  assert.match(html, /제도 활용 준비하기/);
  assert.doesNotMatch(html, /판정 보류|접수 가능/);
  const eligibilitySummary = html.match(/<dt>지원 자격<\/dt><dd>(.*?)<\/dd>/)?.[1];
  assert.match(eligibilitySummary, /<ul class="condition-list"><li>19세 이상<\/li><\/ul>/);
  assert.doesNotMatch(eligibilitySummary, /세부 선발 기준/);
});
test("unstructured source sentences stay out of the eligibility summary", () => {
  const match = evaluateOpportunity({}, {...opportunity, rules: [], unresolvedCriteria: ["기관의 원문 자격 문장"], presentation: {eligibility: ["기관의 원문 자격 문장"]}}, now);
  const html = renderToStaticMarkup(createElement(ResultCard, {match, selected: false, onSelect: () => {}}));
  assert.match(html, /<dt>지원 자격<\/dt><dd>표준 자격 기준 미추출, 전체 조건에서 원문 확인<\/dd>/);
  assert.doesNotMatch(html, /<dt>지원 자격<\/dt><dd>[^<]*기관의 원문 자격 문장/);
});
test("eligibility bullets preserve nested AND, OR and exclusion groups", () => {
  const dept = {...rule, id: "dept", field: "department", operator: "eq", value: "cse", label: "컴퓨터공학과 소속"};
  const industry = {...rule, id: "industry", field: "industryEmployee", operator: "eq", value: true, label: "산업체 재직자"};
  const criteria = {kind: "group", id: "root", operator: "and", children: [rule,
    {kind: "group", id: "alternatives", operator: "or", children: [dept, {...dept, id: "other", value: "semi", label: "반도체공학과 소속"}]},
    {kind: "group", id: "excluded", operator: "not", children: [industry]},
  ]};
  const match = evaluateOpportunity({...profile, industryEmployee: "false"}, {...opportunity, criteria}, now);
  assert.equal(match.status, "possible");
  const html = renderToStaticMarkup(createElement(ResultCard, {match, selected: false, onSelect: () => {}}));
  const summary = html.match(/<dt>지원 자격<\/dt><dd>(.*?)<\/dd>/)?.[1];
  assert.match(summary, /eligibility-summary-group and/);
  assert.match(summary, /다음 조건을 모두 충족/);
  assert.match(summary, /<li><div class="eligibility-summary-group or">/);
  assert.match(summary, /다음 중 하나 이상 충족/);
  assert.match(summary, /<li>컴퓨터공학과 소속<\/li><li>반도체공학과 소속<\/li>/);
  assert.match(summary, /eligibility-summary-group not/);
  assert.match(summary, /다음에 해당하면 제외/);
  assert.equal((summary.match(/<ul /g) ?? []).length, 3);
  assert.doesNotMatch(summary, / \+ | \/ /);
});
test("fact bullets split explicit separators and preserve dates, GPA, URLs and R/A", () => {
  const conditions = ["성적 3.0/4.3 이상 / 접수 10/12", "R/A 활동\nhttps://official.test/notice/12", "접수 10/12"];
  const match = evaluateOpportunity(profile, {...opportunity, presentation: {conditions}}, now);
  const html = renderToStaticMarkup(createElement(ResultCard, {match, selected: false, onSelect: () => {}}));
  const facts = html.match(/<dt>요구사항<\/dt><dd>(.*?)<\/dd>/)?.[1];
  assert.match(facts, /<li>성적 3.0\/4.3 이상<\/li><li>접수 10\/12<\/li><li>R\/A 활동<\/li>/);
  assert.match(facts, /나머지 1개 보기/);
  assert.match(facts, /<li>https:\/\/official.test\/notice\/12<\/li>/);
  assert.equal((facts.match(/<li>/g) ?? []).length, 4);
});
test("long conditions remain available in an expandable bullet", () => {
  const longText = "장학금 지급 조건을 공식 안내에서 확인합니다. ".repeat(16) + "마지막 원문 조건";
  const match = evaluateOpportunity(profile, {...opportunity, presentation: {specialTerms: [longText]}}, now);
  const html = renderToStaticMarkup(createElement(ResultCard, {match, selected: false, onSelect: () => {}}));
  const facts = html.match(/<dt>특약<\/dt><dd>(.*?)<\/dd>/)?.[1];
  assert.match(facts, /<li><details class="condition-full-text">/);
  assert.match(facts, /전체 보기/);
  assert.ok(facts.includes(`<p>${longText}</p>`));
});
test("missing and failed profile conditions are rendered as bullet lists", () => {
  const dept = {...rule, id: "dept", field: "department", operator: "eq", value: "cse", label: "컴퓨터공학과 소속"};
  const item = {...opportunity, rules: [rule, dept]};
  const missing = evaluateOpportunity({}, item, now);
  const missingHtml = renderToStaticMarkup(createElement(ResultCard, {match: missing, selected: false, onSelect: () => {}}));
  assert.match(missingHtml, /내 정보 추가 입력<\/strong><ul class="condition-list"><li>19세 이상<\/li><li>컴퓨터공학과 소속<\/li>/);
  const failed = evaluateOpportunity({age: "15", department: "semi"}, item, now);
  const failedHtml = renderToStaticMarkup(createElement(ResultCard, {match: failed, selected: false, onSelect: () => {}}));
  assert.match(failedHtml, /맞지 않는 기준<\/strong><ul class="condition-list"><li>19세 이상<\/li><li>컴퓨터공학과 소속<\/li>/);
});
test("rendered card explains activities without participation selectors", () => {
  const input = {...createEmptyProfile(), ...profile, conditionAnswers: {"a:attend": "false"}};
  const match = evaluateOpportunity(input, {...opportunity, rules: [rule, participation]}, now);
  const html = renderToStaticMarkup(createElement(ResultCard, {match, selected: false, onSelect: () => {}, profile: input, onAnswer: () => {}}));
  assert.match(html, /신청하면 해야 할 일/);
  assert.match(html, /11월 28~29일 해커톤 참여/);
  assert.match(html, /href="https:\/\/official.test\/hackathon"/);
  assert.doesNotMatch(html, /<select|이 공고의 참여 가능 여부|판정 보류/);
  assert.equal((html.match(/class="rule-row /g) ?? []).length, 1);
});
test("rendered card displays card issuance as a procedure without a selector", () => {
  const card = {...rule, id: "card", field: "trainingCardEligible", operator: "eq", value: true, label: "국민내일배움카드 발급 가능"};
  const match = evaluateOpportunity(profile, {...opportunity, rules: [card, participation]}, now);
  const html = renderToStaticMarkup(createElement(ResultCard, {match, selected: false, onSelect: () => {}, profile, onAnswer: () => {}}));
  assert.doesNotMatch(html, /<select|지원 자격 추가 정보|판정 보류/);
  assert.match(html, /국민내일배움카드 발급 신청과 고용센터 심사 완료/);
});
const remainingStudy = {...rule, id: "study", criterionCode: "remaining_study_months", field: "remainingStudyMonths", operator: "lte", value: 24,
  derivation: {institution: "POSTECH", semestersByLevel: {master: 4, doctoral: 4}, sourceUrl: "https://official.test/regulation.pdf", sourceText: "석사과정 및 박사과정의 수업 연한은 각각 2년"}};
test("POSTECH first-semester doctoral rule is derived from collected formal duration", () => {
  const input = {...createEmptyProfile(), institution: "POSTECH", academicLevel: "doctoral", enrollmentStatus: "enrolled", graduateSemester: "1"};
  assert.equal(evaluateRule(input, remainingStudy), "pass");
  const match = evaluateOpportunity(input, {...opportunity, rules: [remainingStudy]}, now);
  const html = renderToStaticMarkup(createElement(ResultCard, {match, selected: false, onSelect: () => {}}));
  assert.match(html, /남은 수업연한 24개월/);
  assert.match(html, /href="https:\/\/official.test\/regulation.pdf"/);
});
test("duration is never guessed for another institution, missing semester or integrated degree", () => {
  const input = {institution: "POSTECH", academicLevel: "doctoral", enrollmentStatus: "enrolled", graduateSemester: "1"};
  assert.equal(evaluateRule({...input, institution: "OTHER"}, remainingStudy), "unknown");
  assert.equal(evaluateRule({...input, graduateSemester: ""}, remainingStudy), "unknown");
  assert.equal(evaluateRule({...input, academicLevel: "integrated"}, remainingStudy), "unknown");
  assert.equal(evaluateRule(input, {...remainingStudy, derivation: undefined}), "unknown");
  assert.equal(evaluateRule(input, {...remainingStudy, derivation: {...remainingStudy.derivation, semestersByLevel: {doctoral: 6}}}), "fail");
});
test("stale eligibility metadata cannot restore intent questions", () => {
  const intent = {...rule, field: "employmentPreparation", conditionKind: "eligibility", operator: "eq", value: true, label: "취업 준비 목적"};
  const match = evaluateOpportunity({}, {...opportunity, rules: [intent]}, now);
  assert.equal(match.status, "possible");
  assert.equal(match.ruleResults.length, 0);
  assert.equal(match.requirements.length, 1);
});
test("previous purchase restriction preserves current subscription exception", () => {
  const paid = {...rule, id: "paid", field: "codetreePaid", operator: "eq", value: false};
  const subscribed = {...rule, id: "subscribed", field: "codetreeSubscribed", operator: "eq", value: true};
  const criteria = {kind: "group", id: "payment-path", operator: "or", children: [paid, subscribed]};
  const item = {...opportunity, criteria};
  assert.equal(evaluateOpportunity({codetreePaid: "false"}, item, now).status, "possible");
  assert.equal(evaluateOpportunity({codetreePaid: "true", codetreeSubscribed: "true"}, item, now).status, "possible");
  assert.equal(evaluateOpportunity({codetreePaid: "true", codetreeSubscribed: "false"}, item, now).status, "unmet");
  assert.equal(evaluateOpportunity({}, item, now).status, "profile");
});
test("unemployment does not imply full-time student or attendance", () => {
  const input = {...createEmptyProfile(), employmentStatus: "unemployed"};
  assert.equal(evaluateRule(input, {...rule, field: "fullTimeStudent", operator: "eq", value: true}), "unknown");
  assert.equal(evaluateRule(input, {...rule, field: "fullTimeTrainingAvailable", operator: "eq", value: true}), "unknown");
});
test("saved participation answers preserve only exact string booleans", () => {
  saveProfile(storage, {...createEmptyProfile(), conditionAnswers: {"a:attend": "true", "b:attend": "false", "bad": true}});
  assert.deepEqual(loadSavedProfile(storage).conditionAnswers, {"a:attend": "true", "b:attend": "false"});
});
const originalFetch = globalThis.fetch;
try {
  let calls = 0;
  globalThis.fetch = async () => ({ok: true, json: async () => { calls++; return { data: [{...opportunity, id: String(calls)}], meta: {total: 2, page: calls, pageSize: 1, pageCount: 2, datasetVersion: "v1"} }; }});
  const full = await fetchRegistry(new URLSearchParams(), new AbortController().signal);
  assert.equal(full.data.length, 2); assert.equal(calls, 2); count++; console.log("PASS matching input includes every page");
  calls = 0;
  globalThis.fetch = async () => ({ok: true, json: async () => { calls++; return { data: [{id: String(calls)}], meta: {total: 2, page: calls, pageCount: 2, datasetVersion: String(calls)} }; }});
  await assert.rejects(fetchRegistry(new URLSearchParams(), new AbortController().signal), /changed/); count++; console.log("PASS changed snapshot rejected");
} finally { globalThis.fetch = originalFetch; }
console.log(`${count} checks passed`);
