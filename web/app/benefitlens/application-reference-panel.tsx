import type { ApplicationExample, SubmissionDocument } from '@/lib/application-package';
import { referenceHref } from '@/lib/application-package';

type Props = { pack: ApplicationExample; checked: Record<string, boolean>; onCheck: (id: string, value: boolean) => void };

export default function ApplicationReferencePanel({pack, checked, onCheck}: Props) {
  const document = (item: SubmissionDocument) => <li key={item.id}>
    <label><input type="checkbox" checked={checked[item.id] ?? false} onChange={event => onCheck(item.id, event.target.checked)} /><span><strong>{item.title}</strong><small>{item.applicability}</small></span></label>
    <ul>{item.checks.map(check => <li key={check}>{check}</li>)}</ul>
    <a href={referenceHref(item.evidence)} target="_blank" rel="noreferrer">{item.evidence.label}</a>
  </li>;
  return <div className="application-reference">
    <section className="application-example-banner" aria-label="과거 회차 연결 예제">
      <div><span>과거 회차 연결 예제, 접수 종료</span><h2>{pack.title}</h2><p>{pack.round}</p></div>
      <p>{pack.audience}. 신청 마감은 2026년 4월 21일 15시였습니다.</p>
      <a href={pack.officialNotice} target="_blank" rel="noreferrer">해당 회차 공식 공고</a>
    </section>
    <div className="application-reference-grid">
      <section className="submission-checklist" aria-label="공식 제출서류">
        <h3>제출 준비</h3><p>요강의 “10. 제출 서류”를 연결했습니다. 체크는 이번 화면의 준비 메모입니다.</p>
        <ul className="submission-documents">{pack.documents.map(document)}</ul>
        <details><summary>해당자 추가 서류 {pack.optionalDocuments.length}종</summary><ul className="submission-documents">{pack.optionalDocuments.map(document)}</ul></details>
        <details><summary>파일 제출 기준</summary><ul>{pack.fileChecks.map(check => <li key={check}>{check}</li>)}</ul><a href={referenceHref(pack.fileEvidence)} target="_blank" rel="noreferrer">{pack.fileEvidence.label}</a></details>
      </section>
      <section className="success-reference" aria-label="같은 회차 합격 후기">
        <h3>같은 회차 합격 후기</h3><p className="reference-status">{pack.success.round}, 최종합격 자기보고, 기관 대조 미확인</p>
        <ul>{pack.success.context.map(context => <li key={context}>{context}</li>)}</ul>
        <p>아래 문항 옆에서 이 사례의 준비 방식을 참고합니다. 본인의 경험과 계획을 입력하세요.</p>
        <a href={pack.success.url} target="_blank" rel="noreferrer">합격 후기 원문</a>
        <details><summary>후기 확인 범위</summary><ul>{pack.success.unknown.map(item => <li key={item}>{item}</li>)}</ul><p>재사용 허가 미확인. 후기 전문과 타인의 제출 답변은 보관하거나 초안에 복사하지 않습니다.</p></details>
        <details><summary>{pack.history.round}</summary><ul>{pack.history.changes.map(change => <li key={change}>{change}</li>)}</ul><a href={referenceHref(pack.history.evidence)} target="_blank" rel="noreferrer">{pack.history.evidence.label}</a></details>
      </section>
    </div>
    <details className="application-stages"><summary>접수, 면접과 선발 후 단계</summary><ol>{pack.stages.map(stage => <li key={stage.title}><strong>{stage.title}</strong><p>{stage.description} <a href={referenceHref(stage.evidence)} target="_blank" rel="noreferrer">{stage.evidence.label}</a></p></li>)}</ol><a href={referenceHref(pack.periodEvidence)} target="_blank" rel="noreferrer">{pack.periodEvidence.label}</a></details>
    <details className="application-review-scope"><summary>자료 연결의 확인 범위</summary><p>{pack.referenceReview.checkedAt}, 같은 회차 공식 요강 저장본 {pack.referenceReview.pages.join(', ')}쪽을 확인했습니다.</p><p>{pack.referenceReview.scope}. 전체 공고의 자격 승인과 구분합니다.</p><small>저장본 SHA-256: {pack.referenceReview.sha256}</small></details>
  </div>;
}
