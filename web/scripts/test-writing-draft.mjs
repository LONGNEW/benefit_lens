import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const require = createRequire(import.meta.url)
const tsPath = process.env.BENEFITLENS_TYPESCRIPT || 'typescript'
const ts = require(tsPath)
const sourcePath = process.env.BENEFITLENS_DRAFT_MODULE || (fs.existsSync(path.join(here, 'draft.ts')) ? path.join(here, 'draft.ts') : path.join(here, '../lib/draft.ts'))
const fixtureRoot = process.env.BENEFITLENS_WRITING_FIXTURE || (fs.existsSync(path.join(here, 'writing-resources.json')) ? here : path.join(here, '../../pipeline/tests/gold/academic_language/2026-10-04/writing_resources'))
const code = fs.readFileSync(sourcePath, 'utf8')
const output = ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { buildDraft, factsFromFreeText, measureDraftLength } = await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`)
const data = JSON.parse(fs.readFileSync(path.join(fixtureRoot, 'writing-resources.json'), 'utf8'))
const mirae = data.records.find(r => r.record_id === 'miraeasset-35-2026-s2')
const cmk = data.records.find(r => r.record_id === 'cmk-2025-graduate')
const kiwoom = data.records.find(r => r.record_id === 'kiwoom-2026-s2')

for (const record of data.records) {
  test(`always returns a draft with no facts: ${record.record_id}`, () => {
    const result = buildDraft(record)
    assert.ok(result.sections.length > 0)
    assert.ok(result.sections.every(s => s.text.includes('[') && s.missing_slots.length > 0))
    assert.equal(result.is_submission, false)
  })
}
test('verified Mirae questions have three source-linked sections and 800-character limits', () => {
  assert.equal(mirae.questions.length, 3)
  assert.ok(mirae.questions.every(q => q.length_limit.max === 800 && q.length_limit.whitespace_included === 'yes' && q.evidence_refs.length > 0))
  assert.equal(buildDraft(mirae).label, '공식 문항 준비 초안')
})
test('AI penalty keeps a draft and edit option without turning into a prohibition', () => {
  const result = buildDraft(mirae)
  assert.equal(result.policy, 'penalty_possible')
  assert.equal(result.edit_option.label, '내 표현으로 수정')
  assert.ok(result.cautions.some(c => c.kind === 'ai_policy' && c.message.includes('감점')))
  assert.ok(!result.cautions.some(c => c.message.includes('금지합니다')))
})
test('provided fact text is preserved verbatim', () => {
  const fact = '  가상 메모: 수업 자료 두 편을 비교했다.\n이 문장도 내가 입력했다.  '
  const result = buildDraft(mirae, [{ question_id: 'mirae-q1', slot_id: 'preparation', text: fact }])
  assert.ok(result.sections[0].text.includes(fact))
  assert.equal(result.sections[0].provided_fact_count, 1)
  assert.equal(result.factual_claims_added, false)
})
test('missing experiences remain placeholders', () => {
  const result = buildDraft(mirae, [{ question_id: 'mirae-q1', slot_id: 'motivation', text: '가상 메모' }])
  assert.deepEqual(result.sections[0].missing_slots, ['preparation'])
  assert.ok(result.sections[0].text.includes('[직접 준비한 과정과 확인 가능한 행동 입력]'))
})
test('unknown slot is surfaced as unused and never transplanted to a different question', () => {
  const fact = { question_id: 'mirae-q1', slot_id: 'not-a-slot', text: '대체할 수 없는 가상 입력' }
  const result = buildDraft(mirae, [fact])
  assert.deepEqual(result.unused_facts, [fact])
  assert.ok(result.sections.every(s => !s.text.includes(fact.text)))
})
test('multiple supplied facts in a slot are retained in their original order', () => {
  const result = buildDraft(mirae, ['첫 가상 메모', '둘째 가상 메모'].map(text => ({ question_id: 'mirae-q1', slot_id: 'preparation', text })))
  assert.ok(result.sections[0].text.includes('첫 가상 메모\n둘째 가상 메모'))
})
test('unverified CMK prompts remain general, with unknown length', () => {
  const result = buildDraft(cmk)
  assert.equal(result.label, '일반 준비 초안')
  assert.ok(result.sections.every(s => !s.is_official_question && s.length_status === 'unknown'))
  assert.equal(cmk.questions.length, 0)
})
test('Kiwoom generates a preparation memo rather than fictional official self-essay questions', () => {
  const result = buildDraft(kiwoom)
  assert.equal(result.label, '일반 준비 초안')
  assert.equal(result.sections[0].title, '공고 확인 메모')
  assert.ok(result.cautions.some(c => c.kind === 'general_preparation' && c.message.includes('일반 준비 메모')))
})
test('free memo helper preserves facts and does not invent slot classification', () => {
  const text = '가상 준비 메모\n  정확히 유지할 문장'
  const result = buildDraft(cmk, factsFromFreeText(cmk, text))
  assert.ok(result.sections[0].text.includes(text))
  assert.equal(result.sections[0].provided_fact_count, 1)
  assert.ok(result.sections.slice(1).every(s => s.provided_fact_count === 0))
})
test('blank free memo is missing rather than a factual claim', () => {
  assert.deepEqual(factsFromFreeText(cmk, ' \n\t'), [])
})
test('whitespace is included in the known Mirae character count', () => {
  const measured = measureDraftLength('가 나', mirae.questions[0].length_limit)
  assert.equal(measured.character_count, 3)
})
test('known length has correct 800/801 boundary and never deletes text', () => {
  assert.equal(measureDraftLength('가'.repeat(800), mirae.questions[0].length_limit).length_status, 'within_limit')
  assert.equal(measureDraftLength('가'.repeat(801), mirae.questions[0].length_limit).length_status, 'over_limit')
  const long = '가'.repeat(850)
  const result = buildDraft(mirae, [{ question_id: 'mirae-q1', slot_id: 'motivation', text: long }])
  assert.ok(result.sections[0].text.includes(long))
  assert.equal(result.sections[0].length_status, 'over_limit')
})
test('unknown whitespace counting is not declared within the limit', () => {
  assert.equal(measureDraftLength('짧다', { ...mirae.questions[0].length_limit, whitespace_included: 'unknown' }).length_status, 'unknown')
})
test('unsupported byte limits are not silently treated as character limits', () => {
  assert.equal(measureDraftLength('한글', { ...mirae.questions[0].length_limit, unit: 'bytes' }).length_status, 'unknown')
})
test('Unicode code point count is explicitly a demo estimate', () => {
  const measured = measureDraftLength('가😀', mirae.questions[0].length_limit)
  assert.equal(measured.character_count, 2)
  assert.equal(measured.length_result_scope, 'demo_estimate')
})
test('unknown AI policy is not relabeled as permitted', () => {
  const result = buildDraft(cmk)
  assert.equal(result.policy, 'unknown')
  assert.ok(result.cautions.some(c => c.kind === 'ai_policy_unknown'))
})
test('an unsupported verified question without evidence falls back to general preparation', () => {
  const changed = structuredClone(cmk)
  changed.questions = [{ ...mirae.questions[0], evidence_refs: [] }]
  assert.equal(buildDraft(changed).label, '일반 준비 초안')
})
test('Mirae policy and school/language/overflow notes retain evidence', () => {
  const cautions = buildDraft(mirae).cautions
  for (const kind of ['ai_policy', 'school_identification', 'language', 'length']) assert.ok(cautions.some(c => c.kind === kind && c.source_refs.length > 0))
})
test('source PDF hash matches stored original bytes', () => {
  const source = mirae.official_sources.find(s => s.id === 'mirae-35-guide')
  assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(fixtureRoot, source.local_file))).digest('hex'), source.sha256)
})
test('unofficial references are context only and do not retain complete essays', () => {
  for (const record of data.records) {
    assert.equal(record.automatic_eligibility_use, false)
    assert.equal(record.collection_policy.full_self_essay_storage, 'not_retained')
    for (const source of record.unofficial_sources) {
      assert.equal(source.capture_mode, 'metadata_only')
      assert.equal(source.automatic_eligibility_evidence, false)
      assert.ok(!Object.hasOwn(source, 'full_text'))
      assert.ok(!Object.hasOwn(source, 'accepted_essay'))
    }
  }
})
test('building a draft does not mutate supplied records or facts', () => {
  const before = JSON.stringify(mirae)
  const facts = [{ question_id: 'mirae-q1', slot_id: 'motivation', text: '가상 입력' }]
  const factBefore = JSON.stringify(facts)
  buildDraft(mirae, facts)
  assert.equal(JSON.stringify(mirae), before)
  assert.equal(JSON.stringify(facts), factBefore)
})
