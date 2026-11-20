import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {evaluateEligibility,applicationState,evaluateAdditional} from '../pipeline-demo/match.mjs';
import {buildDemoProfile} from '../pipeline-demo/demo-profile.mjs';
import {renderWritingInputs,renderPatternSources,renderEvidenceAnalysis} from '../pipeline-demo/writing-examples.mjs';
const source=new URL('../../data/sample/programs.json',import.meta.url);
const bundle=JSON.parse(readFileSync(source,'utf8'));
const byId=Object.fromEntries(bundle.opportunities.map(p=>[p.opportunity_id,p]));
const phd={'education.degree':'phd','education.status':'enrolled','education.country':'KR','person.nationalities':['KR']};
const ikef=byId['ikef-undergraduate-2026'];
let checks=0;
function test(name,fn){fn();checks++;console.log('PASS '+name);}
test('박사에게 학부 GPA를 묻지 않음',()=>{const r=evaluateEligibility(ikef.eligibility,phd);assert.equal(r.result,false);assert.equal(r.trace.length,1);});
for(const [value,scale,expected] of [[4.0,4.5,true],[3.99,4.5,false],[3.8,4.3,true],[3.79,4.3,false],[3.9,4.0,null],[4.8,4.3,null]]){
 test(`GPA ${value}/${scale}`,()=>{const r=evaluateEligibility(ikef.eligibility,{...phd,'education.degree':'bachelors','education.gpa':{value,scale,degree_scope:'bachelors',period_scope:'semesters_1_to_3'}});assert.equal(r.result,expected);});
}
test('다른 성적 산정 기간은 미확인',()=>{assert.equal(evaluateEligibility(ikef.eligibility,{...phd,'education.degree':'bachelors','education.gpa':{value:4.2,scale:4.5,degree_scope:'bachelors',period_scope:'cumulative'}}).result,null);});
test('미추출과 빈 AND는 통과하지 않음',()=>{assert.equal(evaluateEligibility(null,phd).result,null);assert.equal(evaluateEligibility({all:[]},phd).result,null);});
test('현재 기본 박사 조건과 미래 전일제는 구분',()=>{assert.equal(evaluateEligibility(byId['nvidia-2027-2028'].eligibility,phd).result,true);assert.equal(evaluateEligibility({field:'education.full_time',op:'eq',value:true,at:'award_period'},{'education.full_time':true}).result,null);});
test('첫 학년 예외를 자동 탈락으로 바꾸지 않음',()=>{const p=byId['nvidia-2027-2028'];const r=evaluateAdditional(p.additional_checks,{'first-year':p.additional_checks[0].answer_type==='number'?0:false});assert.equal(r[0].result,'exception');assert.equal(r[1].result,'unknown');});
test('일반 추가 자격 불일치는 보존',()=>{assert.equal(evaluateAdditional(byId['google-east-asia-2026'].additional_checks,{'coursework':false})[0].result,'fail');});
test('마감과 기본 조건 일치는 독립',()=>{const p=byId['google-east-asia-2026'];assert.equal(evaluateEligibility(p.eligibility,phd).result,true);assert.equal(applicationState(p.application,'2026-10-15'),'closed');assert.equal(applicationState(byId['nvidia-2027-2028'].application,'2026-10-15'),'open');assert.equal(applicationState(byId['gates-cambridge-2026-2027'].application,'2026-10-15'),'closed');});
test('프로그램별 패턴은 해당 사례만 참조',()=>{for(const k of bundle.program_knowledge){const ids=new Set(k.experience_cases.map(p=>p.case_id));for(const p of k.writing_pattern){assert.ok(p.supporting_case_ids.every(id=>ids.has(id)));assert.equal(p.causal_success_claim,false);}}});
const mobis=byId['hyundai-mobis-research-scholar-2026-h2'];
test('현재 사용자는 Coursework 미수료이므로 현대모비스 박사 조건 불일치',()=>{
 const profile=buildDemoProfile();assert.equal(profile['education.coursework_completed'],false);
 assert.equal(evaluateEligibility(mobis.eligibility,profile).result,false);
});
test('Coursework 수료, 미확인과 석사 경로를 구분',()=>{
 assert.equal(evaluateEligibility(mobis.eligibility,buildDemoProfile({coursework:'true'})).result,true);
 assert.equal(evaluateEligibility(mobis.eligibility,buildDemoProfile({coursework:''})).result,null);
 assert.equal(evaluateEligibility(mobis.eligibility,buildDemoProfile({degree:'masters',coursework:'false'})).result,true);
});
test('현재 미수료를 Google 수혜 시작 시점의 미충족으로 복사하지 않음',()=>{
 const checks=evaluateAdditional(byId['google-east-asia-2026'].additional_checks,{},buildDemoProfile());
 assert.equal(checks.find(c=>c.check_id==='coursework').result,'unknown');
});
test('LLM 입력 원문 14개와 사례별 원문을 화면에 연결',()=>{
 const knowledge=bundle.program_knowledge.find(k=>k.program_id==='google-phd-fellowship');
 const html=renderWritingInputs(knowledge,{open:true});
 for(const doc of knowledge.pattern_extraction.input_documents)assert.ok(html.includes(doc.url.replace(/&/g,'&amp;')));
 assert.equal((html.match(/class="input-document"/g)||[]).length,14);

 const single={supporting_case_ids:['G1']};
 const sources=renderPatternSources(single,knowledge);
 assert.ok(sources.includes('research_statement_hj_googlefellowship.pdf'));
 assert.ok(!sources.includes('google_statement_2021.pdf'));
});
const server=process.argv[2];
if(server){
 async function get(path){const r=await fetch(server+path);assert.equal(r.status,200,path);return r.json();}
 const stats=await get('/api/pipeline/stats');assert.equal(stats.storage,'PostgreSQL');assert.equal(stats.source_records,10);assert.equal(stats.normalized_records,10);assert.equal(stats.experience_cases,14);assert.equal(stats.llm_knowledge_programs,3);
 const listed=await get('/api/pipeline/opportunities');assert.equal(listed.data.length,10);
 const detail=await get('/api/pipeline/detail?id=nvidia-2027-2028');assert.equal(detail.knowledge.program_id,detail.data.program_id);assert.equal(detail.knowledge.experience_cases[0].case_id,'N1');
 const whole=await get('/api/pipeline/opportunities?scope=all');assert.equal(whole.meta.total,10);assert.equal(whole.data.length,10);
 const last=await get('/api/pipeline/opportunities?scope=all&page=99999');assert.ok(last.data.length>0);assert.equal(last.meta.page,Math.ceil(10/12));
 console.log('PASS PostgreSQL live endpoints, pagination, scope and program knowledge');
}
console.log(`PASS ${checks} client rule checks`);
