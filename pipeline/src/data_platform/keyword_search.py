"""Audit tool-returned search metadata; this module never fetches candidate bodies."""
from __future__ import annotations

import argparse
import hashlib
import html
import ipaddress
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .discovery import PRIVATE, clean_text, role_for

ROOT = Path(__file__).resolve().parents[2]
TOPIC = re.compile(r"장학|장려금|연구지원|자기소개서|학업계획서|연구계획서|수기|후기|fellowship|scholarship|studentship|bursar|stipend|research.grant|travel.grant|personal.statement|motivation.letter|dissertation.funding|application.guideline", re.I)
NOISE = re.compile(r"대출|등록금통계|평균등록금|학과별 등록금|대학별 등록금|(?:^|[/\s])loan|hiring|careers|jobs|tuition02|tuition04_09", re.I)
SESSION = re.compile(r"(?:;|%3b)(?:homepage_)?jsessionid(?:=|%3d)[^/;?]*", re.I)


def safe_source_url(value: str) -> str | None:
    """Keep the source scheme while removing tracking and anonymous session data."""
    try:
        p = urlsplit(html.unescape(value.strip()))
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password or p.port not in {None, 80, 443}:
            return None
        host = p.hostname.lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        pairs = parse_qsl(p.query, keep_blank_values=True)
        if any(k.lower() in {"apikey", "servicekey", "password", "access_token", "auth_token"} for k, _ in pairs):
            return None
        pairs = [(k, SESSION.sub("", v)) for k, v in pairs if not k.lower().startswith("utm_") and k.lower() not in {"ncid", "trk", "gclid", "fbclid"} and not k.lower().endswith("jsessionid")]
        path = SESSION.sub("", p.path) or "/"
        return urlunsplit((p.scheme, host, path, urlencode(sorted(pairs)), ""))
    except (TypeError, ValueError):
        return None


def url_key(value: str) -> str | None:
    """HTTP/HTTPS and www aliases share an audit identity, not a fetch permission."""
    source = safe_source_url(value)
    if not source:
        return None
    p = urlsplit(source)
    return urlunsplit(("https", (p.hostname or "").removeprefix("www."), p.path, p.query, ""))


def hostname(url: str) -> str:
    return urlsplit(url).hostname or ""


def baseline(collection: Path, catalog_db: Path, references: Path, supplements: list[tuple[str, Path]] | None = None) -> dict:
    old = json.loads(collection.read_text(encoding="utf-8"))
    urls = set()
    originals, metadata = set(), set()
    for doc in old["documents"]:
        key = url_key(doc["final_url"])
        if key:
            urls.add(key)
            (metadata if doc["capture_mode"] == "metadata_only" else originals).add(key)
    for edge in old["edges"]:
        key = url_key(edge["url"])
        if key:
            urls.add(key)
    catalog_urls = set()
    with sqlite3.connect(catalog_db) as conn:
        for payload, in conn.execute("SELECT payload_json FROM catalog_item"):
            row = json.loads(payload)
            for value in re.findall(r"https?://[^\s<>]+", row.get("홈페이지 주소", "")):
                key = url_key(value.rstrip(",;"))
                if key:
                    catalog_urls.add(key)
    ref_urls = {key for value in re.findall(r"\]\((https?://[^\s)]+)\)", references.read_text(encoding="utf-8")) if (key := url_key(value))}
    urls.update(catalog_urls | ref_urls)
    supplemental_counts = {}
    for label, path in supplements or []:
        value = json.loads(path.read_text(encoding="utf-8"))
        strings = []
        def visit(item):
            if isinstance(item, dict):
                for child in item.values():
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)
            elif isinstance(item, str):
                strings.append(item)
        visit(value)
        found = {key for item in strings for value in re.findall(r"https?://[^\s<>\"']+", item) if (key := url_key(value))}
        urls.update(found)
        supplemental_counts[label] = len(found)
    hosts = {hostname(u) for u in urls} | set(old["run"].get("registered_hosts_before_run", []))
    return {"urls": urls, "hosts": hosts, "originals": originals, "metadata": metadata,
        "scope": {"collection_run_id": old["run"]["id"], "known_url_keys": len(urls), "catalog_homepage_keys": len(catalog_urls),
            "previous_reference_keys": len(ref_urls), "original_keys": len(originals), "metadata_keys": len(metadata),
            "supplemental_url_keys": supplemental_counts, "known_host_keys": len(hosts),
            "normalization": "http_https_www_tracking_aliases; identifiers and pagination preserved"}}


def scope_hint(host: str) -> str:
    if host == "postech.ac.kr" or host.endswith(".postech.ac.kr"):
        return "postech"
    if host.endswith((".edu", ".ac.kr", ".ac.uk", ".edu.au", ".edu.cn")) or ".edu." in host:
        return "other_university_scope_review"
    return "non_university_or_unknown"


