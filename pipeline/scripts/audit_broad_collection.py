"""Reconcile the broad intake snapshot with PostgreSQL, without modifying either."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from data_platform import db
from data_platform.broad_collection import Store, funding_hints


def audit(folder,check_raw=False):
    store=Store(folder)
    keys=set()
    groups=defaultdict(Counter)
    title_support=set()
    non_grant_title_support=set()
    company_examples=[]
    student_grants=0
    for record in store.iter_records():
        key=record["candidate_key"]
        assert key not in keys
        keys.add(key)
        for field in ("source_family","record_type","capture_status","issuer_kind"):
            groups[field][record[field]]+=1
        assert record["source_url"].startswith("https://")
        assert record["eligibility_status"]=="not_evaluated"
        title=record["title"]
        if re.search(r"장학|scholarship|studentship|bursar|fellowship|奨学|奖学",title,re.I):
            title_support.add(key)
            if record["record_type"]!="general_funding_notice":non_grant_title_support.add(key)
        if record["source_family"]=="grants_gov_xml":
            applicants=record["source_payload"]["fields"].get("EligibleApplicants",[])
            applicants=[applicants] if isinstance(applicants,str) else applicants
            if "21" in applicants:student_grants+=1
        if len(company_examples)<12 and re.search(r"Google|Microsoft|NVIDIA|삼성|롯데|현대|Amazon",title,re.I) and record["source_family"] not in {"previous_originals","previous_search","grants_gov_xml"}:
            company_examples.append({"title":title,"source":record["source_family"],"url":record["source_url"]})
    with store.connect() as local:
        observation_keys={row[0] for row in local.execute('SELECT observation_key FROM observation')}
        source_counts={}
        for family in [r[0] for r in local.execute('SELECT source_family FROM source_run')]:
            source_counts[family]=local.execute("SELECT count(DISTINCT candidate_key),count(DISTINCT json_extract(payload,'$.source_record_id')) FROM observation WHERE json_extract(payload,'$.source_family')=?",(family,)).fetchone()
        previous_keys={r[0] for r in local.execute("SELECT DISTINCT candidate_key FROM observation WHERE json_extract(payload,'$.source_family') IN ('previous_search','previous_originals')")}
        metas=[json.loads(row[0]) for row in local.execute('SELECT payload FROM fetch')]
    with db.connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        pg_keys={r[0] for r in conn.execute('SELECT candidate_key FROM benefitlens.support_discovery_record')}
        pg_observations={r[0] for r in conn.execute('SELECT observation_key FROM benefitlens.support_discovery_observation')}
        previous={r[0] for r in conn.execute('SELECT canonical_key FROM benefitlens.latest_candidate_opportunity JOIN benefitlens.opportunity USING(opportunity_id)')}
        preserved=set(json.loads((folder.parents[1]/'original_ingestion/2026-10-13/before-access-retry-2026-10-14-db.json').read_text())['canonical_keys'])
        assert preserved<=previous and len(previous)==124
        pg_groups=conn.execute('SELECT source_family,count(*) FROM benefitlens.support_discovery_record GROUP BY 1 ORDER BY 1').fetchall()
    assert keys==pg_keys, (len(keys),len(pg_keys))
    assert observation_keys==pg_observations,(len(observation_keys),len(pg_observations))
    assert dict(pg_groups)==dict(groups['source_family'])
    verified_raw=0
    raw_paths=set()
    if check_raw:
        for meta in metas:
            if meta['status']!='received':continue
            path=folder/meta['raw_path']
            if path in raw_paths:continue
            raw_paths.add(path)
            with path.open('rb') as stream:
                sha=hashlib.file_digest(stream,'sha256').hexdigest()
            assert sha==meta['sha256'],str(path)
            verified_raw+=1
    return {
        "postgres_record_keys_match":True,"postgres_observation_keys_match":True,
        "records":len(keys),"observations":len(observation_keys),
        "new_keys_vs_previous_snapshot":len(keys-previous_keys),
        "previous_snapshot_keys_preserved":len(previous_keys),
        "evaluated_registry_candidates_preserved":len(previous),
        "groups":{k:dict(v) for k,v in groups.items()},
        "source_observation_columns":["distinct_candidate_keys","distinct_source_record_ids"],
        "source_observation_counts":source_counts,
        "scholarship_fellowship_title_mentions":len(title_support),
        "non_general_grant_scholarship_fellowship_title_mentions":len(non_grant_title_support),
        "grants_explicit_individual_applicant_code_21":student_grants,
        "company_name_examples_unreviewed":company_examples,
        "new_raw_artifacts_hash_verified":verified_raw,
        "not_claimed":["all records are scholarships","all source records are unique programs","all records are current or individually eligible"],
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--check-raw',action='store_true')
    args=parser.parse_args()
    result=audit(args.folder,args.check_raw)
    (args.folder/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
