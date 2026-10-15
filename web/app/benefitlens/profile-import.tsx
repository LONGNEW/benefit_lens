"use client";

import { useState } from "react";
import type { PersonalProfile } from "@/lib/browser-profile";
import type { PublicDepartment } from "@/lib/profile-assistance";
import { extractProfileCandidates, extractProfileTextCandidates, type ImportedCandidate } from "@/lib/profile-import";

type Props = { departments: PublicDepartment[]; onApply: (values: Partial<PersonalProfile>) => void };

export default function ProfileImport({ departments, onApply }: Props) {
  const [profileUrl, setProfileUrl] = useState("");
  const [copiedText, setCopiedText] = useState("");
  const [candidates, setCandidates] = useState<ImportedCandidate[]>([]);
  const [checked, setChecked] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const valueLabel = (item: ImportedCandidate) => item.field === "department" ? departments.find(department => department.code === item.value)?.department ?? item.value : ({doctoral: "박사", master: "석사", undergraduate: "학부", bachelor: "학사", associate: "전문학사", employed: "재직"} as Record<string, string>)[item.value] ?? item.value;
  const publicProfileUrl = (() => {
    try {
      const url = new URL(profileUrl);
      return url.protocol === "https:" && ["www.linkedin.com", "linkedin.com"].includes(url.hostname) && /^\/in\/[^/]+\/?$/.test(url.pathname) ? url.href : "";
    } catch { return ""; }
  })();

  const showCandidates = (values: ImportedCandidate[], source: string) => {
    setCandidates(values);
    setChecked([]);
    setMessage(values.length ? `${source}에서 ${values.length}개 후보를 찾았습니다. 현재 정보인지 확인한 항목만 선택하세요.` : `${source}에서 판정에 사용할 수 있는 항목을 찾지 못했습니다.`);
  };
  const importFiles = async (files: FileList | null) => {
    if (!files) return;
    const selected = [...files].filter(file => /^(Education|Positions)\.csv$/i.test(file.name));
    if (!selected.length || selected.length > 2 || selected.some(file => file.size > 2_000_000)) {
      setMessage("Education.csv와 Positions.csv 중 필요한 파일만 선택하세요. 파일당 2MB까지 처리합니다.");
      return;
    }
    const data = await Promise.all(selected.map(async file => ({name: file.name, content: await file.text()})));
    showCandidates(extractProfileCandidates(data, departments), "LinkedIn 내려받기 파일");
  };
  const apply = () => {
    const values = Object.fromEntries(candidates.filter(item => checked.includes(item.field)).map(item => [item.field, item.value])) as Partial<PersonalProfile>;
    if (!Object.keys(values).length) return;
    onApply(values);
    setMessage("선택한 후보를 현재 입력에 반영했습니다. 다음 방문에도 쓰려면 아래 저장 버튼을 누르세요.");
    setChecked([]);
  };
  return <section className="profile-import" aria-labelledby="profile-import-heading">
    <div className="profile-import-heading"><span>입력 줄이기</span><h2 id="profile-import-heading">내 기록에서 정보 가져오기</h2></div>
    <p>본인 LinkedIn 기록에서 학력과 경력 후보를 찾습니다. 분석은 브라우저에서 진행하고, 확인한 항목만 내 정보에 반영합니다.</p>
    <label className="profile-import-url"><span>내 공개 프로필 링크, 선택</span><input type="url" placeholder="https://www.linkedin.com/in/..." value={profileUrl} onChange={event => setProfileUrl(event.target.value)} /></label>
    {publicProfileUrl && <a className="profile-import-source" href={publicProfileUrl} target="_blank" rel="noreferrer">내 프로필에서 내용 확인하기</a>}
    <p className="profile-import-url-note">링크는 원본 확인용입니다. 학력과 경력은 아래 방법으로 가져올 수 있습니다.</p>
    <div className="profile-import-methods">
      <div><strong>방법 1. 내 데이터 파일</strong><p><a href="https://www.linkedin.com/help/linkedin/answer/a1339364" target="_blank" rel="noreferrer">LinkedIn에서 내 데이터 내려받기</a> 후 Education.csv와 Positions.csv를 선택하세요.</p><input aria-label="LinkedIn 학력과 경력 CSV 파일" type="file" accept=".csv,text/csv" multiple onChange={event => void importFiles(event.target.files)} /></div>
      <div><strong>방법 2. 프로필 내용 붙여넣기</strong><p>본인 프로필에서 학교와 학위가 보이는 부분을 복사해 넣으세요.</p><textarea aria-label="내 프로필에서 복사한 내용" maxLength={20000} value={copiedText} onChange={event => setCopiedText(event.target.value)} placeholder="예: POSTECH 전자전기공학과, 박사과정" /><button type="button" onClick={() => showCandidates(extractProfileTextCandidates(copiedText, departments), "붙여넣은 내용")}>후보 찾기</button></div>
    </div>
    {message && <p className="profile-import-message" role="status">{message}</p>}
    {candidates.length > 0 && <div className="profile-import-review"><h3>가져올 항목 확인</h3><ul>{candidates.map(item => <li key={item.field}><label><input type="checkbox" checked={checked.includes(item.field)} onChange={event => setChecked(current => event.target.checked ? [...current, item.field] : current.filter(field => field !== item.field))} /><span><strong>{item.label}</strong><em>{valueLabel(item)}</em><small>{item.evidence}</small></span></label></li>)}</ul><button type="button" disabled={!checked.length} onClick={apply}>선택한 정보 반영</button></div>}
  </section>;
}
