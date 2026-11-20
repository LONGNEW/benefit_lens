# Writing pattern의 원문과 평가 근거 비교

2026년 10월 15일 갱신. 공고는 학생 개인 대상 예제 10개를 유지한다. 사용자 요청으로 지정 경험 원문 7개를 보강하고, 전체 원문을 LLM에 다시 입력했다. 문서와 사람을 별도로 세며 같은 사람의 연구계획서와 에세이는 같은 사례 ID다.

## 실제 분석 범위

| 프로그램 | 사례 | 입력 문서 | 전체 텍스트 | 구성 패턴 | 기준별 근거 | 결과별 비교 |
|---|---:|---:|---:|---:|---:|---:|
| Google | 7 | 14 | 147,869자 | 7 | 7 | 0 |
| NVIDIA | 3 | 5 | 38,082자 | 5 | 9 | 2 |
| Gates Cambridge | 4 | 6 | 78,701자 | 5 | 4 | 3 |

기존 9사례와 18문서에서 현재 14사례와 25문서로 늘었다. 지원서 원문이 있는 사례는 Google 4개, NVIDIA 3개, Gates 2개다. 나머지는 인터뷰 및 준비 후기다. Google G4는 작성자 페이지의 결과 표기가 모호하므로 선정 또는 미선정 집단에 넣지 않는다.

## 신규 사례와 결과 출처

