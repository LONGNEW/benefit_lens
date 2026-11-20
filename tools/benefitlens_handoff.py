"""Recorded team handoffs, not an automatic semantic or authorization oracle.

document_id identifies an immutable raw/text snapshot. Literal verification is
optional until artifact_root is supplied; it never creates semantic acceptance.
Keep the original bundle and review report as sidecars: the delivery adapter
projects accepted records only and intentionally does not change the v1 DDL.
"""
from copy import deepcopy
from collections import defaultdict, deque
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
import json
import re
from benefitlens_source_storage import read_source_bytes, read_source_text

VERSION = "team-handoff-v1"
COLUMNS = {
    "program": "program_id title issuer opportunity_type",
    "opportunity_round": "program_id round_id opportunity_id notice_version status official_url application_url opens_on closes_on announces_on checked_on",
    "source_document": "document_id title url kind capture_mode fetched_at raw_sha256 text_sha256 raw_path text_path",
    "evidence": "evidence_id document_id program_id round_id source_role locator quote",
    "claim": "claim_id program_id round_id kind field at value review_state",
    "claim_evidence": "claim_id evidence_id program_id round_id",
    "experience_case": "case_id program_id experience_round_id experience_round_label outcome_status outcome_basis outcome_evidence_id notes",
    "case_document": "case_id document_id program_id document_role finality binding_evidence_id",
    "writing_pattern": "pattern_id program_id target_round_id target_claim_id pattern_kind body scope_status causal_success_claim",
    "pattern_evidence": "pattern_id program_id target_round_id evidence_id document_id source_program_id source_round_id case_id case_document_role purpose",
}
ARRAYS = {"programs": ("program_id",), "opportunity_rounds": ("program_id", "round_id"),
          "source_documents": ("document_id",), "evidence": ("evidence_id",), "claims": ("claim_id",),
          "experience_cases": ("case_id",), "case_documents": ("case_id", "document_id", "document_role"),
          "writing_patterns": ("pattern_id",), "pattern_evidence": ("pattern_id", "evidence_id")}
TABLES = dict(zip(ARRAYS, ("program", "opportunity_round", "source_document", "evidence", "claim",
                          "experience_case", "case_document", "writing_pattern", "pattern_evidence")))
NULLABLE = {"opportunity_rounds": {"application_url", "opens_on", "closes_on", "announces_on"},
            "source_documents": {"raw_path", "raw_sha256"}, "evidence": {"round_id"},
            "claims": {"at"}, "experience_cases": {"experience_round_id", "experience_round_label", "outcome_evidence_id"},
            "case_documents": {"binding_evidence_id"}, "writing_patterns": {"target_claim_id"},
            "pattern_evidence": {"source_round_id", "case_id", "case_document_role"}}
ROLES = {"official_notice", "official_guideline", "official_form", "application_portal", "outcome_record",
         "application_statement", "application_index", "interview", "panel", "reflection", "other"}
OFFICIAL = {"official_notice", "official_guideline", "official_form", "application_portal"}
CASE_ROLES = ROLES - OFFICIAL
MACHINE_FIELDS = {("eligibility", "student_scope"), ("schedule", "current_application_status"),
                  ("schedule", "verified_application_period")}
SEMANTIC = {"human_semantic_review", "agent_semantic_review"}
METHODS = {"grant", "wage", "stipend", "loan", "fee_waiver", "interest_subsidy", "reimbursement", "in_kind", "discount", "other", "unknown"}
SHA = re.compile(r"^[0-9a-f]{64}$")
LOCATOR = re.compile(r"^L([1-9][0-9]*)(?:-L?([1-9][0-9]*))?$")


class HandoffValidationError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__("Invalid team handoff: " + json.dumps(report["errors"], ensure_ascii=False))


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _index(bundle):
    return {name: {tuple(row[k] for k in keys): row for row in bundle.get(name, [])}
            for name, keys in ARRAYS.items()}


def build_handoff_index(bundle):
    """Build an ephemeral index after assembly; never persist or globally cache it."""
    return _index(bundle)


