"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, ChevronLeft, ChevronRight, CircleAlert, Database, ExternalLink, Fingerprint, LockKeyhole, RefreshCw, Search, ShieldCheck, X } from "lucide-react";
import type { EligibilityNode, Opportunity } from "@/lib/opportunity-data";
import { evaluateOpportunity, requirementText, type Match } from "@/lib/eligibility";
import { createEmptyProfile, deleteSavedProfile, saveProfile, saveWorkingProfile, summarizePersonalProfile, type PersonalProfile } from "@/lib/browser-profile";
import { initializeBrowserProfile, parseLocalDemoProfile } from "@/lib/local-demo-profile";
import DraftWorkbench from "./draft-workbench";
import { fetchRegistry, type OpportunityMeta } from "@/lib/registry";
import { fundingLabels, fundingTargetLabels, type FundingTarget } from "@/lib/funding";
import { withAffiliation, prioritizeMatches } from "@/lib/profile-assistance";
import { AssistedProfile } from "./assisted-profile";
import ProfileImport from "./profile-import";
import { APPLICATION_EXAMPLE_PATH, getApplicationExample } from '@/lib/application-package';

type Lifecycle = "all" | "upcoming" | "open" | "rolling" | "closed" | "unknown";
type OperatorGroup = "all" | "school" | "foundation" | "company" | "national" | "other";
type View = "browse" | "profile" | "detail" | "draft" | "about";

const lifecycleCopy: Record<Lifecycle, string> = {
  all: "전체",
  upcoming: "접수 예정",
  open: "접수 중",
  rolling: "상시",
  closed: "마감",
  unknown: "확인 필요",
};

const statusCopy = {
  candidate: { label: "조건 일치 후보", description: "입력값과 수집한 조건이 맞습니다. 원문 해석과 공개 권리 검토는 아직 승인되지 않았습니다." },
  possible: { label: "접수 가능", description: "입력 조건 충족, 현재 접수 기간 내입니다. 선발 확정은 아닙니다." },
  applicable: { label: "제도 대상 가능", description: "공개된 대상 범위와 입력 조건이 맞습니다. 세부 선발과 지급 여부는 공식 안내를 확인하세요." },
  policy: { label: "제도 안내", description: "현재 모집이 아닌 상시 제도입니다. 내 정보를 입력하면 공개된 대상 범위와 비교합니다." },
  profile: { label: "내 정보 필요", description: "조건은 구조화됐지만 비교할 내 정보가 비어 있습니다." },
  review: { label: "원문 보완 필요", description: "공개 조건을 수집했지만 일부를 자동 판정 규칙으로 변환하지 못했습니다." },
  period: { label: "접수 기간 확인 필요", description: "모집 공고의 신청 시작일이나 마감일을 확인하지 못했습니다." },
  unmet: { label: "명시 조건 미충족", description: "입력값과 맞지 않는 공개 기준이 있습니다" },
  closed: { label: "기간 종료", description: "자격과 별개로 현재 접수 기간이 끝났습니다" },
  upcoming: { label: "접수 예정", description: "아직 접수 기간이 시작되지 않았습니다" },
};

type PersonalFilter = "all" | Match["status"];
const personalCopy: Record<PersonalFilter, string> = {
  all: "전체",
  possible: "접수 가능",
  candidate: "조건 일치 후보",
  applicable: "제도 대상 가능",
  policy: "제도 안내",
  profile: "내 정보 필요",
  review: "원문 보완",
  period: "기간 확인",
  unmet: "조건 미충족",
  upcoming: "접수 예정",
  closed: "마감",
};
type SchoolCoverage = { checkedAt: string; departments: Array<{ department: string; code: string; homepage: string; status: string; fundingPages: string[]; failures: Array<{ url: string; reason: string }> }> };

const operatorGroupCopy: Record<OperatorGroup, string> = { all: "전체 운영기관", national: "국가와 지자체", company: "기업", foundation: "재단", school: "학교", other: "기타 민간" };

function formatDate(value?: string | null) {
  if (!value) return "미확인";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value.slice(0, 10) : new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Seoul" }).format(parsed);
}

function displayItems(lines: string[]) {
  // Only split explicit list separators, preserving URLs, dates, GPA and R/A.
  return [...new Set(lines.flatMap(line => typeof line === "string" ? line.split(/\r?\n|\s+\/\s+/) : []).map(line => line.trim().replaceAll("\u00b7", ", ")).filter(Boolean))];
}

function BulletItem({ text, compact }: { text: string; compact: boolean }) {
  return <li>{compact && text.length > 190 ? <details className="condition-full-text"><summary>{text.slice(0, 190)}… <span>전체 보기</span></summary><p>{text}</p></details> : text}</li>;
}

function BulletList({ lines, compact = false }: { lines: string[]; compact?: boolean }) {
  const items = displayItems(lines);
  const visible = compact ? items.slice(0, 3) : items;
  return <><ul className="condition-list">{visible.map(text => <BulletItem key={text} text={text} compact={compact} />)}</ul>{compact && items.length > 3 && <details className="condition-more"><summary>나머지 {items.length - 3}개 보기</summary><ul className="condition-list">{items.slice(3).map(text => <BulletItem key={text} text={text} compact={false} />)}</ul></details>}</>;
}

