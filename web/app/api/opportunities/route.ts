import { env } from "cloudflare:workers";
import { opportunities } from "@/lib/opportunity-data";
import type { EligibilityGroup, Opportunity, Rule } from "@/lib/opportunity-data";
import { isParticipationRequirement, separateConditions } from "@/lib/eligibility";
import { fundingTargetLabels, matchesFunding, type FundingTarget, type FundingTag, type FundingEvidence } from "@/lib/funding";

type OpportunityRow = {
  id: string;
  name: string;
  organization: string;
  support: string;
  category: string;
  source_group: Opportunity["sourceGroup"];
  operator_group: Opportunity["operatorGroup"];
  rules_json: string;
  criteria_json: string;
  unresolved_criteria_json: string;
  caveat: string | null;
  source_url: string;
  source_updated_at: string;
  summary: string;
  application_url: string | null;
  eligibility_json: string;
  benefit_type: string;
  lifecycle_status: Opportunity["lifecycleStatus"];
  publication_status: string;
  rights_status: string;
  production_allowed: number;
  contract_version: string;
  condition_sections_json: string;
  required_documents_json: string;
  application_prompts_json: string;
  structured_benefits_json: string;
  opportunity_version_id: string;
  source_id: string;
  match_mode: Opportunity["matchMode"];
  criteria_version: string;
  structured_rule_count: number;
  unresolved_rule_count: number;
  opens_at: string | null;
  closes_at: string | null;
  record_kind: Opportunity["recordKind"];
  presentation_json: string;
  evidence_sources_json: string;
  funding_tags_json: string;
  funding_evidence_json: string;
};

function parseJson<T>(value: string, fallback: T) {
  try {
    const parsed = JSON.parse(value);
    return parsed as T;
  } catch {
    return fallback;
  }
}

