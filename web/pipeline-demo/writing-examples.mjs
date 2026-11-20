const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={
 'hyeon-index':'G1 공개 자료 안내','hyeon-research':'G1 연구계획서','hyeon-essay':'G1 에세이',
 'julia-index':'G2 공개 자료 목록','julia-proposal':'G2 연구계획서',
 'anjalie-index':'G3 공개 자료 목록','anjalie-proposal':'G3 연구계획서',
 'morgan-index':'G4 공개 자료 목록','morgan-proposal':'G4 연구계획서','morgan-impact':'G4 영향 에세이','morgan-leadership':'G4 리더십 에세이',
 'moon-interview':'G5 공식 인터뷰','yoo-interview':'G6 공식 인터뷰','oh-interview':'G7 공식 인터뷰',
 'rohan-index':'N1 작성자 홈페이지','rohan-proposal':'N1 NVIDIA 연구계획서',
 'sampurna-essay':'C1 Gates Cambridge 에세이','sampurna-review':'C1 지원과 면접 후기',
 'yuke-proposal':'N2 Yuke Wang 연구요약서','yuke-panel':'N2 지원 문서를 공개한 패널',
 'ruth-proposal':'N3 Ruth Fong NVIDIA 연구계획서',
 'naveen-essay':'C2 Naveen Raman 지원 자료','naveen-review':'C2 결과와 작성 해설',
 'paul-review':'C3 Paul Bergen 지원 후기','onon-interview':'C4 Onon Bayasgalan 인터뷰'
};
const sourceLink=doc=>typeof doc.url==='string'&&doc.url.startsWith('https://')?`<a href="${escape(doc.url)}" target="_blank" rel="noreferrer">${escape(labels[doc.source_id]??doc.source_id)}</a>`:escape(labels[doc.source_id]??doc.source_id);

export function renderWritingInputs(knowledge,{open=false}={}){
 const receipt=knowledge?.pattern_extraction;
 if(!receipt)return '';
 const analyses=new Map((knowledge.document_analyses??[]).map(d=>[d.source_id,d]));
 const cases=new Set(receipt.input_documents.flatMap(doc=>doc.case_ids??[]));
 const statements=receipt.input_documents.filter(doc=>doc.role==='application_statement');
 const statementCases=new Set(statements.flatMap(doc=>doc.case_ids??[]));
 return `<section id="writing-inputs"><h3>LLM에 입력한 원문</h3><p class="muted">경험 사례 ${cases.size}건에 연결된 ${receipt.input_document_count}개 문서의 전체 텍스트 ${Number(receipt.full_text_characters).toLocaleString('ko-KR')}자를 입력했습니다. 지원서 원문은 ${statementCases.size}개 사례에서 ${statements.length}개 문서가 확보됐습니다. 같은 사람의 연구계획서와 에세이는 같은 사례 ID로 묶습니다.</p><details class="input-originals" ${open?'open':''}><summary>입력 원문과 문서별 분석 보기</summary>${receipt.input_documents.map(doc=>{const a=analyses.get(doc.source_id);return `<article class="input-document"><strong>${sourceLink(doc)}</strong><p>${escape(a?.summary??'문서별 분석을 확인할 수 없습니다.')}</p>${a?.quote?`<blockquote lang="en">${escape(a.quote)}</blockquote>`:''}</article>`;}).join('')}</details></section>`;
}

export function renderPatternSources(pattern,knowledge){
 const supporting=new Set(pattern.supporting_case_ids??[]);
 const documents=(knowledge?.pattern_extraction?.input_documents??[]).filter(doc=>(doc.case_ids??[]).some(id=>supporting.has(id)));
 if(!documents.length)return '';
 return `<details class="pattern-sources"><summary>근거 사례의 입력 원문 보기</summary><ul>${documents.map(doc=>`<li>${sourceLink(doc)}</li>`).join('')}</ul></details>`;
}

const outcomes={selected:'선정',not_selected:'미선정',ambiguous:'결과 모호',unknown:'결과 미확인'};
export function renderCaseOutcome(item){
 const proofs=item.outcome_evidence??(item.source_refs??[]).filter(r=>r.id==='O1').map(r=>({url:r.url,quote:r.anchor}));
 return `<p><span class="badge">${escape(outcomes[item.outcome_status]??'결과 미확인')}</span></p>${item.source_limitations?`<p class="muted">${escape(item.source_limitations)}</p>`:''}${proofs.length?`<details><summary>결과 확인 출처</summary>${proofs.map(p=>`<p>${sourceLink({source_id:p.quote,url:p.url})}</p>`).join('')}</details>`:''}`;
}
export function renderEvidenceAnalysis(knowledge){
 if(!knowledge?.criterion_observations)return '';
 const cases=new Map(knowledge.experience_cases.map(c=>[c.case_id,c]));
 const criteria=new Map((knowledge.evaluation_criteria??[]).map(c=>[c.criterion_id,c]));
 const docs=new Map((knowledge.pattern_extraction?.input_documents??[]).map(d=>[d.source_id,d]));
 const label=id=>`${id} ${cases.get(id)?.name??''} / ${outcomes[cases.get(id)?.outcome_status]??'결과 미확인'}`;
 const proof=items=>items.map(e=>`<blockquote><p>${escape(e.quote)}</p><small>${escape(e.case_id)} ${sourceLink(docs.get(e.source_id)??{source_id:e.source_id})}</small></blockquote>`).join('');
 return `<section id="writing-evidence"><h3>평가 기준을 입증한 근거</h3><p class="muted">행동과 성과는 공개 원문의 보고입니다. 아래 비교는 LLM이 관찰한 차이이며 심사자가 밝힌 선정 이유가 아닙니다.</p><details><summary>비교에 사용한 공식 평가 기준</summary><ul>${[...criteria.values()].map(c=>`<li>${escape(c.label)}: ${sourceLink({source_id:c.quote,url:c.url})}<p class="muted">${escape(c.scope)}</p></li>`).join('')}</ul></details>${knowledge.criterion_observations.map(o=>`<details class="criterion-observation"><summary>${escape(label(o.case_id))} / ${escape(criteria.get(o.criterion_id)?.label??o.criterion_id)}</summary><dl><dt>주장</dt><dd>${escape(o.claim)}</dd><dt>본인의 행동</dt><dd>${escape(o.personal_action)}</dd><dt>결과로 제시한 근거</dt><dd>${escape(o.result_evidence)}</dd><dt>기관과의 연결</dt><dd>${escape(o.institution_fit)}</dd><dt>자료의 한계</dt><dd>${escape(o.limitations)}</dd></dl>${proof(o.evidence)}</details>`).join('')}<h3>선정 및 미선정 글의 관찰 비교</h3>${knowledge.outcome_comparisons?.length?knowledge.outcome_comparisons.map(c=>`<article class="outcome-comparison"><strong>${escape(criteria.get(c.criterion_id)?.label??c.criterion_id)}</strong><p>${escape(c.observation)}</p><p class="muted">${escape(c.supporting_case_ids.map(label).join(', '))}</p>${proof(c.evidence)}<p class="muted">비교의 한계: ${escape(c.limitations)}</p><p>본인의 글을 준비할 때 확인할 사실</p><ul>${c.user_questions.map(q=>`<li>${escape(q)}</li>`).join('')}</ul></article>`).join(''):'<p>선정과 미선정 양쪽의 원문 근거가 없어 결과별 비교를 만들지 않았습니다.</p>'}</section>`;
}
