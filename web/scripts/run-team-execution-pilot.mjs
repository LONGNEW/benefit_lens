/** Historical, synthetic integration replay. No network, model or database calls. */
import {readFileSync, writeFileSync, mkdirSync, renameSync, realpathSync, lstatSync} from 'node:fs';
import {dirname, resolve, relative, basename} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import * as nodeModule from 'node:module';
import {gunzipSync} from 'node:zlib';
import {evaluateEligibility, evaluateAdditional, applicationState} from '../pipeline-demo/match.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const CACHE = '/home/longnew/.cache/datasets/benefitlens/scale50k-20261109';
export const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const readJson = path => JSON.parse(readFileSync(path, 'utf8'));

export function resolvedOutputPath(path) {
  let anchor = resolve(path);
  const suffix = [];
  while (true) {
    try { lstatSync(anchor); break; }
    catch (error) {
      if (error.code !== 'ENOENT' || dirname(anchor) === anchor) throw error;
      suffix.unshift(basename(anchor)); anchor = dirname(anchor);
    }
  }
  // realpath also rejects a dangling symlink rather than treating it as a
  // normal filename that could later redirect the write outside the cache.
  return resolve(realpathSync(anchor), ...suffix);
}

async function localTs(name) {
  if (typeof nodeModule.stripTypeScriptTypes !== 'function') {
    throw new Error('Node >=22.13 is required; reuse the installed Node 22 runtime, do not install packages.');
  }
  const path = resolve(ROOT, 'web/lib', name);
  const code = nodeModule.stripTypeScriptTypes(readFileSync(path, 'utf8'), {mode: 'strip', sourceUrl: path});
  return import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
}

const degree = value => ({phd: 'doctoral', masters: 'master', bachelors: 'undergraduate'}[value] ?? value);
const FIELDS = {
  'education.degree': 'academicLevel', 'education.status': 'enrollmentStatus',
  'education.institution_id': 'institution', 'education.department_id': 'department',
  'education.semester': 'graduateSemester', 'education.year': 'year',
  'education.full_time': 'fullTimeStudent', 'education.coursework_completed': 'courseworkCompleted',
  'person.nationalities': 'nationality', 'employment.status': 'employmentStatus',
};
function mappedValue(field, value) {
  const scalar = item => field === 'education.degree' ? degree(item)
    : field === 'education.institution_id' && item === 'postech' ? 'POSTECH' : item;
  return Array.isArray(value) ? value.map(scalar) : scalar(value);
}

function webProfile(canonical, empty, normalize) {
  const profile = empty();
  for (const [field, target] of Object.entries(FIELDS)) {
    const value = canonical[field];
    if (value === undefined || value === null) continue;
    if (field === 'person.nationalities' && (!Array.isArray(value) || value.length !== 1)) continue;
    profile[target] = String(mappedValue(field, Array.isArray(value) ? value[0] : value));
  }
  const gpa = canonical['education.gpa'];
  if (gpa && typeof gpa === 'object') {
    profile.gpa = String(gpa.value); profile.gpaScale = String(gpa.scale);
    profile.gpaBasis = gpa.period_scope === 'cumulative' ? 'cumulative' : '';
    profile.gpaEvaluationPeriod = gpa.period_scope ?? '';
  }
  return normalize(profile);
}

function webCondition(node, item, audit, position = 'root') {
  if (!node || typeof node !== 'object') return null;
  const operator = ['all', 'any', 'not'].find(key => node[key] !== undefined);
  if (operator) {
    const children = operator === 'not' ? [node.not] : node[operator];
    return {kind: 'group', id: item.opportunity_id + ':' + position,
      operator: {all: 'and', any: 'or', not: 'not'}[operator], label: operator,
      children: children.map((child, index) => webCondition(child, item, audit, position + '.' + index))};
  }
  const ref = (item.source_refs ?? []).find(ref => ref.id === node.evidence_ref);
  const usable = Boolean(FIELDS[node.field]) && (!node.at || node.at === 'application');
  if (!usable) audit.push({field: node.field, at: node.at, condition_path: position,
    reason: 'not_represented_by_the_legacy_web_profile; canonical_engine_retained'});
  return {kind: 'rule', id: item.opportunity_id + ':' + position,
    criterionCode: node.field, field: usable ? FIELDS[node.field] : '__unsupported__:' + node.field,
    operator: node.op, value: mappedValue(node.field, node.value),
    valueType: typeof node.value === 'number' ? 'number' : typeof node.value === 'boolean' ? 'boolean' : 'enum',
    label: node.field + ' [' + (node.at ?? 'application') + ']',
    sourceUrl: ref?.url, sourceText: JSON.stringify(node)};
}

