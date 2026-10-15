#!/usr/bin/env node
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

// These tests read the source-backed dataset but never alter it. Additional
// synthetic configs below are explicitly invented mechanics cases, not notices.
const require = createRequire(import.meta.url);
const args = process.argv.slice(2);
const options = {};
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--help') {
    console.log('node academic-condition-engine.test.mjs [--module ENGINE.ts] [--dataset NOTICES.json] [--typescript TYPESCRIPT_PACKAGE] [--project-root PROJECT] [--out REPORT.json]');
    process.exit(0);
  }
  if (!['--module', '--dataset', '--typescript', '--project-root', '--out'].includes(args[i]) || !args[i + 1]) throw new Error(`Invalid option ${args[i]}; --help shows usage`);
  options[args[i].slice(2)] = args[++i];
}
const projectRoot = options['project-root'] ?? process.env.BENEFITLENS_PROJECT_ROOT;
const tsPackage = options.typescript ?? (projectRoot ? resolve(projectRoot, 'web/node_modules/typescript') : 'typescript');
const ts = require(tsPackage);
const localModule = fileURLToPath(new URL('./academic-condition-engine.ts', import.meta.url));
const modulePath = options.module ? resolve(options.module) : projectRoot ? resolve(projectRoot, 'web/lib/academic-condition-engine.ts') : existsSync(localModule) ? localModule : fileURLToPath(new URL('../lib/academic-condition-engine.ts', import.meta.url));
const source = readFileSync(modulePath, 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const engine = {};
new Function('exports', 'require', compiled)(engine, require);
const { evaluateAcademicRequirements: evaluate, validateAcademicConfig, compareEnglish, validIsoDate } = engine;
const localDataset = fileURLToPath(new URL('./data/academic-demo-notices.json', import.meta.url));
const datasetPath = options.dataset ? resolve(options.dataset) : projectRoot ? resolve(projectRoot, 'pipeline/tests/gold/academic_language/2026-10-04/academic-demo-notices.json') : existsSync(localDataset) ? localDataset : fileURLToPath(new URL('../../pipeline/tests/gold/academic_language/2026-10-04/academic-demo-notices.json', import.meta.url));
const originalDataset = readFileSync(datasetPath);
const notices = JSON.parse(originalDataset.toString('utf8'));
const cmk = notices.find(item => item.id === 'jeongmonggu-2025').ruleSet;
const pohang = notices.find(item => item.id === 'pohang-regional-2026').ruleSet;
const results = [];
function test(name, run) { run(); results.push({ name, passed: true }); console.log(`PASS ${name}`); }
const copy = value => structuredClone(value);
const pathRule = key => cmk.paths[key];
function gradeProfile(key, score, overrides = {}) {
  const path = pathRule(key), rule = path.grade.alternatives[0];
  return { education_path: path.education_path, source_path: key, grades: [{ metric: rule.metric, score, scale: rule.scale, period: rule.period, context: rule.context, ...overrides }], english: [] };
}
function examFact(testType, score, date) {
  const rule = pathRule('graduate_new').english.alternatives.find(item => item.test_type === testType);
  return { test_type: testType, score, test_date: date, score_scale: rule.score_scale };
}
const goodGrad = () => gradeProfile('graduate_new', 90);
const englishResult = (english, inventoryComplete = false, config = cmk) => evaluate({ ...goodGrad(), english, english_inventory_complete: inventoryComplete }, config);
const englishLeaf = (comparison, type) => comparison.english.traces.find(item => item.expected?.test_type === type);

test('Both source datasets have valid syntax and resolvable evidence references', () => {
  for (const item of notices) assert.deepEqual(validateAcademicConfig(item.ruleSet), []);
});
test('Undergraduate English not_applicable does not turn into overall eligibility', () => {
  const comparison = evaluate(gradeProfile('undergraduate_enrolled', 90), cmk);
  assert.equal(comparison.grade.result, 'pass');
  assert.equal(comparison.english.applicability, 'not_applicable');
  assert.equal(comparison.english.result, 'unknown');
  assert.equal(comparison.comparison_result, 'pass');
  assert.equal(comparison.overall_eligibility, 'not_evaluated');
  assert.equal(comparison.selection_and_payment, 'not_evaluated');
  assert.equal(comparison.label, '성적, 어학 요건 비교');
});
test('Percentile boundary and numeric input validity remain distinct', () => {
  assert.equal(evaluate(gradeProfile('undergraduate_enrolled', 89.9), cmk).grade.result, 'fail');
  assert.equal(evaluate(gradeProfile('undergraduate_enrolled', 90), cmk).grade.result, 'pass');
  for (const score of ['90', null, -1, 100.1, NaN]) assert.equal(evaluate(gradeProfile('undergraduate_enrolled', score), cmk).grade.result, 'unknown');
});
test('GPA never becomes the source percentile by proportional conversion', () => {
  assert.equal(evaluate(gradeProfile('undergraduate_enrolled', 4.5, { metric: 'GPA', scale: 4.5 }), cmk).grade.result, 'unknown');
});
test('Graduate-new and enrolled degree contexts cannot substitute for one another', () => {
  const newProfile = gradeProfile('graduate_new', 90);
  assert.equal(evaluate(newProfile, cmk).grade.result, 'pass');
  assert.equal(evaluate({ ...newProfile, source_path: 'graduate_enrolled' }, cmk).grade.result, 'unknown');
  assert.equal(evaluate(gradeProfile('graduate_new', 90, { period: 'all_semesters' }), cmk).grade.result, 'unknown');
  assert.equal(evaluate({ ...newProfile, education_path: 'undergraduate' }, cmk).comparison_result, 'unknown');
});
test('Both explicit English test-date endpoints are included', () => {
  for (const date of ['2023-04-01', '2025-04-16']) assert.equal(englishResult([examFact('TOEIC', 850, date)]).english.result, 'pass');
});
test('Dates one day outside the window fail the entered test, while incomplete inventory stays unknown', () => {
  for (const date of ['2023-03-31', '2025-04-17']) {
    const incomplete = englishResult([examFact('TOEIC', 850, date)]);
    assert.equal(englishLeaf(incomplete, 'TOEIC').records[0].result, 'fail');
    assert.equal(incomplete.english.result, 'unknown');
    assert.equal(englishResult([examFact('TOEIC', 850, date)], true).english.result, 'fail');
  }
});
test('A low selected test and an unentered accepted alternative have different results', () => {
  const incomplete = englishResult([examFact('TOEIC', 849, '2025-04-16')]);
  assert.equal(englishLeaf(incomplete, 'TOEIC').records[0].result, 'fail');
  assert.equal(incomplete.english.result, 'unknown');
  assert.equal(englishResult([examFact('TOEIC', 849, '2025-04-16')], true).english.result, 'fail');
});
test('One accepted passing alternative decides OR even with another failed test', () => {
  const comparison = englishResult([examFact('TOEIC', 849, '2025-04-16'), examFact('IELTS', 6.5, '2023-04-01')]);
  assert.equal(comparison.english.result, 'pass');
  assert.equal(comparison.comparison_result, 'pass');
});
test('A different TOEFL scale identifier is never converted to the historical score scale', () => {
  const fact = { ...examFact('TOEFL_iBT', 5, '2025-04-16'), score_scale: 'toefl_ibt_total_6_post20260121' };
  const comparison = englishResult([fact], true);
  assert.equal(englishLeaf(comparison, 'TOEFL_iBT').records[0].result, 'unknown');
  assert.equal(comparison.english.result, 'unknown');
});
test('Configured numeric domains reject strings, negative scores and impossible upper scores', () => {
  for (const score of ['850', -1, 991, null]) assert.equal(englishResult([examFact('TOEIC', score, '2025-04-16')], true).english.result, 'unknown');
});
test('IELTS half-point precision is enforced only when config explicitly supplies step', () => {
  const config = copy(cmk);
  config.paths.graduate_new.english.alternatives.find(item => item.test_type === 'IELTS').score_domain.step = 0.5;
  assert.equal(englishResult([examFact('IELTS', 6.7, '2025-04-16')], true, config).english.result, 'unknown');
  assert.equal(englishResult([examFact('IELTS', 6.5, '2025-04-16')], true, config).english.result, 'pass');
});
test('An unsupported source alternative stays unknown instead of disappearing', () => {
  const comparison = englishResult([examFact('OPIc', 'IM3', '2025-04-16')], true);
  assert.equal(comparison.english.traces.length, 7);
  assert.equal(englishLeaf(comparison, 'OPIc').records[0].result, 'unknown');
  assert.equal(comparison.english.result, 'unknown');
});
test('No inferred two-year expiry is applied when a source config has no date window', () => {
  const requirement = copy(pathRule('graduate_new').english);
  requirement.alternatives = [requirement.alternatives.find(item => item.test_type === 'TOEIC')];
  delete requirement.alternatives[0].explicit_date_window;
  assert.equal(compareEnglish({ english: [examFact('TOEIC', 850, '2000-01-01')] }, requirement).result, 'pass');
});
test('Missing and overflowed test dates remain unknown', () => {
  for (const date of ['', '2025-02-30', '2025-4-1', null]) assert.equal(englishResult([examFact('TOEIC', 850, date)], true).english.result, 'unknown');
  assert.equal(validIsoDate('2024-02-29'), true);
  assert.equal(validIsoDate('2025-02-29'), false);
});
test('Ordered grades use only a caller-provided evidence config, never lexical or numeric guessing', () => {
  const base = copy(pathRule('graduate_new').english.alternatives.find(item => item.test_type === 'OPIc'));
  // Deliberately invented rank names; these are not an official OPIc scale.
  Object.assign(base, { supported: true, minimum_score: 'MIDDLE', ordered_values: ['LOW', 'MIDDLE', 'HIGH'] });
  const requirement = { applicability: 'required', alternatives: [base] };
  const fact = score => ({ test_type: 'OPIc', score, test_date: '2025-04-16', score_scale: base.score_scale });
  assert.equal(compareEnglish({ english: [fact('HIGH')] }, requirement).result, 'pass');
  assert.equal(compareEnglish({ english: [fact('LOW')], english_inventory_complete: true }, requirement).result, 'fail');
  assert.equal(compareEnglish({ english: [fact('IM3')] }, requirement).result, 'unknown');
  delete base.ordered_values;
  assert.equal(compareEnglish({ english: [fact('HIGH')] }, requirement).result, 'unknown');
});
test('Source version, byte hash and grounded uncompared requirements are preserved', () => {
  const comparison = evaluate(gradeProfile('graduate_new', 90), cmk);
  assert.deepEqual(comparison.source, cmk.source);
  assert.deepEqual(comparison.uncompared_requirements, pathRule('graduate_new').uncompared_requirements);
  assert.equal(comparison.rule_version, cmk.rule_version);
});
test('Missing education paths and conflicting duplicate grades need confirmation', () => {
  assert.equal(evaluate({}, cmk).comparison_result, 'unknown');
  const profile = gradeProfile('undergraduate_enrolled', 90);
  profile.grades.push({ ...profile.grades[0], score: 89 });
  assert.equal(evaluate(profile, cmk).grade.result, 'unknown');
});
test('A numeric GPA on a different source scale remains unknown', () => {
  const key = Object.keys(pohang.paths)[0], path = pohang.paths[key], rule = path.grade.alternatives[0];
  const profile = { education_path: path.education_path, source_path: key, grades: [{ metric: 'GPA', score: 2.5, scale: 4.3, period: rule.period, context: rule.context }] };
  assert.equal(evaluate(profile, pohang).grade.result, 'unknown');
});
test('Malformed configs fail as unknown, rather than approving a bare threshold', () => {
  const config = copy(cmk); config.paths.graduate_new.english.alternatives[0].explicit_date_window.to = '2025-02-30';
  assert.ok(validateAcademicConfig(config).length > 0);
  assert.equal(evaluate(goodGrad(), config).comparison_result, 'unknown');
});
test('Neither source dataset bytes nor caller configs/facts change during evaluation', () => {
  assert.deepEqual(readFileSync(datasetPath), originalDataset);
  const profile = goodGrad(), config = copy(cmk), before = copy({ profile, config });
  evaluate(profile, config);
  assert.deepEqual({ profile, config }, before);
});
const report = { suite: 'academic condition comparison: source-backed dataset plus explicitly invented mechanics cases', tested_at: new Date().toISOString(), count: results.length, all_passed: true, module_sha256: createHash('sha256').update(source).digest('hex'), dataset_sha256: createHash('sha256').update(originalDataset).digest('hex'), datasets: notices.map(item => ({ id: item.id, rule_version: item.ruleSet.rule_version, source_sha256: item.ruleSet.source.sha256 })), tests: results };
if (options.out) writeFileSync(options.out, JSON.stringify(report, null, 2) + '\n');
console.log(`${results.length} academic boundary/scenario checks passed`);