function CompactField({ label, lines, fallback }: { label: string; lines: string[]; fallback: string }) {
  return <div><dt>{label}</dt><dd>{displayItems(lines).length ? <BulletList lines={lines} compact /> : fallback}</dd></div>;
}

function CriteriaSummary({ node }: { node: EligibilityNode }) {
  if (node.kind === "rule") return <ul className="condition-list"><li>{node.label.replaceAll("\u00b7", ", ")}</li></ul>;
  const operatorLabel = node.operator === "and" ? "다음 조건을 모두 충족" : node.operator === "or" ? "다음 중 하나 이상 충족" : "다음에 해당하면 제외";
  return <div className={`eligibility-summary-group ${node.operator}`}><span className="condition-operator">{operatorLabel}</span><ul className="condition-list">{node.children.map(child => <li key={child.id}>{child.kind === "rule" ? child.label.replaceAll("\u00b7", ", ") : <CriteriaSummary node={child} />}</li>)}</ul></div>;
}

function CriteriaNodeView({ node, results }: { node: EligibilityNode; results: Map<string, "pass" | "fail" | "unknown"> }) {
  if (node.kind === "rule") {
    const result = results.get(node.id) ?? "unknown";
    return (
      <div className={`rule-row ${result}`} title={node.sourceText}>
        {result === "pass" ? <Check size={13} /> : result === "fail" ? <X size={13} /> : <CircleAlert size={13} />}
        <span>{node.label}</span>
        {node.sourceUrl && <a href={node.sourceUrl} target="_blank" rel="noreferrer">근거</a>}
        <em>{result === "pass" ? "충족" : result === "fail" ? "미충족" : "확인 필요"}</em>
      </div>
    );
  }
  const operatorLabel = node.operator === "and" ? "모두 충족" : node.operator === "or" ? "하나 이상" : "제외 조건";
  return (
    <div className={`criteria-group ${node.operator}`}>
      <div className="criteria-group-heading"><strong>{node.label}</strong><span>{operatorLabel}</span></div>
      <ul className="criteria-group-children condition-list">{node.children.map((child) => <li key={child.id}><CriteriaNodeView node={child} results={results} /></li>)}</ul>
    </div>
  );
}

