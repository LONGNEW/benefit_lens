import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
const require = createRequire(import.meta.url);
const ts = require('typescript');
function moduleFrom(path, overrides = {}) {
  const code = ts.transpile(readFileSync(new URL(path, import.meta.url), 'utf8'), {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX});
  const exports = {};
  new Function('exports', 'require', code)(exports, name => overrides[name] ?? require(name));
  return exports;
}
const resources = JSON.parse(readFileSync(new URL('../lib/writing-resources.json', import.meta.url), 'utf8'));
const packages = moduleFrom('../lib/application-package.ts', {'./writing-resources.json': {default: resources}});
const {getApplicationExample, APPLICATION_EXAMPLE_ID, referenceLinks, referenceHref, strategyForQuestion, hasVerifiedOfficialReferences} = packages;
const draft = moduleFrom('../lib/draft.ts');
const {buildDraft} = draft;
const panel = moduleFrom('../app/benefitlens/application-reference-panel.tsx', {'@/lib/application-package': packages});
const Workbench = moduleFrom('../app/benefitlens/draft-workbench.tsx', {'@/lib/application-package': packages, '@/lib/draft': draft, '@/lib/opportunity-writing': moduleFrom('../lib/opportunity-writing.ts'), './application-reference-panel': panel}).default;
const {createElement} = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
const pack = getApplicationExample(APPLICATION_EXAMPLE_ID);
const html = renderToStaticMarkup(createElement(Workbench, {opportunity: null, example: pack}));
let count = 0;
function test(name, run) {run(); count++; console.log(`PASS ${name}`);}

