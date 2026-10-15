"use client";

import {useEffect, useState} from "react";
import {CollectionManifest, DiscoveryDocument, discoveryRoles, discoveryStatuses, documentsForSeed, isCollectionManifest, visibleSeeds} from "@/lib/discovery-collection";
import styles from "./collection.module.css";
import KeywordSearchPanel from "./keyword-search-panel";

function dateTime(value: string) {
  return new Date(value).toLocaleString("ko-KR", {timeZone: "Asia/Seoul", hour12: false});
}

function DocumentCard({doc}: {doc: DiscoveryDocument}) {
  const metadataOnly = doc.capture_mode === "metadata_only";
  return <article className={styles.document}>
    <div className={styles.tags}><span>{discoveryRoles[doc.role] ?? "분류 대기"}</span><span>{doc.kind.toUpperCase()}</span><span>{metadataOnly ? "메타데이터만 보관" : "원문 확보"}</span></div>
    <h3><a href={doc.final_url} target="_blank" rel="noreferrer">{doc.title}</a></h3>
    <p className={styles.url}>{doc.final_url}</p>
    <details><summary>원문 기록 보기</summary>
      <dl><dt>수집 시각</dt><dd>{dateTime(doc.collected_at)}</dd><dt>응답 크기</dt><dd>{doc.bytes.toLocaleString("ko-KR")} bytes</dd>
        <dt>{metadataOnly ? "메타데이터 해시" : "원본 바이트 해시"}</dt><dd>{metadataOnly ? doc.metadata_sha256 : doc.sha256}</dd>
        <dt>DB 기록</dt><dd>{doc.database_change === "reused_version" ? "저장된 버전 재사용" : "새 기록 저장"}</dd>
      </dl>
      {metadataOnly && <p>본문 파일은 보관하지 않습니다. 선정자 연구 소개는 실제 제출 지원서와 다른 자료 유형입니다.</p>}
    </details>
  </article>;
}

