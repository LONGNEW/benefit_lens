"""Validate synthetic specification records; no search, DB or model calls."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).resolve().parents[2]


def index(rows, key):
    result = {row[key]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError("Duplicate " + key)
    return result


def references(values, records):
    if any(value not in records for value in values):
        raise ValueError("Unknown reference: " + ",".join(values))
    return [records[value] for value in values]


def validate_links(plan):
    evidence = index(plan["evidence"], "evidence_id")
    facts = index(plan["facts"], "fact_id")
    requirements = index(plan["requirements"], "criterion_id")
    guidance = index(plan["guidance_rules"], "guidance_id")
    index(plan["resources"], "resource_id")
    index(plan["assessments"], "assessment_id")
    index(plan["draft_slots"], "slot_id")

    def official(rows):
        if any(row["owner_scope"] != "opportunity"
               or row["owner_id"] != plan["opportunity_id"]
               or row["source_kind"] not in {"official_notice", "official_form"}
               for row in rows):
            raise ValueError("Official evidence belongs to another opportunity or role")

    for fact in facts.values():
        sources = references(fact["evidence_ids"], evidence)
        parents = references(fact["derived_from_fact_ids"], facts)
        if fact["review_status"] == "accepted":
            if any(e["owner_scope"] != "applicant"
                   or e["owner_id"] != plan["subject_id"]
                   or e["identity_status"] != "confirmed" for e in sources):
                raise ValueError("Accepted applicant fact has an unconfirmed subject")
        if fact["eligible_for_final_use"]:
            if fact["field"].startswith("experience.own_") and fact["derivation_type"] != "user_confirmed":
                raise ValueError("Personal contribution requires user confirmation")
            if fact["derivation_type"] == "rule_derived" and any(
                    not p["eligible_for_final_use"] for p in parents):
                raise ValueError("Rule-derived fact uses unaccepted parents")

    def visit(key, stack, done):
        if key in stack:
            raise ValueError("Cyclic fact derivation")
        if key in done:
            return
        stack.add(key)
        for parent in facts[key]["derived_from_fact_ids"]:
            visit(parent, stack, done)
        stack.remove(key)
        done.add(key)

    done = set()
    for key in facts:
        visit(key, set(), done)

    for requirement in requirements.values():
        official(references(requirement["evidence_ids"], evidence))
        if requirement["kind"] == "eligibility" and requirement["interpretation_status"] == "reviewed":
            if not requirement["field"] or requirement["op"] is None:
                raise ValueError("Reviewed eligibility atom has no field or operator")

    for resource in plan["resources"]:
        references([resource["requirement_id"]], requirements)
        sources = references(resource["evidence_ids"], evidence)
        if resource["resolution_status"] == "found":
            if resource["artifact_kind"] in {"official_blank_form", "official_guideline"}:
                official(sources)
                if resource["download_url"] not in {e["url"] for e in sources}:
                    raise ValueError("Found official resource has no captured URL")
            elif resource["artifact_kind"] == "user_issued_record":
                if any(e["owner_scope"] != "applicant"
                       or e["owner_id"] != plan["subject_id"]
                       or e["source_kind"] != "user_document" for e in sources):
                    raise ValueError("Found personal record has no applicant document")

    axes = {"eligibility": "eligibility", "activity": "practical_fit",
            "writing_prompt": "readiness", "application_document": "readiness",
            "selection_criterion": "readiness"}
    for assessment in plan["assessments"]:
        requirement = references([assessment["criterion_id"]], requirements)[0]
        used = references(assessment["fact_ids"], facts)
        references(assessment["evidence_ids"], evidence)
        if assessment["axis"] != axes[requirement["kind"]]:
            raise ValueError("Assessment axis does not match requirement kind")
        if assessment["status"] in {"satisfied", "not_satisfied"}:
            if requirement["interpretation_status"] != "reviewed":
                raise ValueError("Unreviewed requirement used for settled assessment")
            if any(not f["eligible_for_final_use"] for f in used):
                raise ValueError("Unaccepted fact used for settled assessment")
        if assessment["axis"] == "eligibility" and assessment["status"] in {"satisfied", "not_satisfied"}:
            matched = [f for f in used if f["field"] == requirement["field"]]
            if not matched:
                raise ValueError("No matching applicant field")
            if any(f["at"] != requirement["at"] for f in matched):
                raise ValueError("Applicant fact and requirement use different times")

    for rule in guidance.values():
        sources = references(rule["evidence_ids"], evidence)
        if rule["basis"] == "official_constraint":
            official(sources)
        elif not any(e["owner_scope"] == "reference_case"
                     and e["owner_id"] == rule["case_id"] for e in sources):
            raise ValueError("Guidance has no evidence from its case")

    for slot in plan["draft_slots"]:
        prompt = references([slot["prompt_id"]], requirements)[0]
        if prompt["kind"] != "writing_prompt":
            raise ValueError("Draft slot does not refer to a writing prompt")
        references(slot["guidance_ids"], guidance)
        used = references(slot["fact_ids"], facts)
        if any(not f["eligible_for_final_use"] for f in used):
            raise ValueError("Unaccepted fact used in draft")

    for question in plan["next_questions"]:
        references(question["why_requirement_ids"], requirements)


def main():
    schema = json.loads((ROOT / "docs/specs/personalization-plan.schema.json").read_text())
    plan = json.loads((ROOT / "evaluation/spec_examples/personalization-plan.synthetic.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def accepted(value):
        try:
            validator.validate(value)
            validate_links(value)
            return True, None
        except (ValidationError, ValueError) as error:
            return False, str(error).splitlines()[0]

    mutations = [
        ("inferred_fact_final_use", lambda p: p["facts"][1].update(eligible_for_final_use=True)),
        ("conflicted_fact_final_use", lambda p: p["facts"][0].update(review_status="conflicted")),
        ("causal_selection_claim", lambda p: p["guidance_rules"][0].update(causal_success_claim=True)),
        ("official_form_without_url", lambda p: p["resources"][1].update(resolution_status="found")),
        ("unknown_evidence", lambda p: p["facts"][0].update(evidence_ids=["missing-evidence"])),
        ("wrong_or_ambiguous_person", lambda p: p["facts"][0].update(evidence_ids=["E-public"])),
        ("reference_case_as_own_fact", lambda p: p["facts"][2].update(evidence_ids=["E-case"])),
        ("unknown_parent_fact", lambda p: p["facts"][0]["derived_from_fact_ids"].append("missing-fact")),
        ("wrong_opportunity_document", lambda p: p["evidence"][0].update(owner_id="demo-fellowship-2026")),
        ("candidate_fact_in_draft", lambda p: p["draft_slots"][0].update(fact_ids=["F-topic"])),
        ("merit_as_eligibility", lambda p: p["requirements"][0].update(kind="selection_criterion")),
        ("accepted_null_fact", lambda p: p["facts"][0].update(value=None)),
        ("historical_fact_as_current", lambda p: p["facts"][0].update(at="historical")),
        ("ambiguous_requirement_as_settled", lambda p: p["requirements"][0].update(interpretation_status="ambiguous")),
    ]
    ok, error = accepted(plan)
    results = [{"case": "synthetic_valid_plan", "expected_accept": True,
                "actual_accept": ok, "passed": ok, "error": error}]
    for name, mutate in mutations:
        invalid = copy.deepcopy(plan)
        mutate(invalid)
        ok, error = accepted(invalid)
        results.append({"case": name, "expected_accept": False,
                        "actual_accept": ok, "passed": not ok, "error": error})
    report = {"scope": "synthetic_contract_validation_only",
              "external_search_executed": False, "model_calls": 0, "db_writes": 0,
              "runtime_integration": False, "cases": len(results),
              "passed": sum(r["passed"] for r in results), "results": results,
              "not_tested": ["source authenticity", "real same-person matching",
                             "semantic extraction accuracy", "evidence entailment",
                             "actual application eligibility", "draft usefulness"]}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["passed"] != report["cases"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
