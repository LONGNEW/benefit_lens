import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';

const require = createRequire(import.meta.url);
const ts = require('typescript');
function moduleFrom(path, overrides = {}) {
  const code = ts.transpile(readFileSync(new URL(path, import.meta.url), 'utf8'), {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022});
  const exports = {};
  new Function('exports', 'require', code)(exports, name => overrides[name] ?? require(name));
  return exports;
}
const browser = moduleFrom('../lib/browser-profile.ts');
const {createEmptyProfile, loadSavedProfile, loadWorkingProfile, saveProfile, saveWorkingProfile, summarizePersonalProfile} = browser;
const {parseLocalDemoProfile, applyLocalDemoProfile, initializeBrowserProfile} = moduleFrom('../lib/local-demo-profile.ts', {'./browser-profile': browser});
const {evaluateRule} = moduleFrom('../lib/eligibility.ts');
// Synthetic test data, not the demo owner's personal history.
const minimal = {institution: 'POSTECH', department: 'cse', graduateSchool: '테스트 대학원', academicLevel: 'master', enrollmentStatus: 'enrolled', graduateSemester: '2', fullTimeStudent: 'true', highestDegreeLevel: 'bachelor', highestDegreeInstitution: 'Example University', birthYear: '2001', nationality: 'KR', employmentStatus: 'unemployed'};
const fragment = envelope => '#local-profile=' + Buffer.from(JSON.stringify(envelope)).toString('base64url');
const hash = fragment({version: 1, profile: minimal});
function storage() {
  const data = new Map();
  return {getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key)};
}
function access(local = storage(), session = storage()) {return {local: () => local, session: () => session};}
let count = 0;
function test(name, run) {run(); count++; console.log(`PASS ${name}`);}

