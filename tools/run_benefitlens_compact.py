"""Extract/group source sentences in code, then normalize many groups per LLM call.

The default inference budget is two CLI calls. It never performs document-wide
model reviews or retries an entire batch because one group is unresolved.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import importlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
from collections import Counter
from urllib.parse import urlparse

from benefitlens_source_storage import read_source_bytes

BASE=Path('/home/longnew/.cache/datasets/benefitlens/scale50k-20261109/team-production-20261119')
REPO=Path(__file__).resolve().parents[1]
MODULES={'notice':'benefitlens_compact_notice','experience':'benefitlens_compact_experience'}


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):
    return sha256(value if isinstance(value,bytes) else value.encode()).hexdigest()


def read(path):return json.loads(Path(path).read_text())


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)
    return {'path':str(path),'sha256':digest(path.read_bytes())}


def jsonl(path):
    with gzip.open(path,'rt',encoding='utf-8') as stream:
        for line in stream:
            if line.strip():yield json.loads(line)


def connect(root):
    root=root.resolve()
    if not root.is_relative_to(BASE.resolve()):raise ValueError('Use the shared dataset cache')
    root.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(root).free<1024**3:raise ValueError('Shared cache space is insufficient')
    cx=sqlite3.connect(root/'queue.sqlite',timeout=60);cx.row_factory=sqlite3.Row
    cx.execute('PRAGMA journal_mode=WAL')
    cx.executescript('''CREATE TABLE IF NOT EXISTS groups(
      group_id TEXT PRIMARY KEY,kind TEXT,group_key TEXT,representative_json TEXT,
      occurrence_count INTEGER DEFAULT 0,state TEXT DEFAULT 'pending',result_json TEXT,receipt_path TEXT);
      CREATE TABLE IF NOT EXISTS occurrences(
      occurrence_id TEXT PRIMARY KEY,group_id TEXT,kind TEXT,record_key TEXT,document_id TEXT,text_sha256 TEXT,
      char_start INTEGER,char_end INTEGER,context_start INTEGER,context_end INTEGER,source_url TEXT,
      source_role TEXT,genre TEXT,sentence_index INTEGER,paragraph_position TEXT,model_representative INTEGER DEFAULT 0);
      CREATE INDEX IF NOT EXISTS compact_group_state ON groups(kind,state,occurrence_count);
      CREATE INDEX IF NOT EXISTS compact_occurrence_group ON occurrences(group_id);
      CREATE INDEX IF NOT EXISTS compact_source_anchor ON occurrences(kind,document_id,text_sha256,char_start,char_end,source_role,genre);
      CREATE TABLE IF NOT EXISTS document_sources(document_id TEXT,text_sha256 TEXT,text_path TEXT,source_snapshot TEXT,
      PRIMARY KEY(document_id,text_sha256));
      CREATE TABLE IF NOT EXISTS calls(invocation_id TEXT PRIMARY KEY,kind TEXT,state TEXT,groups_count INTEGER,
      request_path TEXT,response_path TEXT,receipt_path TEXT,elapsed_seconds REAL,error TEXT);
      CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
    ''')
    return cx


def add(cx,kind,occ,module):
    # A new snapshot can retain the same source under another case owner. Reuse
    # the original anchor, and keep different source versions/roles distinct.
    anchor=(kind,occ['document_id'],occ['text_sha256'],occ['char_start'],occ['char_end'],occ.get('source_role'),occ.get('genre'))
    key=module.group_key(occ)
    if not isinstance(key,str):key=encoded(key)
    gid=kind+'-'+digest(key)[:28]
    if cx.execute('SELECT 1 FROM occurrences WHERE kind=? AND document_id=? AND text_sha256=? AND char_start=? AND char_end=? AND source_role IS ? AND genre IS ? AND group_id=? LIMIT 1',anchor+(gid,)).fetchone():return
    oid=occ.get('occurrence_id') or occ.get('passage_id') or digest(encoded([kind,occ['document_id'],occ['text_sha256'],occ['char_start'],occ['char_end'],occ.get('tags')]))
    if cx.execute('SELECT 1 FROM occurrences WHERE occurrence_id=?',(oid,)).fetchone():
        oid='source-anchor-'+digest(encoded([anchor,gid]))[:40]
    cx.execute('INSERT OR IGNORE INTO groups(group_id,kind,group_key,representative_json) VALUES(?,?,?,?)',
               (gid,kind,key,encoded(occ)))
    values=(oid,gid,kind,occ.get('record_key') or occ.get('case_key'),occ['document_id'],occ['text_sha256'],
        occ['char_start'],occ['char_end'],occ.get('context_start',occ['char_start']),occ.get('context_end',occ['char_end']),
        occ.get('source_url'),occ.get('source_role'),occ.get('genre'),occ.get('sentence_index'),occ.get('paragraph_position','unknown'))
    changed=cx.execute('INSERT OR IGNORE INTO occurrences(occurrence_id,group_id,kind,record_key,document_id,text_sha256,char_start,char_end,context_start,context_end,source_url,source_role,genre,sentence_index,paragraph_position) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',values).rowcount
    if changed:cx.execute('UPDATE groups SET occurrence_count=occurrence_count+1 WHERE group_id=?',(gid,))


BOUNDARY=re.compile(r'(?<=[.!?。！？])\s+|\n[ \t]*\n+|(?m:^[ \t]*[-*•]\s+)')
ABBREV=re.compile(r'(?:\b(?:Mr|Mrs|Ms|Dr|Prof|vs|etc)|\bPh\.D|\bU\.S|\be\.g|\bi\.e)\.$',re.I)


def sentence_passages(text,document,case):
    bounds=[];cursor=0
    for match in BOUNDARY.finditer(text):
        if ABBREV.search(text[max(cursor,match.start()-12):match.start()]):continue
        if match.start()>cursor:bounds.append((cursor,match.start()))
        cursor=match.end()
    if cursor<len(text):bounds.append((cursor,len(text)))
    index=0
    for start,end in bounds:
        while start<end:
            stop=min(end,start+700)
            if stop<end:
                split=text.rfind(' ',start+350,stop)
                if split>start:stop=split
            a,b=start,stop
            while a<b and text[a].isspace():a+=1
            while b>a and text[b-1].isspace():b-=1
            if b>a:
                left=max(0,a-180);right=min(len(text),b+180)
                yield {'passage_id':'passage-'+digest(encoded([document['document_id'],a,b]))[:28],
                    'kind':'experience','record_key':case.get('case_key'),'case_key':case.get('case_key'),
                    'document_id':document['document_id'],'text_sha256':document['text_ref']['sha256'],
                    'char_start':a,'char_end':b,'text':text[a:b],
                    'context_start':left,'context_end':right,'context_text':text[left:right],
                    'source_url':document.get('source_url'),'source_role':case.get('case_scope') or 'unknown',
                    'genre':case.get('genre'),'sentence_index':index,'paragraph_position':'unknown'}
                index+=1
            start=stop


def source_text(reference):
    # The decoded content hash is the semantic input identity. Existing packing
    # metadata may be repaired separately without changing these source bytes.
    raw=read_source_bytes(reference['path'])
    if digest(raw)!=reference['sha256']:raise ValueError('Source text checksum mismatch')
    return raw.decode('utf-8')


def scan(cx,root,kinds,source_root=BASE):
    progress=read(root/'scan-summary.json') if (root/'scan-summary.json').exists() else {'source_documents':{},'errors':[]}
    progress['at']=datetime.now(timezone.utc).isoformat()
    if 'notice' in kinds:
        module=importlib.import_module(MODULES['notice']);seen=set();done=0
        for record in jsonl(source_root/'notice/notices.jsonl.gz'):
            ref=record.get('original_ref')
            if not ref or ref['document_id'] in seen:continue
            seen.add(ref['document_id'])
            try:
                text=source_text({'path':ref['text_path'],'sha256':ref['text_sha256']})
                cx.execute('INSERT OR IGNORE INTO document_sources VALUES(?,?,?,?)',(ref['document_id'],ref['text_sha256'],ref['text_path'],str(source_root)))
                for occ in module.extract_contexts(record,source_text=text):add(cx,'notice',occ,module)
            except (ValueError,OSError,UnicodeError) as error:
                if len(progress['errors'])<20:progress['errors'].append({'document_id':ref['document_id'],'error':str(error)[:200]})
            done+=1
            if done%50==0:cx.commit()
            if done%1000==0:
                cx.commit();save(root/'scan-progress.json',{'kind':'notice','documents':done});print(encoded({'kind':'notice','documents':done}),flush=True)
        progress['source_documents']['notice']=done;cx.commit()
    if 'experience' in kinds:
        module=importlib.import_module(MODULES['experience']);cases={}
        for case in jsonl(source_root/'experience/cases.jsonl.gz'):
            if not case.get('active',True):continue
            scope=case.get('source_roles',{}).get('case_scope') or 'unknown'
            for document in case.get('input_documents',[]):
                cases.setdefault(document['document_id'],{'case_key':case['case_key'],'case_scope':scope,
                    'genre':'application' if 'application_document' in scope else 'experience_review'})
        done=0
        for document in jsonl(source_root/'experience/documents.jsonl.gz'):
            case=cases.get(document['document_id'])
            if not case:continue
            try:
                text=source_text(document['text_ref'])
                cx.execute('INSERT OR IGNORE INTO document_sources VALUES(?,?,?,?)',(document['document_id'],document['text_ref']['sha256'],document['text_ref']['path'],str(source_root)))
                for passage in sentence_passages(text,document,case):
                    occ=module.tag_passage(passage)
                    if occ is not None:add(cx,'experience',occ,module)
            except (ValueError,OSError,UnicodeError) as error:
                if len(progress['errors'])<20:progress['errors'].append({'document_id':document['document_id'],'error':str(error)[:200]})
            done+=1
            if done%50==0:cx.commit()
            if done%1000==0:
                cx.commit();save(root/'scan-progress.json',{'kind':'experience','documents':done});print(encoded({'kind':'experience','documents':done}),flush=True)
        progress['source_documents']['experience']=done;cx.commit()
    progress['state']='code_scan_complete';save(root/'scan-summary.json',progress)
    return progress


def select_groups(cx,kind,batch_size):
    """Prefer reusable spans while keeping different fields and source roles visible."""
    rows=cx.execute("SELECT * FROM groups WHERE kind=? AND state='pending' ORDER BY occurrence_count DESC,group_id",(kind,)).fetchall()
    candidates=[];excluded=0
    for row in rows:
        rep=json.loads(row['representative_json']);focus=rep['text'].strip()
        numeric_value=bool(re.search(r'\d',focus))
        heading_only=bool(re.fullmatch(r'(?:(?:required|preferred|essential)\s+)?(?:required|preferred|eligibility|qualifications?|requirements?|education|benefits?|지원\s*자격|필수|우대|소득|학력|전공)\s*[:：]?',focus,re.I))
        if heading_only or (len(focus)<14 and not numeric_value):
            excluded+=1;continue
        candidates.append((row,rep))
    selected=[];ids=set();hosts=Counter();tags=Counter()
    def choose(predicate,limit):
        added=0
        for row,rep in candidates:
            if len(selected)>=batch_size or added>=limit:break
            if row['group_id'] in ids or not predicate(rep):continue
            host=urlparse(rep.get('source_url')or'').hostname or 'unknown'
            tag=(rep.get('tags')or['unknown'])[0]
            if hosts[host]>=8 or tags[tag]>=12:continue
            selected.append({'group_id':row['group_id'],'representative':rep,
                'occurrence_count':row['occurrence_count'],'source_refs':[]})
            ids.add(row['group_id']);hosts[host]+=1;tags[tag]+=1;added+=1
    if kind=='experience':
        choose(lambda rep:'application_document' in (rep.get('source_role')or''),batch_size//4)
        choose(lambda rep:(rep.get('source_role')or'')=='application_process',batch_size//4)
    choose(lambda rep:True,batch_size)
    return selected,{'pending_groups':len(rows),'metadata_or_short_focus_excluded':excluded,
        'selection':'frequency_with_source_and_field_diversity','selected_groups':len(selected),
        'selected_occurrences':sum(g['occurrence_count'] for g in selected),'source_hosts':dict(hosts),'primary_tags':dict(tags)}


def normalize(cx,root,kinds,max_calls,batch_size):
    blocked=cx.execute("SELECT value FROM meta WHERE key='provider_waiting'").fetchone()
    if blocked:
        print(encoded({'state':'waiting_provider_limit','automatic_retry':False,
            'reason':'A previous provider limit is retained. Explicit resume is required.'}),flush=True)
        return
    calls=0
    for kind in kinds:
        if calls>=max_calls:break
        module=importlib.import_module(MODULES[kind])
        groups,selection=select_groups(cx,kind,batch_size)
        if not groups:continue
        batch=module.build_batch(groups)
        ident=str(uuid.uuid4());folder=root/'batches'/ident;folder.mkdir(parents=True)
        request_ref=save(folder/'request.json',batch)
        save(folder/'selection.json',selection)
        schema=module.result_schema();schema.pop('$schema',None)
        save(folder/'schema.json',schema)
        command=['/home/longnew/.local/bin/claude','--safe-mode','--print','--tools','',
            '--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--permission-mode','plan',
            '--no-chrome','--no-session-persistence','--output-format','json',
            '--system-prompt',batch['request']['system'],'--json-schema',encoded(schema)]
        started=time.monotonic()
        print(encoded({'state':'compact_model_running','kind':kind,'groups':len(groups),'call':calls+1}),flush=True)
        try:
            process=subprocess.run(command,input=batch['request']['user'],text=True,capture_output=True,timeout=600,cwd=REPO)
        except subprocess.TimeoutExpired as error:
            output=error.stdout or b'';stderr=error.stderr or b''
            process=subprocess.CompletedProcess(command,124,
                output.decode('utf-8',errors='replace') if isinstance(output,bytes) else output,
                stderr.decode('utf-8',errors='replace') if isinstance(stderr,bytes) else stderr)
        response=folder/'response.json';response.write_text(process.stdout)
        (folder/'stderr.txt').write_text(process.stderr)
        calls+=1;receipt={'provider':'claude_cli','model_called':False,'invocation_id':ident,'kind':kind,
            'input_sha256':batch['input_sha256'],'request_ref':request_ref,
            'response_ref':{'path':str(response),'sha256':digest(response.read_bytes())},
            'exit_code':process.returncode,'elapsed_seconds':round(time.monotonic()-started,2),'group_results':[]}
        try:
            envelope=json.loads(process.stdout)
            receipt.update(model_called=bool(envelope.get('modelUsage')),models=list((envelope.get('modelUsage')or{})),
                           usage=envelope.get('usage'))
            if envelope.get('api_error_status')==429:
                receipt.update(state='waiting_provider_limit',provider_error=envelope.get('api_error'),
                    provider_message=envelope.get('result'),group_results=[])
                cx.execute("UPDATE groups SET state='waiting_provider_limit',receipt_path=? WHERE group_id IN ("+
                    ','.join('?' for _ in groups)+')',[str(folder/'receipt.json')]+[g['group_id'] for g in groups])
                save(folder/'receipt.json',receipt)
                cx.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)',(ident,kind,receipt['state'],len(groups),
                    request_ref['path'],str(response),str(folder/'receipt.json'),receipt['elapsed_seconds'],receipt.get('provider_error')))
                cx.execute("INSERT OR REPLACE INTO meta VALUES('provider_waiting',?)",(encoded({'receipt_path':str(folder/'receipt.json'),'reason':receipt.get('provider_error')}),))
                cx.commit()
                print(encoded({'state':'waiting_provider_limit','kind':kind,'automatic_retry':False}),flush=True)
                break
            if receipt['model_called']:
                for group in groups:
                    rep=group['representative']
                    cx.execute('UPDATE occurrences SET model_representative=1 WHERE group_id=? AND document_id=? AND char_start=? AND char_end=?',
                        (group['group_id'],rep['document_id'],rep['char_start'],rep['char_end']))
            if process.returncode or envelope.get('is_error') or not receipt['model_called']:raise ValueError('Provider did not return a successful model execution')
            raw=envelope.get('structured_output') or json.loads(envelope['result'])
            receipt['result_ref']=save(folder/'raw-result.json',raw)
            checked=module.validate_result(raw,batch,allow_partial=True)
            save(folder/'validated-result.json',checked)
            accepted=checked.get('groups') or checked.get('results') or checked.get('valid_groups') or []
            accepted_by_id={row['group_id']:row for row in accepted}
            raw_rows=raw.get('groups') or raw.get('results') or []
            raw_by_id={row['group_id']:row for row in raw_rows}
            raw_indices={row['group_id']:index for index,row in enumerate(raw_rows)}
            raw_property='groups' if 'groups' in raw else 'results'
            for group in groups:
                gid=group['group_id'];result=accepted_by_id.get(gid)
                state='analyzed' if result is not None else 'unresolved'
                receipt['group_results'].append({'group_id':gid,'state':state,
                    'result_sha256':digest(encoded(result)) if result is not None else None,
                    'result_pointer':'/'+raw_property+'/'+str(raw_indices[gid]) if gid in raw_indices else None,
                    'raw_result_sha256':digest(encoded(raw_by_id[gid])) if gid in raw_by_id else None})
                cx.execute('UPDATE groups SET state=?,result_json=?,receipt_path=? WHERE group_id=?',
                    (state,encoded(result) if result is not None else None,str(folder/'receipt.json'),gid))
            receipt.update(state='completed',issues=checked.get('issues',checked.get('unresolved_groups',[])))
        except Exception as error:
            receipt.update(state='failed',error=type(error).__name__+': '+str(error)[:900])
            for group in groups:cx.execute("UPDATE groups SET state='unresolved',receipt_path=? WHERE group_id=?",(str(folder/'receipt.json'),group['group_id']))
        save(folder/'receipt.json',receipt)
        cx.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)',(ident,kind,receipt['state'],len(groups),
            request_ref['path'],str(response),str(folder/'receipt.json'),receipt['elapsed_seconds'],receipt.get('error')))
        cx.commit();print(encoded({'kind':kind,'state':receipt['state'],'groups':len(groups),'issues':len(receipt.get('issues',[]))}),flush=True)


def status(cx,root):
    attempted=0;executed=0;subagent_batches=0;codex_attempted=0;codex_completed=0;claude_attempted=0
    for row in cx.execute('SELECT receipt_path FROM calls'):
        if not row['receipt_path']:continue
        receipt=read(row['receipt_path'])
        if receipt.get('provider')=='claude_cli':
            attempted+=1;claude_attempted+=1
            if receipt.get('model_called') is True:executed+=1
        elif receipt.get('provider')=='codex_cli':
            attempted+=1;codex_attempted+=1
            if receipt.get('model_called') is True:executed+=1;codex_completed+=1
        elif receipt.get('provider')=='codex_subagent' and receipt.get('model_called') is True:
            subagent_batches+=1
    result={'at':datetime.now(timezone.utc).isoformat(),'scope':'selected_sentences_and_protected_context_groups',
            'whole_document_analysis_claimed':False,'kinds':{},'cli_attempts':attempted,
            'actual_cli_model_calls':executed,'codex_subagent_batches':subagent_batches,
            'claude_cli_attempts':claude_attempted,'codex_cli_attempts':codex_attempted,
            'completed_codex_cli_batches':codex_completed,
            'actual_model_calls':None if subagent_batches else executed,
            'underlying_codex_model_api_calls':None}
    for kind in MODULES:
        counts=dict(cx.execute('SELECT state,count(*) FROM groups WHERE kind=? GROUP BY state',(kind,)))
        count=cx.execute("""SELECT count(*) FROM occurrences o JOIN groups g ON o.group_id=g.group_id
            WHERE g.kind=? AND g.state='analyzed' AND
            ((g.kind='notice' AND json_array_length(json_extract(g.result_json,'$.mappings'))>0) OR
             (g.kind='experience' AND json_extract(g.result_json,'$.relevance')='relevant'))""",(kind,)).fetchone()[0]
        result['kinds'][kind]={'group_states':counts,'applied_occurrence_spans':count}
    save(root/'status.json',result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['scan','normalize','status'])
    p.add_argument('--root',type=Path,default=BASE/'compact')
    p.add_argument('--source-root',type=Path,default=BASE,help='Retained source snapshot containing notice/ or experience/ exports')
    p.add_argument('--kinds',default='notice,experience')
    p.add_argument('--max-calls',type=int,default=2)
    p.add_argument('--batch-size',type=int,default=40)
    p.add_argument('--resume-provider',action='store_true',help='Explicitly release a retained provider limit and requeue the same groups')
    args=p.parse_args();root=args.root.resolve();cx=connect(root);kinds=args.kinds.split(',')
    if not 1<=args.batch_size<=50:p.error('--batch-size must be between 1 and 50')
    if args.max_calls<0:p.error('--max-calls must be nonnegative')
    if set(kinds)-set(MODULES):p.error('Unknown source kind')
    if args.resume_provider:
        cx.execute("DELETE FROM meta WHERE key='provider_waiting'")
        cx.execute("UPDATE groups SET state='pending',receipt_path=NULL WHERE state='waiting_provider_limit'")
        cx.commit()
    if not args.source_root.resolve().is_relative_to(BASE.resolve()):p.error('Use a retained snapshot in the shared dataset cache')
    if args.command=='scan':print(encoded(scan(cx,root,kinds,args.source_root.resolve())))
    elif args.command=='normalize':normalize(cx,root,kinds,args.max_calls,args.batch_size)
    print(encoded(status(cx,root)))


if __name__=='__main__':main()
