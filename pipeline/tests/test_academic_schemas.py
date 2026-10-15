#!/usr/bin/env python3
"""Replay the previously executed academic/writing schema checks, read-only.

The 19 writing cases below copy the two earlier in-session Python invocations:
the initial two valid inputs and 14 mutations, followed by one valid length
input and its two sequential mutations. The three previously unnamed valid
cases have descriptive report names; their input values are unchanged.
No source download, document reinterpretation, database or UI-copy write occurs.
"""

import argparse
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest_file(path):
    return digest_bytes(Path(path).read_bytes())


def json_digest(value):
    return digest_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def errors_for(validator, value):
    return [
        {
            "path": list(error.absolute_path),
            "schema_path": list(error.absolute_schema_path),
            "message": error.message,
        }
        for error in validator.iter_errors(value)
    ]


def result_case(name, validator, value, expected, origin, mutation=None):
    errors = errors_for(validator, value)
    actual = not errors
    return {
        "name": name,
        "origin": origin,
        "mutation": mutation,
        "input": value,
        "input_sha256": json_digest(value),
        "expected_valid": expected,
        "actual_valid": actual,
        "passed": actual == expected,
        "errors": errors,
    }


def definition_validator(schema, name):
    return Draft202012Validator(
        {"$schema": "https://json-schema.org/draft/2020-12/schema",
         "$ref": "#/$defs/" + name, "$defs": schema["$defs"]},
        format_checker=FormatChecker(),
    )


def replay_academic_profiles(schema):
    # Exact values supplied from the parent's original execution, not new cases.
    valid = [
        {},
        {"education_path": "undergraduate", "source_path": "undergraduate_enrolled",
         "grades": [{"metric": "percentile", "score": 90, "scale": 100,
                     "period": "all_semesters", "context": "current_program"}]},
        {"english": [{"test_type": "OPIc", "score": "IM3", "test_date": "2025-04-16",
                      "score_scale": "opic_grade"}]},
    ]
    invalid = [
        {"english": [{"test_type": "TOEIC", "score": "850", "test_date": "2025-04-16"}]},
        {"english": [{"test_type": "TOEIC", "score": 850, "test_date": "2025-02-30"}]},
        {"grades": [{"metric": "percentile", "score": 101, "scale": 100,
                     "period": "all", "context": "all"}]},
        {"education_path": "unknown"},
        {"english_inventory_complete": "true"},
    ]
    names = ["빈 부분 프로필", "학부 백분위 프로필", "OPIc 문자열 등급 프로필",
             "TOEIC 숫자 점수의 문자열 타입 거부", "존재하지 않는 응시일 거부",
             "백분위 범위 및 성적 문맥 오류 거부", "미지원 교육경로 값 거부",
             "완전성 플래그의 문자열 타입 거부"]
    validator = definition_validator(schema, "Profile")
    inputs = [(value, True) for value in valid] + [(value, False) for value in invalid]
    return [result_case(name, validator, value, expected,
                        "parent-supplied exact original profile input")
            for name, (value, expected) in zip(names, inputs)]