| 사례 | 결과 | 자료와 확인 범위 |
|---|---|---|
| N2 Yuke Wang | 선정, [official](https://research.nvidia.com/graduate-fellowships/2022) | 지원용 공개 문서다. PDF 자체에는 제출 프로그램과 회차가 적혀 있지 않아 정확한 최종 제출본은 미확인이다. |
| N3 Ruth Fong | 미선정, [author_reported](https://medium.com/@ruthfong/applying-to-the-open-philanthropy-ai-fellowship-48dafe7fe27c) | NVIDIA 미선정은 작성자 보고다. 다른 프로그램 Open Philanthropy에서의 선정과 구분한다. |
| C2 Naveen Raman | 미선정, [author_reported](https://naveenraman.com/writing/fellowships.html) | 6쪽 묶음 중 첫 쪽이 Gates 에세이이며 뒤에는 Cambridge 입학 문항과 연구계획이 포함된다. MPhil 지원이다. |
| C3 Paul Bergen | 선정, [official_yearbook_and_author_report](https://www.gatescambridge.org/wp-content/uploads/2020/04/gc_yearbook_2016_web_0.pdf) | 지원 준비와 본인 글의 구성에 대한 후기다. 제출 에세이 전문은 없다. |
| C4 Onon Bayasgalan | 선정, [official_yearbook_and_interview](https://www.gatescambridge.org/wp-content/uploads/2020/04/gates_cambridge_annual_report_2019_final_web.pdf) | 동문회에 실린 본인 인터뷰다. 제출 에세이 전문은 없다. |

선정 결과는 공식 명단, 연감 또는 작성자의 보고로 확인한 상태다. 글의 특징이 그 결과를 만들었다는 뜻으로 저장하지 않는다.

## 실제 모델 입력 원문 전체

### Google

- [hyeon-index](https://hyeonword.com/google_fellowship_materials/), 사례 G1, experience_index
- [hyeon-research](https://hyeonword.com/google_fellowship_materials/assets/research_statement_hj_googlefellowship.pdf), 사례 G1, application_statement
- [hyeon-essay](https://hyeonword.com/google_fellowship_materials/assets/essay_hj_googlefellowship.pdf), 사례 G1, application_statement
- [julia-index](https://juliamendelsohn.github.io/resources/), 사례 G2, experience_index
- [julia-proposal](https://juliamendelsohn.github.io/assets/pdf/google_statement_2021.pdf), 사례 G2, application_statement
- [anjalie-index](https://anjalief.github.io/statements.html), 사례 G3, experience_index
- [anjalie-proposal](https://anjalief.github.io/docs/2019_Google_Fellowship_Proposals.pdf), 사례 G3, application_statement
- [morgan-index](https://www.morgan-klaus.com/fellowships.html), 사례 G4, experience_index
- [morgan-proposal](https://www.morgan-klaus.com/pdfs/misc/Fellowship_Materials/Google2021_Proposal.pdf), 사례 G4, application_statement
- [morgan-impact](https://www.morgan-klaus.com/pdfs/misc/Fellowship_Materials/Google2021_ImpactStatement.pdf), 사례 G4, application_statement
- [morgan-leadership](https://www.morgan-klaus.com/pdfs/misc/Fellowship_Materials/Google2021_LeadershipStatement.pdf), 사례 G4, application_statement
- [moon-interview](https://blog.google/intl/ko-kr/company-news/outreach-initiatives/phd-fellowship-2025-kr-moon/), 사례 G5, official_interview
- [yoo-interview](https://blog.google/intl/ko-kr/company-news/outreach-initiatives/phd-fellowship-2025-kr-yoo/), 사례 G6, official_interview
- [oh-interview](https://blog.google/intl/ko-kr/company-news/outreach-initiatives/phd-fellowship-2025-kr-oh/), 사례 G7, official_interview

### NVIDIA

- [rohan-index](https://rohany.github.io/), 사례 N1, experience_index
- [rohan-proposal](https://rohany.github.io/reports/nvidia-fellowship-proposal.pdf), 사례 N1, application_statement
- [yuke-proposal](https://students-at-systems.org/assets/uploads/fellowship-panel/ywang_research_statement.pdf), 사례 N2, application_statement
- [yuke-panel](https://students-at-systems.org/pages/events/fellowship-panel.html), 사례 N2, experience_review
- [ruth-proposal](https://www.ruthfong.com/files/ruth_fong_nvidia_graduate_fellowship_statement_2017.pdf), 사례 N3, application_statement

### Gates Cambridge

- [sampurna-essay](https://sampurnabuffalo.wordpress.com/wp-content/uploads/2017/11/gatesessay.pdf), 사례 C1, application_statement
- [sampurna-review](https://sampurnabuffalo.wordpress.com/2017/11/16/gates-cambridge-scholarship-sample-essay-interview-and-life-after/), 사례 C1, experience_review
- [naveen-essay](https://naveenraman.com/assets/sop/gates_cambridge.pdf), 사례 C2, application_statement
- [naveen-review](https://naveenraman.com/writing/fellowships.html), 사례 C2, experience_review
- [paul-review](https://mattsosna.com/gates-i/), 사례 C3, experience_review
- [onon-interview](https://hobbyalumni.squarespace.com/interviews/ononbayasgalan), 사례 C4, official_interview

## 무엇을 추가로 추출했는가

기존 문단 순서와 반복 관점은 `writing_pattern`으로 유지한다. 새 `criterion_observations`는 공식 평가 기준별로 주장, 본인의 행동, 결과로 제시한 근거, 기관과의 연결 및 자료의 한계를 분리한다. 원문에 없는 성과는 미확인으로 쓴다.

`outcome_comparisons`는 선정과 미선정 양쪽의 원문 인용, 관찰한 차이, 비교 한계와 본인에게 물을 사실을 함께 담는다. 차이가 분명하지 않다는 결과도 저장한다. 평가 기준은 현재 확보한 공식 안내를 참조하며 과거 심사표의 동일성은 확인되지 않았다.

## 실제 LLM 결과 예제

### NVIDIA

선정 N1과 N2는 NVIDIA 인턴십과 GPU 관련 구현을 제시하고, 미선정 N3는 안전성 연구를 NVIDIA와 공동 적용하자는 방향을 제안한다. 기존 협업과 미래 제안이라는 근거 역할의 차이가 관찰되지만 세 문서 모두 기관 연결을 명시한다.

N1의 [원문](https://rohany.github.io/reports/nvidia-fellowship-proposal.pdf) 근거:

> an internship at NVIDIA

N2의 [원문](https://students-at-systems.org/assets/uploads/fellowship-panel/ywang_research_statement.pdf) 근거:

> NVIDIA’s Clara Parabricks team

비교 한계: 관찰 대상은 세 공개 지원용 원문이다. 홈페이지 목록과 2024년 패널 후기는 당시 제출본을 대체하지 않는다. 회차, 학위 단계, 제출본 확인 수준과 결과 검증 수준이 다르다. 미래 GPU에 미친 실제 영향은 공통으로 미확인이다. 인턴십의 선정 효과나 글의 우열을 추론할 수 없다.

사용자에게 필요한 사실: NVIDIA 기술과 직접 맞닿는 연구 결과는 무엇인가? 실행한 협업과 앞으로 제안할 협업을 각각 어떻게 설명할 수 있는가? 미래 GPU의 설계, 성능 또는 사용에 영향을 줄 구체적 경로는 무엇인가?

### Gates Cambridge

선정 C1과 미선정 C2의 공개 Gates 에세이는 모두 교육과 사회적 기여를 연결한다. C1은 수업 조정 뒤 학생의 활용 일화를, C2는 다양한 지도 활동과 사회 변화의 목적을 제시한다. 제시한 결과의 구체성에 차이가 있다.

C1의 [원문](https://sampurnabuffalo.wordpress.com/wp-content/uploads/2017/11/gatesessay.pdf) 근거:

> she had become her mother's timekeeper

C2의 [원문](https://naveenraman.com/assets/sop/gates_cambridge.pdf) 근거:

> I was drawn to teaching because of its ability to enact social change

비교 한계: 2016-2017 박사와 2021 MPhil 공개본 비교다. C1은 공식 자료와 저자 보고, C2는 저자 보고로 결과가 제시됐다. 실제 최종 제출본과의 일치, 심사 판단은 미확인이다. 일화의 차이로 선정 원인을 설명할 수 없다.

사용자에게 필요한 사실: 기여 활동에서 직접 바꾼 행동과 수혜자의 변화를 확인할 기록은 무엇인가?

## 재현과 화면

추출기는 원문 전체 텍스트를 읽고 해시, 모든 문서 및 사례의 포함, 정확한 인용과 작성자 연결을 확인한다. 결과 비교에는 선정과 미선정 양쪽의 인용이 있어야 한다. 자료 목록만으로 결과 차이를 만들지 않는다. 현재 추출기는 `llm_full_experience_v2`, 프롬프트는 `whole-experience-evidence-v2`다.

원문은 공개 Git에 넣지 않고 `.local/experience-originals/`에 보관한다. 기존 원문 일부는 이전 보관 폴더의 검증된 파일을 참조한다. `make experience-originals COLLECTION="기존 collection.json의 절대 경로"`는 검토한 URL 7개만 확보한다. 이어 `make patterns COLLECTION=".local/experience-originals/collection.json"`, `make sample-db`로 분석하고 적재한다. 광역 공고 수집과 자동화는 중지 상태다.

[NVIDIA 평가 근거 비교](http://127.0.0.1:5179/benefitlens?id=nvidia-2027-2028#writing-evidence), [Gates 평가 근거 비교](http://127.0.0.1:5179/benefitlens?id=gates-cambridge-2026-2027#writing-evidence), [Google 입력 원문](http://127.0.0.1:5179/benefitlens?id=google-east-asia-2026#writing-inputs).

추가 발견한 Shan Chen과 Sid Devic의 Google 원문, 신원 미확인 Accepted 게시 사례, 접근 제한이 있었던 Brando Miranda와 Madeleine Hahne의 자료는 이번 모델 입력에 포함하지 않았다.
