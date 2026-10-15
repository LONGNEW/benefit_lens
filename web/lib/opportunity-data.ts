import type { FundingTag, FundingEvidence } from "./funding";

export type ProfileField =
  | "codetreePaid"
  | "codetreeSubscribed"
  | "codetreePriorChallenge"
  | "codetreeBc3FreePass"
  | "remainingStudyMonths"
  | "expectedGraduationMonth"
  | "employmentPreparation"
  | "fullTimeStudent"
  | "onlineLearningAvailable"
  | "hackathonAvailable"
  | "activityAttendance"
  | "trainingCardEligible"
  | "fullTimeTrainingAvailable"
  | "codingStudyInterest"
  | "institution"
  | "department"
  | "enrollmentCommitment"
  | "nationality"
  | "age"
  | "academicLevel"
  | "highestDegreeLevel"
  | "enrollmentStatus"
  | "year"
  | "graduateSemester"
  | "courseworkCompleted"
  | "previousSemesterCredits"
  | "gpa"
  | "incomeBand"
  | "residenceCity"
  | "ancestorResidenceCity"
  | "residenceMonths"
  | "regionalHighSchool"
  | "topikLevel"
  | "teamSize"
  | "employmentStatus"
  | "industryEmployee";

export type RuleOperator = "eq" | "neq" | "gte" | "lte" | "between" | "in" | "not_in";
export type RuleValue = string | number | boolean | Array<string | number | boolean>;

export type Rule = {
  kind: "rule";
  id: string;
  criterionCode: string;
  field: ProfileField;
  operator: RuleOperator;
  value: RuleValue;
  valueType: "string" | "integer" | "number" | "boolean" | "enum" | "date";
  unit?: string | null;
  label: string;
  sourceText?: string;
  sourceUrl?: string;
  answerKey?: string;
  conditionKind?: "eligibility" | "requirement";
  context?: { basis?: "previous_semester" | "cumulative" | "specified_window"; evaluationPeriod?: string; windowText?: string; requiresReview?: boolean; scaleAlternative?: boolean };
  derivation?: { institution: string; semestersByLevel: Partial<Record<"master" | "doctoral", number>>; sourceText: string; sourceUrl: string };
};

export type EligibilityGroup = {
  kind: "group";
  id: string;
  operator: "and" | "or" | "not";
  label: string;
  children: EligibilityNode[];
};

export type EligibilityNode = EligibilityGroup | Rule;

export type ApplicationPrompt = { id: string; opportunityVersionId: string; promptKey: string; text: string; characterLimit: number | null; sourceUrl: string; reviewStatus: string };
export type StructuredBenefit = { type: string; description: string; amountMin: number | null; amountMax: number | null; currency: string | null; recurrence: string | null; amountQualifier: string | null };
export type ConditionSections = Partial<Record<"eligibility" | "exclusions" | "preferences" | "requirements" | "selection" | "benefits" | "specialTerms" | "applicationPeriod" | "activityPeriod", string[]>>;

export type Opportunity = {
  id: string;
  name: string;
  organization: string;
  support: string;
  category: string;
  sourceGroup?: "school" | "foundation" | "company" | "national";
  operatorGroup?: "school" | "foundation" | "company" | "national" | "other";
  rules: Rule[];
  criteria?: EligibilityGroup | null;
  eligibilityCriteria?: EligibilityNode | null;
  requirements?: Rule[];
  unresolvedCriteria?: string[];
  eligibilityNotes?: string[];
  caveat?: string;
  sourceUrl: string;
  sourceUpdatedAt: string;
  summary?: string;
  applicationUrl?: string;
  benefitType?: string;
  fundingTags?: FundingTag[];
  fundingEvidence?: FundingEvidence[];
  lifecycleStatus?: "upcoming" | "open" | "closed" | "rolling" | "unknown";
  opensAt?: string;
  closesAt?: string;
  publicationStatus?: string;
  rightsStatus?: string;
  productionAllowed?: boolean;
  contractVersion?: string;
  opportunityVersionId?: string;
  requiredDocuments?: string[];
  applicationPrompts?: ApplicationPrompt[];
  structuredBenefits?: StructuredBenefit[];
  conditionSections?: ConditionSections;
  sourceId?: string;
  matchMode?: "structured" | "partial" | "review_required";
  criteriaVersion?: string;
  structuredRuleCount?: number;
  unresolvedRuleCount?: number;
  recordKind?: "notice" | "policy";
  presentation?: Partial<Record<"eligibility" | "conditions" | "benefits" | "specialTerms" | "period", string[]>>;
  evidenceSources?: Array<{ url: string; kind: string; status: string; label: string; error?: string; sha256?: string }>;
};

