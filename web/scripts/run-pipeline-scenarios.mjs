// Run the browser's real rule engine against data served from PostgreSQL.
import assert from 'node:assert/strict';
import {evaluateEligibility,evaluateAdditional,applicationState} from '../pipeline-demo/match.mjs';
import {buildDemoProfile} from '../pipeline-demo/demo-profile.mjs';

const base=process.argv[2];
if(!base?.startsWith('http://127.0.0.1:'))throw new Error('A loopback demo URL is required');
async function get(path){const response=await fetch(base+path);assert.equal(response.status,200,path);return response.json();}
const listing=await get('/api/pipeline/opportunities?scope=all');
assert.equal(listing.meta.total,10);
const profile=buildDemoProfile(),details=[];
for(const item of listing.data){
 const detail=await get('/api/pipeline/detail?id='+encodeURIComponent(item.opportunity_id));
 assert.equal(detail.data.program_id,item.program_id);
 if(detail.knowledge)assert.equal(detail.knowledge.program_id,item.program_id);
 details.push(detail);
}
const byId=Object.fromEntries(details.map(d=>[d.data.opportunity_id,d.data]));
const scenarios=[];
function check(id,actual,expected){assert.deepEqual(actual,expected,id);scenarios.push({id,actual,expected,passed:true});}
check('phd-excludes-undergraduate',evaluateEligibility(byId['ikef-undergraduate-2026'].eligibility,profile).result,false);
const undergraduate={...profile,'education.degree':'bachelors'};
for(const [value,scale,expected] of [[4,4.5,true],[3.99,4.5,false],[3.8,4.3,true],[3.79,4.3,false],[3.9,4,null]]){
 check(`gpa-${value}-of-${scale}`,evaluateEligibility(byId['ikef-undergraduate-2026'].eligibility,{...undergraduate,'education.gpa':{value,scale,degree_scope:'bachelors',period_scope:'semesters_1_to_3'}}).result,expected);
}
check('gates-unstructured-is-not-pass',evaluateEligibility(byId['gates-cambridge-2026-2027'].eligibility,profile).result,null);
check('mobis-current-coursework-false',evaluateEligibility(byId['hyundai-mobis-research-scholar-2026-h2'].eligibility,profile).result,false);
check('mobis-coursework-true',evaluateEligibility(byId['hyundai-mobis-research-scholar-2026-h2'].eligibility,buildDemoProfile({coursework:'true'})).result,true);
check('nvidia-first-year-exception',evaluateAdditional(byId['nvidia-2027-2028'].additional_checks,{'first-year':false},profile).find(c=>c.check_id==='first-year').result,'exception');
check('closed-google-can-match-basics',[
 evaluateEligibility(byId['google-east-asia-2026'].eligibility,profile).result,
 applicationState(byId['google-east-asia-2026'].application,'2026-10-15')],[true,'closed']);
console.log(JSON.stringify({
 profile_kind:'synthetic_demo_profile',
 profile_persisted:false,
 examples:details.map(({data,knowledge})=>({
  opportunity_id:data.opportunity_id,title:data.title,
  ...evaluateEligibility(data.eligibility,profile),
  application_state:applicationState(data.application,'2026-10-15'),
  cases:knowledge?.experience_cases?.length??0,
  patterns:knowledge?.writing_pattern?.length??0
 })),
 scenarios
}));
