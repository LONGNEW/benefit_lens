export type OriginalIngestion = {
  schema_version: "original-ingestion-1";
  collected_at: string;
  web_sync_status: string;
  access_policy?: {robots_mode?: string; follow_public_redirects?: boolean};
  access_retry?: {finished_at: string; retried_candidates: number; recovered_candidates: number; official_origin_candidates: number; still_failed_candidates: number; new_document_count: number; new_candidate_opportunities?: number; existing_candidates_preserved?: number};
  counts: {processed_candidates: number; postgres_raw_documents: number; postgres_unique_raw_documents?: number; unique_content_hashes?: number; official_documents: number; new_candidate_opportunities: number; before_opportunities: number; after_opportunities: number; existing_opportunities_preserved: number; statuses: Record<string, number>};
  results: {candidate_key: string; status: string; origins: string[]; documents: string[]; edges: {url: string; status: string; parent_url: string | null; relation: string}[]}[];
  documents: {url: string; final_url: string; title: string; kind: string; role: string; sha256: string | null; text_length: number; links: {url: string; relation: string}[]}[];
  outcomes: {original_url: string; title: string; canonical_key: string; status: string; publication_status?: string}[];
};
const link = (value: unknown) => {try {const u = new URL(String(value)); return u.protocol === "https:" && !u.username && !u.password;} catch {return false;}};
export function isOriginalIngestion(value: unknown): value is OriginalIngestion {
  if (!value || typeof value !== "object") return false;
  const v = value as OriginalIngestion;
  if (v.access_retry && (!Number.isInteger(v.access_retry.retried_candidates) || !["recovered_candidates","official_origin_candidates","still_failed_candidates","new_document_count"].every(k => Number.isInteger(v.access_retry?.[k as keyof NonNullable<typeof v.access_retry>]) && Number(v.access_retry?.[k as keyof NonNullable<typeof v.access_retry>]) >= 0) || v.access_retry.recovered_candidates + v.access_retry.still_failed_candidates > v.access_retry.retried_candidates || v.access_retry.official_origin_candidates > v.access_retry.recovered_candidates || typeof v.access_retry.finished_at !== "string")) return false;
  if (v.access_policy?.robots_mode && !["respect","observe"].includes(v.access_policy.robots_mode)) return false;
  return v.schema_version === "original-ingestion-1" && typeof v.collected_at === "string" && typeof v.web_sync_status === "string" && !!v.counts
    && ["processed_candidates", "postgres_raw_documents", "official_documents", "new_candidate_opportunities", "before_opportunities", "after_opportunities", "existing_opportunities_preserved"].every(k => Number.isInteger(v.counts[k as keyof typeof v.counts]) && Number(v.counts[k as keyof typeof v.counts]) >= 0)
    && !!v.counts.statuses && Array.isArray(v.results) && Array.isArray(v.documents) && Array.isArray(v.outcomes)
    && v.results.length === v.counts.processed_candidates && v.results.every(r => link(r.candidate_key) && typeof r.status === "string" && Array.isArray(r.origins) && r.origins.every(link) && Array.isArray(r.edges))
    && v.documents.every(d => link(d.url) && link(d.final_url) && typeof d.title === "string" && typeof d.kind === "string" && Array.isArray(d.links))
    && v.outcomes.every(o => link(o.original_url) && typeof o.canonical_key === "string" && typeof o.status === "string");
}
export const originalStatuses: Record<string, string> = {
  official_origin_collected: "기관 원문 연결 완료", collected_without_resolved_official_origin: "자료 확보, 발행기관 원문 미확정",
  fetch_or_access_failed: "원문 접근 실패", experience_or_social_metadata_only: "경험 또는 소셜 자료, 주소만 기록",
  authentication_not_attempted: "로그인 자료, 인증 미시도", non_opportunity_statistics_or_loan: "통계 또는 대출 자료 제외", non_https_or_unsafe_url: "안전한 HTTPS 원문 미확보",
};
