"""Validate every generated structural locator against its unchanged source text."""
import argparse
from bisect import bisect_right
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_benefitlens_experience_production import digest, encoded, jsonl
from benefitlens_source_storage import read_source_bytes
from run_benefitlens_campaign import atomic_json, checked_root, KST


def validate(root, notice_root=None):
    summary = json.loads((root/'summary.json').read_text())
    counts = Counter()
    global_segments = iter(jsonl(root/'segments.jsonl.gz'))
    for document in jsonl(root/'documents.jsonl.gz'):
        counts['documents_checked'] += 1
        if document['source_verification']['state'] != 'raw_and_text_hash_verified':
            assert not document.get('segments_ref')
            counts['documents_held'] += 1
            continue
        ref = document['text_ref']
        original = read_source_bytes(ref['path'], storage=ref['storage'])
        assert digest(original) == ref['sha256']
        text = original.decode('utf8')
        line_ends = [match.start() for match in re.finditer('\n', text)]
        ref = document['segments_ref']
        raw_package = read_source_bytes(ref['path'], storage=ref['storage'])
        assert digest(raw_package) == ref['sha256']
        package = json.loads(raw_package)
        assert package['document_id'] == document['document_id']
        assert package['text_ref'] == document['text_ref']
        assert package['semantic_analysis_performed'] is False
        assert len(package['segments']) == document['segment_count']
        cursor = 0
        for span in package['segments']:
            assert next(global_segments) == span
            start, end = span['char_start'], span['char_end']
            assert 0 <= start < end <= len(text)
            assert span['segment_text_sha256'] == digest(text[start:end])
            assert span['line_start'] == bisect_right(line_ends, start-1)+1
            assert span['line_end'] == bisect_right(line_ends, end-1)+1
            if span['kind'] != 'source_dom_paragraph_aligned':
                assert cursor <= start and not text[cursor:start].strip()
                assert end-start <= 2000
                cursor = end
            counts['segments_checked'] += 1
        assert not text[cursor:].strip()
        counts['documents_with_complete_non_whitespace_coverage'] += 1
    assert next(global_segments, None) is None
    assert counts['documents_checked'] == summary['document_inventory']['linked_existing_documents']
    assert counts['segments_checked'] == summary['document_inventory']['structural_segment_rows']
    cases = {row['case_key']: row for row in jsonl(root/'cases.jsonl.gz')}
    assert len(cases) == summary['case_inventory']['production_scope_rows']
    jobs = {row['case_key']: row for row in jsonl(root/'next-processing-queue.jsonl.gz')}
    assert cases.keys() == jobs.keys()
    for key, case in cases.items():
        assert not case['new_semantic_judgment']
        assert not jobs[key]['automatic_approval_allowed']
        if case['next_stage'] == 'repair_source_inputs':
            assert not case['analysis_stages']['original_integrity_verified']
        counts['cases_checked'] += 1
    source_manifest = None
    if notice_root:
        requirements = {row['candidate_key']: row for row in jsonl(root/'personalization-requirements.jsonl.gz')}
        for source in jsonl(notice_root/'notices.jsonl.gz'):
            record = requirements.pop(source['candidate_key'])
            actual = {c['condition_id']: c for values in record['input_channels'].values() for c in values}
            expected = {c['condition_id']: c for c in source['conditions']}
            assert actual == expected
            for target_key, source_key in (
                ('source_projection_state', 'projection_state'), ('notice_scope', 'scope'),
                ('source_original_ref', 'original_ref'), ('source_origin', 'source_origin'),
                ('variant_count', 'variant_count'), ('unresolved_fields', 'unresolved_fields')):
                assert record[target_key] == source.get(source_key)
            assert record['decision'] == 'unknown' and record['user_questions_issued'] == 0
            counts['notice_requirement_records_checked'] += 1
            counts['unchanged_condition_objects_checked'] += len(actual)
        assert not requirements
        path = notice_root/'manifest.json'
        if path.exists():
            source_manifest = {'path': str(path), 'sha256': digest(path.read_bytes())}
    result = {'at_kst': datetime.now(KST).isoformat(), 'state': 'all_checks_passed',
              'counts': dict(counts), 'network_requests': 0, 'model_calls': 0,
              'database_writes': 0, 'new_semantic_judgments': 0,
              'summary_sha256': digest((root/'summary.json').read_bytes()),
              'frozen_notice_manifest': source_manifest,
              'validation_implementation_sha256': digest(Path(__file__).read_bytes())}
    atomic_json(root/'structural-validation.json', result)
    print(encoded(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--notice-root', type=Path)
    args = parser.parse_args()
    validate(checked_root(args.root), args.notice_root)
