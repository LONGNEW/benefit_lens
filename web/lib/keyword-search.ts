export type KeywordQuery = {
  id: string; q: string; group: string; group_label: string;
  stage: "initial" | "adaptive"; status: string; searched_at?: string; searched_on?: string;
  unique_url_keys: number; marginal_url_keys: number; marginal_host_keys: number;
  result_url_keys: string[];
  derived_from?: {source_query_id: string; source_url: string; observed_term: string};
};

export type KeywordCandidate = {
  url_key: string; source_url: string; title: string; label_truncated: boolean;
  host_key: string; query_ids: string[]; role_hint: string; scope_hint: string;
  new_url_vs_baseline: boolean; new_host_vs_baseline: boolean;
  topic_keyword_match: boolean; noise_keyword_match: boolean; restricted_path_hint: boolean;
  original_status: string; capture_mode: "search_metadata_only";
  official_status: "not_verified"; program_id: null;
};

export type KeywordSearch = {
  schema_version: "keyword-search-1"; searched_on: string;
  counts: {
    planned_queries: number; executed_queries: number; failed_queries: number;
    initial_queries: number; adaptive_queries: number; returned_url_mentions: number;
    unique_url_keys: number; new_url_keys_vs_baseline: number; host_keys: number;
    new_host_keys_vs_baseline: number; topic_keyword_candidates: number;
    noise_keyword_candidates: number; restricted_path_candidates: number;
    adaptive_marginal_urls: number; verified_programs: null; new_originals_fetched_by_this_module: number;
  };
  baseline: {known_url_keys: number; known_host_keys: number};
  groups: {id: string; label: string; queries: number; unique_url_keys: number; marginal_url_keys: number}[];
  queries: KeywordQuery[]; candidates: KeywordCandidate[];
  stop_review: {coverage_complete: boolean; saturation_confirmed: boolean};
};

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
function safeLink(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password && !/jsessionid/i.test(value);
  } catch {return false;}
}
export function isKeywordSearch(value: unknown): value is KeywordSearch {
  if (!record(value) || value.schema_version !== "keyword-search-1" || !record(value.counts) || !record(value.baseline) || !record(value.stop_review) || !Array.isArray(value.queries) || !Array.isArray(value.candidates) || !Array.isArray(value.groups)) return false;
  const c = value.counts;
  const numeric = ["planned_queries", "executed_queries", "failed_queries", "initial_queries", "adaptive_queries", "returned_url_mentions", "unique_url_keys", "new_url_keys_vs_baseline", "host_keys", "new_host_keys_vs_baseline", "topic_keyword_candidates", "noise_keyword_candidates", "restricted_path_candidates", "adaptive_marginal_urls", "new_originals_fetched_by_this_module"];
  if (!numeric.every(key => typeof c[key] === "number" && Number.isInteger(c[key]) && (c[key] as number) >= 0) || c.verified_programs !== null) return false;
  if (value.queries.length !== c.executed_queries || value.candidates.length !== c.unique_url_keys || typeof value.searched_on !== "string" || typeof value.baseline.known_url_keys !== "number" || typeof value.baseline.known_host_keys !== "number") return false;
  if (!value.queries.every(q => record(q) && typeof q.id === "string" && typeof q.q === "string" && typeof q.group === "string" && ["initial", "adaptive"].includes(String(q.stage)) && typeof q.unique_url_keys === "number" && typeof q.marginal_url_keys === "number" && typeof q.marginal_host_keys === "number" && Array.isArray(q.result_url_keys) && q.result_url_keys.every(safeLink) && (q.stage !== "adaptive" || (record(q.derived_from) && typeof q.derived_from.source_query_id === "string" && typeof q.derived_from.observed_term === "string" && safeLink(q.derived_from.source_url))))) return false;
  if (!value.candidates.every(item => record(item) && safeLink(item.url_key) && safeLink(item.source_url) && typeof item.title === "string" && item.title.length <= 160 && typeof item.role_hint === "string" && Array.isArray(item.query_ids) && item.capture_mode === "search_metadata_only" && item.official_status === "not_verified" && item.program_id === null)) return false;
  return value.groups.every(g => record(g) && typeof g.id === "string" && typeof g.label === "string" && typeof g.queries === "number") && new Set(value.queries.map(q => q.id)).size === value.queries.length && new Set(value.candidates.map(item => item.url_key)).size === value.candidates.length;
}

export function queriesForGroup(data: KeywordSearch, group: string) {
  return data.queries.filter(query => group === "all" || query.group === group);
}
export function priorityCandidate(item: KeywordCandidate) {
  return item.topic_keyword_match && !item.noise_keyword_match && !item.restricted_path_hint;
}
export function resultsForQuery(data: KeywordSearch, query: KeywordQuery, priorityOnly = false) {
  const keys = new Set(query.result_url_keys);
  return data.candidates.filter(item => keys.has(item.url_key) && (!priorityOnly || priorityCandidate(item)));
}
