"use client";

import { useEffect, useMemo, useState } from "react";
import type { PersonalProfile } from "@/lib/browser-profile";
import type { Match } from "@/lib/eligibility";
import { questionsForMatch, type PublicDepartment, type QuestionField } from "@/lib/profile-assistance";

type Props = {
  profile: PersonalProfile;
  departments: PublicDepartment[];
  match: Match | null;
  onUpdate: (field: keyof PersonalProfile, value: string) => void;
  onAffiliation: (value: string) => void;
};
type FieldDefinition = { label: string; options?: Array<[string, string]>; type?: string; min?: number; max?: number; step?: string; help?: string };
const yesNo: Array<[string, string]> = [["true", "예"], ["false", "아니요"]];
const fields: Partial<Record<keyof PersonalProfile, FieldDefinition>> = {
  academicLevel: {label: "학위 과정", options: [["undergraduate", "학부"], ["master", "석사"], ["doctoral", "박사"]]},
  enrollmentStatus: {label: "학적 상태", options: [["enrolled", "재학"], ["leave", "휴학"], ["expected_graduation", "졸업예정"], ["graduated", "졸업"]]},
  nationality: {label: "국적", options: [["KR", "대한민국 국적"], ["FOREIGN", "외국 국적"]]},
  age: {label: "만 나이", type: "number", min: 0, max: 100},
  birthYear: {label: "출생연도", type: "number", min: 1900, max: 2100, help: "출생연도만으로 판정이 갈리면 만 나이를 확인합니다."},
  fullTimeStudent: {label: "전일제 대학원생", options: yesNo},
  highestDegreeLevel: {label: "최종 취득 학위", options: [["high_school", "고등학교"], ["associate", "전문학사"], ["bachelor", "학사"], ["master", "석사"], ["doctoral", "박사"]]},
  year: {label: "학년", type: "number", min: 1, max: 6},
  graduateSemester: {label: "대학원 현재 학기", type: "number", min: 1, max: 30, help: "수집한 공식 학칙이 있는 공고는 남은 수업연한을 계산합니다."},
  courseworkCompleted: {label: "Coursework 수료", options: yesNo},
  previousSemesterCredits: {label: "직전 학기 이수학점", type: "number", min: 0, max: 40},
  gpa: {label: "평점", type: "number", min: 0, step: "0.01", help: "만점이 다른 성적은 임의 환산하지 않습니다."},
  gpaScale: {label: "평점 만점", options: [["4.0", "4.0 만점"], ["4.3", "4.3 만점"], ["4.5", "4.5 만점"]]},
  gpaBasis: {label: "성적 평가 기준", options: [["previous_semester", "직전 학기"], ["cumulative", "누적 성적"]]},
  gpaEvaluationPeriod: {label: "성적 평가 학기", help: "공고가 특정 학기를 지정한 경우 2026-1처럼 입력합니다."},
  incomeBand: {label: "학자금지원구간", options: Array.from({length: 11}, (_, i) => [String(i), `${i}구간`])},
  residenceCity: {label: "본인 주민등록 지역"},
  ancestorResidenceCity: {label: "직계존속 주민등록 지역"},
  residenceMonths: {label: "연속 거주 개월", type: "number", min: 0},
  regionalHighSchool: {label: "비수도권 고교 졸업", options: yesNo},
  topikLevel: {label: "TOPIK 등급", options: Array.from({length: 6}, (_, i) => [String(i + 1), `${i + 1}급`])},
  teamSize: {label: "지원 팀 인원", type: "number", min: 1},
  employmentStatus: {label: "취업 상태", options: [["unemployed", "미취업 또는 구직"], ["employed", "취업"], ["self_employed", "자영업"]]},
  industryEmployee: {label: "산업체 재직", options: yesNo},
  expectedGraduationMonth: {label: "졸업 예정 월", type: "month"},
  militaryServiceMonths: {label: "병역 의무복무 개월", type: "number", min: 0, max: 120},
  remainingStudyMonths: {label: "남은 정규 수업연한 (개월)", type: "number", min: 0, help: "개인 졸업 계획이 아닌 학교의 정규 수업연한 기준입니다."},
  codetreePaid: {label: "코드트리 유료 결제 이력", options: yesNo},
  codetreeSubscribed: {label: "코드트리 현재 유료 구독", options: yesNo},
  codetreePriorChallenge: {label: "양떼 목장 챌린지 이전 참여", options: yesNo},
  codetreeBc3FreePass: {label: "청약통장 챌린지 무료 학습권 수혜", options: yesNo},
};