def analyze(rows: list[dict], before: dict) -> dict:
    if len({r["id"] for r in rows}) != len(rows) or len({r["q"] for r in rows}) != len(rows):
        raise ValueError("duplicate query IDs or repeated query phrases")
    rows = sorted(rows, key=lambda r: r["id"])
    seen_urls, seen_hosts = set(before["urls"]), set(before["hosts"])
    candidates, queries, groups = {}, [], {}
    mentions = 0
    for row in rows:
        results = {}
        for ordinal, item in enumerate(row["results"], 1):
            mentions += 1
            key = url_key(item["url"])
            if not key:
                continue
            label = clean_text(html.unescape(item["title"]))
            results.setdefault(key, {"url_key": key, "source_url": safe_source_url(item["url"]), "title": label[:160],
                "label_truncated": len(label) > 160, "citation_ref": item.get("citation_ref"), "tool_response_ordinal": ordinal})
        keys = set(results)
        hosts = {hostname(key) for key in keys}
        new_keys, new_hosts = keys - seen_urls, hosts - seen_hosts
        query = {k: v for k, v in row.items() if k not in {"raw_response", "results", "error"}}
        if "derived_from" in query:
            query["derived_from"] = {k: v for k, v in query["derived_from"].items() if k != "citation_ref"}
            query["derived_from"]["source_url"] = safe_source_url(query["derived_from"]["source_url"])
        query.update({"returned_url_mentions": len(row["results"]), "unique_url_keys": len(keys), "new_url_keys_vs_baseline": len(keys - before["urls"]),
            "marginal_url_keys": len(new_keys), "marginal_host_keys": len(new_hosts),
            "repeated_share_in_plan_order": round(1 - len(new_keys) / len(keys), 4) if keys else None,
            "result_scope": "all_parseable_urls_returned_by_tool; no complete_SERP_or_fixed_top_K_claim", "result_url_keys": sorted(keys)})
        queries.append(query)
        group = groups.setdefault(row["group"], {"id": row["group"], "label": row["group_label"], "queries": 0,
            "marginal_url_keys": 0, "marginal_host_keys": 0, "keys": set()})
        group["queries"] += 1
        group["marginal_url_keys"] += len(new_keys)
        group["marginal_host_keys"] += len(new_hosts)
        group["keys"].update(keys)
        for key, result in results.items():
            if key not in candidates:
                host = hostname(key)
                text = result["title"] + " " + key
                topic = bool(TOPIC.search(text))
                noise = bool(NOISE.search(text))
                restricted = bool(PRIVATE.search(key)) or bool(re.search(r"(?:^|/)sso(?:/|$)", urlsplit(key).path, re.I))
                status = "existing_collection_original" if key in before["originals"] else "existing_metadata_reference" if key in before["metadata"] else "not_collected_in_keyword_search"
                candidates[key] = {**result, "host_key": host, "query_ids": [], "new_url_vs_baseline": key not in before["urls"],
                    "new_host_vs_baseline": host not in before["hosts"], "scope_hint": scope_hint(host),
                    "role_hint": role_for(key, result["title"]), "classification": "keyword_hint_unreviewed",
                    "topic_keyword_match": topic, "noise_keyword_match": noise, "restricted_path_hint": restricted,
                    "original_status": status, "capture_mode": "search_metadata_only", "official_status": "not_verified", "program_id": None,
                    "priority_score": int(topic) * 4 + int(host not in before["hosts"]) * 2 + int(scope_hint(host) == "postech") * 2 - int(noise) * 6 - int(restricted) * 20}
            candidates[key]["query_ids"].append(row["id"])
        seen_urls.update(keys)
        seen_hosts.update(hosts)
    for group in groups.values():
        group["unique_url_keys"] = len(group.pop("keys"))
    ordered = sorted(candidates.values(), key=lambda item: (-item["priority_score"], -len(item["query_ids"]), item["url_key"]))
    actionable = [item for item in ordered if item["topic_keyword_match"] and not item["noise_keyword_match"] and not item["restricted_path_hint"]]
    adaptive = [q for q in queries if q["stage"] == "adaptive"]
    digest = hashlib.sha256(json.dumps(queries, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"schema_version": "keyword-search-1", "run_id": digest, "searched_on": "2026-10-13", "provider": "OpenAI web search",
        "unit": "search URL metadata, not scholarship programs or collected originals", "baseline": before["scope"],
        "counts": {"planned_queries": 100, "executed_queries": sum(q["status"] in {"executed", "no_parseable_results"} for q in queries),
            "failed_queries": sum(q["status"] == "failed" for q in queries), "initial_queries": sum(q["stage"] == "initial" for q in queries),
            "adaptive_queries": len(adaptive), "returned_url_mentions": mentions, "unique_url_keys": len(candidates),
            "new_url_keys_vs_baseline": sum(c["new_url_vs_baseline"] for c in candidates.values()),
            "host_keys": len({c["host_key"] for c in candidates.values()}), "new_host_keys_vs_baseline": len({c["host_key"] for c in candidates.values() if c["new_host_vs_baseline"]}),
            "topic_keyword_candidates": len(actionable), "candidate_new_host_keys": len({c["host_key"] for c in actionable if c["new_host_vs_baseline"]}),
            "noise_keyword_candidates": sum(c["noise_keyword_match"] for c in candidates.values()),
            "restricted_path_candidates": sum(c["restricted_path_hint"] for c in candidates.values()),
            "adaptive_marginal_urls": sum(q["marginal_url_keys"] for q in adaptive), "verified_programs": None, "new_originals_fetched_by_this_module": 0},
        "groups": list(groups.values()), "queries": queries, "candidates": ordered,
        "crawl_queue": [{"url": c["source_url"], "query_ids": c["query_ids"], "role_hint": c["role_hint"],
            "scope_hint": c["scope_hint"], "status": "awaiting_original_and_access_policy_review"} for c in actionable],
        "stop_review": {"coverage_complete": False, "saturation_confirmed": False, "reason": "fixed_100_query_batch_finished; original_review_and_program_resolution_pending",
            "operational_proposal": "after this batch, review after two comparable 10-query waves yield no verified new program or required official document, with pending reviews cleared"}}


def save_database(path: Path, summary: dict) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS keyword_run(id TEXT PRIMARY KEY, summary_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS search_query(run_id TEXT, query_id TEXT, payload_json TEXT, PRIMARY KEY(run_id, query_id));
        CREATE TABLE IF NOT EXISTS search_result(run_id TEXT, query_id TEXT, url_key TEXT, PRIMARY KEY(run_id, query_id, url_key));
        CREATE TABLE IF NOT EXISTS url_candidate(url_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
        """)
        conn.execute("INSERT OR REPLACE INTO keyword_run VALUES (?, ?)", (summary["run_id"], json.dumps(summary["counts"])))
        for query in summary["queries"]:
            conn.execute("INSERT OR REPLACE INTO search_query VALUES (?, ?, ?)", (summary["run_id"], query["id"], json.dumps(query, ensure_ascii=False)))
            for key in query["result_url_keys"]:
                conn.execute("INSERT OR IGNORE INTO search_result VALUES (?, ?, ?)", (summary["run_id"], query["id"], key))
        for candidate in summary["candidates"]:
            conn.execute("INSERT OR REPLACE INTO url_candidate VALUES (?, ?)", (candidate["url_key"], json.dumps(candidate, ensure_ascii=False)))


def main() -> int:
    parser = argparse.ArgumentParser(description="실제 키워드 검색 결과의 중복, 추가 발견량과 파생 근거를 집계")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--collection", type=Path, default=ROOT.parent / "web/public/discovery-collection.json")
    parser.add_argument("--catalog-db", type=Path, default=ROOT / "data/discovery/2026-10-13-demo/collection.sqlite")
    parser.add_argument("--references", type=Path, default=ROOT.parents[3] / "tmp/2026-10-13-benefitlens-national-global-originals.md")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--public-output", type=Path)
    args = parser.parse_args()
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(args.input.glob("q[0-9][0-9][0-9].json"))]
    if len(rows) != 100:
        raise ValueError(f"expected 100 query records, got {len(rows)}")
    supplements = [("registered_source_configuration", ROOT / "config/benefitlens_sources.json"),
        ("previous_collection_export", ROOT / "data/exports/collection_summary.json"),
        ("writing_reference_catalog", ROOT.parent / "web/lib/writing-resources.json")]
    summary = analyze(rows, baseline(args.collection, args.catalog_db, args.references, supplements))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    save_database(args.output / "keyword.sqlite", summary)
    if args.public_output:
        public = {k: v for k, v in summary.items() if k != "crawl_queue"}
        public["candidates"] = [{k: v for k, v in item.items() if k != "citation_ref"} for item in summary["candidates"]]
        args.public_output.parent.mkdir(parents=True, exist_ok=True)
        args.public_output.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"counts": summary["counts"], "groups": summary["groups"], "baseline": summary["baseline"]}, ensure_ascii=False, indent=2))
    return 0 if summary["counts"]["executed_queries"] == 100 else 1


if __name__ == "__main__":
    raise SystemExit(main())
