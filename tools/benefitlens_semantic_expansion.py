"""Small, source-bound semantic deltas; extraction rules only propose candidates.

The caller supplies individually reviewed mappings. No HTTP, model, DB write or
automatic semantic approval occurs here. The immutable base is referenced, not
rewritten. Existing program.type stays untouched by default.
"""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from benefitlens_common_fields import validate_common_claim
from benefitlens_handoff import build_handoff_index, claim_scope_fingerprint
from benefitlens_source_storage import read_source_bytes

VERSION = "semantic-expansion-v1"
RULE_VERSION = "source-signatures-2026-11-09-v1"
OFFICIAL = {"official_notice", "official_guideline", "official_form", "application_portal"}
FORMS = {"internship", "scholarship", "fellowship", "student_job", "loan", "admission_funding",
         "doctoral_research_position", "other", "unknown"}
CURRENCY_LABELS = {"USD": r"\bUSD\b|US dollars?|U\.S\. dollars?", "CHF": r"\bCHF\b|Swiss Francs?",
                   "AUD": r"\bAUD\b|Australian dollars?", "GBP": r"\bGBP\b|pounds? sterling",
                   "KRW": r"\bKRW\b|Korean won|대한민국 원"}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return sha256(canonical(value).encode()).hexdigest()


def _quote(text, quote, locator=None):
    if not isinstance(quote, str) or not quote.strip() or quote not in text:
        raise ValueError("Quote is not an exact original span")
    if locator:
        m = re.fullmatch(r"L([1-9][0-9]*)(?:-L?([1-9][0-9]*))?", locator)
        lines = text.split("\n")
        if not m or not 1 <= int(m[1]) <= int(m[2] or m[1]) <= len(lines) or quote not in "\n".join(lines[int(m[1])-1:int(m[2] or m[1])]):
            raise ValueError("Quote does not match its LF locator")
        return locator
    if text.count(quote) != 1:
        raise ValueError("Ambiguous quote needs an explicit LF locator")
    start = text.index(quote)
    a = text[:start].count("\n") + 1
    return f"L{a}-L{a + quote.count(chr(10))}"


def _literal(document, cache, *, include_raw=False):
    keys = ("raw", "text") if include_raw and document["capture_mode"] == "full_document" else ("text",)
    for kind in keys:
        path = Path(document[kind + "_path"])
        if not path.is_absolute():
            raise ValueError("Source paths must be absolute")
        stat = path.stat()
        stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if path not in cache:
            data = read_source_bytes(path, storage=document.get(kind + '_storage'))
            cache[path] = (stamp, sha256(data).hexdigest(), data.decode("utf-8") if kind == "text" else None)
        elif document.get(kind + '_storage') is not None:
            read_source_bytes(path, storage=document[kind + '_storage'])
        if cache[path][:2] != (stamp, document[kind + "_sha256"]):
            raise ValueError("Immutable source hash mismatch")
    return cache[Path(document["text_path"])][2]


def _candidate(title, family):
    # Title/scope rules intentionally never issue an accepted mapping.
    if re.search(r"\bfellowship\b", title, re.I):
        return "fellowship", "title_fellowship"
    if family in {"UQ", "CAMBRIDGE-FUNDING", "SYDNEY-SCHOLARSHIP", "UNSW", "DAAD"}:
        return "scholarship", "official_funding_source_family"
    if re.search(r"\bintern(?:ship|s)?\b|\bco[ -]?op\b|praktik|\bstage\b|stagiaire|estágio|实习|實習|인턴", title, re.I):
        return "internship", "title_internship_candidate"
    if re.search(r"working[ -]?student|werkstud|student.*(?:assistant|worker)|graduate assistant", title, re.I):
        return "student_job", "title_student_employment_candidate"
    if family == "EURAXESS-PHD":
        return "doctoral_research_position", "doctoral_position_not_assumed_scholarship"
    return "unknown", "no_supported_form_signature"


