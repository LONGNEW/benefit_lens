"use client";

import {useEffect, useState} from "react";
import {discoveryRoles} from "@/lib/discovery-collection";
import {isKeywordSearch, KeywordQuery, KeywordSearch, queriesForGroup, resultsForQuery} from "@/lib/keyword-search";
import {isOriginalIngestion, OriginalIngestion, originalStatuses} from "@/lib/original-ingestion";
import styles from "./collection.module.css";

export function OriginalTrace({data, candidateKey}: {data: OriginalIngestion; candidateKey: string}) {
  const result = data.results.find(r => r.candidate_key === candidateKey);
  if (!result) return <p>이 주소의 원문 처리 기록이 없습니다.</p>;
  return <div className={styles.origin}><strong>{originalStatuses[result.status] ?? result.status}</strong>
    {result.origins.map(url => {
      const doc = data.documents.find(d => d.url === url);
      const outcome = data.outcomes.find(o => o.original_url === doc?.final_url || o.original_url === url);
      return <div key={url}><p><a href={doc?.final_url ?? url} target="_blank" rel="noreferrer">{doc?.title ?? "발행기관 원문"}</a> ({doc?.kind.toUpperCase()})</p>
        <small>{doc?.text_length.toLocaleString("ko-KR")}자 추출{outcome ? ", 공고 DB 연결" : ", 원문 저장"}</small>
        {outcome && <p><a href={`/benefitlens/opportunity?id=${encodeURIComponent(outcome.canonical_key)}`}>DB 후보 보기</a>, 모집 회차와 자격 검토 상태 유지</p>}
      </div>;
    })}
    <details><summary>발견 주소에서 원문으로 이동한 기록 {result.edges.length}개</summary><ul>{result.edges.map((edge,i) => <li key={`${edge.url}-${i}`}>{["non_document_asset","excluded_non_document"].includes(edge.status) ? <span>원문과 무관한 실행파일 제외</span> : <a href={edge.url} target="_blank" rel="noreferrer">{edge.url}</a>}, {edge.status}{edge.parent_url && <small> / 이전 문서 {edge.parent_url}</small>}</li>)}</ul></details>
  </div>;
}

export function KeywordQueryResults({data, query, priorityOnly = false, originals}: {data: KeywordSearch; query: KeywordQuery; priorityOnly?: boolean; originals?: OriginalIngestion | null}) {
  const results = resultsForQuery(data, query, priorityOnly);
  return <section className={styles.queryResults} aria-label="선택 검색어의 발견 결과">
    <h3>{query.q}</h3>
    <p className={styles.searchStats}>{query.id.toUpperCase()}, {query.stage === "adaptive" ? "결과에서 만든 검색어" : "초기 검색어"} / URL {query.unique_url_keys}개 / 앞선 검색과 비교해 추가 {query.marginal_url_keys}개 / 추가 호스트 {query.marginal_host_keys}개</p>
    {query.derived_from && <div className={styles.origin}><strong>왜 이 검색어를 만들었을까?</strong><p>{query.derived_from.source_query_id.toUpperCase()} 검색에서 <a href={query.derived_from.source_url} target="_blank" rel="noreferrer">{query.derived_from.observed_term}</a>라는 표현을 발견했습니다. 이 사업명 또는 표현에 지역, 문서 유형을 붙여 후속 검색했습니다.</p></div>}
    <p className={styles.searchStats}>표시 {results.length}개. 자료 유형은 제목과 URL에 따른 분류 후보이며, 아래 목록의 순서는 검색 엔진 순위가 아닙니다.</p>
    <div className={styles.searchDocuments}>{results.map(item => <article className={styles.document} key={item.url_key}>
      <div className={styles.tags}><span>{discoveryRoles[item.role_hint] ?? "유형 미분류"}, 분류 후보</span><span>{item.new_url_vs_baseline ? "기존 URL 목록에 없던 주소" : "기존 URL 목록에 있던 주소"}</span>{item.noise_keyword_match && <span>대출, 통계 또는 채용 키워드 감지</span>}{item.restricted_path_hint && <span>접근 정책 검토 대상</span>}</div>
      <h4><a href={item.source_url} target="_blank" rel="noreferrer">{item.title}</a></h4>
      <p className={styles.url}>{item.source_url}</p>
      <p className={styles.searchStats}>{item.original_status === "existing_collection_original" ? "기존 수집에서 원문 보관" : item.original_status === "existing_metadata_reference" ? "기존 수집의 메타데이터 자료" : "이번 검색에서는 제목과 주소만 기록"}{item.label_truncated && ", 긴 검색 표제는 일부만 표시"}{item.scope_hint === "other_university_scope_review" && ", 타교 자료의 적용 범위 확인 필요"}</p>
      {originals && <OriginalTrace data={originals} candidateKey={item.url_key} />}
    </article>)}</div>
    {!results.length && <p>이 필터에 해당하는 URL 후보가 없습니다.</p>}
  </section>;
}

