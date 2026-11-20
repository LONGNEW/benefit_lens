"""Run LLM analysis on cached full experience texts for the ten student examples."""
import argparse
from collections import Counter
import hashlib
import json
import sys
from pathlib import Path

import project
sys.path.insert(0, str(project.ROOT/'pipeline/src'))
from data_platform.writing_patterns import (prepare_program, extract, knowledge_from_result,
                                            LEGACY_PROMPT_VERSION, PROMPT_VERSION)


def receipt_version_summary(program_receipts):
    """Describe returned receipts, including reuse; do not relabel them as v3."""
    runs = []
    for program_id, receipt in program_receipts:
        legacy = receipt.get('prompt_version') == LEGACY_PROMPT_VERSION
        scope = ('legacy_v2_requires_scope_review' if legacy else
                 'v3_scope_bound' if receipt.get('prompt_version') == PROMPT_VERSION else 'unknown_version')
        runs.append({'program_id': program_id, 'extractor': receipt.get('extractor') or 'unknown',
                     'prompt_version': receipt.get('prompt_version', 'unknown'),
                     'input_sha256': receipt.get('input_sha256'),
                     'scope_validation': scope,
                     'approved_handoff': False,
                     'original_model_run_at': receipt.get('created_at')})
    extractors = sorted({r['extractor'] for r in runs})
    return {'pattern_extractor': extractors[0] if len(extractors) == 1 else 'mixed' if extractors else 'none',
            'pattern_extractors': extractors,
            'pattern_prompt_versions': sorted({r['prompt_version'] for r in runs}),
            'pattern_scope_states': dict(Counter(r['scope_validation'] for r in runs)),
            'pattern_extraction_runs': runs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection', required=True, type=Path, help='Existing collection.json; no downloads.')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    folder = project.ROOT/'data/sample'
    sample = json.loads((folder/'programs.json').read_text())
    collection = json.loads(args.collection.read_text())
    source_config = json.loads((project.ROOT/'pipeline/config/proposal_demo_sources.json').read_text())
    knowledge = []
    receipts = []
    for old in sample['program_knowledge']:
        context = prepare_program(sample, collection, source_config, old['program_id'])
        print(json.dumps({'program': old['program_id'], 'status': 'full_text_input_ready',
                          'full_documents': len(context['documents']),
                          'characters': sum(len(d['text']) for d in context['documents'])}), flush=True)
        receipt = extract(context, project.ROOT/'.local/pattern-runs', force=args.force)
        receipts.append((old['program_id'], receipt))
        knowledge.append(knowledge_from_result(old, context, receipt))
        print(json.dumps({'program': old['program_id'], 'status': 'validated',
                          'extractor': receipt.get('extractor'),
                          'scope_validation': receipt_version_summary([(old['program_id'], receipt)])['pattern_extraction_runs'][0]['scope_validation'],
                          'patterns': len(receipt['result']['writing_pattern'])}), flush=True)
    sample['program_knowledge'] = knowledge
    raw = (json.dumps(sample, ensure_ascii=False, indent=2)+'\n').encode()
    manifest = json.loads((folder/'manifest.json').read_text())
    manifest.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                    experience_summaries=sum(len(k['experience_cases']) for k in knowledge),
                    criterion_observations=sum(len(k['criterion_observations']) for k in knowledge),
                    outcome_comparisons=sum(len(k['outcome_comparisons']) for k in knowledge),
                    writing_patterns=sum(len(k['writing_pattern']) for k in knowledge))
    manifest.update(receipt_version_summary(receipts))
    # Only publish the bundle after all model calls and source validations succeed.
    (folder/'programs.json').write_bytes(raw)
    (folder/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'programs': len(knowledge), 'patterns': manifest['writing_patterns'],
                      'collection_executed': False}), flush=True)


if __name__ == '__main__':
    main()
