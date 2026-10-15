#!/usr/bin/env node
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { SCHEMA_VERSION, SECTIONS, loadProductionComparator, combine, evaluateAST, evaluateTypedRule, applyFactConstraints, validateFixture, runFixture } from './verified-demo-runner.mjs';
import { summarizeReport } from './summarize-integration-report.mjs';

// All content in this suite is invented. It tests mechanics, not a real notice.
const project = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const comparator = loadProductionComparator(project);
const originalEngineBytes = readFileSync(comparator.sourceFile);
const testResults = [];
function test(name, body) { body(); testResults.push({ name, passed: true }); console.log(`PASS ${name}`); }
const leaf = (id, field, operator, value, value_type = 'integer', extra = {}) => ({ kind: 'rule', id, field, operator, value, value_type, role: 'required', evidence_ids: ['invented-evidence'], ...extra });
const group = (id, operator, children) => ({ kind: 'group', id, operator, children });
const band = leaf('band', 'income_band', 'between', [1, 7]);
const grade = leaf('grade', 'cumulative_gpa', 'gte', 3, 'number', { engine_field: 'gpa', criterion_code: 'gpa', unit: 'gpa_4.5', qualifiers: { scale: 4.5, basis: 'cumulative', period: '2025-2' } });
const fact = (value, extra = {}) => ({ value, ...extra });

