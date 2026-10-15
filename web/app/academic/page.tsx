"use client";

import { useMemo, useState } from "react";
import { academicDemoNotices, type AcademicDemoNotice } from "@/lib/academic-demo-data";
import {
  evaluateAcademicRequirements,
  type AcademicProfile,
  type ComparisonResult,
  type ComparisonTrace,
  type EnglishTest,
  type GradeMetric,
  type PathRequirements,
  type RequirementComparison,
  type SourcePath,
} from "@/lib/academic-condition-engine";
import WritingPanel from "./writing-panel";
import styles from "./academic.module.css";

type TestInput = { id: number; test: EnglishTest; score: string; date: string };
type UncomparedItem = { id?: string; text: string; locator?: string; evidence_ids?: string[] };

const testLabels: Record<EnglishTest, string> = {
  TOEIC: "TOEIC L&R", TOEFL_iBT: "TOEFL iBT", IELTS: "IELTS", NEW_TEPS: "NEW TEPS",
  TOEIC_SPEAKING: "TOEIC Speaking", OPIc: "OPIc", TEPS_SPEAKING: "TEPS Speaking",
};
const pathLabels: Record<SourcePath, string> = {
  undergraduate_enrolled: "학부", graduate_new: "대학원 신입",
  graduate_enrolled: "대학원 재학", undergraduate: "학부", graduate: "대학원",
};
const resultLabels: Record<ComparisonResult, string> = { pass: "요건 충족", fail: "요건 미충족", unknown: "확인 필요" };
const reasonLabels: Record<string, string> = {
  grade_missing_or_context_mismatch: "점수가 없거나 공고가 지정한 성적 기간·과정과 다릅니다.",
  invalid_or_unconfirmed_grade_fact: "입력한 성적이나 만점을 확인해 주세요.",
  different_configured_scale_branch: "이 입력은 다른 만점 기준의 분기에서 비교합니다.",
  scale_mismatch_no_conversion: "공고와 만점이 달라 확인이 필요합니다. GPA를 임의로 환산하지 않습니다.",
  conflicting_grade_facts: "같은 기간·과정의 점수가 서로 달라 확인이 필요합니다.",
  compared_exact_metric_scale_period_context: "같은 성적 종류·만점·평가기간·과정으로 비교했습니다.",
  configured_out_of_demo_scope: "원문 기준은 보존했지만 이 시험의 비교 방식은 아직 검증하지 않았습니다.",
  unconfirmed_english_fact: "영어성적의 확인 상태가 불명확합니다.",
  missing_or_invalid_test_date: "응시일이 없거나 올바른 날짜가 아닙니다.",
  invalid_numeric_score: "시험 점수를 숫자로 입력해 주세요.",
  invalid_fact_domain: "시험의 점수 범위·단위를 확인해 주세요.",
  score_scale_missing_or_mismatch_no_conversion: "시험의 점수 척도를 확인해 주세요. 척도를 임의로 환산하지 않습니다.",
  ordered_score_mapping_unavailable: "등급 사이의 순서를 확인하지 못해 비교를 보류했습니다.",
  invalid_ordered_score_domain: "확인되지 않은 시험 등급입니다.",
  outside_explicit_inclusive_date_window: "응시일이 이 공고에서 정한 기간 밖입니다.",
  compared_score_and_explicit_date_window: "시험 점수와 공고의 응시일 구간을 함께 비교했습니다.",
  compared_score_without_inferred_expiry: "명시된 점수로 비교했습니다. 원문에 없는 유효기간은 추가하지 않습니다.",
  absent_from_declared_complete_comparison_inventory: "모두 입력했다고 표시한 시험 목록에 이 시험이 없습니다.",
  english_fact_missing_inventory_incomplete: "다른 대체시험 성적이 있는지 아직 확인하지 않았습니다.",
  or_over_entered_records_of_same_test: "같은 시험의 입력 기록 중 하나라도 기준을 만족하는지 확인했습니다.",
  unrecognized_test_type: "비교 목록에 없는 시험 종류가 있어 확인이 필요합니다.",
  education_path_missing_or_invalid: "공고의 학부·대학원 대상 분기를 선택해 주세요.",
  source_path_missing_or_inconsistent: "공고의 대상 분기와 선택한 학위가 다릅니다.",
  invalid_rule_config: "공고 규격을 확인해야 합니다.",
  english_requirement_unresolved: "원문에서 영어요건의 적용 여부를 확인하지 못했습니다.",
  grade_requirement_unresolved: "원문에서 성적요건을 확인하지 못했습니다.",
  not_applicable: "선택한 대상 분기에는 이 요건을 적용하지 않습니다.",
};

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function shown(value: unknown, fallback = "미확인") { return typeof value === "string" || typeof value === "number" ? String(value) : fallback; }
function firstPath(notice: AcademicDemoNotice): SourcePath {
  return notice.ruleSet.paths.graduate_enrolled ? "graduate_enrolled" : Object.keys(notice.ruleSet.paths)[0] as SourcePath;
}
function contextKey(path: PathRequirements | undefined) {
  const grade = path?.grade.alternatives[0];
  return grade ? `${grade.period}|${grade.context}` : "";
}
function initialGrade(notice: AcademicDemoNotice, path: SourcePath) {
  return notice.ruleSet.paths[path]?.grade.alternatives[0];
}
function Status({ result, notApplicable = false }: { result: ComparisonResult; notApplicable?: boolean }) {
  return <span className={`${styles.status} ${notApplicable ? styles.notApplicable : styles[result]}`}>{notApplicable ? "적용 없음" : resultLabels[result]}</span>;
}