function rule(
  id: string,
  criterionCode: string,
  field: ProfileField,
  operator: RuleOperator,
  value: RuleValue,
  label: string,
  valueType: Rule["valueType"],
  unit?: string,
): Rule {
  return { kind: "rule", id, criterionCode, field, operator, value, label, valueType, unit };
}

export const opportunities: Opportunity[] = [
  {
    id: "national-i", name: "국가장학금 I유형", organization: "한국장학재단, POSTECH",
    fundingTags: ["scholarship", "tuition"],
    support: "등록금 전액 또는 일부", category: "교육비", sourceGroup: "national",
    rules: [
      rule("income-band", "income_band", "incomeBand", "lte", 8, "학자금지원구간 8구간 이내", "integer", "kosaf_band"),
      rule("credits", "previous_semester_credits", "previousSemesterCredits", "gte", 12, "직전 학기 12학점 이상", "number", "credit"),
      rule("gpa", "gpa", "gpa", "gte", 2.3, "평점 2.3 이상", "number", "gpa_4.3"),
    ],
    unresolvedCriteria: ["지원 구간별 금액과 계속지원 예외 기준"],
    caveat: "지원 구간에 따른 금액과 예외 기준은 공식 공고에서 확인해야 합니다.",
    sourceUrl: "https://postech.ac.kr/kor/college-life/scholarship_types.do", sourceUpdatedAt: "2025-09-30",
    benefitType: "education_cost", lifecycleStatus: "unknown", matchMode: "partial", criteriaVersion: "eligibility-1.0.1",
  },
  {
    id: "regional-talent", name: "지역인재장학금", organization: "한국장학재단, POSTECH",
    fundingTags: ["scholarship", "tuition"],
    support: "등록금 지원", category: "교육비", sourceGroup: "national",
    rules: [
      rule("income-band", "income_band", "incomeBand", "lte", 8, "학자금지원구간 8구간 이내", "integer", "kosaf_band"),
      rule("credits", "previous_semester_credits", "previousSemesterCredits", "gte", 12, "직전 학기 12학점 이상", "number", "credit"),
      rule("gpa", "gpa", "gpa", "gte", 2.3, "평점 2.3 이상", "number", "gpa_4.3"),
      rule("regional-school", "regional_high_school", "regionalHighSchool", "eq", true, "비수도권 고교 졸업", "boolean"),
    ],
    caveat: "대학별 선발 절차는 공식 공고에서 확인해야 합니다.",
    sourceUrl: "https://postech.ac.kr/kor/college-life/scholarship_types.do", sourceUpdatedAt: "2025-09-30",
    benefitType: "education_cost", lifecycleStatus: "unknown", matchMode: "structured", criteriaVersion: "eligibility-1.0.1",
  },
  {
    id: "jigok", name: "지곡장학금", organization: "POSTECH", support: "등록금 전액",
    fundingTags: ["scholarship", "tuition"],
    category: "교육비", sourceGroup: "school",
    rules: [
      rule("credits", "previous_semester_credits", "previousSemesterCredits", "gte", 12, "직전 학기 12학점 이상", "number", "credit"),
      rule("gpa", "gpa", "gpa", "gte", 3.0, "평점 3.0 이상", "number", "gpa_4.3"),
    ],
    caveat: "장학 제도 기준이며 현재 모집 공고를 뜻하지 않습니다.",
    sourceUrl: "https://postech.ac.kr/kor/college-life/scholarship_types.do", sourceUpdatedAt: "2025-09-30",
    benefitType: "education_cost", lifecycleStatus: "unknown", matchMode: "structured", criteriaVersion: "eligibility-1.0.1",
  },
  {
    id: "academic-support", name: "면학장학금", organization: "POSTECH", support: "등록금 전액 또는 일부",
    fundingTags: ["scholarship", "tuition"],
    category: "교육비", sourceGroup: "school",
    rules: [
      rule("income-band", "income_band", "incomeBand", "lte", 8, "학자금지원구간 8구간 이내", "integer", "kosaf_band"),
      rule("credits", "previous_semester_credits", "previousSemesterCredits", "gte", 10, "직전 학기 10학점 이상", "number", "credit"),
      rule("gpa", "gpa", "gpa", "gte", 2.0, "평점 2.0 이상", "number", "gpa_4.3"),
    ],
    unresolvedCriteria: ["계속지원 기준 최초 미달 여부"], caveat: "계속지원 예외 기준은 추가 확인이 필요합니다.",
    sourceUrl: "https://postech.ac.kr/kor/college-life/scholarship_types.do", sourceUpdatedAt: "2025-09-30",
    benefitType: "education_cost", lifecycleStatus: "unknown", matchMode: "partial", criteriaVersion: "eligibility-1.0.1",
  },
  {
    id: "smp", name: "SMP장학금", organization: "POSTECH", support: "매월 25만원",
    fundingTags: ["cash", "scholarship"],
    category: "현금성", sourceGroup: "school",
    rules: [rule("year", "academic_year", "year", "gte", 3, "3학년 이상 재학생", "integer", "academic_year")],
    unresolvedCriteria: ["세부 선발 기준"], caveat: "세부 기준과 모집 기간은 선발 공고에서 확인해야 합니다.",
    sourceUrl: "https://postech.ac.kr/kor/college-life/scholarship_types.do", sourceUpdatedAt: "2025-09-30",
    benefitType: "cash", lifecycleStatus: "unknown", matchMode: "partial", criteriaVersion: "eligibility-1.0.1",
  },
  {
    id: "topik", name: "한국어 우수 장학금", organization: "POSTECH", support: "30만원, 재학 중 1회",
    fundingTags: ["cash", "scholarship"],
    category: "현금성", sourceGroup: "school",
    rules: [
      rule("nationality", "nationality", "nationality", "eq", "FOREIGN", "외국 국적 학부생", "enum"),
      rule("topik", "topik_level", "topikLevel", "gte", 4, "TOPIK 4급 이상", "integer", "topik_level"),
    ],
    unresolvedCriteria: ["재학 중 1회 수혜 여부"], caveat: "재학 중 1회 지원 여부를 추가로 확인해야 합니다.",
    sourceUrl: "https://postech.ac.kr/kor/college-life/scholarship_types.do", sourceUpdatedAt: "2025-09-30",
    benefitType: "cash", lifecycleStatus: "unknown", matchMode: "partial", criteriaVersion: "eligibility-1.0.1",
  },
  {
    id: "pohang-scholarship", name: "포항시 장학생 선발", organization: "포항시장학회", support: "선발 분야별 장학금",
    fundingTags: ["scholarship"],
    category: "교육비", sourceGroup: "foundation",
    rules: [
      rule("city", "residence_city", "residenceCity", "eq", "포항시", "본인 주민등록지가 포항시", "string", "kr_admin_name"),
      rule("months", "residence_months", "residenceMonths", "gte", 12, "포항 연속 거주 12개월 이상", "integer", "month"),
    ],
    unresolvedCriteria: ["부모 거주 경로와 선발 분야별 조건"], caveat: "본인 또는 부모 거주 경로와 선발 분야별 기준을 원문에서 확인해야 합니다.",
    sourceUrl: "https://pohang.go.kr/dept/contents.do?mid=0114020000", sourceUpdatedAt: "2026-09-24",
    benefitType: "education_cost", lifecycleStatus: "unknown", matchMode: "partial", criteriaVersion: "eligibility-1.0.1",
  },
  {
    id: "national-work", name: "국가근로장학금", organization: "한국장학재단", support: "근로 시간에 따른 장학금",
    fundingTags: ["cash", "scholarship"],
    category: "현금성", sourceGroup: "national",
    rules: [
      rule("nationality", "nationality", "nationality", "eq", "KR", "대한민국 국적", "enum"),
      rule("income-band", "income_band", "incomeBand", "lte", 8, "학자금지원구간 8구간 이하", "integer", "kosaf_band"),
    ],
    unresolvedCriteria: ["직전 학기 성적과 소속 대학 선발"], caveat: "직전 학기 성적, 소속 대학 선발과 근로지 배정은 추가 확인이 필요합니다.",
    sourceUrl: "https://www.kosaf.go.kr/ko/scholar.do?pg=scholarship05_04_01p", sourceUpdatedAt: "2026-09-24",
    benefitType: "cash", lifecycleStatus: "unknown", matchMode: "partial", criteriaVersion: "eligibility-1.0.1",
  },
];
