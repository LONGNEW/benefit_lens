"""Bounded source discovery. Search candidates are inputs, not an invented search API."""
from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import io
import json
import re
import sqlite3
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from .http import SafeHttpClient, document_kind

ROOT = Path(__file__).resolve().parents[2]
RELEVANT = re.compile(r"장학|장학생|지원|모집|선발|공지|공고|양식|자료실|수기|후기|인터뷰|scholar|fellow|funding|application|guide|program|faq|download|stifFront|NewsNotice", re.I)
PRIVATE = re.compile(r"login|logout|sign.?in|register|mypage|mybiz|password|member|privacy|umlg|facebook|youtube|instagram", re.I)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: str) -> str:
    return " ".join(value.replace("\u00b7", ", ").split())


def canonical_url(value: str, parent: str | None = None) -> str | None:
    value = urljoin(parent, value) if parent else value.strip()
    try:
        p = urlsplit(value)
        if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
            return None
    except ValueError:
        return None
    pairs = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"ncid", "trk", "fbclid", "gclid", "jsessionid"}]
    path = re.sub(r";jsessionid=[^/;]+", "", p.path, flags=re.I)
    return urlunsplit(("https", p.hostname.lower(), path or "/", urlencode(sorted(pairs)), ""))


def host_key(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.").lower()


def robots_allowed(text: str, url: str, agent: str) -> tuple[bool, float]:
    groups: list[dict] = []
    group = {"agents": [], "rules": [], "delay": 0.0}
    directives = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (v.strip() for v in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if directives:
                groups.append(group)
                group = {"agents": [], "rules": [], "delay": 0.0}
                directives = False
            group["agents"].append(value.lower())
        elif key in {"allow", "disallow"} and group["agents"]:
            directives = True
            if value:
                group["rules"].append((key == "allow", value))
        elif key == "crawl-delay" and group["agents"]:
            directives = True
            try:
                group["delay"] = max(0.0, float(value))
            except ValueError:
                pass
    groups.append(group)
    specific = [g for g in groups if any(a != "*" and a in agent.lower() for a in g["agents"])]
    selected = specific or [g for g in groups if "*" in g["agents"]]
    target = urlsplit(url).path + ("?" + urlsplit(url).query if urlsplit(url).query else "")
    matches = []
    for g in selected:
        for allowed, pattern in g["rules"]:
            end = pattern.endswith("$")
            core = pattern[:-1] if end else pattern
            regex = "^" + ".*".join(re.escape(p) for p in core.split("*")) + ("$" if end else "")
            if re.search(regex, target):
                matches.append((len(core.replace("*", "")), allowed))
    return (max(matches)[1] if matches else True), max((g["delay"] for g in selected), default=0.0)


def role_for(url: str, title: str) -> str:
    text = url + " " + title
    if "stifFront01.do" in url:
        return "experience_listing"
    if re.search(r"인터뷰|수기|후기|stifFront01View|application experience", text, re.I):
        return "experience"
    if re.search(r"graduate-fellowships/(?:20\d\d|archive)|nv-grad-fellow/modal|recipients|선정자|합격자", text, re.I):
        return "recipient_profile"
    if re.search(r"\.pdf|\.hwp|download|양식|가이드|guideline|faq|규정|자료실", text, re.I):
        return "guideline_or_form"
    if re.search(r"NewsNoticeFront01\.do|selectNoticeAndApplicationList|scholar_schedule(?:&|$)|selectNewsList|공지사항", text, re.I):
        return "listing"
    if re.search(r"모집|선발|fellowship|장학생", text, re.I):
        return "notice_candidate"
    if urlsplit(url).path in ("/", "/homepage/index.do", "/main.nkf"):
        return "institution"
    return "unclassified"


def page_data(body: bytes, url: str) -> tuple[str, list[dict]]:
    try:
        decoded = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = body.decode("cp949", errors="replace")
    soup = BeautifulSoup(decoded, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    title = clean_text(title_tag.get_text(" ", strip=True)) if title_tag else url
    # Resolve this site's public form navigation only from its declared HTML action.
    view_action = re.search(r"action\s*:\s*['\"]([^'\"]*stifFront01View\.do[^'\"]*)['\"]", decoded)
    links = []
    seen = set()
    for tag in soup.find_all(["a", "button"]):
        href = tag.get("href", "")
        if not href or href.lower().startswith("javascript:"):
            match = re.search(r"(?:location(?:\.href)?\s*=|window\.open\()\s*['\"]([^'\"]+)['\"]", tag.get("onclick", "") + " " + href)
            href = match[1] if match else ""
            sequence = re.fullmatch(r"listView\((\d+)\)\s*;?", tag.get("onclick", "").strip())
            if not href and sequence and view_action and host_key(url) == "iljufoundation.org":
                action = canonical_url(view_action[1], url)
                if action:
                    p = urlsplit(action)
                    params = dict(parse_qsl(p.query))
                    params.update({"contentNo": sequence[1], "screenFlag": "Y"})
                    href = urlunsplit((p.scheme, p.netloc, p.path, urlencode(params), ""))
        label_tag = tag.select_one(".title, h2, h3, h4")
        label = clean_text(label_tag.get_text(" ", strip=True) if label_tag else tag.get("aria-label") or tag.get_text(" ", strip=True))[:160]
        target = canonical_url(href, url) if href and not href.startswith("#") else None
        if target and target != url and not PRIVATE.search(target + " " + label) and RELEVANT.search(target + " " + label) and target not in seen:
            seen.add(target)
            links.append({"url": target, "label": label or target})
    # Preserve body text only internally for official documents, never in the public demo.
    for tag in soup(["script", "style", "noscript", "nav", "footer"]):
        tag.decompose()
    return title, links


class Crawler:
    def __init__(self, config: dict, client=None, sleep=time.sleep):
        self.config, self.sleep = config, sleep
        limits = config["limits"]
        self.client = client or SafeHttpClient(allowed_hosts=set(config["allowed_hosts"]), user_agent=config["user_agent"],
            timeout_seconds=limits["timeout_seconds"], max_body_bytes=limits["max_body_bytes"])
        self.robots: dict[str, dict] = {}
        self.last_request: dict[str, float] = {}
        self.request_log: list[dict] = []
        self.request_counts = Counter()

    def _guard_page(self, url: str) -> None:
        if PRIVATE.search(url):
            raise RuntimeError("authentication_path_not_fetched")
        origin = "https://" + urlsplit(url).netloc
        if origin not in self.robots:
            try:
                r = self.client.get(origin + "/robots.txt")
                self.robots[origin] = {"url": origin + "/robots.txt", "status": "checked", "checked_at": now(), "text": r.body.decode("utf-8", "replace"), "sha256": r.sha256}
            except Exception as e:
                status = "absent_404" if "HTTP 404 " in str(e) else "unavailable"
                self.robots[origin] = {"url": origin + "/robots.txt", "status": status, "checked_at": now(), "text": "", "error": str(e)[:180]}
        policy = self.robots[origin]
        if policy["status"] == "unavailable":
            raise RuntimeError("robots policy unavailable; page not fetched")
        allowed, robots_delay = robots_allowed(policy["text"], url, self.config["user_agent"])
        if not allowed:
            raise RuntimeError("robots_disallowed")
        host = urlsplit(url).hostname
        if len(self.request_log) >= self.config["limits"]["max_page_fetches"] or self.request_counts[host] >= self.config["limits"]["max_pages_per_host"]:
            raise RuntimeError("request_budget_reached")
        delay = max(self.config["limits"]["delay_seconds"], robots_delay)
        wait = delay - (time.monotonic() - self.last_request.get(origin, 0))
        if wait > 0:
            self.sleep(wait)
        self.last_request[origin] = time.monotonic()
        self.request_counts[host] += 1
        self.request_log.append({"url": url, "requested_at": now(), "response_status": None, "redirect_to": None})

    def _observe(self, url: str, status: int, location: str | None) -> None:
        for request in reversed(self.request_log):
            if request["url"] == url and request["response_status"] is None:
                request["response_status"] = status
                request["redirect_to"] = canonical_url(location, url) if location else None
                break

    def fetch(self, url: str):
        # A redirect destination is checked before contact, including its robots rules.
        return self.client.get(url, request_guard=self._guard_page, response_observer=self._observe)

    def crawl(self, seeds: list[dict], output: Path) -> dict:
        limits = self.config["limits"]
        queue = [(0, -1, i, seed, seed["url"], None, seed["label"]) for i, seed in enumerate(seeds)]
        heapq.heapify(queue)
        ordinal = len(queue)
        documents: dict[str, dict] = {}
        page_links: dict[str, list[dict]] = {}
        edges, deferred = [], []
        counts = Counter()
        fetched = 0
        queued = set((s["seed_id"], s["url"]) for s in seeds)
        def enqueue(seed, links, parent, depth):
            nonlocal ordinal
            if depth >= limits["max_depth"]:
                return
            for link in links:
                key = (seed["seed_id"], link["url"])
                if key not in queued:
                    queued.add(key)
                    role = role_for(link["url"], link["label"])
                    priority = {"experience": 0, "guideline_or_form": 0, "notice_candidate": 1, "experience_listing": 1, "recipient_profile": 2, "listing": 2}.get(role, 3)
                    heapq.heappush(queue, (depth + 1, priority, ordinal, seed, link["url"], parent, link["label"]))
                    ordinal += 1
        while queue:
            depth, _, _, seed, url, parent, label = heapq.heappop(queue)
            edge = {"seed_id": seed["seed_id"], "route": seed["route"], "url": url, "parent_url": parent,
                "label": label, "depth": depth, "query": seed.get("query"), "catalog_row": seed.get("catalog_row")}
            edges.append(edge)
            if url in documents:
                edge["status"] = "reused_in_run"
                edge["document_id"] = documents[url]["id"]
                enqueue(seed, page_links.get(url, []), documents[url]["final_url"], depth)
                continue
            host = urlsplit(url).hostname
            if host not in self.config["allowed_hosts"]:
                edge["status"] = "external_candidate_not_fetched"
                deferred.append(edge.copy())
                continue
            if fetched >= limits["max_page_fetches"] or counts[host] >= limits["max_pages_per_host"]:
                edge["status"] = "visit_limit"
                deferred.append(edge.copy())
                continue
            fetched += 1
            counts[host] += 1
            try:
                r = self.fetch(url)
                final = canonical_url(r.final_url) or url
                kind = document_kind(r.body, r.content_type, final, r.headers)
                title, links = page_data(r.body, final) if kind == "html" else (label, [])
                role = role_for(final, label + " " + title)
                metadata_only = role in {"experience", "experience_listing", "recipient_profile"} or (host_key(final) == "iljufoundation.org" and urlsplit(final).path == "/homepage/index.do")
                if role == "experience" and label:
                    title = label
                metadata_hash = hashlib.sha256(json.dumps({"url": final, "title": title, "role": role, "kind": kind}, sort_keys=True).encode()).hexdigest() if metadata_only else None
                document_id = hashlib.sha256((final + "\n" + (metadata_hash or r.sha256)).encode()).hexdigest()
                doc = {"id": document_id, "url": url, "final_url": final, "title": title, "kind": kind, "role": role,
                    "classification_method": "keyword_rules", "classification_review": "pending" if role == "unclassified" else "candidate",
                    "sha256": None if metadata_only else r.sha256, "metadata_sha256": metadata_hash, "bytes": len(r.body), "collected_at": now(), "status": "collected",
                    "capture_mode": "metadata_only" if metadata_only else "internal_research",
                    "rights_status": "review_required", "eligibility_status": "not_evaluated", "publication_status": "not_promoted",
                    "language": seed.get("language", "ko"), "source_country": seed.get("source_country", "KR"),
                    "round_status": "not_resolved", "raw_path": None}
                if not metadata_only:
                    raw = output / "raw" / (r.sha256 + "." + (kind if kind in {"html", "pdf", "hwp", "hwpx", "image"} else "bin"))
                    raw.parent.mkdir(parents=True, exist_ok=True)
                    if not raw.exists():
                        raw.write_bytes(r.body)
                    doc["raw_path"] = str(raw.relative_to(output))
                documents[url] = doc
                documents[final] = doc
                page_links[url] = page_links[final] = links
                edge["status"] = "collected"
                edge["document_id"] = doc["id"]
                enqueue(seed, links, final, depth)
            except Exception as e:
                edge["status"] = "robots_blocked" if "robots" in str(e) else "visit_limit" if "request_budget" in str(e) else "access_restricted" if "authentication_path" in str(e) else "fetch_failed"
                edge["error"] = str(e)[:240]
        return {"documents": list({doc["id"]: doc for doc in documents.values()}.values()), "edges": edges, "deferred": deferred,
            "page_fetch_attempts": fetched, "attempts_by_host": dict(counts), "page_http_requests": len(self.request_log),
            "request_log": self.request_log, "robots": list(self.robots.values())}


def catalog_seed_rows(rows: list[dict], operators: list[str]) -> list[dict]:
    candidates = {}
    for row in rows:
        if row.get("운영기관명", "").strip() not in operators:
            continue
        url = canonical_url(row.get("홈페이지 주소", "").strip())
        if not url:
            continue
        host = host_key(url)
        graduate = "대학원" in row.get("대학구분", "")
        if host not in candidates or graduate:
            candidates[host] = {"seed_id": "catalog-" + row["번호"].strip(), "route": "catalog", "url": url,
                "label": row["운영기관명"].strip(), "catalog_row": row["번호"].strip(), "catalog_product": row["상품명"].strip(),
                "language": "ko", "source_country": "KR"}
    return list(candidates.values())


def collect_catalog(crawler: Crawler, config: dict, output: Path) -> tuple[dict, list[dict]]:
    catalog = config["catalog"]
    page = crawler.fetch(catalog["page_url"])
    if "이용허락범위 제한 없음" not in BeautifulSoup(page.body, "html.parser").get_text(" ", strip=True):
        raise RuntimeError("catalog reuse scope needs review")
    metadata = json.loads(crawler.fetch(catalog["metadata_url"]).body)
    params = {"atchFileId": metadata["atchFileId"], "fileDetailSn": metadata["fileDetailSn"]}
    gate_url = "https://www.data.go.kr/cmm/cmm/check-limit.json"
    crawler.client._validate_url(gate_url)
    crawler._guard_page(gate_url)
    gate = requests.post(gate_url, data=params, headers={"User-Agent": config["user_agent"]},
        timeout=config["limits"]["timeout_seconds"], allow_redirects=False)
    gate.raise_for_status()
    crawler._observe(gate_url, gate.status_code, None)
    if gate.json().get("needCaptcha") is not False:
        raise RuntimeError("catalog CAPTCHA required; download not attempted")
    params["dataNm"] = metadata["fileDataRegistVO"]["dataNm"]
    download_url = "https://www.data.go.kr/cmm/cmm/fileDownload.do?" + urlencode(params)
    fetched = crawler.fetch(download_url)
    if "text/html" in (fetched.content_type or "") or b"<!DOCTYPE" in fetched.body[:100]:
        raise RuntimeError("catalog returned a page instead of CSV")
    text = None
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = fetched.body.decode(encoding)
            break
        except UnicodeDecodeError:
            pass
    if text is None:
        raise RuntimeError("unsupported catalog encoding")
    rows = [{k: v.strip() if isinstance(v, str) else v for k, v in row.items()}
        for row in csv.DictReader(io.StringIO(text))]
    if not rows or not {"운영기관명", "상품명", "홈페이지 주소"}.issubset(rows[0]):
        raise RuntimeError("catalog columns changed")
    output.mkdir(parents=True, exist_ok=True)
    raw = output / "raw" / (fetched.sha256 + ".csv")
    raw.parent.mkdir(parents=True, exist_ok=True)
    if not raw.exists():
        raw.write_bytes(fetched.body)
    return {"status": "collected", "page_url": catalog["page_url"], "download_url": download_url,
        "title": params["dataNm"], "sha256": fetched.sha256, "raw_path": str(raw.relative_to(output)),
        "row_count": len(rows), "operator_names": len({r["운영기관명"] for r in rows}),
        "graduate_rows": sum("대학원" in r.get("대학구분", "") for r in rows),
        "rights_status": "catalog_unrestricted_as_stated", "collected_at": now()}, rows


def save_database(path: Path, run: dict, catalog_rows: list[dict]) -> dict:
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS discovery_run(id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, manifest_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS catalog_item(id TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS document_version(id TEXT PRIMARY KEY, url TEXT, sha256 TEXT, payload_json TEXT NOT NULL, UNIQUE(url, sha256));
        CREATE TABLE IF NOT EXISTS discovery_edge(run_id TEXT, ordinal INTEGER, payload_json TEXT NOT NULL, PRIMARY KEY(run_id, ordinal));
        """)
        before = conn.execute("SELECT COUNT(*) FROM document_version").fetchone()[0]
        for row in catalog_rows:
            payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
            key = hashlib.sha256((run["catalog"]["sha256"] + payload).encode()).hexdigest()
            conn.execute("INSERT OR IGNORE INTO catalog_item VALUES (?, ?)", (key, payload))
        for doc in run["documents"]:
            exists = conn.execute("SELECT 1 FROM document_version WHERE id=?", (doc["id"],)).fetchone()
            doc["database_change"] = "reused_version" if exists else "new_version"
            conn.execute("INSERT OR IGNORE INTO document_version VALUES (?, ?, ?, ?)",
                (doc["id"], doc["final_url"], doc["sha256"], json.dumps(doc, ensure_ascii=False)))
        for ordinal, edge in enumerate(run["edges"]):
            conn.execute("INSERT INTO discovery_edge VALUES (?, ?, ?)", (run["run"]["id"], ordinal, json.dumps(edge, ensure_ascii=False)))
        conn.execute("INSERT INTO discovery_run VALUES (?, ?, ?, ?)", (run["run"]["id"], run["run"]["started_at"], run["run"]["finished_at"], json.dumps(run, ensure_ascii=False)))
        after = conn.execute("SELECT COUNT(*) FROM document_version").fetchone()[0]
        return {"path": str(path), "new_versions": after - before, "total_document_versions": after,
            "catalog_items": conn.execute("SELECT COUNT(*) FROM catalog_item").fetchone()[0]}


def main() -> int:
    parser = argparse.ArgumentParser(description="공개 색인과 검색 후보로 시작하는 제한 탐색 수집")
    parser.add_argument("--config", type=Path, default=ROOT / "config/discovery_demo.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--demo-output", type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid.uuid4())
    started = now()
    baseline = json.loads((ROOT / "config/benefitlens_sources.json").read_text(encoding="utf-8"))
    registered = sorted({host_key(s["url"]) for s in baseline["sources"]})
    crawler = Crawler(config)
    rows = []
    try:
        catalog, rows = collect_catalog(crawler, config, output)
        seeds = catalog_seed_rows(rows, config["catalog"]["operators_to_visit"])
    except Exception as e:
        catalog = {"status": "failed", "page_url": config["catalog"]["page_url"], "error": str(e)[:240]}
        seeds = []
    for i, candidate in enumerate(config["search_batch"]["candidates"]):
        url = canonical_url(candidate["url"])
        if url:
            seeds.append({**candidate, "url": url, "seed_id": f"search-{i+1}", "route": "search"})
    for seed in seeds:
        seed["new_to_registry"] = host_key(seed["url"]) not in registered
    result = crawler.crawl(seeds, output)
    collected_hosts = {host_key(d["final_url"]) for d in result["documents"]}
    manifest = {"schema_version": "discovery-1", "run": {"id": run_id, "started_at": started, "finished_at": now(),
        "limits": config["limits"], "registered_hosts_before_run": registered, "status": "completed_with_scope_limits",
        "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest()},
        "search_batch": config["search_batch"], "search_dictionary": config["search_dictionary"],
        "catalog": catalog, "seeds": seeds, **result}
    manifest["counts"] = {"catalog_rows": len(rows), "selected_seeds": len(seeds), "collected_urls": len(result["documents"]),
        "unique_content_hashes": len({d["sha256"] for d in result["documents"] if d["sha256"]}),
        "stored_raw_documents": sum(d["capture_mode"] != "metadata_only" for d in result["documents"]),
        "metadata_only_documents": sum(d["capture_mode"] == "metadata_only" for d in result["documents"]), "collected_host_keys": len(collected_hosts),
        "new_host_keys": len(collected_hosts - set(registered)), "roles": dict(Counter(d["role"] for d in result["documents"])),
        "edge_statuses": dict(Counter(e["status"] for e in result["edges"]))}
    manifest["database"] = save_database(output / "collection.sqlite", manifest, rows)
    target = output / (run_id + ".json")
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.demo_output and rows and result["documents"]:
        public = {k: v for k, v in manifest.items() if k not in {"robots", "deferred", "database"}}
        public["catalog"] = {k: v for k, v in manifest["catalog"].items() if k != "raw_path"}
        public["access_policies"] = [{k: v for k, v in policy.items() if k not in {"text", "error"}} for policy in manifest["robots"]]
        public["documents"] = [{k: v for k, v in d.items() if k != "raw_path"} for d in manifest["documents"]]
        args.demo_output.parent.mkdir(parents=True, exist_ok=True)
        args.demo_output.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(target), "counts": manifest["counts"], "database": manifest["database"]}, ensure_ascii=False, indent=2))
    return 0 if rows and result["documents"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
