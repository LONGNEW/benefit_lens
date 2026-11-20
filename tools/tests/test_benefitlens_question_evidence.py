"""Synthetic contract tests."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_question_evidence import (
    SCHEMA_PATH, QuestionEvidenceError, locate_quote, normalize_source_text,
    question_evidence_fingerprint, source_text_sha256, stamp_question_review,
    validate_question_evidence,
)


def fixture():
    text = "Synthetic advice\nDescribe your own action.\nExplain changes.\n"
    quote = "Describe your own action."
    docs = [{"document_id": "answer-17", "url": "https://example.org/questions/12#answer-17",
             "text_ref": "cache://answer-17.txt.gz", "text_sha256": source_text_sha256(text),
             "body_scope": "full_answer", "publisher_role": "community"}]
    row = {"evidence_id": "action-advice", "document_id": "answer-17",
           "question_intent": "personal_contribution", "source_role": "public_advice",
           "claim_type": "recommendation", "quote": quote, "locator": locate_quote(text, quote),
           "original_question": {"id": "12", "text": "Describe a project?",
                                 "context": "Synthetic discussion."},
           "exact_prompt": None,
           "applicability": {"program_ids": [], "round_ids": [], "stages": [],
                             "limitations": ["General advice."]},
           "inference": {"label": "source_explicit", "statement": "Describe own action."},
           "semantic_review": {"state": "pending"}}
    return {"version": "writing-question-evidence-v1", "source_documents": docs,
            "question_evidence": [row]}, {"answer-17": text}


def accepted(bundle):
    return stamp_question_review(bundle, "action-advice", {
        "state": "accepted", "basis": "agent_semantic_review", "reviewer": "synthetic-test",
        "decision_id": "test-1", "reason": "Synthetic advice supports the recommendation.",
        "review_receipt_ref": "fixture://review-1", "reviewed_at": "2026-11-17T00:00:00Z"})


def test_schema_is_valid_and_small():
    source = SCHEMA_PATH.read_text(encoding="utf-8")
    Draft202012Validator.check_schema(json.loads(source))
    assert len(source) < 10000 and "\u00b7" not in source


def test_partial_question_needs_no_program_round_outcome():
    bundle, texts = fixture()
    before = deepcopy(bundle)
    report = validate_question_evidence(bundle, texts)
    assert report["evidence_records"] == report["unique_quote_units"] == 1
    assert report["source_question_threads"] == 1
    assert report["accepted_evidence_records"] == 0
    assert report["independent_applicants"] is None
    assert report["successful_applications"] is None
    assert report["draft_generation_ready"] is False
    assert bundle == before


def test_answers_are_not_applicants():
    bundle, texts = fixture()
    duplicate = deepcopy(bundle["question_evidence"][0])
    duplicate["evidence_id"] = "same-quote"
    duplicate["question_intent"] = "leadership_impact"
    bundle["question_evidence"].append(duplicate)
    second_doc = deepcopy(bundle["source_documents"][0])
    second_doc.update(document_id="answer-18", url="https://example.org/questions/12#answer-18")
    bundle["source_documents"].append(second_doc)
    texts["answer-18"] = texts["answer-17"]
    second = deepcopy(duplicate)
    second.update(evidence_id="second-answer", document_id="answer-18")
    bundle["question_evidence"].append(second)
    report = validate_question_evidence(bundle, texts)
    assert report["evidence_records"] == 3
    assert report["unique_quote_units"] == report["source_question_threads"] == 1
    assert report["unique_source_texts"] == 1
    assert report["independent_applicants"] is None


@pytest.mark.parametrize("alter", ["missing_doc", "missing_text", "hash", "quote", "char", "line", "duplicate_id"])
def test_source_and_location_failures(alter):
    bundle, texts = fixture()
    row = bundle["question_evidence"][0]
    if alter == "missing_doc":
        row["document_id"] = "absent"
    elif alter == "missing_text":
        texts.clear()
    elif alter == "hash":
        texts["answer-17"] += "Changed source."
    elif alter == "quote":
        row["quote"] = "Absent claim."
    elif alter == "char":
        row["locator"]["char_start"] += 1
    elif alter == "line":
        row["locator"]["line_end"] += 1
    else:
        bundle["question_evidence"].append(deepcopy(row))
    with pytest.raises(QuestionEvidenceError):
        validate_question_evidence(bundle, texts)


@pytest.mark.parametrize("role,claim", [
    ("public_advice", "observed_answer"), ("personal_experience", "official_requirement"),
    ("partial_answer", "recommendation"), ("official_policy", "recommendation"),
])
def test_role_assertion_mismatch(role, claim):
    bundle, texts = fixture()
    bundle["question_evidence"][0].update(source_role=role, claim_type=claim)
    with pytest.raises(QuestionEvidenceError, match="Schema"):
        validate_question_evidence(bundle, texts)


def test_partial_answer_records_final_submission_limit_once():
    bundle, texts = fixture()
    row = bundle["question_evidence"][0]
    row.update(source_role="partial_answer", claim_type="observed_answer")
    with pytest.raises(QuestionEvidenceError):
        validate_question_evidence(bundle, texts)
    row["submission_status"] = "final_submission_unconfirmed"
    assert validate_question_evidence(bundle, texts)["evidence_records"] == 1
    row["submission_status"] = "verified_submitted"
    with pytest.raises(QuestionEvidenceError):
        validate_question_evidence(bundle, texts)


def test_community_question_is_not_official():
    bundle, texts = fixture()
    row = bundle["question_evidence"][0]
    row["exact_prompt"] = {"text": row["quote"], "document_id": row["document_id"],
                           "locator": deepcopy(row["locator"])}
    with pytest.raises(QuestionEvidenceError, match="official-program"):
        validate_question_evidence(bundle, texts)
    bundle["source_documents"][0]["publisher_role"] = "official_program"
    assert validate_question_evidence(bundle, texts)["evidence_records"] == 1
    row["exact_prompt"]["text"] = "Invented official question"
    with pytest.raises(QuestionEvidenceError, match="character span"):
        validate_question_evidence(bundle, texts)


def test_requirement_needs_official_publisher():
    bundle, texts = fixture()
    bundle["question_evidence"][0].update(source_role="official_policy", claim_type="official_requirement")
    with pytest.raises(QuestionEvidenceError, match="official-program"):
        validate_question_evidence(bundle, texts)


def test_official_recommendation_is_preserved():
    bundle, texts = fixture()
    bundle["source_documents"][0]["publisher_role"] = "official_program"
    row = bundle["question_evidence"][0]
    row.update(source_role="official_policy", claim_type="official_recommendation")
    before = deepcopy(bundle)
    assert validate_question_evidence(bundle, texts)["evidence_records"] == 1
    assert row["claim_type"] == "official_recommendation" and bundle == before


@pytest.mark.parametrize("field", ["question_intent", "inference", "applicability", "source", "context"])
def test_changed_scope_invalidates_review(field):
    bundle, texts = fixture()
    bundle = accepted(bundle)
    assert validate_question_evidence(bundle, texts)["accepted_evidence_records"] == 1
    row = bundle["question_evidence"][0]
    if field == "question_intent":
        row[field] = "program_fit"
    elif field == "inference":
        row[field]["statement"] = "A changed interpretation."
    elif field == "applicability":
        row[field]["program_ids"] = ["different-program"]
    elif field == "source":
        bundle["source_documents"][0]["body_scope"] = "partial_document"
    else:
        row["original_question"]["context"] = "Changed context."
    with pytest.raises(QuestionEvidenceError, match="scope changed"):
        validate_question_evidence(bundle, texts)


def test_changed_source_invalidates_review():
    bundle, texts = fixture()
    bundle = accepted(bundle)
    texts["answer-17"] += "A later revision.\n"
    bundle["source_documents"][0]["text_sha256"] = source_text_sha256(texts["answer-17"])
    with pytest.raises(QuestionEvidenceError, match="scope changed"):
        validate_question_evidence(bundle, texts)


def test_accepted_review_requires_receipt():
    bundle, texts = fixture()
    bundle["question_evidence"][0]["semantic_review"] = {"state": "accepted"}
    with pytest.raises(QuestionEvidenceError, match="Schema"):
        validate_question_evidence(bundle, texts)


def test_unicode_crlf_repeated_quote():
    text = "첫 문장🙂\r\n행동을 설명한다.\r\n행동을 설명한다.\r\n"
    normalized = normalize_source_text(text)
    quote = "행동을 설명한다."
    first, second = locate_quote(text, quote), locate_quote(text, quote, occurrence=1)
    assert first["line_start"] == 2 and second["line_start"] == 3
    assert normalized[second["char_start"]:second["char_end"]] == quote
    assert source_text_sha256(text) == source_text_sha256(normalized)
    with pytest.raises(QuestionEvidenceError):
        locate_quote(text, quote, occurrence=2)


def test_fingerprint_ignores_unrelated_source():
    bundle, _ = fixture()
    row = bundle["question_evidence"][0]
    digest = question_evidence_fingerprint(bundle, row)
    extra = deepcopy(bundle["source_documents"][0])
    extra["document_id"] = "unrelated"
    bundle["source_documents"].insert(0, extra)
    assert question_evidence_fingerprint(bundle, row) == digest


@pytest.mark.parametrize("field,value", [("successful_applicants", 5), ("outcome_status", "selected"), ("quote", " " * 5)])
def test_outcome_fields_and_empty_quote_rejected(field, value):
    bundle, texts = fixture()
    bundle["question_evidence"][0][field] = value
    with pytest.raises(QuestionEvidenceError):
        validate_question_evidence(bundle, texts)