export function confirmedFacts(profile) {
  if (profile.example_kind !== 'synthetic') throw new Error('This pilot accepts only explicitly synthetic profiles.');
  const seen = new Set(), fields = new Map(), accepted = [], withheld = [];
  for (const fact of profile.confirmed_facts ?? []) {
    if (!fact.fact_id || seen.has(fact.fact_id)) throw new Error('Missing or duplicate fact identity.');
    seen.add(fact.fact_id);
    if (fact.subject_id !== profile.subject_id || fact.experience_id !== profile.experience_id
        || fact.source?.owner_subject_id !== profile.subject_id) {
      throw new Error('A fact belongs to another subject or experience.');
    }
    if (fact.status !== 'confirmed' || fact.source?.kind !== 'synthetic_confirmed_fact'
        || !fact.source?.evidence_id || typeof fact.value !== 'string' || !fact.value.trim()) {
      withheld.push({fact_id: fact.fact_id, reason: 'not_confirmed_for_this_synthetic_pilot'}); continue;
    }
    if (fields.has(fact.field) && fields.get(fact.field) !== fact.value) throw new Error('Conflicting confirmed facts.');
    fields.set(fact.field, fact.value); accepted.push(fact);
  }
  return {accepted, withheld};
}

function validateCanonicalProfile(profile) {
  if (!profile || typeof profile !== 'object' || Array.isArray(profile)) throw new Error('Expected typed canonical profile.');
  for (const field of ['education.full_time', 'education.coursework_completed']) {
    if (profile[field] != null && typeof profile[field] !== 'boolean') throw new Error(field + ' must be a boolean.');
  }
  for (const field of ['education.semester', 'education.year']) {
    if (profile[field] != null && !Number.isInteger(profile[field])) throw new Error(field + ' must be an integer.');
  }
  const nations = profile['person.nationalities'];
  if (nations != null && (!Array.isArray(nations) || !nations.length
      || nations.some(value => typeof value !== 'string' || !value.trim()))) {
    throw new Error('Nationalities must be a nonempty list or be omitted as unknown.');
  }
  const gpa = profile['education.gpa'];
  if (gpa != null && (!Number.isFinite(gpa.value) || !Number.isFinite(gpa.scale)
      || typeof gpa.degree_scope !== 'string' || typeof gpa.period_scope !== 'string')) {
    throw new Error('GPA must retain numeric value and scale, degree scope and period scope.');
  }
}

function checkedPrompt(profile, selected) {
  const task = profile.writing_task;
  if (!task || !selected.application?.prompts?.some(prompt => prompt.kind === task.genre)) {
    throw new Error('The selected opportunity does not contain this writing task kind.');
  }
  const ref = task.source_ref;
  const rawPacked = readFileSync(ref.raw_path), textPacked = readFileSync(ref.path);
  const raw = ref.raw_path.endsWith('.gz') ? gunzipSync(rawPacked) : rawPacked;
  const text = (ref.path.endsWith('.gz') ? gunzipSync(textPacked) : textPacked).toString('utf8');
  if (sha256(raw) !== ref.raw_sha256 || sha256(Buffer.from(text)) !== ref.text_sha256) {
    throw new Error('Official prompt snapshot checksum differs.');
  }
  if (!(selected.source_refs ?? []).some(source => source.url === ref.url && source.content_sha256 === ref.raw_sha256)) {
    throw new Error('Official prompt evidence is not bound to the selected notice snapshot.');
  }
  if (!text.includes(task.question_text)) throw new Error('Official prompt wording is absent from the preserved text.');
  const span = /^L(\d+)(?:-L?(\d+))?$/.exec(ref.locator ?? '');
  const start = span ? Number(span[1]) : 0, end = span ? Number(span[2] ?? span[1]) : 0;
  if (!span || start < 1 || end < start || !text.split('\n').slice(start - 1, end).join('\n').includes(task.question_text)) {
    throw new Error('Official prompt wording is absent from the declared source line span.');
  }
  return {task, verification: {raw_sha256_verified: true, text_sha256_verified: true,
    wording_found_in_preserved_text: true, locator_span_verified: true, current_official_page_rechecked: false,
    source_ref: ref}};
}

