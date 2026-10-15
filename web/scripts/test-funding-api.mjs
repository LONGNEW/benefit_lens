import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
const require = createRequire(import.meta.url);
const ts = require("typescript");
function moduleFrom(path, overrides = {}) {
  const code = ts.transpile(readFileSync(new URL(path, import.meta.url), "utf8"), { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 });
  const exports = {};
  new Function("exports", "require", code)(exports, name => overrides[name] ?? require(name));
  return exports;
}
const sqlite = new DatabaseSync(":memory:");
for (const file of readdirSync(new URL("../drizzle/", import.meta.url)).filter(name => name.endsWith(".sql")).sort()) {
  sqlite.exec(readFileSync(new URL(`../drizzle/${file}`, import.meta.url), "utf8"));
}
const DB = {
  prepare(sql) {
    let values = [];
    return {
      bind(...bindings) { values = bindings; return this; },
      async first() { return sqlite.prepare(sql).get(...values) ?? null; },
      async all() { return {results: sqlite.prepare(sql).all(...values)}; },
      async run() { return sqlite.prepare(sql).run(...values); },
    };
  },
  async batch(statements) {
    sqlite.exec("BEGIN");
    try { const results = []; for (const statement of statements) results.push(await statement.run()); sqlite.exec("COMMIT"); return results; }
    catch (error) { sqlite.exec("ROLLBACK"); throw error; }
  },
};
const funding = moduleFrom("../lib/funding.ts");
const env = {DB, D1_SYNC_TOKEN: "test-token"};
const {POST} = moduleFrom("../app/api/admin/sync/route.ts", {"cloudflare:workers": {env}});
const {GET} = moduleFrom("../app/api/opportunities/route.ts", {
  "cloudflare:workers": {env},
  "@/lib/opportunity-data": moduleFrom("../lib/opportunity-data.ts"),
  "@/lib/eligibility": moduleFrom("../lib/eligibility.ts"),
  "@/lib/funding": funding,
});
function record(id, group, tags) {
  return {id, name: id, organization: `operator-${group}`, support: "공식 혜택", category: "금전 지원",
    sourceGroup: "school", operatorGroup: group, rulesJson: "[]", criteriaJson: "null", unresolvedCriteriaJson: "[]",
    sourceUrl: "https://official.test/notice", sourceUpdatedAt: "2026-10-01T00:00:00Z",
    eligibilityJson: "[]", benefitType: "service", lifecycleStatus: "open", publicationStatus: "needs_review",
    sourceId: "test", matchMode: "review_required", criteriaVersion: "eligibility-1.5.0", structuredRuleCount: 0,
    unresolvedRuleCount: 0, recordKind: "notice", presentationJson: "{}", evidenceSourcesJson: "[]",
    fundingTagsJson: JSON.stringify(tags), fundingEvidenceJson: JSON.stringify(tags.map(tag => ({tag, text: "생활비 장학금 지원", field: "benefit"}))),
  };
}
async function sync(body) {
  const response = await POST(new Request("http://test/api/admin/sync", {method: "POST", headers: {"authorization": "Bearer test-token", "content-type": "application/json"}, body: JSON.stringify({resource: "opportunities", datasetVersion: "test-v1", ...body})}));
  assert.equal(response.status, 200, await response.clone().text());
}
async function get(params = "") {
  const response = await GET(new Request(`http://test/api/opportunities${params ? `?${params}` : ""}`));
  assert.equal(response.status, 200);
  return response.json();
}
let checks = 0;
function check(name, run) { run(); checks++; console.log(`PASS ${name}`); }
try {
  await sync({mode: "start", expectedCount: 3});
  const canonical = {...record("생활비 장학", "foundation", ["scholarship", "living_expenses"]), contractVersion: "opportunity-1.0.1", opportunityVersionId: "version-1", conditionSectionsJson: JSON.stringify({preferences: ["우대 조건"], requirements: []}), requiredDocumentsJson: JSON.stringify(["재학증명서"]), applicationPromptsJson: JSON.stringify([{id: "prompt-1", opportunityVersionId: "version-1", promptKey: "prompt-1", text: "지원 동기", characterLimit: 800, sourceUrl: "https://official.test/form", reviewStatus: "needs_review"}]), structuredBenefitsJson: JSON.stringify([{type: "cash", description: "최대 월 50만원", amountMin: null, amountMax: 500000, currency: "KRW", recurrence: "month", amountQualifier: "up_to"}])};
  await sync({mode: "chunk", rows: [canonical, record("기업 수당", "company", ["cash"]), record("무료 교육", "national", [])]});
  await sync({mode: "finish"});
  const defaultResult = await get();
  check("new canonical fields survive sync and API without losing source or qualifier", () => {
    const row = defaultResult.data.find(item => item.id === canonical.id);
    assert.equal(row.opportunityVersionId, "version-1"); assert.equal(row.contractVersion, "opportunity-1.0.1");
    assert.deepEqual(row.conditionSections.requirements, []); assert.deepEqual(row.requiredDocuments, ["재학증명서"]);
    assert.equal(row.applicationPrompts[0].opportunityVersionId, row.opportunityVersionId);
    assert.equal(row.applicationPrompts[0].sourceUrl, "https://official.test/form");
    assert.equal(row.structuredBenefits[0].amountMin, null); assert.equal(row.structuredBenefits[0].amountQualifier, "up_to");
    assert.equal(defaultResult.meta.collectedAt, "2026-10-01T00:00:00Z");
    assert.ok(defaultResult.meta.synchronizedAt);
  });
  check("default filters financial records using all benefit tags", () => {
    assert.equal(defaultResult.meta.total, 2);
    assert.equal(defaultResult.meta.registeredTotal, 3);
    assert.equal(defaultResult.meta.financialTotal, 2);
    assert.equal(defaultResult.meta.storage, "d1");
    assert.equal(defaultResult.meta.operatorCounts.foundation, 1);
  });
  const living = await get("fundingTarget=living_expenses");
  const scholarship = await get("fundingTarget=scholarship");
  check("one record is searchable under each of its benefit tags", () => {
    assert.equal(living.meta.total, 1); assert.equal(scholarship.meta.total, 1);
    assert.equal(living.data[0].id, scholarship.data[0].id);
    assert.equal(living.data[0].fundingEvidence[0].text, "생활비 장학금 지원");
  });
  const company = await get("operatorGroup=company");
  check("operator filter uses provider not school collection source", () => {
    assert.equal(company.meta.total, 1); assert.equal(company.data[0].sourceGroup, "school");
    assert.equal(company.data[0].operatorGroup, "company");
    assert.equal(company.meta.operatorCounts.foundation, 1);
  });
  const all = await get("fundingTarget=all");
  check("nonfinancial records remain in all archived candidates", () => assert.equal(all.meta.total, 3));
  const invalid = await get("fundingTarget=__proto__&operatorGroup=invalid");
  check("invalid scope safely defaults to financial", () => assert.equal(invalid.meta.total, 2));
  const absent = await get("fundingTarget=tuition");
  check("empty financial target does not return fixture records", () => {
    assert.equal(absent.meta.storage, "d1"); assert.equal(absent.meta.total, 0); assert.deepEqual(absent.data, []);
  });
  const paged = await get("pageSize=1&page=2");
  check("pagination counts unique records not expanded tags", () => { assert.equal(paged.meta.total, 2); assert.equal(paged.meta.pageCount, 2); assert.equal(paged.data.length, 1); });
  check("shared funding matcher accepts each tag and excludes unclassified", () => {
    assert.equal(funding.matchesFunding(["scholarship", "living_expenses"], "living_expenses"), true);
    assert.equal(funding.matchesFunding([], "financial"), false);
    assert.equal(funding.matchesFunding([], "all"), true);
  });
  const approvedEmpty = await get("comparisonMode=approved_read");
  check("approved read and aggregates exclude all review candidates", () => {
    assert.equal(approvedEmpty.meta.total, 0); assert.equal(approvedEmpty.meta.registeredTotal, 0);
    assert.deepEqual(approvedEmpty.meta.operatorCounts, {}); assert.deepEqual(approvedEmpty.meta.lifecycleCounts, {});
  });
  sqlite.exec("UPDATE opportunity_registry SET publication_status='reviewed',rights_status='approved',production_allowed=1 WHERE operator_group='foundation'");
  const approved = await get("comparisonMode=approved_read");
  check("approved data and every aggregate use identical publication gates", () => {
    assert.equal(approved.meta.total, 1); assert.equal(approved.meta.registeredTotal, 1); assert.equal(approved.meta.financialTotal, 1);
    assert.deepEqual(approved.meta.operatorCounts, {foundation: 1}); assert.deepEqual(approved.meta.lifecycleCounts, {open: 1});
    assert.equal(approved.data[0].productionAllowed, true);
  });
  env.DB = undefined;
  const unavailable = await GET(new Request("http://test/api/opportunities?comparisonMode=approved_read"));
  check("approved read fails closed on database absence", () => assert.equal(unavailable.status, 503));
  env.DB = DB;
  const originalLog = console.error;
  try {
    console.error = () => {};
    env.DB = {prepare() {throw new Error("test database failure");}};
    const failed = await GET(new Request("http://test/api/opportunities?comparisonMode=approved_read"));
    check("approved read fails closed on query failure", () => assert.equal(failed.status, 503));
  } finally { env.DB = DB; console.error = originalLog; }
} finally { sqlite.close(); }
console.log(`${checks} checks passed`);
