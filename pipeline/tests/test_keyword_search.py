import json
import sqlite3

import pytest

from data_platform.keyword_search import analyze, safe_source_url, save_database, scope_hint, url_key


def before():
    return {"urls": {"https://known.test/"}, "hosts": {"known.test"}, "originals": {"https://known.test/"}, "metadata": set(), "scope": {"known_url_keys": 1}}


def row(id, query, urls, stage="initial"):
    return {"id": id, "q": query, "stage": stage, "group": "funding", "group_label": "지원", "status": "executed",
        "results": [{"title": "PhD scholarship", "url": url} for url in urls]}


def test_url_identity_keeps_program_and_round_identifiers():
    assert url_key("http://www.official.test/view?round=2026&program=1&utm_source=a#x") == "https://official.test/view?program=1&round=2026"
    assert url_key("https://official.test/view?round=2027&program=1") != url_key("https://official.test/view?round=2026&program=1")
    assert url_key("https://official.test/view;jsessionid=SECRET?program=1") == "https://official.test/view?program=1"


def test_encoded_session_paths_are_removed_from_public_source_links():
    url = "http://www.official.test/view%3BHOMEPAGE_JSESSIONID%3DSECRET?articleNo=5&homepage_jsessionid=SECRET"
    assert safe_source_url(url) == "http://www.official.test/view?articleNo=5"
    summary = analyze([row("q001", "funding", [url])], before())
    assert "SECRET" not in json.dumps(summary)
    assert "SECRET" not in safe_source_url("https://official.test/sso?RelayState=https%3A%2F%2Fofficial.test%2Fview%3Bjsessionid%3DSECRET%3Fid%3D1")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///tmp/private", "https://user:pass@official.test/", "https://127.0.0.1/", "https://10.0.0.1/", "https://localhost/", "https://official.test/?access_token=PRIVATE"])
def test_unsafe_url_is_not_exported(url):
    assert url_key(url) is None


def test_duplicate_urls_keep_multiple_query_links():
    summary = analyze([row("q002", "doctoral funding", ["http://www.new.test/fellowship"]), row("q001", "scholarship", ["https://new.test/fellowship?utm_source=a", "https://new.test/fellowship"])], before())
    assert summary["counts"]["returned_url_mentions"] == 3
    assert summary["counts"]["unique_url_keys"] == 1
    assert summary["candidates"][0]["query_ids"] == ["q001", "q002"]
    assert summary["queries"][0]["marginal_url_keys"] == 1
    assert summary["queries"][1]["marginal_url_keys"] == 0
    assert summary["counts"]["verified_programs"] is None


def test_failed_execution_is_not_search_with_zero_results():
    empty = row("q001", "empty", []); empty["status"] = "no_parseable_results"
    failed = row("q002", "failed", []); failed["status"] = "failed"
    summary = analyze([empty, failed], before())
    assert summary["counts"]["executed_queries"] == 1
    assert summary["counts"]["failed_queries"] == 1
    assert summary["counts"]["new_originals_fetched_by_this_module"] == 0


def test_multiple_documents_on_one_host_are_not_multiple_institutions():
    summary = analyze([row("q001", "funding", ["https://new.test/notice", "https://new.test/form", "https://new.test/review"])], before())
    assert summary["counts"]["unique_url_keys"] == 3
    assert summary["counts"]["host_keys"] == 1


def test_noise_and_login_paths_are_not_prioritized_for_crawling():
    query = row("q001", "funding", ["https://new.test/fellowship", "https://new.test/login", "https://new.test/loan"])
    query["results"][2]["title"] = "학자금 대출"
    summary = analyze([query], before())
    assert len(summary["crawl_queue"]) == 1
    assert summary["crawl_queue"][0]["url"] == "https://new.test/fellowship"


def test_other_university_pages_have_scope_review_not_applicant_recommendation():
    assert scope_hint("ax.postech.ac.kr") == "postech"
    assert scope_hint("grad.princeton.edu") == "other_university_scope_review"
    assert scope_hint("foundation.org") == "non_university_or_unknown"


def test_long_tool_excerpt_labels_do_not_become_public_document_bodies():
    query = row("q001", "funding", ["https://new.test/fellowship"])
    query["results"][0]["title"] = "PhD scholarship " + "long source label " * 100
    summary = analyze([query], before())
    assert len(summary["candidates"][0]["title"]) <= 160
    assert summary["candidates"][0]["label_truncated"]


def test_adaptive_query_provenance_is_preserved():
    parent = row("q001", "scholarship", ["https://new.test/fellowship"])
    child = row("q081", "new program country application", ["https://new.test/form"], "adaptive")
    child["derived_from"] = {"source_query_id": "q001", "source_url": "https://new.test/fellowship", "observed_term": "new program", "citation_ref": "internal"}
    summary = analyze([parent, child], before())
    assert summary["counts"]["adaptive_marginal_urls"] == 1
    assert summary["queries"][1]["derived_from"]["source_query_id"] == "q001"
    assert "citation_ref" not in summary["queries"][1]["derived_from"]


def test_repeated_queries_are_rejected_not_counted_toward_100():
    with pytest.raises(ValueError, match="repeated query"):
        analyze([row("q001", "same", []), row("q002", "same", [])], before())


def test_saving_same_batch_is_idempotent(tmp_path):
    summary = analyze([row("q001", "funding", ["https://new.test/fellowship"])], before())
    database = tmp_path / "keyword.sqlite"
    save_database(database, summary); save_database(database, summary)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM keyword_run").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM search_result").fetchone()[0] == 1
        assert json.loads(conn.execute("SELECT payload_json FROM url_candidate").fetchone()[0])["original_status"] == "not_collected_in_keyword_search"