function fallbackResponse(query = "", lifecycle = "all", operatorGroup = "all", benefitType = "all", fundingTarget: FundingTarget = "financial", comparisonMode = "demo_candidate") {
  if (comparisonMode === "approved_read") return Response.json({data: [], meta: {total: 0, comparisonMode, storage: "unavailable", datasetStatus: "unavailable", rightsStatus: "unknown", lifecycleCounts: {}, operatorCounts: {}, registeredTotal: 0, financialTotal: 0}}, {status: 503, headers: {"Cache-Control": "no-store"}});
  const normalizedQuery = query.toLocaleLowerCase("ko-KR");
  const data = opportunities.filter((item) => {
    const haystack = `${item.name} ${item.organization} ${item.support} ${item.summary ?? ""}`.toLocaleLowerCase("ko-KR");
    return (!normalizedQuery || haystack.includes(normalizedQuery))
      && (lifecycle === "all" || item.lifecycleStatus === lifecycle)
      && (operatorGroup === "all" || (item.operatorGroup ?? item.sourceGroup) === operatorGroup)
      && (benefitType === "all" || item.benefitType === benefitType)
      && matchesFunding(item.fundingTags, fundingTarget);
  });
  return Response.json({
    data: data.map(item => ({ ...item, matchMode: "review_required", caveat: "연결 실패 시 표시하는 예시입니다. 실제 접수 판정에는 사용하지 않습니다." })),
    meta: {
      total: data.length,
      page: 1,
      pageSize: data.length,
      pageCount: 1,
      storage: "fixture-fallback",
      rightsStatus: "not-reviewed",
      datasetStatus: "fallback",
      comparisonMode,
      lifecycleCounts: {},
      fundingTarget,
      registeredTotal: opportunities.length,
      financialTotal: opportunities.filter(item => matchesFunding(item.fundingTags, "financial")).length,
      collectedAt: opportunities[0]?.sourceUpdatedAt ?? null,
    },
  }, { headers: { "Cache-Control": "no-store" } });
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const query = (url.searchParams.get("q") ?? "").trim().slice(0, 100);
  const lifecycle = url.searchParams.get("lifecycle") ?? "all";
  const allowedLifecycle = new Set(["all", "upcoming", "open", "rolling", "closed", "unknown"]);
  const selectedLifecycle = allowedLifecycle.has(lifecycle) ? lifecycle : "all";
  const operatorGroup = url.searchParams.get("operatorGroup") ?? "all";
  const allowedOperatorGroups = new Set(["all", "school", "foundation", "company", "national", "other"]);
  const selectedOperatorGroup = allowedOperatorGroups.has(operatorGroup) ? operatorGroup : "all";
  const benefitType = url.searchParams.get("benefitType") ?? "all";
  const allowedBenefitTypes = new Set(["all", "cash", "education_cost", "housing_finance", "goods", "service", "opportunity", "unknown"]);
  const selectedBenefitType = allowedBenefitTypes.has(benefitType) ? benefitType : "all";
  const requestedFundingTarget = url.searchParams.get("fundingTarget") ?? "financial";
  const fundingTarget: FundingTarget = Object.hasOwn(fundingTargetLabels, requestedFundingTarget) ? requestedFundingTarget as FundingTarget : "financial";
  const comparisonMode = url.searchParams.get("comparisonMode") === "approved_read" ? "approved_read" : "demo_candidate";
  if (!env.DB) return fallbackResponse(query, selectedLifecycle, selectedOperatorGroup, selectedBenefitType, fundingTarget, comparisonMode);
  const page = Math.max(1, Number.parseInt(url.searchParams.get("page") ?? "1", 10) || 1);
  const pageSize = Math.min(100, Math.max(1, Number.parseInt(url.searchParams.get("pageSize") ?? "12", 10) || 12));
  const search = `%${query}%`;
  const searchWhere = "(? = '' OR name LIKE ? OR organization LIKE ? OR support LIKE ? OR summary LIKE ?)";
  const fundingWhere = "(? = 'all' OR (? = 'financial' AND json_array_length(funding_tags_json) > 0) OR EXISTS (SELECT 1 FROM json_each(funding_tags_json) WHERE value = ?))";
  const approvalWhere = comparisonMode === "approved_read" ? "publication_status = 'reviewed' AND rights_status = 'approved' AND production_allowed = 1" : "1 = 1";
  const where = `${approvalWhere} AND ${searchWhere} AND (? = 'all' OR lifecycle_status = ?) AND (? = 'all' OR operator_group = ?) AND (? = 'all' OR benefit_type = ?) AND ${fundingWhere}`;

  try {
    const searchBindings = [query, search, search, search, search];
    const fundingBindings = [fundingTarget, fundingTarget, fundingTarget];
    const count = await env.DB.prepare(`SELECT count(*) AS count FROM opportunity_registry WHERE ${where}`)
      .bind(...searchBindings, selectedLifecycle, selectedLifecycle, selectedOperatorGroup, selectedOperatorGroup, selectedBenefitType, selectedBenefitType, ...fundingBindings)
      .first<{ count: number }>();
    const total = Number(count?.count ?? 0);
    const pageCount = Math.max(1, Math.ceil(total / pageSize));
    const safePage = Math.min(page, pageCount);
    const rows = await env.DB.prepare(`SELECT * FROM opportunity_registry WHERE ${where} ORDER BY CASE lifecycle_status WHEN 'open' THEN 0 WHEN 'rolling' THEN 1 WHEN 'upcoming' THEN 2 WHEN 'unknown' THEN 3 ELSE 4 END, source_updated_at DESC, name LIMIT ? OFFSET ?`)
      .bind(...searchBindings, selectedLifecycle, selectedLifecycle, selectedOperatorGroup, selectedOperatorGroup, selectedBenefitType, selectedBenefitType, ...fundingBindings, pageSize, (safePage - 1) * pageSize)
      .all<OpportunityRow>();
    const counts = await env.DB.prepare(`SELECT lifecycle_status, count(*) AS count FROM opportunity_registry WHERE ${approvalWhere} AND ${searchWhere} AND (? = 'all' OR operator_group = ?) AND (? = 'all' OR benefit_type = ?) AND ${fundingWhere} GROUP BY lifecycle_status`)
      .bind(...searchBindings, selectedOperatorGroup, selectedOperatorGroup, selectedBenefitType, selectedBenefitType, ...fundingBindings)
      .all<{ lifecycle_status: string; count: number }>();
    const operatorCounts = await env.DB.prepare(`SELECT operator_group, count(*) AS count FROM opportunity_registry WHERE ${approvalWhere} AND ${searchWhere} AND (? = 'all' OR lifecycle_status = ?) AND (? = 'all' OR benefit_type = ?) AND ${fundingWhere} GROUP BY operator_group`)
      .bind(...searchBindings, selectedLifecycle, selectedLifecycle, selectedBenefitType, selectedBenefitType, ...fundingBindings)
      .all<{ operator_group: string; count: number }>();
    const scopeCounts = await env.DB.prepare(`SELECT count(*) AS registered, max(source_updated_at) AS collected_at, COALESCE(sum(CASE WHEN json_array_length(funding_tags_json) > 0 THEN 1 ELSE 0 END), 0) AS financial FROM opportunity_registry WHERE ${approvalWhere}`)
      .first<{ registered: number; financial: number; collected_at: string | null }>();
    const state = await env.DB.prepare("SELECT dataset_version, status, updated_at FROM sync_state WHERE resource = 'opportunities'")
      .first<{ dataset_version: string; status: string; updated_at: string }>();
    const data = rows.results.map((row): Opportunity => ({
      id: row.id,
      name: row.name,
      organization: row.organization,
      support: row.support,
      category: row.category,
      sourceGroup: row.source_group,
      operatorGroup: row.operator_group,
      rules: parseJson<Rule[]>(row.rules_json, []),
      criteria: parseJson<EligibilityGroup | null>(row.criteria_json, null),
      unresolvedCriteria: parseJson<string[]>(row.unresolved_criteria_json, []),
      eligibilityNotes: parseJson<string[]>(row.eligibility_json, []),
      caveat: row.caveat ?? undefined,
      sourceUrl: row.source_url,
      sourceUpdatedAt: row.source_updated_at,
      summary: row.summary,
      applicationUrl: row.application_url ?? undefined,
      benefitType: row.benefit_type,
      fundingTags: parseJson<FundingTag[]>(row.funding_tags_json, []),
      fundingEvidence: parseJson<FundingEvidence[]>(row.funding_evidence_json, []),
      lifecycleStatus: row.lifecycle_status,
      publicationStatus: row.publication_status,
      rightsStatus: row.rights_status,
      productionAllowed: row.production_allowed === 1,
      contractVersion: row.contract_version,
      opportunityVersionId: row.opportunity_version_id,
      conditionSections: parseJson(row.condition_sections_json, {}),
      requiredDocuments: parseJson(row.required_documents_json, []),
      applicationPrompts: parseJson(row.application_prompts_json, []),
      structuredBenefits: parseJson(row.structured_benefits_json, []),
      sourceId: row.source_id,
      matchMode: row.match_mode,
      criteriaVersion: row.criteria_version,
      structuredRuleCount: parseJson<Rule[]>(row.rules_json, []).filter(rule => !isParticipationRequirement(rule)).length,
      unresolvedRuleCount: Number(row.unresolved_rule_count),
      opensAt: row.opens_at ?? undefined,
      closesAt: row.closes_at ?? undefined,
      recordKind: row.record_kind,
      presentation: parseJson(row.presentation_json, {}),
      evidenceSources: parseJson(row.evidence_sources_json, []),
    })).map(item => ({...item, ...separateConditions(item)}));
    return Response.json({
      data,
      meta: {
        total,
        page: safePage,
        pageSize,
        pageCount,
        storage: "d1",
        rightsStatus: comparisonMode === "approved_read" ? "approved" : "review-required",
        comparisonMode,
        datasetStatus: state?.status ?? "unknown",
        datasetVersion: state?.dataset_version ?? null,
        collectedAt: scopeCounts?.collected_at ?? null,
        synchronizedAt: state?.updated_at ?? null,
        fundingTarget,
        registeredTotal: Number(scopeCounts?.registered ?? 0),
        financialTotal: Number(scopeCounts?.financial ?? 0),
        operatorCounts: Object.fromEntries(operatorCounts.results.map(item => [item.operator_group, Number(item.count)])),
        lifecycleCounts: Object.fromEntries(counts.results.map((item) => [item.lifecycle_status, Number(item.count)])),
      },
    }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    console.error("Opportunity registry D1 fallback", error);
    return fallbackResponse(query, selectedLifecycle, selectedOperatorGroup, selectedBenefitType, fundingTarget, comparisonMode);
  }
}