def replay_writing_cases(schema):
    # First original invocation: its exact base, valid-unofficial input and lambdas.
    v = Draft202012Validator(schema, format_checker=FormatChecker())
    rights = {"status": "unknown", "verified_range": "unknown",
              "public_url_is_permission": False, "basis": None}
    source = {"id": "synthetic-official", "source_kind": "official_notice",
              "title": "합성 검증용 출처 메타데이터", "url": "https://example.invalid/notice",
              "published_at": None, "checked_at": None, "document_version": None,
              "verification_scope": "형식 검증만 수행한 합성 입력", "rights_review": rights}
    record = {"record_id": "synthetic-reference",
              "notice": {"notice_id": "synthetic-notice", "title": "합성 검증용 공고"},
              "round": {"round_id": "synthetic-round", "label": "합성 회차", "academic_path": "unknown"},
              "collection_policy": {"real_student_accepted_essay_collection": "excluded",
                                    "full_self_essay_storage": "not_retained",
                                    "unofficial_sources_use": "context_only"},
              "official_sources": [source], "unofficial_sources": [], "official_forms": [],
              "questions": [], "evidence": [], "ai_writing_policy": "unknown", "policy_source": [],
              "policy_review": {"status": "not_reviewed",
                                "scope": "검토 범위를 아직 확정하지 않은 합성 입력", "checked_at": None},
              "automatic_eligibility_use": False}
    bundle = {"schema_version": "0.1.0", "records": [record]}
    results = [result_case("초기 합성 기본 참조 레코드", v, copy.deepcopy(bundle), True,
                           "original invocation 1: unnamed valid base assertion")]
    cases = []

    def invalid(label, fn, mutation):
        c = copy.deepcopy(bundle)
        fn(c["records"][0])
        cases.append((label, c, mutation))

    invalid("AI 정책 허용 추정값 거부", lambda r: r.update(ai_writing_policy="probably_allowed"),
            "record.ai_writing_policy = probably_allowed")
    invalid("AI 정책 미기재에 출처 없는 입력 거부", lambda r: r.update(ai_writing_policy="not_stated"),
            "record.ai_writing_policy = not_stated; policy_source remains []")
    invalid("미확인 권리의 배포 승인 거부", lambda r: r["official_sources"][0]["rights_review"].update(verified_range="redistribution"),
            "rights_review.verified_range = redistribution while status remains unknown")
    invalid("권리 검증에 명시 근거 없는 입력 거부", lambda r: r["official_sources"][0]["rights_review"].update(status="verified_scope", verified_range="short_quote"),
            "rights_review.status = verified_scope; verified_range = short_quote; basis remains null")
    invalid("공개 URL을 권리 승인으로 표시하는 입력 거부", lambda r: r["official_sources"][0]["rights_review"].update(public_url_is_permission=True),
            "rights_review.public_url_is_permission = true")
    invalid("학생 자기소개서 본문 필드 거부", lambda r: r.update(full_self_essay="합성 문자열"),
            "record.full_self_essay = 합성 문자열 (unsupported field)")
    invalid("실제 합격글 수집 범위 변경 거부", lambda r: r["collection_policy"].update(real_student_accepted_essay_collection="included"),
            "collection_policy.real_student_accepted_essay_collection = included")
    invalid("참조 자료의 자동 자격 사용 거부", lambda r: r.update(automatic_eligibility_use=True),
            "record.automatic_eligibility_use = true")
    q = {"id": "synthetic-question", "title": "합성 문항 메타데이터", "text": None,
         "wording_status": "unknown",
         "length_limit": {"status": "unknown", "unit": "unknown", "whitespace_included": "unknown",
                          "min": None, "max": None, "exact": None, "evidence_refs": []},
         "evidence_refs": [], "fact_slots": []}
    invalid("미확인 한도에 임의 수치 입력 거부", lambda r: (r["questions"].append(copy.deepcopy(q)), r["questions"][0]["length_limit"].update(max=1)),
            "append original q; q.length_limit.max = 1 while status remains unknown")
    invalid("한도 known에 수치 없는 입력 거부", lambda r: (r["questions"].append(copy.deepcopy(q)), r["questions"][0]["length_limit"].update(status="known", unit="characters", evidence_refs=["synthetic-evidence"])),
            "append original q; length status known, unit characters, evidence synthetic-evidence; all bounds remain null")
    invalid("공식 문항 검증 요약의 무근거 입력 거부", lambda r: (r["questions"].append(copy.deepcopy(q)), r["questions"][0].update(wording_status="verified_summary", text="합성 문항 요약")),
            "append original q; wording_status verified_summary and text 합성 문항 요약; evidence_refs remains []")
    e = {"id": "synthetic-evidence", "source_id": "synthetic-official", "url": "https://example.invalid/notice",
         "page": None, "round": "synthetic-round", "location": "합성 위치"}
    invalid("짧은 인용과 전문 참조 동시 저장 거부", lambda r: (r["evidence"].append(copy.deepcopy(e)), r["evidence"][0].update(excerpt={"kind": "short_quote", "text": "합성 인용", "ref": "synthetic-path", "media_type": "text/plain"})),
            "append original e; excerpt mixes short_quote text and content_ref ref/media_type")
    u = copy.deepcopy(source)
    u.update(source_kind="success_account", capture_mode="metadata_only",
             automatic_eligibility_evidence=False, use_scope="context_only", scope_alignment="different_round")
    valid_unofficial = copy.deepcopy(bundle)
    valid_unofficial["records"][0]["unofficial_sources"] = [u]
    results.append(result_case("합성 수기 메타데이터 참조", v, copy.deepcopy(valid_unofficial), True,
                               "original invocation 1: unnamed valid_unofficial assertion"))
    invalid("다른 회차 수기의 자동 자격 근거 사용 거부", lambda r: (r["unofficial_sources"].append(copy.deepcopy(u)), r["unofficial_sources"][0].update(automatic_eligibility_evidence=True)),
            "append original u; automatic_eligibility_evidence = true")
    invalid("수기에 본문 보존 경로 추가 거부", lambda r: (r["unofficial_sources"].append(copy.deepcopy(u)), r["unofficial_sources"][0].update(local_file="synthetic-essay.txt")),
            "append original u; local_file = synthetic-essay.txt (unsupported for unofficial source)")
    for label, c, mutation in cases:
        results.append(result_case(label, v, c, False,
                                   "original invocation 1: exact original mutation", mutation))

    # Second original invocation: the same base, then the same two sequential mutations.
    v = definition_validator(schema, "lengthLimit")
    base = {"status": "known", "unit": "characters", "whitespace_included": "unknown",
            "min": None, "max": 1, "exact": None, "evidence_refs": ["synthetic-evidence"],
            "counting_method": "unicode_code_points"}
    results.append(result_case("문자 단위와 코드포인트 계수 조합", v, copy.deepcopy(base), True,
                               "original invocation 2: unnamed valid length assertion"))
    base["counting_method"] = "encoded_bytes"
    results.append(result_case("문자 단위와 바이트 계수 모순 거부", v, copy.deepcopy(base), False,
                               "original invocation 2: exact sequential mutation",
                               "base.counting_method = encoded_bytes"))
    base.update(status="not_applicable", unit="unknown", max=None,
                evidence_refs=[], counting_method="unknown")
    results.append(result_case("근거 없는 길이 적용 제외 거부", v, copy.deepcopy(base), False,
                               "original invocation 2: exact sequential mutation",
                               "same base after byte mutation: status not_applicable; unit unknown; max null; evidence_refs []; counting_method unknown"))
    assert len(results) == 19
    assert sum(case["expected_valid"] for case in results) == 3
    return results


