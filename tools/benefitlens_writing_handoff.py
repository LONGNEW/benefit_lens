"""Pure preparation adapter for reviewed writing inputs.

This module does no I/O or model call. It prepares checklists, scoped guidance
and fact slots, not a finished application. Run the common handoff validator
with artifact_root separately to verify snapshot bytes and exact LF spans.
Recorded semantic reviews are provenance, not an automatic truth guarantee.
"""
from copy import deepcopy
import hashlib
import json
import re

VERSION = "writing-handoff-v1"
OFFICIAL = {"official_notice", "official_guideline", "official_form", "application_portal"}
WRITING_FIELDS = {"research_proposal", "research_statement", "personal_statement", "essay",
                  "statement_of_purpose", "motivation_statement", "writing_prompt"}
OBSERVATION_ROLES = {"application_statement", "reflection", "interview", "panel"}
SEMANTIC = {"agent_semantic_review", "human_semantic_review"}


class WritingHandoffError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _index(bundle, name, key):
    out = {}
    for row in bundle.get(name, []):
        identifier = row.get(key)
        if not identifier or identifier in out:
            raise WritingHandoffError(f"Missing or duplicate {name}.{key}")
        out[identifier] = row
    return out


def _sorted(rows):
    return sorted(rows, key=_canonical)


def writing_scope_fingerprint(bundle, pattern):
    """Bind a reviewed interpretation to current document/case/target semantics.

    Review metadata is excluded to avoid self-reference. Reordering unrelated
    records does not change the digest. Changing a quote, role, finality,
    outcome, permissions, prompt, source round or target claim invalidates it.
    """
    ix = {name: _index(bundle, name, key) for name, key in (
        ("source_documents", "document_id"), ("evidence", "evidence_id"),
        ("claims", "claim_id"), ("experience_cases", "case_id"))}
    claim = ix["claims"].get(pattern.get("target_claim_id"))
    links = [r for r in bundle.get("pattern_evidence", []) if r.get("pattern_id") == pattern.get("pattern_id")]
    case_ids = {r["case_id"] for r in links if r.get("case_id")}
    if not case_ids <= set(ix["experience_cases"]):
        raise WritingHandoffError("Pattern references a missing case")
    cases = [ix["experience_cases"][c] for c in case_ids]
    bindings = [r for r in bundle.get("case_documents", [])
                if any(r.get("case_id") == link.get("case_id") and
                       r.get("document_id") == ix["evidence"].get(link.get("evidence_id"), {}).get("document_id")
                       for link in links if link.get("case_id"))]
    evidence_ids = {r["evidence_id"] for r in links}
    evidence_ids.update(claim.get("evidence_ids", []) if claim else [])
    evidence_ids.update(c["outcome_evidence_id"] for c in cases if c.get("outcome_evidence_id"))
    evidence_ids.update(r["binding_evidence_id"] for r in bindings if r.get("binding_evidence_id"))
    if not evidence_ids <= set(ix["evidence"]):
        raise WritingHandoffError("Pattern references missing source evidence")
    evidence = [ix["evidence"][eid] for eid in evidence_ids]
    doc_ids = {e["document_id"] for e in evidence}
    if not doc_ids <= set(ix["source_documents"]):
        raise WritingHandoffError("Pattern references a missing source snapshot")
    clean = lambda row: {k: v for k, v in row.items() if k != "semantic_review"}
    payload = {"pattern": clean(pattern), "target_claim": clean(claim) if claim else None,
               "target_rounds": _sorted([r for r in bundle.get("opportunity_rounds", [])
                   if (r.get("program_id"), r.get("round_id")) == (pattern.get("program_id"), pattern.get("target_round_id"))]),
               "pattern_evidence": _sorted(links), "case_documents": _sorted(bindings),
               "experience_cases": _sorted(cases), "evidence": _sorted(evidence),
               "source_documents": _sorted([ix["source_documents"][d] for d in doc_ids])}
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def _review_error(row):
    review = row.get("semantic_review") or {}
    if review.get("state") != "accepted":
        return "semantic_review_not_accepted"
    if review.get("basis") not in SEMANTIC:
        return "writing_requires_recorded_semantic_review"
    if any(not isinstance(review.get(k), str) or not review[k].strip()
           for k in ("reviewer", "decision_id", "reason", "review_receipt_ref")):
        return "semantic_review_receipt_missing"
    return None