test('A genuine zero band fails the inclusive 1–7 range; absence remains unknown', () => {
  assert.equal(evaluateTypedRule({ income_band: 0 }, band, comparator).result, 'fail');
  assert.equal(evaluateTypedRule({ income_band: 1 }, band, comparator).result, 'pass');
  assert.equal(evaluateTypedRule({ income_band: 7 }, band, comparator).result, 'pass');
  assert.equal(evaluateTypedRule({ income_band: 8 }, band, comparator).result, 'fail');
  assert.equal(evaluateTypedRule({}, band, comparator).result, 'unknown');
});
test('Numbers and booleans are never accepted from string coercion', () => {
  assert.equal(evaluateTypedRule({ income_band: '1' }, band, comparator).result, 'unknown');
  const duplicate = leaf('duplicate', 'same_term_duplicate', 'eq', false, 'boolean', { role: 'duplicate' });
  assert.equal(evaluateTypedRule({ same_term_duplicate: 'false' }, duplicate, comparator).result, 'unknown');
  assert.equal(evaluateTypedRule({ same_term_duplicate: false }, duplicate, comparator).result, 'pass');
  assert.equal(evaluateTypedRule({}, duplicate, comparator).result, 'unknown');
});
test('Fact domains reject zero/out-of-range bands and unrecognized admission classes as unknown', () => {
  const scopedBand = leaf('scoped_band', 'effective_band', 'lte', 3, 'integer', { engine_field: 'incomeBand', domain: { minimum: 1, maximum: 10 } });
  for (const value of [0, 11]) {
    const actual = evaluateTypedRule({ effective_band: value }, scopedBand, comparator);
    assert.equal(actual.result, 'unknown'); assert.equal(actual.reason, 'invalid_fact_domain');
  }
  assert.equal(evaluateTypedRule({ effective_band: 1 }, scopedBand, comparator).result, 'pass');
  assert.equal(evaluateTypedRule({ effective_band: 4 }, scopedBand, comparator).result, 'fail');
  const admission = leaf('class', 'reported_class', 'not_in', ['new', 'transfer'], 'string', { domain: { enum: ['regular', 'new', 'transfer'] } });
  assert.equal(evaluateTypedRule({ reported_class: 'unclassified' }, admission, comparator).result, 'unknown');
  assert.equal(evaluateTypedRule({ reported_class: 'regular' }, admission, comparator).result, 'pass');
  assert.equal(evaluateTypedRule({ reported_class: 'new' }, admission, comparator).result, 'fail');
});
test('Contradictory grade context invalidates only derived comparison facts while preserving source attrs', () => {
  const constraints = [{ id: 'regular_grade_computable', when: { field: 'grade_basis', value: 'previous_regular' }, require: { field: 'grade_uncomputable', value: false }, invalidate_fields: ['cumulative_gpa'] }];
  const attrs = { grade_basis: 'previous_regular', grade_uncomputable: true, cumulative_gpa: fact(3, { scale: 4.5, basis: 'cumulative', period: '2025-2' }) };
  const before = structuredClone(attrs);
  const checked = applyFactConstraints(attrs, constraints);
  assert.deepEqual(attrs, before);
  assert.equal(checked.attrs.cumulative_gpa.status, 'invalid');
  assert.equal(evaluateTypedRule(checked.attrs, grade, comparator).result, 'unknown');
  assert.equal(checked.conflicts[0].source_required_fact, true);
  assert.equal(checked.conflicts[0].reason, 'conditional_fact_conflict');
  const normal = applyFactConstraints({ ...attrs, grade_uncomputable: false }, constraints);
  assert.equal(normal.conflicts.length, 0);
  assert.equal(evaluateTypedRule(normal.attrs, grade, comparator).result, 'pass');
});
test('Different score periods, bases and scales remain unknown before the existing comparator', () => {
  const valid = fact(3, { scale: 4.5, basis: 'cumulative', period: '2025-2' });
  const actual = evaluateTypedRule({ cumulative_gpa: valid }, grade, comparator);
  assert.equal(actual.result, 'pass');
  assert.equal(actual.engine, 'existing_evaluateRule_after_qualifier_gate');
  for (const changed of [{ period: '2026-1' }, { basis: 'previous_semester' }, { scale: 4.3 }]) {
    assert.equal(evaluateTypedRule({ cumulative_gpa: { ...valid, ...changed } }, grade, comparator).result, 'unknown');
  }
  assert.equal(evaluateTypedRule({ cumulative_gpa: 3 }, grade, comparator).result, 'unknown');
  assert.equal(evaluateTypedRule({ cumulative_gpa: { ...valid, value: 4.6 } }, grade, comparator).result, 'unknown');
});
test('AND, OR and NOT preserve unknown while retaining a decisive known result', () => {
  assert.equal(combine('not', ['unknown']), 'unknown');
  assert.equal(combine('and', ['fail', 'unknown']), 'fail');
  assert.equal(combine('or', ['pass', 'unknown']), 'pass');
  assert.equal(combine('or', ['fail', 'unknown']), 'unknown');
});
test('Exclusion role does not double-negate an explicit NOT', () => {
  const excluded = leaf('full_support', 'full_tuition_support', 'eq', true, 'boolean', { role: 'exclusion' });
  const root = group('not_support', 'not', [excluded]);
  assert.equal(evaluateAST({ full_tuition_support: false }, root, comparator), 'pass');
  assert.equal(evaluateAST({ full_tuition_support: true }, root, comparator), 'fail');
  assert.equal(evaluateAST({}, root, comparator), 'unknown');
});
test('An exception remains within its own group and cannot waive enrollment', () => {
  const root = group('scoped_exception', 'and', [
    leaf('enrolled', 'enrollment_status', 'eq', 'enrolled', 'string', { engine_field: 'enrollmentStatus' }),
    group('credit_or_exception', 'or', [leaf('credits', 'credits', 'gte', 12, 'number'), leaf('credit_exception', 'credit_exception', 'eq', true, 'boolean')]),
  ]);
  assert.equal(evaluateAST({ enrollment_status: 'leave', credits: 11, credit_exception: true }, root, comparator), 'fail');
  assert.equal(evaluateAST({ enrollment_status: 'enrolled', credits: 11, credit_exception: true }, root, comparator), 'pass');
});
test('Explicit scale guards make the incompatible GPA branch fail without conversion', () => {
  const makePath = (scale, threshold) => group(`scale_path_${scale}`, 'and', [
    leaf(`guard_${scale}`, 'gpa_scale', 'eq', scale, 'number'),
    { ...grade, id: `grade_${scale}`, value: threshold, unit: `gpa_${scale}`, qualifiers: { ...grade.qualifiers, scale } },
  ]);
  const root = group('gpa_alternatives', 'or', [makePath(4.5, 3), makePath(4.3, 2.7)]);
  assert.equal(evaluateAST({ gpa_scale: 4.5, cumulative_gpa: fact(2.9, { scale: 4.5, basis: 'cumulative', period: '2025-2' }) }, root, comparator), 'fail');
  assert.equal(evaluateAST({ gpa_scale: 4.3, cumulative_gpa: fact(2.7, { scale: 4.3, basis: 'cumulative', period: '2025-2' }) }, root, comparator), 'pass');
});
test('Continuous residence and a finalized current-term band require matching qualifications', () => {
  const residence = leaf('residence', 'residence_months', 'gte', 12, 'integer', { engine_field: 'residenceMonths', qualifiers: { basis: 'continuous', as_of: 'notice_publication_date' } });
  assert.equal(evaluateTypedRule({ residence_months: fact(12, { basis: 'continuous', as_of: 'notice_publication_date' }) }, residence, comparator).result, 'pass');
  assert.equal(evaluateTypedRule({ residence_months: fact(12, { basis: 'total', as_of: 'notice_publication_date' }) }, residence, comparator).result, 'unknown');
  const termBand = { ...band, qualifiers: { period: '2026-2', status: 'final' } };
  assert.equal(evaluateTypedRule({ income_band: fact(3, { period: '2026-1', status: 'final' }) }, termBand, comparator).result, 'unknown');
  assert.equal(evaluateTypedRule({ income_band: fact(3, { period: '2026-2', status: 'unconfirmed' }) }, termBand, comparator).result, 'unknown');
});

