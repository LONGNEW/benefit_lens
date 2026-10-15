"use client";

import { useMemo, useState } from "react";
import { ExternalLink, FilePenLine, LockKeyhole } from "lucide-react";
import type { Opportunity } from "@/lib/opportunity-data";
import { buildDraft, getDraftQuestions, measureDraftLength, type PreparationDraft } from "@/lib/draft";
import { writingRecordForOpportunity } from "@/lib/opportunity-writing";
import { referenceHref, referenceLinks, strategyForQuestion, type ApplicationExample } from "@/lib/application-package";
import ApplicationReferencePanel from './application-reference-panel';

export default function DraftWorkbench({ opportunity, example = null }: { opportunity: Opportunity | null; example?: ApplicationExample | null }) {
  const pack = opportunity ? null : example;
  const record = useMemo(() => opportunity ? writingRecordForOpportunity(opportunity) : pack?.record ?? null, [opportunity, pack]);
  const questions = useMemo(() => record ? getDraftQuestions(record) : [], [record]);
  const [facts, setFacts] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<PreparationDraft | null>(() => pack && record ? buildDraft(record, []) : null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const policyCaution = record ? buildDraft(record, []).cautions.find(item => item.kind === 'ai_policy') : null;
  const cleanText = (value: string) => value.replaceAll('\u00b7', ', ');
  const links = (refs: string[]) => referenceLinks(pack, refs).map(link => <a key={referenceHref(link)} href={referenceHref(link)} target="_blank" rel="noreferrer">{cleanText(link.label)}</a>);
  const generate = () => {
    if (!record) return;
    setDraft(buildDraft(record, questions.flatMap(question => question.fact_slots.map(slot => ({question_id: question.id, slot_id: slot.id, text: facts[`${question.id}:${slot.id}`] ?? ''})))));
    setEdits({});
  };
  const pending = (opportunity?.applicationPrompts ?? []).filter(prompt => !record?.questions.some(question => question.id === prompt.id));
  return <section className="draft-workspace" aria-labelledby="draft-heading">
    <div className="draft-heading"><div><span>STEP 03</span><h2 id="draft-heading">{pack ? '서류, 과거 기록과 합격 후기로 준비하기' : '선택한 기회의 지원 준비'}</h2></div><p><LockKeyhole size={14} /> 입력과 수정은 이 브라우저에서 처리합니다.</p></div>
    {pack && <ApplicationReferencePanel pack={pack} checked={checked} onCheck={(id, value) => setChecked(current => ({...current, [id]: value}))} />}
    {policyCaution && <div className="writing-policy-caution"><strong>이 회차의 작성 정책</strong><p>{cleanText(policyCaution.message)}</p>{links(policyCaution.source_refs)}</div>}
    <div className="draft-grid">
      <div className="draft-input-panel">
        {opportunity && <div className="selected-opportunity"><h3>{opportunity.name}</h3><p>{opportunity.organization}</p><a href={opportunity.sourceUrl} target="_blank" rel="noreferrer">공식 출처 <ExternalLink size={13} /></a></div>}
        {(opportunity?.requiredDocuments?.length ?? 0) > 0 && <section className="collected-documents"><h3>수집한 제출서류</h3><ul>{opportunity!.requiredDocuments!.map((document, index) => <li key={index}>{cleanText(document)}</li>)}</ul></section>}
        <p className="prompt-warning">{pack ? '제35기 요강에서 확인한 3문항의 요약입니다. 같은 회차 후기의 준비 전략을 참고하고 본인의 사실만 입력하세요.' : record?.questions.length ? "선택한 공고 버전의 검토된 공식 문항을 사용합니다." : "이 공고 버전에는 검토된 공식 문항이 없습니다. 아래 질문은 일반 준비용입니다."}</p>
        {pending.length > 0 && <details><summary>문항 추출 후보 {pending.length}개, 검토 대기</summary>{pending.map(prompt => <p key={prompt.id}>{prompt.text} <a href={prompt.sourceUrl} target="_blank" rel="noreferrer">추출 출처</a></p>)}</details>}
        <div className="experience-form">{questions.map(question => <div key={question.id}>
          <h3>{cleanText(question.title)}</h3>{question.text && <p>{cleanText(question.text)}</p>}
          {question.length_limit.max !== null && <small>원문 제한 {question.length_limit.max}자, {question.length_limit.whitespace_included === 'yes' ? '공백 포함' : question.length_limit.whitespace_included === 'no' ? '공백 제외' : '공백 계산 방식 미확인'}</small>}
          {links(question.evidence_refs)}
          {pack && strategyForQuestion(pack, question.id) && <aside className="question-strategy"><strong>후기에서 참고할 작성 방향</strong><p>{strategyForQuestion(pack, question.id)!.summary}</p><small>{strategyForQuestion(pack, question.id)!.ask}</small><a href={pack.success.url} target="_blank" rel="noreferrer">같은 회차 후기 근거</a></aside>}
          {question.fact_slots.map(slot => <label key={slot.id}><span>{slot.label}</span><textarea value={facts[`${question.id}:${slot.id}`] ?? ''} onChange={event => {setFacts(current => ({...current, [`${question.id}:${slot.id}`]: event.target.value})); setDraft(null); setEdits({});}} placeholder={slot.placeholder} /></label>)}
        </div>)}</div>
        <button className="generate-draft" type="button" disabled={!record} onClick={generate}><FilePenLine size={16} /> 입력한 사실로 준비 초안 만들기</button>
      </div>
      <div className="draft-output-panel" aria-live="polite">
        {draft ? <><h3>{draft.label}</h3><p>로컬 문항별 정리 템플릿, LLM 생성 아님</p>
          {draft.sections.map(section => {
            const text = edits[section.question_id] ?? section.text;
            const question = questions.find(item => item.id === section.question_id)!;
            const measured = measureDraftLength(text, question.length_limit);
            const strategy = pack ? strategyForQuestion(pack, section.question_id) : null;
            return <div className="draft-document" key={section.question_id}><h4>{cleanText(section.title)}</h4>{strategy && <p className="draft-reference-direction">참고 방향: {strategy.summary}</p>}<label><span>내 표현으로 수정</span><textarea aria-label={`${cleanText(section.title)} 초안 수정`} rows={8} value={text} onChange={event => setEdits(current => ({...current, [section.question_id]: event.target.value}))} /></label><small>참고 문자수 {measured.character_count}자{question.length_limit.max !== null ? ` / 원문 제한 ${question.length_limit.max}자` : ', 공식 분량 미확인'}{question.length_limit.whitespace_included === 'yes' ? ', 공백 포함' : ''}{measured.length_status === 'over_limit' ? ', 제한 초과' : ''}</small>{links(section.source_refs)}</div>;
          })}
          <div className="provenance-panel"><h3>입력과 작성 기준</h3>{draft.cautions.filter(caution => caution.kind !== 'ai_policy').map((caution, index) => <p key={index}>{cleanText(caution.message)} {links(caution.source_refs)}</p>)}<p>사용자가 입력한 경험과 계획을 문항별로 정리했습니다. 입력이 없는 항목은 빈칸으로 남깁니다.</p>{pack && <p>후기 전략은 참고 방향으로 표시합니다. 초안 본문과 문자수에는 본인의 입력과 준비 틀만 포함됩니다.</p>}</div>
        </> : <div className="draft-placeholder"><h3>본인의 경험과 계획을 입력하세요.</h3><p>문항과 분량은 선택한 공고 버전에 연결됩니다. 다른 회차의 질문은 가져오지 않습니다.</p></div>}
      </div>
    </div>
  </section>;
}