def claim_scope_fingerprint(bundle, claim, *, indexes=None):
    """Bind review to inputs. Reuse indexes only within an unchanged assembly."""
    ix = _index(bundle) if indexes is None else indexes
    pid, rid = claim["program_id"], claim["round_id"]
    program, round_row = ix["programs"].get((pid,)), ix["opportunity_rounds"].get((pid, rid))
    if not program or not round_row:
        raise ValueError("Claim scope lacks exact program/round")
    evidence, documents = [], {}
    for eid in sorted(set(claim.get("evidence_ids", []))):
        e = ix["evidence"].get((eid,))
        if not e:
            raise ValueError("Claim scope lacks evidence " + eid)
        doc = ix["source_documents"].get((e["document_id"],))
        if not doc:
            raise ValueError("Claim scope lacks snapshot " + e["document_id"])
        evidence.append({k: deepcopy(e.get(k)) for k in COLUMNS["evidence"].split() + ["purpose", "evidence_purpose"]})
        documents[doc["document_id"]] = {k: deepcopy(doc.get(k)) for k in COLUMNS["source_document"].split() + ["access_scope", "owner_subject_id", "permission_basis"]}
    payload = {
        "contract_version": VERSION,
        "program": {k: deepcopy(program.get(k)) for k in COLUMNS["program"].split()},
        "round": {k: deepcopy(round_row.get(k)) for k in COLUMNS["opportunity_round"].split()},
        "claim": {k: deepcopy(claim.get(k)) for k in ("claim_id", "program_id", "round_id", "kind", "field", "value", "at", "source_status")},
        "evidence": evidence,
        "documents": [documents[k] for k in sorted(documents)],
        "private_subject_binding": bundle.get("metadata", {}).get("subject_id") if any(d.get("access_scope") == "user_private" for d in documents.values()) else None,
        "review_policy": {k: (claim.get("semantic_review") or {}).get(k) for k in ("basis", "rule_id", "rule_version")},
        "declared_dependencies": sorted(set(bundle.get("metadata", {}).get("claim_dependencies", {}).get(claim["claim_id"], []))),
    }
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _close_claim_dependencies(bundle, output, accepted_claims):
    dependencies = bundle.get("metadata", {}).get("claim_dependencies", {})
    reverse = defaultdict(set)
    for dependent, conditions in dependencies.items():
        for condition in conditions:
            reverse[condition].add(dependent)
    pending = deque(row["claim_id"] for row in bundle["claims"] if row["claim_id"] not in accepted_claims)
    while pending:
        blocked = pending.popleft()
        for dependent in reverse[blocked]:
            if dependent in accepted_claims:
                accepted_claims.remove(dependent)
                pending.append(dependent)
    kept = []
    for item in output["accepted"]:
        if item["entity"] == "claim" and item["id"] not in accepted_claims:
            item["reasons"].append("dependency_not_accepted")
            output["held"].append(item)
        else:
            kept.append(item)
    output["accepted"] = kept