export async function runPilot(snapshot, profile) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(snapshot.as_of ?? '') || snapshot.as_of !== profile.as_of) {
    throw new Error('A common fixed historical as_of date is required.');
  }
  if (!Array.isArray(snapshot.opportunities) || snapshot.opportunities.length !== 10
      || new Set(snapshot.opportunities.map(item => item.opportunity_id)).size !== 10) {
    throw new Error('Expected ten distinct stored demo opportunities.');
  }
  const [eligibility, assistance, profiles, drafts] = await Promise.all([
    localTs('eligibility.ts'), localTs('profile-assistance.ts'),
    localTs('browser-profile.ts'), localTs('draft.ts')]);
  const facts = confirmedFacts(profile);
  validateCanonicalProfile(profile.canonical_profile);
  const current = webProfile(profile.canonical_profile, profiles.createEmptyProfile, profiles.normalizeProfile);
  const now = new Date(snapshot.as_of + 'T12:00:00+09:00');
  const records = snapshot.opportunities.map(item => {
    const basic = evaluateEligibility(item.eligibility, profile.canonical_profile);
    const additional = evaluateAdditional(item.additional_checks ?? [],
      profile.condition_answers?.[item.opportunity_id] ?? {}, profile.canonical_profile);
    const unsupported = [];
    const criteria = webCondition(item.eligibility, item, unsupported);
    const webItem = {id: item.opportunity_id, name: item.title, organization: item.issuer?.name ?? '',
      sourceUrl: item.application?.url, sourceUpdatedAt: snapshot.as_of,
      category: item.category, support: '', rules: [], criteria,
      lifecycleStatus: 'unknown', matchMode: criteria ? 'structured' : 'partial',
      unresolvedCriteria: ['historical snapshot; source and additional checks require review']};
    const match = eligibility.evaluateOpportunity(current, webItem, now);
    const questions = assistance.questionsForMatch(current, match);
    return {opportunity_id: item.opportunity_id, program_id: item.program_id,
      round_id: item.round_id ?? null, title: item.title, execution_scope: 'historical_demo',
      decision: basic.result === true ? 'basic_conditions_match' : basic.result === false ? 'basic_conditions_mismatch' : 'needs_info',
      eligibility: basic, additional_checks: additional,
      snapshot_application_state: applicationState(item.application ?? {}, snapshot.as_of),
      current_application_status: 'unknown_not_reverified', current_status_verified: false,
      next_questions: (basic.result === false ? [] : additional).filter(check => ['unknown', 'invalid', 'exception'].includes(check.result))
        .map(check => ({check_id: check.check_id, question: check.question, at: check.at,
          evidence_ref: check.evidence_ref, reason: check.result})),
      question_scope: basic.result === false ? 'not_requested_for_basic_mismatch' : 'unresolved_additional_checks',
      web_profile_questions: questions,
      web_bridge: {eligibility_result: match.eligibilityResult, adapter_unrepresented: unsupported,
        canonical_engine_is_authoritative_for_this_snapshot: true,
        basic_result_agrees: match.eligibilityResult === (basic.result === true ? 'pass' : basic.result === false ? 'fail' : 'unknown')},
      all_conditions_verified: false, production_eligible: false,
      source_refs: item.source_refs ?? [], source_version_hash: item.version_hash ?? null};
  });
  const selected = snapshot.opportunities.find(item => item.opportunity_id === profile.selected_opportunity_id);
  if (!selected) throw new Error('Selected opportunity is absent.');
  const {task, verification} = checkedPrompt(profile, selected);
  const record = {record_id: selected.opportunity_id + ':' + selected.round_id,
    notice: {notice_id: selected.opportunity_id, title: selected.title},
    round: {round_id: selected.round_id, label: selected.title, academic_path: 'historical_synthetic_pilot'},
    questions: [{id: task.task_id, title: task.genre, text: task.question_text,
      wording_status: task.wording_status, evidence_refs: [task.source_ref.url],
      length_limit: {status: 'known', unit: 'pages', whitespace_included: 'unknown', min: null,
        max: task.constraints.max_pages, exact: null, evidence_refs: [task.source_ref.url]},
      fact_slots: [{id: 'confirmed_prior_work', label: '합성 확인 사실의 과거 연구 재료', placeholder: '확인된 본인 경험 입력'},
                   {id: 'future_plan', label: '향후 연구 계획', placeholder: '본인이 확인할 향후 연구 계획 입력'}]}],
    ai_writing_policy: 'unknown', policy_source: [], official_forms: []};
  const officialQuestions = drafts.getDraftQuestions(record);
  const slotFields = task.fact_slot_fields ?? {};
  const usedFactIds = new Set();
  const supplied = officialQuestions[0].fact_slots.flatMap(slot => {
    const fields = slotFields[slot.id];
    if (!Array.isArray(fields)) throw new Error('Each draft slot needs an explicit fact-field selection.');
    const matches = facts.accepted.filter(fact => fields.includes(fact.field));
    matches.forEach(fact => usedFactIds.add(fact.fact_id));
    return matches.length ? [{question_id: officialQuestions[0].id, slot_id: slot.id,
      text: matches.map(fact => fact.value).join('\n')}] : [];
  });
  const preparation = drafts.buildDraft(record, supplied);
  return {contract_version: 'team-execution-personalization-v1', execution_scope: 'historical_synthetic_pilot',
    example_kind: 'synthetic', as_of: snapshot.as_of, current_status_verified: false,
    network_requests: 0, model_calls: 0, database_writes: 0, records,
    profile_trace: {subject_id: profile.subject_id, source_kind: 'explicit_synthetic_fixture',
      coursework_from_input: profile.canonical_profile['education.coursework_completed'],
      coursework_inferred_from_gpa: false, accepted_fact_ids: facts.accepted.map(fact => fact.fact_id),
      withheld_facts: facts.withheld},
    selected_writing_record: record, official_prompt_verification: verification,
    ready_draft: {kind: 'deterministic_preparation_draft', scope: 'historical_synthetic_preparation',
      status: preparation.sections.some(section => section.missing_slots.length)
        ? 'needs_confirmed_facts' : 'preparation_text_populated_not_verified_application',
      draft: preparation,
      unused_confirmed_fact_ids: facts.accepted.filter(fact => !usedFactIds.has(fact.fact_id)).map(fact => fact.fact_id),
      llm_generated: false, user_application_submitted: false},
    bridge: {task_spec: {task_id: task.task_id, subject_id: profile.subject_id, experience_id: profile.experience_id,
      opportunity_id: selected.opportunity_id, program_id: selected.program_id, round_id: selected.round_id,
      question_text: officialQuestions[0].text, genre: task.genre, constraints: task.constraints,
      source_ref: task.source_ref}, confirmed_facts: facts.accepted,
      template_id: task.template_id, template_rationale: task.template_rationale,
      style_profile: profile.style_profile},
    limitations: ['Basic conditions and unanswered additional checks remain separate.',
      'Source text is a preserved historical snapshot, not a current recruitment check.',
      'Preparation text is supplied synthetic material; no applicant outcome is predicted.']};
}

