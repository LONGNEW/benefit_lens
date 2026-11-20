"""Persist verified public writing originals and case bindings in PostgreSQL.

Only a completed collector snapshot is imported. Outcome claims retain their
published evidence; no writing pattern is inferred or approved by this loader.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from hashlib import sha256
import json

import project
from benefitlens_source_storage import read_source_bytes
from collect_benefitlens_writing_corpus import ROOT, index_cases
from run_benefitlens_campaign import KST, atomic_json, single_writer


def verified_bytes(ref):
    raw = read_source_bytes(ref['path'], storage=ref.get('storage'))
    if sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('Writing source decoded hash mismatch')
    return raw


def prepare_document(doc):
    raw, text_bytes = verified_bytes(doc['raw']), verified_bytes(doc['text'])
    text = text_bytes.decode('utf-8')
    if len(text.strip()) < 100 or '\x00' in text:
        raise ValueError('Writing source has unusable text')
    if doc.get('access_scope') != 'public':
        raise ValueError('Writing source is not classified as public')
    fetched_at = doc['raw'].get('at')
    if not fetched_at and doc.get('origin') == 'legacy_verified_bytes':
        path = project.ROOT/'.local/experience-originals/collection.json'
        old = json.loads(path.read_text())['documents']
        fetched_at = next((d.get('fetched_at') for d in old if d.get('sha256') == doc['raw']['sha256']), None)
    if not fetched_at:
        raise ValueError('Original capture timestamp is unavailable')
    return {'document_id': doc['document_id'], 'canonical_url': doc['url'],
            'content_sha256': sha256(raw).hexdigest(), 'text_sha256': sha256(text_bytes).hexdigest(),
            'kind': 'pdf' if raw.startswith(b'%PDF-') else 'html',
            'raw_path': doc['raw']['path'], 'text_path': doc['text']['path'],
            'fetched_at': fetched_at,
            'metadata': {'writing_corpus': {**doc, 'full_text': text,
                'semantic_review': 'unreviewed', 'model_called': False}}}


def prepare_case(case, documents, db_ids):
    missing = set(case['document_ids']) - documents.keys()
    if missing:
        raise ValueError('Case references an unavailable original')
    docs = [documents[key] for key in case['document_ids']]
    claim = case.get('outcome') or 'unknown'
    if claim not in {'selected', 'not_selected', 'unknown', 'honorable_mention'}:
        raise ValueError('Unsupported case outcome claim')
    return {'case_key': 'writing-corpus-' + case['case_id'],
            'program_id': case['program_id'], 'person_name': case.get('person_name'),
            'application_round': case.get('cycle'), 'outcome_claim': claim,
            'evidence_status': 'public_original_collected', 'source_url': docs[0]['url'],
            'payload': {**case, 'source_family': 'writing_corpus_expansion_v1',
                'document_ids': [db_ids[key] for key in case['document_ids']],
                'collector_document_ids': case['document_ids'],
                'source_urls': sorted({d['url'] for d in docs}),
                'semantic_review': 'unreviewed', 'approved_pattern': False, 'active': True}}


def run():
    from psycopg.types.json import Jsonb
    (ROOT/'postgres-import').mkdir(parents=True, exist_ok=True)
    with single_writer(ROOT/'postgres-import'):
        state = json.loads((ROOT/'status.json').read_text())
        if state['state'] != 'complete':
            raise ValueError('Wait for the writing collector to complete before importing')
        corpus = json.loads(verified_bytes(state['corpus']))
        documents = {d['document_id']: d for d in corpus['source_documents'] if d['status'] == 'collected'}
        prepared = {key: prepare_document(doc) for key, doc in documents.items()}
        cases = index_cases(list(documents.values()))
        published_cases = {c['case_id']:c for c in corpus.get('experience_cases', [])}
        for case in cases:
            published = published_cases.get(case['case_id'], {})
            if sorted(published.get('document_ids', [])) != sorted(case['document_ids']):
                raise ValueError('Published case document index does not match collected originals')
            case['experience_passages'] = published.get('experience_passages', [])
            for key in ('quality_audit','evidence_spans'):
                if key in published:
                    case[key] = published[key]
        db_ids = {}
        with project.connect() as conn:
            for key, doc in prepared.items():
                row = conn.execute('''INSERT INTO benefitlens.pipeline_document
                    (document_id,canonical_url,content_sha256,text_sha256,kind,
                     raw_path,text_path,fetched_at,metadata)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(canonical_url_hash,content_sha256) DO UPDATE
                    SET metadata=benefitlens.pipeline_document.metadata || excluded.metadata
                    RETURNING document_id''',
                    tuple(doc[k] for k in ('document_id','canonical_url','content_sha256','text_sha256',
                                          'kind','raw_path','text_path','fetched_at')) + (Jsonb(doc['metadata']),)).fetchone()
                db_ids[key] = row[0]
            for case in cases:
                item = prepare_case(case, documents, db_ids)
                conn.execute('''INSERT INTO benefitlens.experience_discovery_case
                    (case_key,program_id,person_name,application_round,outcome_claim,
                     evidence_status,source_url,payload)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(case_key) DO UPDATE SET program_id=excluded.program_id,
                      person_name=excluded.person_name,application_round=excluded.application_round,
                      outcome_claim=excluded.outcome_claim,evidence_status=excluded.evidence_status,
                      source_url=excluded.source_url,payload=excluded.payload,updated_at=now()''',
                    tuple(item[k] for k in ('case_key','program_id','person_name','application_round',
                                           'outcome_claim','evidence_status','source_url')) + (Jsonb(item['payload']),))
                for alias in case.get('merged_case_ids', []):
                    conn.execute('''UPDATE benefitlens.experience_discovery_case
                        SET evidence_status='merged_duplicate',payload=payload || %s,updated_at=now()
                        WHERE case_key=%s AND payload->>'source_family'='writing_corpus_expansion_v1' ''',
                        (Jsonb({'active':False,'merged_into':item['case_key']}),'writing-corpus-'+alias))
            actual = conn.execute("SELECT count(*) FROM benefitlens.experience_discovery_case WHERE payload->>'source_family'='writing_corpus_expansion_v1' AND evidence_status='public_original_collected'").fetchone()[0]
        result = {'at_kst': datetime.now(KST).isoformat(), 'corpus': state['corpus'],
                  'documents_imported': len(db_ids), 'cases_imported': len(cases),
                  'database_total_cases_in_source_family': actual,
                  'program_case_counts': dict(Counter(c['program_id'] for c in cases)),
                  'outcome_counts': dict(Counter(c.get('outcome') or 'unknown' for c in cases)),
                  'document_id_mapping': db_ids, 'approved_patterns_added': 0, 'model_called': False}
        atomic_json(ROOT/'postgres-import/status.json', result)
        print(json.dumps({k:v for k,v in result.items() if k not in ('document_id_mapping','corpus')}, ensure_ascii=False))


if __name__ == '__main__':
    run()
