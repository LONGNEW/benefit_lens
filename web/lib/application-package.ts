import resources from './writing-resources.json';
import type { WritingRecord } from './draft';

export type ReferenceLink = { label: string; url: string; page: number | null };
export type SubmissionDocument = { id: string; title: string; applicability: string; checks: string[]; evidence: ReferenceLink };
export type SuccessStrategy = { questionId: string; summary: string; ask: string; sourceId: string };
export type ApplicationExample = {
  id: string; record: WritingRecord; title: string; round: string; audience: string;
  mode: 'historical_example'; lifecycle: 'closed'; qualification: 'not_evaluated'; checkedAt: string;
  periodEvidence: ReferenceLink;
  referenceReview: { basis: 'same_round_saved_pdf'; sha256: string; pages: number[]; checkedAt: string; scope: string };
  opensAt: string; closesAt: string; applicationUrl: null; officialNotice: string;
  documents: SubmissionDocument[]; optionalDocuments: SubmissionDocument[];
  fileChecks: string[]; fileEvidence: ReferenceLink;
  stages: Array<{ title: string; description: string; evidence: ReferenceLink }>;
  history: { round: string; changes: string[]; evidence: ReferenceLink };
  success: { sourceId: string; url: string; round: string; reportedAt: string;
    verification: 'self_reported'; reusePermission: 'unknown'; context: string[]; unknown: string[] };
  strategies: SuccessStrategy[];
  sources: { official: Array<{id: string; url: string}>; unofficial: Array<{id: string; url: string; scope_alignment: string; use_scope: string}> };
  evidence: Array<{ id: string; source_id: string; round: string; url: string; page: number | null; location: string }>;
};

const id = 'miraeasset-35-2026-s2';
const record = resources.records.find(item => item.record_id === id)!;
const guide = 'https://www.hansung.ac.kr/bbs/hansung/2127/60765/download.do';
const guideSource = record.official_sources.find(item => item.id === 'mirae-35-guide');
const guideHash = guideSource && 'sha256' in guideSource && typeof guideSource.sha256 === 'string' ? guideSource.sha256 : '';
const source = (page: number): ReferenceLink => ({label: `제35기 공식 요강 ${page}쪽`, url: guide, page});
const documents: SubmissionDocument[] = [
  {id: 'online-application', title: '온라인 지원서와 자기소개서', applicability: '지원자 공통', checks: ['접수 페이지에서 직접 작성'], evidence: source(7)},
  {id: 'transcript', title: '국문 성적증명서', applicability: '지원자 공통', checks: ['전체 학년 성적 포함', '모교 직인이 있는 대외제출용'], evidence: source(7)},
  {id: 'income-notice', title: '학자금지원구간 통지서', applicability: '지원자 공통', checks: ['요강이 지정한 학기 확인', '한국장학재단 발급기관 직인'], evidence: source(7)},
];