function ConditionCard({ title, comparison, path, notice, stage }: {
  title: string; comparison: RequirementComparison; path: PathRequirements | undefined; notice: AcademicDemoNotice; stage: "grade" | "english";
}) {
  const requirement = path?.[stage];
  const nonApplicable = comparison.applicability === "not_applicable";
  const summary = stage === "grade"
    ? path?.grade.alternatives.map(rule => `${rule.metric === "GPA" ? "GPA" : "백분위"} ${rule.minimum_score} / ${rule.scale} 이상`).join(" 또는 ")
    : path?.english.alternatives.map(rule => `${testLabels[rule.test_type]} ${rule.minimum_score} 이상`).join(" · ");
  const relevantEvidence = new Set([
    ...(requirement?.evidence_ids ?? []),
    ...(requirement?.alternatives.flatMap(rule => rule.evidence_ids) ?? []),
    ...(stage === "english" ? path?.english.alternatives.flatMap(rule => rule.explicit_date_window?.evidence_ids ?? []) ?? [] : []),
  ]);
  const evidence = notice.ruleSet.source.evidence.filter(item => relevantEvidence.has(item.id));
  const catalogIds = new Set(path?.english.alternatives.flatMap(rule => Array.isArray(rule.domain_evidence_ids) ? rule.domain_evidence_ids.filter((id): id is string => typeof id === "string") : []) ?? []);
  const catalogEvidence = stage === "english" ? notice.ruleSet.source.evidence.filter(item => item.evidence_kind === "exam_catalog" && catalogIds.has(item.id)) : [];
  const renderedTraces = comparison.traces.flatMap(trace => trace.records?.length ? trace.records : [trace]);
  const visibleTraces = renderedTraces.filter(trace => stage === "grade" || trace.actual !== null && trace.actual !== undefined || trace.result === "unknown");
  return <article className={styles.conditionCard}>
    <div className={styles.conditionTop}><h3>{title}</h3><Status result={comparison.result} notApplicable={nonApplicable} /></div>
    <p className={styles.conditionSummary}>{nonApplicable ? requirement?.reason ?? "원문의 이 대상 분기에는 별도 요건이 없습니다." : summary || "원문 확인 필요"}</p>
    {stage === "english" && !nonApplicable && <p className={styles.reason}>대체시험 중 하나를 충족하면 됩니다. 점수와 응시일은 함께 확인합니다.</p>}
    {stage === "grade" && notice.id === "pohang-regional-2026" && <p className={styles.reason}>직전학기 성적표의 직접 비교입니다. 교환으로 성적 산정이 어려운 경우의 대체학기는 이번 비교 범위 밖입니다.</p>}
    {comparison.reasons.slice(0, 2).map(reason => <p key={reason} className={styles.reason}>{reasonLabels[reason] ?? "해당 조건을 확인할 자료가 부족합니다."}</p>)}
    {visibleTraces.length > 0 && <div className={styles.trace}>{visibleTraces.map((trace, index) => <TraceLine key={`${trace.rule_id}-${index}`} trace={trace} notice={notice} stage={stage} />)}</div>}
    <details className={styles.sourceDetail}>
      <summary>요건 원문 근거</summary>
      {evidence.map(item => <p key={item.id}>{item.locator}<br />{item.quote}</p>)}
      <a href={notice.ruleSet.source.url} target="_blank" rel="noreferrer">공식 출처에서 확인 ↗</a>
    </details>
    {catalogEvidence.length > 0 && <details className={styles.sourceDetail}>
      <summary>시험 점수 척도의 별도 근거</summary>
      <p>시험기관의 설명을 검토해 요약한 자료입니다. 장학 공고의 지원 기준과 구분하며, 서로 다른 척도를 환산하는 데 사용하지 않습니다.</p>
      {catalogEvidence.map(item => {
        const rule = path?.english.alternatives.find(alternative => Array.isArray(alternative.domain_evidence_ids) && alternative.domain_evidence_ids.includes(item.id));
        const url = typeof item.url === "string" && item.url.startsWith("https://") ? item.url : null;
        return <p key={item.id}><strong>{rule ? testLabels[rule.test_type] : "시험 척도"}</strong><br />{item.quote}<br /><small>{item.locator}<br />확인일: {shown(item.checked_at)}<br />척도 버전: {shown(item.catalog_version)}</small>{url && <><br /><a href={url} target="_blank" rel="noreferrer">시험기관의 설명 ↗</a></>}</p>;
      })}
    </details>}
  </article>;
}

