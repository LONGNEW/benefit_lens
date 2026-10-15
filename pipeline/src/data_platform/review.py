"""Explicit, version-bound local reviews. Source rights are never auto-approved."""
from __future__ import annotations
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from contextlib import nullcontext

from psycopg.types.json import Jsonb
from . import db
from .criteria import normalize_eligibility
from .contract import prepare_record, semantic_role
from .parsers import ParsedOpportunity, is_application_question, _soup
from .linked import extract_document

FORMAT = "benefitlens-review-1"

def compact(text: str) -> str:
    return " ".join(text.replace("\u00b7", ", ").split())

def validate_candidate(candidate: dict, text: str, source_text: str) -> dict:
    """Validate literal provenance and computable semantics, without network calls."""
    if not text.strip() or compact(text) not in compact(source_text):
        raise ValueError("review text must occur in the stored original source")
    if not compact(str(candidate["source_quote"])) or compact(str(candidate["source_quote"])) not in compact(source_text):
        raise ValueError("candidate quote is absent from the original source")
    kind = candidate["field_kind"]
    if kind == "eligibility":
        normalized = normalize_eligibility([text])
        if not normalized.rules:
            raise ValueError("no canonical rule can be promoted from this text")
        return {"field": kind, "rules": len(normalized.rules), "unresolved": normalized.unresolved_rules}
    if kind == "application_prompt":
        if not is_application_question(text):
            raise ValueError("FAQ or document instructions are not application questions")
    elif kind == "required_document":
        if semantic_role(text) != "document":
            raise ValueError("text is not an explicit document requirement")
    elif kind == "benefit":
        raise ValueError("benefit promotion requires dedicated amount review; not automatically supported")
    return {"field": kind, "unresolved": []}

def _load(connection, version_id: str):
    cursor = connection.execute("""
        SELECT v.*,o.canonical_key,p.canonical_name AS provider_name,p.provider_kind,p.official_url AS provider_url,
               s.source_id,s.canonical_url AS source_url,d.raw_document_id
        FROM benefitlens.opportunity_version v
        JOIN benefitlens.opportunity o USING(opportunity_id)
        JOIN benefitlens.provider p USING(provider_id)
        JOIN platform.collection_run run ON run.run_id=v.run_id
        JOIN platform.data_source s USING(source_id)
        JOIN platform.document_version d USING(document_version_id)
        WHERE v.opportunity_version_id=%s AND v.valid_to IS NULL FOR UPDATE OF v
    """, (version_id,))
    row = cursor.fetchone()
    if not row:
        raise ValueError("review target is missing or no longer the current version")
    data = dict(zip([col.name for col in cursor.description], row))
    benefits = connection.execute("SELECT benefit_type,description FROM benefitlens.benefit WHERE opportunity_version_id=%s ORDER BY benefit_id", (version_id,)).fetchall()
    rules = connection.execute("SELECT rule_text FROM benefitlens.eligibility_rule WHERE opportunity_version_id=%s ORDER BY position,eligibility_rule_id", (version_id,)).fetchall()
    documents = connection.execute("SELECT document_name FROM benefitlens.required_document WHERE opportunity_version_id=%s ORDER BY required_document_id", (version_id,)).fetchall()
    prompts = connection.execute("SELECT prompt_key,prompt_text,character_limit,source_url FROM benefitlens.application_prompt WHERE opportunity_version_id=%s ORDER BY prompt_key", (version_id,)).fetchall()
    evidence = dict(connection.execute("SELECT field_name,evidence_text FROM benefitlens.evidence_span WHERE opportunity_version_id=%s", (version_id,)).fetchall())
    record = ParsedOpportunity(
        canonical_key=data["canonical_key"], title=data["title"], summary=data["summary"] or "", official_url=data["official_url"],
        benefit_type=benefits[0][0] if benefits else "unknown", benefit_description=benefits[0][1] if benefits else "",
        additional_benefits=[{"benefit_type": item[0], "description": item[1]} for item in benefits[1:]],
        eligibility_rules=list(dict.fromkeys(item[0] for item in rules)), required_documents=[item[0] for item in documents],
        application_prompts=[{"prompt_key": item[0], "prompt_text": item[1], "character_limit": item[2], "source_url": item[3]} for item in prompts],
        evidence=evidence, lifecycle_status=data["lifecycle_status"], application_url=data["application_url"],
        opens_at=data["opens_at"].isoformat() if data["opens_at"] else None, closes_at=data["closes_at"].isoformat() if data["closes_at"] else None,
        provider_name=data["provider_name"], provider_kind=data["provider_kind"], provider_url=data["provider_url"],
        record_kind=data["record_kind"], presentation=data["presentation_json"], evidence_sources=data["evidence_sources_json"],
    )
    return data, prepare_record(record)

def make_packet(version_id: str) -> dict:
    with db.connect() as connection:
        data, record = _load(connection, version_id)
        candidates = connection.execute("SELECT extraction_candidate_id,field_kind,source_url,source_quote,review_status FROM benefitlens.extraction_candidate WHERE opportunity_version_id=%s ORDER BY extraction_candidate_id", (version_id,)).fetchall()
        return {"format": FORMAT, "opportunity_version_id": version_id, "fingerprint": data["content_fingerprint_sha256"],
                "title": data["title"], "official_url": data["official_url"], "action": "pending", "reason": "",
                "candidate_id": None, "source_text": "", "confirmed_complete": False, "ocr_verified": False,
                "unresolved": normalize_eligibility(record.eligibility_rules).unresolved_rules,
                "candidates": [{"id": row[0], "field": row[1], "url": row[2], "quote": row[3], "status": row[4]} for row in candidates]}

