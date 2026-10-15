#!/usr/bin/env node
/*
 * Standalone, offline demo. Reads the production rule comparator without changing
 * it; qualified facts and unsupported fields are evaluated in this file.
 * A pass means that this synthetic profile matches the modeled public conditions.
 * It is never a scholarship selection or award decision.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { pathToFileURL, fileURLToPath } from 'node:url';

export const SCHEMA_VERSION = 'benefitlens.verified-demo.v1';
export const SECTIONS = ['eligibility', 'exclusions', 'exceptions', 'documents', 'selection', 'payment', 'referenced_rules'];
const RESULTS = new Set(['pass', 'fail', 'unknown']);
const OPERATORS = new Set(['eq', 'neq', 'in', 'not_in', 'gte', 'lte', 'between']);
const TYPES = new Set(['integer', 'number', 'string', 'boolean']);
const ROLES = new Set(['required', 'exclusion', 'document', 'duplicate']);
const QUALIFIERS = new Set(['period', 'basis', 'scale', 'status', 'as_of']);
const CORE_FIELDS = new Set(['institution', 'department', 'nationality', 'academicLevel', 'enrollmentStatus', 'age', 'year', 'graduateSemester', 'previousSemesterCredits', 'gpa', 'incomeBand', 'residenceMonths', 'topikLevel', 'teamSize', 'remainingStudyMonths', 'courseworkCompleted', 'regionalHighSchool', 'fullTimeStudent', 'industryEmployee']);
const TEXT_FIELDS = new Set(['institution', 'department', 'nationality', 'academicLevel', 'enrollmentStatus']);
const BOOLEAN_FIELDS = new Set(['courseworkCompleted', 'regionalHighSchool', 'fullTimeStudent', 'industryEmployee']);
const INTEGER_FIELDS = new Set(['age', 'year', 'graduateSemester', 'incomeBand', 'residenceMonths', 'topikLevel', 'teamSize', 'remainingStudyMonths']);
const text = value => typeof value === 'string' && value.trim().length > 0;
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const normalizeQuote = value => value.normalize('NFKC').replace(/\s+/gu, '');
const inType = (value, type) => type === 'integer' ? Number.isSafeInteger(value) : type === 'number' ? typeof value === 'number' && Number.isFinite(value) : typeof value === type;

export function loadProductionComparator(projectRoot) {
  const sourceFile = resolve(projectRoot, 'web/lib/eligibility.ts');
  const tsFile = resolve(projectRoot, 'web/node_modules/typescript');
  const require = createRequire(import.meta.url);
  const ts = require(tsFile);
  const source = readFileSync(sourceFile, 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports = {};
  new Function('exports', 'require', compiled)(exports, require);
  if (typeof exports.evaluateRule !== 'function') throw new Error('Existing evaluateRule export not found');
  return { evaluateRule: exports.evaluateRule, sourceFile, sourceSha256: sha256(source) };
}

export function combine(operator, results) {
  if (!results.length || (operator === 'not' && results.length !== 1)) throw new Error('Invalid group arity');
  if (operator === 'not') return results[0] === 'pass' ? 'fail' : results[0] === 'fail' ? 'pass' : 'unknown';
  if (operator === 'or') return results.includes('pass') ? 'pass' : results.every(r => r === 'fail') ? 'fail' : 'unknown';
  if (operator === 'and') return results.includes('fail') ? 'fail' : results.every(r => r === 'pass') ? 'pass' : 'unknown';
  throw new Error('Invalid group operator');
}

export function evaluateTypedRule(attrs, rule, comparator, now = new Date()) {
  const fact = attrs[rule.field];
  const descriptor = object(fact) ? fact : { value: fact };
  const value = descriptor.value;
  const base = { rule_id: rule.id, field: rule.field, role: rule.role, evidence_ids: rule.evidence_ids, actual: value ?? null, qualifiers: rule.qualifiers ?? {}, engine: 'demo_typed_wrapper' };
  if (value === undefined || value === null || (typeof value === 'string' && !value.trim())) return { ...base, result: 'unknown', reason: 'missing_fact' };
  if (!inType(value, rule.value_type) || (typeof value === 'number' && value < 0)) return { ...base, result: 'unknown', reason: 'invalid_fact_type_or_domain' };
  if (rule.domain && ((rule.domain.minimum !== undefined && value < rule.domain.minimum) || (rule.domain.maximum !== undefined && value > rule.domain.maximum) || (rule.domain.enum !== undefined && !rule.domain.enum.includes(value)))) return { ...base, result: 'unknown', reason: 'invalid_fact_domain', domain: rule.domain };
  if (descriptor.status === 'unknown' || descriptor.status === 'missing' || descriptor.status === 'unconfirmed' || descriptor.status === 'invalid') return { ...base, result: 'unknown', reason: 'unconfirmed_fact' };
  for (const [key, expected] of Object.entries(rule.qualifiers ?? {})) {
    if (descriptor[key] === undefined || descriptor[key] === null || descriptor[key] === '') return { ...base, result: 'unknown', reason: `missing_qualifier:${key}` };
    if (descriptor[key] !== expected) return { ...base, result: 'unknown', reason: `qualifier_mismatch:${key}` };
  }
  if (rule.value_type === 'number' && rule.qualifiers?.scale !== undefined && value > rule.qualifiers.scale) return { ...base, result: 'unknown', reason: 'value_exceeds_scale' };
  if (rule.engine_field) {
    if (!comparator) throw new Error('Production comparator required for engine_field');
    const profile = { [rule.engine_field]: String(value) };
    if (rule.engine_field === 'gpa') {
      profile.gpaScale = String(descriptor.scale ?? '');
      profile.gpaBasis = String(descriptor.basis ?? '');
      profile.gpaEvaluationPeriod = String(descriptor.period ?? '');
    }
    const productionRule = { kind: 'rule', id: rule.id, field: rule.engine_field, operator: rule.operator, value: rule.value, valueType: rule.value_type, criterionCode: rule.criterion_code ?? rule.field, unit: rule.unit, label: rule.label ?? rule.id, context: rule.engine_field === 'gpa' ? {basis: rule.qualifiers?.basis, evaluationPeriod: rule.qualifiers?.period} : undefined };
    const result = comparator.evaluateRule(profile, productionRule, now);
    return { ...base, result, engine: 'existing_evaluateRule_after_qualifier_gate', reason: result === 'unknown' ? 'production_comparator_unknown' : 'compared' };
  }
  let matches;
  if (rule.operator === 'eq') matches = value === rule.value;
  else if (rule.operator === 'neq') matches = value !== rule.value;
  else if (rule.operator === 'in') matches = rule.value.includes(value);
  else if (rule.operator === 'not_in') matches = !rule.value.includes(value);
  else if (rule.operator === 'gte') matches = value >= rule.value;
  else if (rule.operator === 'lte') matches = value <= rule.value;
  else if (rule.operator === 'between') matches = value >= rule.value[0] && value <= rule.value[1];
  else throw new Error('Unsupported operator');
  return { ...base, result: matches ? 'pass' : 'fail', reason: 'compared' };
}

export function evaluateAST(attrs, node, comparator, now, trace = []) {
  if (node.kind === 'rule') {
    const result = evaluateTypedRule(attrs, node, comparator, now);
    trace.push(result);
    return result.result;
  }
  return combine(node.operator, node.children.map(child => evaluateAST(attrs, child, comparator, now, trace)));
}

export function applyFactConstraints(attrs, constraints = []) {
  const evaluatedAttrs = structuredClone(attrs);
  const conflicts = [];
  const read = field => {
    const raw = attrs[field];
    const descriptor = object(raw) ? raw : { value: raw };
    const confirmed = descriptor.value !== undefined && descriptor.value !== null && !['unknown', 'missing', 'unconfirmed', 'invalid'].includes(descriptor.status);
    return { raw, value: descriptor.value, confirmed };
  };
  // Inspect the original facts for every constraint. Invalidating one field must
  // not hide another contradiction, and the fixture's original attrs are intact.
  for (const constraint of constraints) {
    const when = read(constraint.when.field);
    if (!when.confirmed || when.value !== constraint.when.value) continue;
    const required = read(constraint.require.field);
    if (required.confirmed && required.value === constraint.require.value) continue;
    for (const field of constraint.invalidate_fields) {
      const original = evaluatedAttrs[field];
      evaluatedAttrs[field] = object(original) ? { ...original, status: 'invalid' } : { value: original ?? null, status: 'invalid' };
    }
    conflicts.push({ constraint_id: constraint.id, reason: required.confirmed ? 'conditional_fact_conflict' : 'conditional_fact_missing', when: constraint.when, require: constraint.require, source_when_fact: when.raw ?? null, source_required_fact: required.raw ?? null, invalidated_fields: constraint.invalidate_fields });
  }
  return { attrs: evaluatedAttrs, conflicts };
}

export function validateFixture(fixture, fixturePath, { verifyFiles = true } = {}) {
  const errors = [];
  const check = (ok, message) => { if (!ok) errors.push(message); };
  if (!object(fixture)) return ['Fixture must be a JSON object'];
  check(fixture.schema_version === SCHEMA_VERSION, 'schema_version must be benefitlens.verified-demo.v1');
  check(text(fixture.sample_id), 'sample_id missing');
  check(text(fixture.title), 'title missing');
  check(fixture.source_check === 'verified_full', 'source_check must explicitly be verified_full');
  check(fixture.normalization_status === 'scoped_demo', 'normalization_status must explicitly be scoped_demo; source review is not rule approval');
  const domain = fixture.evaluation_domain;
  check(text(domain) || (Array.isArray(domain) && domain.length > 0 && domain.every(text)) || (object(domain) && Object.keys(domain).length > 0), 'evaluation_domain must explicitly state the limited synthetic comparison scope');
  if (fixture.semantic_limits !== undefined) check(Array.isArray(fixture.semantic_limits), 'semantic_limits must be an array when supplied');
  if (fixture.unresolved_source_semantics !== undefined) check(Array.isArray(fixture.unresolved_source_semantics), 'unresolved_source_semantics must be an array when supplied');
  check(fixture.historical_demo === true, 'This bundle accepts historical_demo=true only');
  const asOf = Date.parse(fixture.evaluated_as_of);
  check(Number.isFinite(asOf), 'evaluated_as_of must be an explicit ISO date/time');
  const admission = fixture.admission;
  check(object(admission), 'admission object missing');
  if (object(admission)) {
    const opens = Date.parse(admission.opens_at), closes = Date.parse(admission.closes_at);
    check(Number.isFinite(opens) && Number.isFinite(closes) && opens <= closes, 'admission dates missing, invalid or reversed');
    check(admission.status === 'closed', 'Historical sample admission.status must be closed');
    check(closes < asOf, 'Historical sample deadline must be before evaluated_as_of');
  }
  const sourceMap = new Map(), evidenceMap = new Map(), nodeIds = new Set(), profileIds = new Set(), ruleFields = new Set();
  const sourceChecks = [];
  check(Array.isArray(fixture.sources) && fixture.sources.length > 0, 'sources must be nonempty');
  for (const source of fixture.sources ?? []) {
    if (!object(source)) { errors.push('Invalid source object'); continue; }
    check(text(source.id) && !sourceMap.has(source.id), `Missing or duplicate source id: ${source.id}`);
    sourceMap.set(source.id, source);
    check(text(source.url) && /^https:\/\//u.test(source.url), `${source.id}: HTTPS source URL missing`);
    check(Object.hasOwn(source, 'published_at') && (source.published_at === null || text(source.published_at)), `${source.id}: published_at must be supplied (null if unavailable)`);
    check(text(source.document_version), `${source.id}: document_version missing`);
    check(text(source.checked_at) && Number.isFinite(Date.parse(source.checked_at)), `${source.id}: checked_at missing or invalid`);
    check(text(source.scope_note), `${source.id}: review scope_note missing`);
    const format = source.format ?? 'pdf';
    check(['pdf', 'hwp', 'text', 'html'].includes(format), `${source.id}: unsupported source format`);
    if (format === 'pdf') {
      check(Number.isSafeInteger(source.page_count) && source.page_count > 0, `${source.id}: invalid page_count`);
      const pages = source.relevant_pages, reviewed = source.reviewed_pages;
      check(Array.isArray(pages) && pages.length > 0, `${source.id}: relevant_pages missing`);
      check(Array.isArray(reviewed) && reviewed.length > 0, `${source.id}: reviewed_pages missing`);
      if (Array.isArray(pages) && Array.isArray(reviewed)) {
        check(new Set(pages).size === pages.length && new Set(reviewed).size === reviewed.length, `${source.id}: duplicate page declarations`);
        check([...pages, ...reviewed].every(p => Number.isSafeInteger(p) && p >= 1 && p <= source.page_count), `${source.id}: page declarations outside PDF range`);
        check(pages.every(p => reviewed.includes(p)), `${source.id}: relevant pages have not all been reviewed`);
      }
    } else {
      check(source.page_count === null, `${source.id}: non-PDF source must explicitly use page_count=null rather than invent physical pages`);
      const sections = source.relevant_sections, reviewed = source.reviewed_sections;
      check(Array.isArray(sections) && sections.length > 0 && sections.every(text), `${source.id}: relevant_sections missing`);
      check(Array.isArray(reviewed) && reviewed.length > 0 && reviewed.every(text), `${source.id}: reviewed_sections missing`);
      if (Array.isArray(sections) && Array.isArray(reviewed)) {
        check(new Set(sections).size === sections.length && new Set(reviewed).size === reviewed.length, `${source.id}: duplicate section declarations`);
        check(sections.every(section => reviewed.includes(section)), `${source.id}: relevant sections have not all been reviewed`);
      }
      if (format === 'hwp') check(text(source.extracted_text_file), `${source.id}: HWP requires a preserved extracted text companion`);
    }
    check(text(source.file), `${source.id}: preserved source file missing`);
    check(typeof source.sha256 === 'string' && /^[a-f0-9]{64}$/u.test(source.sha256), `${source.id}: invalid source SHA256`);
    if (verifyFiles && text(source.file)) {
      try {
        const path = resolve(dirname(fixturePath), source.file);
        const actualHash = sha256(readFileSync(path));
        check(actualHash === source.sha256, `${source.id}: preserved source SHA256 mismatch`);
        sourceChecks.push({ source_id: source.id, file: path, sha256: actualHash, hash_matches: actualHash === source.sha256 });
      } catch (error) { errors.push(`${source.id}: cannot read preserved source: ${error.code ?? error.message}`); }
    }
    if (source.extracted_text_file) {
      check(/^[a-f0-9]{64}$/u.test(source.extracted_text_sha256 ?? ''), `${source.id}: extracted text SHA256 missing`);
      if (verifyFiles) {
        try {
          const bytes = readFileSync(resolve(dirname(fixturePath), source.extracted_text_file));
          check(sha256(bytes) === source.extracted_text_sha256, `${source.id}: extracted text SHA256 mismatch`);
          source._checkedText = normalizeQuote(bytes.toString('utf8'));
        } catch (error) { errors.push(`${source.id}: cannot read extracted text: ${error.code ?? error.message}`); }
      }
    }
  }
  check(Array.isArray(fixture.evidence) && fixture.evidence.length > 0, 'evidence must be nonempty');
  for (const item of fixture.evidence ?? []) {
    if (!object(item)) { errors.push('Invalid evidence object'); continue; }
    check(text(item.id) && !evidenceMap.has(item.id), `Missing or duplicate evidence id: ${item.id}`);
    evidenceMap.set(item.id, item);
    const source = sourceMap.get(item.source_id);
    check(Boolean(source), `${item.id}: source_id does not resolve`);
    check(text(item.locator) || (object(item.locator) && Object.keys(item.locator).length > 0), `${item.id}: condition locator missing`);
    check(text(item.quote), `${item.id}: condition quote missing`);
    check(text(item.verification_method), `${item.id}: verification_method missing`);
    if (source?._checkedText && text(item.quote)) check(source._checkedText.includes(normalizeQuote(item.quote)), `${item.id}: exact quote not found in extracted source text`);
    if (item.pdf_pages !== undefined) {
      check(Array.isArray(item.pdf_pages) && item.pdf_pages.length > 0 && item.pdf_pages.every(p => source?.relevant_pages?.includes(p)), `${item.id}: evidence PDF pages outside declared relevant scope`);
    }
    if (item.sections !== undefined) check(Array.isArray(item.sections) && item.sections.length > 0 && item.sections.every(section => source?.relevant_sections?.includes(section)), `${item.id}: evidence sections outside declared relevant scope`);
  }
  const refs = (ids, prefix) => {
    check(Array.isArray(ids) && ids.length > 0, `${prefix}: evidence_ids must be nonempty`);
    if (Array.isArray(ids)) for (const id of ids) check(evidenceMap.has(id), `${prefix}: unresolved evidence ${id}`);
  };
  for (const section of SECTIONS) {
    check(fixture.completeness?.[section] === 'complete', `${section}: completeness must explicitly be complete`);
    refs(fixture.coverage?.[section], `coverage.${section}`);
  }
  check(Array.isArray(fixture.completeness?.unresolved_gating) && fixture.completeness.unresolved_gating.length === 0, 'unresolved_gating must explicitly be empty for verified_full');
  const validateNode = node => {
    if (!object(node)) { errors.push('Invalid criteria node'); return; }
    check(text(node.id) && !nodeIds.has(node.id), `Missing or duplicate criteria node id: ${node.id}`);
    nodeIds.add(node.id);
    if (node.kind === 'group') {
      check(['and', 'or', 'not'].includes(node.operator), `${node.id}: invalid group operator`);
      check(Array.isArray(node.children) && node.children.length > 0, `${node.id}: empty group`);
      if (node.operator === 'not') check(node.children?.length === 1, `${node.id}: NOT requires exactly one child`);
      if (Array.isArray(node.children)) node.children.forEach(validateNode);
      return;
    }
    check(node.kind === 'rule', `${node.id}: unknown node kind`);
    check(text(node.field), `${node.id}: field missing`);
    if (text(node.field)) ruleFields.add(node.field);
    check(TYPES.has(node.value_type), `${node.id}: unsupported value_type`);
    check(OPERATORS.has(node.operator), `${node.id}: unsupported operator`);
    check(ROLES.has(node.role), `${node.id}: role must be required/exclusion/document/duplicate`);
    refs(node.evidence_ids, node.id);
    const values = ['in', 'not_in', 'between'].includes(node.operator) ? node.value : [node.value];
    check(Array.isArray(values) && values.length > 0 && values.every(value => inType(value, node.value_type)), `${node.id}: operand type mismatch or empty operand`);
    if (['gte', 'lte', 'between'].includes(node.operator)) check(['integer', 'number'].includes(node.value_type), `${node.id}: numeric comparison requires numeric type`);
    if (node.operator === 'between') check(node.value?.length === 2 && node.value[0] <= node.value[1], `${node.id}: invalid inclusive between bounds`);
    if (node.domain !== undefined) {
      check(object(node.domain) && Object.keys(node.domain).length > 0, `${node.id}: domain must be a nonempty object`);
      if (object(node.domain)) {
        for (const key of Object.keys(node.domain)) check(['minimum', 'maximum', 'enum'].includes(key), `${node.id}: unsupported domain member ${key}`);
        for (const key of ['minimum', 'maximum']) if (node.domain[key] !== undefined) {
          check(['integer', 'number'].includes(node.value_type) && typeof node.domain[key] === 'number' && Number.isFinite(node.domain[key]), `${node.id}: domain ${key} must be a finite numeric bound for a numeric field`);
        }
        if (node.domain.minimum !== undefined && node.domain.maximum !== undefined) check(node.domain.minimum <= node.domain.maximum, `${node.id}: domain bounds are reversed`);
        if (node.domain.enum !== undefined) check(Array.isArray(node.domain.enum) && node.domain.enum.length > 0 && node.domain.enum.every(value => inType(value, node.value_type)) && new Set(node.domain.enum).size === node.domain.enum.length, `${node.id}: domain enum must be a nonempty unique array of correctly typed fact values`);
      }
    }
    if (node.qualifiers !== undefined) {
      check(object(node.qualifiers), `${node.id}: qualifiers must be an object`);
      if (object(node.qualifiers)) for (const [key, value] of Object.entries(node.qualifiers)) {
        check(QUALIFIERS.has(key), `${node.id}: unsupported qualifier ${key}`);
        check(typeof value === 'string' ? text(value) : typeof value === 'number' && Number.isFinite(value) && value > 0, `${node.id}: qualifier ${key} invalid`);
        if (key === 'scale') check(typeof value === 'number' && value > 0, `${node.id}: GPA/score scale must be a positive number`);
      }
    }
    if (node.engine_field) {
      check(CORE_FIELDS.has(node.engine_field), `${node.id}: engine_field is not safely supported by this adapter`);
      const allowedType = TEXT_FIELDS.has(node.engine_field) ? node.value_type === 'string' : BOOLEAN_FIELDS.has(node.engine_field) ? node.value_type === 'boolean' : INTEGER_FIELDS.has(node.engine_field) ? node.value_type === 'integer' : ['integer', 'number'].includes(node.value_type);
      check(allowedType, `${node.id}: engine_field and value_type are incompatible`);
      if (node.engine_field === 'gpa') {
        check(node.criterion_code === 'gpa', `${node.id}: GPA engine rule must use criterion_code=gpa`);
        check(node.unit === `gpa_${node.qualifiers?.scale}`, `${node.id}: GPA engine unit and explicit qualifier scale must agree`);
        check(text(node.qualifiers?.basis) && text(node.qualifiers?.period), `${node.id}: GPA requires explicit basis and period`);
      }
    }
  };
  validateNode(fixture.criteria?.root);
  if (fixture.criteria?.readiness_root !== undefined) validateNode(fixture.criteria.readiness_root);
  if (fixture.criteria?.payment_root !== undefined) validateNode(fixture.criteria.payment_root);
  if (fixture.fact_constraints !== undefined) {
    check(Array.isArray(fixture.fact_constraints), 'fact_constraints must be an array');
    const constraintIds = new Set();
    for (const constraint of Array.isArray(fixture.fact_constraints) ? fixture.fact_constraints : []) {
      if (!object(constraint)) { errors.push('Invalid fact constraint object'); continue; }
      check(text(constraint.id) && !constraintIds.has(constraint.id) && !nodeIds.has(constraint.id), `Missing, duplicate or ambiguous fact constraint id: ${constraint.id}`);
      constraintIds.add(constraint.id);
      for (const key of ['when', 'require']) {
        const clause = constraint[key];
        check(object(clause) && text(clause.field) && Object.hasOwn(clause, 'value') && ['string', 'boolean', 'number'].includes(typeof clause.value) && (typeof clause.value !== 'number' || Number.isFinite(clause.value)), `${constraint.id}: ${key} must contain a field and finite primitive value`);
      }
      check(Array.isArray(constraint.invalidate_fields) && constraint.invalidate_fields.length > 0 && constraint.invalidate_fields.every(field => text(field) && ruleFields.has(field)) && new Set(constraint.invalidate_fields).size === constraint.invalidate_fields.length, `${constraint.id}: invalidate_fields must uniquely name modeled rule fields`);
    }
  }
  check(Array.isArray(fixture.non_gating), 'non_gating must explicitly be an array');
  for (const item of fixture.non_gating ?? []) {
    check(['selection', 'payment', 'procedural'].includes(item.kind) && text(item.text), 'Invalid non_gating item');
    refs(item.evidence_ids, `non_gating.${item.kind}`);
  }
  check(Array.isArray(fixture.profiles) && fixture.profiles.length >= 3, 'At least three synthetic profiles are required');
  for (const profile of fixture.profiles ?? []) {
    check(text(profile.id) && !profileIds.has(profile.id), `Missing or duplicate profile id: ${profile.id}`);
    profileIds.add(profile.id);
    check(profile.synthetic === true, `${profile.id}: synthetic=true must be explicit`);
    check(object(profile.attrs), `${profile.id}: attrs must be an object`);
    check(RESULTS.has(profile.expected), `${profile.id}: invalid expected result`);
    if (profile.expected_readiness !== undefined) {
      check(RESULTS.has(profile.expected_readiness), `${profile.id}: invalid expected_readiness result`);
      check(fixture.criteria?.readiness_root !== undefined, `${profile.id}: expected_readiness requires readiness_root`);
    }
    if (profile.expected_payment !== undefined) {
      check(RESULTS.has(profile.expected_payment), `${profile.id}: invalid expected_payment result`);
      check(fixture.criteria?.payment_root !== undefined, `${profile.id}: expected_payment requires payment_root`);
    }
    if (profile.expected_reasons !== undefined) check(Array.isArray(profile.expected_reasons) && profile.expected_reasons.every(id => nodeIds.has(id)), `${profile.id}: expected_reasons must reference criteria ids`);
  }
  check(['pass', 'fail', 'unknown'].every(r => fixture.profiles?.some(p => p.expected === r)), 'Synthetic scenarios must include pass, fail and unknown expectations');
  for (const source of sourceMap.values()) delete source._checkedText;
  return Object.assign(errors, { sourceChecks });
}

export function runFixture(fixture, fixturePath, comparator, { fixtureSha256 } = {}) {
  const fixtureHash = fixtureSha256 ?? sha256(readFileSync(fixturePath));
  const errors = validateFixture(fixture, fixturePath);
  const sourceAndScope = { fixture_sha256: fixtureHash, source_check: fixture.source_check, source_check_meaning: 'Reviewer declares that the target notice version and applicable publicly available references were read; this is not approval of all automatic rules.', normalization_status: fixture.normalization_status ?? null, evaluation_domain: fixture.evaluation_domain ?? null, semantic_limits: fixture.semantic_limits ?? [], unresolved_source_semantics: fixture.unresolved_source_semantics ?? [] };
  if (errors.length) return { fixture: fixturePath, sample_id: fixture.sample_id, ...sourceAndScope, validation: 'failed', errors: [...errors], source_files: errors.sourceChecks, scenarios: [] };
  const scenarios = fixture.profiles.map(profile => {
    const checkedFacts = applyFactConstraints(profile.attrs, fixture.fact_constraints);
    const leafResults = [];
    const result = evaluateAST(checkedFacts.attrs, fixture.criteria.root, comparator, new Date(fixture.evaluated_as_of), leafResults);
    const readinessLeafResults = [];
    const readinessResult = fixture.criteria.readiness_root ? evaluateAST(checkedFacts.attrs, fixture.criteria.readiness_root, comparator, new Date(fixture.evaluated_as_of), readinessLeafResults) : null;
    const paymentLeafResults = [];
    const paymentResult = fixture.criteria.payment_root ? evaluateAST(checkedFacts.attrs, fixture.criteria.payment_root, comparator, new Date(fixture.evaluated_as_of), paymentLeafResults) : null;
    const expectedReasonsMatch = (profile.expected_reasons ?? []).every(id => leafResults.some(leaf => leaf.rule_id === id && leaf.result !== 'pass'));
    const expectedReadinessMatch = profile.expected_readiness === undefined || readinessResult === profile.expected_readiness;
    const expectedPaymentMatch = profile.expected_payment === undefined || paymentResult === profile.expected_payment;
    return { profile_id: profile.id, synthetic: true, result, qualification_result: result, expected: profile.expected, readiness_result: readinessResult, expected_readiness: profile.expected_readiness ?? null, payment_result: paymentResult, expected_payment: profile.expected_payment ?? null, payment_result_meaning: 'Synthetic profile matches or fails only the modeled payment-stage conditions; no selection or payment confirmation is made.', fact_constraint_conflicts: checkedFacts.conflicts, expectation_matches: result === profile.expected && expectedReasonsMatch && expectedReadinessMatch && expectedPaymentMatch, leaf_results: leafResults, readiness_leaf_results: readinessLeafResults, payment_leaf_results: paymentLeafResults };
  });
  return { fixture: fixturePath, sample_id: fixture.sample_id, title: fixture.title, ...sourceAndScope, validation: 'passed', source_files: errors.sourceChecks, admission: fixture.admission, historical_demo: true, comparison_scope: 'modeled public application conditions within evaluation_domain for synthetic profiles; selection and award remain separate', review_scope_limitation: 'Byte hashes, evidence references, declared review coverage and syntax are checked; human source interpretation, completeness and automatic normalization approval are not proven by the runner.', scenarios, test_passed: scenarios.every(s => s.expectation_matches), non_gating: fixture.non_gating };
}

async function main() {
  const args = process.argv.slice(2);
  let project = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  let output;
  const fixtures = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--project-root') project = args[++i];
    else if (args[i] === '--out') output = args[++i];
    else if (args[i] === '--help') {
      console.log('node verified-demo-runner.mjs [--project-root PATH] [--out REPORT.json] FIXTURE.json ...');
      return;
    } else fixtures.push(resolve(args[i]));
  }
  if (!fixtures.length) throw new Error('Provide at least one fixture JSON file; --help shows usage');
  const comparator = loadProductionComparator(project);
  const samples = fixtures.map(path => {
    try {
      const fixtureBytes = readFileSync(path);
      return runFixture(JSON.parse(fixtureBytes.toString('utf8')), path, comparator, { fixtureSha256: sha256(fixtureBytes) });
    }
    catch (error) { return { fixture: path, validation: 'failed', errors: [error.message], scenarios: [] }; }
  });
  const report = { report_version: SCHEMA_VERSION, generated_at: new Date().toISOString(), offline: true, production_engine_read_only: { path: comparator.sourceFile, sha256: comparator.sourceSha256, reused_function: 'evaluateRule', extended_in_demo_only: ['qualified period/basis/scale/status/as_of facts', 'typed custom fields', 'AST group composition and evidence validation'] }, all_passed: samples.every(s => s.validation === 'passed' && s.test_passed), samples };
  if (output) writeFileSync(resolve(output), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
  if (!report.all_passed) process.exitCode = 1;
}
if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) main().catch(error => { console.error(error.message); process.exitCode = 1; });
