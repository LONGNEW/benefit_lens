import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
const require = createRequire(import.meta.url);
const ts = require("typescript");
function moduleFrom(path, overrides = {}) {
  const code = ts.transpile(readFileSync(new URL(path, import.meta.url), "utf8"), {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX});
  const exports = {};
  new Function("exports", "require", code)(exports, name => overrides[name] ?? require(name));
  return exports;
}
const {createEmptyProfile, saveWorkingProfile, loadWorkingProfile} = moduleFrom("../lib/browser-profile.ts");
const {evaluateOpportunity} = moduleFrom("../lib/eligibility.ts");
const assistance = moduleFrom("../lib/profile-assistance.ts");
const {withAffiliation, questionsForMatch, prioritizeMatches} = assistance;
const {parseCsv, extractProfileCandidates, extractProfileTextCandidates} = moduleFrom("../lib/profile-import.ts");
const {AssistedProfile} = moduleFrom("../app/benefitlens/assisted-profile.tsx", {"@/lib/profile-assistance": assistance});
const {createElement} = require("react");
const {renderToStaticMarkup} = require("react-dom/server");
const now = new Date("2026-10-01T12:00:00+09:00");
const departments = [{code: "cse", department: "컴퓨터공학과"}, {code: "gift", department: "친환경소재대학원 배터리공학과"}];
function rule(field, value, extra = {}) {return {kind: "rule", id: field, criterionCode: field, field, operator: "eq", value, valueType: typeof value === "boolean" ? "boolean" : typeof value === "number" ? "integer" : "string", label: `조건-${field}`, ...extra};}
const school = rule("institution", "POSTECH");
const department = rule("department", "cse");
const degree = rule("academicLevel", "doctoral");
const industry = rule("industryEmployee", false);
function opportunity(rules, criteria = null) {return {id: "test", name: "테스트 제도", organization: "공식 기관", rules, criteria, recordKind: "policy", matchMode: "structured"};}
function plan(profile, item) {return questionsForMatch(profile, evaluateOpportunity(profile, item, now));}
let count = 0;
function test(name, run) {run(); count++; console.log(`PASS ${name}`);}
test("explicit department selection links public affiliation without inventing personal facts", () => {
  const next = withAffiliation(createEmptyProfile(), "department:gift", departments);
  assert.equal(next.institution, "POSTECH"); assert.equal(next.department, "gift"); assert.equal(next.graduateSchool, "친환경소재대학원");
  for (const field of ["academicLevel", "enrollmentStatus", "nationality", "industryEmployee", "incomeBand", "gpa"]) assert.equal(next[field], "");
});
test("invalid department is ignored and changing school clears obsolete affiliation only", () => {
  const original = {...createEmptyProfile(), institution: "POSTECH", department: "cse", nationality: "KR", gpa: "3.5"};
  assert.equal(withAffiliation(original, "department:unknown", departments), original);
  const next = withAffiliation(original, "OTHER", departments);
  assert.equal(next.department, ""); assert.equal(next.nationality, "KR"); assert.equal(next.gpa, "3.5");
});
test("initial page has only three optional selectors before advanced details", () => {
  const html = renderToStaticMarkup(createElement(AssistedProfile, {profile: createEmptyProfile(), departments, match: null, onUpdate() {}, onAffiliation() {}}));
  const quick = html.split('<section class="selected-profile-questions"')[0];
  assert.equal((quick.match(/<select/g) ?? []).length, 3);
  assert.equal((html.match(/<select/g) ?? []).length, 3);
  assert.doesNotMatch(html, /<input|학자금지원구간|평점|만 나이|산업체 재직/);
  assert.doesNotMatch(quick, /required=|학자금지원구간|평점|만 나이|산업체 재직/);
  assert.match(html, /입력하지 않아도 모든 지원을 볼 수 있습니다/);
});
test("school and department unknowns collapse to one affiliation question", () => {
  const questions = plan(createEmptyProfile(), opportunity([school, department])).questions;
  assert.equal(questions.length, 1); assert.equal(questions[0].field, "affiliation"); assert.equal(questions[0].reasons.length, 2);
});
test("known school and degree are reused and only missing industry status is asked", () => {
  const profile = {...createEmptyProfile(), institution: "POSTECH", academicLevel: "doctoral"};
  assert.deepEqual(plan(profile, opportunity([school, degree, industry])).questions.map(q => q.field), ["industryEmployee"]);
});
test("a passing OR branch does not ask for another branch", () => {
  const profile = {...createEmptyProfile(), institution: "POSTECH"};
  const item = opportunity([school, degree], {kind: "group", id: "root", operator: "or", children: [school, degree]});
  assert.deepEqual(plan(profile, item).questions, []);
});
test("unknown OR chooses fewest distinct unanswered questions", () => {
  const item = opportunity([school, degree, industry], {kind: "group", id: "root", operator: "or", children: [
    {kind: "group", id: "large", operator: "and", children: [degree, industry]}, school,
  ]});
  const result = plan(createEmptyProfile(), item);
  assert.equal(result.alternative, true); assert.deepEqual(result.questions.map(q => q.field), ["affiliation"]);
});
test("an explicitly failed OR branch is not chosen to reduce question count", () => {
  const profile = {...createEmptyProfile(), institution: "OTHER"};
  const item = opportunity([school, degree, industry], {kind: "group", id: "root", operator: "or", children: [school, {kind: "group", id: "other", operator: "and", children: [degree, industry]}]});
  assert.deepEqual(plan(profile, item).questions.map(q => q.field), ["academicLevel", "industryEmployee"]);
});
test("failed mandatory conditions do not trigger unrelated personal questions", () => {
  const profile = {...createEmptyProfile(), institution: "OTHER"};
  assert.deepEqual(plan(profile, opportunity([school, degree, industry])).questions, []);
});
test("exclusion conditions preserve unknown and then fail or pass correctly", () => {
  const employed = rule("industryEmployee", true);
  const item = opportunity([employed], {kind: "group", id: "exclude", operator: "not", children: [employed]});
  assert.deepEqual(plan(createEmptyProfile(), item).questions.map(q => q.field), ["industryEmployee"]);
  assert.equal(evaluateOpportunity({...createEmptyProfile(), industryEmployee: "true"}, item, now).eligibilityResult, "fail");
  assert.equal(evaluateOpportunity({...createEmptyProfile(), industryEmployee: "false"}, item, now).eligibilityResult, "pass");
});
test("activities and unparsed text never become invented personal questions", () => {
  const activity = rule("fullTimeTrainingAvailable", true);
  const item = {...opportunity([school, activity]), unresolvedCriteria: ["소득과 성적은 원문 확인 필요"]};
  assert.deepEqual(plan(createEmptyProfile(), item).questions.map(q => q.field), ["affiliation"]);
});
test("GPA questions request only missing comparison-ready qualifiers", () => {
  const grade = rule("gpa", 3.5, {criterionCode: "gpa", operator: "gte", valueType: "number", unit: "gpa_4.3", context: {basis: "previous_semester", evaluationPeriod: "2026-1"}});
  assert.deepEqual(plan(createEmptyProfile(), opportunity([grade])).questions.map(q => q.field), ["gpa", "gpaBasis", "gpaEvaluationPeriod"]);
  const known = {...createEmptyProfile(), gpa: "3.8", gpaBasis: "previous_semester", gpaEvaluationPeriod: "2026-1"};
  assert.deepEqual(plan(known, opportunity([grade])).questions.map(q => q.field), ["gpaScale"]);
  assert.deepEqual(plan({...known, gpaScale: "4.3"}, opportunity([grade])).questions, []);
});
test("missing GPA source qualifiers do not trigger requests for personal data", () => {
  const grade = rule("gpa", 3.5, {criterionCode: "gpa", operator: "gte", valueType: "number", unit: "gpa_scale"});
  assert.deepEqual(plan(createEmptyProfile(), opportunity([grade])).questions, []);
  const window = {...grade, unit: "gpa_4.3", context: {basis: "specified_window", requiresReview: true}};
  assert.deepEqual(plan(createEmptyProfile(), opportunity([window])).questions, []);
});
test("selected GPA qualifiers have working UI editors and no default scale", () => {
  const grade = rule("gpa", 3.5, {criterionCode: "gpa", operator: "gte", valueType: "number", unit: "gpa_4.3", context: {basis: "previous_semester", evaluationPeriod: "2026-1"}});
  const profile = createEmptyProfile();
  const html = renderToStaticMarkup(createElement(AssistedProfile, {profile, departments, match: evaluateOpportunity(profile, opportunity([grade]), now), onUpdate() {}, onAffiliation() {}}));
  assert.match(html, /성적 평가 기준|성적 평가 학기/);
  assert.match(html, /<option value="" selected="">만점 미입력<\/option>/);
});
test("official study duration requires semester only and remains rule-derived", () => {
  const duration = rule("remainingStudyMonths", 24, {operator: "lte", derivation: {institution: "POSTECH", semestersByLevel: {master: 4, doctoral: 4}, sourceUrl: "https://official.test/rules", sourceText: "수업 연한 2년"}});
  const profile = {...createEmptyProfile(), institution: "POSTECH", academicLevel: "doctoral", enrollmentStatus: "enrolled"};
  assert.deepEqual(plan(profile, opportunity([duration])).questions.map(q => q.field), ["graduateSemester"]);
  const result = evaluateOpportunity({...profile, graduateSemester: "1"}, opportunity([duration]), now);
  assert.equal(result.eligibilityResult, "pass"); assert.match(result.derivedFacts[0].text, /남은 수업연한 24개월/);
});
test("military exception asks service duration instead of repeating known age", () => {
  const age = rule("age", [15, 34], {operator: "between", unit: "year_military_extension_39"});
  assert.deepEqual(plan({...createEmptyProfile(), age: "36"}, opportunity([age])).questions.map(q => q.field), ["militaryServiceMonths"]);
});
test("selected notice shows its missing field, no unrelated income or grade requests", () => {
  const profile = {...createEmptyProfile(), institution: "POSTECH", academicLevel: "doctoral"};
  const match = evaluateOpportunity(profile, opportunity([school, degree, industry]), now);
  const html = renderToStaticMarkup(createElement(AssistedProfile, {profile, departments, match, onUpdate() {}, onAffiliation() {}}));
  const selected = html.split('<section class="selected-profile-questions"')[1].split('</section>')[0];
  assert.match(selected, /산업체 재직|필요한 이유/); assert.equal((selected.match(/<select/g) ?? []).length, 1);
  assert.doesNotMatch(selected, /학자금지원구간|평점/);
});
test("no input preserves discovery order and incomplete profiles are never promoted", () => {
  const incomplete = evaluateOpportunity(createEmptyProfile(), opportunity([school]), now);
  const passed = evaluateOpportunity({...createEmptyProfile(), institution: "POSTECH"}, {...opportunity([school]), id: "passed"}, now);
  const list = [incomplete, passed];
  assert.equal(prioritizeMatches(list, createEmptyProfile()), list);
  const sorted = prioritizeMatches(list, {...createEmptyProfile(), institution: "POSTECH"});
  assert.equal(sorted[0].id, "passed"); assert.equal(sorted[1].status, "policy"); assert.equal(list[0], incomplete);
});
test("LinkedIn export CSV handles quoted commas and line breaks", () => {
  const rows = parseCsv('\uFEFFSchool Name,Degree Name,Notes\r\n"POSTECH, Pohang","Doctor of Philosophy","Research, engineering\nLaboratory"\r\n');
  assert.deepEqual(rows[1], ["POSTECH, Pohang", "Doctor of Philosophy", "Research, engineering\nLaboratory"]);
});
test("owner export proposes current education, completed degree and work for review", () => {
  const data = [
    {name: "Education.csv", content: 'School Name,Degree Name,Field Of Study,Start Date,End Date\nFormer University,Master of Science,Physics,2019,2021\nPOSTECH,Doctor of Philosophy,컴퓨터공학과,2024,\n'},
    {name: "Positions.csv", content: 'Company Name,Title,Started On,Finished On\nCompany A,Researcher,2024,\n'},
  ];
  const candidates = extractProfileCandidates(data, departments);
  assert.deepEqual(Object.fromEntries(candidates.map(item => [item.field, item.value])), {highestDegreeLevel: "master", highestDegreeInstitution: "Former University", institution: "POSTECH", academicLevel: "doctoral", department: "cse", employmentStatus: "employed"});
  assert.ok(candidates.find(item => item.field === "employmentStatus")?.evidence.includes("현재 상태 확인 필요"));
  assert.equal(candidates.some(item => ["incomeBand", "gpa", "industryEmployee", "enrollmentStatus"].includes(item.field)), false);
});
test("ambiguous copied degree history does not become current academic level", () => {
  const candidates = extractProfileTextCandidates("POSTECH 컴퓨터공학과\nMaster of Science\nPhD candidate", departments);
  assert.deepEqual(candidates.map(item => item.field), ["institution", "department"]);
});
test("working profile survives a page change in tab storage", () => {
  const data = new Map();
  const storage = {getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key)};
  saveWorkingProfile(storage, {...createEmptyProfile(), institution: "POSTECH", academicLevel: "doctoral"});
  assert.equal(loadWorkingProfile(storage).academicLevel, "doctoral");
  assert.equal(loadWorkingProfile({getItem: () => "not-json"}), null);
});
console.log(`${count} checks passed`);
