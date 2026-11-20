"""Independent coverage and no-student-coercion checks on full derived files."""
import argparse
from collections import Counter
from datetime import datetime
from functools import lru_cache
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from audit_benefitlens_experience_production import jsonl
from expand_benefitlens_experience_quality import digest,verified_bytes
from run_benefitlens_campaign import atomic_json,KST


def validate(root,experience_root,notice_root):
    quality=root/'quality';personal=root/'personalization';counts=Counter()
    original_docs={row['document_id']:row for row in jsonl(experience_root/'documents.jsonl.gz')}
    derived_docs={row['document_id']:row for row in jsonl(quality/'documents.jsonl.gz')}
    assert original_docs.keys()==derived_docs.keys()
    for did,doc in original_docs.items():
        assert derived_docs[did]['text_ref']==doc['text_ref']
        assert derived_docs[did]['raw_ref']==doc['raw_ref']
        counts['original_document_refs_preserved']+=1
        counts['documents_with_storage_metadata_warning']+=bool(derived_docs[did].get('storage_warnings'))
    @lru_cache(maxsize=8)
    def text_for(did):
        return verified_bytes(original_docs[did]['text_ref'])[0].decode('utf8')
    for row in jsonl(quality/'zones.jsonl.gz'):
        text=text_for(row['document_id']);a,b=row['char_start'],row['char_end']
        assert 0<=a<b<=len(text)
        assert digest(text[a:b].encode())==row['span_sha256']
        counts['source_zone_spans_checked']+=1
    original=iter(jsonl(experience_root/'segments.jsonl.gz'))
    for row in jsonl(quality/'segments.jsonl.gz'):
        source=next(original)
        for key in ('segment_id','document_id','char_start','char_end','segment_text_sha256'):
            assert row[key]==source[key]
        assert row['source_span_hash_verified']
        counts['original_segment_spans_preserved']+=1
    assert next(original,None) is None
    for row in jsonl(quality/'occurrences.jsonl.gz'):
        if row['quality']['auto_excluded']:
            assert row['source_binding_verified']
            if row.get('exclusion_basis')=='source_verified_exact_context_of_codex_irrelevant_group':
                assert row['reference_relevance']=='irrelevant'
            else:
                assert row['quality']['zone_ids']
            counts['evidence_bound_noise_exclusions']+=1
        counts['occurrence_projection_rows']+=1
    notice_ids={r['candidate_key'] for r in jsonl(notice_root/'notices.jsonl.gz')}
    actual_ids=set()
    for row in jsonl(personal/'requirements.jsonl.gz'):
        assert row['candidate_key'] not in actual_ids
        actual_ids.add(row['candidate_key'])
        assert row['personal_eligibility_decision']=='unknown' and row['live_questions_issued']==0
        for values in row['input_channels'].values():
            for item in values:
                assert item['required_user_input'] is False and item['live_question_issued'] is False
                if item['subject']!='applicant':assert item['proposed_question'] is None
                if item['modality']=='preferred':assert item['route'] not in ('common_profile','program_followup')
                counts['input_candidate_subject_and_modality_checks']+=1
        for doc in row['writing_material_requests']:
            if doc['authorship_candidate'] in ('institution','referee','employer'):
                assert not doc['student_authored_draft']
            counts['document_authorship_checks']+=1
        counts['notice_requirement_rows']+=1
    assert actual_ids==notice_ids
    result={'at_kst':datetime.now(KST).isoformat(),'state':'all_checks_passed','counts':dict(counts),
        'new_semantic_meaning_approvals':0,'model_calls':0,'source_database_writes':0,
        'validator_sha256':digest(Path(__file__).read_bytes()),
        'quality_summary_sha256':digest((quality/'summary.json').read_bytes()),
        'personalization_summary_sha256':digest((personal/'summary.json').read_bytes())}
    atomic_json(root/'coverage-validation.json',result);print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--experience-root',type=Path,required=True)
    parser.add_argument('--notice-root',type=Path,required=True)
    a=parser.parse_args();validate(a.root,a.experience_root,a.notice_root)