def generate_semantic_patch(bundle, source_rows=None, *, reviewed_mappings=(),
                            review_receipt_ref=None, reviewer="schema-agent", input_refs=(), known_mappings=()):
    """Return a small delta. Only supplied explicit reviews create approved rows.

    source_rows is optional; the existing source_specific sidecar supplies the
    source family and title. Reviewed mappings name exact IDs and original spans.
    """
    ix = build_handoff_index(bundle)
    bindings = {(e["program_id"], e.get("round_id"), e["document_id"]) for e in bundle["evidence"] if e["source_role"] in OFFICIAL}
    sidecars = {(r["program_id"], r["round_id"]): r.get("fields", {}) for r in bundle.get("metadata", {}).get("source_specific", [])}
    if source_rows is not None:
        for row in source_rows:
            if "program_id" in row and "round_id" in row:
                sidecars[(row["program_id"], row["round_id"])] = row.get("fields", row)
    by_scope = {}
    for e in bundle["evidence"]:
        if e["source_role"] in OFFICIAL:
            by_scope.setdefault((e["program_id"], e.get("round_id")), []).append(e)
    cache, decisions = {}, []
    for scope, round_row in sorted(ix["opportunity_rounds"].items()):
        program = ix["programs"][(scope[0],)]
        fields = sidecars.get(scope, {})
        form, rule = _candidate(program["title"], fields.get("source_family"))
        quote_ref = None
        for e in by_scope.get(scope, []):
            doc = ix["source_documents"][(e["document_id"],)]
            text = _literal(doc, cache)
            if program["title"].strip() in text:
                quote = program["title"].strip()
                start = text.index(quote)
                line = text[:start].count("\n") + 1
                quote_ref = {"document_id": doc["document_id"], "text_sha256": doc["text_sha256"],
                             "quote": quote, "locator": f"L{line}-L{line + quote.count(chr(10))}"}
                break
        if not quote_ref:
            form, rule = "unknown", "title_not_in_bound_source"
        decisions.append({"program_id": scope[0], "round_id": scope[1], "proposed_form": form,
                          "state": "unknown" if form == "unknown" else "candidate", "rule_id": rule,
                          "rule_version": RULE_VERSION, "source_family": fields.get("source_family"), "evidence": quote_ref})
    patch = {"contract_version": VERSION, "base_bundle_sha256": fingerprint(bundle),
             "input_refs": list(input_refs), "base_counts": {k: len(bundle[k]) for k in ("programs", "opportunity_rounds", "claims", "source_documents")},
             "program_updates": [], "evidence": [], "claims": [], "mapping_decisions": decisions,
             "anchors": {"programs": {}, "opportunity_rounds": {}, "source_documents": {}},
             "scope": "Explicitly reviewed mappings only; extraction-rule output stays candidate. No new opportunity count."}
    for spec in reviewed_mappings:
        if not review_receipt_ref or not spec.get("reason") or not spec.get("rule_id") or not spec.get("rule_version"):
            raise ValueError("Reviewed mappings require receipt, reason and rule scope/version")
        scope = (spec["program_id"], spec["round_id"])
        doc = ix["source_documents"].get((spec["document_id"],))
        if scope not in ix["opportunity_rounds"] or not doc or (*scope, spec["document_id"]) not in bindings:
            raise ValueError("Mapping needs an existing exact-round official snapshot binding")
        if doc.get("access_scope") != "public":
            raise ValueError("This expansion handles public opportunity documents only")
        text = _literal(doc, cache, include_raw=True)
        locator = _quote(text, spec["quote"], spec.get("locator"))
        core = {k: deepcopy(spec.get(k)) for k in ("program_id", "round_id", "kind", "field", "value", "at")}
        key = fingerprint({**core, "document_id": doc["document_id"], "quote": spec["quote"],
                           "rule_id": spec["rule_id"], "rule_version": spec["rule_version"]})[:24]
        eid, cid = "semantic-evidence-" + key, "semantic-claim-" + key
        value = core["value"]
        if core["field"] == "opportunity_classification":
            if core["kind"] != "advice" or value.get("opportunity_form") not in FORMS or value.get("schema_version") != VERSION:
                raise ValueError("Classification has its own informational axis")
        if core["field"] == "support_use_set":
            if core["kind"] != "advice" or value.get("schema_version") != VERSION or not isinstance(value.get("purposes"), list) or not value["purposes"] or not set(value["purposes"]) <= {"tuition", "living", "housing", "travel", "research", "unknown"} or value.get("allocation") != "shared_not_additive":
                raise ValueError("Shared use set must not multiply one award across purposes")
        if core["field"] == "support_component" and value["amount"].get("currency_code"):
            code = value["amount"]["currency_code"]
            label = CURRENCY_LABELS.get(code, r"\b" + re.escape(code) + r"\b")
            if not spec.get("currency_quote") or spec["currency_quote"] not in spec["quote"] or not re.search(label, spec["currency_quote"], re.I):
                raise ValueError("Currency normalization needs explicit quoted currency evidence")
            value["amount"]["currency_evidence_ids"] = [eid]
        evidence = {"evidence_id": eid, "document_id": doc["document_id"], "program_id": scope[0], "round_id": scope[1],
                    "source_role": spec.get("source_role", "official_notice"), "quote": spec["quote"], "locator": locator}
        if evidence["source_role"] not in OFFICIAL:
            raise ValueError("Expansion cannot promote an experience into official eligibility")
        claim = {**core, "claim_id": cid, "review_state": "reviewed", "evidence_ids": [eid],
                 "semantic_review": {"state": "accepted", "basis": "agent_semantic_review", "reviewer": reviewer,
                     "decision_id": "semantic-review-" + key, "reason": spec["reason"], "review_receipt_ref": str(review_receipt_ref),
                     "rule_id": spec["rule_id"], "rule_version": spec["rule_version"]}}
        errors = validate_common_claim(claim)
        if errors:
            raise ValueError("Invalid typed mapping: " + "; ".join(errors))
        for component, identity, record in (("programs", scope[0], ix["programs"][(scope[0],)]),
                                             ("opportunity_rounds", canonical(list(scope)), ix["opportunity_rounds"][scope]),
                                             ("source_documents", doc["document_id"], doc)):
            patch["anchors"][component][identity] = fingerprint(record)
        previous = next((c for c in patch["claims"] if c["claim_id"] == cid), None)
        if previous is not None:
            if canonical(previous) != canonical(claim):
                raise ValueError("Conflicting duplicate semantic mapping")
            continue
        patch["evidence"].append(evidence)
        patch["claims"].append(claim)
    patch = prune_known_aliases(patch, known_mappings)
    # Compute new input fingerprints over a lightweight provisional overlay.
    overlay = dict(bundle)
    overlay["evidence"] = bundle["evidence"] + patch["evidence"]
    overlay["claims"] = bundle["claims"] + patch["claims"]
    indexes = build_handoff_index(overlay)
    for claim in patch["claims"]:
        claim["semantic_review"]["input_scope_fingerprint"] = claim_scope_fingerprint(overlay, claim, indexes=indexes)
    approved = {(c["program_id"], c["round_id"]): c["value"]["opportunity_form"] for c in patch["claims"] if c["field"] == "opportunity_classification"}
    for decision in decisions:
        if (decision["program_id"], decision["round_id"]) in approved:
            decision["state"] = "reviewed"
            decision["approved_form"] = approved[(decision["program_id"], decision["round_id"])]
    patch["summary"] = {"prior_program_types": dict(Counter(p["opportunity_type"] for p in bundle["programs"])),
                        "mapping_states": dict(Counter(d["state"] for d in decisions)),
                        "candidate_forms": dict(Counter(d["proposed_form"] for d in decisions if d["state"] == "candidate")),
                        "reviewed_forms": dict(Counter(approved.values())), "added_claims": len(patch["claims"]),
                        "new_opportunity_rounds": 0, "raw_source_documents_changed": 0,
                        "aliases_to_carried_mappings": len(patch["aliases"])}
    return patch


