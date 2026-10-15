from datetime import datetime, timezone
from data_platform.benefitlens import _retained_notice_records, _collect_html_details
from data_platform.parsers import ParsedOpportunity


def notice(key="current"):
    return ParsedOpportunity(canonical_key=key, title="공고", summary="공고", official_url="https://official.test/detail", benefit_type="unknown", benefit_description="공고")


class Result:
    def __init__(self, value): self.value = value
    def fetchall(self): return self.value
    def fetchone(self): return self.value


def test_retained_active_notices_are_refreshed_within_the_existing_detail_limit():
    class Connection:
        def execute(self, query, params):
            assert "lifecycle_status<>'closed'" in query
            return Result([("current", "공고", "https://official.test/current", "2026.09.01"), ("older", "이전 공고", "https://official.test/older", "2026.08.01")])
    records = _retained_notice_records(Connection(), {"source_id": "source", "detail_parser": "postech_cse_notice_detail", "detail_limit": 2}, [notice()])
    assert [r.canonical_key for r in records] == ["current", "older"]
    assert records[1].evidence["published_at"] == "2026.08.01"


def test_detail_failure_does_not_overwrite_existing_detail_with_list_only_row():
    class Connection:
        def execute(self, query, params): return Result((1,))
    class Client:
        def get(self, url): raise RuntimeError("official page unavailable")
    records, _, _, stats = _collect_html_details(Connection(), Client(), {"source_id": "source", "detail_parser": "postech_cse_notice_detail"}, [notice()], "run", datetime.now(timezone.utc))
    assert records == []
    assert stats["failed"] == 1


def test_new_detail_failure_remains_an_unstructured_candidate():
    class Connection:
        def execute(self, query, params): return Result(None)
    class Client:
        def get(self, url): raise RuntimeError("official page unavailable")
    records, _, _, stats = _collect_html_details(Connection(), Client(), {"source_id": "source", "detail_parser": "postech_cse_notice_detail"}, [notice()], "run", datetime.now(timezone.utc))
    assert records == [notice()]
    assert stats["failed"] == 1
