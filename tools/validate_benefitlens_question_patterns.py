"""Verify local question-pattern evidence without DB or network access."""
from hashlib import sha256
import json
from pathlib import Path

from benefitlens_source_storage import read_source_bytes

CATALOGUE = Path(__file__).resolve().parents[1] / "pipeline/config/question_writing_patterns_v1.json"
INTENTS = {"research_motivation", "personal_contribution", "program_fit",
           "execution_plan", "leadership_impact", "challenge_reflection"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(path=CATALOGUE):
    raw = path.read_text(encoding="utf-8")
    require(len(raw) <= 10000 and "\u00b7" not in raw, "Document rules violated")
    data = json.loads(raw)
    root = Path(data["source_root"])
    manifests = {}
    for name, digest in data["manifest_sha256"].items():
        body = read_source_bytes(root / name)
        require(sha256(body).hexdigest() == digest, f"Manifest changed: {name}")
        manifests[name] = json.loads(body)

    evidence = {}
    qa_pairs = 0
    for item in data["evidence"]:
        key = item["evidence_id"]
        require(key not in evidence, f"Duplicate evidence: {key}")
        ref = item["fulltext_ref"]
        if "path" in ref:
            text = read_source_bytes(ref["path"], storage=ref["storage"]).decode("utf-8")
            require(item["source_role"] == "official_policy" and
                    item.get("claim_type") == "official_recommendation",
                    f"Wrong official guidance role: {key}")
        elif "answer_id" in ref:
            manifest = manifests[ref["manifest"]]
            rows = [a for a in manifest["items"] if a["answer_id"] == ref["answer_id"]]
            require(len(rows) == 1, f"Missing answer: {key}")
            answer = rows[0]
            require(ref["field"] == "body", f"Unexpected answer field: {key}")
            text = answer["body"]
            require(item["source_role"] == "public_advice", f"Wrong Q&A role: {key}")
            q = item["original_question"]
            questions = manifests[q["manifest"]]
            matches = [x for x in questions if str(x["question_id"]) == q["id"]]
            require(len(matches) == 1 and matches[0]["question_id"] == answer["question_id"],
                    f"Question-answer linkage failed: {key}")
            require(matches[0]["title"] == q["title"], f"Question title changed: {key}")
            if q.get("reported_prompt"):
                require(q["reported_prompt"] in matches[0]["body"], f"Prompt excerpt not present: {key}")
            qa_pairs += 1
        else:
            manifest = manifests[ref["manifest"]]
            rows = [a for a in manifest if a["id"] == ref["article_id"]]
            require(len(rows) == 1, f"Missing reflection: {key}")
            require(ref["field"] == "text", f"Unexpected reflection field: {key}")
            source = rows[0]["text"]
            text = read_source_bytes(source["path"], storage=source["storage"]).decode("utf-8")
            require(item["source_role"] == "personal_experience", f"Wrong reflection role: {key}")
        require(sha256(text.encode()).hexdigest() == ref["sha256"], f"Full text changed: {key}")
        loc = item["locator"]
        start, end = loc["char_start"], loc["char_end"]
        require(isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text),
                f"Invalid offsets: {key}")
        require(text[start:end] == item["quote"], f"Quote mismatch: {key}")
        require(loc["line_start"] == text[:start].count("\n") + 1 and
                loc["line_end"] == text[:end].count("\n") + 1, f"Line mismatch: {key}")
        require(len(item["quote"].split()) <= 20, f"Quote too long: {key}")
        evidence[key] = item

    require(qa_pairs >= 3, "Need at least three original question-answer pairs")
    require({x["question_intent"] for x in data["patterns"]} == INTENTS,
            "Question intent coverage mismatch")
    for pattern in data["patterns"]:
        source = evidence[pattern["evidence_id"]]
        require(pattern["question_intent"] == source["question_intent"], "Intent linkage mismatch")
        require("editorial_template" in pattern["status"], "Missing editorial label")
        require(all(pattern.get(k) for k in ["opening_role", "middle_material", "closing_role",
                                           "bridge", "material_selection", "scope"]),
                "Incomplete paragraph specification")
        require(len(pattern["template"]) == 3 and all("[" in x and "]" in x for x in pattern["template"]),
                "Template must retain unconfirmed fact slots")
    return {"patterns": len(data["patterns"]), "evidence": len(evidence), "question_answer_pairs": qa_pairs,
            "official_recommendations": sum(e["source_role"] == "official_policy" for e in evidence.values()),
            "actual_submitted_answers": data["actual_submitted_answers"], "network_called": False,
            "model_batch_called": False}


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
