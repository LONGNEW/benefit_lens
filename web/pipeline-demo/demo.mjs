import {evaluateEligibility,applicationState,evaluateAdditional} from './match.mjs';
const $=id=>document.getElementById(id);
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const link=(url,label)=>typeof url==='string'&&url.startsWith('https://')?`<a href="${escape(url)}" target="_blank" rel="noreferrer">${escape(label)}</a>`:escape(label);
const number=value=>Number(value??0).toLocaleString('ko-KR');
const stateLabels={open:'접수 중',closed:'마감 회차',upcoming:'접수 예정',rolling:'상시',unknown:'접수 기간 미확정'};
const kinds={scholarship:'장학금',fellowship:'펠로우십',admission_funding:'진학 지원',research_grant:'일반 연구비',other:'기타 지원'};
let page=1,meta=null,items=[],selected=null,asOf='2026-10-15',requestNo=0;
const answers={};
function profile(){
 const p={'person.nationalities':['KR'],'education.degree':$('degree').value,'education.status':'enrolled','education.country':'KR','education.institution_id':'postech','education.department_id':'ax','education.semester':1,'education.full_time':true,'employment.status':'unemployed'};
 if($('gpa').value!=='')p['education.gpa']={value:Number($('gpa').value),scale:Number($('scale').value),degree_scope:$('degree').value,period_scope:$('gpa-period').value};
 return p;
}
function comparison(item){const result=evaluateEligibility(item.eligibility,profile());return {...result,label:result.result===true?'기본 조건 일치':result.result===false?'기본 조건 불일치':item.eligibility?'기본정보 추가 필요':'조건 정규화 또는 진학 정보 필요',style:result.result===true?'pass':result.result===false?'fail':'unknown'};}
function benefitText(item){return (item.benefits??[]).map(b=>{let money=b.amount!=null?`${number(b.amount)} ${b.currency??''}`:b.min_amount!=null||b.max_amount!=null?`${b.min_amount!=null?number(b.min_amount):'?'} ~ ${b.max_amount!=null?number(b.max_amount):'?'} ${b.currency??''}`:'';return [money,b.amount_kind==='maximum'?'최대':b.amount_kind==='fixed'?'정액':'',b.period==='semester'?'학기당':b.period==='year'?'연간':'',b.description].filter(Boolean).join(' ');}).filter(Boolean).join(' / ')||'혜택 상세 정규화 대기';}
function renderCards(){
 $('cards').innerHTML=items.map(item=>{const c=comparison(item),state=applicationState(item.application,asOf);return `<article class="card"><div class="badges"><span class="badge">${escape(kinds[item.category]??item.category)}</span><span class="badge">${escape(stateLabels[state])}</span><span class="badge ${c.style}">${c.label}</span></div><h3>${escape(item.title)}</h3><p class="organization">${escape(item.issuer?.name??'제공 기관 미확정')}</p><dl><dt>혜택</dt><dd>${escape(benefitText(item))}</dd><dt>추가 자격</dt><dd>${item.additional_checks.length}개, 선택 후 확인</dd><dt>처리 상태</dt><dd>${item.extractor==='source_native_fields_v1'?'원천 필드 정규화, 상세 해석 남음':'원문을 읽어 정리한 데모'}</dd></dl><button data-open="${escape(item.opportunity_id)}">자격과 경험 자료 연결 보기</button></article>`;}).join('')||'<p>해당 자료가 없습니다.</p>';
 $('result-count').textContent=`${number(meta?.total)}건, 현재 페이지 ${items.length}건`;
 $('page').textContent=`${page} / ${Math.max(1,Math.ceil((meta?.total??0)/12))}`;
 $('previous').disabled=page<=1;$('next').disabled=page*12>=(meta?.total??0);
 for(const button of document.querySelectorAll('[data-open]'))button.addEventListener('click',()=>openDetail(button.dataset.open));
}
async function request(path){const response=await fetch(path,{cache:'no-store'});if(!response.ok)throw new Error('DB 조회에 실패했습니다. 로컬 서버의 실행 상태를 확인하세요.');return response.json();}
async function search(){const n=++requestNo;try{const params=new URLSearchParams({scope:$('scope').value,category:$('category').value,q:$('query').value,page:String(page)});const data=await request('/api/pipeline/opportunities?'+params);if(n!==requestNo)return;items=data.data;meta=data.meta;page=meta.page;renderCards();}catch(error){if(n===requestNo)$('cards').textContent=error.message;}}
async function openDetail(id){try{const data=await request('/api/pipeline/detail?id='+encodeURIComponent(id));selected=data;renderDetail();}catch(error){$('detail').textContent=error.message;}}
function renderDetail(){
 if(!selected)return;
 const item=selected.data,knowledge=selected.knowledge,c=comparison(item),refs=item.source_refs??[];
 const ref=id=>{const r=refs.find(r=>r.id===id);return r?link(r.url,r.locator??'공식 원문'):'';};
 const checks=evaluateAdditional(item.additional_checks,answers[item.opportunity_id]??{});
 const checkLabels={pass:'답변상 충족',fail:'답변상 미충족',exception:'기관 예외 심사 필요',unknown:'미답변 또는 조건 해석 필요'};
 $('detail').innerHTML=`<p class="eyebrow">공고와 프로그램 지식의 연결</p><h2>${escape(item.title)}</h2><span class="badge ${c.style}">${c.label}</span><p>${escape(item.explanation)}</p><p class="muted">회차 ${escape(item.round_id??'미확정')} / 모집 상태 ${escape(stateLabels[applicationState(item.application,asOf)])}</p><h3>기본 조건의 비교 근거</h3><ul>${c.trace.map(t=>`<li>${escape(t.field)} ${escape(JSON.stringify(t.expected))}${t.scale?` / ${t.scale} 만점`:''}: ${t.result===true?'일치':t.result===false?'불일치':'정보 필요'}</li>`).join('')||'<li>기본 조건 자동 비교를 위한 구조화가 남아 있습니다.</li>'}</ul><h3>혜택</h3><p>${escape(benefitText(item))}</p><h3>선택한 공고의 추가 자격</h3><p class="muted">추가 자격을 답하지 않아도 기본 비교는 표시합니다. 명시적인 미충족과 기관 예외 검토는 별도로 표시합니다.</p>${checks.map((check,index)=>`<div class="check"><label>${escape(check.question)}${check.answer_type==='boolean'?`<select data-check="${index}"><option value="">아직 답하지 않음</option value="true" ${check.result==='pass'?'selected':''}>예</option><option value="false" ${['fail','exception'].includes(check.result)?'selected':''}>아니오</option></select>`:''}</label><span class="muted">${checkLabels[check.result]} ${ref(check.evidence_ref)}</span>${check.exception?`<p class="muted">${escape(check.exception)}</p>`:''}</div>`).join('')||'<p>추가 질문이 아직 정리되지 않았습니다.</p>'}<h3>신청하면 준비하거나 해야 할 일</h3><ul>${(item.requirements??[]).map(r=>`<li>${escape(r.description)} ${ref(r.evidence_ref)}</li>`).join('')||'<li>제출 및 활동 요구사항 정규화 대기</li>'}</ul><p>${link(item.application.url,'공식 신청 안내 열기')}</p><h3>같은 프로그램의 실제 경험 자료</h3>${knowledge?knowledge.experience_cases.map(k=>`<div class="case"><strong>${escape(k.name)}</strong><p>${escape(k.document_type)} / 지원 회차 ${escape(k.application_round??'미확인')} / 선정 확인 ${escape(k.outcome_verification)}</p>${(k.fragments??[]).map(f=>`<p>${escape(f.summary)}</p>`).join('')}<p class="source">${k.source_refs.map(r=>link(r.url,r.locator??'원문')).join(' / ')}</p></div>`).join(''):'<p>연결된 경험 자료가 아직 없습니다.</p>'}<h3>사례에서 읽어 낸 작성 패턴</h3>${knowledge?.writing_pattern?.length?knowledge.writing_pattern.map(p=>`<div class="pattern"><strong>${escape(p.insight)}</strong><ol>${p.sections.map(s=>`<li>${escape(s)}</li>`).join('')}</ol><small>근거 사례 ${escape(p.supporting_case_ids.join(', '))} / ${p.basis==='single_case_observation'?'단일 사례 관찰':'여러 사례의 구성 비교'}</small><small>${escape((p.variations??[]).join(' '))}</small><p>본인에게 필요한 내용: ${escape(p.required_inputs.join(', '))}</p><button data-outline="${escape(p.pattern_id)}">빈칸 개요 보기</button></div>`).join(''):'<p>현재 회차 문항과 연결할 작성 패턴이 없습니다.</p>'}<div id="outline"></div><details><summary>저장된 스키마와 원문 연결</summary><pre>${escape(JSON.stringify({opportunity_id:item.opportunity_id,program_id:item.program_id,eligibility:item.eligibility,benefits:item.benefits,extractor:item.extractor,source_refs:item.source_refs,version_hash:selected.version_hash},null,2))}</pre></details>`;
 for(const node of document.querySelectorAll('[data-check]'))node.addEventListener('change',()=>{answers[item.opportunity_id]??={};answers[item.opportunity_id][checks[Number(node.dataset.check)].check_id]=node.value===''?undefined:node.value==='true';renderDetail();});
 for(const button of document.querySelectorAll('[data-outline]'))button.addEventListener('click',()=>{const p=knowledge.writing_pattern.find(p=>p.pattern_id===button.dataset.outline);$('outline').innerHTML='<h3>사용자 경험을 넣을 빈칸</h3><p class="muted">작성 구조 예시입니다. 타인의 경험이나 아직 확인하지 않은 본인의 사실을 채워 넣지 않습니다.</p><textarea aria-label="사용자 경험 개요">'+escape(p.sections.map((s,i)=>`${i+1}. ${s}\n[${p.required_inputs[i]??'본인의 경험과 근거'}]`).join('\n\n'))+'</textarea>';});
}
async function stats(){
 try{
  const s=await request('/api/pipeline/stats');asOf=s.as_of;
  $('stats').innerHTML=[[s.source_records,'저장된 원천 또는 표본'],[s.normalized_records,'공고 모델'],[s.reviewed_demo,'데모 공고'],[s.experience_cases,'연결된 경험 요약']].map(([n,label])=>`<div class="stat"><strong>${number(n)}</strong><span>${label}</span></div>`).join('');
  $('progress').innerHTML=s.public_sample?`<p>공개 배포용 공고 ${number(s.public_sample)}개와 프로그램 ${s.knowledge_programs}개의 경험 요약을 PostgreSQL에서 조회합니다. 별도 대용량 DB와 원문 다운로드 없이 실행합니다.</p><p>기준일 ${escape(s.as_of)}의 과거 회차 표본이며 일부 공통 조건만 비교하는 예제가 포함됩니다. 모집 중 여부와 모든 자격을 보증하지 않습니다.</p>`:`<p>PostgreSQL 실조회, 기준일 ${escape(s.as_of)}. 원천 필드 정규화 ${number(s.work_items.source_fields_normalized)}건, 상세 해석 대기 ${number(s.work_items.pending)}건입니다. 경험이 연결된 프로그램 ${s.knowledge_programs}개입니다.</p>`;
  $('waves').innerHTML='<h3>경험 검색 계획과 기록</h3>'+s.search_waves.map(w=>`<div class="wave"><strong>${w.wave}단계</strong><p>${escape(w.queries.map(q=>q.q).join(' / '))}</p><p>확장 표현: ${escape(w.accepted_terms.map(t=>t.term).join(', ')||'없음')} / ${escape(w.status)}</p></div>`).join('');
 }catch(error){$('stats').textContent=error.message;}
}
$('search-form').addEventListener('submit',event=>{event.preventDefault();page=1;search();});
$('profile-form').addEventListener('submit',event=>event.preventDefault());
$('profile-form').addEventListener('input',()=>{renderCards();renderDetail();});
$('previous').addEventListener('click',()=>{page--;search();});$('next').addEventListener('click',()=>{page++;search();});
await stats();await search();
