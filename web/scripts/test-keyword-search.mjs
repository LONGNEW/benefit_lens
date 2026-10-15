import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const ts = require('typescript');
function load(path, overrides = {}) {
  const code = ts.transpile(readFileSync(new URL(path, import.meta.url), 'utf8'), {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX});
  const exports = {};
  new Function('exports', 'require', code)(exports, name => overrides[name] ?? require(name));
  return exports;
}
const text = readFileSync(new URL('../public/keyword-search.json', import.meta.url), 'utf8');
const data = JSON.parse(text);
const lib = load('../lib/keyword-search.ts');
const roles = load('../lib/discovery-collection.ts');
const originals = load('../lib/original-ingestion.ts');
const css = new Proxy({}, {get: (_, key) => String(key)});
const {KeywordSearchView, KeywordQueryResults, OriginalTrace} = load('../app/benefitlens/collection/keyword-search-panel.tsx', {'@/lib/keyword-search': lib, '@/lib/discovery-collection': roles, '@/lib/original-ingestion': originals, './collection.module.css': {default: css}});
const {createElement} = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
let count = 0;
function test(name, run) {run(); count++; console.log(`PASS ${name}`);}

test('100 distinct executed searches and two stages have real evidence', () => {
  assert.ok(lib.isKeywordSearch(data));
  assert.equal(data.queries.length, 100);
  assert.equal(new Set(data.queries.map(q => q.q)).size, 100);
  assert.equal(data.counts.executed_queries, 100);
  assert.equal(data.counts.initial_queries, 80);
  assert.equal(data.counts.adaptive_queries, 20);
  assert.ok(data.queries.every(q => q.status === 'executed' && (q.searched_at || q.searched_on)));
});
test('each adaptive phrase retains an actual parent result URL', () => {
  for(const query of data.queries.filter(q => q.stage === 'adaptive')) {
    const parent = data.queries.find(q => q.id === query.derived_from.source_query_id);
    const source = data.candidates.find(c => c.source_url === query.derived_from.source_url);
    assert.ok(parent && source && source.query_ids.includes(parent.id));
    assert.ok(query.derived_from.observed_term);
  }
});
test('URL identities preserve all query relationships without program claims', () => {
  assert.equal(data.counts.unique_url_keys, data.candidates.length);
  assert.equal(data.counts.host_keys, new Set(data.candidates.map(c => c.host_key)).size);
  assert.equal(data.counts.adaptive_marginal_urls, data.queries.filter(q => q.stage === 'adaptive').reduce((n,q) => n+q.marginal_url_keys,0));
  for(const candidate of data.candidates) {
    assert.equal(candidate.program_id, null);
    assert.equal(candidate.official_status, 'not_verified');
    assert.equal(candidate.capture_mode, 'search_metadata_only');
    for(const id of candidate.query_ids) assert.ok(data.queries.find(q => q.id === id).result_url_keys.includes(candidate.url_key));
  }
  assert.equal(data.counts.new_originals_fetched_by_this_module, 0);
  assert.equal(data.stop_review.coverage_complete, false);
});
test('all ten query families filter without dropping selected query results', () => {
  assert.equal(data.groups.length, 10);
  assert.equal(lib.queriesForGroup(data,'all').length,100);
  for(const group of data.groups) assert.equal(lib.queriesForGroup(data,group.id).length,10);
  for(const query of data.queries) assert.equal(lib.resultsForQuery(data,query).length,query.unique_url_keys);
  const expected = data.candidates.filter(lib.priorityCandidate).length;
  assert.equal(expected,data.counts.topic_keyword_candidates);
});
test('public export contains no response bodies, internal paths, session or citation references', () => {
  assert.doesNotMatch(text,/"raw_response"|"citation_ref"|"raw_path"|\/home\/longnew|jsessionid|\u00b7/i);
  assert.ok(data.candidates.every(c => [...c.title].length <= 160));
});
test('invalid metadata cannot render fabricated search counts', () => {
  for(const value of [null, {}, {...data,counts:{}}, {...data,queries:[{}]}, {...data,candidates:[{}]}, {...data,candidates:[{...data.candidates[0],source_url:'javascript:alert(1)'}]}]) assert.equal(lib.isKeywordSearch(value),false);
});
test('UI exposes the observed keyword link and separates search from raw collection', () => {
  const html = renderToStaticMarkup(createElement(KeywordSearchView,{data}));
  assert.match(html,/100개 검색어/);
  assert.match(html,/Q038/);
  assert.match(html,/왜 이 검색어를 만들었을까/);
  assert.match(html,/Qualcomm Innovation Fellowship South Korea 2026/);
  assert.match(html,/이후 원문 수집과 DB 적재 단계로 연결/);
  assert.doesNotMatch(html,/지원 가능|검색 완료입니다|\u00b7/);
  const query = data.queries.find(q => q.id === 'q088');
  const child = renderToStaticMarkup(createElement(KeywordQueryResults,{data,query}));
  assert.match(child,/research.adobe.com\/india-ai-research-fellowship/);
  assert.match(child,/Q037/);
});
test('actual original evidence joins discoveries and database candidate links', () => {
  const evidence=JSON.parse(readFileSync(new URL('../public/original-ingestion.json',import.meta.url),'utf8'));
  assert.ok(originals.isOriginalIngestion(evidence));
  const linked=evidence.results.find(r => r.origins.some(url => evidence.outcomes.some(o => o.original_url === url)));
  assert.ok(linked);
  const html=renderToStaticMarkup(createElement(OriginalTrace,{data:evidence,candidateKey:linked.candidate_key}));
  assert.match(html,/기관 원문 연결 완료/);
  assert.match(html,/DB 후보 보기/);
  assert.match(html,/발견 주소에서 원문으로 이동한 기록/);
  assert.doesNotMatch(html,/신청 가능 확정|최종 합격/);
  for(const v of [null,{}, {...evidence,counts:{}},{...evidence,results:[{...linked,candidate_key:'javascript:alert(1)'}]}]) assert.equal(originals.isOriginalIngestion(v),false);
});
test('retry UI separates received documents, original issuer resolution and remaining failure', () => {
  const actual=JSON.parse(readFileSync(new URL('../public/original-ingestion.json',import.meta.url),'utf8'));
  const evidence={...actual,access_policy:{robots_mode:'observe',follow_public_redirects:true},access_retry:{finished_at:'2026-10-14T00:00:00Z',retried_candidates:5,recovered_candidates:3,official_origin_candidates:1,still_failed_candidates:2,new_document_count:4,new_candidate_opportunities:0,existing_candidates_preserved:123}};
  assert.ok(originals.isOriginalIngestion(evidence));
  const html=renderToStaticMarkup(createElement(KeywordSearchView,{data,originals:evidence}));
  assert.match(html,/5개 재시도/); assert.match(html,/자료 수신 3개/); assert.match(html,/접근 실패 2개/);
  assert.match(html,/관찰 모드/); assert.match(html,/이번에 추가한 공고 및 정책 후보 0개/);
  assert.equal(originals.isOriginalIngestion({...evidence,access_retry:{...evidence.access_retry,recovered_candidates:50}}),false);
});
console.log(`${count} keyword search checks passed`);
