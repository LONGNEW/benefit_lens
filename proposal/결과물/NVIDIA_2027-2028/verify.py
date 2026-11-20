"""Load the frozen packet into disposable PostgreSQL 16 and verify it."""
import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import time

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--container-file', type=Path, help='Use a prestarted isolated packet database, then stop it')
args = parser.parse_args()
contract = json.loads((root/'contract.json').read_text())
schema = contract['schema']
order = contract['load_order']
assert all(re.fullmatch('[a-z][a-z_0-9]*', n) for n in [schema, *order])
records = defaultdict(list)
for p in sorted((root/'data').glob('*.json')):
    for table, rows in json.loads(p.read_text()).items():
        assert table in order and isinstance(rows,list), (p,table)
        records[table].extend(rows)
assert records and all(records[t] for t in order), list(records)
docs = {d['document_id']:d for d in records['source_document']}
assert len(docs)==len(records['source_document'])
verified_files = 0
for d in docs.values():
    for path_key,hash_key in [('raw_path','raw_sha256'),('text_path','text_sha256')]:
        if not d.get(path_key):
            assert not d.get(hash_key), d['document_id']
            continue
        p = Path(d[path_key])
        if not p.is_absolute():
            p = root/p
        assert sha256(p.read_bytes()).hexdigest()==d[hash_key], (d['document_id'],path_key)
        verified_files += 1
verified_spans = 0
for e in records['evidence']:
    d = docs[e['document_id']]
    p = Path(d['text_path'])
    if not p.is_absolute():
        p = root/p
    text = p.read_text()
    norm = lambda x: ' '.join(x.split())
    q = e['quote']
    assert q.strip() and norm(q) in norm(text), (e['evidence_id'],q)
    loc=e['locator']
    m=re.fullmatch(r'L(\d+)(?:-L?(\d+))?',loc)
    if m:
        start,end=int(m[1]),int(m[2] or m[1])
        assert norm(q) in norm('\n'.join(text.split('\n')[start-1:end])), (e['evidence_id'],loc)
        verified_spans+=1
    else:
        raise ValueError(('unsupported locator',e['evidence_id'],loc))
literal=lambda x: "'"+x.replace("'","''")+"'"
assert not any('\0' in json.dumps(records[t],ensure_ascii=False) for t in order)

if args.container_file:
    cid=args.container_file.read_text().strip()
    assert re.fullmatch('[0-9a-f]{64}',cid)
else:
    cid=subprocess.check_output([
        'docker','run','--rm','-d','--pull=never','--network=none',
        '--tmpfs','/var/lib/postgresql/data',
        '-e','POSTGRES_HOST_AUTH_METHOD=trust','-e','POSTGRES_DB=benefitlens_delivery',
        'postgres:16-alpine'],text=True,timeout=30).strip()

def psql(sql, reject=None):
    r=subprocess.run(['docker','exec','-i',cid,'psql','-X','-U','postgres',
                      '-d','benefitlens_delivery','-qAt','-v','ON_ERROR_STOP=1','-v','VERBOSITY=verbose'],
                     input=sql,text=True,capture_output=True,timeout=40)
    if reject:
        assert r.returncode and reject in r.stderr, (reject,r.stderr)
        return r.stderr.splitlines()[0]
    if r.returncode:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()

try:
    end=time.monotonic()+20
    while True:
        try:
            version=psql("SELECT current_setting('server_version');")
            break
        except RuntimeError:
            if time.monotonic()>end: raise
            time.sleep(.4)
    assert version.startswith('16.'), version
    assert psql(f"SELECT count(*) FROM information_schema.tables WHERE table_schema={literal(schema)};")=="0", "packet database is not empty"
    psql((root/'schema.sql').read_text())
    inserts=[]
    for t in order:
        cols=set(psql(f"SELECT column_name FROM information_schema.columns WHERE table_schema={literal(schema)} AND table_name={literal(t)};").splitlines())
        assert cols and all(set(row)<=cols for row in records[t]), (t,cols,records[t][0])
        data=literal(json.dumps(records[t],ensure_ascii=False))
        inserts.append(f"INSERT INTO {schema}.{t} SELECT * FROM jsonb_populate_recordset(NULL::{schema}.{t},{data}::jsonb) ON CONFLICT DO NOTHING;")
    sql="BEGIN;\n"+"\n".join(inserts)+"\nCOMMIT;"
    snapshot=lambda: {t: json.loads(psql(f"SELECT COALESCE(jsonb_agg(to_jsonb(q) ORDER BY to_jsonb(q)::text),'[]'::jsonb) FROM {schema}.{t} q;")) for t in order}
    psql(sql)
    first=snapshot()
    for t in order:
        assert len(first[t])==len(records[t]), (t,len(first[t]),len(records[t]))
    psql(sql)
    assert snapshot()==first, 'second load differs'
    checks={}
    if (root/'checks.sql.json').is_file():
        for name, test in json.loads((root/'checks.sql.json').read_text()).items():
            if 'sqlstate' in test:
                checks[name]=psql('BEGIN;\n'+test['sql']+'\nROLLBACK;',reject=test['sqlstate'])
            else:
                actual=json.loads(psql(test['sql']))
                assert actual==test['expected'],(name,actual,test['expected'])
                checks[name]=actual
    q4=(root/'queries.sql').read_text().split('-- Q4.',1)[1].split('\n',1)[1]
    assert psql(q4)=='', 'Q4 found contract violations'
    checks['contract_violation_rows']=0
    outputs={}
    for p in sorted((root/'queries').glob('*.sql')):
        result=json.loads(psql(p.read_text()))
        serialized=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
        if len(serialized)>10000:
            serialized=json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n'
        assert len(serialized)<=10000, p
        (root/f'{p.stem}.result.json').write_text(serialized)
        outputs[p.stem]=f'{p.stem}.result.json'
    report={'postgres_version':version, 'rows':{t:len(first[t]) for t in order},
            'raw_or_text_hashes_verified':verified_files, 'source_spans_verified':verified_spans,
            'same_packet_loaded_twice_without_changes':True, 'checks':checks, 'query_results':outputs,
            'scope':'temporary isolated PostgreSQL; no production migrations or writes'}
    (root/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))
finally:
    subprocess.run(['docker','stop','-t','2',cid],capture_output=True,check=False,timeout=15)
