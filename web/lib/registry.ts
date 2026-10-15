import type { Opportunity } from "./opportunity-data";

export type OpportunityMeta = {
  total: number; page: number; pageSize: number; pageCount: number;
  storage?: string; datasetStatus?: string; datasetVersion?: string | null;
  collectedAt?: string | null; lifecycleCounts?: Record<string, number>;
  synchronizedAt?: string | null;
  fundingTarget?: string; registeredTotal?: number; financialTotal?: number;
  operatorCounts?: Record<string, number>;
};

// Personal attributes never leave the browser. Load the complete search result
// before matching and paging it; incomplete snapshots must fail visibly.
export async function fetchRegistry(params: URLSearchParams, signal: AbortSignal) {
  const data: Opportunity[] = [];
  let first: OpportunityMeta | null = null;
  for (let page = 1; page <= (first?.pageCount ?? 1); page++) {
    if (page > 100) throw new Error("registry exceeds client matching limit");
    const query = new URLSearchParams(params);
    query.set("page", String(page)); query.set("pageSize", "100");
    const response = await fetch(`/api/opportunities?${query}`, { cache: "no-store", signal });
    if (!response.ok) throw new Error("registry unavailable");
    const payload = await response.json() as { data: Opportunity[]; meta: OpportunityMeta };
    if (!Array.isArray(payload.data) || !payload.meta) throw new Error("invalid registry");
    if (payload.meta.datasetStatus === "syncing") throw new Error("registry is syncing; retry after completion");
    if (!first) first = payload.meta;
    else if (payload.meta.datasetVersion !== first.datasetVersion || payload.meta.total !== first.total || payload.meta.page !== page) throw new Error("registry changed during loading");
    data.push(...payload.data);
  }
  if (!first || data.length !== first.total || new Set(data.map(item => item.id)).size !== data.length) throw new Error("incomplete registry");
  return { data, meta: first };
}
