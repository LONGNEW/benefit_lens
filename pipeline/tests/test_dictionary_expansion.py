import copy
import json
from pathlib import Path

import pytest

from data_platform.dictionary_expansion import Expansion, chunks, contains, fingerprint, search_receipts


@pytest.fixture
def config():
    return {"schema_version": "test", "scope": "fixture", "seeds": [{"term": "studentship", "lane": "announcement"}],
            "templates": {"announcement": {"en": ['"{term}" graduate funding'], "ko": ['"{term}" 대학원']}},
            "results_per_query": 3, "search_batch_size": 4, "fetch_workers": 2,
            "extract_batch_size": 4, "chunk_characters": 16000, "max_attempts": 2}


class Backend:
    def __init__(self):
        self.searches = []

    def search(self, queries):
        self.searches.extend(queries)
        return {q: {"event_ids": ["real-fixture-event"], "results": [{"url": "https://example.org/funding", "title": "Funding"}]} for q in queries}, "fixture"

    def extract(self, documents, dictionary):
        return {"documents": [{"chunk_id": d["chunk_id"], "terms": [{"term": "completion fellowship", "lane": "announcement",
                 "kind": "funding_mechanism", "quote": "A completion fellowship funds doctoral students.", "explanation": "Final-stage student funding", "equivalent_to": ""}]} for d in documents]}, "fixture"


def fetch(url, folder):
    text = "A completion fellowship funds doctoral students. " * 3
    path = folder / "text" / "fixture.txt"
    path.write_text(text)
    return {"text": text, "text_path": str(path), "content_sha256": fingerprint(text), "final_url": url}


def test_expands_new_terms_then_requires_confirmation_and_resumes(tmp_path, config):
    backend = Backend()
    run = Expansion(tmp_path, config, backend, fetcher=fetch)
    result = run.run()
    assert result["status"] == "converged"
    assert result["dictionary_terms"] == 2
    assert [w["payload"]["new_terms"] for w in result["waves"]] == [1, 0, 0]
    assert result["waves"][-1]["mode"] == "confirmation_rescan"
    assert '"completion fellowship" graduate funding' in backend.searches
    before = len(backend.searches)
    assert Expansion(tmp_path, config, backend, fetcher=fetch).run()["status"] == "converged"
    assert len(backend.searches) == before
    graph = json.loads((tmp_path / "expansion-graph.json").read_text())
    assert any(e["term"] == "completion fellowship" for e in graph["edges"])


def test_failure_and_query_cap_are_never_convergence(tmp_path, config, monkeypatch):
    monkeypatch.setattr("data_platform.dictionary_expansion.time.sleep", lambda _: None)
    def blocked(*_):
        raise RuntimeError("HTTP 403 from public origin")
    run = Expansion(tmp_path / "blocked", config, Backend(), fetcher=blocked)
    result = run.run()
    assert result["status"] == "frontier_exhausted_with_errors"
    assert result["counters"]["document"]["failed"] == 1
    assert run.rows("SELECT attempts FROM document")[0]["attempts"] == 1
    capped = Expansion(tmp_path / "capped", config, Backend(), fetcher=fetch)
    assert capped.run(max_queries=1)["status"] == "query_limit"
    assert capped.run()["status"] == "converged"


def test_recovered_failed_source_reopens_expansion(tmp_path, config, monkeypatch):
    monkeypatch.setattr("data_platform.dictionary_expansion.time.sleep", lambda _: None)
    def blocked(*_):
        raise RuntimeError("HTTP 403")
    run = Expansion(tmp_path, config, Backend(), fetcher=blocked)
    assert run.run()["status"] == "frontier_exhausted_with_errors"
    run.fetcher = fetch
    result = run.run(retry_failed=True)
    assert result["status"] == "converged"
    assert result["dictionary_terms"] == 2
    # A recovered source originated in wave 1, but must still schedule its new
    # expression in the reopened active wave rather than silently leaving it out.
    assert '"completion fellowship" graduate funding' in run.backend.searches


def test_real_search_event_required_and_model_urls_cannot_be_imported():
    query = '"studentship" graduate funding'
    event = {"type": "item.completed", "item": {"id": "search1", "type": "web_search", "action": {"type": "search", "query": query},
             "results": [{"url": "https://example.org/real", "title": "Original"}, {"url": "file:///etc/passwd"}]}}
    assert search_receipts([event], [query])[query]["results"][0]["url"] == "https://example.org/real"
    with pytest.raises(ValueError, match="exact-query"):
        search_receipts([{"type": "item.completed", "item": {"type": "agent_message", "text": "I searched."}}], [query])
    with pytest.raises(ValueError, match="exact-query"):
        search_receipts([event], ["rewritten query"])
    pooled = copy.deepcopy(event)
    pooled["item"]["action"] = {"type": "search", "queries": [query, "another query"]}
    with pytest.raises(ValueError, match="pooled"):
        search_receipts([pooled], [query, "another query"])


def test_literal_evidence_and_chunk_coverage(tmp_path, config):
    run = Expansion(tmp_path, config, Backend(), fetcher=fetch)
    inputs = [{"chunk_id": "c", "text": "A completion fellowship funds doctoral students."}]
    result = run.backend.extract(inputs, run.dictionary())[0]
    run.validate_extraction(result, inputs, run.dictionary())
    result["documents"][0]["terms"][0]["term"] = "imaginary award"
    with pytest.raises(ValueError, match="absent"):
        run.validate_extraction(result, inputs, run.dictionary())
    assert not contains("immigrant support", "grant")
    assert contains("Travel\n grant", "travel grant")
    text = "abcdefghijklmnopqrstuvwxyz" * 2000
    covered = set()
    for start, end, value in chunks(text):
        assert value == text[start:end]
        covered.update(range(start, end))
    assert covered == set(range(len(text)))


def test_resume_rejects_silent_scope_change_and_double_runner(tmp_path, config):
    run = Expansion(tmp_path, config, Backend(), fetcher=fetch)
    changed = copy.deepcopy(config)
    changed["results_per_query"] = 5
    with pytest.raises(ValueError, match="policy changed"):
        Expansion(tmp_path, changed, Backend())
    with run.lock():
        with pytest.raises(BlockingIOError):
            with run.lock():
                pass


def test_invalid_evidence_does_not_discard_other_chunks(tmp_path, config):
    run = Expansion(tmp_path, config, Backend(), fetcher=fetch)
    inputs = [
        {"chunk_id": "valid", "text": "A completion fellowship funds doctoral students."},
        {"chunk_id": "invalid", "text": "The announcement does not describe any award."},
    ]
    result, _ = run.backend.extract(inputs, run.dictionary())
    rejected = {}
    valid = run.validate_extraction(result, inputs, run.dictionary(), rejected=rejected)
    assert [chunk["chunk_id"] for chunk in valid] == ["valid"]
    assert "absent" in str(rejected["invalid"])