def collect_reference_errors(value, evidence_ids, location):
    errors = []
    if isinstance(value, dict):
        for key, child in value.items():
            here = location + [key]
            if key in ("evidence_ids", "domain_evidence_ids"):
                for eid in child:
                    if eid not in evidence_ids:
                        errors.append({"path": here, "kind": "missing_evidence", "id": eid})
            else:
                errors.extend(collect_reference_errors(child, evidence_ids, here))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(collect_reference_errors(child, evidence_ids, location + [index]))
    return errors


def source_hash_result(source_id, path, expected, origin, record_id, base=None):
    if path is not None:
        path = Path(path)
        if not path.is_absolute():
            path = base / path
        path = path.resolve()
    actual = digest_file(path) if path and path.is_file() else None
    return {"record_id": record_id, "source_id": source_id,
            "path": str(path) if path else None, "path_origin": origin,
            "expected_sha256": expected, "actual_sha256": actual,
            "expected_match": True, "actual_match": actual == expected and actual is not None,
            "passed": actual == expected and actual is not None}


def academic_semantics(notices, academic_data_path, inventory):
    results, hash_checks = [], []
    mapped = {}
    for row in inventory.get("source_hash_checks", []):
        if row.get("path"):
            mapped.setdefault(row["source_id"], []).append(row)
    notice_ids = [n["id"] for n in notices]
    for n in notices:
        rid = n["id"]
        source = n["ruleSet"]["source"]
        evidence = source["evidence"]
        ids = {e["id"] for e in evidence}
        errors = []
        if len(ids) != len(evidence):
            errors.append({"kind": "duplicate_evidence_ids"})
        if notice_ids.count(rid) != 1:
            errors.append({"kind": "duplicate_notice_ids", "id": rid})
        errors.extend(collect_reference_errors(n["ruleSet"], ids, ["ruleSet"]))
        expected = source.get("sha256")
        if expected:
            candidates = [row for row in mapped.get(source["source_id"], [])
                          if row.get("expected_sha256") == expected]
            local = source.get("local_file")
            if local:
                origin = "academic source.local_file relative to data JSON directory"
            elif len({row["path"] for row in candidates}) == 1:
                local = candidates[0]["path"]
                origin = "existing db/academic-demo.report.json path inventory only; bytes independently rehashed"
            else:
                origin = "no unambiguous preserved-source path for declared SHA256"
            hash_checks.append(source_hash_result(source["source_id"], local, expected,
                                                  origin, rid, academic_data_path.parent))
        results.append({"name": "academic_source_and_evidence_references", "record_id": rid,
                        "expected_valid": True, "actual_valid": not errors,
                        "passed": not errors, "errors": errors})
    return results, hash_checks