test('all twelve minimal fields including Korean text are imported on loopback', () => {
  for (const hostname of ['localhost', '127.0.0.1', '[::1]', '::1']) assert.deepEqual(parseLocalDemoProfile(hostname, hash), minimal);
});
test('public hosts and lookalike loopback domains cannot apply private defaults', () => {
  for (const hostname of ['example.com', 'localhost.example.com', '127.0.0.1.example.com']) assert.equal(parseLocalDemoProfile(hostname, hash), null);
});
test('empty public profile remains empty without an import', () => {
  const restored = initializeBrowserProfile(access());
  assert.deepEqual(restored.profile, createEmptyProfile()); assert.equal(restored.savedProfile, null);
  assert.equal(summarizePersonalProfile(restored.profile), '');
});
test('malformed, oversized and incompatible fragments are ignored', () => {
  for (const invalid of ['', '#other=x', '#local-profile=invalid!', '#local-profile=' + 'a'.repeat(8000), fragment({version: 2, profile: minimal}), fragment({version: 1, profile: []}), fragment({version: 1, profile: null})]) assert.equal(parseLocalDemoProfile('localhost', invalid), null);
});
test('credentials and optional eligibility facts cannot be added through a minimal demo link', () => {
  const parsed = parseLocalDemoProfile('localhost', fragment({version: 1, profile: {...minimal, age: '26', gpa: '4.0', incomeBand: '1', accessToken: 'ignored', conditionAnswers: {a: 'true'}, fullTimeTrainingAvailable: 'true'}}));
  assert.deepEqual(parsed, minimal);
});
test('invalid enum, type, birth year and semester values stay unknown', () => {
  const parsed = parseLocalDemoProfile('localhost', fragment({version: 1, profile: {institution: 'invented', nationality: 'unknown', fullTimeStudent: true, birthYear: '2001-01-01', graduateSemester: '0', academicLevel: 'doctoral'}}));
  assert.deepEqual(parsed, {academicLevel: 'doctoral'});
  for (const value of ['31', '-1', '1.5']) assert.equal(parseLocalDemoProfile('localhost', fragment({version: 1, profile: {graduateSemester: value}})), null);
});
test('applying known facts preserves extra input but clears an obsolete exact age', () => {
  const current = {...createEmptyProfile(), age: '22', gpa: '3.7', incomeBand: '4', residenceCity: '서울', conditionAnswers: {existing: 'false'}};
  const applied = applyLocalDemoProfile(current, minimal);
  assert.equal(applied.age, ''); assert.equal(applied.gpa, '3.7'); assert.equal(applied.incomeBand, '4'); assert.equal(applied.residenceCity, '서울');
  assert.deepEqual(applied.conditionAnswers, current.conditionAnswers); assert.equal(current.age, '22');
  assert.equal(applyLocalDemoProfile(current, {academicLevel: 'master'}).age, '22');
});
test('without an import current tab input still takes precedence over a saved default', () => {
  const stores = access();
  saveProfile(stores.local(), {...createEmptyProfile(), academicLevel: 'master'});
  saveWorkingProfile(stores.session(), {...createEmptyProfile(), academicLevel: 'doctoral'});
  assert.equal(initializeBrowserProfile(stores).profile.academicLevel, 'doctoral');
});
test('demo updates both saved default and current tab then survives a route change', () => {
  const stores = access();
  saveWorkingProfile(stores.session(), {...createEmptyProfile(), academicLevel: 'undergraduate', age: '18', gpa: '3.6'});
  const applied = initializeBrowserProfile(stores, parseLocalDemoProfile('localhost', hash));
  assert.equal(applied.profile.academicLevel, 'master'); assert.equal(applied.profile.gpa, '3.6'); assert.equal(applied.profile.age, '');
  assert.deepEqual(loadSavedProfile(stores.local()), applied.profile);
  assert.deepEqual(loadWorkingProfile(stores.session()), applied.profile);
  assert.deepEqual(initializeBrowserProfile(stores).profile, applied.profile);
  assert.match(applied.message, /저장했습니다/);
});
test('saved default works in a new tab with no working input', () => {
  const stores = access();
  const applied = initializeBrowserProfile(stores, minimal);
  assert.deepEqual(initializeBrowserProfile(access(stores.local(), storage())).profile, applied.profile);
});
test('cleared or edited working input cannot erase previously saved extra facts', () => {
  const stores = access();
  saveProfile(stores.local(), {...createEmptyProfile(), gpa: '3.9', incomeBand: '3', codetreePaid: 'false'});
  saveWorkingProfile(stores.session(), {...createEmptyProfile(), gpa: '3.5'});
  const applied = initializeBrowserProfile(stores, minimal);
  assert.equal(applied.profile.gpa, '3.5'); assert.equal(applied.savedProfile.gpa, '3.9');
  assert.equal(applied.savedProfile.incomeBand, '3'); assert.equal(applied.savedProfile.codetreePaid, 'false');
  assert.equal(loadWorkingProfile(stores.session()).gpa, '3.5'); assert.equal(loadSavedProfile(stores.local()).gpa, '3.9');
});
test('a blocked local storage getter does not discard existing session input', () => {
  const stores = access();
  saveWorkingProfile(stores.session(), {...createEmptyProfile(), gpa: '3.8'});
  stores.local = () => {throw new Error('blocked');};
  const applied = initializeBrowserProfile(stores, minimal);
  assert.equal(applied.profile.gpa, '3.8'); assert.equal(applied.profile.academicLevel, 'master'); assert.equal(applied.savedProfile, null);
  assert.match(applied.message, /현재 탭/);
});
test('failed writes retain input and the previous saved default without claiming a save', () => {
  const local = storage();
  const old = {...createEmptyProfile(), academicLevel: 'doctoral'};
  saveProfile(local, old);
  local.setItem = () => {throw new Error('quota');};
  const applied = initializeBrowserProfile(access(local), minimal);
  assert.equal(applied.profile.academicLevel, 'master'); assert.deepEqual(applied.savedProfile, old);
  assert.doesNotMatch(applied.message, /저장했습니다/);
});
test('an unreadable existing default is never overwritten by the demo', () => {
  let writes = 0;
  const local = {getItem: () => {throw new Error('read blocked');}, setItem: () => {writes++;}, removeItem() {}};
  const applied = initializeBrowserProfile(access(local), minimal);
  assert.equal(writes, 0); assert.equal(applied.profile.birthYear, minimal.birthYear); assert.equal(applied.savedProfile, null);
});
test('failed session writes invalidate an obsolete working profile before another route load', () => {
  const stores = access();
  saveWorkingProfile(stores.session(), {...createEmptyProfile(), academicLevel: 'undergraduate'});
  stores.session().setItem = () => {throw new Error('quota');};
  const applied = initializeBrowserProfile(stores, minimal);
  assert.equal(loadWorkingProfile(stores.session()), null);
  assert.deepEqual(initializeBrowserProfile(stores).profile, applied.savedProfile);
});
test('nonrecoverable stale session storage is reported as a partial save', () => {
  const stores = access();
  saveWorkingProfile(stores.session(), {...createEmptyProfile(), academicLevel: 'undergraduate'});
  stores.session().setItem = stores.session().removeItem = () => {throw new Error('blocked');};
  const applied = initializeBrowserProfile(stores, minimal);
  assert.equal(applied.savedProfile.academicLevel, 'master'); assert.match(applied.message, /새 탭/);
});
test('failure of both writes preserves the previous working snapshot as well as the saved default', () => {
  const stores = access();
  saveProfile(stores.local(), {...createEmptyProfile(), gpa: '3.9'});
  saveWorkingProfile(stores.session(), {...createEmptyProfile(), gpa: '3.5', residenceCity: '서울'});
  stores.local().setItem = stores.session().setItem = () => {throw new Error('quota');};
  const applied = initializeBrowserProfile(stores, minimal);
  assert.equal(applied.profile.academicLevel, 'master'); assert.equal(loadWorkingProfile(stores.session()).gpa, '3.5');
  assert.equal(loadWorkingProfile(stores.session()).residenceCity, '서울'); assert.equal(loadSavedProfile(stores.local()).gpa, '3.9');
  assert.match(applied.message, /이번 화면에서만/);
});
test('blocked local and session getters still permit a current-screen demo', () => {
  const blocked = () => {throw new Error('disabled');};
  const applied = initializeBrowserProfile({local: blocked, session: blocked}, minimal);
  assert.equal(applied.profile.birthYear, minimal.birthYear); assert.equal(applied.profile.gpa, ''); assert.equal(applied.profile.incomeBand, '');
});
test('compact profile summary displays known facts without inventing age or activity intent', () => {
  const summary = summarizePersonalProfile({...createEmptyProfile(), ...minimal}, '컴퓨터공학과');
  for (const text of ['POSTECH', '테스트 대학원', '컴퓨터공학과', '석사 2학기', '재학', '전일제', 'Example University 학사 취득', '2001년생', '대한민국 국적', '미취업']) assert.ok(summary.includes(text));
  assert.doesNotMatch(summary, /만 \d+세|참여 가능|소득/);
});
test('birth year bounds are used after an old exact age is cleared', () => {
  const applied = applyLocalDemoProfile({...createEmptyProfile(), age: '17'}, minimal);
  const rule = {kind: 'rule', id: 'age', field: 'age', operator: 'between', value: [19, 34], valueType: 'integer', label: '19~34세'};
  assert.equal(evaluateRule(applied, rule, new Date('2026-10-04T00:00:00+09:00')), 'pass');
  assert.equal(evaluateRule(applied, {...rule, operator: 'lte', value: 24}, new Date('2026-10-04T00:00:00+09:00')), 'unknown');
});

console.log(`${count} checks passed`);