async function cli() {
  const options = {};
  for (let i = 2; i < process.argv.length; i += 2) {
    if (!process.argv[i].startsWith('--') || !process.argv[i + 1]) throw new Error('Expected --option value pairs.');
    options[process.argv[i].slice(2)] = process.argv[i + 1];
  }
  const input = resolve(options.opportunities ?? resolve(ROOT, 'data/sample/programs.json'));
  const profilePath = resolve(options.profile ?? resolve(ROOT, 'evaluation/spec_examples/team-execution-profile.synthetic.json'));
  const output = resolve(options.output ?? CACHE + '/team-execution-20261119/personalization/result.json');
  const actualOutput = resolvedOutputPath(output), actualCache = realpathSync(CACHE);
  if (relative(actualCache, actualOutput).startsWith('..')) throw new Error('Store pilot outputs under the shared dataset cache.');
  const bytes = readFileSync(input), profile = readJson(profilePath), inputSha = sha256(bytes);
  if (!(profile.allowed_input_sha256 ?? [profile.input_snapshot_sha256]).includes(inputSha)) {
    throw new Error('Input snapshot is not pinned by the synthetic fixture.');
  }
  const result = await runPilot(JSON.parse(bytes), profile);
  const samplePath = resolve(ROOT, 'data/sample/programs.json');
  const manifest = readJson(resolve(ROOT, 'data/sample/manifest.json'));
  result.input_integrity = {path: input, sha256: inputSha, pinned_profile_match: true,
    profile_path: profilePath, profile_sha256: sha256(readFileSync(profilePath)),
    public_sample_manifest_sha256_matches: sha256(readFileSync(samplePath)) === manifest.sha256,
    execution_input_is_public_sample: input === samplePath};
  result.code_fingerprints = Object.fromEntries(['web/pipeline-demo/match.mjs', 'web/lib/eligibility.ts',
    'web/lib/profile-assistance.ts', 'web/lib/browser-profile.ts', 'web/lib/draft.ts',
    'web/scripts/run-team-execution-pilot.mjs'].map(name => [name, sha256(readFileSync(resolve(ROOT, name)))]));
  mkdirSync(dirname(output), {recursive: true});
  writeFileSync(output + '.tmp', JSON.stringify(result, null, 2) + '\n');
  renameSync(output + '.tmp', output);
  console.log(JSON.stringify({output, records: result.records.length, as_of: result.as_of,
    basic_decisions: result.records.reduce((counts, item) => ({...counts, [item.decision]: (counts[item.decision] ?? 0) + 1}), {}),
    network_requests: 0, model_calls: 0, database_writes: 0}));
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  cli().catch(error => {console.error(error.message); process.exitCode = 1;});
}