def writing_semantics(data, data_path):
    # Reproduction of the earlier writing-reference semantic/read-only hash check.
    results, hash_checks = [], []
    record_ids = [r["record_id"] for r in data["records"]]
    for r in data["records"]:
        rid = r["record_id"]
        official = {x["id"]: x for x in r["official_sources"]}
        all_sources = r["official_sources"] + r["unofficial_sources"]
        ev = {x["id"]: x for x in r["evidence"]}
        qids = {x["id"] for x in r["questions"]}
        round_id = r["round"]["round_id"]
        errors = []
        if record_ids.count(rid) != 1:
            errors.append({"kind": "duplicate_record_ids", "id": rid})
        for key in ["official_sources", "unofficial_sources", "evidence", "questions", "official_forms"]:
            xs = r[key]
            if len({x["id"] for x in xs}) != len(xs):
                errors.append({"kind": "duplicate_ids", "collection": key})
        if len({x["id"] for x in all_sources}) != len(all_sources):
            errors.append({"kind": "source_id_collision"})

        def refs(values, label):
            for eid in values:
                if eid not in ev:
                    errors.append({"kind": "missing_evidence", "location": label, "id": eid})
                elif ev[eid]["source_id"] not in official:
                    errors.append({"kind": "unofficial_rule_evidence", "location": label, "id": eid})

        for e in r["evidence"]:
            if e["source_id"] not in official:
                errors.append({"kind": "unknown_or_unofficial_source", "id": e["id"]})
            if e["round"] != round_id:
                errors.append({"kind": "evidence_round_mismatch", "id": e["id"]})
            source = official.get(e["source_id"])
            if source and e["url"] != source["url"]:
                errors.append({"kind": "evidence_url_mismatch", "id": e["id"]})
            if source and e["page"] and source.get("page_count") and e["page"] > source["page_count"]:
                errors.append({"kind": "page_exceeds_document", "id": e["id"]})
        for q in r["questions"]:
            refs(q["evidence_refs"], q["id"])
            refs(q["length_limit"]["evidence_refs"], q["id"] + ".length_limit")
            ll = q["length_limit"]
            if ll["min"] is not None and ll["max"] is not None and ll["min"] > ll["max"]:
                errors.append({"kind": "min_greater_than_max", "id": q["id"]})
            if len({x["id"] for x in q["fact_slots"]}) != len(q["fact_slots"]):
                errors.append({"kind": "slot_id_collision", "id": q["id"]})
        if "writing_constraints" in r:
            refs(r["writing_constraints"]["evidence_refs"], "writing_constraints")
        for p in r["policy_source"]:
            if p["source_id"] not in official:
                errors.append({"kind": "unknown_or_unofficial_policy_source", "id": p["source_id"]})
            if p["round"] != round_id:
                errors.append({"kind": "policy_round_mismatch", "id": p["source_id"]})
            if p.get("evidence_ref"):
                refs([p["evidence_ref"]], "policy_source")
        for form in r["official_forms"]:
            for sid in form["source_ids"]:
                if sid not in official:
                    errors.append({"kind": "unknown_or_unofficial_form_source", "id": form["id"], "source_id": sid})
            for qid in form["question_ids"]:
                if qid not in qids:
                    errors.append({"kind": "unknown_form_question", "id": form["id"], "question_id": qid})
        for source in r["official_sources"]:
            if source.get("local_file"):
                hash_checks.append(source_hash_result(
                    source["id"], source["local_file"], source["sha256"],
                    "source.local_file relative to containing data JSON directory", rid, data_path.parent))
        results.append({"name": "writing_source_round_question_form_references", "record_id": rid,
                        "expected_valid": True, "actual_valid": not errors,
                        "passed": not errors, "errors": errors})
    return results, hash_checks