export function ResultCard({ match, selected, onSelect }: { match: Match; selected: boolean; onSelect: (match: Match) => void }) {
  const copy = statusCopy[match.status];
  const operatorLabel = operatorGroupCopy[(match.operatorGroup ?? match.sourceGroup ?? "national") as OperatorGroup];
  const criteria = match.eligibilityCriteria;
  const results = new Map(match.ruleResults.map((rule) => [rule.id, rule.result]));
  const missing = match.eligibilityResult === "unknown" ? match.ruleResults.filter(rule => rule.result === "unknown") : [];
  const failed = match.ruleResults.filter(rule => rule.result === "fail");
  const unresolved = match.unresolvedCriteria ?? [];
  return (
    <article className={`opportunity-card ${match.status}${selected ? " selected" : ""}`}>
      <div className="opportunity-topline">
        <div className="category-tags"><span className="category-tag">{operatorLabel}</span>{match.fundingTags?.length ? match.fundingTags.map(tag => <span className="category-tag" key={tag}>{fundingLabels[tag]}</span>) : <span className="category-tag">{match.category}</span>}</div>
        <span className={`lifecycle-tag ${match.lifecycleStatus ?? "unknown"}`}>{match.recordKind === "policy" ? "제도 안내" : lifecycleCopy[(match.lifecycleStatus ?? "unknown") as Lifecycle]}</span>
      </div>
      <h3>{match.name}</h3>
      <p className="organization">{match.organization}</p>
      <span className={`match-status ${match.status}`}>{copy.label}</span>
      {match.status === "unmet" && failed.length > 0 && <div className="match-reason"><strong>맞지 않는 기준</strong><BulletList lines={failed.map(rule => rule.label)} /></div>}
      {missing.length > 0 && <div className="match-reason"><strong>{match.recordKind === "policy" ? "제도 대상 확인에 필요한 정보" : "내 정보 추가 입력"}</strong><BulletList lines={missing.map(rule => rule.label)} /></div>}
      {unresolved.length > 0 && <p className="match-reason">{match.recordKind === "policy" ? `추가 선발 기준 ${unresolved.length}개를 수집했으나 시스템 규칙으로 해석하지 못했습니다. 대상 비교에는 구조화된 기준을 사용하며, 아래에 수집한 원문을 표시합니다.` : `공개 조건 ${unresolved.length}개를 수집했으나 자동 판정으로 해석하지 못했습니다. 아래에 원문 조건을 표시합니다.`}</p>}
      <dl className="opportunity-facts">
        <div><dt>지원 자격</dt><dd>{criteria ? <CriteriaSummary node={criteria} /> : match.requirements.length ? "공개된 추가 자격 제한 없음" : "표준 자격 기준 미추출, 전체 조건에서 원문 확인"}</dd></div>
        <CompactField label="우대 조건" lines={match.conditionSections?.preferences ?? []} fallback="별도 우대 항목 미추출" />
        {Boolean(match.conditionSections?.selection?.length) && <CompactField label="선발과 심사" lines={match.conditionSections?.selection ?? []} fallback="별도 선발 안내 없음" />}
        <CompactField label="요구사항" lines={match.conditionSections ? match.conditionSections.requirements ?? [] : match.presentation?.conditions ?? []} fallback={match.requirements.length ? "아래 신청 후 활동 안내 참고" : "별도 안내 없음"} />
        <CompactField label="혜택" lines={(match.conditionSections ? match.conditionSections.benefits ?? [] : match.presentation?.benefits ?? [match.support]).filter(line => line !== match.name)} fallback="혜택 항목 미추출" />
        <CompactField label="특약" lines={match.conditionSections ? match.conditionSections.specialTerms ?? [] : match.presentation?.specialTerms ?? []} fallback="별도 특약 항목 미추출" />
        <CompactField label="준비 서류" lines={match.requiredDocuments ?? []} fallback="제출 서류 미추출" />
        <CompactField label="활동 기간" lines={match.conditionSections?.activityPeriod ?? []} fallback="활동 기간 미추출" />
        <CompactField label="기간" lines={match.recordKind === "policy" ? ["제도 안내, 현재 접수 공고와 구분"] : match.opensAt || match.closesAt ? [`${formatDate(match.opensAt)} ~ ${formatDate(match.closesAt)}`] : match.lifecycleStatus === "rolling" ? ["상시 접수"] : []} fallback="접수 기간 미확인" />
      </dl>
      {match.requirements.length > 0 && <section className="participation-requirements" aria-label={`${match.name}: 신청 후 요구사항`}><strong>신청하면 해야 할 일</strong><ul>{match.requirements.map(rule => <li key={rule.id}><span>{requirementText(rule).replaceAll("\u00b7", ", ")}</span>{rule.sourceUrl && <a href={rule.sourceUrl} target="_blank" rel="noreferrer">근거</a>}</li>)}</ul></section>}
      <details className="opportunity-details">
        <summary>전체 조건과 판정 근거</summary>
        <p className="status-description">{copy.description}</p>
        {(match.fundingEvidence ?? []).length > 0 && <section className="funding-evidence"><strong>금전 지원 분류 근거</strong>{match.fundingEvidence?.map(item => <p key={item.tag}><b>{fundingLabels[item.tag]}</b>: {item.text.replaceAll("\u00b7", ", ")} <small>({item.field === "title" ? "공고 제목" : "추출한 혜택"})</small></p>)}</section>}
        {criteria && <CriteriaNodeView node={criteria} results={results} />}
        {match.derivedFacts.map((fact, index) => <p className="derived-fact" key={index}>{fact.text} <a href={fact.sourceUrl} target="_blank" rel="noreferrer">학칙 근거</a></p>)}
        {unresolved.length > 0 && <section className="unresolved-text"><strong>시스템 해석이 남은 수집 조건</strong><BulletList lines={unresolved} /></section>}
        {(match.evidenceSources ?? []).map((source, index) => <p className="evidence-source" key={index}><a href={source.url} target="_blank" rel="noreferrer">{source.label}</a><span>{source.status === "failed" ? `수집 실패: ${source.error ?? "원천 접근 불가"}` : source.status === "ocr_review" ? "이미지 OCR 추출, 검토 전" : source.status === "duplicate" ? "중복 자료" : "수집 완료"}</span></p>)}
        {Object.entries(match.conditionSections ?? match.presentation ?? {}).map(([key, lines]) => <div key={key}><strong>{({eligibility: "지원 자격 원문", exclusions: "제외 조건", preferences: "우대 조건", requirements: "신청 후 요구사항과 준비", selection: "선발과 심사", conditions: "조건", benefits: "혜택", specialTerms: "특약", applicationPeriod: "접수 기간", activityPeriod: "활동 기간", period: "기간"} as Record<string, string>)[key] ?? key}</strong><BulletList lines={lines ?? []} /></div>)}
        <p className="source-date">수집 {formatDate(match.sourceUpdatedAt)}, {match.verificationStatus === "approved" ? "원문과 공개 권리 검토 승인" : "자료 검토 대기"}</p>
      </details>
      <div className="card-links">
        <a href={match.sourceUrl} target="_blank" rel="noreferrer">공식 원문 <ExternalLink size={13} /></a>
        {match.applicationUrl && <a href={match.applicationUrl} target="_blank" rel="noreferrer">신청 사이트 <ExternalLink size={13} /></a>}
      </div>
      <button className="select-opportunity" type="button" disabled={match.status === "closed"} onClick={() => onSelect(match)}>{match.status === "closed" ? "접수 기간 종료" : selected ? "선택됨" : match.recordKind === "policy" ? "제도 활용 준비하기" : "지원 준비하기"}</button>
    </article>
  );
}

