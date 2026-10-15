"""High-recall support discovery; no issuer allowlist or user eligibility gate.

Catalog records, full publications and URL-only leads remain distinguishable.
Nothing in this module promotes a record into the evaluated opportunity table.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import re
import sqlite3
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

from . import db
from .discovery import canonical_url, clean_text
from .http import SafeHttpClient
from .original_collection import AUTH, NON_DOCUMENT_ASSET

ROOT = Path(__file__).resolve().parents[2]
AUTH_PATH = re.compile(r"(?:^|/)(?:login|logout|sign[-_]?in|sso|mypage|password|register|account)(?:[/\.]|$)",re.I)
HINTS = {
    "scholarship": r"장학|scholarship|studentship|bursar|beasiswa|becas|bourse|stipendium|奨学|奖学",
    "research_funding": r"연구|research|fellowship|펠로십|grant|연수|科研",
    "tuition_learning": r"교육|학습|수업|등록금|훈련|tuition|education|training|course|learning|学費",
    "livelihood_housing": r"생활비|생계|주거|월세|식비|living|subsistence|maintenance|housing|stipend|生活|助成",
    "cash_support": r"지원금|수당|현금|상금|장려금|allowance|cash|award|bénéfice|subsid|benefit",
    "loan": r"대출|loan",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(value: str | bytes):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def plain(value):
    if value is None:
        return ""
    if not re.search(r"<[A-Za-z!/]", str(value)):
        return "\n".join(clean_text(html.unescape(line)) for line in str(value).splitlines() if clean_text(line))
    soup = BeautifulSoup(str(value), "html.parser")
    for node in soup.select("script,style,noscript"):
        node.decompose()
    return "\n".join(clean_text(line) for line in soup.get_text("\n", strip=True).splitlines() if clean_text(line))


def normalize_url(value, parent=None):
    url = canonical_url(value, parent)
    if not url:
        return None
    p = urlsplit(url)
    if AUTH_PATH.search(p.path) or NON_DOCUMENT_ASSET.search(p.path):
        return None
    # Keep record IDs, rounds and query distinctions. Do not collapse by title.
    return url


def funding_hints(text):
    return [name for name, pattern in HINTS.items() if re.search(pattern, text, re.I)]


def postgres_safe(value):
    """PostgreSQL text cannot represent NUL; raw evidence remains unchanged."""
    if isinstance(value,str):
        return value.replace("\x00","\ufffd").encode("utf-8",errors="replace").decode("utf-8")
    if isinstance(value,dict):
        return {postgres_safe(k):postgres_safe(v) for k,v in value.items()}
    if isinstance(value,list):
        return [postgres_safe(v) for v in value]
    if isinstance(value,tuple):
        return tuple(postgres_safe(v) for v in value)
    return value


def make_record(*, url, title, family, record_type, capture_status,
                listing_url, source_id=None, title_basis="observed_title",
                issuer_name=None, issuer_kind="unknown", country=None,
                text="", payload=None, evidence_sha256=None, identity=None):
    url = normalize_url(url)
    listing_url = normalize_url(listing_url)
    title = clean_text(html.unescape(str(title or "")))
    if not url or not listing_url or not title:
        return None
    return {
        "candidate_key": "support:" + digest(identity or url), "source_url": url,
        "title": title, "title_basis": title_basis, "source_family": family,
        "source_record_id": str(source_id) if source_id is not None else None,
        "record_type": record_type, "capture_status": capture_status,
        "issuer_name": issuer_name, "issuer_kind": issuer_kind,
        "source_country": country, "funding_hints": funding_hints(title + "\n" + text),
        "detail_text": text, "source_payload": payload or {},
        "listing_url": listing_url, "evidence_sha256": evidence_sha256,
        "observed_at": utc_now(), "rights_status": "review_required",
        "eligibility_status": "not_evaluated",
    }


class Store:
    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "raw").mkdir(exist_ok=True)
        self.path = folder / "collection.sqlite"
        self.lock = threading.RLock()
        with self.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS record(candidate_key TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS observation(observation_key TEXT PRIMARY KEY, candidate_key TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fetch(request_key TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS source_run(source_family TEXT PRIMARY KEY, payload TEXT NOT NULL);
            """)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def put(self, record):
        self.put_many([record])

    def put_many(self, records):
        with self.lock, self.connect() as conn:
            for record in records:
                if record is None:
                    continue
                key = record["candidate_key"]
                observation_key = digest(key + "\n" + record["source_family"] + "\n" + record["listing_url"])
                existing = conn.execute("SELECT payload FROM record WHERE candidate_key=?", (key,)).fetchone()
                value = record
                if existing:
                    old = json.loads(existing[0])
                    rank = lambda r: (r["title_basis"] == "observed_title", len(r.get("detail_text", "")))
                    if rank(old) > rank(record):
                        value = old
                conn.execute("INSERT OR REPLACE INTO record VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))
                conn.execute("INSERT OR REPLACE INTO observation VALUES (?,?,?)", (observation_key, key, json.dumps(record, ensure_ascii=False)))

    def status(self, family, **values):
        with self.lock, self.connect() as conn:
            previous=conn.execute("SELECT payload FROM source_run WHERE source_family=?",(family,)).fetchone()
            payload={**(json.loads(previous[0]) if previous else {}),"family":family,"updated_at":utc_now(),**values}
            if values.get("status")!="failed" and "error" not in values:payload.pop("error",None)
            conn.execute("INSERT OR REPLACE INTO source_run VALUES (?,?)", (family, json.dumps(payload, ensure_ascii=False)))
        print(json.dumps(payload, ensure_ascii=False), flush=True)

    def records(self):
        return list(self.iter_records())

    def iter_records(self):
        with self.connect() as conn:
            for row in conn.execute("SELECT payload FROM record ORDER BY candidate_key"):
                yield json.loads(row[0])

    def export(self):
        columns = ["candidate_key", "title", "source_family", "source_url", "record_type", "capture_status", "issuer_name", "issuer_kind", "source_country", "title_basis", "listing_url", "rights_status", "eligibility_status"]
        token=uuid.uuid4().hex
        json_path=self.folder/("records."+token+".next")
        csv_path=self.folder/("catalog."+token+".next")
        groups={name:Counter() for name in ("source_families","record_types","capture_statuses","title_bases","source_countries","funding_hints","title_funding_hints")}
        with self.connect() as local:
            catalog_families={"kosaf_catalog","dreamspon","youthcenter_policies","jasso","uq","unsw","ubc"}
            catalog_keys={row[0] for row in local.execute("SELECT DISTINCT candidate_key FROM observation WHERE json_extract(payload,'$.source_family') IN ("+','.join('?' for _ in catalog_families)+")",tuple(catalog_families))}
        catalog_path=self.folder/("support-catalog."+token+".next")
        urls=set()
        count=with_text=0
        catalog_count=0
        with json_path.open("w",encoding="utf-8") as out, csv_path.open("w",encoding="utf-8-sig",newline="") as csv_out, catalog_path.open("w",encoding="utf-8-sig",newline="") as selected_out:
            writer = csv.DictWriter(csv_out, fieldnames=columns, extrasaction="ignore")
            selected_writer=csv.DictWriter(selected_out,fieldnames=columns,extrasaction="ignore")
            writer.writeheader()
            selected_writer.writeheader()
            for record in self.iter_records():
                out.write(json.dumps(record,ensure_ascii=False)+"\n")
                writer.writerow(record)
                if record["candidate_key"] in catalog_keys:
                    selected_writer.writerow(record)
                    catalog_count+=1
                count+=1
                with_text+=bool(record.get("detail_text"))
                urls.add(record["source_url"])
                for name,field in (("source_families","source_family"),("record_types","record_type"),("capture_statuses","capture_status"),("title_bases","title_basis")):
                    groups[name][record[field]]+=1
                groups["source_countries"][record.get("source_country") or "unknown"]+=1
                groups["funding_hints"].update(record["funding_hints"])
                groups["title_funding_hints"].update(funding_hints(record["title"]))
        json_path.replace(self.folder/"records.jsonl")
        csv_path.replace(self.folder/"catalog.csv")
        catalog_path.replace(self.folder/"support-catalog.csv")
        with self.connect() as conn:
            observations = conn.execute("SELECT count(*) FROM observation").fetchone()[0]
            fetches = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM fetch")]
            sources = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM source_run")]
        manifest = {
            "schema_version": "broad-support-1", "updated_at": utc_now(),
            "counts": {
                "unique_source_records": count, "unique_source_urls": len(urls), "observations": observations,
                "support_catalog_entries":catalog_count,
                **{name:dict(counter) for name,counter in groups.items()},
                "records_with_detail_text": with_text,
                "fetches": len(fetches),
                "successful_fetches": sum(f.get("status") == "received" for f in fetches),
                "failed_fetches": sum(f.get("status") == "failed" for f in fetches),
            },
            "sources": sources,
            "count_unit": "source catalog IDs or normalized source URLs; not semantically deduplicated programs or eligible applications",
            "issuer_allowlist_required": False, "personal_eligibility_applied": False,
            "previous_opportunity_registry_modified": False,
        }
        (self.folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest


class PublicFetcher:
    def __init__(self, store: Store, delay=1.0):
        self.store, self.delay = store, delay
        self.lock = threading.Lock()
        self.host_locks, self.last = {}, {}
        self.robots_checked = set()

    def _pace(self, url):
        host = urlsplit(url).hostname
        with self.lock:
            lock = self.host_locks.setdefault(host, threading.Lock())
        with lock:
            delay = max(0, self.last.get(host, 0) + self.delay - time.monotonic())
            if delay:
                time.sleep(delay)
            self.last[host] = time.monotonic()

    def fetch(self, url, *, payload=None, form=None, retry_failed=False):
        url = normalize_url(url)
        if not url:
            raise ValueError("public HTTPS URL required")
        method = "GET" if payload is None and form is None else "POST"
        request_key = digest(json.dumps([method, url, payload, form], sort_keys=True, ensure_ascii=False))
        with self.store.connect() as conn:
            cached = conn.execute("SELECT payload FROM fetch WHERE request_key=?", (request_key,)).fetchone()
        if cached:
            meta = json.loads(cached[0])
            if meta["status"] == "received":
                return (self.store.folder / meta["raw_path"]).read_bytes(), meta
            transient=bool(re.search(r"ReadTimeout|ConnectTimeout|HTTP 50[234]",meta.get("error","")))
            if not retry_failed and not (transient and meta.get("attempt_count",1)<3):
                raise RuntimeError(meta["error"])
        client = SafeHttpClient(allowed_hosts={urlsplit(url).hostname}, user_agent="BenefitLens-course-public-collector/2.0 (+internal research)", timeout_seconds=35, max_body_bytes=None, allow_public_redirects=True, max_download_seconds=300)
        if urlsplit(url).path != "/robots.txt":
            origin="https://"+urlsplit(url).netloc
            with self.lock:
                check_robots=origin not in self.robots_checked
                self.robots_checked.add(origin)
            if check_robots:
                try:
                    self.fetch(origin+"/robots.txt")
                except Exception:
                    pass
        self._pace(url)
        old_meta=json.loads(cached[0]) if cached else {}
        meta = {"url": url, "method": method, "request_payload": payload or form, "observed_at": utc_now(), "robots_mode":"observe", "auth_bypass":False,"attempt_count":old_meta.get("attempt_count",1)+1 if cached else 1}
        if old_meta.get("error"):meta["previous_error"]=old_meta["error"]
        try:
            if method == "GET":
                response = client.get(url, request_guard=lambda target: self._guard(client, target))
                body = response.body
                meta.update(http_status=response.status_code, final_url=response.final_url, content_type=response.content_type, public_pagination_headers={k.lower():v for k,v in response.headers.items() if k.lower() in {"x-wp-total","x-wp-totalpages"}})
            else:
                # POST is used only for public catalog search/download operations.
                self._guard(client, url)
                response = requests.post(url, json=payload, data=form, headers={"User-Agent": client.user_agent}, timeout=45, allow_redirects=False)
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code} from public catalog endpoint")
                body = response.content
                meta.update(http_status=response.status_code, final_url=url, content_type=response.headers.get("Content-Type"))
            if body.startswith((b"MZ", b"\x7fELF")):
                raise ValueError("non-document executable excluded")
            sha = digest(body)
            path = self.store.folder / "raw" / (sha + ".bin")
            if not path.exists():
                path.write_bytes(body)
            meta.update(status="received", sha256=sha, bytes=len(body), raw_path=str(path.relative_to(self.store.folder)))
        except Exception as error:
            meta.update(status="failed", error=type(error).__name__ + ": " + str(error)[:300])
            with self.store.lock, self.store.connect() as conn:
                conn.execute("INSERT OR REPLACE INTO fetch VALUES (?,?)", (request_key, json.dumps(meta, ensure_ascii=False)))
            raise
        with self.store.lock, self.store.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO fetch VALUES (?,?)", (request_key, json.dumps(meta, ensure_ascii=False)))
        return body, meta

    def _guard(self, client, url):
        if not normalize_url(url):
            raise ValueError("authentication or non-document path excluded")
        client._validate_url(url, allow_unlisted=True)


def import_previous(store):
    folder = ROOT / "data/original_ingestion/2026-10-13"
    old = json.loads((folder / "collection.json").read_text(encoding="utf-8"))
    for result in old["results"]:
        store.put(make_record(url=result["search_url"], title=result["title"], family="previous_search", record_type="search_lead", capture_status="search_metadata", listing_url=result["search_url"], payload={"query_ids": result["query_ids"], "previous_resolution": result["status"]}))
    for doc in old["documents"]:
        if not doc.get("raw_path") or doc["status"] != "collected":
            continue
        text = (folder / doc["text_path"]).read_text(encoding="utf-8") if doc.get("text_path") else ""
        store.put(make_record(url=doc["final_url"], title=doc["title"], family="previous_originals", record_type="document_candidate", capture_status="stored_document", listing_url=doc["url"], issuer_name=(doc.get("primary_provider") or {}).get("name"), issuer_kind=(doc.get("primary_provider") or {}).get("kind", "unknown"), text=text, evidence_sha256=doc["sha256"], payload={"previous_role": doc["role"], "kind": doc["kind"], "previous_raw_path": str(folder / doc["raw_path"]), "extraction_error": doc.get("extraction_error")}))
    store.status("previous_snapshot", status="complete", note="Existing search leads and raw originals retained without issuer gate")


def ingest_postgres(store):
    migration = ROOT / "migrations/019_broad_support_collection.sql"
    expected_keys=set()
    before = None
    with db.connect() as conn:
        baseline = {r[0] for r in conn.execute("SELECT canonical_key FROM benefitlens.latest_candidate_opportunity JOIN benefitlens.opportunity USING(opportunity_id)")}
        conn.execute(migration.read_text(encoding="utf-8"))
        before = conn.execute("SELECT count(*) FROM benefitlens.support_discovery_record").fetchone()[0]
        statement="""
                INSERT INTO benefitlens.support_discovery_record (
                  candidate_key,source_url,title,title_basis,source_family,source_record_id,record_type,capture_status,
                  issuer_name,issuer_kind,source_country,funding_hints,detail_text,payload,first_seen_at,last_seen_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(candidate_key) DO UPDATE SET
                  title=EXCLUDED.title,title_basis=EXCLUDED.title_basis,source_family=EXCLUDED.source_family,
                  source_record_id=EXCLUDED.source_record_id,record_type=EXCLUDED.record_type,capture_status=EXCLUDED.capture_status,
                  issuer_name=EXCLUDED.issuer_name,issuer_kind=EXCLUDED.issuer_kind,source_country=EXCLUDED.source_country,
                  funding_hints=EXCLUDED.funding_hints,detail_text=EXCLUDED.detail_text,payload=EXCLUDED.payload,last_seen_at=EXCLUDED.last_seen_at
                WHERE benefitlens.support_discovery_record.payload IS DISTINCT FROM EXCLUDED.payload
                   OR benefitlens.support_discovery_record.detail_text IS DISTINCT FROM EXCLUDED.detail_text
            """
        conn.commit()
        def values(r):
            expected_keys.add(r["candidate_key"])
            safe=postgres_safe(r)
            return (safe["candidate_key"],safe["source_url"],safe["title"],safe["title_basis"],safe["source_family"],safe.get("source_record_id"),safe["record_type"],safe["capture_status"],safe.get("issuer_name"),safe["issuer_kind"],safe.get("source_country"),Jsonb(safe["funding_hints"]),safe.get("detail_text"),Jsonb({k:v for k,v in safe.items() if k!="detail_text"}),safe["observed_at"],safe["observed_at"])
        with conn.cursor() as cursor:
            batch=[]
            for record in store.iter_records():
                batch.append(values(record))
                if len(batch)>=500:
                    cursor.executemany(statement,batch)
                    conn.commit()
                    batch=[]
                    if len(expected_keys)%10000==0:print(json.dumps({"postgres_records_written":len(expected_keys)}),flush=True)
            if batch:cursor.executemany(statement,batch)
            conn.commit()
            observation_statement="""INSERT INTO benefitlens.support_discovery_observation VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(observation_key) DO UPDATE SET evidence_sha256=EXCLUDED.evidence_sha256,payload=EXCLUDED.payload,observed_at=EXCLUDED.observed_at
                WHERE benefitlens.support_discovery_observation.payload IS DISTINCT FROM EXCLUDED.payload
                   OR benefitlens.support_discovery_observation.evidence_sha256 IS DISTINCT FROM EXCLUDED.evidence_sha256"""
            with store.connect() as local:
                rows=local.execute("SELECT observation_key,payload FROM observation")
                while True:
                    rows_batch=rows.fetchmany(500)
                    if not rows_batch:break
                    batch=[]
                    for key,payload in rows_batch:
                        r=json.loads(payload)
                        if r["candidate_key"] not in expected_keys:continue
                        metadata={k:v for k,v in r.items() if k not in {"detail_text","source_payload"}}
                        safe=postgres_safe(r)
                        batch.append((key,safe["candidate_key"],safe["source_family"],safe["listing_url"],safe.get("evidence_sha256"),Jsonb(postgres_safe(metadata)),safe["observed_at"]))
                    cursor.executemany(observation_statement,batch)
                    conn.commit()
        after = conn.execute("SELECT count(*) FROM benefitlens.support_discovery_record").fetchone()[0]
        actual = {r[0] for r in conn.execute("SELECT candidate_key FROM benefitlens.support_discovery_record")}
        assert expected_keys <= actual
        assert baseline == {r[0] for r in conn.execute("SELECT canonical_key FROM benefitlens.latest_candidate_opportunity JOIN benefitlens.opportunity USING(opportunity_id)")}
        conn.execute("INSERT INTO benefitlens.support_discovery_batch VALUES (%s,%s,%s,%s) ON CONFLICT(batch_id) DO UPDATE SET finished_at=EXCLUDED.finished_at,manifest=EXCLUDED.manifest",("broad-support-"+store.folder.name,utc_now(),utc_now(),Jsonb(json.loads((store.folder/"manifest.json").read_text(encoding="utf-8")))))
    result = {"before": before, "after": after, "new_records": after-before, "source_records_in_snapshot":len(expected_keys), "previous_candidate_count_preserved": len(baseline)}
    (store.folder / "postgres-ingestion.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--import-previous", action="store_true")
    parser.add_argument("--ingest", action="store_true")
    args=parser.parse_args()
    store=Store(args.output)
    if args.import_previous:
        import_previous(store)
    print(json.dumps(store.export()["counts"], ensure_ascii=False), flush=True)
    if args.ingest:
        print(json.dumps(ingest_postgres(store), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
