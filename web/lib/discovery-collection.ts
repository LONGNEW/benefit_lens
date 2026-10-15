export type DiscoverySeed = {
  seed_id: string; route: "catalog" | "search"; url: string; label: string;
  query?: string | null; catalog_row?: string; catalog_product?: string; new_to_registry: boolean;
};
export type DiscoveryDocument = {
  id: string; url: string; final_url: string; title: string; kind: string; role: string;
  capture_mode: string; sha256: string | null; metadata_sha256?: string | null;
  collected_at: string; bytes: number; eligibility_status: string; database_change: string;
};
export type DiscoveryEdge = {
  seed_id: string; route: "catalog" | "search"; url: string; parent_url: string | null;
  label: string; depth: number; status: string; document_id?: string; error?: string;
};
export type CollectionManifest = {
  schema_version: string;
  run: {id: string; started_at: string; finished_at: string; limits: {max_depth: number; max_page_fetches: number; max_pages_per_host: number; delay_seconds: number}; registered_hosts_before_run: string[]};
  catalog: {status: string; page_url: string; title?: string; row_count?: number; operator_names?: number; graduate_rows?: number; sha256?: string};
  seeds: DiscoverySeed[]; documents: DiscoveryDocument[]; edges: DiscoveryEdge[];
  counts: {catalog_rows: number; collected_urls: number; unique_content_hashes: number; new_host_keys: number; stored_raw_documents: number; metadata_only_documents: number};
  page_http_requests: number;
  access_policies: {url: string; status: string; checked_at: string}[];
  request_log: {url: string; requested_at: string; response_status: number | null; redirect_to: string | null}[];
  search_batch: {provider: string; searched_on: string; selection: string; candidates: {query: string; url: string; label: string}[]};
};

export const discoveryRoles: Record<string, string> = {
  institution: "기관 안내", listing: "공고 목록", notice_candidate: "공고 후보",
  guideline_or_form: "작성 안내 또는 양식", experience: "경험 자료",
  experience_listing: "경험 자료 목록", recipient_profile: "선정자 연구 소개", unclassified: "역할 분류 대기",
};
export const discoveryStatuses: Record<string, string> = {
  collected: "확보", reused_in_run: "동일 원문 연결", visit_limit: "방문 한도",
  external_candidate_not_fetched: "외부 출처 검토 대기", robots_blocked: "접근 정책으로 차단", fetch_failed: "요청 실패",
  access_restricted: "로그인 경로 수집 제외",
};

export function documentsForSeed(data: CollectionManifest, seedId: string): DiscoveryDocument[] {
  const ids = new Set(data.edges.filter(edge => edge.seed_id === seedId).map(edge => edge.document_id).filter(Boolean));
  return data.documents.filter(doc => ids.has(doc.id));
}

export function visibleSeeds(data: CollectionManifest, route: "all" | "catalog" | "search") {
  return data.seeds.filter(seed => route === "all" || seed.route === route);
}

export function metadataIsSafe(doc: DiscoveryDocument): boolean {
  return doc.capture_mode !== "metadata_only" || (doc.sha256 === null && Boolean(doc.metadata_sha256));
}

export function isCollectionManifest(value: unknown): value is CollectionManifest {
  if (!value || typeof value !== "object") return false;
  const data = value as CollectionManifest;
  if (data.schema_version !== "discovery-1" || !data.run || !data.run.limits || !data.catalog || !data.counts || !data.search_batch) return false;
  if (![data.seeds, data.documents, data.edges, data.access_policies, data.request_log].every(Array.isArray)) return false;
  if (typeof data.run.id !== "string" || typeof data.run.finished_at !== "string" || typeof data.catalog.page_url !== "string") return false;
  if (![data.counts.catalog_rows, data.counts.stored_raw_documents, data.counts.metadata_only_documents, data.counts.new_host_keys,
    data.counts.unique_content_hashes, data.page_http_requests, data.run.limits.max_depth, data.run.limits.max_page_fetches,
    data.run.limits.max_pages_per_host, data.run.limits.delay_seconds].every(v => typeof v === "number" && Number.isFinite(v) && v >= 0)) return false;
  return data.seeds.every(s => s && typeof s.seed_id === "string" && typeof s.url === "string" && typeof s.label === "string" && ["catalog", "search"].includes(s.route))
    && data.documents.every(d => d && typeof d.id === "string" && typeof d.title === "string" && typeof d.final_url === "string" && typeof d.kind === "string"
      && typeof d.collected_at === "string" && typeof d.bytes === "number" && metadataIsSafe(d))
    && data.edges.every(e => e && typeof e.seed_id === "string" && typeof e.url === "string" && typeof e.label === "string" && typeof e.status === "string" && typeof e.depth === "number")
    && data.access_policies.every(p => p && typeof p.url === "string" && typeof p.checked_at === "string")
    && data.request_log.every(r => r && typeof r.url === "string" && typeof r.requested_at === "string");
}