export function OpportunityPreview({ match }: { match: Match }) {
  const tags = match.fundingTags?.map(tag => fundingLabels[tag]) ?? [];
  const benefits = displayItems(match.conditionSections ? match.conditionSections.benefits ?? [] : match.presentation?.benefits ?? [match.support]).filter(text => text !== match.name);
  const conditions = match.ruleResults.map(rule => rule.label.replaceAll("\u00b7", ", "));
  const detailUrl = `/benefitlens/opportunity?id=${encodeURIComponent(match.id)}`;
  return <article className={`opportunity-preview ${match.status}`}>
    <div className="preview-topline"><span className={`match-status ${match.status}`}>{statusCopy[match.status].label}</span><span>{operatorGroupCopy[(match.operatorGroup ?? match.sourceGroup ?? "national") as OperatorGroup]}{match.recordKind === "policy" ? ", 제도 안내" : ""}</span></div>
    <h3><a href={detailUrl}>{match.name}</a></h3>
    <p className="preview-organization">{match.organization}</p>
    <div className="preview-tags">{tags.map(tag => <span key={tag}>{tag}</span>)}</div>
    <div className="preview-body"><div><strong>어떤 지원인가요?</strong>{benefits.length ? <ul>{benefits.slice(0, 2).map(line => <li key={line}>{line.length > 110 ? `${line.slice(0, 110)}…` : line}</li>)}</ul> : <p>지원 내용은 상세에서 확인하세요.</p>}{benefits.length > 2 && <small>혜택 {benefits.length - 2}개 더 있음</small>}</div><div><strong>누가 대상인가요?</strong>{conditions.length ? <ul>{conditions.slice(0, 2).map(line => <li key={line}>{line}</li>)}</ul> : <p>원문 자격 확인 필요</p>}{conditions.length > 2 && <small>조건 {conditions.length - 2}개 더 있음</small>}</div></div>
    <div className="preview-footer"><span>{match.recordKind === "policy" ? "상시 제도 안내" : match.closesAt ? `마감 ${formatDate(match.closesAt)}` : match.lifecycleStatus === "rolling" ? "상시 접수" : "접수 기간 확인 필요"}</span><a href={detailUrl}>자격과 근거 보기 <ExternalLink size={14} /></a></div>
  </article>;
}

