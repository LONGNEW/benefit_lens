"""Reproduce archived search and provenance measurements without network or DB writes."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

ROOT = Path('/home/longnew/Desktop/course_work')
RUN = ROOT / 'tmp/benefitlens_auto_dictionary_20261018/run-v3'
REPO = ROOT / '26-2 학기/데이터엔지니어링/benefit_lens'


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def dump(path, data):
    payload = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    assert len(payload) <= 10000 and '\u00b7' not in payload, path
    path.write_text(payload)


def host(url):
    return (urlsplit(url).hostname or '(missing)').lower()


def top(counts, limit=5):
    return dict(sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:limit])


def run_audit(run):
    status = json.loads((run / 'status.json').read_text())
    db = run / 'expansion.sqlite'
    with sqlite3.connect(db.resolve().as_uri()+'?mode=ro', uri=True) as c:
        c.execute('PRAGMA query_only=1')
        docs = list(c.execute('SELECT id,url,wave,status,payload FROM document'))
        queries = list(c.execute('''SELECT q.id,q.wave,q.status,q.attempts,q.q,t.term,t.lane,t.seed
            FROM query q JOIN term t ON t.id=q.term_id'''))
        wave_rows = list(c.execute('SELECT number,status,payload FROM wave ORDER BY number'))
        links = list(c.execute('SELECT query_id,document_id FROM query_document'))
    assert len(queries) == sum(status['counters']['query'].values())
    assert dict(Counter(row[3] for row in docs)) == status['counters']['document']
    collected = {}
    for doc_id, url, wave, state, payload in docs:
        if state == 'collected':
            data = json.loads(payload)
            collected[doc_id] = (wave, data['content_sha256'], host(data['final_url']), data['kind'])
    lookup = {row[0]: row for row in queries if row[2] == 'executed'}
    by_lane, by_seed = defaultdict(set), defaultdict(set)
    for qid, docid in links:
        if qid in lookup and docid in collected:
            row = lookup[qid]
            by_lane[docid].add(row[6]); by_seed[docid].add(row[7])
    hosts = Counter(x[2] for x in collected.values())
    routes = Counter('ko' if re.search('[가-힣]', row[5]) else 'en' for row in lookup.values())
    query_lanes = Counter(row[6] for row in lookup.values())
    link_lane = Counter('+'.join(sorted(by_lane[k])) or '(none)' for k in collected)
    link_seed = Counter('+'.join('seed' if n else 'expanded' for n in sorted(by_seed[k])) or '(none)' for k in collected)
    seen_hashes, waves = set(), []
    for wave, state, payload in wave_rows:
        w = json.loads(payload)
        d = [x for x in collected.values() if x[0] == wave]
        hashes = {x[1] for x in d}
        q = [row for row in lookup.values() if row[1] == wave]
        r = [row for row in queries if row[1] == wave]
        h = Counter(x[2] for x in d)
        elapsed = (datetime.fromisoformat(w['completed_at'])-datetime.fromisoformat(w['started_at'])).total_seconds() if state == 'closed' else None
        waves.append({'wave':wave,'state':state,'executed_queries':len(q),'failed_queries':sum(x[2]=='failed' for x in r),
                      'query_row_attempts':sum(x[3] for x in r),
                      'ko_route':sum(bool(re.search('[가-힣]', x[5])) for x in q),
                      'en_route':sum(not bool(re.search('[가-힣]', x[5])) for x in q),
                      'document_states':dict(Counter(x[3] for x in docs if x[2]==wave)),
                      'collected_distinct_content_sha256':len(hashes),
                      'first_seen_content_sha256':len(hashes-seen_hashes),
                      'hashes_already_seen_in_previous_waves':len(hashes&seen_hashes),
                      'distinct_final_hostnames':len(h),'top_final_hostnames':top(h,3),
                      'closed_wave_elapsed_seconds':elapsed,
                      'collected_urls_per_wall_hour':round(len(d)*3600/elapsed,2) if elapsed else None})
        seen_hashes |= hashes
    return {'checkpoint':str(run/'status.json'),'checkpoint_sha256':digest(run/'status.json'),
            'sqlite':str(db),'as_of_saved':status['updated_at'],'saved_status':status['status'],
            'methods':{'language':'regex any Hangul in term matches dictionary_expansion.py:325 template selection; not document language or country',
                       'host':'lowercased hostname of final_url of collected URL rows; no merge of subdomains or organizations',
                       'wave':'document.wave is first registration wave; unique SHA-256 is bytes dedup, not program/round dedup',
                       'query_lanes':'query linked to term.lane; document lane sets come from executed query links and can overlap',
                       'attempts':'sum query row attempts, not HTTP requests, tool calls, LLM tokens, or currency',
                       'elapsed':'saved wave start to completion; open wave excluded from rates; not an application serving load test'},
            'executed_queries':len(lookup),'distinct_executed_query_strings':len({x[4] for x in lookup.values()}),
            'executed_template_routes':dict(routes),'executed_query_lanes':dict(query_lanes),
            'collected_url_rows':len(collected),'distinct_content_sha256':len(seen_hashes),
            'collected_document_kinds':dict(Counter(x[3] for x in collected.values())),
            'collected_url_linked_lanes':dict(link_lane),'collected_url_linked_seed_status':dict(link_seed),
            'distinct_final_hostnames':len(hosts),'top_final_hostnames':top(hosts,10),
            'top_10_host_url_share_pct':round(100*sum(top(hosts,10).values())/len(collected),2),
            'wave_breakdown':waves,
            'cannot_infer':['current approved program rounds','eligible countries or aid variety','web completeness','serving capacity','causal advantage of expansion'],
            'fresh_searches':0,'production_db_writes':0}


ANCHORS = {
 'nvidia-official': (34, 'required in-person summer internship before the award year'),
 'nvidia-recipients': (43, 'Rohan Yadav'),
 'rohan-index': (187, 'My application statement for the NVIDIA Graduate Fellowship'),
 'rohan-proposal': (None, 'Performance Accessibility'),
 'yuke-proposal': (1, 'Research Summary'),
 'yuke-panel': (37, 'Yuke advised highlighting key insights and comparing them to existing work.'),
}


def source_audit(repo):
    manifest = repo / '.local/experience-originals/collection.json'
    m = json.loads(manifest.read_text())
    r = []
    for d in m['documents']:
        if d['id'] not in ANCHORS:
            continue
        text_path, raw_path = Path(d['text_path']), Path(d['raw_path'])
        assert digest(text_path) == d['text_sha256'] and digest(raw_path) == d['sha256']
        lines = text_path.read_text().splitlines()
        expected, anchor = ANCHORS[d['id']]
        line = next((i for i,t in enumerate(lines,1) if anchor in t), None) if expected is None else expected
        assert line and anchor in lines[line-1], (d['id'], line)
        r.append({k:d[k] for k in ('id','program_id','role','url','document_id','fetched_at','sha256','text_sha256','raw_path','text_path')} | {'case_id':d.get('case_id'),'verified_line':line,'exact_anchor':anchor,'hashes_recomputed_match':True})
    assert len(r) == len(ANCHORS)
    return {'manifest':str(manifest),'manifest_sha256':digest(manifest),'scope':m['scope'],
            'roles_and_lineage':r,
            'limits':{'nvidia-official':'stored official notice version; current round not re-verified',
                      'N1':'index links public application; official outcome list confirms named recipient, not exact final submission or causation',
                      'N2':'PDF and panel linked in manifest; paper title and attributed advice do not prove submission round, finality, or exact official prompt'},
            'no_new_fetch':True}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, default=RUN)
    p.add_argument('--repo', type=Path, default=REPO)
    p.add_argument('--output', type=Path, default=Path(__file__).resolve().parent)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    dump(a.output/'archived_run_audit.json', run_audit(a.run))
    dump(a.output/'provenance_audit.json', source_audit(a.repo))


if __name__ == '__main__':
    main()