def prune_known_aliases(patch, known_mappings):
    """Same exact recruitment/span/value is not new meaning; units stay distinct."""
    def identity(record, quote, document_id):
        value = deepcopy(record["value"])
        if record["field"] == "support_component":
            value.pop("component_id", None)  # display identity, not another award
        at = record.get("at")
        if record["kind"] == "benefit" and at == "source_observation":
            at = None
        return canonical({k: record[k] for k in ("program_id", "round_id", "kind", "field")} | {"value": value, "quote": quote, "document_id": document_id, "at": at})
    known = {identity(r, r["quote"], r["document_id"]): r for r in known_mappings}
    spans = {e["evidence_id"]: e for e in patch["evidence"]}
    kept, aliases = [], []
    for claim in patch["claims"]:
        quote = spans[claim["evidence_ids"][0]]["quote"]
        existing = known.get(identity(claim, quote, spans[claim["evidence_ids"][0]]["document_id"]))
        if existing is None:
            kept.append(claim)
        else:
            core = {k: existing[k] for k in ("program_id", "round_id", "kind", "field", "value")}
            existing_id = "common-claim-" + fingerprint({**core, "document_id": existing["document_id"], "quote": existing["quote"], "at": existing.get("at")})[:24]
            aliases.append({"omitted_new_claim_id": claim["claim_id"], "existing_claim_id": existing_id,
                            "program_id": claim["program_id"], "round_id": claim["round_id"], "field": claim["field"],
                            "reason": "Same exact recruitment, source span and normalized value; not an incremental fact"})
    refs = {eid for c in kept for eid in c["evidence_ids"]}
    result = dict(patch)
    result.update(claims=kept, evidence=[e for e in patch["evidence"] if e["evidence_id"] in refs], aliases=aliases)
    return result


