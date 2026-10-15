import type { PersonalProfile } from "./browser-profile";
import type { PublicDepartment } from "./profile-assistance";

export type ImportedCandidate = {
  field: keyof PersonalProfile;
  value: string;
  label: string;
  evidence: string;
};

export type ImportedFile = { name: string; content: string };

export function parseCsv(content: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  const input = content.replace(/^\uFEFF/, "");
  for (let index = 0; index < input.length; index++) {
    const char = input[index];
    if (char === '"') {
      if (quoted && input[index + 1] === '"') { cell += '"'; index++; }
      else quoted = !quoted;
    } else if (char === "," && !quoted) {
      row.push(cell); cell = "";
    } else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && input[index + 1] === "\n") index++;
      row.push(cell); cell = "";
      if (row.some(value => value.trim())) rows.push(row);
      row = [];
    } else cell += char;
  }
  row.push(cell);
  if (row.some(value => value.trim())) rows.push(row);
  return rows;
}

function mappedRows(content: string): Record<string, string>[] {
  const rows = parseCsv(content);
  const header = rows.shift()?.map(value => value.trim().toLowerCase()) ?? [];
  return rows.map(row => Object.fromEntries(header.map((key, index) => [key, (row[index] ?? "").trim()])));
}

function valueOf(row: Record<string, string>, ...names: string[]) {
  return names.map(name => row[name]).find(Boolean) ?? "";
}

function degreeLevel(value: string) {
  if (/ph\.?d|doctor|박사/i.test(value)) return "doctoral";
  if (/master|m\.?s\.?\b|석사|mba/i.test(value)) return "master";
  if (/bachelor|b\.?s\.?\b|학사/i.test(value)) return "bachelor";
  if (/associate|전문학사/i.test(value)) return "associate";
  return "";
}

export function extractProfileCandidates(files: ImportedFile[], departments: PublicDepartment[]): ImportedCandidate[] {
  const candidates: ImportedCandidate[] = [];
  const add = (field: keyof PersonalProfile, value: string, label: string, evidence: string) => {
    if (!value || candidates.some(item => item.field === field)) return;
    candidates.push({field, value, label, evidence: evidence.slice(0, 220)});
  };
  const education = files.filter(file => /(?:^|\/)education\.csv$/i.test(file.name)).flatMap(file => mappedRows(file.content));
  const completed = education.map(row => ({row, level: degreeLevel(valueOf(row, "degree name", "degree"))}))
    .filter(item => item.level && valueOf(item.row, "end date", "end year"))
    .sort((a, b) => ({doctoral: 5, master: 4, bachelor: 3, associate: 2}[b.level as "doctoral"] ?? 0) - ({doctoral: 5, master: 4, bachelor: 3, associate: 2}[a.level as "doctoral"] ?? 0));
  const highest = completed[0];
  if (highest) {
    const school = valueOf(highest.row, "school name", "school");
    add("highestDegreeLevel", highest.level, "최종 취득 학위", `${school}, ${valueOf(highest.row, "degree name", "degree")}, 종료 ${valueOf(highest.row, "end date", "end year")}`);
    if (school) add("highestDegreeInstitution", school, "최종 취득 학교", `${school}, ${valueOf(highest.row, "degree name", "degree")}`);
  }
  const current = education.find(row => !valueOf(row, "end date", "end year") && valueOf(row, "school name", "school"));
  if (current) {
    const school = valueOf(current, "school name", "school");
    const degree = valueOf(current, "degree name", "degree");
    if (/postech|포항공과대학교|포스텍/i.test(school)) add("institution", "POSTECH", "현재 소속 학교", `${school}, 종료일 없음, 재학 여부 확인 필요`);
    const level = degreeLevel(degree);
    if (["master", "doctoral", "bachelor"].includes(level)) add("academicLevel", level === "bachelor" ? "undergraduate" : level, "현재 학위 과정", `${school}, ${degree}, 종료일 없음, 재학 여부 확인 필요`);
    const field = valueOf(current, "field of study", "field of study name", "major").toLowerCase().replace(/\s/g, "");
    const department = departments.find(item => field.length >= 5 && item.department.toLowerCase().replace(/\s/g, "").endsWith(field));
    if (department && /postech|포항공과대학교|포스텍/i.test(school)) add("department", department.code, "현재 학과", `${school}, ${valueOf(current, "field of study", "field of study name", "major")}, 종료일 없음`);
  }
  const positions = files.filter(file => /(?:^|\/)positions\.csv$/i.test(file.name)).flatMap(file => mappedRows(file.content));
  const openPosition = positions.find(row => !valueOf(row, "finished on", "end date", "end year") && valueOf(row, "company name", "company") && valueOf(row, "title", "position"));
  if (openPosition) add("employmentStatus", "employed", "취업 상태", `${valueOf(openPosition, "company name", "company")}, ${valueOf(openPosition, "title", "position")}, 종료일 없음, 현재 상태 확인 필요`);
  return candidates;
}

export function extractProfileTextCandidates(text: string, departments: PublicDepartment[]): ImportedCandidate[] {
  const lines = text.slice(0, 20000).split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const candidates: ImportedCandidate[] = [];
  const school = lines.find(line => /postech|포항공과대학교|포스텍/i.test(line));
  if (school) candidates.push({field: "institution", value: "POSTECH", label: "소속 학교", evidence: `${school.slice(0, 180)} (현재 소속인지 확인)`});
  const degreeLines = lines.filter(line => /ph\.?d|doctor|박사|master|석사|bachelor|학사/i.test(line));
  const levels = [...new Set(degreeLines.map(degreeLevel).filter(Boolean))];
  if (levels.length === 1) candidates.push({field: "academicLevel", value: levels[0] === "bachelor" ? "undergraduate" : levels[0], label: "학위 과정", evidence: `${degreeLines[0].slice(0, 180)} (현재 과정인지 확인)`});
  const department = departments.find(item => lines.some(line => line.toLowerCase().replace(/\s/g, "").includes(item.department.toLowerCase().replace(/\s/g, ""))));
  if (school && department) candidates.push({field: "department", value: department.code, label: "학과", evidence: `${department.department} (현재 학과인지 확인)`});
  return candidates;
}
