"""Read-only reconciliation of intake, canonical SQL and experience evidence."""
import argparse
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

import project
from benefitlens_experience_coverage import database_coverage
from benefitlens_source_storage import read_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json


def audit(sample_size=80):
    with project.connect() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        def rows(query):
            return conn.execute(query).fetchall()
        tables = [r[0] for r in rows("SELECT table_name FROM information_schema.tables "
                  "WHERE table_schema='benefitlens' AND table_type='BASE TABLE' ORDER BY 1")]
        counts = {t: conn.execute('SELECT count(*) FROM benefitlens."'+t+'"').fetchone()[0]
                  for t in tables}
        discovery = dict(rows("SELECT capture_status,count(*) FROM benefitlens.support_discovery_record "
                              "WHERE record_type!='sample' GROUP BY 1"))
        integrity = rows("""SELECT
          count(*) FILTER (WHERE r.capture_status='original_available' AND d.document_id IS NULL),
          count(*) FILTER (WHERE w.candidate_key IS NULL),
          count(*) FILTER (WHERE r.capture_status='original_available' AND w.status!='needs_semantic_review'),
          count(*) FILTER (WHERE r.source_country IS NULL OR r.source_country=''),
          count(*) FILTER (WHERE r.issuer_name IS NULL OR r.issuer_name=''),
          count(*) FILTER (WHERE r.payload->>'current_open_verified'='true')
          FROM benefitlens.support_discovery_record r
          LEFT JOIN benefitlens.pipeline_document d ON d.document_id=r.payload->>'document_id'
          LEFT JOIN benefitlens.pipeline_work_item w ON w.candidate_key=r.candidate_key
          WHERE r.record_type!='sample'""")[0]
        active = "coalesce((payload->>'active')::boolean,true)"
        quality = rows(f"""SELECT count(*),
          count(*) FILTER (WHERE application_round IS NULL OR application_round IN ('','unknown')),
          count(*) FILTER (WHERE person_name IS NULL OR person_name=''),
          count(*) FILTER (WHERE coalesce(payload->>'semantic_review','unreviewed')='unreviewed'),
          count(*) FILTER (WHERE payload->>'approved_pattern'='true'),
          count(*) FILTER (WHERE program_id LIKE 'higherin:%'),
          count(*) FILTER (WHERE payload->>'identity_status'='anonymous_source_record'),
          count(*) FILTER (WHERE outcome_claim='selected'),
          count(*) FILTER (WHERE outcome_claim='not_selected')
          FROM benefitlens.experience_discovery_case WHERE {active}""")[0]
        hosts = rows(f"SELECT split_part(source_url,'/',3),count(*) FROM benefitlens.experience_discovery_case "
                     f"WHERE {active} GROUP BY 1 ORDER BY 2 DESC LIMIT 12")
        doclinks = rows(f"""SELECT count(*),count(*) FILTER (WHERE d.document_id IS NULL)
          FROM benefitlens.experience_discovery_case c
          CROSS JOIN LATERAL jsonb_array_elements_text(coalesce(c.payload->'document_ids','[]')) x(id)
          LEFT JOIN benefitlens.pipeline_document d ON d.document_id=x.id WHERE {active.replace('payload', 'c.payload')}""")[0]
        # Stable, evenly spaced samples cover the complete key range, not only the first rows.
        source_rows = rows("""SELECT candidate_key,payload->'source_document'
          FROM benefitlens.support_discovery_record WHERE record_type!='sample'
          AND capture_status='original_available' ORDER BY candidate_key""")
        step = max(1, len(source_rows)//sample_size)
        source_sample = source_rows[::step][:sample_size]
        exp_rows = rows(f"SELECT case_key,payload->'document',payload->'evidence' "
                        f"FROM benefitlens.experience_discovery_case WHERE {active} "
                        "AND payload ? 'document' ORDER BY case_key")
        step = max(1, len(exp_rows)//sample_size)
        experience_sample = exp_rows[::step][:sample_size]
    source_errors, experience_errors = [], []
    def check(ref, path_key='path', hash_key='sha256', storage=None):
        raw = read_source_bytes(ref[path_key], storage=storage)
        if sha256(raw).hexdigest() != ref[hash_key]:
            raise ValueError('Decoded source hash mismatch')
        return raw
    for key, doc in source_sample:
        try:
            check(doc, 'raw_path', 'content_sha256', doc['metadata'].get('raw_storage'))
            check(doc, 'text_path', 'text_sha256', doc['metadata'].get('text_storage')).decode('utf8')
        except (OSError, ValueError, KeyError, TypeError) as error:
            source_errors.append({'key':key,'error':str(error)[:160]})
    for key, doc, evidence in experience_sample:
        try:
            check(doc['raw'], storage=doc['raw'].get('storage'))
            text = check(doc['text'], storage=doc['text'].get('storage')).decode('utf8')
            for item in evidence or []:
                if item['exact_quote'] not in text:
                    raise ValueError('Evidence quote missing from full text')
                if item.get('text_sha256') and item['text_sha256'] != doc['text']['sha256']:
                    raise ValueError('Evidence text hash differs')
        except (OSError, ValueError, KeyError, TypeError) as error:
            experience_errors.append({'key':key,'error':str(error)[:160]})
    return {'at_kst':datetime.now(KST).isoformat(), 'database_mode':'repeatable_read_read_only',
      'table_counts':counts,'discovery_states':discovery,
      'discovery_integrity':dict(zip(['missing_original_document_rows','missing_work_items',
        'original_work_state_mismatch','missing_country','missing_issuer','verified_current_open'],integrity)),
      'experience_quality':dict(zip(['active_rows','unknown_round','missing_person_name',
        'semantic_unreviewed','approved_pattern','higherin_role_based_program_keys',
        'anonymous_source_records','selected_claims','not_selected_claims'],quality)),
      'experience_document_links':{'references':doclinks[0],'missing_documents':doclinks[1]},
      'experience_top_hosts':dict(hosts),
      'source_hash_sample':{'size':len(source_sample),'errors':source_errors},
      'experience_hash_quote_sample':{'size':len(experience_sample),'errors':experience_errors},
      'sampling':'stable key order, evenly spaced; no semantic correctness inference',
      'counting_limitation':'Anonymous native review/text identities cannot establish distinct authors or person-round episodes.',
      'coverage_separate_snapshot':database_coverage()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=CACHE/'schema-quality-audit-20261117/status.json')
    parser.add_argument('--sample-size',type=int,default=80,choices=range(1,501))
    args=parser.parse_args()
    args.output.resolve().relative_to(CACHE.resolve())
    result=audit(args.sample_size)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    atomic_json(args.output,result)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
