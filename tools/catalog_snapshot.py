"""Generate a small aggregate inventory from a completed private data bundle."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('bundle', type=Path)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    manifest = json.loads((bundle / 'manifest.json').read_text())
    counts = json.loads((bundle / 'table-counts.json').read_text())
    root = Path(__file__).resolve().parents[1]
    output = root / 'data/catalogs'
    output.mkdir(parents=True, exist_ok=True)
    fixture = json.loads((root / 'pipeline/config/proposal_demo_annotations.json').read_text())
    record = {'snapshot_started_at': manifest['started_at'], 'snapshot_completed_at': manifest['completed_at'],
              'timezone': 'Asia/Seoul', 'bundle_id': bundle.name,
              'manifest_sha256': hashlib.sha256((bundle / 'manifest.json').read_bytes()).hexdigest(),
              'tables': counts, 'files': manifest['files'],
              'bundle_bytes': sum(item['bytes'] for item in manifest['artifacts'].values()),
              'units': {'source_candidates': counts['benefitlens.support_discovery_record'],
                        'opportunity_rows_not_all_reviewed': counts['benefitlens.opportunities'],
                        'document_versions': counts['benefitlens.pipeline_document'],
                        'discovered_experience_candidates': counts['benefitlens.experience_discovery_case'],
                        'reviewed_demo_opportunities_from_config': len(fixture['opportunities']),
                        'reviewed_knowledge_programs': len(fixture['program_knowledge']),
                        'reviewed_knowledge_cases': sum(len(item['experience_cases']) for item in fixture['program_knowledge'])},
              'replication': 'Private bundle must be delivered separately. No source text or personal profile is included in this aggregate.'}
    (output / '2026-10-15-inventory.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    shutil.copyfile(bundle / 'schema.sql', output / '2026-10-15-schema.sql')
    print(json.dumps(record['units'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
