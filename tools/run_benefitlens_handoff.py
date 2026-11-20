"""Run the offline discovery -> reviewed schema -> writing handoff.

Originals stay in their existing cache. PostgreSQL verification is opt-in and
uses the existing disposable, network-isolated runner. No live collection,
external model invocation, personal search, or production write happens here.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys

from benefitlens_batch_db import DEFAULT, load_rows, read_json
from benefitlens_common_fields import COMMON_FIELDS, apply_reviewed_enrichments, validate_common_claim
from benefitlens_handoff import HandoffValidationError, review_mapping, to_delivery_rows, validate_handoff


ROOT = Path(__file__).resolve().parents[1]
TABLE_ARRAYS = {
    "program": "programs", "opportunity_round": "opportunity_rounds",
    "source_document": "source_documents", "evidence": "evidence", "claim": "claims",
    "experience_case": "experience_cases", "case_document": "case_documents",
    "writing_pattern": "writing_patterns", "pattern_evidence": "pattern_evidence",
}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return sha256(encode(value).encode()).hexdigest()


def immutable_json(path, value):
    content = (encode(value) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError("Immutable output conflict: " + str(path))
    else:
        with path.open("xb") as stream:
            stream.write(content)
    return {"path": str(path.resolve()), "sha256": sha256(content).hexdigest(), "bytes": len(content)}


def import_delivery_packet(data_dir, *, decisions=None):
    """Preserve old data as candidates unless a separate review is supplied."""
    data_dir = Path(data_dir).resolve()
    contract = read_json(DEFAULT / "contract.json")
    tables, _ = load_rows(data_dir, contract["load_order"], contract["column_order"])
    supplied = decisions or {}
    bundle = {"contract_version": "team-handoff-v1", "producer": "legacy_packet_adapter",
              "created_at": max((d["fetched_at"] for d in tables["source_document"]), default="1970-01-01T00:00:00+00:00")}
    bundle.update({array: deepcopy(tables[table]) for table, array in TABLE_ARRAYS.items()})
    for document in bundle["source_documents"]:
        document["access_scope"] = "public"
        for kind in ("raw", "text"):
            if document.get(kind + "_path"):
                document[kind + "_path"] = str((data_dir.parent / document[kind + "_path"]).resolve())
    refs = {}
    for link in tables["claim_evidence"]:
        refs.setdefault(link["claim_id"], []).append(link["evidence_id"])
    for array, key in (("claims", "claim_id"), ("writing_patterns", "pattern_id")):
        for row in bundle[array]:
            if array == "claims":
                row["evidence_ids"] = refs.get(row[key], [])
            row["semantic_review"] = deepcopy(supplied.get(row[key], {
                "state": "candidate", "reason": "Legacy reviewed flags do not establish a new semantic review"}))
    bundle["metadata"] = {"input_packet": str(data_dir), "legacy_flags_preserved": True,
                          "observation_only": True, "fresh_web_check": False,
                          "scope": "Imported candidates; approval requires a separate, scoped review record"}
    bundle["bundle_id"] = "legacy-handoff-" + fingerprint(bundle)[:24]
    return bundle


def review_summary(review):
    return {state: {"count": len(records), "by_entity": dict(Counter(r["entity"] for r in records)),
                    "ids": [r["id"] for r in records],
                    "reasons": dict(Counter(reason for r in records for reason in r["reasons"]))}
            for state, records in review.items()}


def apply_review_patch(bundle, patch):
    """Apply an explicit new review to an exact base, never alter source facts."""
    if not isinstance(patch, dict) or patch.get("base_bundle_sha256") != fingerprint(bundle):
        raise ValueError("Review patch does not match the immutable base bundle")
    reviews = patch.get("reviews")
    if not isinstance(reviews, dict) or not isinstance(patch.get("review_receipt_ref"), str) or not patch["review_receipt_ref"]:
        raise ValueError("Review patch requires review records and a receipt reference")
    result = deepcopy(bundle)
    known = {r["claim_id"]: r for r in result["claims"]}
    if not set(reviews) <= set(known):
        raise ValueError("Review patch references an unknown claim")
    for key, record in reviews.items():
        if not isinstance(record, dict):
            raise ValueError("Review patch record is not an object")
        known[key]["semantic_review"] = deepcopy(record)
    result.setdefault("metadata", {})["review_patch"] = {
        "base_bundle_sha256": patch["base_bundle_sha256"], "patch_sha256": fingerprint(patch),
        "review_receipt_ref": patch["review_receipt_ref"], "claims": len(reviews),
        "scope": "new explicit reviews; claim values and source relationships unchanged"}
    result["bundle_id"] = "reviewed-handoff-" + fingerprint([bundle["bundle_id"], fingerprint(patch)])[:24]
    return result


def _code_versions():
    paths = [Path(__file__), ROOT / "tools/benefitlens_handoff.py",
             ROOT / "tools/benefitlens_common_fields.py", ROOT / "tools/benefitlens_writing_handoff.py",
             ROOT / "tools/benefitlens_discovery_handoff.py", ROOT / "tools/benefitlens_application_period.py",
             ROOT / "tools/build_benefitlens_live_batch.py",
             ROOT / "tools/benefitlens_source_storage.py", ROOT / "tools/benefitlens_mcf_period.py",
             ROOT / "tools/expand_benefitlens_pass.py",
             ROOT / "tools/benefitlens_batch_db.py",
             ROOT / "pipeline/schemas/team-handoff.schema.json", ROOT / "pipeline/schemas/common-fields.schema.json",
             DEFAULT / "schema.sql", DEFAULT / "contract.json"]
    return {str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest() for p in paths}


def _snapshot_registry(state_dir, bundle):
    """Guard immutable IDs across runs, in addition to within-bundle checks."""
    path = state_dir / "snapshot-identities.json"
    registry = read_json(path) if path.exists() else {}
    next_registry = deepcopy(registry)
    for doc in bundle["source_documents"]:
        signature = {k: doc.get(k) for k in ("url", "capture_mode", "raw_sha256", "text_sha256")}
        old = registry.get(doc["document_id"])
        if old is not None and old != signature:
            raise ValueError("Immutable document snapshot ID reused with different content: " + doc["document_id"])
        next_registry[doc["document_id"]] = signature
    if next_registry != registry:
        temp = path.with_suffix(".tmp")
        temp.write_text(encode(next_registry) + "\n")
        temp.replace(path)


def projection_audit(bundle, rows, review):
    approved_claims = {r["id"] for r in review["accepted"] if r["entity"] == "claim"}
    approved_patterns = {r["id"] for r in review["accepted"] if r["entity"] == "writing_pattern"}
    expected_edges = {(c["claim_id"], eid, c["program_id"], c["round_id"])
                      for c in bundle["claims"] if c["claim_id"] in approved_claims for eid in c["evidence_ids"]}
    actual_edges = {(r["claim_id"], r["evidence_id"], r["program_id"], r["round_id"]) for r in rows["claim_evidence"]}
    findings = {"claim_ids_match_approved": {r["claim_id"] for r in rows["claim"]} == approved_claims,
                "pattern_ids_match_approved": {r["pattern_id"] for r in rows["writing_pattern"]} == approved_patterns,
                "claim_evidence_relations_match": expected_edges == actual_edges}
    if not all(findings.values()):
        raise ValueError("Projection audit failed: " + encode(findings))
    return findings


def execute_handoff(bundle, state_dir, *, artifact_root=Path("/"), applicant_facts=None,
                    required_fields_by_claim=None, enrichments=None, reviewer=None,
                    review_receipt_ref=None, review_patch=None, input_ref=None,
                    database=False, check_only=False):
    from benefitlens_writing_handoff import build_writing_handoff
    if database and check_only:
        raise ValueError("database and check_only cannot be combined")
    state_dir = Path(state_dir).resolve()
    artifact_root = Path(artifact_root).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / ".handoff.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original_hash = fingerprint(bundle)
        if input_ref and input_ref.get("semantic_bundle_sha256") != original_hash:
            raise ValueError("Input reference does not match supplied bundle")
        if check_only and not input_ref:
            raise ValueError("check_only requires an immutable input reference")
        current = apply_review_patch(bundle, review_patch) if review_patch else deepcopy(bundle)
        first = validate_handoff(current, artifact_root, field_validator=validate_common_claim)
        if not first["valid"]:
            raise HandoffValidationError(first)
        if enrichments:
            current = apply_reviewed_enrichments(current, enrichments, reviewer=reviewer,
                                                 review_receipt_ref=review_receipt_ref)
            checked = validate_handoff(current, artifact_root, field_validator=validate_common_claim)
            if not checked["valid"]:
                raise HandoffValidationError(checked)
        current = build_writing_handoff(current, applicant_facts=applicant_facts,
                                        required_fields_by_claim=required_fields_by_claim)
        checked = validate_handoff(current, artifact_root, field_validator=validate_common_claim)
        if not checked["valid"]:
            raise HandoffValidationError(checked)
        review = review_mapping(current)
        sources = _code_versions()
        key = fingerprint({"input": original_hash, "result": current, "code_versions": sources,
                           "input_ref": input_ref, "database_requested": database, "check_only": check_only})
        run = state_dir / "runs" / key
        _snapshot_registry(state_dir, current)
        receipt_path = run / "receipt.json"
        if receipt_path.exists():
            old = read_json(receipt_path)
            for artifact in old["artifacts"].values():
                path = Path(artifact["path"])
                if not path.is_file() or sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
                    raise ValueError("Saved handoff output is missing or changed: " + str(path))
            return {**old, "cache_hit": True}
        # Account for serialization and packet outputs without duplicating originals.
        estimated = len(encode(current).encode()) * (1 if check_only else 3)
        if shutil.disk_usage(state_dir).free < estimated + 128 * 1024 * 1024:
            raise OSError("Insufficient free space for handoff artifacts and 128 MiB reserve")
        run.mkdir(parents=True, exist_ok=True)
        artifacts = {}
        # Full source data remains in the supplied input bundle in check-only mode.
        if not check_only:
            artifacts["bundle"] = immutable_json(run / "handoff.json", current)
        artifacts["review"] = immutable_json(run / "review.json", review_summary(review))
        writing = current.get("metadata", {}).get("writing_result", {})
        artifacts["writing"] = immutable_json(run / "writing.json", writing)
        rows = to_delivery_rows(current, artifact_root, field_validator=validate_common_claim)
        projection = projection_audit(current, rows, review)
        database_result = {"executed": False}
        if not check_only:
            from run_benefitlens_batch import export_shards
            export_shards(run / "data", rows)
            for path in sorted((run / "data").glob("*.json")):
                artifacts["data/" + path.name] = {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
        if database:
            if check_only:
                raise ValueError("database and check_only cannot be combined")
            command = [sys.executable, str(ROOT / "tools/benefitlens_batch_db.py"),
                       "--data-dir", str(run / "data"), "--output", str(run / "database"),
                       "--artifact-root", str(artifact_root)]
            with (run / "database.log").open("w") as log:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
            verification = read_json(run / "database/verification.json")
            if verification.get("contract_violation_rows") != 0 or verification.get("same_input_replay_unchanged") is not True:
                raise ValueError("Database verification did not confirm the handoff")
            database_result = {"executed": True, "verification": verification,
                               "command": command, "scope": "disposable PostgreSQL; no production writes"}
            for path in sorted((run / "database").glob("*.json")):
                artifacts["database/" + path.name] = {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
        receipt = {"contract_version": "team-handoff-v1", "run_id": key, "run": str(run),
                   "input_bundle_sha256": original_hash, "input_bundle_id": bundle["bundle_id"],
                   "input_ref": input_ref or {"scope": "in-memory input preserved in output bundle", "semantic_bundle_sha256": original_hash},
                   "review_patch": current.get("metadata", {}).get("review_patch"),
                   "output_bundle_sha256": fingerprint(current), "code_versions": sources,
                   "validation": checked, "review": {s: {k: v for k, v in block.items() if k != "ids"} for s, block in review_summary(review).items()},
                   "projected_rows": {table: len(records) for table, records in rows.items()},
                   "projection_audit": projection,
                   "common_fields": dict(Counter(c["field"] for c in current["claims"] if (c["kind"], c["field"]) in COMMON_FIELDS)),
                   "observed_check_dates": sorted({r["checked_on"] for r in current["opportunity_rounds"]}),
                   "family_counts": current.get("metadata", {}).get("counts"),
                   "artifacts": artifacts, "database": database_result,
                   "new_http_requests": 0, "external_model_calls": 0,
                   "semantic_review_scope": "recorded review attestations and rule scope; not an independent truth or accuracy certification",
                   "fresh_web_check": False, "created_at": datetime.now(timezone.utc).isoformat(),
                   "check_only": check_only, "cache_hit": False}
        immutable_json(receipt_path, receipt)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bundle", type=Path)
    source.add_argument("--packet", type=Path, help="existing ten-table data directory")
    parser.add_argument("--decisions", type=Path, help="scoped review patch for --bundle, or claim/pattern review map for --packet")
    parser.add_argument("--facts", type=Path)
    parser.add_argument("--required-fields", type=Path)
    parser.add_argument("--enrichments", type=Path)
    parser.add_argument("--reviewer")
    parser.add_argument("--review-receipt")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, default=Path("/"))
    parser.add_argument("--database", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.database and args.check_only:
        parser.error("--database cannot be combined with --check-only")
    def optional(path):
        return read_json(path) if path else None
    try:
        bundle = read_json(args.bundle) if args.bundle else import_delivery_packet(args.packet, decisions=optional(args.decisions))
        input_ref = {"path": str((args.bundle or args.packet).resolve()), "semantic_bundle_sha256": fingerprint(bundle)}
        if args.bundle:
            input_ref["sha256"] = sha256(args.bundle.read_bytes()).hexdigest()
        if args.decisions:
            input_ref["decisions"] = {"path": str(args.decisions.resolve()), "sha256": sha256(args.decisions.read_bytes()).hexdigest()}
        for name in ("facts", "required_fields", "enrichments"):
            path = getattr(args, name)
            if path:
                input_ref[name] = {"path": str(path.resolve()), "sha256": sha256(path.read_bytes()).hexdigest()}
        receipt = execute_handoff(bundle, args.state, artifact_root=args.artifact_root,
                                  applicant_facts=optional(args.facts), required_fields_by_claim=optional(args.required_fields),
                                  enrichments=optional(args.enrichments), reviewer=args.reviewer,
                                  review_receipt_ref=args.review_receipt, review_patch=optional(args.decisions) if args.bundle else None,
                                  input_ref=input_ref, database=args.database, check_only=args.check_only)
        print(encode({"run": receipt["run"], "receipt": str(Path(receipt["run"]) / "receipt.json"),
                      "cache_hit": receipt["cache_hit"], "validation": receipt["validation"],
                      "projected_rows": receipt["projected_rows"], "review": receipt["review"]}))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, "benefitlens_handoff: " + str(error) + "\n")


if __name__ == "__main__":
    main()