const temporary = mkdtempSync(join(tmpdir(), 'benefitlens-demo-test-'));
try {
  const bytes = Buffer.from('INVENTED SELF TEST ONLY: bands 1 through 7. Supporting scope, exclusions, exceptions, documents, ranking and payment are invented.');
  writeFileSync(join(temporary, 'invented-source.txt'), bytes);
  const hash = createHash('sha256').update(bytes).digest('hex');
  const fixturePath = join(temporary, 'fixture.json');
  const fixture = {
    schema_version: SCHEMA_VERSION, sample_id: 'invented-mechanics-only', title: 'Invented mechanics test — not a real scholarship', source_check: 'verified_full', normalization_status: 'scoped_demo', evaluation_domain: 'Invented three-profile mechanics examples only; no real notice semantics.', semantic_limits: ['Invented source; not a reviewed real scholarship.'], unresolved_source_semantics: [], evaluated_as_of: '2026-10-04T00:00:00+09:00', historical_demo: true,
    admission: { opens_at: '2026-01-01T00:00:00+09:00', closes_at: '2026-01-02T00:00:00+09:00', status: 'closed' },
    sources: [{ id: 'invented-source', format: 'text', url: 'https://example.invalid/synthetic-self-test', published_at: null, document_version: 'invented-v1', checked_at: '2026-10-04T00:00:00+09:00', file: 'invented-source.txt', sha256: hash, extracted_text_file: 'invented-source.txt', extracted_text_sha256: hash, page_count: null, relevant_sections: ['invented-line-1'], reviewed_sections: ['invented-line-1'], scope_note: 'Entire invented self-test text; this source is not used in the real samples.' }],
    evidence: [{ id: 'invented-evidence', source_id: 'invented-source', locator: 'Invented test line 1', quote: 'bands 1 through 7.', verification_method: 'Synthetic self-test text', sections: ['invented-line-1'] }],
    completeness: { ...Object.fromEntries(SECTIONS.map(section => [section, 'complete'])), unresolved_gating: [] },
    coverage: Object.fromEntries(SECTIONS.map(section => [section, ['invented-evidence']])),
    criteria: { root: band, readiness_root: leaf('document_ready', 'document_ready', 'eq', true, 'boolean', { role: 'document' }) }, non_gating: [],
    profiles: [
      { id: 'invented-pass', synthetic: true, attrs: { income_band: 1, document_ready: false }, expected: 'pass', expected_readiness: 'fail' },
      { id: 'invented-fail', synthetic: true, attrs: { income_band: 0, document_ready: true }, expected: 'fail', expected_readiness: 'pass', expected_reasons: ['band'] },
      { id: 'invented-unknown', synthetic: true, attrs: {}, expected: 'unknown', expected_readiness: 'unknown' },
    ],
  };
  const clone = () => structuredClone(fixture);
  writeFileSync(fixturePath, JSON.stringify(fixture, null, 2) + '\n');
  test('A closed historical sample separates public-condition matching from readiness', () => {
    const report = runFixture(clone(), fixturePath, comparator);
    assert.equal(report.validation, 'passed');
    assert.equal(report.test_passed, true);
    assert.equal(report.admission.status, 'closed');
    assert.equal(report.scenarios[0].qualification_result, 'pass');
    assert.equal(report.scenarios[0].readiness_result, 'fail');
    assert.equal(report.fixture_sha256, createHash('sha256').update(readFileSync(fixturePath)).digest('hex'));
  });
  test('Source review never silently becomes approval of all normalized rules', () => {
    const value = clone(); value.unresolved_source_semantics = ['Invented ambiguity outside the three-profile mechanics scope.'];
    const report = runFixture(value, fixturePath, comparator);
    assert.equal(report.normalization_status, 'scoped_demo');
    assert.equal(report.evaluation_domain, fixture.evaluation_domain);
    assert.deepEqual(report.unresolved_source_semantics, value.unresolved_source_semantics);
    assert.deepEqual(report.semantic_limits, fixture.semantic_limits);
    const broad = clone(); delete broad.evaluation_domain;
    assert.ok(validateFixture(broad, fixturePath).length > 0);
    const approved = clone(); approved.normalization_status = 'approved';
    assert.ok(validateFixture(approved, fixturePath).length > 0);
  });
  test('Payment-stage duplicate resolution stays separate from qualification and readiness', () => {
    const value = clone();
    value.criteria.payment_root = leaf('payment_duplicate_resolved', 'same_term_duplicate', 'eq', false, 'boolean', { role: 'duplicate' });
    value.profiles[0].attrs.document_ready = true;
    value.profiles[0].expected_readiness = 'pass';
    value.profiles[0].attrs.same_term_duplicate = true;
    value.profiles[0].expected_payment = 'fail';
    value.profiles.push({ id: 'invented-duplicate-resolved', synthetic: true, attrs: { income_band: 1, document_ready: true, same_term_duplicate: false }, expected: 'pass', expected_readiness: 'pass', expected_payment: 'pass' });
    value.profiles[2].expected_payment = 'unknown';
    const report = runFixture(value, fixturePath, comparator);
    assert.equal(report.test_passed, true);
    assert.deepEqual([report.scenarios[0].qualification_result, report.scenarios[0].readiness_result, report.scenarios[0].payment_result], ['pass', 'pass', 'fail']);
    assert.equal(report.scenarios[3].payment_result, 'pass');
    assert.equal(report.scenarios[2].payment_result, 'unknown');
    const missing = clone(); missing.profiles[0].expected_payment = 'pass';
    assert.ok(validateFixture(missing, fixturePath).some(error => error.includes('expected_payment requires payment_root')));
    const duplicateId = clone(); duplicateId.criteria.payment_root = { ...duplicateId.criteria.root };
    assert.ok(validateFixture(duplicateId, fixturePath).some(error => error.includes('duplicate criteria node id')));
    const wrongExpected = structuredClone(value); wrongExpected.profiles[0].expected_payment = 'pass';
    assert.equal(runFixture(wrongExpected, fixturePath, comparator).test_passed, false);
  });
  test('Domain declarations validate fact types without restricting comparison thresholds', () => {
    const value = clone(); value.criteria.root.domain = { minimum: 1, maximum: 10 }; value.criteria.root.value = [0, 7];
    assert.equal(validateFixture(value, fixturePath).length, 0);
    for (const domain of [{ minimum: 10, maximum: 1 }, { enum: [1, 'unknown'] }, { enum: [] }]) {
      const bad = clone(); bad.criteria.root.domain = domain;
      assert.ok(validateFixture(bad, fixturePath).length > 0);
    }
  });
  test('Fact-constraint reports preserve conflicts and reject undefined invalidation targets', () => {
    const value = clone();
    value.fact_constraints = [{ id: 'regular_context_valid', when: { field: 'grade_basis', value: 'previous_regular' }, require: { field: 'grade_uncomputable', value: false }, invalidate_fields: ['income_band'] }];
    value.profiles[0].attrs.grade_basis = 'previous_regular'; value.profiles[0].attrs.grade_uncomputable = true; value.profiles[0].expected = 'unknown';
    value.profiles.push({ id: 'invented-valid-restores-pass', synthetic: true, attrs: { income_band: 1, document_ready: true }, expected: 'pass', expected_readiness: 'pass' });
    const before = structuredClone(value.profiles[0].attrs);
    const report = runFixture(value, fixturePath, comparator);
    assert.equal(report.test_passed, true);
    assert.equal(report.scenarios[0].fact_constraint_conflicts.length, 1);
    assert.equal(report.scenarios[0].qualification_result, 'unknown');
    assert.deepEqual(value.profiles[0].attrs, before);
    const bad = clone(); bad.fact_constraints = [{ ...value.fact_constraints[0], invalidate_fields: ['nonexistent_modeled_field'] }];
    assert.ok(validateFixture(bad, fixturePath).length > 0);
  });
  test('Compact summaries preserve report references and reject stale fixture hashes', () => {
    const sample = runFixture(clone(), fixturePath, comparator);
    const report = { report_version: SCHEMA_VERSION, generated_at: '2026-10-04T00:00:00+09:00', offline: true, all_passed: true, production_engine_read_only: { path: comparator.sourceFile, sha256: comparator.sourceSha256 }, samples: [sample] };
    const reportPath = join(temporary, 'report.json'), summaryPath = join(temporary, 'summary.json');
    const summary = summarizeReport(report, reportPath, summaryPath, Buffer.from(JSON.stringify(report)));
    assert.equal(summary.full_report, 'report.json');
    assert.equal(summary.samples[0].fixture, 'fixture.json');
    assert.equal(summary.samples[0].scenario_count, 3);
    assert.equal(Object.hasOwn(summary.samples[0].scenarios[0], 'leaf_results'), false);
    const missing = structuredClone(report); delete missing.samples[0].fixture_sha256;
    assert.throws(() => summarizeReport(missing, reportPath, summaryPath, Buffer.from(JSON.stringify(missing))), /no fixture_sha256/);
    const originalBytes = readFileSync(fixturePath);
    writeFileSync(fixturePath, Buffer.concat([originalBytes, Buffer.from('\n')]));
    assert.throws(() => summarizeReport(report, reportPath, summaryPath, Buffer.from(JSON.stringify(report))), /stale report/);
    writeFileSync(fixturePath, originalBytes);
  });
  test('Incomplete declared coverage, unresolved mandatory rules and missing evidence fail strict validation', () => {
    for (const mutation of [f => { f.completeness.exceptions = 'pending'; }, f => { f.completeness.unresolved_gating = ['unread attachment']; }, f => { delete f.coverage.documents; }, f => { f.criteria.root.evidence_ids = ['absent']; }]) {
      const value = clone(); mutation(value);
      assert.ok(validateFixture(value, fixturePath).length > 0);
    }
  });
  test('Preserved-byte tampering and fabricated evidence quotes fail validation', () => {
    const changedHash = clone(); changedHash.sources[0].sha256 = '0'.repeat(64);
    assert.ok(validateFixture(changedHash, fixturePath).some(error => error.includes('SHA256 mismatch')));
    const changedQuote = clone(); changedQuote.evidence[0].quote = 'this quote is absent';
    assert.ok(validateFixture(changedQuote, fixturePath).some(error => error.includes('exact quote not found')));
  });
  test('Declared source scope cannot skip relevant sections or invent HWP pages', () => {
    for (const mutation of [f => { f.sources[0].relevant_sections = ['invented-line-1', 'unread']; }, f => { f.sources[0].reviewed_sections = []; }, f => { f.sources[0].format = 'hwp'; f.sources[0].page_count = 1; }, f => { f.evidence[0].sections = ['outside']; }]) {
      const value = clone(); mutation(value);
      assert.ok(validateFixture(value, fixturePath).length > 0);
    }
  });
  test('PDF review declarations reject incomplete or out-of-range pages', () => {
    const pdf = clone(); pdf.sources[0].format = 'pdf'; pdf.sources[0].page_count = 1; pdf.sources[0].relevant_pages = [1]; pdf.sources[0].reviewed_pages = [1]; delete pdf.evidence[0].sections;
    for (const mutation of [f => { f.sources[0].relevant_pages = [1, 2]; }, f => { f.sources[0].reviewed_pages = []; }, f => { f.evidence[0].pdf_pages = [2]; }]) {
      const value = structuredClone(pdf); mutation(value);
      assert.ok(validateFixture(value, fixturePath).length > 0);
    }
  });
  test('Empty groups, ambiguous NOT and duplicate IDs are rejected', () => {
    for (const root of [group('empty', 'and', []), group('ambiguous_not', 'not', [band, { ...band, id: 'second_band' }]), group('dup', 'and', [band, band])]) {
      const value = clone(); value.criteria.root = root;
      assert.ok(validateFixture(value, fixturePath).length > 0);
    }
  });
  test('Inverted ranges and incompatible numeric/boolean operands are rejected', () => {
    for (const root of [{ ...band, value: [7, 1] }, { ...band, value_type: 'boolean', value: [false, true] }, { ...band, value: [1, '7'] }]) {
      const value = clone(); value.criteria.root = root;
      assert.ok(validateFixture(value, fixturePath).length > 0);
    }
  });
  test('A non-synthetic profile and an unclosed historical deadline cannot pass strict validation', () => {
    const realFlag = clone(); realFlag.profiles[0].synthetic = false;
    assert.ok(validateFixture(realFlag, fixturePath).length > 0);
    const future = clone(); future.admission.closes_at = '2027-01-01T00:00:00+09:00';
    assert.ok(validateFixture(future, fixturePath).length > 0);
  });
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
test('The production comparator source is byte-identical after read-only loading and tests', () => {
  assert.deepEqual(readFileSync(comparator.sourceFile), originalEngineBytes);
});
const report = { suite: 'invented-mechanics-only; no real notice qualification or source review asserted', tested_at: new Date().toISOString(), count: testResults.length, all_passed: true, production_comparator: { file: comparator.sourceFile, sha256: comparator.sourceSha256 }, tests: testResults };
const out = process.argv.indexOf('--out');
if (out >= 0) writeFileSync(process.argv[out + 1], JSON.stringify(report, null, 2) + '\n');
console.log(`${testResults.length} independent boundary/scenario checks passed`);
