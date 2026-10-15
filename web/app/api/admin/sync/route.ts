import { env } from "cloudflare:workers";

type SyncMode = "start" | "chunk" | "finish";

type SyncRequest = {
  mode?: SyncMode;
  resource?: "opportunities";
  datasetVersion?: string;
  expectedCount?: number;
  rows?: Array<Record<string, unknown>>;
};

const opportunityColumns = [
  "id", "name", "organization", "support", "category", "source_group", "operator_group",
  "rules_json", "criteria_json", "unresolved_criteria_json", "caveat",
  "source_url", "source_updated_at", "summary", "application_url", "eligibility_json",
  "benefit_type", "lifecycle_status", "publication_status", "source_id", "match_mode",
  "criteria_version", "structured_rule_count", "unresolved_rule_count", "opens_at", "closes_at",
  "record_kind", "presentation_json", "evidence_sources_json",
  "funding_tags_json", "funding_evidence_json",
  "rights_status", "production_allowed", "contract_version", "condition_sections_json",
  "required_documents_json", "application_prompts_json", "structured_benefits_json", "opportunity_version_id",
] as const;

function jsonError(message: string, status: number) {
  return Response.json({ error: message }, { status });
}

function authorized(request: Request) {
  const configured = env.D1_SYNC_TOKEN;
  const supplied = request.headers.get("authorization");
  return Boolean(configured && supplied === `Bearer ${configured}`);
}

function requiredString(row: Record<string, unknown>, key: string) {
  const value = row[key];
  if (typeof value !== "string" || !value.trim()) throw new Error(`${key} is required`);
  return value;
}