export function CollectionView({data}: {data: CollectionManifest}) {
  const [route, setRoute] = useState<"all" | "catalog" | "search">("all");
  const [selectedId, setSelectedId] = useState(data.seeds[0]?.seed_id ?? "");
  const available = visibleSeeds(data, route);
  const selected = available.find(seed => seed.seed_id === selectedId) ?? available[0];
  const docs = selected ? documentsForSeed(data, selected.seed_id) : [];
  const edges = selected ? data.edges.filter(edge => edge.seed_id === selected.seed_id) : [];
  const unresolved = edges.filter(edge => !["collected", "reused_in_run"].includes(edge.status));
  const requests = data.request_log ?? [];
  return <main className="site-shell benefitlens-shell">
    <header className="topbar">
      <a href="/benefitlens" className="brand"><span className="brand-mark"><span /></span><span><strong>BenefitLens</strong><small>지원 정보의 발견과 연결</small></span></a>
      <nav aria-label="BenefitLens 페이지"><a href="/benefitlens">지원 찾기</a><a href="/benefitlens/profile">내 정보</a><a href="/benefitlens/draft">초안 작성</a><a href="/benefitlens/collection" className="active">수집 데모</a><a href="/benefitlens/about">데이터 기준</a></nav>
      <div className="mode-chip"><span /> COLLECTION DEMO</div>
    </header>
    <div className={styles.wrap}>
      <section className={styles.intro}><p className="eyebrow">발견 주소에서 발행기관 원문과 DB까지</p><h1>공고는 어디서 찾았을까?</h1><p className="lede">100개 검색에서 발견한 주소를 공식 본문과 첨부로 연결하고 DB에 적재했습니다. 검색어를 선택하면 발견 주소, 원문 확보 상태와 후보 DB 연결을 확인할 수 있습니다.</p><p className={styles.time}>아래 소규모 탐색 실행 {dateTime(data.run.finished_at)} (한국 시간)</p></section>
      <div className={styles.notice}>수집 원문과 공고 후보를 함께 보여줍니다. 모집 회차, 조건 해석과 이용 권한의 검토 상태는 각 후보에 남깁니다.</div>
      <section className={styles.metrics} aria-label="앞선 소규모 탐색 결과">
        <div><strong>{data.counts.catalog_rows.toLocaleString("ko-KR")}</strong><span>통합 색인의 상품 행</span><small>현재 모집 공고 수와 구분</small></div>
        <div><strong>{data.counts.stored_raw_documents}</strong><span>원문을 보존한 자료</span><small>고유 내용 해시 {data.counts.unique_content_hashes}개</small></div>
        <div><strong>{data.counts.metadata_only_documents}</strong><span>메타데이터 전용 자료</span><small>경험과 선정자 소개 등</small></div>
        <div><strong>{data.counts.new_host_keys}</strong><span>미등록 도메인 후보</span><small>실행 전 설정과 비교, www 별칭 통합</small></div>
      </section>
      <KeywordSearchPanel />
      <section className={styles.controls} aria-label="발견 경로 선택">
        {([['all','두 경로 모두'],['catalog','통합 목록 경로'],['search','검색과 링크 탐색']] as const).map(([value,label]) => <button key={value} aria-pressed={route === value} onClick={() => setRoute(value)}>{label}</button>)}
        <span>깊이 {data.run.limits.max_depth}, 페이지 HTTP 요청 {data.page_http_requests}/{data.run.limits.max_page_fetches}회</span>
      </section>
      <div className={styles.workspace}>
        <aside className={styles.sources} aria-label="발견 출처">
          <h2>시작 출처</h2>
          {available.map(seed => <button key={seed.seed_id} className={selected?.seed_id === seed.seed_id ? styles.selected : ""} aria-pressed={selected?.seed_id === seed.seed_id} onClick={() => setSelectedId(seed.seed_id)}><span>{seed.route === "catalog" ? "통합 목록" : "검색 후보"}</span><strong>{seed.label}</strong><small>{documentsForSeed(data, seed.seed_id).length}개 자료 연결{seed.new_to_registry ? ", 미등록 도메인" : ", 기존 출처"}</small></button>)}
        </aside>
        <section className={styles.bundle} aria-label="선택한 출처의 원문 묶음">
          {selected ? <>
            <h2>{selected.label}</h2>
            <div className={styles.origin}><strong>{selected.route === "catalog" ? "통합 목록에서 발견" : "검색에서 발견"}</strong>
              {selected.route === "catalog" ? <p><a href={data.catalog.page_url} target="_blank" rel="noreferrer">한국장학재단 CSV</a>의 {selected.catalog_row}번 행, {selected.catalog_product}</p> : <p>검색어 <code>{selected.query}</code><br />검색 서비스 {data.search_batch.provider}, {data.search_batch.searched_on}에 선택한 후보</p>}
              <p>시작 페이지 <a href={selected.url} target="_blank" rel="noreferrer">{selected.url}</a></p>
            </div>
            <h3 className={styles.sectionTitle}>확보한 자료 {docs.length}개</h3>
            <div className={styles.documents}>{docs.map(doc => <DocumentCard key={doc.id} doc={doc} />)}</div>
            {!docs.length && <p>이 시작 출처에서 확보한 원문이 없습니다. 아래 방문 기록에서 실패와 제한을 확인하세요.</p>}
            <details className={styles.trace}><summary>따라간 링크와 미확보 후보 {edges.length}개 보기</summary><p>시작 페이지는 깊이 0입니다. 방문하지 않은 후보 {unresolved.length}개를 포함합니다.</p>
              <ol>{edges.map((edge,index) => <li key={`${edge.url}-${index}`}><span className={styles.traceStatus}>{discoveryStatuses[edge.status] ?? edge.status}, 깊이 {edge.depth}</span><a href={edge.url} target="_blank" rel="noreferrer">{edge.label}</a><small>{edge.parent_url ? `이전 페이지: ${edge.parent_url}` : "검색 또는 CSV의 시작 URL"}</small></li>)}</ol>
            </details>
          </> : <p>이 경로의 시작 출처가 없습니다.</p>}
        </section>
      </div>
      <details className={styles.operations}><summary>탐색 범위와 접근 정책, 실제 요청 기록</summary>
        <p>검색 결과는 검색 서비스에서 선택한 후보를 입력했습니다. 검색 전체 결과와 전국 모든 기관을 수집한 것이 아닙니다. 기관 API 자동 연동은 키와 승인 범위를 확보한 뒤 추가합니다.</p>
        <p>최대 깊이 {data.run.limits.max_depth}, 호스트당 페이지 요청 {data.run.limits.max_pages_per_host}회, 요청 간격 최소 {data.run.limits.delay_seconds}초입니다. 페이지 요청에는 CSV 확보와 리디렉션을 포함하고 robots.txt 확인은 별도로 기록합니다.</p>
        <ul>{data.access_policies.map(policy => <li key={policy.url}><a href={policy.url} target="_blank" rel="noreferrer">{policy.url}</a>: {policy.status === "checked" ? "확인" : policy.status === "absent_404" ? "404, 제공된 규칙 없음" : "확인 불가"}, {dateTime(policy.checked_at)}</li>)}</ul>
        <div className={styles.requestTable}><table><thead><tr><th>요청 시각</th><th>URL</th><th>응답</th></tr></thead><tbody>{requests.map((request,index) => <tr key={index}><td>{dateTime(request.requested_at)}</td><td>{request.url}{request.redirect_to && <small>이동 대상 {request.redirect_to}</small>}</td><td>{request.response_status ?? "연결 실패"}</td></tr>)}</tbody></table></div>
        <p>실행 ID <code>{data.run.id}</code></p>
      </details>
    </div>
  </main>;
}

export default function CollectionDashboard() {
  const [data, setData] = useState<CollectionManifest | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    fetch("/discovery-collection.json", {cache: "no-store", signal: controller.signal}).then(response => {
      if (!response.ok) throw new Error("collection unavailable");
      return response.json();
    }).then(value => {
      if (!isCollectionManifest(value)) throw new Error("collection format changed");
      setData(value);
    }).catch(() => {if (!controller.signal.aborted) setError(true);});
    return () => controller.abort();
  }, []);
  if (data) return <CollectionView data={data} />;
  return <main className={styles.wrap}><h1>수집 데모</h1><p role="status">{error ? "실제 수집 결과를 불러오지 못했습니다. 수집 실행 산출물을 확인해야 합니다." : "실제 수집 결과를 불러오는 중입니다."}</p><a href="/benefitlens">지원 찾기로 돌아가기</a></main>;
}
