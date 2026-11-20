// Module-level rendering test with a minimal DOM adapter, not a browser QA claim.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const html=readFileSync(new URL('../pipeline-demo/pipeline-run.html',import.meta.url),'utf8');
const nodes=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],{textContent:'',innerHTML:''}]));
const receipt=JSON.parse(readFileSync(new URL('../pipeline-demo/pipeline-run.json',import.meta.url),'utf8'));
const previous={document:globalThis.document,fetch:globalThis.fetch};
try{
 globalThis.document={getElementById(id){assert.ok(nodes[id],id);return nodes[id];}};
 globalThis.fetch=async path=>{assert.equal(path,'/assets/pipeline-run.json');return {ok:true,json:async()=>receipt};};
 await import('../pipeline-demo/pipeline-run.mjs');
 assert.ok(nodes['run-state'].textContent.includes(receipt.data_as_of));
 assert.equal((nodes['run-stages'].innerHTML.match(/class="audit-stage"/g)??[]).length,6);
 assert.equal((nodes['run-examples'].innerHTML.match(/<tr>/g)??[]).length,10);
 assert.equal((nodes['run-scenarios'].innerHTML.match(/<li>/g)??[]).length,11);
 assert.ok(nodes['run-outline'].innerHTML.includes('연구를 묶는 문제와 목표'));
 assert.ok(nodes['run-gaps'].innerHTML.includes('기본 조건 트리가 비어'));
 assert.ok(nodes['run-stages'].innerHTML.includes('텍스트 29개 재추출'));
 console.log('PASS pipeline receipt module rendering: 6 stages, 10 examples, 11 scenarios and explicit gaps');
}finally{
 globalThis.document=previous.document;globalThis.fetch=previous.fetch;
}
