"""Validate source-backed advice for one writing request at a time.

No DB, network or model calls. Full applications, known outcomes and rounds
are optional context; a community question is never an official prompt.
Offsets are Unicode code points into LF-normalized text, end-exclusive;
line numbers are one-based and inclusive. Reviews record an interpretation,
not a factual guarantee. All quoted source texts must be supplied by caller.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

VERSION = "writing-question-evidence-v1"
QUESTION_INTENTS = (
    "research_motivation", "personal_contribution", "program_fit",
    "execution_plan", "leadership_impact", "challenge_reflection",
)
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "pipeline/schemas/writing-question-evidence.schema.json"


class QuestionEvidenceError(ValueError):
    pass


def normalize_source_text(text):
    if not isinstance(text, str):
        raise QuestionEvidenceError("Source text must be a string")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def source_text_sha256(text):
    return hashlib.sha256(normalize_source_text(text).encode("utf-8")).hexdigest()


def locate_quote(text, quote, *, occurrence=0):
    """Return an exact location; repeated quotes require explicit occurrence."""
    text = normalize_source_text(text)
    if not isinstance(quote, str) or not quote or "\r" in quote:
        raise QuestionEvidenceError("Quote must be nonempty LF-normalized text")
    if type(occurrence) is not int or occurrence < 0:
        raise QuestionEvidenceError("Quote occurrence must be a nonnegative integer")
    start = -1
    for _ in range(occurrence + 1):
        start = text.find(quote, start + 1)
        if start < 0:
            raise QuestionEvidenceError("Exact quote occurrence not found")
    end = start + len(quote)
    return {"char_start": start, "char_end": end,
            "line_start": text.count("\n", 0, start) + 1,
            "line_end": text.count("\n", 0, end - 1) + 1}


def _index(rows, key):
    index = {}
    for row in rows:
        identifier = row[key]
        if identifier in index:
            raise QuestionEvidenceError(f"Duplicate {key}: {identifier}")
        index[identifier] = row
    return index


def question_evidence_fingerprint(bundle, evidence):
    """Bind a review to advice semantics and referenced source metadata."""
    docs = _index(bundle["source_documents"], "document_id")
    ids = {evidence["document_id"]}
    if evidence.get("exact_prompt"):
        ids.add(evidence["exact_prompt"]["document_id"])
    if not ids <= docs.keys():
        raise QuestionEvidenceError("Evidence references a missing source document")
    payload = {"version": bundle["version"],
               "evidence": {k: v for k, v in evidence.items() if k != "semantic_review"},
               "source_documents": [docs[identifier] for identifier in sorted(ids)]}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _verify_span(text, quote, locator, label):
    start, end = locator["char_start"], locator["char_end"]
    if not 0 <= start < end <= len(text) or text[start:end] != quote:
        raise QuestionEvidenceError(f"{label}: quote does not match character span")
    expected = {"line_start": text.count("\n", 0, start) + 1,
                "line_end": text.count("\n", 0, end - 1) + 1}
    if any(locator[key] != value for key, value in expected.items()):
        raise QuestionEvidenceError(f"{label}: line locator does not match character span")


def validate_question_evidence(bundle, document_texts):
    """Check schema, source hashes, quote locations and accepted-review scope.

    document_texts maps document_id to its stored source text. A supplied text
    is checked against text_sha256, while text_ref preserves storage lineage.
    Returns unit counts, never inferred applicants or successful applications.
    """
    with SCHEMA_PATH.open(encoding="utf-8") as handle:
        schema = json.load(handle)
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker())
                    .iter_errors(bundle), key=lambda e: str(list(e.absolute_path)))
    if errors:
        first = errors[0]
        raise QuestionEvidenceError(f"Schema {list(first.absolute_path)}: {first.message}")
    docs = _index(bundle["source_documents"], "document_id")
    rows = _index(bundle["question_evidence"], "evidence_id")
    texts = {}
    for identifier, doc in docs.items():
        if identifier not in document_texts:
            raise QuestionEvidenceError(f"Missing source text: {identifier}")
        text = normalize_source_text(document_texts[identifier])
        if source_text_sha256(text) != doc["text_sha256"]:
            raise QuestionEvidenceError(f"Source text hash mismatch: {identifier}")
        texts[identifier] = text
    for identifier, row in rows.items():
        did = row["document_id"]
        if did not in docs:
            raise QuestionEvidenceError(f"Missing source document: {did}")
        _verify_span(texts[did], row["quote"], row["locator"], identifier)
        if row["source_role"] == "official_policy" and docs[did]["publisher_role"] != "official_program":
            raise QuestionEvidenceError("Official requirements require an official-program source")
        prompt = row.get("exact_prompt")
        if prompt:
            prompt_did = prompt["document_id"]
            if prompt_did not in docs or docs[prompt_did]["publisher_role"] != "official_program":
                raise QuestionEvidenceError("Exact prompt requires an official-program source")
            _verify_span(texts[prompt_did], prompt["text"], prompt["locator"], identifier + ":prompt")
        review = row["semantic_review"]
        if review["state"] == "accepted":
            expected = question_evidence_fingerprint(bundle, row)
            if review["input_fingerprint"] != expected:
                raise QuestionEvidenceError(f"Accepted review scope changed: {identifier}")
    # Identical spans syndicated across URLs count once, and one Q&A thread
    # remains one discussion even when several answers or observations exist.
    unique_units = {(docs[r["document_id"]]["text_sha256"],
                     r["locator"]["char_start"], r["locator"]["char_end"],
                     r["source_role"]) for r in rows.values()}
    threads = {(docs[r["document_id"]]["url"].split("#", 1)[0].split("/", 3)[2],
                r["original_question"]["id"]) for r in rows.values()
               if r["original_question"]["id"] is not None}
    return {"version": VERSION, "source_documents": len(docs),
            "unique_source_texts": len({d["text_sha256"] for d in docs.values()}),
            "evidence_records": len(rows), "unique_quote_units": len(unique_units),
            "source_question_threads": len(threads),
            "accepted_evidence_records": sum(r["semantic_review"]["state"] == "accepted" for r in rows.values()),
            "question_intents": sorted({r["question_intent"] for r in rows.values()}),
            "independent_applicants": None, "successful_applications": None,
            "draft_generation_ready": False}


def stamp_question_review(bundle, evidence_id, review):
    """Attach an explicitly supplied review receipt to a copy, never infer one."""
    result = deepcopy(bundle)
    rows = _index(result["question_evidence"], "evidence_id")
    if evidence_id not in rows:
        raise QuestionEvidenceError("Unknown evidence ID")
    rows[evidence_id]["semantic_review"] = deepcopy(review)
    rows[evidence_id]["semantic_review"]["input_fingerprint"] = question_evidence_fingerprint(result, rows[evidence_id])
    return result
