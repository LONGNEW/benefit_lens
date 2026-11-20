const $=id=>document.getElementById(id);
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const states={open:'접수 중',closed:'마감 회차',upcoming:'접수 예정',rolling:'상시',unknown:'기간 미확정'};
const gaps={six_notices_summary_only:'6개 공고는 검토 요약만 있어 실행 입력으로 사용할 원문 파일 연결이 필요합니다.',gates_eligibility_missing:'Gates 공고는 기본 조건 트리가 비어 있어 자동 통과시키지 않았습니다.',notice_llm_extraction_not_executed:'공고 원문에서 조건 트리를 새로 만드는 LLM 전처리는 이번 경로에 연결되지 않았습니다.',personalized_generation_not_connected:'사용자 사실을 받아 완성 지원서를 생성하는 서비스 경로가 남아 있습니다.',refresh_scheduler_not_executed:'주기적 원문 갱신은 설계 대상으로 남겨 두었고 실행하지 않았습니다.',historical_collection_failures_retained:'이전 원문 수집 실패 2건은 실패 상태로 남겼으며 포화 완료로 처리하지 않았습니다.'};
const scenarioNames={'phd-excludes-undergraduate':'박사 프로필은 학부 장학금에서 제외','gpa-4-of-4.5':'4.5 만점의 4.0 경계값 충족','gpa-3.99-of-4.5':'4.5 만점의 경계값 미달','gpa-3.8-of-4.3':'4.3 만점의 3.8 경계값 충족','gpa-3.79-of-4.3':'4.3 만점의 경계값 미달','gpa-3.9-of-4':'공식 4.0 만점 분기 없음은 미확인','gates-unstructured-is-not-pass':'미추출 조건을 통과로 바꾸지 않음','mobis-current-coursework-false':'교과 미수료 박사 조건 불일치','mobis-coursework-true':'교과 수료 입력으로 조건 일치 전환','nvidia-first-year-exception':'박사 첫 학년 미완료는 기관 예외 검토','closed-google-can-match-basics':'기본 조건 일치와 마감 회차를 분리'};
function describe(stage){
 switch(stage.stage){
 case 'source_integrity':return ['원문 확인과 텍스트 추출',`공고 ${stage.notices}개와 경험 문서 ${stage.experience_documents}개, 총 ${stage.required_documents}개 원본 해시 확인과 텍스트 ${stage.text_reextracted}개 재추출`,'저장 원본으로 이번 실행'];
 case 'notice_contract':return ['공고 전처리 계약',`공고 ${stage.opportunities}개 검사, 조건 트리 ${stage.condition_trees}개, 세부 조건 ${stage.condition_leaves}개`,'검토 표본 재검증'];
 case 'experience_patterns':return ['경험 전처리',`${stage.programs}개 프로그램의 문서 ${stage.documents}개, 사례 ${stage.cases}개, 패턴 ${stage.patterns}개`,'기존 LLM 결과 재검증'];
 case 'database':return ['DB 적재',`공고 ${stage.opportunities}개를 두 번 적재, 변경 버전 ${stage.revision_count_before} → ${stage.revision_count_after}, 두 번째 적재에서 해시와 갱신 시각 동일`,'이번 실행'];
 case 'online_matching':return ['조회와 조건 판정',`실제 HTTP API의 예제 ${stage.examples}개, 시나리오 ${stage.passed}/${stage.scenarios} 통과`,'이번 실행'];
 case 'application_preparation':return ['지원 준비',`경험 패턴 기반 개요 ${stage.outlines}개, 완성 지원서 생성 ${stage.personalized_drafts}개`,'이번 실행'];
 default:throw new Error('알 수 없는 실행 단계');
 }
}
try{
 const response=await fetch('/assets/pipeline-run.json',{cache:'no-store'});
 if(!response.ok)throw new Error('실행 기록이 없습니다. make pipeline-replay로 생성하세요.');
 const run=await response.json();
 $('run-state').textContent=`실행 ${new Date(run.finished_at).toLocaleString('ko-KR')} / 데이터 기준일 ${run.data_as_of} / 제한 사항을 포함한 스냅샷 재현 검사 통과`;
 $('run-stages').innerHTML=run.stages.map((s,i)=>{const [title,body,mode]=describe(s);return `<article class="audit-stage"><span class="badge">${i+1}. ${escape(mode)}</span><h3>${escape(title)}</h3><p>${escape(body)}</p></article>`;}).join('');
 $('run-examples').innerHTML=run.examples.map(e=>`<tr><td><a href="/benefitlens?id=${encodeURIComponent(e.opportunity_id)}">${escape(e.title)}</a></td><td>${e.result===true?'일치':e.result===false?'불일치':'미확인'}</td><td>${escape(states[e.application_state])}</td><td>${e.cases}</td><td>${e.patterns}</td></tr>`).join('');
 $('run-models').innerHTML=run.model_reuse.map(m=>`<details class="audit-stage"><summary>${escape(m.program_id)}: 문서 ${m.documents}개, 사례 ${m.cases}개, 패턴 ${m.patterns}개</summary><p>이전 모델: ${escape(m.model)} / ${escape(m.original_model_run_at)}</p><p>전체 입력 ${m.characters.toLocaleString('ko-KR')}자</p><pre>${escape(m.input_sha256)}</pre></details>`).join('');
 $('run-scenarios').innerHTML=`<ul>${run.scenarios.map(s=>`<li>${escape(scenarioNames[s.id]??s.id)}: 통과</li>`).join('')}</ul>`;
 $('run-outline').innerHTML=`<p>프로그램 ${escape(run.outline.program_id)} / 근거 사례 ${escape(run.outline.supporting_case_ids.join(', '))}</p><pre class="audit-outline">${escape(run.outline.text)}</pre>`;
 $('run-gaps').innerHTML=run.gaps.map(g=>`<li>${escape(gaps[g]??g)}</li>`).join('');
}catch(error){$('run-state').textContent=error.message;}
