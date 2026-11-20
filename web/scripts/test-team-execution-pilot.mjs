import assert from 'node:assert/strict';
import {readFileSync, mkdirSync, symlinkSync, rmSync, mkdtempSync} from 'node:fs';
import {dirname, resolve, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
import {runPilot, resolvedOutputPath} from './run-team-execution-pilot.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const snapshot = JSON.parse(readFileSync(resolve(root, 'data/sample/programs.json'), 'utf8'));
const input = JSON.parse(readFileSync(resolve(root, 'evaluation/spec_examples/team-execution-profile.synthetic.json'), 'utf8'));
const copy = value => structuredClone(value);
globalThis.fetch = () => {throw new Error('Tests must not request a network resource.');};
let checks = 0;
async function test(name, run) {await run(); checks++; console.log('PASS', name);}
const base = await runPilot(copy(snapshot), copy(input));

await test('historical matching does not promote three basic matches to current verified applications', async () => {
  assert.equal(base.records.length, 10);
  assert.equal(base.records.filter(row => row.decision === 'basic_conditions_match').length, 3);
  assert(base.records.every(row => row.current_status_verified === false && row.production_eligible === false));
  const nvidia = base.records.find(row => row.opportunity_id === 'nvidia-2027-2028');
  assert.equal(nvidia.snapshot_application_state, 'open');
  assert.equal(nvidia.current_application_status, 'unknown_not_reverified');
  assert(nvidia.next_questions.length > 0);
});

await test('high GPA never changes the explicit not-completed coursework fact', async () => {
  const profile = copy(input);
  profile.canonical_profile['education.gpa'] = {value: 4.3, scale: 4.3, degree_scope: 'phd', period_scope: 'cumulative'};
  const result = await runPilot(copy(snapshot), profile);
  assert.equal(result.records.find(row => row.opportunity_id === 'hyundai-mobis-research-scholar-2026-h2').decision, 'basic_conditions_mismatch');
  assert.equal(result.profile_trace.coursework_from_input, false);
  assert.equal(result.profile_trace.coursework_inferred_from_gpa, false);
  const google = result.records.find(row => row.opportunity_id === 'google-east-asia-2026');
  assert.equal(google.additional_checks.find(check => check.check_id === 'coursework').result, 'unknown');
});

await test('a basic mismatch does not trigger irrelevant personal questions', async () => {
  const mismatches = base.records.filter(row => row.decision === 'basic_conditions_mismatch');
  assert(mismatches.length > 0);
  for (const row of mismatches) {
    assert.equal(row.next_questions.length, 0);
    assert.equal(row.question_scope, 'not_requested_for_basic_mismatch');
  }
});

await test('missing or incompatible GPA basis stays unknown for an undergraduate candidate', async () => {
  const profile = copy(input);
  profile.canonical_profile['education.degree'] = 'bachelors';
  let result = await runPilot(copy(snapshot), profile);
  assert.equal(result.records.find(row => row.opportunity_id === 'ikef-undergraduate-2026').decision, 'needs_info');
  profile.canonical_profile['education.gpa'] = {value: 4.3, scale: 4.3, degree_scope: 'phd', period_scope: 'cumulative'};
  result = await runPilot(copy(snapshot), profile);
  assert.equal(result.records.find(row => row.opportunity_id === 'ikef-undergraduate-2026').decision, 'needs_info');
});

await test('a representation gap is reported instead of erasing the canonical GPA scope', async () => {
  const profile = copy(input);
  profile.canonical_profile['education.degree'] = 'bachelors';
  profile.canonical_profile['education.gpa'] = {value: 4, scale: 4.5, degree_scope: 'bachelors', period_scope: 'semesters_1_to_3'};
  const result = await runPilot(copy(snapshot), profile);
  const record = result.records.find(row => row.opportunity_id === 'ikef-undergraduate-2026');
  assert.equal(record.decision, 'basic_conditions_match');
  assert.equal(record.web_bridge.basic_result_agrees, false);
  assert(record.web_bridge.adapter_unrepresented.some(item => item.field === 'education.gpa'));
  assert.equal(record.production_eligible, false);
});

await test('another subject or experience cannot supply a confirmed fact', async () => {
  for (const key of ['subject_id', 'experience_id']) {
    const profile = copy(input);
    profile.confirmed_facts[0][key] = 'another-' + key;
    await assert.rejects(runPilot(copy(snapshot), profile), /another subject or experience/);
  }
});

await test('a public reference case is not adopted as this synthetic applicant fact', async () => {
  const profile = copy(input);
  profile.confirmed_facts[0].source.kind = 'public_reference_case';
  profile.confirmed_facts[0].value = 'REFERENCE_CASE_MUST_NOT_ENTER_THE_DRAFT';
  const result = await runPilot(copy(snapshot), profile);
  assert(!JSON.stringify(result.ready_draft).includes('REFERENCE_CASE_MUST_NOT_ENTER_THE_DRAFT'));
  assert.equal(result.profile_trace.withheld_facts.length, 1);
});

await test('unconfirmed and non-text values are withheld without fabricating a replacement', async () => {
  const profile = copy(input);
  profile.confirmed_facts[0].status = 'candidate';
  profile.confirmed_facts[0].value = 'UNCONFIRMED_VALUE';
  profile.confirmed_facts[1].value = {claim: 'NON_TEXT_VALUE'};
  const result = await runPilot(copy(snapshot), profile);
  assert.equal(result.profile_trace.withheld_facts.length, 2);
  assert(!JSON.stringify(result.ready_draft).includes('UNCONFIRMED_VALUE'));
  assert(!JSON.stringify(result.ready_draft).includes('NON_TEXT_VALUE'));
  assert.equal(result.ready_draft.draft.factual_claims_added, false);
});

await test('confirmed material for an undeclared slot remains unused', async () => {
  const profile = copy(input), fact = copy(profile.confirmed_facts[0]);
  fact.fact_id = 'unrelated-fact'; fact.field = 'unrelated_task_material'; fact.value = 'UNRELATED_MATERIAL';
  profile.confirmed_facts.push(fact);
  const result = await runPilot(copy(snapshot), profile);
  assert(result.ready_draft.unused_confirmed_fact_ids.includes('unrelated-fact'));
  assert(!JSON.stringify(result.ready_draft.draft).includes('UNRELATED_MATERIAL'));
});

await test('coursework boolean strings and untyped GPA values are rejected', async () => {
  let profile = copy(input);
  profile.canonical_profile['education.coursework_completed'] = 'false';
  await assert.rejects(runPilot(copy(snapshot), profile), /must be a boolean/);
  profile = copy(input);
  profile.canonical_profile['education.gpa'] = {value: '4.0', scale: 4.5, degree_scope: 'phd', period_scope: 'cumulative'};
  await assert.rejects(runPilot(copy(snapshot), profile), /GPA must retain/);
});

await test('invented official wording and mismatched historical dates fail the handoff', async () => {
  let profile = copy(input);
  profile.writing_task.question_text = 'An invented official question';
  await assert.rejects(runPilot(copy(snapshot), profile), /wording is absent/);
  profile = copy(input); profile.as_of = '2026-11-19';
  await assert.rejects(runPilot(copy(snapshot), profile), /historical as_of/);
});

await test('a page bound is not certified by a character counter and the missing plan stays explicit', async () => {
  assert.equal(base.ready_draft.kind, 'deterministic_preparation_draft');
  assert(base.ready_draft.draft.sections[0].missing_slots.includes('future_plan'));
  assert.equal(base.ready_draft.draft.sections[0].length_status, 'unknown');
  assert.equal(base.ready_draft.user_application_submitted, false);
  assert.equal(base.official_prompt_verification.raw_sha256_verified, true);
  assert.equal(base.official_prompt_verification.text_sha256_verified, true);
});

await test('a correct phrase with a wrong source line locator is rejected', async () => {
  const profile = copy(input);
  profile.writing_task.source_ref.locator = 'L1-L2';
  await assert.rejects(runPilot(copy(snapshot), profile), /declared source line span/);
});

await test('both calls use the same input and produce identical results', async () => {
  assert.deepEqual(await runPilot(copy(snapshot), copy(input)), base);
  assert.equal(base.network_requests + base.model_calls + base.database_writes, 0);
});

await test('output paths resolve through an approved cache symlink and reject dangling links', async () => {
  const folder = mkdtempSync(join(tmpdir(), 'benefitlens-pilot-path-'));
  try {
    mkdirSync(join(folder, 'actual')); symlinkSync(join(folder, 'actual'), join(folder, 'cache'));
    assert.equal(resolvedOutputPath(join(folder, 'cache', 'new', 'result.json')), join(folder, 'actual', 'new', 'result.json'));
    symlinkSync(join(folder, 'missing-destination'), join(folder, 'dangling'));
    assert.throws(() => resolvedOutputPath(join(folder, 'dangling')));
  } finally {rmSync(folder, {recursive: true, force: true});}
});

console.log(JSON.stringify({checks_passed: checks, network_requests: 0, model_calls: 0, database_writes: 0}));