def _review(bundle):
    indexes = _index(bundle)
    evidence = indexes["evidence"]
    documents = indexes["source_documents"]
    output = {"accepted": [], "held": [], "rejected": []}
    accepted_claims = set()
    for entity, array in (("claim", "claims"), ("writing_pattern", "writing_patterns")):
        if entity == "writing_pattern":
            _close_claim_dependencies(bundle, output, accepted_claims)
        for row in indexes[array].values():
            identifier = row["claim_id" if entity == "claim" else "pattern_id"]
            review = row.get("semantic_review") or {}
            state = review.get("state", "candidate")
            reasons = []
            if state != "accepted":
                reasons.append("semantic_" + str(state))
            for key in ("reviewer", "decision_id", "reason", "basis"):
                if not _text(review.get(key)):
                    reasons.append("missing_" + key)
            basis = review.get("basis")
            if basis in SEMANTIC:
                if not _text(review.get("review_receipt_ref")):
                    reasons.append("missing_review_receipt_ref")
            elif basis in {"machine_rule", "existing_rule_review"}:
                if entity != "claim" or (row["kind"], row["field"]) not in MACHINE_FIELDS:
                    reasons.append("machine_scope_not_allowed")
                for key in ("rule_id", "rule_version"):
                    if not _text(review.get(key)):
                        reasons.append("missing_" + key)
            else:
                reasons.append("unrecognized_review_basis")
            if entity == "claim":
                refs = row.get("evidence_ids", [])
                if row.get("value") is None:
                    reasons.append("unknown_value")
                if not _text(review.get("input_scope_fingerprint")):
                    reasons.append("missing_scope_fingerprint")
                else:
                    try:
                        if claim_scope_fingerprint(bundle, row, indexes=indexes) != review["input_scope_fingerprint"]:
                            reasons.append("scope_fingerprint_mismatch")
                    except (ValueError, KeyError, TypeError):
                        reasons.append("scope_fingerprint_invalid")
            else:
                refs = [r["evidence_id"] for r in indexes["pattern_evidence"].values() if r["pattern_id"] == identifier]
                if row.get("target_claim_id") and row["target_claim_id"] not in accepted_claims:
                    reasons.append("target_claim_not_accepted")
                if not _text(review.get("input_scope_fingerprint")):
                    reasons.append("missing_scope_fingerprint")
                else:
                    try:
                        from benefitlens_writing_handoff import writing_scope_fingerprint
                        if writing_scope_fingerprint(bundle, row) != review["input_scope_fingerprint"]:
                            reasons.append("scope_fingerprint_mismatch")
                    except ImportError:
                        reasons.append("scope_verifier_unavailable")
                    except (ValueError, KeyError, TypeError):
                        reasons.append("scope_fingerprint_invalid")
            if not refs:
                reasons.append("missing_evidence")
            for eid in refs:
                e = evidence.get((eid,))
                doc = documents.get((e["document_id"],)) if e else None
                if not doc or doc.get("access_scope") not in {"public", "user_private"}:
                    reasons.append("undeclared_document_access")
            bucket = "rejected" if state == "rejected" or "machine_scope_not_allowed" in reasons else "held" if reasons else "accepted"
            output[bucket].append({"entity": entity, "id": identifier, "record": deepcopy(row), "reasons": sorted(set(reasons))})
            if entity == "claim" and bucket == "accepted":
                accepted_claims.add(identifier)
    return output


