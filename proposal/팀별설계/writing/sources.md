# 작성 기준과 사례 근거

2026-11-09 확인. 기관 안내 2개와 원 연구의 저자 초록 1개를 읽었다. 새 경험 자료의 수집은 하지 않았다.

| 원문 | 정확한 인용과 위치 | 무엇에 쓸 수 있나 |
| --- | --- | --- |
| [NVIDIA Graduate Fellowship](https://research.nvidia.com/graduate-fellowships), 2027-2028, How to apply 및 Helpful hints | “The bibliography does not count toward the limit.”, “Connect the work to NVIDIA”, “If you previously interned at NVIDIA, describe the work.” | 해당 회차는 연구계획 본문과 그림 2쪽, 참고문헌 별도. GPU 관련성, 현재까지의 성과와 비전은 평가 및 작성 안내다. 과거 NVIDIA 인턴 경험 서술은 해당할 때 추가하는 안내다. 대면 인턴 수행 가능 여부라는 별도 공고 요구와 구분한다. |
| [NSF GRFP 공식 How to Apply](https://www.nsf.gov/funding/initiatives/grfp/how-to-apply), Graduate Research Plan Statement 및 Statement formatting requirements | “You must include headings for Intellectual Merit and Broader Impacts in your statement.”, “These page limits include all references, citations” | NSF 연구계획은 참고문헌을 포함해 2쪽이며 해당 두 제목이 필수다. NVIDIA의 형식과 달라 타 기관의 양식, 분량 계산과 필수 제목은 이전할 수 없다. |
| [Lu 외, System 2021, 저자 초록](https://digitalcommons.memphis.edu/facpubs/18089/), DOI 10.1016/j.system.2021.102543 | “substantial disciplinary variation in both the distribution of rhetorical move-steps and the associations between phrase-frames and rhetorical move-steps” | 사회과학 5개 분야의 출판 논문 서론 500편을 비교한 연구다. 글의 기능을 나눠 관찰할 근거는 되지만 지원서의 이상적 순서, 선정률이나 작성 효과를 시험한 연구는 아니다. |

일반 원칙에서 “읽는 이의 질문에 답하고, 본인 역할, 결과와 한계를 연결한다”를 설계 후보로 삼았다. 기관 안내와 연구를 적용해 만든 제안이며 위 논문이 이 원칙의 지원서 효과를 직접 입증한 것은 아니다. 팀 토의는 반례와 후보를 발굴하는 입력이며 원문 근거로 승격하지 않는다.

기존 자료 대조:

- `data/sample/programs.json`의 `nvidia-2027-2028`, `application.prompts[0]`은 `research_proposal`, `max_pages: 2`, `references_excluded: true`를 담는다. 같은 URL의 공식 원문을 보존한 `benefitlens/pipeline/data/proposal_demo/2026-10-15/text/nvidia-official.txt` 131행은 양식, 151-163행은 작성 안내다. 상세 작성 안내의 구조화 필드는 이 표본의 `application.prompts[0]`에 없다.
- [N2 공개 연구문 21행](</home/longnew/Desktop/course_work/26-2 학기/데이터엔지니어링/benefit_lens/.local/experience-originals/text/yuke-proposal.txt:21>): “Below I would briefly discuss my highlighted work to achieve this goal:”는 연구 목표에서 프로젝트로 전환하는 문장이다. PDF 제목은 “Research Summary”이며 정확한 최종 제출본과 당시 문항 매핑은 미확인이다.
- [N2 패널 후기 37행](</home/longnew/Desktop/course_work/26-2 학기/데이터엔지니어링/benefit_lens/.local/experience-originals/text/yuke-panel.txt:37>): “Yuke advised highlighting key insights and comparing them to existing work.”는 행사 후기에서 발표자에게 귀속한 조언이다. 공개 연구문과 같은 사람의 자료이고 제출 글의 순서를 확인하는 근거가 아니다.
- [저장된 사례 구조](</home/longnew/Desktop/course_work/26-2 학기/데이터엔지니어링/benefit_lens/data/sample/programs.json:3307>): `research-arc`는 N1, N2, N3의 연구 목표, 기여와 근거, 다음 단계 또는 지원 효용을 관찰한 기존 패턴이다. 최종본 동일성, 인과 효과나 신청 가능 상태를 확인한 값이 아니다.
