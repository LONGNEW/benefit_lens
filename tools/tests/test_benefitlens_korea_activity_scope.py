"""Student activities enter review with their payment and application evidence."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_benefitlens_korea import board_items, classify


@pytest.mark.parametrize("title,payment", [
    ("대학생 서포터즈 모집", "월 활동비 10만원"),
    ("대학생 아이디어 경진대회", "수상 상금 100만원"),
    ("대학생 실험 참가자 모집", "참여 보상 상품권 1만원"),
    ("대학원생 연구조교 모집", "월 급여 50만원"),
])
def test_paid_student_activity_reaches_review(title, payment):
    text = f"대학생과 대학원생 대상. {payment}. 신청기간 2026.11.01 ~ 2026.11.30. 신청방법 온라인."
    assert classify(text, title, "2026-11-15") == "awaiting_review"


def test_activity_without_payment_and_expired_notice_are_held():
    text = "대학생 대상. 신청기간 2026.11.01 ~ 2026.11.30. 신청방법 온라인."
    assert classify(text, "대학생 서포터즈 모집", "2026-11-15") == "activity_payment_unconfirmed"
    text = text.replace("2026.11.30", "2026.11.10") + " 활동비 10만원."
    assert classify(text, "대학생 서포터즈 모집", "2026-11-15") == "current_application_dates_unconfirmed"


def test_board_lists_new_activity_but_skips_result_announcement():
    html = ('<a href="/notice?articleNo=1">Student stipend</a>'
            '<a href="/notice?articleNo=2">대학생 서포터즈 모집</a>'
            '<a href="/notice?articleNo=3">서포터즈 선발 결과</a>').encode()
    result = list(board_items(html, "https://example.edu/notice",
                             {"identity": "articleNo", "issuer": "Example University"}))
    assert [native for native, _ in result] == ["1", "2"]
