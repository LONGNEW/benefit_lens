/** Local, deterministic preparation drafts. No model, network or persistence calls. */
export type AiWritingPolicy = 'unknown' | 'not_stated' | 'allowed' | 'penalty_possible' | 'prohibited'
export interface LengthLimit {
  status: 'known' | 'unknown' | 'not_stated' | 'not_applicable'
  unit: 'characters' | 'bytes' | 'words' | 'unknown'
  whitespace_included: 'yes' | 'no' | 'unknown'
  min: number | null
  max: number | null
  exact: number | null
  evidence_refs: string[]
}
export interface FactSlot { id: string; label: string; placeholder: string }
export interface WritingQuestion {
  id: string
  title: string
  text: string | null
  wording_status: 'verified_summary' | 'verified_exact' | 'unknown'
  length_limit: LengthLimit
  fact_slots: FactSlot[]
  evidence_refs: string[]
}
export interface WritingRecord {
  record_id: string
  notice: { notice_id: string; title: string }
  round: { round_id: string; label: string; academic_path: string }
  questions: WritingQuestion[]
  ai_writing_policy: AiWritingPolicy
  policy_source: Array<{ source_id: string; url: string; page: number | null; round: string }>
  official_forms: Array<{ form_kind: string; availability: string }>
  writing_constraints?: {
    language: string
    proper_names_english_allowed: boolean | null
    source_overflow_behavior: string
    demo_overflow_behavior: string
    school_identification_caution: boolean | null
    evidence_refs: string[]
  }
}
export interface ProvidedFact { question_id: string; slot_id: string; text: string }
export interface DraftCaution { kind: string; message: string; source_refs: string[] }
export interface DraftSection {
  question_id: string
  title: string
  text: string
  character_count: number
  length_status: 'within_limit' | 'over_limit' | 'under_limit' | 'unknown'
  length_result_scope: 'demo_estimate'
  missing_slots: string[]
  provided_fact_count: number
  is_official_question: boolean
  question_summary: string | null
  source_refs: string[]
}
export interface PreparationDraft {
  label: '공식 문항 준비 초안' | '일반 준비 초안'
  sections: DraftSection[]
  cautions: DraftCaution[]
  edit_option: { id: 'rewrite_in_own_words'; label: '내 표현으로 수정'; mode: 'manual' }
  policy: AiWritingPolicy
  unused_facts: ProvidedFact[]
  factual_claims_added: false
  is_submission: false
}
export type WritingResourceRecord = WritingRecord
export type DraftResult = PreparationDraft
const UNKNOWN_LIMIT: LengthLimit = { status: 'unknown', unit: 'unknown', whitespace_included: 'unknown', min: null, max: null, exact: null, evidence_refs: [] }

/** System-created preparation prompts never enter the official question collection. */
export function getDraftQuestions(record: WritingRecord): WritingQuestion[] {
  const known = record.questions.filter(q => ['verified_summary', 'verified_exact'].includes(q.wording_status) && typeof q.text === 'string' && q.evidence_refs.length > 0)
  if (known.length > 0) return known
  const noEssay = record.official_forms.some(f => f.form_kind === 'not_required' && f.availability === 'not_required')
  const descriptors = noEssay
    ? [
        ['general-notice', '공고 확인 메모', 'confirmed', '확인한 내용', '직접 확인한 공고 내용과 아직 확인할 점 입력'],
        ['general-documents', '서류 준비 메모', 'documents', '준비할 자료', '본인이 준비할 자료와 남은 확인 사항 입력'],
        ['general-questions', '문의할 내용', 'questions', '본인의 질문', '공고 담당자에게 확인하고 싶은 내용 입력'],
      ]
    : [
        ['general-motivation', '지원 이유 준비', 'motivation', '본인의 지원 이유', '이 지원을 생각하게 된 실제 이유 입력'],
        ['general-experience', '본인 경험 정리', 'experience', '실제 경험과 준비', '본인이 실제로 한 경험이나 준비 과정 입력'],
        ['general-plan', '학업·연구 계획 준비', 'plan', '본인의 계획', '본인이 실행하려는 학업 또는 연구 계획 입력'],
      ]
  return descriptors.map(([id, title, slotId, label, placeholder]) => ({
    id, title, text: null, wording_status: 'unknown', length_limit: { ...UNKNOWN_LIMIT },
    evidence_refs: [], fact_slots: [{ id: slotId, label, placeholder }],
  }))
}

/** Counts all supplied whitespace. It estimates the official counter rather than certifying it. */
export function measureDraftLength(text: string, limit: LengthLimit): Pick<DraftSection, 'character_count' | 'length_status' | 'length_result_scope'> {
  const countedText = limit.whitespace_included === 'no' ? text.replace(/\s/gu, '') : text
  const count = Array.from(countedText).length
  let status: DraftSection['length_status'] = 'unknown'
  if (limit.status === 'known' && limit.unit === 'characters' && limit.whitespace_included !== 'unknown') {
    if ((limit.max !== null && count > limit.max) || (limit.exact !== null && count > limit.exact)) status = 'over_limit'
    else if ((limit.min !== null && count < limit.min) || (limit.exact !== null && count < limit.exact)) status = 'under_limit'
    else status = 'within_limit'
  }
  return { character_count: count, length_status: status, length_result_scope: 'demo_estimate' }
}

