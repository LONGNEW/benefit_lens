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
const data = JSON.parse(readFileSync(new URL('../public/discovery-collection.json', import.meta.url), 'utf8'));
const lib = load('../lib/discovery-collection.ts');
const css = new Proxy({}, {get: (_, key) => String(key)});
const {CollectionView} = load('../app/benefitlens/collection/collection-dashboard.tsx', {'@/lib/discovery-collection': lib, './collection.module.css': {default: css}, './keyword-search-panel': {default: () => null}});
const {createElement} = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
const html = renderToStaticMarkup(createElement(CollectionView, {data}));
let count = 0;
function test(name, run) {run(); count++; console.log(`PASS ${name}`);}
test('snapshot contains actual catalog and two discovery routes', () => {
  assert.ok(lib.isCollectionManifest(data));
  assert.equal(data.schema_version, 'discovery-1');
  assert.equal(data.catalog.status, 'collected'); assert.ok(data.catalog.row_count > 0);
  assert.ok(lib.visibleSeeds(data, 'catalog').length); assert.ok(lib.visibleSeeds(data, 'search').length);
  for(const seed of lib.visibleSeeds(data,'catalog')) assert.ok(seed.catalog_row && seed.catalog_product);
  for(const seed of lib.visibleSeeds(data,'search')) assert.ok(seed.query);
});
test('invalid snapshots fail the input contract instead of showing fabricated data', () => {
  for(const value of [null, {}, {schema_version:'discovery-1', documents:[], seeds:[]}, {...data,counts:{}}, {...data,documents:[{}]}]) assert.equal(lib.isCollectionManifest(value),false);
});
test('documents are connected by stored discovery edges, not fabricated labels', () => {
  for(const seed of data.seeds) {
    for(const doc of lib.documentsForSeed(data, seed.seed_id)) assert.ok(data.edges.some(edge => edge.seed_id === seed.seed_id && edge.document_id === doc.id));
  }
  for(const edge of data.edges.filter(edge=>edge.document_id)) assert.ok(data.documents.some(doc=>doc.id===edge.document_id));
});
test('metadata references have no raw content hash or internal file path', () => {
  for(const doc of data.documents) {assert.ok(lib.metadataIsSafe(doc)); assert.equal('raw_path' in doc,false);}
  assert.equal('raw_path' in data.catalog,false); assert.equal('database' in data,false);
  assert.equal(data.counts.metadata_only_documents, data.documents.filter(doc=>doc.capture_mode==='metadata_only').length);
});
test('run counts, actual request budgets and response status logs agree', () => {
  assert.equal(data.counts.collected_urls, data.documents.length);
  assert.equal(data.page_http_requests, data.request_log.length);
  assert.ok(data.page_http_requests<=data.run.limits.max_page_fetches);
  const hosts={}; for(const request of data.request_log) {const host=new URL(request.url).host;hosts[host]=(hosts[host]??0)+1; assert.ok(request.requested_at);}
  assert.ok(Object.values(hosts).every(count=>count<=data.run.limits.max_pages_per_host));
  assert.equal(data.counts.unique_content_hashes, new Set(data.documents.map(doc=>doc.sha256).filter(Boolean)).size);
});
test('page names discovery and stored originals without claiming applicant eligibility', () => {
  assert.match(html,/공고는 어디서 찾았을까/); assert.match(html,/통합 목록 경로/); assert.match(html,/검색과 링크 탐색/);
  assert.match(html,/100개 검색에서 발견한 주소/); assert.match(html,/미등록 도메인 후보/); assert.match(html,/모집 회차, 조건 해석과 이용 권한/);
  assert.doesNotMatch(html,/접수 가능|전국 수집 완료|1999|DGIST|\u00b7/);
});
test('no candidate URL includes anonymous session identifiers', () => {
  for(const edge of data.edges) assert.doesNotMatch(edge.url,/jsessionid/i);
  for(const doc of data.documents) assert.doesNotMatch(doc.final_url,/jsessionid/i);
});
console.log(`${count} collection demo checks passed`);