export function KeywordSearchView({data, originals}: {data: KeywordSearch; originals?: OriginalIngestion | null}) {
  const [group, setGroup] = useState("all");
  const [selectedId, setSelectedId] = useState("q086");
  const [priorityOnly, setPriorityOnly] = useState(false);
  const queries = queriesForGroup(data, group);
  const query = queries.find(item => item.id === selectedId) ?? queries[0];
  return <section className={styles.keywordSection} aria-labelledby="keyword-heading">
    <p className="eyebrow">검색 범위 확장, {data.searched_on}</p><h2 id="keyword-heading">100개 검색어, 무엇이 더 발견됐을까?</h2>
    <p>초기 {data.counts.initial_queries}개 검색에, 결과에서 찾은 사업명과 표현으로 만든 {data.counts.adaptive_queries}개 검색을 더했습니다. 검색어를 선택하면 발견 주소와 후속 검색의 근거를 볼 수 있습니다.</p>
    <div className={styles.metrics} aria-label="키워드 검색 실행 결과">
      <div><strong>{data.counts.executed_queries}</strong><span>실제 실행한 서로 다른 검색어</span><small>검색 실패 {data.counts.failed_queries}개</small></div>
      <div><strong>{data.counts.unique_url_keys.toLocaleString("ko-KR")}</strong><span>중복을 정리한 URL 후보</span><small>반환 URL 언급 {data.counts.returned_url_mentions.toLocaleString("ko-KR")}건</small></div>
      <div><strong>{data.counts.adaptive_marginal_urls}</strong><span>후속 20개가 추가한 URL</span><small>계획 순서대로 기존 주소와 비교</small></div>
      <div><strong>{data.counts.topic_keyword_candidates}</strong><span>주제 키워드에 맞는 검토 후보</span><small>공고, 양식과 경험 자료 포함, 미검증</small></div>
    </div>
    <div className={styles.notice}>검색 URL 수와 지원사업 수는 다릅니다. 검색은 발견 주소를 기록하고, 이후 원문 수집과 DB 적재 단계로 연결합니다.</div>
    {originals && <section aria-label="추가 검색의 원문 수집과 DB 적재"><h3>발견 주소에서 원문과 DB까지</h3><div className={styles.metrics}>
      <div><strong>{originals.counts.processed_candidates.toLocaleString("ko-KR")}</strong><span>처리한 검색 주소</span></div>
      <div><strong>{originals.counts.postgres_raw_documents.toLocaleString("ko-KR")}</strong><span>원문 저장소에 연결한 자료</span><small>{originals.counts.postgres_unique_raw_documents !== undefined && originals.counts.unique_content_hashes !== undefined ? `${originals.counts.postgres_unique_raw_documents.toLocaleString("ko-KR")}개 DB 원문 행, 고유 내용 ${originals.counts.unique_content_hashes.toLocaleString("ko-KR")}개` : "발견 경로와 참고 페이지 포함"}</small></div>
      <div><strong>{originals.counts.new_candidate_opportunities}</strong><span>원문 기반으로 추가한 공고, 정책 후보</span><small>모집 회차, 자격과 이용 권한 검토 필요</small></div>
      <div><strong>{originals.counts.after_opportunities}</strong><span>전체 후보 DB</span><small>기존 {originals.counts.existing_opportunities_preserved}개 보존 / {originals.web_sync_status === "local_d1_verified" ? "데모 DB 동기화 확인" : "데모 DB 동기화 대기"}</small></div>
    </div><p>기관 원문이 연결된 검색 주소 {originals.counts.statuses.official_origin_collected ?? 0}개입니다. 같은 공고로 연결되는 여러 주소와 첨부를 포함하므로 고유 지원사업 수와 구분합니다.</p>
      {originals.access_retry && <section aria-label="접근 제한 조정 후 재수집 결과"><h4>실패 주소를 제한 조정 후 다시 수집했습니다</h4>
        <p>{originals.access_retry.retried_candidates}개 재시도 → 자료 수신 {originals.access_retry.recovered_candidates}개, 접근 실패 {originals.access_retry.still_failed_candidates}개. 등록기관 자료로 연결한 주소 {originals.access_retry.official_origin_candidates}개, 기관 확인과 문서 분류 대기 {originals.access_retry.recovered_candidates - originals.access_retry.official_origin_candidates}개입니다.</p>
        <p>8MB 다운로드 한도를 없애고 공개 HTTPS 도메인 이동을 허용했습니다. robots.txt는 조회와 규칙을 기록하며 수집을 중단시키지 않는 관찰 모드입니다. 로그인과 CAPTCHA는 우회하지 않습니다.</p>
        <p>새로 연결한 자료 {originals.access_retry.new_document_count}개, 이번에 추가한 공고 및 정책 후보 {originals.access_retry.new_candidate_opportunities ?? 0}개. 기존 후보 {originals.access_retry.existing_candidates_preserved ?? originals.counts.before_opportunities}개를 보존했습니다.</p>
      </section>}
      <details><summary>전체 주소의 처리 상태</summary><ul>{Object.entries(originals.counts.statuses).map(([key,count]) => <li key={key}>{originalStatuses[key] ?? key}: {count}개</li>)}</ul></details>
    </section>}
    <div className={styles.controls} aria-label="검색어 범주 선택"><button aria-pressed={group === "all"} onClick={() => setGroup("all")}>전체 100개</button>{data.groups.map(item => <button key={item.id} aria-pressed={group === item.id} onClick={() => setGroup(item.id)}>{item.label} {item.queries}</button>)}</div>
    <div className={styles.queryControls}><label htmlFor="keyword-query">실행한 검색어</label><select id="keyword-query" value={query?.id ?? ""} onChange={event => setSelectedId(event.target.value)}>{queries.map(item => <option key={item.id} value={item.id}>{item.id.toUpperCase()} {item.q} (추가 URL {item.marginal_url_keys})</option>)}</select><label><input type="checkbox" checked={priorityOnly} onChange={event => setPriorityOnly(event.target.checked)} />주제 키워드 일치, 잡음과 제한 경로 제외</label></div>
    {query && <KeywordQueryResults data={data} query={query} priorityOnly={priorityOnly} originals={originals} />}
    <details className={styles.operations}><summary>추가 발견량과 검색 종료 기준</summary>
      <p>비교 목록은 앞선 수집의 원문과 링크, 통합 색인의 홈페이지, 출처 설정, 기존 수집 요약과 작성 참고 목록, 국내 및 해외 확보 문서의 URL {data.baseline.known_url_keys.toLocaleString("ko-KR")}개입니다. 이번 검색과 겹치는 주소는 {data.counts.unique_url_keys - data.counts.new_url_keys_vs_baseline}개입니다. 같은 사업의 다른 회차, 양식과 페이지 주소는 서로 다른 URL로 남깁니다.</p>
      <p>추가 URL 수는 Q001부터 Q100까지 계획 순서로 집계했습니다. HTTP와 HTTPS, www 별칭, 추적 및 세션 값은 통합하고 게시글과 회차 식별자는 유지했습니다. 검색 서비스가 반환한 파싱 가능 URL을 기록했으며, 검색 엔진의 전체 결과나 고정 상위 K개를 뜻하지 않습니다.</p>
      <p>호스트 {data.counts.host_keys}개 중 비교 목록에 없던 호스트는 {data.counts.new_host_keys_vs_baseline}개입니다. 호스트 수는 기관 수와 다릅니다. 잡음 키워드 후보 {data.counts.noise_keyword_candidates}개와 제한 경로 후보 {data.counts.restricted_path_candidates}개도 원문 검토 전의 규칙 기반 표시입니다.</p>
      <p>100개는 이번 탐색량입니다. 검색 완료 여부는 아직 미확인입니다. 다음 운영안은 원문 검토를 마친 뒤, 비슷한 범위의 10개 검색 묶음 두 번에서 새 사업과 필요한 공식 문서가 모두 나오지 않을 때 추가 탐색을 재검토하는 것입니다.</p>
    </details>
  </section>;
}

export default function KeywordSearchPanel() {
  const [data, setData] = useState<KeywordSearch | null>(null);
  const [error, setError] = useState(false);
  const [originals, setOriginals] = useState<OriginalIngestion | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    fetch("/keyword-search.json", {cache: "no-store", signal: controller.signal}).then(response => {
      if (!response.ok) throw new Error("search evidence unavailable");
      return response.json();
    }).then(value => {
      if (!isKeywordSearch(value)) throw new Error("search evidence format changed");
      setData(value);
    }).catch(() => {if (!controller.signal.aborted) setError(true);});
    fetch("/original-ingestion.json", {cache: "no-store", signal: controller.signal}).then(r => r.ok ? r.json() : null).then(v => {if (isOriginalIngestion(v)) setOriginals(v);}).catch(() => {});
    return () => controller.abort();
  }, []);
  return data ? <KeywordSearchView data={data} originals={originals} /> : <section className={styles.keywordSection}><h2>키워드 검색 확대</h2><p role="status">{error ? "실제 검색 기록을 불러오지 못했습니다." : "실제 검색 기록을 불러오는 중입니다."}</p></section>;
}