def validate_handoff(bundle, artifact_root=None, *, field_validator=None):
    """Return JSON-safe findings. field_validator(claim) returns list[str] or None.

    Receipt references attest who recorded a review, not that its meaning is true.
    Private-document ownership is a declared binding, not a server ACL check.
    """
    report = {"valid": False, "errors": [], "warnings": [],
              "literal_checks": {"state": "not_run", "documents": 0, "spans": 0}}
    def error(code, path, message):
        report["errors"].append({"code": code, "path": path, "message": message})
    def warning(code, path, message):
        report["warnings"].append({"code": code, "path": path, "message": message})
    if not isinstance(bundle, dict):
        error("invalid_bundle", "$", "Expected an object")
        return report
    try:
        report["bundle_sha256"] = sha256(_canonical(bundle).encode("utf-8")).hexdigest()
    except (ValueError, TypeError):
        error("not_json", "$", "Bundle must be finite JSON data")
        return report
    if bundle.get("contract_version") != VERSION:
        error("contract_version", "contract_version", "Expected " + VERSION)
    if set(bundle) - (set(ARRAYS) | {"contract_version", "bundle_id", "producer", "created_at", "metadata"}):
        error("unknown_envelope_field", "$", "Put source-specific and sidecar data under metadata")
    for name in ("bundle_id", "producer", "created_at"):
        if not _text(bundle.get(name)):
            error("missing_header", name, "Required nonempty string")
    try:
        if datetime.fromisoformat(bundle.get("created_at", "").replace("Z", "+00:00")).utcoffset() is None:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        error("invalid_time", "created_at", "Expected timezone-aware ISO timestamp")
    if "metadata" not in bundle or not isinstance(bundle.get("metadata"), dict):
        error("invalid_metadata", "metadata", "Expected object")
    for name, keys in ARRAYS.items():
        rows = bundle.get(name)
        if not isinstance(rows, list):
            error("invalid_array", name, "Expected array")
            continue
        seen = {}
        for n, row in enumerate(rows):
            path = f"{name}[{n}]"
            if not isinstance(row, dict) or not all(_text(row.get(k)) for k in keys):
                error("missing_identity", path, "Expected object with keys " + str(keys))
                continue
            expected = COLUMNS[TABLES[name]].split()
            if name == "claims":
                expected = [k for k in expected if k != "review_state"] + ["evidence_ids"]
            for field in expected:
                if field not in row:
                    error("missing_field", path + "." + field, "Required field, including explicit null for unknown")
                    continue
                value = row[field]
                if field == "value":
                    continue
                if value is None and field in NULLABLE.get(name, set()):
                    continue
                if field == "body":
                    ok = isinstance(value, dict)
                elif field == "causal_success_claim":
                    ok = isinstance(value, bool)
                elif field == "evidence_ids":
                    ok = isinstance(value, list) and all(_text(v) for v in value)
                else:
                    ok = _text(value)
                if not ok:
                    error("invalid_field_type", path + "." + field, "Unexpected type or empty value")
            if "semantic_review" in row and (not isinstance(row["semantic_review"], dict) or any(not isinstance(v, str) for v in row["semantic_review"].values())):
                error("invalid_review", path, "Semantic review is an object of string-valued provenance fields")
            key = tuple(row[k] for k in keys)
            if key in seen:
                if _canonical(seen[key]) != _canonical(row):
                    error("conflicting_identity", path, "Same immutable key has different content")
                else:
                    warning("identical_duplicate", path, "Identical row is projected once")
            seen[key] = row
    if report["errors"]:
        return report
    memberships = bundle.get("metadata", {}).get("family_memberships", [])
    if not isinstance(memberships, list):
        error("family_membership", "metadata.family_memberships", "Expected array")
        return report
    ix = _index(bundle)
    programs, rounds, docs, ev = (ix[n] for n in ("programs", "opportunity_rounds", "source_documents", "evidence"))
    dependencies = bundle["metadata"].get("claim_dependencies", {})
    if not isinstance(dependencies, dict):
        error("claim_dependencies", "metadata.claim_dependencies", "Expected claim_id to condition claim ID arrays")
        return report
    dependency_nodes, dependency_reverse, dependency_counts = set(), defaultdict(set), {}
    for dependent, conditions in dependencies.items():
        if not _text(dependent):
            error("claim_dependencies", "metadata.claim_dependencies", "Dependent IDs must be nonempty strings")
            continue
        current = ix["claims"].get((dependent,))
        if not current:
            error("dependency_missing_claim", "metadata.claim_dependencies", "Dependent claim does not exist: " + dependent)
        if not isinstance(conditions, list) or not all(_text(k) for k in conditions):
            error("claim_dependencies", dependent, "Conditions must be an array of claim IDs")
            continue
        dependency_nodes.add(dependent)
        dependency_counts[dependent] = len(set(conditions))
        for condition in set(conditions):
            source = ix["claims"].get((condition,))
            if not source:
                error("dependency_missing_claim", dependent, "Condition claim does not exist: " + condition)
            elif current and (source["program_id"], source["round_id"]) != (current["program_id"], current["round_id"]):
                error("dependency_scope", dependent, "Condition belongs to another recruitment variant")
            dependency_nodes.add(condition)
            dependency_reverse[condition].add(dependent)
    ready = deque(k for k in dependency_nodes if not dependency_counts.get(k, 0))
    visited = 0
    while ready:
        condition = ready.popleft()
        visited += 1
        for dependent in dependency_reverse[condition]:
            dependency_counts[dependent] -= 1
            if dependency_counts[dependent] == 0:
                ready.append(dependent)
    if visited != len(dependency_nodes):
        error("claim_dependency_cycle", "metadata.claim_dependencies", "Cyclic dependencies cannot be approved")
    if report["errors"]:
        return report
    def required(row, keys, path):
        for key in keys.split():
            if not _text(row.get(key)):
                error("missing_field", path + "." + key, "Required nonempty string")
    def member(value, options, path):
        if value not in options:
            error("invalid_enum", path, "Unsupported value")
    def round_ref(pid, rid, path, nullable=False):
        if (pid,) not in programs or (rid is None and not nullable) or (rid is not None and (pid, rid) not in rounds):
            error("round_binding", path, "Missing exact program/round binding")
    def evidence_ref(eid, pid, rid, path, exact_round=True):
        source = ev.get((eid,))
        if not source or source.get("program_id") != pid or (exact_round and source.get("round_id") != rid):
            error("evidence_scope", path, "Evidence belongs to another or unknown recruitment variant")
        return source
    for row in programs.values():
        required(row, "title issuer", row["program_id"])
        member(row.get("opportunity_type"), {"scholarship", "fellowship", "admission_funding", "internship", "other"}, row["program_id"])
    opportunity_ids = set()
    for row in rounds.values():
        path = row["round_id"]
        required(row, "opportunity_id notice_version official_url checked_on", path)
        if (row["program_id"],) not in programs:
            error("program_binding", path, "Program missing")
        if row.get("opportunity_id") in opportunity_ids:
            error("opportunity_identity", path, "opportunity_id reused")
        opportunity_ids.add(row.get("opportunity_id"))
        member(row.get("status"), {"open", "closed", "upcoming", "historical", "unknown"}, path)
        try:
            for k in ("opens_on", "closes_on", "announces_on", "checked_on"):
                if row.get(k) is not None:
                    date.fromisoformat(row[k])
            if row.get("opens_on") and row.get("closes_on") and row["opens_on"] > row["closes_on"]:
                raise ValueError()
        except (TypeError, ValueError):
            error("invalid_date", path, "Invalid or reversed calendar dates")
        for k in ("official_url", "application_url"):
            if row.get(k) is not None and not re.match(r"^https?://", row[k]):
                error("invalid_url", path + "." + k, "Expected HTTP(S) URL")
    texts, literal_files, literal_texts = {}, {}, {}
    for row in docs.values():
        path = row["document_id"]
        required(row, "title url kind fetched_at text_path text_sha256", path)
        member(row.get("capture_mode"), {"full_document", "public_web_excerpt"}, path)
        if not re.match(r"^https?://", row["url"]):
            error("invalid_url", path, "Expected HTTP(S) URL")
        try:
            if datetime.fromisoformat(row["fetched_at"].replace("Z", "+00:00")).utcoffset() is None:
                raise ValueError()
        except ValueError:
            error("invalid_time", path, "Snapshot needs timezone-aware fetched_at")
        if row.get("capture_mode") == "full_document":
            required(row, "raw_path raw_sha256", path)
        elif row.get("raw_path") is not None or row.get("raw_sha256") is not None:
            error("excerpt_original", path, "An excerpt cannot claim original bytes")
        for kind in ("raw", "text"):
            checksum = row.get(kind + "_sha256")
            if checksum is not None and (not isinstance(checksum, str) or not SHA.fullmatch(checksum)):
                error("invalid_hash", path, "Expected lowercase SHA-256")
        access = row.get("access_scope")
        if access is None:
            warning("undeclared_document_access", path, "Dependent records remain held")
        elif access == "user_private":
            if not _text(row.get("owner_subject_id")) or row.get("owner_subject_id") != bundle.get("metadata", {}).get("subject_id") or not _text(row.get("permission_basis")):
                error("private_document_owner", path, "Private document needs a matching subject and permission basis")
        elif access != "public":
            error("document_access", path, "Expected public or user_private")
        if artifact_root is not None:
            for kind in ("raw", "text"):
                if kind == "raw" and row.get("capture_mode") == "public_web_excerpt":
                    continue
                try:
                    f = (Path(artifact_root) / row[kind + "_path"]).resolve()
                    stat = f.stat()
                    stamp = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                    if f not in literal_files:
                        literal_files[f] = (stamp, sha256(read_source_bytes(f, storage=row.get(kind + '_storage'))).hexdigest())
                    elif row.get(kind + '_storage') is not None:
                        read_source_bytes(f, storage=row[kind + '_storage'])
                    if literal_files[f] != (stamp, row[kind + "_sha256"]):
                        raise ValueError("Hash mismatch")
                    if kind == "text":
                        if f not in literal_texts:
                            literal_texts[f] = read_source_text(f).split("\n")
                        texts[path] = literal_texts[f]
                    after = f.stat()
                    if stamp != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise ValueError("Artifact changed during validation")
                except (OSError, KeyError, TypeError, UnicodeError, ValueError) as exc:
                    error("literal_artifact", path + "." + kind, str(exc))
            report["literal_checks"]["documents"] += 1
    for row in ev.values():
        path = row["evidence_id"]
        required(row, "document_id program_id locator quote", path)
        round_ref(row.get("program_id"), row.get("round_id"), path, True)
        if (row.get("document_id"),) not in docs:
            error("snapshot_binding", path, "Evidence snapshot missing")
        member(row.get("source_role"), ROLES, path)
        loc = LOCATOR.fullmatch(row.get("locator", ""))
        if not loc:
            error("invalid_locator", path, "Expected LF locator L1 or L1-L2")
        if artifact_root is not None and loc:
            lines = texts.get(row.get("document_id"), [])
            a, b = int(loc[1]), int(loc[2] or loc[1])
            actual = " ".join(" ".join(lines[a-1:b]).split())
            quote = " ".join(row.get("quote", "").split())
            if not (1 <= a <= b <= len(lines) and quote and quote in actual):
                error("literal_quote", path, "Quote absent at the declared extraction locator")
            report["literal_checks"]["spans"] += 1
    def declared_unknown_false(value):
        if isinstance(value, list):
            return any(declared_unknown_false(item) for item in value)
        if not isinstance(value, dict):
            return False
        unknown = any(value.get(k) in ("unknown", "unmapped", "missing", "ambiguous") for k in ("status", "state", "source_status", "interpretation_status"))
        bad = unknown and any(value.get(k) is False for k in ("eligible", "satisfied", "matches", "qualifies", "result", "decision"))
        return bad or any(declared_unknown_false(v) for v in value.values() if isinstance(v, dict)) or any(declared_unknown_false(x) for v in value.values() if isinstance(v, list) for x in v)
    for row in ix["claims"].values():
        path = row["claim_id"]
        required(row, "program_id round_id field", path)
        round_ref(row.get("program_id"), row.get("round_id"), path)
        member(row.get("kind"), {"eligibility", "benefit", "activity", "submission", "form", "advice", "schedule", "selection_criterion"}, path)
        refs = row.get("evidence_ids", [])
        if not isinstance(refs, list) or not all(_text(e) for e in refs):
            error("invalid_evidence_ids", path, "Expected array of evidence IDs")
            refs = []
        for eid in refs:
            e = evidence_ref(eid, row.get("program_id"), row.get("round_id"), path)
            if e and e.get("source_role") not in OFFICIAL:
                error("claim_source_role", path, "Current claims need official scoped evidence")
        val = row.get("value")
        if declared_unknown_false(val) or (row.get("source_status") in ("unknown", "missing", "unmapped", "ambiguous") and val is False):
            error("unknown_became_false", path, "Unknown must not become a negative eligibility decision")
        if row.get("kind") == "benefit" and isinstance(val, dict):
            for component in [val] + (val.get("support_components") if isinstance(val.get("support_components"), list) else []):
                if isinstance(component, dict):
                    for k in ("method", "payment_method", "support_method"):
                        if k in component and (not isinstance(component[k], str) or component[k] not in METHODS):
                            error("benefit_method_axis", path, "Opportunity form is not a payment method")
                    embedded_refs = component.get("evidence_ids", [])
                    if not isinstance(embedded_refs, list) or not all(_text(e) for e in embedded_refs):
                        error("invalid_evidence_ids", path, "Component evidence IDs need an array")
                    else:
                        for eid in embedded_refs:
                            evidence_ref(eid, row.get("program_id"), row.get("round_id"), path)
                            if eid not in refs:
                                error("unlinked_component_evidence", path, "Component evidence must also be linked by its parent claim")
                    scope = component.get("source_scope")
                    if isinstance(scope, dict) and (scope.get("program_id"), scope.get("round_id")) != (row.get("program_id"), row.get("round_id")):
                        error("benefit_variant_mix", path, "Benefit belongs to another recruitment variant")
        if field_validator is not None:
            messages = field_validator(deepcopy(row))
            if messages is not None and not isinstance(messages, list):
                error("field_validator_contract", path, "field_validator must return list[str] or None")
                messages = []
            for message in messages or []:
                error("field_validation", path, str(message))
    for row in ix["experience_cases"].values():
        path = row["case_id"]
        round_ref(row.get("program_id"), row.get("experience_round_id"), path, True)
        member(row.get("outcome_status"), {"selected", "not_selected", "unknown"}, path)
        member(row.get("outcome_basis"), {"official_record", "author_report", "unknown"}, path)
        if row.get("outcome_status") == "unknown":
            if row.get("outcome_basis") != "unknown":
                error("outcome_unknown", path, "Unknown outcome needs unknown basis")
        elif row.get("outcome_basis") == "unknown" or not row.get("outcome_evidence_id"):
            error("outcome_evidence", path, "Known outcome needs sourced evidence")
        if row.get("outcome_evidence_id"):
            evidence_ref(row["outcome_evidence_id"], row.get("program_id"), None, path, False)
    for row in ix["case_documents"].values():
        path = str((row["case_id"], row["document_id"]))
        case = ix["experience_cases"].get((row["case_id"],))
        if not case or case.get("program_id") != row.get("program_id") or (row["document_id"],) not in docs:
            error("case_document_binding", path, "Case, snapshot and program must match")
        member(row.get("document_role"), CASE_ROLES, path)
        member(row.get("finality"), {"unknown", "public_draft", "confirmed_final", "not_applicable"}, path)
        if row["document_role"] != "application_statement" and row.get("finality") != "not_applicable":
            error("document_finality", path, "Finality applies only to application statements")
        if row.get("finality") == "confirmed_final" and not row.get("finality_evidence_id"):
            error("unproven_finality", path, "Confirmed final needs explicit evidence")
        for k in ("binding_evidence_id", "finality_evidence_id"):
            if row.get(k):
                evidence_ref(row[k], row.get("program_id"), None, path, False)
    for row in ix["writing_patterns"].values():
        path = row["pattern_id"]
        round_ref(row.get("program_id"), row.get("target_round_id"), path)
        member(row.get("pattern_kind"), {"structure", "strengthen", "avoid", "question"}, path)
        member(row.get("scope_status"), {"same_round", "historical_transfer", "cross_program", "general", "official_only"}, path)
        if not isinstance(row.get("body"), dict) or row.get("causal_success_claim") is not False:
            error("pattern_body", path, "Need object body and explicit false causal_success_claim")
        target = ix["claims"].get((row.get("target_claim_id"),))
        if row.get("target_claim_id") and (not target or (target.get("program_id"), target.get("round_id")) != (row.get("program_id"), row.get("target_round_id"))):
            error("pattern_target", path, "Target claim must belong to exact target round")
        elif target and target.get("kind") not in {"submission", "form"}:
            error("pattern_target_kind", path, "Writing pattern must target a submission/form requirement")
    for row in ix["pattern_evidence"].values():
        path = str((row["pattern_id"], row["evidence_id"]))
        pattern = ix["writing_patterns"].get((row["pattern_id"],))
        if not pattern or (pattern.get("program_id"), pattern.get("target_round_id")) != (row.get("program_id"), row.get("target_round_id")):
            error("pattern_binding", path, "Pattern target binding mismatch")
        e = evidence_ref(row["evidence_id"], row.get("source_program_id"), row.get("source_round_id"), path)
        if e and row.get("document_id") != e.get("document_id"):
            error("pattern_snapshot", path, "Pattern must identify evidence's exact snapshot")
        purpose = row.get("purpose")
        member(purpose, {"case_observation", "official_constraint", "official_guidance"}, path)
        if purpose == "case_observation":
            binding = ix["case_documents"].get((row.get("case_id"), row.get("document_id"), row.get("case_document_role")))
            if not binding or binding.get("program_id") != row.get("source_program_id") or not e or e.get("source_role") != row.get("case_document_role"):
                error("pattern_case_binding", path, "Observation must use the bound case and document role")
        elif row.get("case_id") is not None or row.get("case_document_role") is not None or not e or e.get("source_role") not in OFFICIAL or (row.get("source_program_id"), row.get("source_round_id")) != (row.get("program_id"), row.get("target_round_id")):
            error("pattern_official_binding", path, "Official guidance needs exact target-round official evidence and no case")
        if pattern and (pattern.get("scope_status") in {"same_round", "official_only"}) and (row.get("source_program_id"), row.get("source_round_id")) != (pattern.get("program_id"), pattern.get("target_round_id")):
            error("pattern_scope", path, "Declared scope cannot use a historical or different round")
        if pattern and pattern.get("scope_status") == "historical_transfer" and row.get("source_program_id") != pattern.get("program_id"):
            error("pattern_scope", path, "Cross-program evidence must be declared cross_program")
    families = {}
    for row in memberships:
        if not isinstance(row, dict):
            error("family_membership", "metadata.family_memberships", "Expected object")
            continue
        if not all(_text(row.get(k)) for k in ("program_id", "round_id", "opportunity_family_id", "rule_version", "review_state")):
            error("family_membership", "metadata.family_memberships", "All identity and review fields must be nonempty strings")
            continue
        required(row, "program_id round_id opportunity_family_id rule_version review_state", "family_membership")
        round_ref(row.get("program_id"), row.get("round_id"), "family_membership")
        key = (row.get("program_id"), row.get("round_id"))
        if key in families and families[key] != row.get("opportunity_family_id"):
            error("family_conflict", "family_membership", "One recruitment variant cannot silently move to another family")
        families[key] = row.get("opportunity_family_id")
    if artifact_root is not None:
        report["literal_checks"]["state"] = "failed" if any(e["code"].startswith("literal_") for e in report["errors"]) else "verified"
    else:
        warning("literal_not_run", "$", "Hashes and LF quotes have not been checked against local files")
    if not report["errors"]:
        review = _review(bundle)
        for item in review["held"] + review["rejected"]:
            warning("not_accepted", item["id"], ",".join(item["reasons"]))
    report["valid"] = not report["errors"]
    return report


