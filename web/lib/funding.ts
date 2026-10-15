export type FundingTag = "cash" | "scholarship" | "tuition" | "living_expenses";
export type FundingTarget = "financial" | FundingTag | "all";
export type FundingEvidence = { tag: FundingTag; text: string; field: string };

export const fundingLabels: Record<FundingTag, string> = {
  cash: "현금성 지원", scholarship: "장학금", tuition: "등록금", living_expenses: "생활비",
};
export const fundingTargetLabels: Record<FundingTarget, string> = {
  financial: "금전 지원 전체", ...fundingLabels, all: "전체 보관 목록 (교육, 행사 포함)",
};

export function matchesFunding(tags: FundingTag[] | undefined, target: FundingTarget) {
  return target === "all" || (target === "financial" ? Boolean(tags?.length) : Boolean(tags?.includes(target)));
}
