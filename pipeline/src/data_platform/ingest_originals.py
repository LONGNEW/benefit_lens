"""Ingest collected originals, never search snippets, into the existing registry."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import db
from .contract import prepare_record
from .funding import operator_group
from .original_collection import BENEFIT, ELIGIBILITY, PERIOD, ROOT, TOPIC, document_url, primary_provider
from .keyword_search import url_key
from .parsers import ParsedOpportunity, _application_window, _full_dates, _infer_benefit_type


def opportunity_identity(url: str) -> str:
    """Collapse verified navigation aliases, retain program and round identifiers."""
    value=document_url(url)
    p=urlsplit(value)
    pairs=parse_qsl(p.query,keep_blank_values=True)
    if (p.hostname or "").endswith("kosaf.go.kr") and p.path=="/ko/scholar.do" and any(k=="pg" for k,v in pairs):
        pairs=[(k,v) for k,v in pairs if k=="pg"]
    if (p.hostname or "").endswith("kosaf.go.kr") and p.path=="/ko/notice.do" and any(k=="seqNo" for k,v in pairs):
        pairs=[(k,v) for k,v in pairs if k in {"mode","seqNo"}]
    pairs=[(k,v) for k,v in pairs if k!="msockid"]
    host="www.postech.ac.kr" if re.fullmatch(r"pacmsmain\d+\.postech\.ac\.kr",p.hostname or "") else p.netloc
    return url_key(urlunsplit((p.scheme,host,p.path,urlencode(pairs),"")))


def sections(text: str) -> dict[str, list[str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    result = {key: [] for key in ("eligibility", "benefits", "requirements", "specialTerms", "selection", "period")}
    patterns = {"eligibility": ELIGIBILITY, "benefits": BENEFIT, "period": PERIOD,
        "requirements": re.compile(r"지원\s*논문\s*자격|접수\s*방법|제출\s*서류|신청\s*방법|지원\s*방법|application.submission|required.documents|how.to.apply",re.I),
        "specialTerms": re.compile(r"유의\s*사항|중복수혜|중복\s*수혜|special.terms|award.conditions",re.I),
        "selection": re.compile(r"선발\s*방법|심사|선발\s*절차|review.criteria|selection.process",re.I)}
    current = None
    remaining = 0
    faq=False
    layout_reference=False
    for line in lines:
        if line.startswith("[") and line.endswith("]"):
            current,remaining,faq=None,0,False
            layout_reference=".pptx]" in line.lower()
            continue
        if layout_reference: continue
        if re.search(r"\bFAQ\b|자주\s*묻는\s*질문",line,re.I):
            faq=True
            current=None
        if faq or re.match(r"^[QA]\s*:",line,re.I): continue
        matched = next((key for key, pattern in patterns.items() if len(line)<240 and pattern.search(line)),None)
        if matched:
            current,remaining = matched,14
        elif remaining<=0 or (len(line)<80 and re.match(r"^(?:Research Area|Paper review|Final session|Need further|콘텐츠 담당|문의|목록|로그인|Copyright)",line,re.I)):
            current=None
        elif re.match(r"^[가-하]\.[ ]",line):
            current=None
        if current:
            result[current].append(line[:1400])
            remaining-=1
    return {k:list(dict.fromkeys(v)) for k,v in result.items()}


def record_from_document(doc: dict, text: str, config: dict, attached: list[dict]) -> ParsedOpportunity | None:
    provider = primary_provider(doc,text,config)
    if not provider or doc["status"]!="collected" or doc["capture_mode"]=="metadata_only":
        return None
    if doc["role"] not in {"notice_or_policy","guideline_or_form","listing_or_reference"} or doc["kind"] not in {"html","pdf","hwp","hwpx","zip"} or len(text)<150:
        return None
    # Recipient announcements and broad index pages are not application notices.
    if re.search(r"장학생.*(?:선정|합격)|선정.*장학생|winners|recipients|announcing.*scholars|news.*fellowship",doc["title"],re.I):
        return None
    if re.search(r"(?:/|\b)(?:faq|resources|how-to-apply)(?:/|\b)|frequently.asked|additional.resources|자주\s*묻는",doc["final_url"]+" "+doc["title"],re.I):
        return None
    if doc["title"] in {"한국장학재단","Finding Scholarships"}:
        return None
    if re.search(r"/media\.do|/tuition\.do|/CO/jspAction\.do|faqs|info_sessions|IBM_awards\.pdf|/policies/document/|/PDA/goodsinfo/M_05_|/cmm/mainpdf\.nkf",doc["final_url"],re.I):
        return None
    if doc["kind"]!="html" and not TOPIC.search(text[:1800]):
        # A mention in a later chapter of an admission handbook, budget manual
        # or annual report is reference evidence, not an independent opportunity.
        return None
    if doc["kind"]!="html" and re.search(r"예산편성|관리.?운영.?지침|연구비.?관리|입학.?전형|모집.?요강|성과.?보고",text[:1800]):
        return None
    fields=sections(text)
    url=document_url(doc["final_url"])
    title=doc["title"]
    if not url or not TOPIC.search(title):
        return None
    # The key identifies an actual origin page. Unresolved program/round identity
    # is explicitly retained instead of guessing that every page is a new program.
    key="origin:"+hashlib.sha256(opportunity_identity(url).encode()).hexdigest()[:24]
    kind="policy" if doc["kind"]!="html" or re.search(r"scholar\.do|scholarship_(?:types|information)|support-policy|재정지원|지원제도",url+" "+title,re.I) else "notice"
    evidence_sources=[{"url":doc["final_url"],"kind":doc["kind"],"status":"collected","label":"확보한 공식 원문","text":text[:30000],"sha256":doc["sha256"],"rawDocumentId":doc.get("pg_raw_document_id"),"discoveryRole":"primary_original","roundStatus":"not_resolved"}]
    for asset in attached:
        if asset.get("text"):
            evidence_sources.append({"url":asset["final_url"],"kind":asset["kind"],"status":"ocr_review" if asset["kind"]=="image" else "collected","label":asset["title"],"text":asset["text"][:30000],"sha256":asset["sha256"],"rawDocumentId":asset.get("pg_raw_document_id"),"discoveryRole":"linked_attachment"})
            extra=sections(asset["text"])
            for field in fields:
                fields[field]=list(dict.fromkeys([*fields[field],*extra[field]]))
        elif asset.get("extraction_error"):
            evidence_sources.append({"url":asset["final_url"],"kind":asset["kind"],"status":"failed","label":asset["title"],"error":"텍스트 추출 미완료: "+asset["extraction_error"]})
    if sum(bool(fields[k]) for k in ("eligibility","benefits","period"))<2:
        return None
    if doc["kind"]=="zip" and re.search(r"논문 접수 마감.*20\d{2}년",text):
        kind="notice"
    benefit=" / ".join(fields["benefits"])[:3500]
    required=[line for line in fields["requirements"] if re.search(r"서류|신청서|계획서|증명서|추천서|paper|signed|questionnaire|proposal|statement|transcript|letter",line,re.I)]
    record=ParsedOpportunity(canonical_key=key,title=title,summary=text[:1500],official_url=url,
        benefit_type=_infer_benefit_type(title+" "+benefit),benefit_description=benefit,
        eligibility_rules=fields["eligibility"],required_documents=required,
        provider_name=provider["name"],provider_kind=provider["kind"],provider_url="https://"+provider["host"]+"/",
        record_kind=kind,presentation={k:v for k,v in fields.items() if k!="period"},
        evidence={"detail":text[:16000],"benefit":benefit,"eligibility":"\n".join(fields["eligibility"]),"discovery":"공식 원문 확보 후 적재, 기관과 모집 회차의 추가 검토 필요"},
        evidence_sources=evidence_sources)
    opens,closes,status,deadline=_application_window(fields["period"],record)
    dates=_full_dates(" ".join(fields["period"]))
    if len(dates)==1 and re.search(r"(?:접수|신청)\s*마감|마감\s*일", " ".join(fields["period"])) and not closes:
        opens=None
        moment=datetime.combine(dates[0],time(23,59,59),timezone(timedelta(hours=9)))
        closes=moment.isoformat()
        status="closed" if datetime.now(timezone.utc)>moment else "open"
    if re.search(r"applications? (?:are )?closed|submission due date done|접수.*종료", " ".join(fields["period"]),re.I):
        status="closed"
    if kind=="policy":
        opens,closes,status=None,None,"unknown"
    return prepare_record(replace(record,opens_at=opens,closes_at=closes,lifecycle_status=status))


def ingest(collection_path: Path, config_path: Path, output: Path, public_output: Path | None=None):
    collection=json.loads(collection_path.read_text(encoding="utf-8"))
    if collection.get("complete") is not True:
        raise ValueError("only completed candidate ledgers may be ingested")
    config=json.loads(config_path.read_text(encoding="utf-8"))
    root=collection_path.parent
    docs={d["url"]:dict(d) for d in collection["documents"]}
    outcomes=[]
    run_context={}
    with db.connect() as conn:
        existing=conn.execute("SELECT canonical_key,official_url FROM benefitlens.latest_candidate_opportunity JOIN benefitlens.opportunity USING(opportunity_id)").fetchall()
        existing_by_url={opportunity_identity(url):key for key,url in existing if document_url(url)}
        before_keys={key for key,_ in existing}
        baseline_path=root/"before-ingestion.json"
        if not baseline_path.exists():
            baseline_path.write_text(json.dumps({"canonical_keys":sorted(key for key in before_keys if not key.startswith("origin:"))},ensure_ascii=False,indent=2),encoding="utf-8")
        task_baseline=set(json.loads(baseline_path.read_text(encoding="utf-8"))["canonical_keys"])
        # Persist raw originals, including relay evidence, independently of notice promotion.
        for doc in docs.values():
            if not doc.get("raw_path") or doc["status"]!="collected":
                continue
            host=urlsplit(doc["final_url"]).hostname or "unknown"
            provider=doc.get("provider") or {"name":host,"kind":"association_nonprofit"}
            source_id="discovered-origin-"+host
            source={"source_id":source_id,"provider_name":provider["name"],"provider_kind":provider["kind"],"source_group":operator_group(provider["kind"]),
                "source_kind":"official_page" if doc.get("primary_provider") else "public_reference","url":"https://"+host+"/","rights_status":"review_required","enabled":True,"production_allowed":False,"complete_snapshot":False,"schedule_allowed":False}
            if source_id not in run_context:
                db.upsert_source(conn,source)
                run_context[source_id]=(source,*db.start_run(conn,source_id,parser_name="search-original-ingestion-1"))
            source,run_id,started=run_context[source_id]
            artifact=(root/doc["raw_path"]).resolve()
            if not artifact.is_relative_to(root.resolve()):
                raise ValueError("original artifact outside collection")
            blob=artifact.read_bytes()
            if hashlib.sha256(blob).hexdigest()!=doc["sha256"]:
                raise ValueError("stored original hash mismatch")
            seen=datetime.fromisoformat(doc["collected_at"])
            previous=conn.execute("SELECT document_version_id,raw_document_id FROM platform.document_version WHERE source_id=%s AND canonical_url=%s AND body_sha256=%s",(source_id,document_url(doc["final_url"]),doc["sha256"])).fetchone()
            if previous:
                version_id,raw_id=previous
            else:
                raw_id=db.store_raw_document(conn,run_id=run_id,source_id=source_id,source_url=doc["url"],final_url=doc["final_url"],fetched_at=seen,http_status=200,content_type=doc["content_type"],headers={},body=blob,body_sha256=doc["sha256"])
                version_id=db.register_document_version(conn,source_id=source_id,canonical_url=document_url(doc["final_url"]),body_sha256=doc["sha256"],raw_document_id=raw_id,seen_at=seen)
            doc.update(pg_raw_document_id=raw_id,pg_document_version_id=version_id,pg_source_id=source_id)
            if doc.get("text_path"):
                doc["text"]=(root/doc["text_path"]).read_text(encoding="utf-8")
            conn.commit()
        prepared={}
        attachments={}
        for doc in docs.values():
            if not doc.get("pg_raw_document_id"):
                continue
            attached=[docs[link["url"]] for link in doc["links"] if link["url"] in docs and (docs[link["url"]]["kind"] in {"pdf","hwp","hwpx","image","zip"} or re.search(r"faq|how-to-apply|resources",link["url"],re.I))]
            attachments[doc["url"]]=attached
            prepared[doc["url"]]=record_from_document(doc,doc.get("text",""),config,attached)
        linked_assets={asset["url"]:parent for parent,attached in attachments.items() if prepared.get(parent) and docs[parent]["kind"]=="html" for asset in attached if asset["kind"]!="html"}
        selected={}
        def registry_key(record):
            existing_key=existing_by_url.get(opportunity_identity(record.official_url))
            return existing_key if existing_key and not existing_key.startswith("origin:") else record.canonical_key
        for url,record in prepared.items():
            if record is None or url in linked_assets: continue
            identity=opportunity_identity(record.official_url)
            incumbent=selected.get(identity)
            rank=lambda u:(docs[u]["url"]==docs[u]["final_url"],docs[u]["collected_at"],u)
            if incumbent is None or rank(url)>rank(incumbent): selected[identity]=url
        for url,record in prepared.items():
            if record is None:
                continue
            doc=docs[url]
            attached=attachments[url]
            if url in linked_assets:
                parent=linked_assets[url]
                outcomes.append({"original_url":doc["final_url"],"title":doc["title"],"canonical_key":registry_key(prepared[parent]),"status":"attachment_linked_to_candidate","parent_original_url":docs[parent]["final_url"]})
                continue
            record_identity=opportunity_identity(record.official_url)
            if selected[record_identity]!=url:
                outcomes.append({"original_url":record.official_url,"title":record.title,"canonical_key":registry_key(prepared[selected[record_identity]]),"status":"alias_linked_to_candidate"})
                continue
            if record_identity in existing_by_url and not existing_by_url[record_identity].startswith("origin:"):
                outcomes.append({"original_url":record.official_url,"title":record.title,"canonical_key":existing_by_url[record_identity],"status":"existing_opportunity_preserved"})
                continue
            source,run_id,started=run_context[doc["pg_source_id"]]
            # Extraction changes create a later derived version even if source
            # bytes were collected earlier. Keep raw fetched_at unchanged.
            try:
                with conn.transaction():
                    db.ingest_opportunities(conn,source=source,run_id=run_id,document_version_id=doc["pg_document_version_id"],raw_document_id=doc["pg_raw_document_id"],records=[record],seen_at=datetime.now(timezone.utc))
            except Exception as error:
                outcomes.append({"original_url":record.official_url,"title":record.title,"canonical_key":record.canonical_key,"status":"candidate_ingestion_failed","error_class":type(error).__name__})
                continue
            existing_by_url[record_identity]=record.canonical_key
            outcomes.append({"original_url":record.official_url,"title":record.title,"canonical_key":record.canonical_key,"status":"candidate_ingested","record_kind":record.record_kind,"source_id":source["source_id"],"raw_document_id":doc["pg_raw_document_id"],"document_version_id":doc["pg_document_version_id"],"eligibility_sentences":len(record.eligibility_rules),"attachments":len(attached),"publication_status":"needs_review"})
        for source,run_id,started in run_context.values():
            db.finish_run(conn,run_id,status="succeeded",raw_record_count=sum(d.get("pg_source_id")==source["source_id"] for d in docs.values()),normalized_record_count=sum(o.get("source_id")==source["source_id"] for o in outcomes))
        active_discovery_keys={o["canonical_key"] for o in outcomes if o["status"] in {"candidate_ingested","candidate_ingestion_failed"}}
        covered={opportunity_identity(d["final_url"]) for d in docs.values() if d.get("pg_raw_document_id")}
        obsolete={key for key,url in existing if key.startswith("origin:") and document_url(url) and opportunity_identity(url) in covered and key not in active_discovery_keys}
        for key in obsolete:
            conn.execute("UPDATE benefitlens.opportunity SET retired_at=now() WHERE canonical_key=%s",(key,))
        conn.commit()
        after_keys={row[0] for row in conn.execute("SELECT canonical_key FROM benefitlens.latest_candidate_opportunity JOIN benefitlens.opportunity USING(opportunity_id)")}
        if not task_baseline<=after_keys:
            raise RuntimeError("existing opportunity lost")
    manifest={"schema_version":"original-ingestion-1","collected_at":collection["finished_at"],"counts":{**collection["counts"],"postgres_raw_documents":sum(bool(d.get("pg_raw_document_id")) for d in docs.values()),"new_candidate_opportunities":len(after_keys-task_baseline),"new_in_this_pass":len(after_keys-before_keys),"reference_candidates_retired_this_pass":len(obsolete),"before_opportunities":len(task_baseline),"after_opportunities":len(after_keys),"existing_opportunities_preserved":len(task_baseline & after_keys)},
        "outcomes":outcomes,"results":collection["results"],"documents":[{k:v for k,v in d.items() if k not in {"text","raw_path","text_path"}} for d in docs.values()],"limits":collection["limits"],"web_sync_status":"not_run"}
    manifest["access_policy"]=collection.get("access_policy",{})
    retry_path=root/"access-retry.json"
    preservation_path=root/"before-access-retry-2026-10-14-db.json"
    if retry_path.exists():
        retry=json.loads(retry_path.read_text(encoding="utf-8"))
        changes=retry["changes"]
        manifest["access_retry"]={"finished_at":retry["finished_at"],"retried_candidates":retry["retried_candidates"],"recovered_candidates":sum(c["after"] in {"official_origin_collected","collected_without_resolved_official_origin"} for c in changes),"official_origin_candidates":sum(c["after"]=="official_origin_collected" for c in changes),"still_failed_candidates":sum(c["after"]=="fetch_or_access_failed" for c in changes),"new_document_count":len(retry["new_document_urls"])}
        if preservation_path.exists():
            preserved=set(json.loads(preservation_path.read_text(encoding="utf-8"))["canonical_keys"])
            if not preserved<=after_keys: raise RuntimeError("pre-retry candidate missing")
            manifest["access_retry"].update(existing_candidates_preserved=len(preserved),new_candidate_opportunities=len(after_keys-preserved))
    output.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    if public_output:
        public={**manifest,"documents":[{k:v for k,v in d.items() if not k.startswith("pg_")} for d in manifest["documents"]]}
        public_output.write_text(json.dumps(public,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(manifest["counts"],ensure_ascii=False),flush=True)
    return manifest


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--collection",type=Path,required=True)
    parser.add_argument("--config",type=Path,default=ROOT/"config/original_sources.json")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--public-output",type=Path)
    args=parser.parse_args()
    ingest(args.collection,args.config,args.output,args.public_output)


if __name__=="__main__":
    main()