def review_mapping(bundle):
    """Preserve full candidate records; only explicit, scoped review is accepted."""
    report = validate_handoff(bundle)
    if not report["valid"]:
        raise HandoffValidationError(report)
    return _review(bundle)


def to_delivery_rows(bundle, artifact_root=None, *, field_validator=None, structural_only=False):
    """Project to v1. structural_only=True is explicitly not for publication/load.

    A publishing projection requires on-disk hashes and LF evidence checks.
    Keep bundle/review and any open-status interpretation scope as sidecars.
    """
    if artifact_root is None and not structural_only:
        raise HandoffValidationError({"valid": False, "errors": [{"code": "literal_required", "path": "$",
            "message": "Provide artifact_root for delivery; structural_only=True is a non-publishing projection"}],
            "warnings": [], "literal_checks": {"state": "not_run", "documents": 0, "spans": 0}})
    report = validate_handoff(bundle, artifact_root, field_validator=field_validator)
    if not report["valid"]:
        raise HandoffValidationError(report)
    ix = _index(bundle)
    review = _review(bundle)
    claims = {r["id"] for r in review["accepted"] if r["entity"] == "claim"}
    patterns = {r["id"] for r in review["accepted"] if r["entity"] == "writing_pattern"}
    rows = {table: [] for table in COLUMNS}
    for source, table in (("programs", "program"), ("opportunity_rounds", "opportunity_round"),
                          ("source_documents", "source_document"), ("evidence", "evidence"),
                          ("experience_cases", "experience_case"), ("case_documents", "case_document"),
                          ("claims", "claim"), ("writing_patterns", "writing_pattern"), ("pattern_evidence", "pattern_evidence")):
        for record in ix[source].values():
            if source == "claims" and record["claim_id"] not in claims:
                continue
            if source in {"writing_patterns", "pattern_evidence"} and record["pattern_id"] not in patterns:
                continue
            out = {k: deepcopy(record.get(k)) for k in COLUMNS[table].split()}
            if source == "claims":
                out["review_state"] = "reviewed"
                rows["claim_evidence"].extend({"claim_id": record["claim_id"], "evidence_id": eid,
                                               "program_id": record["program_id"], "round_id": record["round_id"]}
                                              for eid in sorted(set(record.get("evidence_ids", []))))
            rows[table].append(out)
    return rows