/** A free memo is placed in the first preparation slot without guessing how to classify it. */
export function factsFromFreeText(record: WritingRecord, text: string): ProvidedFact[] {
  if (typeof text !== 'string' || text.trim().length === 0) return []
  const first = getDraftQuestions(record)[0]
  if (!first?.fact_slots[0]) return []
  return [{ question_id: first.id, slot_id: first.fact_slots[0].id, text }]
}

export function buildDraft(record: WritingRecord, providedFacts: ProvidedFact[] = []): PreparationDraft {
  const questions = getDraftQuestions(record)
  const knownIds = new Set(record.questions.filter(q => ['verified_summary', 'verified_exact'].includes(q.wording_status) && q.evidence_refs.length > 0).map(q => q.id))
  const consumed = new Set<number>()
  const sections = questions.map(question => {
    const missing: string[] = []
    let supplied = 0
    const chunks = question.fact_slots.map(slot => {
      const matches = providedFacts.map((fact, index) => ({ fact, index })).filter(({ fact }) => fact.question_id === question.id && fact.slot_id === slot.id && typeof fact.text === 'string' && fact.text.trim().length > 0)
      matches.forEach(({ index }) => consumed.add(index))
      supplied += matches.length
      if (matches.length === 0) missing.push(slot.id)
      const body = matches.length ? matches.map(({ fact }) => fact.text).join('\n') : `[${slot.placeholder}]`
      return `${slot.label}:\n${body}`
    })
    const text = chunks.join('\n\n') || '[본인이 제공할 사실을 입력해 주세요]'
    return {
      question_id: question.id, title: question.title, text,
      ...measureDraftLength(text, question.length_limit), missing_slots: missing,
      provided_fact_count: supplied, is_official_question: knownIds.has(question.id),
      question_summary: question.text, source_refs: [...question.evidence_refs],
    }
  })
  const official = sections.every(section => section.is_official_question) && sections.length > 0
  const policyRefs = record.policy_source.map(source => source.url + (source.page === null ? '' : `#page=${source.page}`))
  const cautions: DraftCaution[] = []
  if (record.ai_writing_policy === 'penalty_possible') cautions.push({ kind: 'ai_policy', message: '이 회차는 생성형 AI로 작성한 자기소개서를 본인의 고유한 서술로 인정하지 않고 감점할 수 있다고 안내합니다. 초안의 사실을 확인하고 내 표현으로 수정하세요.', source_refs: policyRefs })
  else if (record.ai_writing_policy === 'prohibited') cautions.push({ kind: 'ai_policy', message: '이 회차의 확인된 정책은 AI 작성물을 금지합니다. 이 준비 초안의 제출 여부는 해당 공식 정책을 확인하세요.', source_refs: policyRefs })
  else if (record.ai_writing_policy === 'unknown') cautions.push({ kind: 'ai_policy_unknown', message: '이 회차의 AI 작성 정책은 아직 확인하지 못했습니다.', source_refs: policyRefs })
  else if (record.ai_writing_policy === 'not_stated') cautions.push({ kind: 'ai_policy_scope', message: '확인한 범위에서 AI 정책 언급을 찾지 못했습니다. 이를 사용 허용으로 해석하지 않습니다.', source_refs: policyRefs })
  if (!official) {
    const noEssay = record.official_forms.some(f => f.form_kind === 'not_required' && f.availability === 'not_required')
    cautions.push({ kind: 'general_preparation', message: noEssay ? '이 회차에 공식 자기소개서 문항은 확인되지 않으며, 공개 표의 자기소개서 심사 항목은 없습니다. 아래는 일반 준비 메모입니다.' : '공식 문항의 정확한 내용과 분량을 아직 확인하지 못했습니다. 아래는 일반 준비 초안이며 공식 질문을 대신하지 않습니다.', source_refs: [] })
  }
  if (record.writing_constraints?.school_identification_caution) cautions.push({ kind: 'school_identification', message: '이 회차는 소속 대학·초중고교를 직접 또는 간접적으로 식별할 수 있는 표현에 심사 불이익을 안내합니다.', source_refs: record.writing_constraints.evidence_refs })
  if (record.writing_constraints?.language === 'ko') cautions.push({ kind: 'language', message: '국문 작성이 필요합니다. 파견교·과목·활동 등의 고유 명칭은 영문 표기가 가능합니다.', source_refs: record.writing_constraints.evidence_refs })
  if (record.writing_constraints?.source_overflow_behavior === 'automatic_deletion') cautions.push({ kind: 'length', message: '공식 접수 화면은 문항의 제한 글자수를 넘는 입력을 자동 삭제한다고 안내합니다. 이 데모는 글을 삭제하지 않고 초과를 표시합니다. 참고 문자수는 원문 접수 카운터와 다를 수 있습니다.', source_refs: record.writing_constraints.evidence_refs })
  if (sections.some(section => section.missing_slots.length > 0)) cautions.push({ kind: 'missing_facts', message: '아직 제공하지 않은 경험과 계획은 빈칸으로 남겼습니다.', source_refs: [] })
  return {
    label: official ? '공식 문항 준비 초안' : '일반 준비 초안', sections, cautions,
    edit_option: { id: 'rewrite_in_own_words', label: '내 표현으로 수정', mode: 'manual' },
    policy: record.ai_writing_policy, unused_facts: providedFacts.filter((_, index) => !consumed.has(index)),
    factual_claims_added: false, is_submission: false,
  }
}
