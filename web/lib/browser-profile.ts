import type { Profile } from "./eligibility";

// School history is display-only, not a substitute for current eligibility fields.
export type PersonalProfile = Profile & { graduateSchool: string; highestDegreeInstitution: string; birthYear: string };
export const PROFILE_STORAGE_KEY = "benefitlens.personal-profile.v1";
export const WORKING_PROFILE_STORAGE_KEY = "benefitlens.working-profile.v1";
const LEGACY_PROFILE_STORAGE_KEY = "unlock.personal-profile.v1";
type ProfileStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export function createEmptyProfile(): PersonalProfile {
  return {
    institution: "", department: "", graduateSchool: "", highestDegreeInstitution: "",
    enrollmentCommitment: "", nationality: "", age: "", academicLevel: "",
    highestDegreeLevel: "", enrollmentStatus: "", year: "", graduateSemester: "",
    courseworkCompleted: "", previousSemesterCredits: "", gpa: "", gpaScale: "", gpaBasis: "", gpaEvaluationPeriod: "",
    incomeBand: "", residenceCity: "", ancestorResidenceCity: "", residenceMonths: "",
    regionalHighSchool: "", topikLevel: "", teamSize: "", employmentStatus: "",
    industryEmployee: "",
    birthYear: "", onlineLearningAvailable: "", hackathonAvailable: "", activityAttendance: "",
    fullTimeStudent: "",
    employmentPreparation: "", expectedGraduationMonth: "", militaryServiceMonths: "",
    trainingCardEligible: "", fullTimeTrainingAvailable: "", codingStudyInterest: "",
    remainingStudyMonths: "",
    codetreePaid: "", codetreeSubscribed: "", codetreePriorChallenge: "", codetreeBc3FreePass: "",
  };
}

const choices: Partial<Record<keyof PersonalProfile, string[]>> = {
  codetreePaid: ["true", "false"], codetreeSubscribed: ["true", "false"],
  codetreePriorChallenge: ["true", "false"], codetreeBc3FreePass: ["true", "false"],
  institution: ["POSTECH", "OTHER"], nationality: ["KR", "FOREIGN"],
  academicLevel: ["undergraduate", "master", "doctoral"],
  highestDegreeLevel: ["high_school", "associate", "bachelor", "master", "doctoral"],
  enrollmentStatus: ["enrolled", "leave", "expected_graduation", "graduated"],
  enrollmentCommitment: ["true", "false"], courseworkCompleted: ["true", "false"],
  regionalHighSchool: ["true", "false"], gpaScale: ["4.0", "4.3", "4.5"], gpaBasis: ["previous_semester", "cumulative"],
  employmentStatus: ["unemployed", "employed", "self_employed"],
  industryEmployee: ["true", "false"],
  onlineLearningAvailable: ["true", "false"], hackathonAvailable: ["true", "false"],
  fullTimeStudent: ["true", "false"],
  employmentPreparation: ["true", "false"],
  activityAttendance: ["true", "false"], trainingCardEligible: ["true", "false"],
  fullTimeTrainingAvailable: ["true", "false"], codingStudyInterest: ["true", "false"],
};

export function normalizeProfile(value: unknown): PersonalProfile | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const profile = createEmptyProfile();
  const input = value as Record<string, unknown>;
  for (const key of Object.keys(profile) as Array<keyof PersonalProfile>) {
    if (key === "conditionAnswers") continue;
    const raw = input[key];
    if (typeof raw !== "string" || raw.length > 150) continue;
    const text = raw.trim();
    if (choices[key] && text && !choices[key]?.includes(text)) continue;
    profile[key] = text;
  }
  if (input.conditionAnswers && typeof input.conditionAnswers === "object" && !Array.isArray(input.conditionAnswers)) {
    profile.conditionAnswers = Object.fromEntries(Object.entries(input.conditionAnswers).filter(([key, answer]) => key.length < 250 && typeof answer === "string" && ["true", "false"].includes(answer)).slice(0, 200)) as Record<string, string>;
  }
  return profile;
}

export function loadSavedProfile(storage: ProfileStorage): PersonalProfile | null {
  const raw = storage.getItem(PROFILE_STORAGE_KEY) ?? storage.getItem(LEGACY_PROFILE_STORAGE_KEY);
  if (!raw) return null;
  try {
    const envelope: unknown = JSON.parse(raw);
    if (!envelope || typeof envelope !== "object" || Array.isArray(envelope)) return null;
    const { version, profile } = envelope as { version?: unknown; profile?: unknown };
    return version === 1 ? normalizeProfile(profile) : null;
  } catch {
    return null;
  }
}

export function saveProfile(storage: ProfileStorage, profile: PersonalProfile): void {
  storage.setItem(PROFILE_STORAGE_KEY, JSON.stringify({ version: 1, profile: normalizeProfile(profile) }));
  storage.removeItem(LEGACY_PROFILE_STORAGE_KEY);
}

export function deleteSavedProfile(storage: ProfileStorage): void {
  storage.removeItem(PROFILE_STORAGE_KEY);
  storage.removeItem(LEGACY_PROFILE_STORAGE_KEY);
}

export function loadWorkingProfile(storage: ProfileStorage): PersonalProfile | null {
  const raw = storage.getItem(WORKING_PROFILE_STORAGE_KEY);
  if (!raw) return null;
  try { return normalizeProfile(JSON.parse(raw)); } catch { return null; }
}

export function saveWorkingProfile(storage: ProfileStorage, profile: PersonalProfile): void {
  storage.setItem(WORKING_PROFILE_STORAGE_KEY, JSON.stringify(normalizeProfile(profile)));
}

export function summarizePersonalProfile(profile: PersonalProfile, departmentLabel = profile.department): string {
  const degree = ({ high_school: "고등학교", associate: "전문학사", bachelor: "학사", master: "석사", doctoral: "박사" } as Record<string, string>)[profile.highestDegreeLevel];
  const academic = ({ undergraduate: "학부", master: "석사", doctoral: "박사" } as Record<string, string>)[profile.academicLevel];
  const nationality = ({ KR: "대한민국 국적", FOREIGN: "외국 국적" } as Record<string, string>)[profile.nationality];
  const employment = ({ unemployed: "미취업", employed: "재직", self_employed: "자영업" } as Record<string, string>)[profile.employmentStatus];
  const enrollment = ({ enrolled: "재학", leave: "휴학", expected_graduation: "졸업예정", graduated: "졸업" } as Record<string, string>)[profile.enrollmentStatus];
  return [profile.institution === "OTHER" ? "다른 학교" : profile.institution, profile.graduateSchool, departmentLabel,
    academic ? `${academic}${profile.graduateSemester ? ` ${profile.graduateSemester}학기` : ""}` : "", enrollment,
    profile.fullTimeStudent === "true" ? "전일제" : profile.fullTimeStudent === "false" ? "비전일제" : "",
    degree ? `${profile.highestDegreeInstitution ? `${profile.highestDegreeInstitution} ` : ""}${degree} 취득` : "",
    profile.birthYear ? `${profile.birthYear}년생` : "", nationality, employment].filter(Boolean).join(", ");
}