function TraceLine({ trace, notice, stage }: { trace: ComparisonTrace; notice: AcademicDemoNotice; stage: "grade" | "english" }) {
  const expected = object(trace.expected) ? trace.expected : {};
  const prefix = stage === "english" && typeof expected.test_type === "string" ? testLabels[expected.test_type as EnglishTest] : "성적";
  const source = notice.ruleSet.source.evidence.find(item => trace.evidence_ids.includes(item.id));
  return <div className={styles.traceItem}>
    <span className={`${styles.traceDot} ${styles[trace.result]}`} aria-hidden="true" />
    <span><strong>{prefix} · {resultLabels[trace.result]}</strong><br />{reasonLabels[trace.reason] ?? "입력 기록과 원문 조건을 비교했습니다."}{source && <small><br />{source.locator}</small>}</span>
  </div>;
}

function unpackUncompared(items: unknown[]): UncomparedItem[] {
  return items.flatMap((item, index) => typeof item === "string" ? [{ id: String(index), text: item }] : object(item) && typeof item.text === "string" ? [{ id: shown(item.id, String(index)), text: item.text, locator: typeof item.locator === "string" ? item.locator : undefined, evidence_ids: Array.isArray(item.evidence_ids) ? item.evidence_ids.filter((id): id is string => typeof id === "string") : [] }] : []);
}

