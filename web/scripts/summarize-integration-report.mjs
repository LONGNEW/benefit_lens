#!/usr/bin/env node
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, relative, resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';

const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const fields = (object, names) => Object.fromEntries(names.filter(name => Object.hasOwn(object, name)).map(name => [name, object[name]]));
const relativeFile = (base, file) => relative(base, resolve(file)).split('\\').join('/');
const distribution = (scenarios, key) => {
  const output = { pass: 0, fail: 0, unknown: 0, not_modeled: 0 };
  for (const scenario of scenarios) output[scenario[key] === null || scenario[key] === undefined ? 'not_modeled' : scenario[key]]++;
  return output;
};

export function summarizeReport(report, reportPath, outputPath, reportBytes) {
  const base = dirname(resolve(outputPath));
  const samples = report.samples.map(sample => {
    if (!/^[a-f0-9]{64}$/u.test(sample.fixture_sha256 ?? '')) throw new Error(`${sample.sample_id}: report has no fixture_sha256; rerun verified-demo-runner before summarizing`);
    const actualHash = hash(readFileSync(sample.fixture));
    if (actualHash !== sample.fixture_sha256) throw new Error(`${sample.sample_id}: stale report, current fixture SHA256 differs`);
    const output = fields(sample, ['sample_id', 'title', 'fixture_sha256', 'source_check', 'source_check_meaning', 'normalization_status', 'evaluation_domain', 'semantic_limits', 'unresolved_source_semantics', 'validation', 'errors', 'admission', 'historical_demo', 'comparison_scope', 'review_scope_limitation', 'test_passed', 'non_gating']);
    output.fixture = relativeFile(base, sample.fixture);
    output.source_files = (sample.source_files ?? []).map(source => ({ ...source, file: relativeFile(base, source.file) }));
    output.scenario_count = sample.scenarios.length;
    output.result_counts = Object.fromEntries(['qualification_result', 'readiness_result', 'payment_result'].map(key => [key, distribution(sample.scenarios, key)]));
    output.trace_count = { qualification: 0, readiness: 0, payment: 0 };
    output.comparison_engine_counts = {};
    for (const scenario of sample.scenarios) {
      for (const [stage, key] of [['qualification', 'leaf_results'], ['readiness', 'readiness_leaf_results'], ['payment', 'payment_leaf_results']]) {
        const traces = scenario[key] ?? [];
        output.trace_count[stage] += traces.length;
        for (const trace of traces) output.comparison_engine_counts[trace.engine] = (output.comparison_engine_counts[trace.engine] ?? 0) + 1;
      }
    }
    output.scenarios = sample.scenarios.map(scenario => {
      const item = fields(scenario, ['profile_id', 'synthetic', 'qualification_result', 'expected', 'readiness_result', 'expected_readiness', 'payment_result', 'expected_payment', 'expectation_matches']);
      if (scenario.fact_constraint_conflicts?.length) item.fact_constraint_conflicts = scenario.fact_constraint_conflicts;
      return item;
    });
    return output;
  });
  return { ...fields(report, ['report_version', 'generated_at', 'offline', 'production_engine_read_only', 'all_passed']), summarized_at: new Date().toISOString(), full_report: relativeFile(base, reportPath), full_report_sha256: hash(reportBytes ?? readFileSync(reportPath)), summary_scope: 'Repeated leaf traces are omitted. Full traces remain in full_report; canonical profiles, AST and source evidence remain in each fixture. Source reading, scoped normalization and synthetic condition comparison are distinct; no selection or payment is confirmed.', total_samples: samples.length, total_scenarios: samples.reduce((count, sample) => count + sample.scenario_count, 0), total_trace_rows: samples.reduce((count, sample) => count + Object.values(sample.trace_count).reduce((a, b) => a + b, 0), 0), samples };
}

function main() {
  const workRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
  const args = process.argv.slice(2);
  let reportPath = resolve(workRoot, 'integration-report.json');
  let outputPath = resolve(workRoot, 'integration-summary.json');
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--report') reportPath = resolve(args[++i]);
    else if (args[i] === '--out') outputPath = resolve(args[++i]);
    else if (args[i] === '--help') { console.log('node summarize-integration-report.mjs [--report REPORT.json] [--out SUMMARY.json]'); return; }
    else throw new Error(`Unknown argument ${args[i]}`);
  }
  if (resolve(reportPath) === resolve(outputPath)) throw new Error('Summary must be a separate file; full report is preserved');
  const reportBytes = readFileSync(reportPath);
  const report = JSON.parse(reportBytes.toString('utf8'));
  const summary = summarizeReport(report, reportPath, outputPath, reportBytes);
  const bytes = JSON.stringify(summary, null, 2) + '\n';
  writeFileSync(outputPath, bytes);
  console.log(JSON.stringify({ summary: outputPath, bytes: Buffer.byteLength(bytes), all_passed: summary.all_passed, samples: summary.total_samples, scenarios: summary.total_scenarios, trace_rows_in_full_report: summary.total_trace_rows, full_report: summary.full_report }));
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  try { main(); } catch (error) { console.error(error.message); process.exitCode = 1; }
}