def process_packet(packet: dict, reviewer: str, apply: bool = False, *, connection_override=None) -> dict:
    if packet.get("format") != FORMAT or packet.get("action") not in {"promote", "reject", "reviewed"}:
        raise ValueError("explicit promote, reject or reviewed decision is required")
    if not reviewer.strip() or len(str(packet.get("reason", "")).strip()) < 5:
        raise ValueError("reviewer and a specific review reason are required")
    digest = hashlib.sha256(json.dumps(packet, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with (db.connect() if connection_override is None else nullcontext(connection_override)) as connection:
        prior = connection.execute("SELECT opportunity_version_id FROM benefitlens.review_event WHERE packet_sha256=%s", (digest,)).fetchone()
        if prior:
            return {"status": "already_applied", "opportunity_version_id": str(prior[0])}
        data, record = _load(connection, str(packet["opportunity_version_id"]))
        if packet.get("fingerprint") != data["content_fingerprint_sha256"]:
            raise ValueError("stale review packet fingerprint")
        action = packet["action"]
        candidate = None
        details: dict[str, Any] = {}
        if action in {"promote", "reject"}:
            cursor = connection.execute("""SELECT x.*,v.opportunity_id FROM benefitlens.extraction_candidate x
                JOIN benefitlens.opportunity_version v USING(opportunity_version_id)
                WHERE extraction_candidate_id=%s FOR UPDATE OF x""", (packet.get("candidate_id"),))
            row = cursor.fetchone()
            if not row:
                raise ValueError("candidate not found")
            candidate = dict(zip([col.name for col in cursor.description], row))
            if candidate["opportunity_id"] != data["opportunity_id"] or candidate["review_status"] != "pending":
                raise ValueError("candidate belongs to another opportunity or was already reviewed")
            if action == "promote":
                accepted_ids = {data["raw_document_id"], *[s.get("rawDocumentId") for s in record.evidence_sources]}
                if candidate["raw_document_id"] not in accepted_ids:
                    raise ValueError("candidate source is not linked to the current version")
                source = connection.execute("SELECT body FROM platform.raw_document WHERE raw_document_id=%s", (candidate["raw_document_id"],)).fetchone()
                kind = candidate["source_kind"]
                if kind == "ocr":
                    if packet.get("ocr_verified") is not True:
                        raise ValueError("original image comparison must be explicitly confirmed")
                    source_text = next((str(s.get("text", "")) for s in record.evidence_sources if s.get("rawDocumentId") == candidate["raw_document_id"]), "")
                elif kind in {"pdf", "hwp", "hwpx"}:
                    source_text = extract_document(bytes(source[0]), kind)
                else:
                    source_text = _soup(bytes(source[0])).get_text(" ", strip=True) if kind == "html" else bytes(source[0]).decode("utf-8", errors="replace")
                text = str(packet.get("source_text", ""))
                details = validate_candidate(candidate, text, source_text)
                if candidate["field_kind"] == "eligibility":
                    record = replace(record, eligibility_rules=list(dict.fromkeys(record.eligibility_rules + [text])))
                elif candidate["field_kind"] == "required_document":
                    record = replace(record, required_documents=list(dict.fromkeys(record.required_documents + [text])))
                elif candidate["field_kind"] == "application_prompt":
                    prompt = {"prompt_key": "review-" + str(candidate["extraction_candidate_id"]), "prompt_text": text,
                              "character_limit": candidate["proposed_value"].get("character_limit"), "source_url": candidate["source_url"]}
                    record = replace(record, application_prompts=record.application_prompts + [prompt])
        else:
            normalized = normalize_eligibility(record.eligibility_rules)
            failed_sources = [s for s in record.evidence_sources if s.get("status") in {"failed", "ocr_review"}]
            if packet.get("confirmed_complete") is not True or normalized.match_mode != "structured" or failed_sources:
                raise ValueError("complete original-source review and zero unresolved conditions are required")
            if any(rule.context.get("requiresReview") for rule in normalized.rules):
                raise ValueError("unresolved rule context blocks approval")
        if not apply:
            connection.rollback()
            return {"status": "validated_dry_run", "action": action, "detail": details, "rights_changed": False}
        target = data["opportunity_version_id"]
        if action == "promote":
            record = replace(record, evidence={**record.evidence, "review_packet_sha256": digest})
            db.ingest_opportunities(connection, source={"provider_name": data["provider_name"], "provider_kind": data["provider_kind"], "url": data["source_url"], "complete_snapshot": False},
                run_id=data["run_id"], document_version_id=data["document_version_id"], raw_document_id=data["raw_document_id"], records=[record], seen_at=datetime.now(timezone.utc))
            target = connection.execute("SELECT opportunity_version_id FROM benefitlens.opportunity_version WHERE opportunity_id=%s AND valid_to IS NULL", (data["opportunity_id"],)).fetchone()[0]
        if candidate:
            connection.execute("UPDATE benefitlens.extraction_candidate SET review_status=%s,reviewed_at=now(),reviewed_by=%s WHERE extraction_candidate_id=%s", ("accepted" if action == "promote" else "rejected", reviewer, candidate["extraction_candidate_id"]))
        if action == "reviewed":
            connection.execute("UPDATE benefitlens.opportunity_version SET publication_status='reviewed' WHERE opportunity_version_id=%s", (target,))
        connection.execute("INSERT INTO benefitlens.review_event(opportunity_version_id,extraction_candidate_id,action,reviewer,reason,packet_sha256,detail_json) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (target, candidate["extraction_candidate_id"] if candidate else None, action, reviewer, packet["reason"], digest, Jsonb(details)))
        return {"status": "applied", "action": action, "opportunity_version_id": str(target), "rights_changed": False}
