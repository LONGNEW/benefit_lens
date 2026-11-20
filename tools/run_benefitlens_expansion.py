"""Validate a hash-pinned base and small expansion overlays without copying the base.

This is an offline composition runner. Collectors own HTTP evidence and its
observed date. Historical writing rounds never enter the current-call count.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from hashlib import sha256
import json
from pathlib import Path

from benefitlens_batch_db import read_json
from benefitlens_common_fields import apply_reviewed_enrichments
from benefitlens_handoff import ARRAYS, MACHINE_FIELDS, review_mapping, to_delivery_rows
from run_benefitlens_handoff import apply_review_patch, execute_handoff, fingerprint, immutable_json

VERSION = "expansion-manifest-v1"
ROLES = {"current_opportunities", "historical_writing"}
ROOT = Path(__file__).resolve().parents[1]
META_ARRAYS = {
    "family_memberships": ("program_id", "round_id"),
    "source_specific": ("program_id", "round_id"),
    "source_rule_checks": ("program_id", "round_id"),
    "normalized_classification_overlay": ("program_id", "round_id"),
}


def file_ref(path):
    path = Path(path).resolve(strict=True)
    with path.open("rb") as stream:
        digest = sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "sha256": digest.hexdigest()}


def pinned_json(ref):
    if not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not Path(ref["path"]).is_absolute():
        raise ValueError("Input references require an absolute path")
    actual = file_ref(ref["path"])
    if actual["sha256"] != ref.get("sha256"):
        raise ValueError("Pinned input hash mismatch: " + ref["path"])
    return read_json(Path(ref["path"]))


def merge_rows(first, second, keys, label):
    merged = {}
    duplicates = 0
    for row in first + second:
        key = tuple(row[k] for k in keys)
        if key in merged:
            if merged[key] != row:
                raise ValueError("Conflicting expansion identity in " + label + ": " + repr(key))
            duplicates += 1
        else:
            merged[key] = row
    return list(merged.values()), duplicates


def merge_bundle(base, extra):
    """Make new containers; never mutate either input or overwrite snapshots."""
    if base.get("contract_version") != "team-handoff-v1" or extra.get("contract_version") != "team-handoff-v1":
        raise ValueError("Expansion requires team-handoff-v1 inputs")
    result = dict(base)
    duplicate_counts = {}
    for name, keys in ARRAYS.items():
        result[name], duplicate_counts[name] = merge_rows(base[name], extra[name], keys, name)
    left, right = base.get("metadata", {}), extra.get("metadata", {})
    result["metadata"] = dict(left)
    subjects = {m["subject_id"] for m in (left, right) if m.get("subject_id")}
    if len(subjects) > 1:
        raise ValueError("Different private subjects need separate handoffs")
    if subjects:
        result["metadata"]["subject_id"] = next(iter(subjects))
    for name, keys in META_ARRAYS.items():
        result["metadata"][name], _ = merge_rows(left.get(name, []), right.get(name, []), keys, "metadata." + name)
    dependencies = dict(left.get("claim_dependencies", {}))
    for key, value in right.get("claim_dependencies", {}).items():
        if key in dependencies and dependencies[key] != value:
            raise ValueError("Conflicting claim dependency " + key)
        dependencies[key] = value
    result["metadata"]["claim_dependencies"] = dependencies
    result["metadata"].pop("writing_result", None)
    return result, duplicate_counts


def current_rounds(bundle, as_of):
    """Only same-day, approved scope/status/period records enter this count."""
    accepted = {r["id"] for r in review_mapping(bundle)["accepted"] if r["entity"] == "claim"}
    fields = {}
    for claim in bundle["claims"]:
        field = claim.get("field")
        value = claim.get("value")
        if not isinstance(value, dict):
            continue
        if field == "student_scope" and value.get("student_scope_verified") is not True:
            continue
        if field == "current_application_status" and (value.get("status") != "open" or value.get("application_period_verified") is not True):
            continue
        if field == "verified_application_period":
            period = value.get("application_period")
            if not isinstance(period, dict) or period.get("verification_state") != "verified":
                continue
        if claim["claim_id"] in accepted and (claim["kind"], field) in MACHINE_FIELDS:
            fields.setdefault((claim["program_id"], claim["round_id"]), set()).add((claim["kind"], claim["field"]))
    return {(r["program_id"], r["round_id"]) for r in bundle["opportunity_rounds"]
            if r["status"] == "open" and r["checked_on"] == as_of
            and (not r.get("opens_on") or r["opens_on"] <= as_of)
            and (not r.get("closes_on") or r["closes_on"] >= as_of)
            and fields.get((r["program_id"], r["round_id"]), set()) == MACHINE_FIELDS}


def family_metrics(bundle, round_keys):
    families = {}
    covered = set()
    by_variant = {}
    for link in bundle.get("metadata", {}).get("family_memberships", []):
        key = link["program_id"], link["round_id"]
        if key not in round_keys:
            continue
        signature = link["opportunity_family_id"], link["review_state"], link.get("rule_version")
        if key in by_variant and by_variant[key] != signature:
            raise ValueError("Conflicting family membership for one variant: " + repr(key))
        by_variant[key] = signature
        covered.add(key)
        families.setdefault(link["opportunity_family_id"], []).append(link["review_state"])
    passed = sum(all(s == "confirmed" for s in states) for states in families.values())
    return {"parent_family_candidates": len(families), "rule_passed_families": passed,
            "review_families": len(families) - passed,
            "variants_without_family_link": len(round_keys - covered)}


def family_ids(bundle, round_keys):
    return {row["opportunity_family_id"] for row in bundle.get("metadata", {}).get("family_memberships", [])
            if (row["program_id"], row["round_id"]) in round_keys}


def regroup_families(bundle, round_keys):
    """Run the same issuer/role policy across base and delta, not each separately."""
    from benefitlens_opportunity_groups import VERSION as GROUP_VERSION, group_opportunities
    entries = [row for row in bundle.get("metadata", {}).get("source_specific", [])
               if (row["program_id"], row["round_id"]) in round_keys]
    if {(e["program_id"], e["round_id"]) for e in entries} != round_keys:
        raise ValueError("Global grouping requires original source fields for every current variant")
    groups, review, duplicates = group_opportunities([e["fields"] for e in entries])
    if duplicates:
        raise ValueError("A source/native identity is repeated across the expansion")
    links = []
    for group in groups:
        for variant in group["variants"]:
            entry = entries[variant["input_line"] - 1]
            links.append({"program_id": entry["program_id"], "round_id": entry["round_id"],
                          "opportunity_family_id": group["group_id"], "rule_version": GROUP_VERSION,
                          "review_state": group["decision"]})
    result = dict(bundle)
    result["metadata"] = dict(bundle.get("metadata", {}))
    result["metadata"]["family_memberships"] = links
    result["metadata"]["expansion_grouping"] = {"rule_version": GROUP_VERSION,
        "cross_input_similar_role_review_count": len(review), "source_variants": len(entries),
        "original_memberships_sha256": fingerprint(bundle.get("metadata", {}).get("family_memberships", []))}
    return result


def apply_family_reviews(bundle, refs):
    """Only lower family confidence using pinned, exact source-bound reviews."""
    if not refs:
        return bundle
    entries = {(r['program_id'], r['round_id']):r['fields']
               for r in bundle.get('metadata', {}).get('source_specific', [])}
    originals = {(c['program_id'], c['round_id']):c['value'].get('posting', {}).get('raw_sha256')
                 for c in bundle['claims'] if c.get('field') == 'verified_application_period'}
    links = bundle.get('metadata', {}).get('family_memberships', [])
    membership = {(r['program_id'], r['round_id']):r for r in links}
    targets, reviews = set(), []
    for ref in refs:
        patches = pinned_json(ref)
        if not isinstance(patches, list):
            raise ValueError('Family reviews require an explicit review list')
        for patch in patches:
            if not patch.get('review_id') or not patch.get('reason') or not patch.get('variants'):
                raise ValueError('Family review lacks its decision or source scope')
            pinned_json(patch['evidence_ref'])
            for variant in patch['variants']:
                key = variant['program_id'], variant['round_id']
                row, link = entries.get(key, {}), membership.get(key, {})
                if (not link or link.get('opportunity_family_id') != variant.get('group_id')
                        or not originals.get(key) or originals[key] != variant.get('raw_sha256')
                        or row.get('source_family') != variant.get('source_family')
                        or str(row.get('native_id') or row.get('id')) != str(variant.get('native_id'))):
                    raise ValueError('Family review source or membership scope mismatch')
                targets.add(link['opportunity_family_id'])
            reviews.append({'review_id':patch['review_id'],'reason':patch['reason'],
                            'evidence_ref':patch['evidence_ref']})
    previous_passed = {r['opportunity_family_id'] for r in links
                       if r['opportunity_family_id'] in targets and r['review_state'] == 'confirmed'}
    result = dict(bundle)
    result['metadata'] = dict(bundle['metadata'])
    result['metadata']['family_memberships'] = [
        dict(r, review_state='review_required') if r['opportunity_family_id'] in targets else dict(r)
        for r in links]
    result['metadata']['family_review_overrides'] = {
        'input_refs':refs, 'reviews':reviews, 'target_group_ids':sorted(targets),
        'newly_downgraded_groups':len(previous_passed), 'family_ids_merged':0}
    return result


def compose_manifest(manifest, _manifest_chain=()):
    allowed = {"contract_version", "as_of", "baseline", "baseline_approval_patch", "semantic_patch", "semantic_patches",
               "deltas", "required_fields", "enrichments", "audit_inputs", "comparison_manifest", "family_review_overrides"}
    if not isinstance(manifest, dict) or set(manifest) - allowed:
        raise ValueError("Unknown expansion manifest fields")
    if not {"contract_version", "as_of", "baseline", "deltas"} <= set(manifest):
        raise ValueError("Missing expansion manifest fields")
    if manifest.get("contract_version") != VERSION:
        raise ValueError("Unsupported expansion manifest")
    identity = fingerprint(manifest)
    if identity in _manifest_chain:
        raise ValueError("Cyclic comparison manifest lineage")
    for key in ("deltas", "required_fields", "enrichments", "audit_inputs", "semantic_patches", "family_review_overrides"):
        if not isinstance(manifest.get(key, []), list) or any(not isinstance(item, dict) for item in manifest.get(key, [])):
            raise ValueError("Manifest " + key + " must contain input reference objects")
    day = manifest.get("as_of")
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("as_of must be an ISO date")
    for ref in manifest.get("audit_inputs", []):
        pinned_json(ref)
    base = pinned_json(manifest["baseline"])
    if manifest.get("baseline_approval_patch"):
        base = apply_review_patch(base, pinned_json(manifest["baseline_approval_patch"]))
    base_keys = {(r["program_id"], r["round_id"]) for r in base["opportunity_rounds"]}
    base_current = current_rounds(base, day)
    regrouped_base = regroup_families(base, base_current)
    base_families = family_metrics(regrouped_base, base_current)
    base_family_ids = family_ids(regrouped_base, base_current)
    result = base
    versions = {str(Path(__file__).relative_to(ROOT)): file_ref(__file__)["sha256"]}
    for item in manifest.get("enrichments", []):
        review_ref = item["review_receipt"]
        pinned_json(review_ref)
        result = apply_reviewed_enrichments(result, pinned_json(item), reviewer=item["reviewer"],
                                           review_receipt_ref=review_ref["path"])
    role_keys = {role: set() for role in ROLES}
    prior_history_keys = set()
    input_lineage, collapsed = [], Counter()
    for item in manifest.get("deltas", []):
        role = item.get("role")
        if role not in ROLES:
            raise ValueError("Unknown expansion input role")
        addition_kind = item.get("addition_kind", "new")
        if addition_kind not in {"new", "prior_validated"}:
            raise ValueError("Unknown expansion lineage")
        extra = pinned_json(item)
        keys = {(r["program_id"], r["round_id"]) for r in extra["opportunity_rounds"]}
        if any(existing_keys & keys for existing_role, existing_keys in role_keys.items() if existing_role != role):
            raise ValueError("One round cannot be both current and historical writing")
        if role == "historical_writing" and any(r["status"] == "open" for r in extra["opportunity_rounds"]):
            raise ValueError("Historical writing input contains an open round")
        if role == "historical_writing" and keys & base_current:
            raise ValueError("Historical writing round conflicts with a baseline current round")
        role_keys[role].update(keys)
        if role == "historical_writing" and addition_kind == "prior_validated":
            prior_history_keys.update(keys)
        result, duplicates = merge_bundle(result, extra)
        collapsed.update(duplicates)
        input_lineage.append({"path": item["path"], "sha256": item["sha256"], "role": role,
                              "addition_kind": addition_kind,
                              "bundle_id": extra["bundle_id"], "metadata_sha256": fingerprint(extra["metadata"])})
    semantic_refs = ([manifest["semantic_patch"]] if manifest.get("semantic_patch") else []) + manifest.get("semantic_patches", [])
    if semantic_refs:
        from benefitlens_semantic_expansion import apply_semantic_patch
        for ref in semantic_refs:
            result = apply_semantic_patch(result, pinned_json(ref))
        versions["tools/benefitlens_semantic_expansion.py"] = file_ref(ROOT / "tools/benefitlens_semantic_expansion.py")["sha256"]
    verified_current = current_rounds(result, day)
    # A new current-input round is counted only after the three approved gates.
    unverified_new = role_keys["current_opportunities"] - base_keys - verified_current
    if unverified_new:
        raise ValueError("New current input lacks approved scope/status/period: " + repr(sorted(unverified_new)))
    added_keys = verified_current - base_keys
    if added_keys:
        from run_benefitlens_batch import validate_open_cohort
        rows = to_delivery_rows(result, Path("/"))
        rows["opportunity_round"] = [r for r in rows["opportunity_round"]
            if (r["program_id"], r["round_id"]) in added_keys]
        validate_open_cohort(rows, {r["opportunity_id"]: "source_backed" for r in rows["opportunity_round"]}, date.fromisoformat(day))
    result = regroup_families(result, verified_current)
    result = apply_family_reviews(result, manifest.get('family_review_overrides', []))
    required = {}
    for ref in manifest.get("required_fields", []):
        for key, fields in pinned_json(ref).items():
            if key in required and required[key] != fields:
                raise ValueError("Conflicting writing input requirements: " + key)
            required[key] = fields
    comparison_keys, comparison_current = base_keys, base_current
    comparison_families, comparison_family_ids = base_families, base_family_ids
    if manifest.get("comparison_manifest"):
        previous_manifest = pinned_json(manifest["comparison_manifest"])
        if previous_manifest.get("as_of") != day:
            raise ValueError("Comparison manifest must have the same observation date")
        for anchor in ("baseline", "baseline_approval_patch"):
            if previous_manifest.get(anchor) != manifest.get(anchor):
                raise ValueError("Comparison manifest uses a different reconstruction anchor")
        previous, previous_required, _ = compose_manifest(previous_manifest, (*_manifest_chain, identity))
        if any(ref not in manifest.get('family_review_overrides', [])
               for ref in previous_manifest.get('family_review_overrides', [])):
            raise ValueError('Comparison lineage drops prior family reviews')
        # Preserve every previously carried fact and source snapshot. A comparison
        # reference alone must not hide a dropped or rewritten prior input.
        for name, keys in ARRAYS.items():
            index = {tuple(row[k] for k in keys): row for row in result[name]}
            if any(index.get(tuple(row[k] for k in keys)) != row for row in previous[name]):
                raise ValueError("Comparison lineage drops or changes prior " + name)
        for name in ("source_specific",):
            keys = META_ARRAYS[name]
            index = {tuple(row[k] for k in keys): row for row in result["metadata"].get(name, [])}
            if any(index.get(tuple(row[k] for k in keys)) != row for row in previous["metadata"].get(name, [])):
                raise ValueError("Comparison lineage drops or changes prior " + name)
        if any(required.get(key) != value for key, value in previous_required.items()):
            raise ValueError("Comparison lineage drops or changes prior writing requirements")
        comparison_keys = {(r["program_id"], r["round_id"]) for r in previous["opportunity_rounds"]}
        comparison_current = current_rounds(previous, day)
        comparison_families = family_metrics(previous, comparison_current)
        comparison_family_ids = family_ids(previous, comparison_current)
    current_metrics = family_metrics(result, verified_current)
    versions["tools/benefitlens_opportunity_groups.py"] = file_ref(ROOT / "tools/benefitlens_opportunity_groups.py")["sha256"]
    metadata = result["metadata"] = dict(result["metadata"])
    summary = {"as_of": day, "baseline_source_variants": len(comparison_current) if manifest.get("comparison_manifest") else len(base_keys),
        "reconstruction_baseline_rounds": len(base_keys),
        "comparison_manifest": manifest.get("comparison_manifest"),
        "baseline_current_rule_verified_variants": len(comparison_current),
        "current_rule_verified_variants": len(verified_current),
        "new_current_variants": len(verified_current - comparison_keys),
        "new_current_periods_rechecked_from_originals": len(verified_current - comparison_keys),
        "all_incremental_current_periods_rechecked_from_originals": len(added_keys),
        "historical_writing_rounds_added": len(role_keys["historical_writing"] - base_keys),
        "historical_writing_rounds_carried_forward": len(role_keys["historical_writing"] & comparison_keys) if manifest.get("comparison_manifest") else len(prior_history_keys),
        "historical_writing_rounds_new_in_this_expansion": len(role_keys["historical_writing"] - comparison_keys - prior_history_keys),
        "baseline_family_metrics": comparison_families, "current_family_metrics": current_metrics,
        "new_parent_family_candidates": len(family_ids(result, verified_current) - comparison_family_ids),
        "baseline_family_candidates_removed": len(comparison_family_ids - family_ids(result, verified_current)),
        "family_metrics_scope": "current rule-verified variants only, globally regrouped across all inputs",
        "exact_input_rows_collapsed": dict(collapsed),
        "unique_semantic_opportunity_count": None,
        "scope": "same-day rule-verified source variants and issuer-role family candidates; not personal eligibility"}
    metadata.update(expansion_summary=summary, expansion_inputs=input_lineage, expansion_code_versions=versions,
                    expansion_manifest_sha256=fingerprint(manifest), observation_only=False, fresh_web_check=False)
    # The runner makes no HTTP calls. Each collector's observation is retained in its pinned input.
    metadata["counts"] = {"processed_source_postings": len(verified_current), **current_metrics}
    result["bundle_id"] = "expanded-handoff-" + fingerprint([manifest, versions])[:24]
    result["producer"] = "expansion_coordinator"
    result["created_at"] = max([base["created_at"]] + [pinned_json(item)["created_at"] for item in manifest.get("deltas", [])])
    return result, required, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest_ref = file_ref(args.manifest)
        bundle, required, summary = compose_manifest(pinned_json(manifest_ref))
        receipt = execute_handoff(bundle, args.state, artifact_root=Path("/"), required_fields_by_claim=required,
            check_only=True, input_ref={**manifest_ref, "semantic_bundle_sha256": fingerprint(bundle),
                                      "reference_kind": VERSION})
        output = {"contract_version": VERSION, "manifest": manifest_ref,
                  "run": receipt["run"], "receipt": str(Path(receipt["run"]) / "receipt.json"),
                  "cache_hit": receipt["cache_hit"], "expansion": summary,
                  "review": receipt["review"], "projected_rows": receipt["projected_rows"],
                  "literal_checks": receipt["validation"]["literal_checks"]}
        immutable_json(Path(receipt["run"]) / "expansion.json", output | {"cache_hit": False})
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, "benefitlens_expansion: " + str(error) + "\n")


if __name__ == "__main__":
    main()
