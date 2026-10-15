import unittest

from data_platform.funding import classify_funding, operator_group


class FundingTests(unittest.TestCase):
    def test_multi_label_and_exact_evidence(self):
        text = "등록금 보전 884,000원 / 생활비 보조 966,000원"
        result = classify_funding("재정지원 제도", [text], {"benefits": ["조교수당 지급"]})
        self.assertEqual(result["tags"], ["cash", "tuition", "living_expenses"])
        self.assertEqual(result["evidence"][1]["text"], "등록금 보전 884,000원")
        self.assertEqual(result["evidence"][2]["text"], "생활비 보조 966,000원")

    def test_title_only_does_not_invent_amount_or_usage(self):
        result = classify_funding("2027 포스코한국유학장학생 선발", ["2027 포스코한국유학장학생 선발"])
        self.assertEqual(result["tags"], ["scholarship"])
        self.assertEqual(result["evidence"][0]["text"], "2027 포스코한국유학장학생 선발")

    def test_scholarship_evidence_prefers_title_to_incidental_restriction(self):
        result = classify_funding("포스코해외유학장학 선발", ["성적 미달 시 장학금 지급 중단", "생활비: USD 30,000/년"])
        evidence = next(item for item in result["evidence"] if item["tag"] == "scholarship")
        self.assertEqual(evidence["field"], "title")
        self.assertEqual(evidence["text"], "포스코해외유학장학 선발")

    def test_additional_benefit_not_hidden_by_goods(self):
        result = classify_funding("AI 교육", ["모니터 제공", "월 50만 원 훈련 장려금"])
        self.assertEqual(result["tags"], ["cash"])

    def test_eligibility_and_period_not_used_as_benefit(self):
        result = classify_funding("채용 설명회", ["멘토링"], {
            "eligibility": ["장학금 수혜자 제외"],
            "conditions": ["등록금 납부 필요"],
            "period": ["생활비 지원 대상 공지"],
        })
        self.assertEqual(result["tags"], [])

    def test_loan_interest_is_not_direct_tuition_or_living_aid(self):
        result = classify_funding("안동시 학자금 대출 이자 지원", ["등록금, 생활비 대출의 발생이자 지원"])
        self.assertEqual(result["tags"], [])

    def test_free_training_goods_prizes_and_own_cost_are_excluded(self):
        for text in ["교육비 전액 무료", "노트북 지급", "총 상금 1,000만원 지급", "본인 부담 등록금 60만원", "월 5만원 구독 이용권 지원", "장학금 수혜자 제외"]:
            with self.subTest(text=text):
                self.assertEqual(classify_funding("교육 프로그램", [text])["tags"], [])

    def test_unknown_operator_is_not_a_company(self):
        self.assertEqual(operator_group("association_nonprofit"), "other")
        self.assertEqual(operator_group("corporate_foundation"), "foundation")
        self.assertEqual(operator_group("public_agency"), "national")

    def test_scholarship_title_spellings_and_purpose(self):
        self.assertEqual(classify_funding("포스코비전장학 선발", [])["tags"], ["scholarship"])
        self.assertEqual(classify_funding("포스코사이언스펠로십 선발", [])["tags"], ["scholarship"])
        self.assertEqual(classify_funding("생활비 장학금", ["매월 50만원"])["tags"], ["cash", "scholarship", "living_expenses"])
        self.assertEqual(classify_funding("삼성대여장학금", ["등록금 지원"])["tags"], [])

    def test_standalone_extracted_amount_and_foreign_currency(self):
        self.assertEqual(classify_funding("활동 지원", ["8,000,000원 (연간 최대)"])["tags"], ["cash"])
        self.assertEqual(classify_funding("해외유학 장학", ["생활비: USD 30,000/년"])["tags"], ["scholarship", "living_expenses"])


if __name__ == "__main__":
    unittest.main()
