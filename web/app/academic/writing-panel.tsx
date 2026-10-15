"use client";

import { useReducer, useState } from "react";
import { draftEditReducer, initialDraftEditState } from "@/lib/draft-edit-state";
import writingResources from "@/lib/writing-resources.json";
import { buildDraft, getDraftQuestions, measureDraftLength, type DraftResult, type ProvidedFact, type WritingResourceRecord } from "@/lib/draft";
import styles from "./academic.module.css";

type ResourceSource = {
  id: string; url: string; title: string; source_kind: string;
  published_at: string | null; verification_scope: string; summary?: string;
  rights_review?: { status: string; verified_range: string };
};
type Evidence = { id: string; url: string; page: number | null; location: string };
type ResourceRecord = WritingResourceRecord & {
  official_sources: ResourceSource[]; unofficial_sources: ResourceSource[]; evidence: Evidence[];
};
const records = writingResources.records as unknown as ResourceRecord[];
const sourceKindLabels: Record<string, string> = {
  official_notice: "공식 공고", official_form: "공식 요강·양식", official_manual: "공식 안내",
  success_account: "제3자 합격 후기 · 자기보고", personal_review: "개인 후기",
};

export default function WritingPanel() {
  const [recordId, setRecordId] = useState(records[0].record_id);
  const record = records.find(item => item.record_id === recordId) ?? records[0];
  const questions = getDraftQuestions(record);
  const [facts, setFacts] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<DraftResult>(() => buildDraft(records[0], []));
  const [{ editing, texts: edited }, dispatchEdit] = useReducer(draftEditReducer, undefined, initialDraftEditState);

  function chooseRecord(id: string) {
    const next = records.find(item => item.record_id === id) ?? records[0];
    setRecordId(id); setFacts({}); setDraft(buildDraft(next, [])); dispatchEdit({ type: "reset" });
  }
  function updateDraft() {
    const provided: ProvidedFact[] = questions.flatMap(question => question.fact_slots.map(slot => ({
      question_id: question.id, slot_id: slot.id, text: facts[`${question.id}/${slot.id}`] ?? "",
    })));
    setDraft(buildDraft(record, provided)); dispatchEdit({ type: "reset" });
  }
  function ownWords() {
    dispatchEdit({ type: "enter", sections: draft.sections });
  }
  function restoreDraftText() {
    dispatchEdit({ type: "restore", sections: draft.sections });
  }
  function sourceLinks(refs: string[]) {
    const sources = refs.flatMap(ref => {
      if (ref.startsWith("https://")) return [{ url: ref, label: "정책 원문 확인 ↗" }];
      const evidence = record.evidence.find(item => item.id === ref);
      return evidence ? [{ url: `${evidence.url}${evidence.page ? `#page=${evidence.page}` : ""}`, label: `${evidence.page ? `${evidence.page}쪽 · ` : ""}${evidence.location} ↗` }] : [];
    });
    return [...new Map(sources.map(source => [source.url, source])).values()].map(source => <a key={source.url} href={source.url} target="_blank" rel="noreferrer">{source.label}</a>);
  }
  const aiCaution = draft.cautions.find(item => item.kind === "ai_policy");

  return <section className={styles.writing} id="writing-prep" aria-label="초안과 준비 자료">
    <div className={styles.writingTitle}>
      <div><h2>내 사실로 시작하는 초안</h2><p>확인한 문항과 준비 자료를 보고 초안을 제공합니다. 경험·계획은 입력한 내용만 쓰며, 비어 있는 부분은 질문으로 남깁니다.</p></div>
      <select aria-label="초안 준비할 공고·회차" value={recordId} onChange={event => chooseRecord(event.target.value)}>{records.map(item => <option key={item.record_id} value={item.record_id}>{item.notice.title} · {item.round.label}</option>)}</select>
    </div>
    {aiCaution && <div className={styles.caution}><strong>공고별 주의 메모</strong><br />{aiCaution.message}<br />{sourceLinks(aiCaution.source_refs)}</div>}
    <div className={styles.writingLayout} style={{ marginTop: aiCaution ? 18 : undefined }}>
      <div>
        <div className={styles.factFields}>{questions.map(question => <div key={question.id} className={styles.factGroup}>
          <h3>{question.title}</h3>
          {question.text && <p className={styles.reason}>{question.text} <span>문항의 독자 요약입니다.</span></p>}
          {question.fact_slots.map(slot => <label key={slot.id} className={styles.factField}><span>{slot.label}</span><textarea value={facts[`${question.id}/${slot.id}`] ?? ""} onChange={event => setFacts(current => ({ ...current, [`${question.id}/${slot.id}`]: event.target.value }))} placeholder={slot.placeholder} /></label>)}
        </div>)}</div>
        <div className={styles.draftActions}><button type="button" onClick={updateDraft}>입력한 사실로 초안 구성</button><button type="button" className={styles.secondary} onClick={ownWords}>내 표현으로 수정</button>{editing && <button type="button" className={styles.secondary} onClick={restoreDraftText}>현재 초안으로 되돌리기</button>}</div>
        <p className={styles.reason}>이 탭에만 남는 준비 초안입니다. 수정 버튼을 다시 눌러도 편집한 내용은 유지됩니다. 현재 초안으로 되돌리기는 편집 내용을 초안으로 바꿉니다. 새 초안 구성이나 공고 전환 시 편집 내용은 초기화됩니다. 제출이나 외부 저장은 하지 않습니다.</p>
      </div>
      <div className={styles.draftOutput}>
        <div className={styles.draftLabel}><strong>{draft.label}</strong><span>{editing ? "내 표현으로 수정 중" : "빈칸을 남긴 준비 초안"}</span></div>
        {draft.sections.map(section => {
          const question = questions.find(item => item.id === section.question_id);
          const text = editing ? edited[section.question_id] ?? section.text : section.text;
          const measured = question ? measureDraftLength(text, question.length_limit) : { character_count: Array.from(text).length, length_status: "unknown" };
          const limit = question?.length_limit.status === "known" ? question.length_limit.max : null;
          return <article key={section.question_id} className={styles.draftSection}>
            <h3>{section.title}</h3>
            {editing ? <textarea className={styles.editedDraft} aria-label={`${section.title} 내 표현으로 수정`} value={text} onChange={event => dispatchEdit({ type: "edit", questionId: section.question_id, text: event.target.value })} rows={7} /> : <pre>{text}</pre>}
            <p className={`${styles.characterCount} ${measured.length_status === "over_limit" ? styles.overLimit : ""}`}>{measured.character_count}자{limit ? ` / ${limit}자 · 공백 포함 참고 계산` : " · 공식 글자수 제한 미확인"}{measured.length_status === "over_limit" ? " · 제한 초과" : ""}</p>
          </article>;
        })}
        {draft.cautions.filter(item => item !== aiCaution).map((item, index) => <div key={`${item.kind}-${index}`} className={styles.caution}>{item.message}{item.source_refs.length > 0 && <><br />{sourceLinks(item.source_refs)}</>}</div>)}
      </div>
    </div>
    <details className={styles.writingEvidence}>
      <summary>공식 문항·합격 후기·검증 범위 보기</summary>
      <p>{record.round.label}의 자료를 구분해서 보존합니다. 개인 후기는 공식 선발 기준이나 제출 원고로 사용하지 않습니다.</p>
      <ul>{record.official_sources.map(source => <li key={source.id}><a href={source.url} target="_blank" rel="noreferrer">{sourceKindLabels[source.source_kind] ?? "공식 출처"} · {source.title} ↗</a><br />발행일: {source.published_at ?? "미확인"} · {source.verification_scope}</li>)}{record.unofficial_sources.map(source => <li key={source.id}><a href={source.url} target="_blank" rel="noreferrer">{sourceKindLabels[source.source_kind] ?? "비공식 출처"} · {source.title} ↗</a><br />발행일: {source.published_at ?? "미확인"} · {source.verification_scope}{source.summary && <p>{source.summary}</p>}</li>)}</ul>
      <p>자료의 공개 접근과 재사용 허용은 별개입니다. 확인된 재사용 권리 범위를 기록하고, 후기의 전체 원문이나 다른 사람의 경험을 내 초안으로 가져오지 않습니다.</p>
    </details>
  </section>;
}
