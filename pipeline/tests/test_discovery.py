import hashlib
import json
import sqlite3

import pytest

from data_platform.discovery import Crawler, canonical_url, catalog_seed_rows, host_key, page_data, robots_allowed, role_for, save_database
from data_platform.http import FetchResult, SafeHttpClient


def result(url, body, content_type="text/html"):
    return FetchResult(url, url, 200, content_type, {}, body.encode())


def config(**limits):
    return {"user_agent": "BenefitLens-course-demo/1.0", "allowed_hosts": ["official.test"],
        "limits": {"max_depth": 2, "max_page_fetches": 6, "max_pages_per_host": 6,
            "delay_seconds": 0, "timeout_seconds": 1, "max_body_bytes": 10000, **limits}}


class FakeClient:
    def __init__(self, pages):
        self.pages, self.seen = pages, []

    def get(self, url, request_guard=None, response_observer=None):
        if request_guard:
            request_guard(url)
        self.seen.append(url)
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        if response_observer:
            response_observer(url, 200, None)
        return result(url, value)


def seeds(*routes):
    return [{"seed_id": str(i), "route": route, "url": "https://official.test/", "label": "재단", "query": "장학금" if route == "search" else None}
        for i, route in enumerate(routes)]


def test_canonical_preserves_identifiers_and_removes_only_tracking():
    assert canonical_url("https://Official.test:443/notice?wr_id=102&ncid=123&id=%2Fkj#faq") == "https://official.test/notice?id=%2Fkj&wr_id=102"
    for url in ["http://official.test", "javascript:alert(1)", "https://user:pass@official.test", "https://official.test:8080", "file:///tmp/private"]:
        assert canonical_url(url) is None
    assert host_key("https://www.official.test/") == host_key("https://official.test/")


@pytest.mark.parametrize("url,allowed", [("/admin/a", False), ("/admin/public", True), ("/a/private.pdf", False), ("/a/public.pdf", True), ("/a/private.pdf?x=1", True)])
def test_robots_longest_rule_and_wildcards(url, allowed):
    text = "User-agent: *\nDisallow: /admin/\nAllow: /admin/public\nDisallow: /*private.pdf$\nCrawl-delay: 3"
    assert robots_allowed(text, "https://official.test" + url, "BenefitLens-course-demo/1.0") == (allowed, 3.0)


def test_specific_agent_does_not_apply_to_other_agents():
    assert robots_allowed("User-agent: Googlebot\nDisallow: /", "https://official.test/", "BenefitLens") == (True, 0)
    assert robots_allowed("User-agent: *\nDisallow: /\nUser-agent: BenefitLens\nAllow: /", "https://official.test/", "BenefitLens-course-demo") == (True, 0)


def test_links_filter_logins_and_keep_literal_navigation():
    html = '<h1>재단</h1><a href="/login">장학생 로그인</a><a href="/notice?wr_id=1">장학금 공고</a><a href="#faq">FAQ</a><button onclick="location.href=\'/guide.pdf\'">신청 양식</button><a href="https://other.test/fellowship">새 재단</a>'
    title, links = page_data(html.encode(), "https://official.test/")
    assert title == "재단"
    assert [item["url"] for item in links] == ["https://official.test/notice?wr_id=1", "https://official.test/guide.pdf", "https://other.test/fellowship"]


def test_same_url_two_discovery_paths_preserve_both_edges_and_children(tmp_path):
    fake = FakeClient({"https://official.test/robots.txt": "User-agent: *\nAllow: /", "https://official.test/": '<a href="/notice">장학금 모집 공고</a>', "https://official.test/notice": "<h1>장학금 모집</h1>"})
    out = Crawler(config(), fake).crawl(seeds("catalog", "search"), tmp_path)
    assert len(out["documents"]) == 2
    assert fake.seen.count("https://official.test/") == 1
    assert len(out["edges"]) == 4
    assert {e["route"] for e in out["edges"] if e["url"].endswith("notice")} == {"catalog", "search"}
    assert all(e["document_id"] for e in out["edges"])


def test_depth_and_visit_limits_do_not_claim_complete_coverage(tmp_path):
    pages = {"https://official.test/robots.txt": "User-agent: *\nAllow: /", "https://official.test/": '<a href="/notice">공고</a>', "https://official.test/notice": '<a href="/guide">양식</a>'}
    out = Crawler(config(max_depth=1), FakeClient(pages)).crawl(seeds("search"), tmp_path)
    assert len(out["documents"]) == 2
    assert max(e["depth"] for e in out["edges"]) == 1
    out = Crawler(config(max_page_fetches=1), FakeClient(pages)).crawl(seeds("search"), tmp_path)
    assert out["page_fetch_attempts"] == 1
    assert out["edges"][1]["status"] == "visit_limit"


def test_external_candidate_is_recorded_without_request(tmp_path):
    fake = FakeClient({"https://official.test/robots.txt": "", "https://official.test/": '<a href="https://other.test/fellowship">장학금</a>'})
    out = Crawler(config(), fake).crawl(seeds("search"), tmp_path)
    assert out["edges"][1]["status"] == "external_candidate_not_fetched"
    assert not any("other.test" in url for url in fake.seen)


