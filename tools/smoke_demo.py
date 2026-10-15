"""Start and stop a private demo process, checking only this repository's DB."""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import project


def main():
    config = project.settings()
    port = int(config['BENEFITLENS_DEMO_PORT'])
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', port))
    process = subprocess.Popen([sys.executable, '-m', 'data_platform.pipeline_demo', '--port', str(port)],
                               cwd=project.ROOT, env=project.environment(), stdout=subprocess.DEVNULL)
    def get(path):
        with urlopen(f'http://127.0.0.1:{port}' + path, timeout=10) as response:
            return json.load(response)
    try:
        for attempt in range(50):
            if process.poll() is not None:
                raise RuntimeError('Demo process exited before readiness.')
            try:
                stats = get('/api/pipeline/stats')
                break
            except OSError:
                if attempt == 49:
                    raise
                time.sleep(0.1)
        with project.connect() as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            total = conn.execute('SELECT count(*) FROM benefitlens.opportunities').fetchone()[0]
            source = conn.execute('SELECT count(*) FROM benefitlens.support_discovery_record').fetchone()[0]
        assert stats['normalized_records'] == total
        assert stats['source_records'] == source
        reviewed = get('/api/pipeline/opportunities')
        assert reviewed['meta']['total'] == stats['reviewed_demo']
        whole = get('/api/pipeline/opportunities?scope=all')
        assert whole['meta']['total'] == total
        chosen = next(p for p in reviewed['data'] if p['program_id']=='google-phd-fellowship')
        detail = get('/api/pipeline/detail?id=' + chosen['opportunity_id'])
        assert detail['data']['program_id'] == detail['knowledge']['program_id']
        report = {'api_smoke_passed': True, 'source_candidates': source, 'opportunities': total,
                  'reviewed_demo': stats['reviewed_demo'], 'reviewed_experience_cases': stats['experience_cases'],
                  'process_stopped_after_check': True}
        output = project.ROOT / '.local/demo-smoke.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == '__main__':
    main()