const example: ApplicationExample = {
  id, record: record as unknown as WritingRecord, title: '미래에셋 해외교환 장학생', round: '제35기, 2026년 가을학기 파견',
  mode: 'historical_example', lifecycle: 'closed', qualification: 'not_evaluated', checkedAt: '2026-10-04',
  periodEvidence: {label: '제35기 재단 공고의 선발 일정', url: record.official_sources[0].url, page: null},
  referenceReview: {basis: 'same_round_saved_pdf', sha256: guideHash, pages: [2, 3, 4, 7, 8, 11], checkedAt: '2026-10-04', scope: '이전 기수 변경점, 접수와 선발 단계, 제출서류, 선발 후 의무, 문항과 작성 규정의 추가 확인'},
  audience: '학부 교환학생용 과거 회차, 현재 박사과정의 지원 후보와 분리',
  opensAt: '2026-04-15T10:00:00+09:00', closesAt: '2026-04-21T15:00:00+09:00',
  applicationUrl: null, officialNotice: record.official_sources[0].url,
  documents,
  optionalDocuments: [
    {id: 'additional-points', title: '가산점 증빙', applicability: '수급자, 차상위, 장애인가정, 국가유공자 자녀 등 해당자', checks: ['해당 사유별 증명서', '부모 명의 증빙은 가족관계와 개인정보 동의서 등 요강의 추가 요건 확인'], evidence: source(7)},
    {id: 'transfer', title: '전적대학 국문 성적증명서', applicability: '편입생', checks: ['전체 학년 성적과 학교 직인'], evidence: source(7)},
    {id: 'second-major', title: '제2전공 이수 증빙', applicability: '성적표에 지원할 제2전공이 표시되지 않는 경우', checks: ['지원자 정보, 현재 학기 전공명과 기관 직인 또는 학과장 서명'], evidence: source(7)},
  ],
  fileChecks: ['첨부서류는 PDF로 제출', '파일 암호 해제', '주민등록번호 뒷자리 비공개', '2026년 3월 3일 이후 발급', '파일명: 서류명_생년월일 6자리_성명'],
  fileEvidence: source(7),
  stages: [
    {title: '온라인 지원', description: '공식 공고에서 해당 회차 접수 페이지로 이동해 지원서 작성과 서류 업로드. 이 회차는 접수 종료.', evidence: source(4)},
    {title: '서류 합격 후', description: '대상자에게 안내된 온라인 영상 면접에 응시.', evidence: source(3)},
    {title: '최종 선발 후', description: '수여식 참석과 파견 이후 과제, 성적, 이수 의무가 적용됨.', evidence: source(8)},
  ],
  history: {round: '제34기와 제35기 비교', changes: ['미주, 유럽, 기타 지역 지원금: 750만원에서 850만원', '아시아 지원금: 550만원에서 650만원', '제35기는 접수 마감 후 파견 프로그램 유형 변경 불가'], evidence: source(2)},
  success: {sourceId: 'mirae-review-linkareer', url: record.unofficial_sources[0].url, round: '제35기', reportedAt: '2026-09-15',
    verification: 'self_reported', reusePermission: 'unknown',
    context: ['학부 교환학생 준비 사례', '작성자가 밝힌 관심 분야: 마케팅', '파견 지역: 스페인', '준비 내용: 어학 준비, 파견교 강의와 활동 조사'],
    unknown: ['합격을 재단에서 대조 확인한 기록 없음', '학점과 소득을 비교할 검증된 정보 없음', '완전한 제출 자소서는 미보존'],
  },
  strategies: [
    {questionId: 'mirae-q1', sourceId: 'mirae-review-linkareer', summary: '교환이 필요해진 계기와 실제 준비 행동을 하나의 주제로 연결한 사례.', ask: '본인이 겪은 계기와 직접 준비한 행동은 무엇인가요?',},
    {questionId: 'mirae-q2', sourceId: 'mirae-review-linkareer', summary: '기대하는 변화를 현지에서 실행할 구체적인 활동과 연결한 사례.', ask: '어떤 활동으로 변화를 만들고, 무엇을 확인할 계획인가요?',},
    {questionId: 'mirae-q3', sourceId: 'mirae-review-linkareer', summary: '파견 중 활동과 귀국 후 학업에 남길 결과물을 연결한 사례.', ask: '경험을 어떤 학업 계획이나 결과물로 이어갈 것인가요?',},
  ],
  sources: {official: record.official_sources, unofficial: record.unofficial_sources},
  evidence: record.evidence,
};

export const APPLICATION_EXAMPLE_ID = id;
export const APPLICATION_EXAMPLE_PATH = '/benefitlens/examples/miraeasset-35';

export function getApplicationExample(exampleId: string): ApplicationExample | null {
  return exampleId === example.id && example.record.round.round_id === '35-2026-fall' && hasVerifiedOfficialReferences(example) ? example : null;
}

export function hasVerifiedOfficialReferences(pack: ApplicationExample): boolean {
  const official = (ref: string) => {
    const evidence = pack.evidence.find(item => item.id === ref && item.round === pack.record.round.round_id);
    return Boolean(evidence && pack.sources.official.some(source => source.id === evidence.source_id && source.url === evidence.url));
  };
  return /^[a-f0-9]{64}$/.test(pack.referenceReview.sha256) && pack.record.questions.length > 0 && pack.record.questions.every(question =>
    question.evidence_refs.length > 0 && question.evidence_refs.every(official)
    && (question.length_limit.status !== 'known' || question.length_limit.evidence_refs.length > 0 && question.length_limit.evidence_refs.every(official)))
    && pack.record.policy_source.length > 0 && pack.record.policy_source.every(policy => policy.round === pack.record.round.round_id
      && pack.sources.official.some(source => source.id === policy.source_id && source.url === policy.url));
}

export function strategyForQuestion(pack: ApplicationExample, questionId: string): SuccessStrategy | null {
  if (pack.success.round !== '제35기' || pack.record.round.round_id !== '35-2026-fall') return null;
  const source = pack.sources.unofficial.find(item => item.id === pack.success.sourceId && item.url === pack.success.url);
  if (!source || source.scope_alignment !== 'same_round' || source.use_scope !== 'context_only') return null;
  return pack.strategies.find(strategy => strategy.questionId === questionId && strategy.sourceId === pack.success.sourceId) ?? null;
}

export function referenceLinks(pack: ApplicationExample | null, refs: string[]): ReferenceLink[] {
  return refs.flatMap(ref => {
    const known = pack?.evidence.find(evidence => evidence.id === ref);
    if (known) return [{label: known.location, url: known.url, page: known.page}];
    return /^https?:\/\//.test(ref) ? [{label: '원문 근거', url: ref, page: null}] : [];
  });
}

export function referenceHref(link: ReferenceLink): string {
  return link.page ? `${link.url.split('#')[0]}#page=${link.page}` : link.url;
}