export default function AcademicDemoPage() {
  const [noticeId, setNoticeId] = useState(academicDemoNotices[0].id);
  const notice = academicDemoNotices.find(item => item.id === noticeId) ?? academicDemoNotices[0];
  const [pathKey, setPathKey] = useState<SourcePath>(() => firstPath(academicDemoNotices[0]));
  const path = notice.ruleSet.paths[pathKey];
  const [metric, setMetric] = useState<GradeMetric>(() => initialGrade(academicDemoNotices[0], firstPath(academicDemoNotices[0]))?.metric ?? "percentile");
  const [gradeScore, setGradeScore] = useState("");
  const [gradeScale, setGradeScale] = useState(() => String(initialGrade(academicDemoNotices[0], firstPath(academicDemoNotices[0]))?.scale ?? 100));
  const [gradeContext, setGradeContext] = useState(() => contextKey(academicDemoNotices[0].ruleSet.paths[firstPath(academicDemoNotices[0])]));
  const [tests, setTests] = useState<TestInput[]>([{ id: 1, test: "TOEIC", score: "", date: "" }]);
  const [completeInventory, setCompleteInventory] = useState(false);

  const profile = useMemo<AcademicProfile>(() => {
    const [period = "", context = ""] = gradeContext.split("|");
    return {
      education_path: path?.education_path, source_path: pathKey,
      grades: gradeScore.trim() ? [{ metric, score: Number(gradeScore), scale: Number(gradeScale), period, context }] : [],
      english: tests.filter(test => test.score.trim() || test.date).map(test => {
        const alternative = path?.english.alternatives.find(rule => rule.test_type === test.test);
        return { test_type: test.test, score: test.test === "OPIc" ? test.score : test.score.trim() ? Number(test.score) : Number.NaN, test_date: test.date, ...(alternative?.score_scale !== undefined ? { score_scale: alternative.score_scale } : {}) };
      }),
      english_inventory_complete: completeInventory,
    };
  }, [path, pathKey, gradeContext, metric, gradeScore, gradeScale, tests, completeInventory]);
  const comparison = useMemo(() => evaluateAcademicRequirements(profile, notice.ruleSet), [profile, notice]);
  const contextOptions = [...new Set(path?.grade.alternatives.map(rule => `${rule.period}|${rule.context}`) ?? [])];
  const uncompared = unpackUncompared(comparison.uncompared_requirements);

  function chooseNotice(id: string) {
    const next = academicDemoNotices.find(item => item.id === id) ?? academicDemoNotices[0];
    const key = firstPath(next), grade = initialGrade(next, key);
    setNoticeId(id); setPathKey(key); setMetric(grade?.metric ?? "GPA"); setGradeScore("");
    setGradeScale(String(grade?.scale ?? 4.5)); setGradeContext(contextKey(next.ruleSet.paths[key]));
    setTests([{ id: 1, test: "TOEIC", score: "", date: "" }]); setCompleteInventory(false);
  }
  function choosePath(key: SourcePath) { setPathKey(key); setGradeContext(contextKey(notice.ruleSet.paths[key])); }
  function updateTest(id: number, patch: Partial<TestInput>) { setTests(current => current.map(test => test.id === id ? { ...test, ...patch } : test)); }
  function preset(kind: "boundary" | "gpa" | "date" | "pohang") {
    const target = academicDemoNotices.find(item => item.id === (kind === "pohang" ? "pohang-regional-2026" : "jeongmonggu-2025")) ?? notice;
    const key = firstPath(target), grade = initialGrade(target, key), english = target.ruleSet.paths[key]?.english.alternatives.find(rule => rule.test_type === "TOEIC");
    setNoticeId(target.id); setPathKey(key); setGradeContext(contextKey(target.ruleSet.paths[key]));
    setMetric(kind === "gpa" ? "GPA" : grade?.metric ?? "GPA");
    setGradeScore(kind === "gpa" ? "4.0" : String(grade?.minimum_score ?? 2.5));
    setGradeScale(kind === "gpa" ? "4.5" : String(grade?.scale ?? 4.5));
    setTests([{ id: 1, test: "TOEIC", score: kind === "pohang" ? "" : String(english?.minimum_score ?? 850), date: kind === "pohang" ? "" : kind === "date" ? "2023-03-31" : english?.explicit_date_window?.from ?? "2023-04-01" }]);
    setCompleteInventory(kind === "date");
  }

  return <div className={styles.shell}>
    <header className={styles.topbar}>
      <a className={styles.brand} href="/academic"><span className={styles.mark}>⌃</span><span>BenefitLens</span></a>
      <nav className={styles.nav} aria-label="주 메뉴"><a href="/academic" className={styles.active} aria-current="page">성적·어학 비교</a><a href="#writing-prep">초안 준비</a></nav>
      <span className={styles.localBadge}>LOCAL DEMO</span>
    </header>
    <main className={styles.main}>
      <section className={styles.intro}>
        <div><p className={styles.eyebrow}>BENEFITLENS · 조건 비교 데모</p><h1>점수로 비교하고,<br />근거로 확인하세요.</h1><p>공고가 요구하는 성적·어학 기준을 입력값과 비교합니다. 다른 필수조건과 선발 기준은 원문 근거와 함께 남겨 둡니다.</p></div>
        <div className={styles.historical}><strong>과거 공고로 보는 데모</strong><span>2025·2026 모집 · 접수 종료</span></div>
      </section>
      <section className={styles.noticeBar} aria-label="비교할 공고">
        <label>비교할 공고<select value={noticeId} onChange={event => chooseNotice(event.target.value)}>{academicDemoNotices.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
        <div className={styles.noticeSummary}><strong>{notice.ruleSet.source.title}</strong><p>{notice.description}<a href={notice.ruleSet.source.url} target="_blank" rel="noreferrer">원문 ↗</a></p></div>
      </section>
      <div className={styles.workspace}>
        <section className={styles.inputs} aria-label="성적과 어학 입력">
          <div className={styles.panelHeading}><div><small>MY SCORES</small><h2>비교할 정보</h2></div><button type="button" className={styles.reset} onClick={() => chooseNotice(noticeId)}>초기화 ↺</button></div>
          <p className={styles.inputNote}>이 화면의 입력은 현재 탭에만 남습니다. 이름·학번·증명서를 입력할 필요가 없습니다.</p>
          <label className={styles.field}><span>공고의 대상 분기</span><select value={pathKey} onChange={event => choosePath(event.target.value as SourcePath)}>{Object.keys(notice.ruleSet.paths).map(key => <option key={key} value={key}>{pathLabels[key as SourcePath]}</option>)}</select><small>학부·대학원에 따라 적용되는 점수와 평가기간이 다릅니다.</small></label>
          <div className={styles.formSection}>
            <h3>01 · 성적</h3>
            <div className={styles.segmented} aria-label="성적 종류">{(["GPA", "percentile"] as GradeMetric[]).map(value => <button key={value} type="button" aria-pressed={metric === value} className={metric === value ? styles.chosen : ""} onClick={() => { setMetric(value); setGradeScale(value === "percentile" ? "100" : "4.5"); }}>{value === "GPA" ? "GPA" : "백분위"}</button>)}</div>
            <div className={styles.fieldGrid}>
              <label className={styles.field}><span>점수</span><input inputMode="decimal" type="number" step="any" value={gradeScore} onChange={event => setGradeScore(event.target.value)} placeholder={metric === "GPA" ? "예: 2.5" : "예: 90"} /></label>
              <label className={styles.field}><span>만점</span><select value={gradeScale} onChange={event => setGradeScale(event.target.value)}>{(metric === "percentile" ? ["100", ""] : ["4.5", "4.3", "4.0", "5.0", ""]).map(scale => <option key={scale} value={scale}>{scale || "미확인"}</option>)}</select></label>
              <label className={`${styles.field} ${styles.wide}`}><span>평가기간·성적표 과정</span><select value={gradeContext} onChange={event => setGradeContext(event.target.value)}>{contextOptions.map(key => <option key={key} value={key}>{notice.gradeContextLabels?.[key] ?? key.replace("|", " · ")}</option>)}<option value="">다른 기간 또는 미확인</option></select><small>GPA를 백분위로 바꾸거나 다른 만점으로 환산하지 않습니다.</small></label>
            </div>
          </div>
          <div className={styles.formSection}>
            <h3>02 · 영어시험</h3>
            {path?.english.applicability === "not_applicable" ? <div className={styles.emptyEnglish}>{path.english.reason ?? "이 대상 분기의 원문에는 영어 점수 기준이 없습니다."}</div> : <>
              {tests.map((test, index) => {
                const rule = path?.english.alternatives.find(item => item.test_type === test.test);
                return <div key={test.id} className={styles.englishRow}>
                  <div className={styles.englishRowLabel}><span>시험 기록 {index + 1}</span>{tests.length > 1 && <button onClick={() => setTests(current => current.filter(item => item.id !== test.id))} type="button" aria-label={`시험 기록 ${index + 1} 삭제`}>삭제</button>}</div>
                  <div className={styles.fieldGrid}>
                    <label className={`${styles.field} ${styles.wide}`}><span>시험 종류</span><select value={test.test} onChange={event => updateTest(test.id, { test: event.target.value as EnglishTest, score: "" })}>{Object.entries(testLabels).map(([value, label]) => <option key={value} value={value}>{label}{path?.english.alternatives.find(item => item.test_type === value)?.supported === false ? " · 비교 미검증" : ""}</option>)}</select></label>
                    <label className={styles.field}><span>{test.test === "OPIc" ? "등급" : "점수"}</span><input inputMode={test.test === "OPIc" ? "text" : "decimal"} type={test.test === "OPIc" ? "text" : "number"} step="any" value={test.score} onChange={event => updateTest(test.id, { score: event.target.value })} placeholder={shown(rule?.minimum_score, "점수")} /></label>
                    <label className={styles.field}><span>응시일</span><input type="date" value={test.date} onChange={event => updateTest(test.id, { date: event.target.value })} /></label>
                  </div>
                  {rule?.score_domain?.maximum !== undefined && <p className={styles.profileCaution}>이 공고의 시험 척도: {rule.score_domain.maximum}점{test.test === "TOEFL_iBT" ? " · 2025 모집에 적용되는 기존 척도" : ""}</p>}
                  {rule?.explicit_date_window && <p className={styles.profileCaution}>인정 응시일: {rule.explicit_date_window.from} ~ {rule.explicit_date_window.to} (양끝 포함)</p>}
                </div>;
              })}
              <button type="button" className={styles.addTest} onClick={() => setTests(current => [...current, { id: Math.max(...current.map(item => item.id), 0) + 1, test: "TOEIC", score: "", date: "" }])}>＋ 다른 시험 기록 추가</button>
              <label className={styles.check}><input type="checkbox" checked={completeInventory} onChange={event => setCompleteInventory(event.target.checked)} /><span>비교할 보유 영어시험 성적을 모두 입력했습니다.<br />선택하지 않으면, 입력하지 않은 대체시험은 미확인으로 남습니다.</span></label>
            </>}
          </div>
          <button type="button" className={styles.compareButton} onClick={() => document.getElementById("academic-results")?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" })}>성적·어학 요건 비교<span aria-hidden="true">→</span></button>
          <p className={styles.profileCaution}>입력과 결과는 이 화면에서 바로 갱신됩니다. 전체 지원가능 여부나 합격을 판단하지 않습니다.</p>
        </section>
        <section className={styles.results} id="academic-results" aria-label="비교 결과" aria-live="polite">
          <div className={styles.resultHead}><div><small>CONDITION CHECK</small><h2>성적·어학 요건 비교</h2></div><Status result={comparison.comparison_result} /></div>
          <p className={styles.scope}>입력한 성적·어학과 선택한 공고의 지정 요건만 비교합니다. 전체 지원자격·선발·지급은 평가하지 않습니다.</p>
          <div className={styles.cards}><ConditionCard title="성적 요건" comparison={comparison.grade} notice={notice} path={path} stage="grade" /><ConditionCard title="어학 요건" comparison={comparison.english} notice={notice} path={path} stage="english" /></div>
          <section className={styles.uncompared}><h3>이 데모에서 비교하지 않은 조건</h3><p>아래 조건도 실제 지원에서는 확인해야 합니다. 이번 결과의 충족 표시는 이 조건에 대한 판단을 포함하지 않습니다.</p><ul>{uncompared.map((item, index) => {
            const sources = notice.ruleSet.source.evidence.filter(evidence => item.evidence_ids?.includes(evidence.id));
            return <li key={item.id ?? index}>{item.text}<a href={notice.ruleSet.source.url} target="_blank" rel="noreferrer">{item.locator ?? sources[0]?.locator ?? "원문"} ↗</a>{sources.length > 0 && <details className={styles.conditionEvidence}><summary>근거 내용 보기</summary>{sources.map(source => <p key={source.id}><strong>{source.locator}</strong><br />{source.quote}</p>)}</details>}</li>;
          })}</ul></section>
          <details className={styles.sourceMeta}><summary>출처·시점·규칙 버전 확인</summary><dl><dt>원문</dt><dd><a href={notice.ruleSet.source.url} target="_blank" rel="noreferrer">{notice.ruleSet.source.title} ↗</a></dd><dt>문서 버전</dt><dd>{notice.ruleSet.source.document_version}</dd><dt>게시일</dt><dd>{shown(notice.ruleSet.source.published_at, "미확인 · PDF 생성일과 구분")}</dd><dt>원문 확인일</dt><dd>{shown(notice.ruleSet.source.checked_at)}</dd><dt>비교 규칙</dt><dd>{notice.ruleSet.rule_version}</dd><dt>검토 범위</dt><dd>지정 성적·어학 요건의 국소 비교. 전체 사업 규정 검증과 구분합니다.</dd></dl>{notice.limitations?.map(item => <p key={item}>{item}</p>)}</details>
          <div className={styles.presets}><span>가상 입력으로 확인</span><button type="button" onClick={() => preset("boundary")}>백분위·TOEIC 경계값</button><button type="button" onClick={() => preset("gpa")}>GPA 환산 미상</button><button type="button" onClick={() => preset("date")}>응시일 기간 밖</button><button type="button" onClick={() => preset("pohang")}>포항 GPA 경계값</button></div>
        </section>
      </div>
      <WritingPanel />
      <p className={styles.footer}>역사적 모집 공고와 가상 입력을 쓰는 로컬 데모입니다. 실제 학생의 자격 확정, 신청서 제출, 개인정보 수집은 수행하지 않습니다.</p>
    </main>
  </div>;
}
