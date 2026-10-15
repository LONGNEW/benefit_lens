import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
const require = createRequire(import.meta.url);
const ts = require('typescript');
function load(path) {
  const exports = {};
  new Function('exports', 'require', ts.transpile(readFileSync(new URL(path, import.meta.url), 'utf8'), {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}))(exports, require);
  return exports;
}
const {writingRecordForOpportunity} = load('../lib/opportunity-writing.ts');
const {buildDraft, getDraftQuestions, measureDraftLength} = load('../lib/draft.ts');
const prompt = {id: 'p', opportunityVersionId: 'v1', promptKey: 'prompt-1', text: '지원 동기를 800자 이내로 기술하세요.', characterLimit: 800, sourceUrl: 'https://official.test/form', reviewStatus: 'reviewed'};
const opportunity = {id: 'o', opportunityVersionId: 'v1', name: '선택 공고', applicationPrompts: [prompt]};
const known = writingRecordForOpportunity(opportunity);
assert.equal(getDraftQuestions(known)[0].text, prompt.text);
assert.equal(buildDraft(known, []).label, '공식 문항 준비 초안');
assert.equal(measureDraftLength('x'.repeat(801), known.questions[0].length_limit).length_status, 'unknown');
for (const changed of [{opportunityVersionId: 'v2'}, {reviewStatus: 'needs_review'}, {sourceUrl: ''}]) {
  const record = writingRecordForOpportunity({...opportunity, applicationPrompts: [{...prompt, ...changed}]});
  assert.equal(record.questions.length, 0);
  assert.equal(buildDraft(record, []).label, '일반 준비 초안');
}
const general = writingRecordForOpportunity({...opportunity, applicationPrompts: []});
const question = getDraftQuestions(general)[0];
const draft = buildDraft(general, [{question_id: question.id, slot_id: question.fact_slots[0].id, text: '내가 입력한 실제 경험'}]);
assert.ok(draft.sections[0].text.includes('내가 입력한 실제 경험'));
assert.equal(draft.factual_claims_added, false);
console.log('7 opportunity writing contract checks passed');