def test_experience_body_is_not_saved(tmp_path):
    fake = FakeClient({"https://official.test/robots.txt": "", "https://official.test/": '<a href="/interview">장학생 인터뷰</a>', "https://official.test/interview": '<h1>장학생 인터뷰</h1><p>PERSONAL_ANSWER_NOT_FOR_STORAGE</p>'})
    out = Crawler(config(), fake).crawl(seeds("search"), tmp_path)
    doc = next(d for d in out["documents"] if d["role"] == "experience")
    assert doc["capture_mode"] == "metadata_only" and doc["raw_path"] is None
    assert doc["sha256"] is None and doc["metadata_sha256"]
    assert "PERSONAL_ANSWER_NOT_FOR_STORAGE" not in json.dumps(out)
    assert not any(b"PERSONAL_ANSWER_NOT_FOR_STORAGE" in p.read_bytes() for p in (tmp_path / "raw").iterdir())


def test_robots_unavailable_blocks_page_but_absent_404_is_recorded(tmp_path):
    for error, fetched in [(RuntimeError("HTTP 403 from robots"), False), (RuntimeError("HTTP 404 from robots"), True)]:
        fake = FakeClient({"https://official.test/robots.txt": error, "https://official.test/": "<h1>재단</h1>"})
        out = Crawler(config(), fake).crawl(seeds("catalog"), tmp_path)
        assert bool(out["documents"]) is fetched


def test_robots_disallow_prevents_page_contact(tmp_path):
    fake = FakeClient({"https://official.test/robots.txt": "User-agent: *\nDisallow: /"})
    out = Crawler(config(), fake).crawl(seeds("search"), tmp_path)
    assert not out["documents"]
    assert fake.seen == ["https://official.test/robots.txt"]


def test_authentication_destination_is_not_contacted():
    crawler = Crawler(config(), FakeClient({}))
    with pytest.raises(RuntimeError, match="authentication_path_not_fetched"):
        crawler._guard_page("https://official.test/umlg/umlg01.do")
    assert not crawler.request_log


def test_redirect_guard_runs_before_destination_request(monkeypatch):
    client = SafeHttpClient(allowed_hosts={"official.test", "destination.test"}, user_agent="test", timeout_seconds=1, max_body_bytes=1000)
    monkeypatch.setattr("data_platform.http.socket.getaddrinfo", lambda *a, **k: [(None, None, None, None, ("8.8.8.8", 443))])
    seen = []
    class Redirect:
        status_code = 302
        headers = {"Location": "https://destination.test/private"}
        def close(self): pass
    def get(url, **kwargs):
        seen.append(url)
        return Redirect()
    monkeypatch.setattr("data_platform.http.requests.get", get)
    def guard(url):
        if "destination.test" in url:
            raise RuntimeError("robots_disallowed")
    with pytest.raises(RuntimeError, match="robots_disallowed"):
        client.get("https://official.test/", request_guard=guard)
    assert seen == ["https://official.test/"]


def test_catalog_prefers_graduate_row_without_changing_program_data():
    rows = [{"번호": "1", "운영기관명": "관정재단", "상품명": "학부", "대학구분": "학부", "홈페이지 주소": "https://www.ikef.or.kr/"},
        {"번호": "2", "운영기관명": "관정재단", "상품명": "대학원", "대학구분": "일반대학원", "홈페이지 주소": "https://www.ikef.or.kr"}]
    assert catalog_seed_rows(rows, ["관정재단"])[0]["catalog_row"] == "2"


def test_recipient_profile_is_not_actual_application_experience():
    assert role_for("https://research.nvidia.com/graduate-fellowships/2026", "Fellows") == "recipient_profile"
    assert role_for("https://research.nvidia.com/nv-grad-fellow/modal/4997", "| Research") == "recipient_profile"


def test_declared_public_form_action_resolves_article_without_saving_preview():
    html = '''<script>function listView(listSeq){$('#contentNo').val(listSeq);$('#listForm').attr({action:'/html/stifFront01View.do;jsessionid=ANONYMOUS?contentTypeCode=FS'}).submit();}</script><a href="javaScript:;" onclick="listView(43120)"><p class="title">장학생 인터뷰</p><p class="detail">PERSONAL_PREVIEW_NOT_A_TITLE</p></a>'''
    _, links = page_data(html.encode(), "https://www.iljufoundation.org/html/stifFront01.do")
    assert len(links) == 1
    assert links[0]["label"] == "장학생 인터뷰"
    assert "jsessionid" not in links[0]["url"]
    assert "contentNo=43120" in links[0]["url"]
    assert "PERSONAL_PREVIEW" not in json.dumps(links)


def test_database_reuses_same_version_and_retains_per_run_edges(tmp_path):
    doc = {"id": "doc1", "final_url": "https://official.test/notice", "sha256": "hash"}
    row = {"번호": "1", "운영기관명": "재단"}
    run = {"run": {"id": "run1", "started_at": "a", "finished_at": "b"}, "catalog": {"sha256": "csvhash"}, "documents": [doc], "edges": [{"route": "catalog"}]}
    path = tmp_path / "collection.sqlite"
    assert save_database(path, run, [row])["new_versions"] == 1
    run["run"]["id"] = "run2"
    assert save_database(path, run, [row])["new_versions"] == 0
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM catalog_item").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM document_version").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM discovery_edge").fetchone()[0] == 2
