import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

const here = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const outIndex = args.indexOf('--out');
if (args.length && (outIndex !== 0 || args.length !== 2)) throw new Error('Usage: node test-draft-edit-state.mjs [--out PATH]');
const require = createRequire(import.meta.url);
const ts = require(process.env.BENEFITLENS_TYPESCRIPT || 'typescript');
const editPath = process.env.BENEFITLENS_EDIT_MODULE || path.join(here, '../lib/draft-edit-state.ts');
const draftPath = process.env.BENEFITLENS_DRAFT_MODULE || path.join(here, '../lib/draft.ts');
const resourcePath = process.env.BENEFITLENS_WRITING_DATA || path.join(here, '../lib/writing-resources.json');
const sha = file => createHash('sha256').update(readFileSync(file)).digest('hex');
async function loadModule(file) {
  const output = ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);
}
const { initialDraftEditState, draftEditReducer: reduce } = await loadModule(editPath);
const { buildDraft } = await loadModule(draftPath);
const data = JSON.parse(readFileSync(resourcePath, 'utf8'));
const mirae = buildDraft(data.records.find(item => item.record_id === 'miraeasset-35-2026-s2'), []);
const cmk = buildDraft(data.records.find(item => item.record_id === 'cmk-2025-graduate'), []);
const id = mirae.sections[0].question_id;
const report = { suite: 'Actual manual-edit state reducer, using actual draft-generator fixture sections; not browser click QA', tested_at: new Date().toISOString(), module_sha256: sha(editPath), draft_module_sha256: sha(draftPath), resource_sha256: sha(resourcePath), tests: [] };
function check(name, fn) {
  try { fn(); report.tests.push({ name, passed: true }); }
  catch (error) { report.tests.push({ name, passed: false, error: error.message }); }
}
const start = () => reduce(initialDraftEditState(), { type: 'enter', sections: mirae.sections });
check('Initial edit entry copies all actual draft sections', () => {
  const state = start();
  assert.equal(state.editing, true);
  assert.deepEqual(state.texts, Object.fromEntries(mirae.sections.map(section => [section.question_id, section.text])));
});
check('Editing then clicking enter again preserves every user edit', () => {
  let state = start();
  state = reduce(state, { type: 'edit', questionId: id, text: '직접 고친 검증용 문장' });
  const before = state;
  state = reduce(state, { type: 'enter', sections: mirae.sections });
  assert.strictEqual(state, before);
  assert.equal(state.texts[id], '직접 고친 검증용 문장');
});
check('Editing one question preserves previously edited text in other questions', () => {
  const secondId = mirae.sections[1].question_id;
  let state = reduce(start(), { type: 'edit', questionId: secondId, text: '두 번째 문항 수정' });
  state = reduce(state, { type: 'edit', questionId: id, text: '첫 번째 문항 수정' });
  state = reduce(state, { type: 'enter', sections: mirae.sections });
  assert.equal(state.texts[secondId], '두 번째 문항 수정');
  assert.equal(state.texts[id], '첫 번째 문항 수정');
});
check('Duplicate entry actions in one queue are idempotent before and after editing', () => {
  let state = initialDraftEditState();
  for (let index = 0; index < 2; index++) state = reduce(state, { type: 'enter', sections: mirae.sections });
  state = reduce(state, { type: 'edit', questionId: id, text: '중복 클릭 뒤에도 남을 문장' });
  const before = state;
  for (let index = 0; index < 2; index++) state = reduce(state, { type: 'enter', sections: mirae.sections });
  assert.strictEqual(state, before);
  assert.equal(state.texts[id], '중복 클릭 뒤에도 남을 문장');
});
check('Intentionally empty edited text stays empty through repeated entry', () => {
  let state = reduce(start(), { type: 'edit', questionId: id, text: '' });
  state = reduce(state, { type: 'enter', sections: mirae.sections });
  state = reduce(state, { type: 'enter', sections: mirae.sections });
  assert.ok(Object.hasOwn(state.texts, id));
  assert.equal(state.texts[id], '');
});
check('Whitespace-only edits are retained exactly', () => {
  const before = reduce(start(), { type: 'edit', questionId: id, text: ' \n\t ' });
  const after = reduce(before, { type: 'enter', sections: mirae.sections });
  assert.equal(after.texts[id], ' \n\t ');
});
check('Re-entry ignores a changed section payload rather than overwriting editing text', () => {
  const before = reduce(start(), { type: 'edit', questionId: id, text: '이미 수정한 내용' });
  const changed = mirae.sections.map(section => ({ ...section, text: '다른 초안 내용' }));
  assert.strictEqual(reduce(before, { type: 'enter', sections: changed }), before);
});
check('Only the explicit restore action replaces manual text with current draft text', () => {
  const manual = reduce(start(), { type: 'edit', questionId: id, text: '복원 전 수동 문장' });
  assert.equal(reduce(manual, { type: 'enter', sections: mirae.sections }).texts[id], '복원 전 수동 문장');
  const restored = reduce(manual, { type: 'restore', sections: mirae.sections });
  assert.equal(restored.editing, true);
  assert.deepEqual(restored.texts, Object.fromEntries(mirae.sections.map(section => [section.question_id, section.text])));
});
check('Explicit restore uses the latest draft and preserves its empty text without old keys', () => {
  const manual = reduce(start(), { type: 'edit', questionId: id, text: '수동 수정' });
  const latest = [{ question_id: id, text: '' }, { question_id: 'latest-only', text: '최신 초안 문장' }];
  const restored = reduce(manual, { type: 'restore', sections: latest });
  assert.deepEqual(restored, { editing: true, texts: { [id]: '', 'latest-only': '최신 초안 문장' } });
});
check('Notice switching resets the old record and enters only the newly selected notice draft', () => {
  const manual = reduce(start(), { type: 'edit', questionId: id, text: '이전 공고의 편집문장' });
  const switched = reduce(manual, { type: 'reset' });
  assert.deepEqual(switched, { editing: false, texts: {} });
  const entered = reduce(switched, { type: 'enter', sections: cmk.sections });
  assert.deepEqual(entered.texts, Object.fromEntries(cmk.sections.map(section => [section.question_id, section.text])));
  assert.ok(!Object.values(entered.texts).includes('이전 공고의 편집문장'));
});
check('Notice reset prevents text carryover even when the next record reuses a question ID', () => {
  const manual = reduce(start(), { type: 'edit', questionId: id, text: '다른 공고에 옮기면 안 되는 문장' });
  const nextSections = [{ question_id: id, text: '새 공고의 빈칸 초안' }];
  const entered = reduce(reduce(manual, { type: 'reset' }), { type: 'enter', sections: nextSections });
  assert.equal(entered.texts[id], '새 공고의 빈칸 초안');
});
check('Explicit new-draft context reset returns to a clean non-editing state', () => {
  const manual = reduce(start(), { type: 'edit', questionId: id, text: '이전 생성본 편집' });
  assert.deepEqual(reduce(manual, { type: 'reset' }), initialDraftEditState());
});
check('Edits outside active mode and unknown question IDs do not alter current text', () => {
  const initial = initialDraftEditState();
  assert.strictEqual(reduce(initial, { type: 'edit', questionId: id, text: '적용되면 안 됨' }), initial);
  const editing = start();
  assert.strictEqual(reduce(editing, { type: 'edit', questionId: 'other-record-question', text: '혼입 금지' }), editing);
});
check('Reducer never mutates prior state or input draft section objects', () => {
  const sections = Object.freeze(mirae.sections.map(section => Object.freeze({ question_id: section.question_id, text: section.text })));
  const initial = Object.freeze({ editing: false, texts: Object.freeze({}) });
  const entered = reduce(initial, { type: 'enter', sections });
  const before = structuredClone(entered);
  const edited = reduce(entered, { type: 'edit', questionId: id, text: '독립 상태' });
  reduce(edited, { type: 'restore', sections });
  assert.deepEqual(initial, { editing: false, texts: {} });
  assert.deepEqual(entered, before);
  assert.deepEqual(sections, mirae.sections.map(section => ({ question_id: section.question_id, text: section.text })));
});
report.count = report.tests.length;
report.passed = report.tests.filter(test => test.passed).length;
report.failed = report.count - report.passed;
report.all_passed = report.failed === 0;
if (outIndex !== -1) writeFileSync(args[outIndex + 1], JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
if (!report.all_passed) process.exitCode = 1;