export default function BenefitLensDashboard({ view = "browse", exampleId = "" }: { view?: View; exampleId?: string }) {
  const router = useRouter();
  const applicationExample = getApplicationExample(exampleId);
  const [profile, setProfile] = useState<PersonalProfile>(createEmptyProfile);
  const [savedProfile, setSavedProfile] = useState<PersonalProfile | null>(null);
  const [profileMessage, setProfileMessage] = useState("");
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [meta, setMeta] = useState<OpportunityMeta>({ total: 0, page: 1, pageSize: 12, pageCount: 1, lifecycleCounts: {} });
  const [loading, setLoading] = useState(view !== "profile" && !applicationExample);
  const [loadError, setLoadError] = useState(false);
  const [draftQuery, setDraftQuery] = useState("");
  const [query, setQuery] = useState("");
  const [lifecycle, setLifecycle] = useState<Lifecycle>("all");
  const [operatorGroup, setOperatorGroup] = useState<OperatorGroup>("all");
  const [fundingTarget, setFundingTarget] = useState<FundingTarget>("financial");
  const [page, setPage] = useState(1);
  const [reloadToken, setReloadToken] = useState(0);
  const [selectedId] = useState(() => typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("id") ?? "");
  const [hydrated, setHydrated] = useState(false);
  const profileInitialized = useRef(false);
  const [personalFilter, setPersonalFilter] = useState<PersonalFilter>("all");
  const [schoolCoverage, setSchoolCoverage] = useState<SchoolCoverage | null>(null);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    if (profileInitialized.current) return;
    profileInitialized.current = true;
    const patch = parseLocalDemoProfile(window.location.hostname, window.location.hash);
    let fragmentCleared = true;
    if (patch) {
      // Remove private fragment data before any navigation, including when storage is blocked.
      try { window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search); }
      catch { fragmentCleared = false; }
    }
    const restored = initializeBrowserProfile({local: () => window.localStorage, session: () => window.sessionStorage}, patch);
    // Browser storage is available only after hydration, never during SSR.
    setProfile(restored.profile);
    setSavedProfile(restored.savedProfile);
    setProfileMessage(restored.message + (fragmentCleared ? "" : " 주소의 임시 정보를 제거하지 못했습니다."));
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    try { saveWorkingProfile(window.sessionStorage, profile); } catch { /* Session storage is optional. */ }
  }, [hydrated, profile]);

  useEffect(() => {
    fetch("/school-coverage.json").then(response => response.ok ? response.json() as Promise<SchoolCoverage> : null).then(setSchoolCoverage).catch(() => undefined);
    const timer = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (view === "profile" || applicationExample) return;
    let active = true;
    const controller = new AbortController();
    const params = new URLSearchParams({ lifecycle: view === "browse" ? lifecycle : "all", operatorGroup: view === "browse" ? operatorGroup : "all", fundingTarget: view === "browse" ? fundingTarget : "all" });
    if (query) params.set("q", query);
    fetchRegistry(params, controller.signal)
      .then((payload) => {
        if (!active) return;
        setOpportunities(Array.isArray(payload.data) ? payload.data : []);
        setMeta(payload.meta ?? { total: 0, page: 1, pageSize: 12, pageCount: 1 });
      })
      .catch(() => { if (active) setLoadError(true); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [applicationExample, fundingTarget, lifecycle, operatorGroup, query, reloadToken, view]);

  const matches = useMemo(() => prioritizeMatches(opportunities.map((item) => evaluateOpportunity(profile, item, now)), profile), [profile, opportunities, now]);
  const filtered = matches.filter(match => personalFilter === "all" || match.status === personalFilter);
  const pageCount = Math.max(1, Math.ceil(filtered.length / 12));
  const safePage = Math.min(page, pageCount);
  const visible = filtered.slice((safePage - 1) * 12, safePage * 12);
  const selectedMatch = selectedId ? matches.find(item => item.id === selectedId) ?? null : null;
  const prepareRequest = () => { setLoading(true); setLoadError(false); };
  const reloadRegistry = () => { prepareRequest(); setReloadToken((value) => value + 1); };
  const update = (field: keyof PersonalProfile, value: string) => { setPage(1); setProfileMessage(""); setProfile((current) => ({ ...current, [field]: value })); };
  const chooseAffiliation = (value: string) => { setPage(1); setProfileMessage(""); setProfile(current => withAffiliation(current, value, schoolCoverage?.departments ?? [])); };
  const savePersonalProfile = () => {
    try {
      saveProfile(window.localStorage, profile);
      setSavedProfile({ ...profile });
      setProfileMessage("이 브라우저의 기본정보로 저장했습니다. 다음에도 자동으로 불러옵니다.");
    } catch {
      setProfileMessage("저장하지 못했습니다. 이번 화면의 입력값은 유지됩니다.");
    }
  };
  const clearPersonalProfile = () => {
    try {
      deleteSavedProfile(window.localStorage);
      setSavedProfile(null);
      setProfile(createEmptyProfile());
      setPage(1);
      setProfileMessage("이 브라우저의 저장 정보와 현재 입력을 삭제했습니다.");
    } catch {
      setProfileMessage("저장 정보를 삭제하지 못했습니다.");
    }
  };
  const applyImportedProfile = (values: Partial<PersonalProfile>) => {
    setProfile(current => ({...current, ...values, ...(values.department ? {institution: "POSTECH"} : {})}));
    setProfileMessage("확인한 정보만 현재 입력에 반영했습니다. 저장하면 다음 방문에도 유지됩니다.");
  };
  const departmentLabel = schoolCoverage?.departments.find(item => item.code === profile.department)?.department ?? profile.department;
  const profileSummary = summarizePersonalProfile(profile, departmentLabel);
  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    prepareRequest();
    setPage(1);
    setQuery(draftQuery.trim());
    setReloadToken(value => value + 1);
  };
  const changeLifecycle = (value: Lifecycle) => {
    if (value === lifecycle) return;
    prepareRequest();
    setPage(1);
    setLifecycle(value);
  };
  const changeOperatorGroup = (value: OperatorGroup) => { prepareRequest(); setPage(1); setOperatorGroup(value); };
  const changeFundingTarget = (value: FundingTarget) => { prepareRequest(); setPage(1); setFundingTarget(value); };

  useEffect(() => {
    if (view !== "browse") return;
    const modelContext = (document as unknown as { modelContext?: { registerTool?: (tool: unknown, options?: { signal?: AbortSignal }) => void | Promise<void> } }).modelContext;
    if (!modelContext?.registerTool) return;
    const lifecycleController = new AbortController();
    const tool = {
      name: "search_benefitlens_opportunities",
      title: "BenefitLens 금전 지원 검색",
      description: "검색어, 모집 상태, 운영기관과 금전 지원 분류로 D1 레지스트리를 조회합니다.",
      inputSchema: {
        type: "object",
        properties: {
          query: { type: "string", maxLength: 100 },
          lifecycle: { type: "string", enum: ["all", "upcoming", "open", "rolling", "closed", "unknown"] },
          operatorGroup: { type: "string", enum: Object.keys(operatorGroupCopy) },
          fundingTarget: { type: "string", enum: Object.keys(fundingTargetLabels) },
        },
        required: ["query", "lifecycle", "operatorGroup", "fundingTarget"],
        additionalProperties: false,
      },
      annotations: { readOnlyHint: true, untrustedContentHint: false },
      execute: (input: Record<string, unknown>) => {
        const nextQuery = typeof input.query === "string" ? input.query.slice(0, 100) : "";
        const nextLifecycle = typeof input.lifecycle === "string" && input.lifecycle in lifecycleCopy ? input.lifecycle as Lifecycle : "all";
        const nextOperatorGroup = typeof input.operatorGroup === "string" && input.operatorGroup in operatorGroupCopy ? input.operatorGroup as OperatorGroup : "all";
        const nextFundingTarget = typeof input.fundingTarget === "string" && Object.hasOwn(fundingTargetLabels, input.fundingTarget) ? input.fundingTarget as FundingTarget : "financial";
        setDraftQuery(nextQuery);
        prepareRequest();
        setQuery(nextQuery);
        setLifecycle(nextLifecycle);
        setOperatorGroup(nextOperatorGroup);
        setFundingTarget(nextFundingTarget);
        setPage(1);
        setReloadToken(value => value + 1);
        return { query: nextQuery, lifecycle: nextLifecycle, operatorGroup: nextOperatorGroup, fundingTarget: nextFundingTarget };
      },
    };
    try { void Promise.resolve(modelContext.registerTool(tool, { signal: lifecycleController.signal })).catch(() => undefined); } catch { /* unsupported registry */ }
    return () => lifecycleController.abort();
  }, [view]);

  const profilePanel = (
          <aside className="profile-panel">
            <div className="profile-heading"><div><span>선택 사항</span><h2>조금만 알려주세요</h2></div><button onClick={() => { setProfile(createEmptyProfile()); setProfileMessage("현재 입력만 비웠습니다. 저장한 정보는 복원할 수 있습니다."); setPage(1); }}><RefreshCw size={13} /> 입력 비우기</button></div>
            <div className="privacy-note"><LockKeyhole size={15} /><span>기본정보는 이 브라우저에만 저장하며 서버로 보내지 않습니다. 공용 기기에서는 저장하지 마세요.</span></div>
            {profileSummary && <p className="profile-summary" aria-label="내 정보 요약">{profileSummary}</p>}
            <div className="profile-save-controls">
              <button type="button" onClick={savePersonalProfile}>내 기본정보로 저장</button>
              {savedProfile && <><button type="button" onClick={() => { setProfile({ ...savedProfile }); setPage(1); setProfileMessage("저장한 기본정보로 복원했습니다."); }}>저장한 정보로 복원</button><button type="button" onClick={clearPersonalProfile}>저장 정보 삭제</button></>}
            </div>
            <p className="profile-storage-status" role="status">{profileMessage || (savedProfile ? "저장한 값은 재사용합니다. 바꾼 값은 저장 버튼을 누르면 다음에도 유지됩니다." : "모든 입력은 선택 사항입니다. 빈 값은 미확인으로 유지합니다.")}</p>
            <AssistedProfile key={selectedMatch?.id ?? "no-selection"} profile={profile} departments={schoolCoverage?.departments ?? []} match={selectedMatch} onUpdate={update} onAffiliation={chooseAffiliation} />
            <div className="profile-footer"><ShieldCheck size={17} /><p><strong>통과 기준</strong><span>지원 자격은 AND 모두 충족, OR 하나 이상 충족으로 판정합니다. 신청 후 학습, 활동, 출석은 요구사항으로 안내합니다.</span></p></div>
          </aside>
  );
  const resultsPanel = (
          <section className="results-panel browse-panel">
            <div className="results-heading"><div><span>지원 목록</span><h2>{fundingTarget === "all" ? "전체 보관 목록" : "금전 지원 찾기"}</h2></div><div className="result-total"><Search size={15} /><strong>{filtered.length.toLocaleString("ko-KR")}</strong><span>개 결과</span></div></div>
            <form className="opportunity-search" onSubmit={submitSearch}><label><Search size={16} /><input value={draftQuery} onChange={(event) => setDraftQuery(event.target.value)} placeholder="사업명, 운영기관, 지원내용 검색" aria-label="지원 사업 검색어" /></label><button type="submit">검색</button></form>
            <div className="structured-filters" aria-label="금전 지원 분류 필터">
              <label><span>지원 유형</span><select value={fundingTarget} onChange={event => changeFundingTarget(event.target.value as FundingTarget)}>{(Object.keys(fundingTargetLabels) as FundingTarget[]).map(value => <option key={value} value={value}>{fundingTargetLabels[value]}</option>)}</select></label>
              <label><span>운영기관</span><select value={operatorGroup} onChange={event => changeOperatorGroup(event.target.value as OperatorGroup)}>{(Object.keys(operatorGroupCopy) as OperatorGroup[]).map(value => <option key={value} value={value}>{operatorGroupCopy[value]}{value !== "all" && meta.operatorCounts ? ` (${meta.operatorCounts[value] ?? 0})` : ""}</option>)}</select></label>
            </div>
            <details className="filter-fold"><summary>내 조건 결과로 좁히기</summary><div className="result-filters personal-filters" role="group" aria-label="내 조건 판정 필터">{(Object.keys(personalCopy) as PersonalFilter[]).map(value => <button key={value} aria-pressed={personalFilter === value} className={personalFilter === value ? "active" : ""} onClick={() => { setPersonalFilter(value); setPage(1); }}>{personalCopy[value]} <strong>{value === "all" ? matches.length : matches.filter(match => match.status === value).length}</strong></button>)}</div></details>
            {personalFilter === "possible" && <p className="filter-explanation">지원 자격을 충족하고 현재 접수 중인 공고입니다. 각 공고의 신청 후 요구사항을 함께 확인하세요.</p>}
            {personalFilter === "candidate" && <p className="filter-explanation">수집한 조건과 입력값이 맞는 검토 전 후보입니다. 원문과 공개 권리 승인이 완료되면 접수 가능 상태로 표시합니다.</p>}
            {personalFilter === "applicable" && <p className="filter-explanation">공개된 제도 대상 범위가 내 조건과 맞는 항목입니다. 실제 선발, 지급과 신청 시점은 공식 안내를 확인하세요.</p>}
            {personalFilter === "policy" && <p className="filter-explanation">상시 제도 정보입니다. 내 정보를 채우면 공개된 대상 범위를 비교해 제도 대상 가능 또는 조건 미충족으로 바뀝니다.</p>}
            {personalFilter === "profile" && <p className="filter-explanation">공고 조건은 판정할 수 있지만 비교에 필요한 내 정보가 비어 있습니다.</p>}
            {personalFilter === "review" && <p className="filter-explanation">공식 원문은 수집했지만 일부 조건을 표준 규칙으로 변환하지 못한 공고입니다.</p>}
            {personalFilter === "period" && <p className="filter-explanation">모집 사실은 확인했지만 현재 신청 기간을 확정하지 못한 공고입니다.</p>}
            <details className="period-filters"><summary>접수 기간으로 추가 좁히기</summary><div className="result-filters" role="group" aria-label="공고 기간 필터">
              {(Object.keys(lifecycleCopy) as Lifecycle[]).map((value) => <button key={value} className={lifecycle === value ? "active" : ""} onClick={() => changeLifecycle(value)}>{lifecycleCopy[value]} {value !== "all" && <strong>{meta.lifecycleCounts?.[value] ?? 0}</strong>}</button>)}
            </div></details>
            {loading ? <div className="result-state">정책 레지스트리를 불러오는 중입니다.</div> : loadError ? <div className="result-state error">정책 정보를 불러오지 못했습니다. 수집 실패는 자격 미충족이 아닙니다.</div> : (
              <>
                <div className="opportunity-grid">{visible.map((match) => <OpportunityPreview key={match.id} match={match} />)}{filtered.length === 0 && <div className="result-state">현재 조건의 수집 후보가 없습니다. 지원 유형, 운영기관과 내 조건 필터를 확인하세요.</div>}</div>
                <nav className="pagination" aria-label="정책 결과 페이지"><button disabled={safePage <= 1 || loading} onClick={() => setPage(Math.max(1, safePage - 1))}><ChevronLeft size={15} /> 이전</button><span>{safePage} / {pageCount}</span><button disabled={safePage >= pageCount || loading} onClick={() => setPage(Math.min(pageCount, safePage + 1))}>다음 <ChevronRight size={15} /></button></nav>
              </>
            )}
          </section>
  );

  const intro = {
    browse: {title: "나에게 맞는 금전 지원 찾기", description: "혜택과 대상부터 살펴보고, 관심 있는 지원의 세부 자격을 확인하세요."},
    profile: {title: "내 정보", description: "알고 있는 정보만 입력하세요. 필요한 조건은 지원 상세에서 추가로 확인할 수 있습니다."},
    detail: {title: "지원 상세", description: "혜택, 대상, 기간과 공식 근거를 한곳에서 확인하세요."},
    draft: {title: applicationExample ? "제출서류와 합격 후기 연결 예제" : "지원 초안", description: applicationExample ? "공식 요건, 과거 회차의 변경점, 합격 후기와 본인의 경험을 구분해 준비합니다." : "선택한 지원을 바탕으로 확인된 경험만 사용해 첫 문장을 만듭니다."},
    about: {title: "데이터 기준", description: "수집 범위와 분류 방식, 데이터의 최신 상태를 확인하세요."},
  }[view];

  return (
    <main className="site-shell benefitlens-shell">
      <header className="topbar">
        {/* Vinext client preview is more stable with a native anchor here. */}
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
        <a href="/" className="brand" aria-label="데이터 프로토타입 홈">
          <span className="brand-mark"><span /></span>
          <span><strong>BenefitLens</strong><small>내 조건으로 찾는 금전 지원</small></span>
        </a>
        <nav aria-label="BenefitLens 페이지"><a href="/benefitlens" className={view === "browse" || view === "detail" ? "active" : ""}>지원 찾기</a><a href="/benefitlens/profile" className={view === "profile" ? "active" : ""}>내 정보</a><a href="/benefitlens/draft" className={view === "draft" ? "active" : ""}>초안 작성</a><a href="/benefitlens/collection">수집 데모</a><a href="/benefitlens/about" className={view === "about" ? "active" : ""}>데이터 기준</a></nav>
        <div className="mode-chip"><span /> DATA PROJECT</div>
      </header>

      <div className="benefitlens-wrap">
        <section className="benefitlens-intro">
          <div>
            <p className="eyebrow"><Fingerprint size={14} /> FINANCIAL SUPPORT REGISTRY</p>
            <h1>{intro.title}</h1>
            <p className="lede">{intro.description}</p>
          </div>
          {(view === "browse" || view === "about") && <div className="registry-status">
            <Database size={19} />
            <div><span>{meta.storage === "d1" ? "공식 자료 수집본" : "예시 데이터"}</span><strong>{loading ? "연결 중" : loadError ? "연결 실패" : `${meta.total.toLocaleString("ko-KR")}개 후보`}</strong><button className="registry-refresh" type="button" onClick={reloadRegistry} disabled={loading}><RefreshCw size={12} /> 새로고침</button></div>
            <i className={loadError ? "error" : loading ? "loading" : "ready"} />
          </div>}
        </section>

        {view === "browse" && <p className="browse-data-note">{meta.storage === "d1" ? `공식 자료 ${meta.total.toLocaleString("ko-KR")}개를 탐색 중입니다.` : "지원 자료를 불러오고 있습니다."} <a href="/benefitlens/about">수집 범위와 판정 기준 보기</a> <a href={APPLICATION_EXAMPLE_PATH}>제출서류와 합격 후기 연결 예제</a></p>}

        {view === "about" && <section className="dataset-banner" aria-label="데이터 상태">
          <div><Database size={17} /><span><strong>혜택 유형, 운영기관과 자격을 구분해 정리</strong> 원문 수집 {formatDate(meta.collectedAt)}, 동기화 {formatDate(meta.synchronizedAt)}</span></div>
          <p>{meta.storage === "d1" && <>보관 후보 {meta.registeredTotal ?? 0}개 중 금전 지원 {meta.financialTotal ?? 0}개. 현재 수집본은 학교 비중이 높습니다. </>}‘접수 가능’은 자격과 기간이 맞는 모집 공고, ‘제도 대상 가능’은 공개된 대상 범위가 맞는 제도 안내입니다. 선발과 지급은 운영기관이 결정합니다.</p>
        </section>}

        {view === "about" && <section className="about-section"><h2>어떤 지원을 모으나요?</h2><p>현금성 지원은 수당과 지원금, 장학금은 장학 및 펠로우십, 등록금은 대학 학비의 보전과 감면, 생활비는 생활비와 생계비 지원을 포함합니다. 한 제도에 여러 혜택이 있으면 각 유형에서 찾을 수 있습니다.</p><p>무료 교육, 채용 안내, 물품만 제공하는 사업, 상금만 있는 대회, 대출과 이자 지원은 기본 목록에서 제외합니다. ‘전체 보관 목록’에서 기존 수집본을 볼 수 있습니다. 제목에서만 장학을 확인한 공고는 지급액과 용도를 추정하지 않습니다.</p><p>수집한 게시판이 아닌 운영기관 기준으로 묶습니다. 학교 게시판에서 수집한 기업 사업도 기업으로 표시합니다. 지원 상세의 전체 조건과 판정 근거에서 분류에 사용한 문구를 확인하세요.</p></section>}

        {view === "about" && schoolCoverage && <details className="school-coverage"><summary>POSTECH 전체 학교 수집 범위: 학과와 대학원 {schoolCoverage.departments.length}곳 확인, 공개 지원 페이지 {schoolCoverage.departments.filter(item => item.status === "public_funding_found").length}곳 발견</summary><p>학교 공통 제도와 학과별 공개 페이지, 연결 첨부와 이미지 원문을 수집합니다. 최근 탐색: {schoolCoverage.checkedAt.slice(0, 10)}. 로그인 자료와 탐색 범위 밖 공지는 미수집이며, 변환 실패 경로는 아래에 표시합니다.</p><div>{schoolCoverage.departments.map(item => <p key={`${item.department}-${item.homepage}`}><a href={item.homepage} target="_blank" rel="noreferrer">{item.department}</a><span>{item.status === "public_funding_found" ? `공개 지원 페이지 ${item.fundingPages.length}개` : item.status === "access_failed" ? "접근 실패, 추가 확인" : "제한 탐색 내 지원 페이지 미발견"}{item.failures.length > 0 && `, 미수집 경로 ${item.failures.length}개`}</span></p>)}</div></details>}

        {view === "browse" && resultsPanel}
        {view === "profile" && <div className="profile-page-grid"><ProfileImport departments={schoolCoverage?.departments ?? []} onApply={applyImportedProfile} />{profilePanel}</div>}
        {view === "detail" && (loading ? <div className="result-state">지원 정보를 불러오는 중입니다.</div> : selectedMatch ? <div className="detail-layout"><section className="detail-main"><a className="back-link" href="/benefitlens">지원 목록으로 돌아가기</a><ResultCard match={selectedMatch} selected={false} onSelect={match => router.push(`/benefitlens/draft?id=${encodeURIComponent(match.id)}`)} /></section>{profilePanel}</div> : <div className="result-state error">지원 정보를 찾지 못했습니다. <a href="/benefitlens">지원 목록으로 돌아가기</a></div>)}
        {view === "draft" && <div className="draft-page">{applicationExample ? <DraftWorkbench key={applicationExample.id} opportunity={null} example={applicationExample} /> : selectedMatch ? <DraftWorkbench key={`${selectedMatch.id}:${selectedMatch.opportunityVersionId ?? ''}`} opportunity={selectedMatch} /> : loading ? <div className="result-state">지원 정보를 불러오는 중입니다.</div> : <section className="draft-start"><h2>어떤 지원에 쓸 글인가요?</h2><p>지원 목록에서 관심 있는 항목의 자격과 근거를 확인한 뒤 초안을 시작할 수 있습니다.</p><a href="/benefitlens">지원 목록 살펴보기</a><a className="historical-example-link" href={APPLICATION_EXAMPLE_PATH}>제출서류와 합격 후기 연결 예제</a></section>}</div>}
      </div>
    </main>
  );
}