function nullableString(value: unknown) {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function nonNegativeInteger(row: Record<string, unknown>, key: string) {
  const value = Number(row[key]);
  if (!Number.isInteger(value) || value < 0) throw new Error(`${key} must be a non-negative integer`);
  return value;
}

function opportunityValues(row: Record<string, unknown>) {
  return [
    requiredString(row, "id"), requiredString(row, "name"), requiredString(row, "organization"),
    requiredString(row, "support"), requiredString(row, "category"), requiredString(row, "sourceGroup"),
    requiredString(row, "operatorGroup"),
    requiredString(row, "rulesJson"), requiredString(row, "criteriaJson"), requiredString(row, "unresolvedCriteriaJson"),
    nullableString(row.caveat), requiredString(row, "sourceUrl"), requiredString(row, "sourceUpdatedAt"),
    nullableString(row.summary) ?? "", nullableString(row.applicationUrl),
    requiredString(row, "eligibilityJson"), requiredString(row, "benefitType"),
    requiredString(row, "lifecycleStatus"), requiredString(row, "publicationStatus"),
    requiredString(row, "sourceId"), requiredString(row, "matchMode"),
    requiredString(row, "criteriaVersion"), nonNegativeInteger(row, "structuredRuleCount"),
    nonNegativeInteger(row, "unresolvedRuleCount"), nullableString(row.opensAt), nullableString(row.closesAt),
    requiredString(row, "recordKind"), requiredString(row, "presentationJson"),
    requiredString(row, "evidenceSourcesJson"),
    requiredString(row, "fundingTagsJson"), requiredString(row, "fundingEvidenceJson"),
    nullableString(row.rightsStatus) ?? "review_required", row.productionAllowed === true ? 1 : 0,
    nullableString(row.contractVersion) ?? "opportunity-legacy", nullableString(row.conditionSectionsJson) ?? "{}",
    nullableString(row.requiredDocumentsJson) ?? "[]", nullableString(row.applicationPromptsJson) ?? "[]",
    nullableString(row.structuredBenefitsJson) ?? "[]",
    nullableString(row.opportunityVersionId) ?? "",
  ];
}

function upsertStatement(row: Record<string, unknown>) {
  const columns = opportunityColumns;
  const values = opportunityValues(row);
  const assignments = columns.slice(1).map((column) => `${column}=excluded.${column}`).join(", ");
  const sql = `INSERT INTO opportunity_registry_stage (${columns.join(", ")}) VALUES (${columns.map(() => "?").join(", ")}) ON CONFLICT(id) DO UPDATE SET ${assignments}`;
  return env.DB!.prepare(sql).bind(...values);
}

export async function POST(request: Request) {
  if (!env.DB) return jsonError("D1 binding unavailable", 503);
  if (!authorized(request)) return jsonError("unauthorized", 401);

  let body: SyncRequest;
  try {
    body = await request.json() as SyncRequest;
  } catch {
    return jsonError("invalid JSON", 400);
  }
  const { mode, resource, datasetVersion } = body;
  if (!mode || !["start", "chunk", "finish"].includes(mode)) return jsonError("invalid mode", 400);
  if (resource !== "opportunities") return jsonError("invalid resource", 400);
  if (!datasetVersion || datasetVersion.length > 120) return jsonError("invalid datasetVersion", 400);

  const stageTable = "opportunity_registry_stage";
  const targetTable = "opportunity_registry";
  const columns = opportunityColumns;
  const now = new Date().toISOString();

  try {
    if (mode === "start") {
      const expectedCount = Number(body.expectedCount);
      if (!Number.isInteger(expectedCount) || expectedCount < 0) return jsonError("invalid expectedCount", 400);
      await env.DB.batch([
        env.DB.prepare(`DELETE FROM ${stageTable}`),
        env.DB.prepare(`INSERT INTO sync_state (resource, dataset_version, expected_count, loaded_count, status, updated_at) VALUES (?, ?, ?, 0, 'syncing', ?) ON CONFLICT(resource) DO UPDATE SET dataset_version=excluded.dataset_version, expected_count=excluded.expected_count, loaded_count=0, status='syncing', updated_at=excluded.updated_at`).bind(resource, datasetVersion, expectedCount, now),
      ]);
      return Response.json({ resource, mode, expectedCount, status: "syncing" });
    }

    if (mode === "chunk") {
      const rows = Array.isArray(body.rows) ? body.rows : [];
      if (rows.length < 1 || rows.length > 50) return jsonError("rows must contain 1 to 50 items", 400);
      const state = await env.DB.prepare("SELECT dataset_version, status FROM sync_state WHERE resource = ?").bind(resource).first<{ dataset_version: string; status: string }>();
      if (!state || state.dataset_version !== datasetVersion || state.status !== "syncing") return jsonError("sync state mismatch", 409);
      await env.DB.batch(rows.map((row) => upsertStatement(row)));
      const count = await env.DB.prepare(`SELECT count(*) AS count FROM ${stageTable}`).first<{ count: number }>();
      await env.DB.prepare("UPDATE sync_state SET loaded_count = ?, updated_at = ? WHERE resource = ?").bind(Number(count?.count ?? 0), now, resource).run();
      return Response.json({ resource, mode, loadedCount: Number(count?.count ?? 0) });
    }

    const state = await env.DB.prepare("SELECT expected_count, dataset_version, status FROM sync_state WHERE resource = ?").bind(resource).first<{ expected_count: number; dataset_version: string; status: string }>();
    const count = await env.DB.prepare(`SELECT count(*) AS count FROM ${stageTable}`).first<{ count: number }>();
    const loadedCount = Number(count?.count ?? 0);
    if (!state || state.dataset_version !== datasetVersion || state.status !== "syncing") return jsonError("sync state mismatch", 409);
    if (loadedCount !== Number(state.expected_count)) return jsonError(`loaded ${loadedCount}, expected ${state.expected_count}`, 409);
    await env.DB.batch([
      env.DB.prepare(`DELETE FROM ${targetTable}`),
      env.DB.prepare(`INSERT INTO ${targetTable} (${columns.join(", ")}) SELECT ${columns.join(", ")} FROM ${stageTable}`),
      env.DB.prepare(`DELETE FROM ${stageTable}`),
      env.DB.prepare("UPDATE sync_state SET loaded_count = ?, status = 'complete', updated_at = ? WHERE resource = ?").bind(loadedCount, now, resource),
    ]);
    return Response.json({ resource, mode, loadedCount, status: "complete" });
  } catch (error) {
    console.error("D1 sync failed", error);
    return jsonError(error instanceof Error ? error.message : "sync failed", 500);
  }
}