def apply_semantic_patch(bundle, patch):
    """Return a non-destructive small overlay; permit additive upstream deltas.

    Complete-base hashes remain audit references. Every reviewed target is
    anchored to unchanged program, recruitment variant and snapshot content.
    """
    if patch.get("contract_version") != VERSION or patch.get("program_updates"):
        raise ValueError("Unsupported semantic patch or forbidden program mutation")
    ix = build_handoff_index(bundle)
    for component, anchors in patch["anchors"].items():
        for identity, expected in anchors.items():
            key = tuple(json.loads(identity)) if component == "opportunity_rounds" else (identity,)
            if key not in ix[component] or fingerprint(ix[component][key]) != expected:
                raise ValueError("Semantic patch anchor changed: " + component + " " + identity)
    result = dict(bundle)
    for array, identity in (("evidence", "evidence_id"), ("claims", "claim_id")):
        known = {r[identity]: r for r in bundle[array]}
        additions = []
        for row in patch[array]:
            if row[identity] in known:
                if canonical(known[row[identity]]) != canonical(row):
                    raise ValueError("Conflicting immutable semantic row")
            else:
                known[row[identity]] = row
                additions.append(deepcopy(row))
        result[array] = bundle[array] + additions
    metadata = dict(bundle.get("metadata", {}))
    overlays = {r["claim_id"]: r for r in metadata.get("normalized_classification_overlay", [])}
    for c in patch["claims"]:
        if c["field"] == "opportunity_classification":
            overlays[c["claim_id"]] = {"program_id": c["program_id"], "round_id": c["round_id"], "claim_id": c["claim_id"],
                                       "opportunity_form": c["value"]["opportunity_form"], "review_state": "accepted"}
    metadata["normalized_classification_overlay"] = list(overlays.values())
    applied = dict(metadata.get("semantic_expansion_patches", {}))
    applied[fingerprint(patch)] = {"base_bundle_sha256": patch["base_bundle_sha256"], "summary": patch["summary"]}
    metadata["semantic_expansion_patches"] = applied
    result["metadata"] = metadata
    content = dict(result)
    content.pop("bundle_id", None)
    result["bundle_id"] = "semantic-handoff-" + fingerprint(content)[:24]
    return result


def write_semantic_patch(patch, path, *, max_bytes=5 * 1024 * 1024):
    """Content-addressed caller path, bounded and immutable; never dump the base."""
    data = (canonical(patch) + "\n").encode()
    path = Path(path)
    if len(data) > max_bytes:
        raise ValueError("Semantic patch exceeds byte budget")
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("Immutable patch output conflict")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
    return {"path": str(path.resolve()), "sha256": sha256(data).hexdigest(), "bytes": len(data)}