def resolve_cli_path(value, project, default):
    path = Path(value) if value else project / default
    if not path.is_absolute():
        path = project / path
    return path.resolve()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--academic-data")
    parser.add_argument("--writing-data")
    parser.add_argument("--academic-schema")
    parser.add_argument("--writing-schema")
    parser.add_argument("--out", required=True, help="Report JSON path; no input/source file may be overwritten")
    args = parser.parse_args()
    project = Path(args.project_root).resolve()
    fixture = "pipeline/tests/gold/academic_language/2026-10-04/"
    paths = {
        "academic_data": resolve_cli_path(args.academic_data, project, fixture + "academic-demo-notices.json"),
        "writing_data": resolve_cli_path(args.writing_data, project, fixture + "writing_resources/writing-resources.json"),
        "academic_schema": resolve_cli_path(args.academic_schema, project, "pipeline/schemas/academic-comparison.schema.json"),
        "writing_schema": resolve_cli_path(args.writing_schema, project, "pipeline/schemas/writing-reference.schema.json"),
    }
    out = Path(args.out).resolve()
    inventory_path = paths["academic_data"].parent / "db/academic-demo.report.json"
    protected = set(paths.values()) | {Path(__file__).resolve(), inventory_path.resolve()}
    if out in protected:
        parser.error("--out must not overwrite an input, inventory or the test script")
    report = {
        "report_type": "benefitlens_academic_writing_schema_exact_replay",
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project), "format_checker": True,
        "writing_case_provenance": "Exact inputs/mutations from the two earlier in-session tool calls; no session-file read or new invented case",
        "academic_case_provenance": "Exact 3 valid and 5 invalid original profile inputs supplied by parent",
        "local_file_relative_anchor": "containing data JSON directory",
        "web_ui_copy_validated": False, "source_reinterpretation_performed": False,
        "input_files": [], "schema_checks": [], "academic_profile_cases": [],
        "writing_replay_cases": [], "actual_data_cases": [],
        "bundle_validation": [], "semantic_checks": [], "source_hash_checks": [],
        "input_unchanged_checks": [], "all_passed": False,
        "scope_limits": [
            "Schema and reference/hash checks do not establish source authenticity, complete reading, rights or policy interpretation.",
            "No eligibility, award, selection or submission permission is established.",
            "Academic primary-source locations use an existing DB report only as a path inventory; source bytes are independently rehashed against the academic source SHA256.",
            "Writing-source metadata without a local_file is not a preserved-file hash check.",
        ],
    }
    try:
        loaded = {}
        for role, path in paths.items():
            raw = path.read_bytes()
            loaded[role] = json.loads(raw)
            report["input_files"].append({"role": role, "path": str(path), "sha256": digest_bytes(raw)})
        inventory = {}
        if inventory_path.is_file():
            raw = inventory_path.read_bytes()
            inventory = json.loads(raw)
            report["input_files"].append({"role": "academic_source_path_inventory", "path": str(inventory_path.resolve()), "sha256": digest_bytes(raw)})
        report["input_files"].append({"role": "test_script", "path": str(Path(__file__).resolve()), "sha256": digest_file(__file__)})
        for name in ("academic_schema", "writing_schema"):
            errors = []
            try:
                Draft202012Validator.check_schema(loaded[name])
            except Exception as exc:
                errors.append(str(exc))
            report["schema_checks"].append({"name": name + "_draft202012", "expected_valid": True,
                                             "actual_valid": not errors, "passed": not errors, "errors": errors})
        if not all(c["passed"] for c in report["schema_checks"]):
            raise ValueError("A schema failed draft 2020-12 meta-validation")
        report["academic_profile_cases"] = replay_academic_profiles(loaded["academic_schema"])
        report["writing_replay_cases"] = replay_writing_cases(loaded["writing_schema"])
        academic = loaded["academic_data"]
        writing = loaded["writing_data"]
        academic_validator = Draft202012Validator(loaded["academic_schema"], format_checker=FormatChecker())
        writing_validator = Draft202012Validator(loaded["writing_schema"], format_checker=FormatChecker())
        report["bundle_validation"] = [
            result_case("academic_actual_array", academic_validator, academic, True, "actual pipeline original"),
            result_case("writing_actual_bundle", writing_validator, writing, True, "actual pipeline original"),
        ]
        av = definition_validator(loaded["academic_schema"], "Notice")
        wv = definition_validator(loaded["writing_schema"], "record")
        for value in academic:
            case = result_case("academic_notice:" + value["id"], av, value, True, "actual pipeline original")
            case.pop("input")
            report["actual_data_cases"].append(case)
        for value in writing["records"]:
            case = result_case("writing_record:" + value["record_id"], wv, value, True, "actual pipeline original")
            case.pop("input")
            report["actual_data_cases"].append(case)
        # Source data is identified by path/hash, not duplicated into the result report.
        for case in report["bundle_validation"]:
            case.pop("input")
        ac, ah = academic_semantics(academic, paths["academic_data"], inventory)
        wc, wh = writing_semantics(writing, paths["writing_data"])
        report["semantic_checks"] = ac + wc
        report["source_hash_checks"] = ah + wh
        for row in report["source_hash_checks"]:
            if row["path"]:
                protected.add(Path(row["path"]).resolve())
        if out in protected:
            raise ValueError("--out would overwrite a preserved original source")
        for item in report["input_files"]:
            actual = digest_file(item["path"])
            report["input_unchanged_checks"].append({"role": item["role"], "path": item["path"],
                                                     "expected_sha256": item["sha256"], "actual_sha256": actual,
                                                     "passed": item["sha256"] == actual})
        groups = [report[key] for key in ("schema_checks", "academic_profile_cases", "writing_replay_cases",
                  "bundle_validation", "actual_data_cases", "semantic_checks", "source_hash_checks", "input_unchanged_checks")]
        report["all_passed"] = all(check["passed"] for group in groups for check in group)
    except Exception as exc:
        report["fatal_error"] = {"type": type(exc).__name__, "message": str(exc)}
        report["all_passed"] = False
    report["counts"] = {
        "academic_profile_cases": len(report["academic_profile_cases"]),
        "academic_profile_expected_valid": sum(c["expected_valid"] for c in report["academic_profile_cases"]),
        "academic_profile_expected_rejected": sum(not c["expected_valid"] for c in report["academic_profile_cases"]),
        "writing_replay_cases": len(report["writing_replay_cases"]),
        "writing_expected_valid": sum(c["expected_valid"] for c in report["writing_replay_cases"]),
        "writing_expected_rejected": sum(not c["expected_valid"] for c in report["writing_replay_cases"]),
        "synthetic_regression_cases": len(report["academic_profile_cases"]) + len(report["writing_replay_cases"]),
        "strict_actual_data_records": len(report["actual_data_cases"]),
        "strict_academic_notices": sum(c["name"].startswith("academic_notice:") for c in report["actual_data_cases"]),
        "strict_writing_records": sum(c["name"].startswith("writing_record:") for c in report["actual_data_cases"]),
        "semantic_record_checks": len(report["semantic_checks"]),
        "source_hash_comparisons": len(report["source_hash_checks"]),
    }
    if out in protected:
        parser.error("Refusing to overwrite protected input/source file with report")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "all_passed": report["all_passed"], "counts": report["counts"],
                      "fatal_error": report.get("fatal_error")}, ensure_ascii=False, indent=2))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
