"""Verify the local read model against PostgreSQL before marking the handoff."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen
from data_platform import db


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--base-url",default="http://localhost:59022")
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--public-manifest",type=Path,required=True)
    args=parser.parse_args()
    if urlsplit(args.base_url).hostname not in {"localhost","127.0.0.1","::1"}:
        raise ValueError("verification is local only")
    rows=[]
    first=None
    page=1
    while page<= (first["pageCount"] if first else 1):
        with urlopen(args.base_url+f"/api/opportunities?fundingTarget=all&pageSize=100&page={page}",timeout=10) as response:
            payload=json.load(response)
        meta=payload["meta"]
        assert meta["storage"]=="d1" and meta["datasetStatus"]=="complete"
        if first is None: first=meta
        assert meta["datasetVersion"]==first["datasetVersion"]
        rows.extend(payload["data"])
        page+=1
    with db.connect() as conn:
        keys={row[0] for row in conn.execute("SELECT canonical_key FROM benefitlens.latest_candidate_opportunity JOIN benefitlens.opportunity USING(opportunity_id)")}
    actual={r["id"] for r in rows}
    assert actual==keys and len(rows)==len(actual)==first["total"]
    manifest=json.loads(args.manifest.read_text(encoding="utf-8"))
    stored=[d for d in manifest["documents"] if d.get("pg_raw_document_id")]
    storage_counts={"postgres_unique_raw_documents":len({d["pg_raw_document_id"] for d in stored}),"unique_content_hashes":len({d["sha256"] for d in stored})}
    assert manifest["counts"]["after_opportunities"]==len(keys)
    assert not any(o["status"]=="candidate_ingestion_failed" for o in manifest["outcomes"])
    assert all(o["canonical_key"] in actual for o in manifest["outcomes"])
    audit={"postgres_keys_match_d1":True,"candidate_count":len(keys),"dataset_version":first["datasetVersion"],"synchronized_at":first["synchronizedAt"]}
    (args.manifest.parent/"preview-audit.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    for path in (args.manifest,args.public_manifest):
        value=json.loads(path.read_text(encoding="utf-8"))
        value["web_sync_status"]="local_d1_verified"
        value["web_sync"]=audit
        value["counts"].update(storage_counts)
        path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(audit,ensure_ascii=False))


if __name__=="__main__": main()
