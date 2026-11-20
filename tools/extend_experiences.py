"""Fetch only the reviewed experience URLs listed in experience_additions.json."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import project
sys.path.insert(0, str(project.ROOT/'pipeline/src'))
from data_platform.proposal_pipeline import SourceCollector
from data_platform.demo_scope import validate_student_examples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection', required=True, type=Path)
    args = parser.parse_args()
    config = json.loads((project.ROOT/'pipeline/config/experience_additions.json').read_text())
    folder = project.ROOT/'data/sample'
    sample = json.loads((folder/'programs.json').read_text())
    validate_student_examples(sample['opportunities'])
    collection = json.loads(args.collection.read_text())
    output = project.ROOT/'.local/experience-originals'
    output.mkdir(parents=True, exist_ok=True)
    if (output/'collection.json').exists():
        prior = json.loads((output/'collection.json').read_text())
        documents = {d['id']: d for d in collection['documents']}
        documents.update({d['id']: d for d in prior['documents']})
    else:
        documents = {d['id']: d for d in collection['documents']}
    sources = json.loads((project.ROOT/'pipeline/config/proposal_demo_sources.json').read_text())
    indexed = {s['id']: s for s in sources['sources']}
    collector = SourceCollector(output)
    for source in config['sources']:
        old = documents.get(source['id'])
        if (old and old.get('status') == 'collected' and old['url'] == source['url']
                and Path(old['text_path']).is_file() and Path(old['raw_path']).is_file()):
            valid = (hashlib.sha256(Path(old['text_path']).read_bytes()).hexdigest() == old['text_sha256']
                     and hashlib.sha256(Path(old['raw_path']).read_bytes()).hexdigest() == old['sha256'])
        else:
            valid = False
        doc = old if valid else collector.fetch(source)
        documents[source['id']] = doc
        print(json.dumps({'id': source['id'], 'status': doc['status'], 'error': doc.get('error')}, ensure_ascii=False), flush=True)
        indexed[source['id']] = source
    merged = {'documents': list(documents.values()), 'scope': 'reviewed_experience_urls_only',
              'robots_checked': sorted(collector.robots)}
    (output/'collection.json').write_text(json.dumps(merged, ensure_ascii=False, indent=2)+'\n')
    failed = [s['id'] for s in config['sources'] if documents[s['id']]['status'] != 'collected']
    if failed:
        raise ValueError('Originals not published because sources failed: ' + ', '.join(failed))
    for knowledge in sample['program_knowledge']:
        cases = {c['case_id']: c for c in knowledge['experience_cases']}
        for case in cases.values():
            case.update(config['existing_case_updates'].get(case['case_id'], {}))
        for case in config['cases']:
            if case['program_id'] != knowledge['program_id']:
                continue
            value = {k: v for k, v in case.items() if k != 'program_id'}
            value['source_refs'] = [{'id': s['id'], 'source_id': s['id'], 'url': documents[s['id']]['final_url'],
                                     'locator': s['role'], 'content_sha256': documents[s['id']]['sha256']}
                                    for s in config['sources'] if value['case_id'] in s['experience_case_ids']]
            value['fragments'] = []
            cases.setdefault(case['case_id'], value)
        knowledge['experience_cases'] = list(cases.values())
    sources['sources'] = list(indexed.values())
    sources['evaluation_criteria'] = config['evaluation_criteria']
    (project.ROOT/'pipeline/config/proposal_demo_sources.json').write_text(json.dumps(sources, ensure_ascii=False, indent=2)+'\n')
    raw = (json.dumps(sample, ensure_ascii=False, indent=2)+'\n').encode()
    manifest = json.loads((folder/'manifest.json').read_text())
    manifest.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                    experience_summaries=sum(len(k['experience_cases']) for k in sample['program_knowledge']))
    (folder/'programs.json').write_bytes(raw)
    (folder/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'collection': str(output/'collection.json'), 'cases': manifest['experience_summaries']}))


if __name__ == '__main__':
    main()