test('only explicit historical example selection resolves the curated round', () => {
  assert.equal(pack.record.record_id, APPLICATION_EXAMPLE_ID);
  for (const id of ['', 'miraeasset', 'miraeasset-36-2026-s2', 'candidate-id']) assert.equal(getApplicationExample(id), null);
});
test('historical undergraduate example is closed and never evaluates the owner profile', () => {
  assert.equal(pack.mode, 'historical_example'); assert.equal(pack.lifecycle, 'closed'); assert.equal(pack.qualification, 'not_evaluated');
  assert.equal(pack.record.round.academic_path, 'undergraduate_exchange'); assert.equal(pack.applicationUrl, null);
  assert.match(html, /접수 종료/); assert.match(html, /현재 박사과정의 지원 후보와 분리/); assert.doesNotMatch(html, /접수 가능|DGIST|1999년생/);
});
test('three common and three conditional documents retain scoped official PDF evidence', () => {
  assert.equal(pack.documents.length, 3); assert.equal(pack.optionalDocuments.length, 3);
  for (const item of [...pack.documents, ...pack.optionalDocuments]) {assert.equal(item.evidence.page, 7); assert.equal(item.evidence.url, pack.fileEvidence.url);}
  assert.equal(pack.fileChecks.length, 5); assert.match(html, /파일 암호 해제/); assert.match(html, /발급기관 직인/);
  assert.deepEqual(pack.referenceReview.pages, [2, 3, 4, 7, 8, 11]);
  assert.equal(pack.referenceReview.sha256, resources.records[0].official_sources[1].sha256);
  assert.deepEqual(resources.records[0].official_sources[1].pages_reviewed, [11]);
  assert.equal(pack.periodEvidence.url, pack.officialNotice);
});
test('interview and post-selection duties are separate from application document requirements', () => {
  assert.deepEqual(pack.stages.map(stage => stage.title), ['온라인 지원', '서류 합격 후', '최종 선발 후']);
  assert.match(html, /대상자에게 안내된 온라인 영상 면접/);
  assert.doesNotMatch(pack.documents.map(item => item.title).join(','), /면접|수여식|과제/);
});
test('previous-round changes have their own evidence and do not replace current writing questions', () => {
  assert.equal(pack.history.evidence.page, 2); assert.match(html, /제34기와 제35기 비교/);
  assert.equal(pack.record.round.round_id, '35-2026-fall'); assert.equal(pack.record.questions.length, 3);
});
test('official evidence ids resolve to working page-specific links, unknown ids are not URLs', () => {
  const links = referenceLinks(pack, ['mirae-writing-page11', 'missing-id']);
  assert.equal(links.length, 1); assert.equal(links[0].page, 11); assert.match(referenceHref(links[0]), /#page=11$/);
  assert.match(html, /href="https:\/\/www.hansung.ac.kr\/bbs\/hansung\/2127\/60765\/download.do#page=11"/);
  assert.equal(referenceLinks(null, ['missing-id']).length, 0);
});
test('three question-specific strategies require the same recorded round and source', () => {
  for (const question of pack.record.questions) assert.ok(strategyForQuestion(pack, question.id));
  assert.equal(strategyForQuestion(pack, 'unknown-question'), null);
  const other = structuredClone(pack); other.success.round = '제34기';
  assert.equal(strategyForQuestion(other, 'mirae-q1'), null);
  other.success.round = pack.success.round; other.record.round.round_id = '34-2026-spring';
  assert.equal(strategyForQuestion(other, 'mirae-q1'), null);
});
test('story metadata must exist and confirm matching round and context-only scope', () => {
  for (const scope of ['different_round', 'unknown']) {
    const other = structuredClone(pack); other.sources.unofficial[0].scope_alignment = scope;
    assert.equal(strategyForQuestion(other, 'mirae-q1'), null);
  }
  const missing = structuredClone(pack); missing.sources.unofficial = [];
  assert.equal(strategyForQuestion(missing, 'mirae-q1'), null);
});
test('every formal question, length and policy reference belongs to an official source in this round', () => {
  assert.equal(hasVerifiedOfficialReferences(pack), true);
  const missing = structuredClone(pack); missing.evidence = [];
  assert.equal(hasVerifiedOfficialReferences(missing), false);
  const otherRound = structuredClone(pack); otherRound.evidence[0].round = '34-2026-spring';
  assert.equal(hasVerifiedOfficialReferences(otherRound), false);
  const wrongPolicy = structuredClone(pack); wrongPolicy.record.policy_source[0].url = pack.success.url;
  assert.equal(hasVerifiedOfficialReferences(wrongPolicy), false);
});
test('self-reported success does not become verified selection evidence or reuse permission', () => {
  assert.equal(pack.success.verification, 'self_reported'); assert.equal(pack.success.reusePermission, 'unknown');
  assert.match(html, /최종합격 자기보고/); assert.match(html, /재사용 허가 미확인/);
  assert.equal('fullEssay' in pack.success, false); assert.equal('gpa' in pack.success, false);
});
test('strategies and another person context never enter user draft text', () => {
  const copied = structuredClone(pack);
  copied.strategies[0].summary = 'STRATEGY_NOT_USER_FACT'; copied.success.context = ['CONTEXT_NOT_USER_FACT'];
  const made = buildDraft(copied.record, [{question_id: 'mirae-q1', slot_id: 'motivation', text: '내가 직접 수행한 경험'}]);
  assert.ok(made.sections[0].text.includes('내가 직접 수행한 경험'));
  assert.doesNotMatch(made.sections.map(section => section.text).join('\n'), /STRATEGY_NOT_USER_FACT|CONTEXT_NOT_USER_FACT|스페인|마케팅/);
  assert.equal(made.factual_claims_added, false);
});
test('UI presents policy, known character counting and original evidence before generating', () => {
  assert.match(html, /이 회차의 작성 정책/); assert.match(html, /감점할 수/); assert.match(html, /공백 포함/);
  assert.doesNotMatch(html, /공백 계산 방식 미확인|\u00b7/);
  assert.match(html, /후기에서 참고할 작성 방향/); assert.match(html, /로컬 문항별 정리 템플릿, LLM 생성 아님/);
});
test('document checkboxes are explicit temporary preparation notes, not file uploads or submission', () => {
  assert.equal((html.match(/type="checkbox"/g) ?? []).length, 6);
  assert.doesNotMatch(html, /type="file"|checked=""|신청 사이트|자동 제출/);
  assert.match(html, /이번 화면의 준비 메모/);
});
test('a normal opportunity cannot silently inherit historical documents or questions', () => {
  const normal = {id: 'normal', name: '실제 공고', organization: '운영 기관', sourceUrl: 'https://official.test/current', applicationPrompts: [], requiredDocuments: ['현재 공고의 제출서류']};
  const actual = renderToStaticMarkup(createElement(Workbench, {opportunity: normal, example: pack}));
  assert.match(actual, /일반 준비용/); assert.match(actual, /현재 공고의 제출서류/);
  assert.doesNotMatch(actual, /제35기|합격 후기 원문|스페인|국문 성적증명서|후기에서 참고할 작성 방향/);
});
console.log(`${count} connected application checks passed (logic and SSR, not browser click QA)`);