def _source_error(doc, subject_id):
    if not doc:
        return "source_snapshot_missing"
    if not re.fullmatch(r"[0-9a-f]{64}", doc.get("text_sha256", "")):
        return "source_text_hash_missing"
    if doc.get("capture_mode") == "full_document":
        if not re.fullmatch(r"[0-9a-f]{64}", doc.get("raw_sha256", "")):
            return "source_raw_hash_missing"
    elif doc.get("capture_mode") != "public_web_excerpt":
        return "source_capture_mode_unknown"
    if doc.get("access_scope") == "user_private":
        if not subject_id or doc.get("owner_subject_id") != subject_id or not doc.get("permission_basis"):
            return "private_document_owner_or_permission_mismatch"
    elif doc.get("access_scope") != "public":
        return "source_access_undeclared"
    return None


def _prompt(claim):
    value = claim.get("value")
    if claim.get("field") not in WRITING_FIELDS or not isinstance(value, dict):
        return False
    return any(value.get(k) not in (None, "", False) for k in
               ("prompt", "prompt_text", "question", "text", "max_pages", "max_words", "max_characters", "length"))


def build_writing_handoff(bundle, applicant_facts=None, *, target_opportunity_id=None,
                          required_fields_by_claim=None):
    """Return a copy with deterministic preparation in metadata.writing_result.

    required_fields_by_claim maps a reviewed prompt claim ID to field names or
    {field, question} objects. It is an explicit writing needs contract, never
    an inferred eligibility rule. Applicant facts require a recorded semantic
    review, accepted/final-use status, same subject and experience, and private
    document evidence with matching ownership. Unfilled slots stay questions.
    Existing SQL pattern rows are preserved; this adapter never approves them.
    """
    if bundle.get("contract_version") != "team-handoff-v1":
        raise WritingHandoffError("Expected team-handoff-v1")
    result = deepcopy(bundle)
    subject = result.get("metadata", {}).get("subject_id")
    docs = _index(result, "source_documents", "document_id")
    evidence = _index(result, "evidence", "evidence_id")
    claims = _index(result, "claims", "claim_id")
    cases = _index(result, "experience_cases", "case_id")
    _index(result, "writing_patterns", "pattern_id")
    allowed_rounds = {(r["program_id"], r["round_id"]) for r in result.get("opportunity_rounds", [])
                      if target_opportunity_id is None or r.get("opportunity_id") == target_opportunity_id}
    if target_opportunity_id and not allowed_rounds:
        raise WritingHandoffError("Target opportunity not found")
    output = {"adapter_version": VERSION, "generation_mode": "deterministic_preparation",
              "llm_called": False, "is_finished_application": False,
              "literal_verification": "requires_common_validator_with_artifact_root",
              "preparation_checklist": [], "guidance": [], "draft_slots": [],
              "missing_questions": [], "accepted_pattern_ids": [], "held": []}
    def hold(kind, identifier, reason):
        output["held"].append({"kind": kind, "id": identifier, "reason": reason})
    def check_evidence(eid):
        e = evidence.get(eid)
        if not e or not e.get("quote") or not re.fullmatch(r"L[1-9]\d*(?:-L?[1-9]\d*)?", e.get("locator", "")):
            return "missing_evidence_or_LF_span"
        return _source_error(docs.get(e["document_id"]), subject)
    targets = {}
    for cid, claim in claims.items():
        if (claim.get("program_id"), claim.get("round_id")) not in allowed_rounds:
            continue
        if claim.get("kind") != "submission":
            continue
        reason = _review_error(claim)
        refs = claim.get("evidence_ids", [])
        if not reason and (claim.get("review_state") != "reviewed" or not refs or claim.get("value") is None):
            reason = "submission_claim_not_reviewed_or_source_missing"
        if not reason:
            try:
                from benefitlens_handoff import claim_scope_fingerprint
                if claim.get('semantic_review', {}).get('input_scope_fingerprint') != claim_scope_fingerprint(result, claim):
                    reason = 'claim_input_scope_fingerprint_missing_or_changed'
            except ImportError:
                reason = 'claim_scope_verifier_unavailable'
            except (KeyError, TypeError, ValueError):
                reason = 'claim_scope_fingerprint_invalid'
        for eid in refs:
            reason = reason or check_evidence(eid)
            e = evidence.get(eid, {})
            if e.get("source_role") not in OFFICIAL or (e.get("program_id"), e.get("round_id")) != (claim.get("program_id"), claim.get("round_id")):
                reason = reason or "official_submission_scope_mismatch"
        if reason:
            hold("claim", cid, reason)
            continue
        item = {"guidance_id": "official:" + cid, "target_claim_id": cid, "field": claim["field"],
                "official_value": deepcopy(claim["value"]), "evidence_ids": list(refs),
                "basis": "official_constraint", "causal_success_claim": False}
        output["preparation_checklist"].append(item)
        if _prompt(claim):
            targets[cid] = claim
            output["guidance"].append(deepcopy(item))

    for pattern in result.get("writing_patterns", []):
        pid, cid = pattern["pattern_id"], pattern.get("target_claim_id")
        if (pattern.get("program_id"), pattern.get("target_round_id")) not in allowed_rounds:
            continue
        reason = _review_error(pattern)
        if cid not in targets:
            reason = reason or "target_is_not_reviewed_writing_prompt"
        elif (targets[cid].get('program_id'), targets[cid].get('round_id')) != (pattern.get('program_id'), pattern.get('target_round_id')):
            reason = reason or 'target_prompt_program_or_round_mismatch'
        if pattern.get("causal_success_claim") is not False:
            reason = reason or "causal_success_claim_forbidden"
        if pattern.get("scope_status") not in {"same_round", "historical_transfer", "cross_program", "general", "official_only"}:
            reason = reason or "pattern_transfer_scope_unknown"
        links = [r for r in result.get("pattern_evidence", []) if r.get("pattern_id") == pid]
        if not links:
            reason = reason or "pattern_evidence_missing"
        body = pattern.get("body", {})
        if not isinstance(body, dict):
            body = {}
            reason = reason or "pattern_body_not_object"
        case_links = []
        for link in links:
            eid = link.get("evidence_id")
            reason = reason or check_evidence(eid)
            e = evidence.get(eid, {})
            if (link.get("program_id"), link.get("target_round_id")) != (pattern.get("program_id"), pattern.get("target_round_id")):
                reason = reason or "pattern_target_binding_mismatch"
            if (link.get("source_program_id", e.get("program_id")), link.get("source_round_id", e.get("round_id"))) != (e.get("program_id"), e.get("round_id")):
                reason = reason or "pattern_source_binding_mismatch"
            if link.get("document_id", e.get("document_id")) != e.get("document_id"):
                reason = reason or "pattern_snapshot_mismatch"
            if link.get("purpose") in {"official_constraint", "official_guidance"}:
                if link.get("case_id") or link.get("case_document_role") or e.get("source_role") not in OFFICIAL or (e.get("program_id"), e.get("round_id")) != (pattern.get("program_id"), pattern.get("target_round_id")):
                    reason = reason or "official_pattern_scope_mismatch"
            elif link.get("purpose") == "case_observation":
                case = cases.get(link.get("case_id"))
                role = link.get("case_document_role")
                bindings = [b for b in result.get("case_documents", []) if
                    (b.get("case_id"), b.get("document_id"), b.get("document_role")) ==
                    (link.get("case_id"), e.get("document_id"), role)]
                if not case or not bindings or role not in OBSERVATION_ROLES or e.get("source_role") != role:
                    reason = reason or "case_document_role_binding_mismatch"
                else:
                    binding = bindings[0]
                    if binding.get("program_id") != case.get("program_id") or e.get("program_id") != case.get("program_id"):
                        reason = reason or "case_program_binding_mismatch"
                    if pattern.get("scope_status") in {"same_round", "historical_transfer"} and case.get("program_id") != pattern.get("program_id"):
                        reason = reason or "cross_program_transfer_not_declared"
                    if pattern.get("scope_status") == "same_round" and case.get("experience_round_id") != pattern.get("target_round_id"):
                        reason = reason or "case_round_mismatch"
                    if pattern.get("scope_status") == "official_only":
                        reason = reason or "official_only_contains_case_observation"
                    binding_id = binding.get("binding_evidence_id")
                    reason = reason or check_evidence(binding_id)
                    if evidence.get(binding_id, {}).get("program_id") != case.get("program_id"):
                        reason = reason or "case_binding_evidence_program_mismatch"
                    if binding.get("finality") not in {"unknown", "public_draft", "confirmed_final", "not_applicable"}:
                        reason = reason or "case_finality_unknown_enum"
                    case_links.append((case, binding, e))
            else:
                reason = reason or "pattern_evidence_purpose_unknown"
        if case_links and not body.get("scope_limit"):
            reason = reason or "case_transfer_limit_missing"
        if body.get("comparison_scope") in {"limited_textual_contrast", "same_prompt_final_statements"}:
            if any(b.get("document_role") != "application_statement" for c, b, e in case_links):
                reason = reason or "textual_comparison_requires_statement_roles"
            if not {"selected", "not_selected"} <= {c.get("outcome_status") for c, b, e in case_links}:
                reason = reason or "outcome_comparison_groups_missing"
            if any(c.get("outcome_status") not in {"selected", "not_selected"} for c, b, e in case_links):
                reason = reason or "ambiguous_outcome_comparison"
        if body.get("comparison_scope") == "same_prompt_final_statements":
            if not case_links or any(b.get("document_role") != "application_statement" or b.get("finality") != "confirmed_final"
                    or not b.get("prompt_id") or not c.get("experience_round_id") for c, b, e in case_links):
                reason = reason or "final_statement_comparison_not_bound"
            elif len({(c["experience_round_id"], b["prompt_id"]) for c, b, e in case_links}) != 1:
                reason = reason or "comparison_round_or_prompt_mismatch"
        try:
            current = writing_scope_fingerprint(result, pattern)
            if pattern.get("semantic_review", {}).get("input_scope_fingerprint") != current:
                reason = reason or "input_scope_fingerprint_missing_or_changed"
        except (KeyError, TypeError, ValueError) as exc:
            reason = reason or str(exc)
        if reason:
            hold("writing_pattern", pid, reason)
            continue
        output["accepted_pattern_ids"].append(pid)
        output["guidance"].append({"guidance_id": pid, "target_claim_id": cid,
            "basis": "reviewed_pattern", "scope_status": pattern["scope_status"],
            "body": deepcopy(body), "evidence_ids": [r["evidence_id"] for r in links],
            "source_scopes": [{"case_id": c['case_id'], "document_id": e['document_id'],
                "document_role": b['document_role'], "finality": b['finality'],
                "experience_round_id": c.get('experience_round_id'),
                "outcome_status": c.get('outcome_status')} for c, b, e in case_links],
            "causal_success_claim": False})

    fields_by_claim = required_fields_by_claim or {}
    if not isinstance(fields_by_claim, dict):
        raise WritingHandoffError("required_fields_by_claim must be a mapping")
    for cid in fields_by_claim:
        if cid not in targets:
            hold("required_fields", cid, "field_needs_require_reviewed_writing_prompt")
    facts = []
    seen_facts = set()
    for fact in applicant_facts or []:
        fid = fact.get("fact_id")
        if not fid or fid in seen_facts:
            raise WritingHandoffError("Missing or duplicate applicant fact_id")
        seen_facts.add(fid)
        reason = _review_error(fact)
        if not subject or fact.get("subject_id") != subject or not fact.get("experience_id"):
            reason = reason or "applicant_subject_or_experience_missing"
        if fact.get("review_status") != "accepted" or fact.get("eligible_for_final_use") is not True or fact.get("derivation_type") not in {"user_confirmed", "source_extract", "rule_derived"} or fact.get("value") in (None, ""):
            reason = reason or "applicant_fact_not_confirmed"
        if fact.get("derivation_type") == "rule_derived" and (not fact.get("derived_from_fact_ids") or not fact.get("derivation_rule")):
            reason = reason or "derived_fact_lineage_missing"
        if not fact.get("evidence_ids"):
            reason = reason or "applicant_evidence_missing"
        for eid in fact.get("evidence_ids", []):
            reason = reason or check_evidence(eid)
            e = evidence.get(eid, {})
            d = docs.get(e.get("document_id"), {})
            if d.get("access_scope") != "user_private" or d.get("owner_subject_id") != subject or e.get("source_role") in OFFICIAL:
                reason = reason or "applicant_fact_cannot_borrow_public_or_official_evidence"
            if e.get("experience_id") != fact.get("experience_id"):
                reason = reason or "applicant_evidence_experience_mismatch"
        if reason:
            hold("applicant_fact", fid, reason)
        else:
            facts.append(fact)
    # A derived accepted value cannot silently borrow another experience's
    # parents or survive a missing/held parent. Cycles have no accepted root.
    resolved = {f['fact_id'] for f in facts if f.get('derivation_type') != 'rule_derived'}
    by_fact = {f['fact_id']: f for f in facts}
    for _ in range(len(facts)):
        before = len(resolved)
        for fact in facts:
            parents = fact.get('derived_from_fact_ids', [])
            if fact['fact_id'] in resolved or not parents:
                continue
            if all(p in resolved and by_fact[p]['experience_id'] == fact['experience_id']
                   and by_fact[p]['subject_id'] == fact['subject_id'] for p in parents):
                resolved.add(fact['fact_id'])
        if len(resolved) == before:
            break
    for fact in facts:
        if fact['fact_id'] not in resolved:
            hold('applicant_fact', fact['fact_id'], 'derived_fact_parents_not_accepted_in_same_experience')
    facts = [f for f in facts if f['fact_id'] in resolved]
    for cid, claim in targets.items():
        needs = fields_by_claim.get(cid, [])
        if not isinstance(needs, list) or any(not isinstance(n, (str, dict)) for n in needs):
            raise WritingHandoffError("Each prompt needs an explicit list of fields")
        normalized = [{"field": n, "question": f"Please provide {n} from this experience."} if isinstance(n, str) else n for n in needs]
        if any(not isinstance(n.get("field"), str) or not n["field"].strip() or not n.get("question") for n in normalized):
            raise WritingHandoffError("Field needs require field and question")
        if not normalized:
            output["missing_questions"].append({"target_claim_id": cid, "field": None,
                "experience_id": None, "question": "Select the writing inputs for this official prompt.",
                "reason": "writing_needs_not_specified"})
            continue
        fields = list(dict.fromkeys(n["field"] for n in normalized))
        relevant = [f for f in facts if f.get("field") in fields]
        experiences = sorted({f["experience_id"] for f in relevant}) or [None]
        for exp in experiences:
            selected = [f for f in relevant if f["experience_id"] == exp]
            conflicts = {field for field in fields if len({_canonical(f["value"]) for f in selected if f["field"] == field}) > 1}
            selected = [f for f in selected if f["field"] not in conflicts]
            missing = [f for f in fields if not any(v["field"] == f for v in selected)]
            output["draft_slots"].append({"slot_id": cid + ":" + (exp or "unassigned"),
                "prompt_id": cid, "target_claim_id": cid, "experience_id": exp,
                "guidance_ids": [g["guidance_id"] for g in output["guidance"] if g["target_claim_id"] == cid],
                "fact_ids": [f["fact_id"] for f in selected],
                "fact_values": [{"field": f["field"], "value": deepcopy(f["value"]),
                    "evidence_ids": list(f["evidence_ids"])} for f in selected],
                "missing_fields": missing, "draft_text": None,
                "status": "needs_user_facts" if missing else "facts_ready_for_drafting"})
            for need in normalized:
                if need["field"] in missing:
                    output["missing_questions"].append({"target_claim_id": cid, "field": need["field"],
                        "experience_id": exp, "question": need["question"],
                        "reason": "conflicting_facts" if need["field"] in conflicts else "missing_confirmed_fact"})
    result.setdefault("metadata", {})["writing_result"] = output
    return result