export function AssistedProfile({profile, departments, match, onUpdate, onAffiliation}: Props) {
  const plan = useMemo(() => match ? questionsForMatch(profile, match) : null, [profile, match]);
  const [askedQuestions, setAskedQuestions] = useState(() => plan?.questions ?? []);
  const [showAdvanced, setShowAdvanced] = useState(false);
  useEffect(() => {
    if (!plan) return;
    // Keep exposed editors mounted while the user types a multi-digit value.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAskedQuestions(current => {
      const merged = new Map(current.map(question => [question.field, question]));
      for (const question of plan.questions) if (!merged.has(question.field)) merged.set(question.field, question);
      return merged.size === current.length ? current : [...merged.values()];
    });
  }, [plan]);
  const displayedQuestions = [...new Map([...askedQuestions, ...(plan?.questions ?? [])].map(question => [question.field, question])).values()];
  const groupedDepartments = [...departments.reduce((groups, item) => groups.set(item.code, [...(groups.get(item.code) ?? []), item.department]), new Map<string, string[]>())];
  function editor(field: QuestionField, prefix: string) {
    const id = `${prefix}-${String(field)}`;
    if (field === "affiliation") return <label key={id} htmlFor={id}><span>소속 학교 또는 학과</span><select id={id} value={profile.department && profile.institution === "POSTECH" ? `department:${profile.department}` : profile.institution} onChange={event => onAffiliation(event.target.value)}><option value="">입력 없이 보기</option><option value="POSTECH">POSTECH, 학과 선택 안 함</option><option value="OTHER">다른 학교</option><optgroup label="POSTECH 학과와 대학원">{groupedDepartments.map(([code, names]) => <option key={code} value={`department:${code}`}>POSTECH {names.join(", ")}</option>)}</optgroup></select><small>학과를 선택하면 학교 소속을 함께 연결합니다.</small></label>;
    const definition = fields[field];
    if (!definition) return null;
    const value = typeof profile[field] === "string" ? profile[field] : "";
    return <label key={id} htmlFor={id}><span>{definition.label}</span>{field === "gpa" ? <span className="compound-field"><input id={id} type="number" min="0" max={profile.gpaScale || undefined} step="0.01" value={value} onChange={event => onUpdate(field, event.target.value)} /><select aria-label="평점 만점" value={profile.gpaScale} onChange={event => onUpdate("gpaScale", event.target.value)}><option value="">만점 미입력</option><option value="4.0">4.0 만점</option><option value="4.3">4.3 만점</option><option value="4.5">4.5 만점</option></select></span> : definition.options ? <select id={id} value={value} onChange={event => onUpdate(field, event.target.value)}><option value="">모름 또는 건너뛰기</option>{definition.options.map(([option, label]) => <option key={option} value={option}>{label}</option>)}</select> : <input id={id} type={definition.type ?? "text"} min={definition.min} max={definition.max} step={definition.step} maxLength={150} value={value} onChange={event => onUpdate(field, event.target.value)} />}{definition.help && <small>{definition.help}</small>}</label>;
  }
  const basicFields: QuestionField[] = ["affiliation", "academicLevel", "enrollmentStatus"];
  const advancedFields = (Object.keys(fields) as Array<keyof PersonalProfile>).filter(field => !basicFields.includes(field));
  return <div className="profile-form assisted-profile">
    <p className="profile-guidance">입력하지 않아도 모든 지원을 볼 수 있습니다. 아래 3가지만 알려주면 입력한 조건에 맞는 후보부터 보여드립니다.</p>
    <div className="field-grid quick-profile" aria-label="선택 입력 3개">{basicFields.map(field => editor(field, "quick"))}</div>
    <section className="selected-profile-questions" aria-label="선택한 공고에 필요한 정보">
      <h3>{match ? "이 공고에 필요한 정보만" : "마음에 드는 공고부터 고르세요"}</h3>
      {match ? <>
        <p className="selected-profile-name">{match.name}</p>
        {plan?.alternative && <p>여러 자격 경로 중 질문이 적은 경로부터 확인합니다. 다른 경로는 카드의 전체 조건에서 볼 수 있습니다.</p>}
        {displayedQuestions.length > 0 && <><p>이 공고의 확인 항목 {displayedQuestions.length}개. 모르는 값은 건너뛰세요.</p><div className="field-grid">{displayedQuestions.map(question => <div className="profile-question" key={String(question.field)}>{basicFields.includes(question.field) ? <a className="basic-question-link" href={`#quick-${String(question.field)}`}>위의 {question.field === "affiliation" ? "소속" : fields[question.field as keyof PersonalProfile]?.label}에서 선택</a> : editor(question.field, "selected")}<div className="question-reason"><span>필요한 이유</span><ul className="condition-list">{question.reasons.map(reason => <li key={reason}>{reason.replaceAll("\u00b7", ", ")}</li>)}</ul></div></div>)}</div></>}
        <p role="status">{match.eligibilityResult === "fail" ? "입력한 정보와 맞지 않는 조건이 있습니다. 해당 조건을 카드에서 확인하세요." : match.eligibilityResult === "pass" ? "추가 입력 없이 공개 자격 범위를 비교했습니다. 카드의 모집 기간과 원문 보완 상태를 확인하세요." : !displayedQuestions.length ? "공개 조건 중 구조화되지 않은 항목은 공식 원문을 보완해야 합니다." : `판정에 필요한 미확인 항목 ${plan?.questions.length ?? 0}개가 남았습니다.`}</p>
        {match.derivedFacts.map((fact, index) => <p className="assisted-derived" key={index}>{fact.text} <a href={fact.sourceUrl} target="_blank" rel="noreferrer">계산 근거</a></p>)}
      </> : <p>지원사업과 공식 조건은 이미 수집되어 있습니다. 공고에서 준비하기를 누르면 아직 모르는 정보 중 필요한 것만 물어봅니다.</p>}
    </section>
    <details className="service-history advanced-profile" open={showAdvanced} onToggle={event => setShowAdvanced(event.currentTarget.open)}><summary>나머지 정보 직접 수정 (선택)</summary>{showAdvanced && <><p>선택한 공고에서 요청하지 않은 정보는 입력하지 않아도 됩니다.</p><div className="field-grid">{advancedFields.map(field => editor(field, "advanced"))}</div></>}</details>
  </div>;
}
