import type { Opportunity } from './opportunity-data';
import type { WritingRecord } from './draft';

/** Only this exact notice version may supply official writing questions. */
export function writingRecordForOpportunity(opportunity: Opportunity): WritingRecord {
  const prompts = (opportunity.applicationPrompts ?? []).filter(prompt =>
    Boolean(opportunity.opportunityVersionId) && prompt.opportunityVersionId === opportunity.opportunityVersionId
    && prompt.reviewStatus === 'reviewed' && prompt.text.trim().length > 0 && /^https?:\/\//.test(prompt.sourceUrl));
  return {
    record_id: opportunity.opportunityVersionId ?? opportunity.id,
    notice: { notice_id: opportunity.id, title: opportunity.name },
    round: { round_id: opportunity.opportunityVersionId ?? '', label: opportunity.name, academic_path: '' },
    questions: prompts.map(prompt => ({
      id: prompt.id, title: prompt.promptKey, text: prompt.text, wording_status: 'verified_exact',
      length_limit: {status: prompt.characterLimit === null ? 'unknown' : 'known', unit: 'characters', whitespace_included: 'unknown', min: null, max: prompt.characterLimit, exact: null, evidence_refs: [prompt.sourceUrl]},
      evidence_refs: [prompt.sourceUrl], fact_slots: [{id: 'answer', label: '본인의 사실과 답변', placeholder: '문항에 답할 실제 경험과 계획 입력'}],
    })),
    ai_writing_policy: 'unknown', policy_source: [], official_forms: [],
  };
}
