"""Build and verify successive BenefitLens batches from reviewed input shards."""
from __future__ import annotations

import argparse
from datetime import date, datetime
import fcntl
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from benefitlens_batch_db import read_json, validate_sources

ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "proposal/결과물/NVIDIA_2027-2028"
DB_RUNNER = ROOT / "tools/benefitlens_batch_db.py"
DEFAULT_INCOMING = ROOT / "data/batch/approved"
KEYS = {
    "program": ("program_id",), "opportunity_round": ("program_id", "round_id"),
    "source_document": ("document_id",), "evidence": ("evidence_id",), "claim": ("claim_id",),
    "claim_evidence": ("claim_id", "evidence_id"), "experience_case": ("case_id",),
    "case_document": ("case_id", "document_id", "document_role"), "writing_pattern": ("pattern_id",),
    "pattern_evidence": ("pattern_id", "evidence_id"),
}
OFFICIAL = {"official_notice", "official_guideline"}


def encode(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def read(path):
    return read_json(path)


def sample_rows(sample, as_of, exclude):
    source = read(sample)
    if source["format"] != "benefitlens-public-sample-v1":
        raise ValueError("unsupported sample format")
    for item in source["opportunities"]:
        pid, rid = item["program_id"], item["round_id"]
        if (pid, rid) in exclude:
            continue
        if item["recipient_type"] != "individual" or item["category"] == "research_grant":
            raise ValueError(f"not in individual student scope: {pid}/{rid}")
        info = item["application"]
        opened, closed = info.get("opens_on"), info.get("closes_on")
        # Summary data preserves the archived date, without treating it as a live offer.
        status = "closed" if closed and date.fromisoformat(closed) < as_of else "unknown"
        refs = item.get("source_refs", [])
        url = next((ref["url"] for ref in refs if urlsplit(ref.get("url", "")).scheme in ("http", "https")), info.get("url"))
        if not url or not rid:
            raise ValueError(f"sample row lacks a source or round: {pid}")
        yield "program", {"program_id": pid, "title": item["title"], "issuer": item["issuer"]["name"],
                          "opportunity_type": item["category"]}
        yield "opportunity_round", {"program_id": pid, "round_id": rid, "opportunity_id": item["opportunity_id"],
                                    "notice_version": f"sample-{source['as_of']}", "status": status,
                                    "official_url": url, "application_url": None, "opens_on": opened,
                                    "closes_on": closed, "announces_on": None, "checked_on": source["as_of"]}


def rows_from(directory):
    if not directory.exists():
        return
    for p in sorted(directory.glob("*.json")):
        data = read(p)
        if not isinstance(data, dict) or not data or set(data) - set(KEYS):
            raise ValueError(f"not a table shard: {p}")
        for table, rows in data.items():
            if not isinstance(rows, list):
                raise ValueError(f"expected list for {table}: {p}")
            for row in rows:
                if table == "source_document":
                    row = dict(row)
                    for name in ("raw_path", "text_path"):
                        if row.get(name) and not Path(row[name]).is_absolute():
                            row[name] = str((directory.parent / row[name]).resolve())
                yield table, row


def assemble(sample, as_of, directories):
    merged = {t: {} for t in KEYS}

    def add(table, row):
        key = tuple(row[field] for field in KEYS[table])
        existing = merged[table].get(key)
        if existing is not None and existing != row:
            raise ValueError(f"conflicting input rows for {table} {key}")
        merged[table][key] = row

    # Approved incoming rows replace sample placeholders only for that exact round.
    incoming = [record for d in directories for record in rows_from(d)]
    approved_rounds = {(row["program_id"], row["round_id"]) for t, row in incoming if t == "opportunity_round"}
    approved_programs = {row["program_id"] for t, row in incoming if t == "program"}
    sample_records = sample_rows(sample, as_of, approved_rounds) if sample is not None else []
    for table, row in sample_records:
        if table != "program" or row["program_id"] not in approved_programs:
            add(table, row)
    for table, row in incoming:
        add(table, row)
    evidence = {key[0]: e for key, e in merged["evidence"].items()}
    docs = {key[0]: d for key, d in merged["source_document"].items()}
    claims = {key[0]: c for key, c in merged["claim"].items()}
    supported = set()
    for row in merged["claim_evidence"].values():
        c, e = claims[row["claim_id"]], evidence[row["evidence_id"]]
        if c["review_state"] == "reviewed" and e["source_role"] in OFFICIAL and docs[e["document_id"]]["capture_mode"] == "full_document":
            supported.add((c["program_id"], c["round_id"]))
    basis = {row["opportunity_id"]: "source_backed" if key in supported else "summary_only"
             for key, row in merged["opportunity_round"].items()}
    return {t: [v for _, v in sorted(rows.items())] for t, rows in merged.items()}, basis


def validate_open_cohort(tables, basis, as_of):
    if not tables["opportunity_round"]:
        raise ValueError("current cohort has no verified open records")
    periods = {(c['program_id'],c['round_id']): c for c in tables.get('claim', [])
               if c.get('field') == 'verified_application_period' and c.get('review_state') == 'reviewed'}
    evidence = {e['evidence_id']: e for e in tables.get('evidence', [])}
    linked = {}
    for link in tables.get('claim_evidence', []):
        linked.setdefault(link['claim_id'], []).append(evidence[link['evidence_id']])
    cache = {}
    for row in tables["opportunity_round"]:
        if row["status"] != "open" or row["checked_on"] != as_of.isoformat():
            raise ValueError(f"not verified open on {as_of}: {row['opportunity_id']}")
        if row.get("opens_on") and date.fromisoformat(row["opens_on"]) > as_of:
            raise ValueError(f"application has not opened: {row['opportunity_id']}")
        if row.get("closes_on") and date.fromisoformat(row["closes_on"]) < as_of:
            raise ValueError(f"application deadline has passed: {row['opportunity_id']}")
        if not row.get("application_url") or basis[row["opportunity_id"]] != "source_backed":
            raise ValueError(f"missing application link or official evidence: {row['opportunity_id']}")
        period_claim = periods.get((row['program_id'],row['round_id']))
        if not period_claim or not any(e['source_role'] in OFFICIAL for e in linked.get(period_claim['claim_id'], [])):
            raise ValueError(f"missing verified application-period evidence: {row['opportunity_id']}")
        value = period_claim['value']
        if value['posting'].get('closes_on') != row.get('closes_on'):
            raise ValueError('Round closing date differs from independently verified period')
        from benefitlens_application_period import verify_application_period
        verify_application_period(value['posting'] | {'application_period':value['application_period']}, as_of, cache)


def export_shards(directory, tables):
    for table, rows in tables.items():
        parts, part = [], []
        for row in rows:
            if part and len(json.dumps({table: [*part, row]}, ensure_ascii=False, indent=2)) > 9500:
                parts.append(part)
                part = []
            part.append(row)
        if part:
            parts.append(part)
        for i, part in enumerate(parts, 1):
            write(directory / f"{table}-{i:03}.json", {table: part})


def execute(args):
    current_open_only = getattr(args, "current_open_only", False)
    directories = args.incoming if current_open_only else [PACKET / "data", *args.incoming]
    tables, basis = assemble(None if current_open_only else args.sample, args.as_of, directories)
    if current_open_only:
        validate_open_cohort(tables, basis, args.as_of)
    # Include validation and schema versions in the checkpoint key.
    source_files = [Path(__file__).resolve(), DB_RUNNER, Path(__file__).with_name('benefitlens_application_period.py'),
                    Path(__file__).with_name('build_benefitlens_live_batch.py'),
                    Path(__file__).with_name('benefitlens_source_storage.py'),
                    Path(__file__).with_name('expand_benefitlens_pass.py'),
                    Path(__file__).with_name('benefitlens_mcf_period.py'), PACKET / "schema.sql", PACKET / "contract.json"]
    fingerprint = sha256(encode([tables, basis, [sha256(p.read_bytes()).hexdigest() for p in source_files]]).encode()).hexdigest()
    run = args.state / "runs" / fingerprint
    output = run / "output"
    receipt = output / "verification.json"
    cached = False
    if receipt.is_file() and all((output / f"{name}.result.json").is_file() for name in ("rounds", "experiences", "patterns")):
        try:
            previous = read(receipt)
            cached = (previous.get("rows") == {table: len(rows) for table, rows in tables.items()}
                      and previous.get("contract_violation_rows") == 0
                      and previous.get("same_input_replay_unchanged") is True)
        except (ValueError, OSError):
            pass
    if not cached:
        export_shards(run / "data", tables)
        write(run / "metadata.json", {"opportunity_basis": basis})
        output.mkdir(parents=True, exist_ok=True)
        with (run / "database.log").open("w") as log:
            subprocess.run([sys.executable, str(DB_RUNNER), "--data-dir", str(run / "data"),
                            "--output", str(output), "--metadata", str(run / "metadata.json")],
                           stdout=log, stderr=subprocess.STDOUT, check=True)
    else:
        # A saved success must not hide a later missing or altered source file.
        validate_sources(tables, run)
    report = {"batch_id": fingerprint, "run": str(run), "verification": str(receipt),
              "rounds": len(basis), "source_backed": sum(v == "source_backed" for v in basis.values()),
              "summary_only": sum(v == "summary_only" for v in basis.values()),
              "cases": len(tables["experience_case"]), "patterns": len(tables["writing_pattern"])}
    latest = args.state / "latest.json"
    if not latest.is_file() or read(latest)["batch_id"] != fingerprint:
        with (args.state / "history.jsonl").open("a") as log:
            log.write(encode(report | {"at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat()}) + "\n")
        write(latest, report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--sample", type=Path, default=ROOT / "data/sample/programs.json")
    p.add_argument("--incoming", type=Path, action="append", default=[])
    p.add_argument("--current-open-only", action="store_true", help="use only explicit incoming shards and require verified open records on as-of date")
    p.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(ZoneInfo("Asia/Seoul")).date())
    p.add_argument("--follow-seconds", type=float, default=0, help="look for new reviewed shards for this long")
    p.add_argument("--poll-seconds", type=float, default=8)
    args = p.parse_args()
    if args.follow_seconds < 0 or not 0 < args.poll_seconds <= 50:
        p.error("invalid follow or poll duration")
    args.state = args.state.resolve()
    if not args.current_open_only:
        args.incoming = [DEFAULT_INCOMING, *args.incoming]
    args.state.mkdir(parents=True, exist_ok=True)
    with (args.state / "batch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        deadline = time.monotonic() + args.follow_seconds
        last_id = None
        while True:
            try:
                result = execute(args)
            except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
                write(args.state / "last_failure.json", {"error": str(exc), "at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat()})
                if not args.follow_seconds:
                    raise
            else:
                if result["batch_id"] != last_id:
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                    last_id = result["batch_id"]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(args.poll_seconds, remaining))
        if last_id is None:
            raise SystemExit("No batch passed validation; see last_failure.json")


if __name__ == "__main__":
    main()
