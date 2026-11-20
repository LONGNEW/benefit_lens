"""Stream an author-distributed PERSUADE archive into essay and annotation tables.

The source publicly provides its ZIP password. Essay IDs, complete texts and
discourse annotation rows remain separate. Scores are writing ratings.
"""
import argparse
from collections import Counter
from datetime import datetime
from hashlib import sha256
from functools import lru_cache
import csv
import io
import json
from pathlib import Path
import re
import shutil
import sqlite3
import unicodedata
import zipfile

from run_benefitlens_campaign import CACHE, KST, atomic_json, single_writer


def essay_key(text):
    return sha256(' '.join(unicodedata.normalize('NFKC', text).split()).encode()).hexdigest()


@lru_cache(maxsize=64)
def whitespace_index(text):
    chunks=[];starts=[];ends=[];previous=0
    for word in re.finditer(r'\S+',text):
        if chunks:
            chunks.append(' ');starts.append(previous);ends.append(word.start())
        chunks.append(word.group())
        starts.extend(range(word.start(),word.end()));ends.extend(range(word.start()+1,word.end()+1))
        previous=word.end()
    return ''.join(chunks),starts,ends


def span_locator(text, quote, source_start, source_end):
    """Test both conventions, then exact-search only; never invent a span."""
    start, end = int(source_start), int(source_end)
    for stop in (end, end+1):
        if 0 <= start < stop <= len(text) and text[start:stop] == quote:
            return {'start':start,'end':stop,'basis':'source_offsets_exact','verified_text':quote}
    index=text.find(quote)
    if quote and index >= 0 and text.find(quote,index+1) < 0:
        return {'start':index,'end':index+len(quote),'basis':'unique_exact_text_match','verified_text':quote}
    trimmed=quote.strip();index=text.find(trimmed)
    if trimmed and index>=0 and text.find(trimmed,index+1)<0:
        return {'start':index,'end':index+len(trimmed),'basis':'trimmed_whitespace_exact','verified_text':trimmed}
    anchored=[i for i in range(max(0,start-2),min(len(text),start+3))
              if trimmed and text[i:i+len(trimmed)]==trimmed and abs(i+len(trimmed)-end)<=3]
    if len(anchored)==1:
        left=anchored[0]
        return {'start':left,'end':left+len(trimmed),'basis':'trimmed_source_offsets_exact','verified_text':trimmed}
    normal,starts,ends=whitespace_index(text);normal_quote=' '.join(quote.split());index=normal.find(normal_quote)
    if normal_quote and index>=0 and normal.find(normal_quote,index+1)<0:
        left,right=starts[index],ends[index+len(normal_quote)-1]
        return {'start':left,'end':right,'basis':'whitespace_equivalent_text','verified_text':text[left:right]}
    return {'start':None,'end':None,'basis':'unresolved_source_span','verified_text':None}


def process_archive(path, root):
    if path.resolve().is_relative_to(CACHE.resolve()) is False:
        raise ValueError('Source archive must reside in shared dataset cache')
    digest=sha256(path.read_bytes()).hexdigest()
    database=root/'persuade-test.sqlite'
    cx=sqlite3.connect(database)
    cx.executescript('''
      CREATE TABLE IF NOT EXISTS essay(
        text_key TEXT PRIMARY KEY, native_id TEXT, prompt_name TEXT, assignment TEXT,
        full_text TEXT NOT NULL, text_sha256 TEXT, holistic_score REAL,
        task TEXT, source_text TEXT, source_split TEXT);
      CREATE TABLE IF NOT EXISTS essay_alias(native_id TEXT,text_key TEXT, PRIMARY KEY(native_id,text_key));
      CREATE TABLE IF NOT EXISTS discourse(
        source_row INTEGER PRIMARY KEY, text_key TEXT, native_id TEXT,
        discourse_type TEXT, effectiveness TEXT, discourse_text TEXT,
        source_start INTEGER,source_end INTEGER,verified_start INTEGER,verified_end INTEGER,
        span_basis TEXT,hierarchical_id TEXT,hierarchical_label TEXT,verified_text TEXT);
    ''')
    if 'verified_text' not in {r[1] for r in cx.execute('PRAGMA table_info(discourse)')}:
        cx.execute('ALTER TABLE discourse ADD COLUMN verified_text TEXT')
    with cx:
        cx.execute('DELETE FROM discourse');cx.execute('DELETE FROM essay_alias');cx.execute('DELETE FROM essay')
        rows=0; spans=Counter(); consistency=Counter(); ids={}
        with zipfile.ZipFile(path) as archive:
            members=[m for m in archive.infolist() if m.filename.endswith('.csv')]
            if len(members)!=1 or members[0].file_size > 1024**3:
                raise ValueError('Expected one bounded original CSV')
            member=members[0]
            with archive.open(member,pwd=b'persuade_test') as handle:
                reader=csv.DictReader(io.TextIOWrapper(handle,encoding='utf-8-sig',newline=''))
                required={'essay_id_comp','full_text','holistic_essay_score','discourse_text','discourse_type',
                          'discourse_effectiveness','discourse_start','discourse_end','prompt_name','assignment'}
                if not required <= set(reader.fieldnames):raise ValueError('Unexpected corpus columns')
                for row in reader:
                    rows+=1;text=row['full_text'];key=essay_key(text)
                    native=row.get('essay_id_comp') or row['essay_id']
                    ids.setdefault(native,set()).add(key)
                    score=float(row['holistic_essay_score']) if row['holistic_essay_score'] else None
                    values=(key,native,row['prompt_name'],row['assignment'],text,sha256(text.encode()).hexdigest(),
                            score,row.get('task'),row.get('source_text'),row.get('competition_set'))
                    old=cx.execute('SELECT text_sha256,holistic_score,prompt_name FROM essay WHERE text_key=?',(key,)).fetchone()
                    if old:
                        if old[0]!=values[5]:consistency['normalized_equal_text_variants']+=1
                        if old[1]!=score or old[2]!=row['prompt_name']:consistency['inconsistent_score_or_prompt']+=1
                    else:cx.execute('INSERT INTO essay VALUES(?,?,?,?,?,?,?,?,?,?)',values)
                    cx.execute('INSERT OR IGNORE INTO essay_alias VALUES(?,?)',(native,key))
                    quote=row['discourse_text']; loc=span_locator(text,quote,row['discourse_start'],row['discourse_end'])
                    spans[loc['basis']]+=1
                    cx.execute('INSERT INTO discourse VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(
                        rows,key,row['discourse_id'],row['discourse_type'],row['discourse_effectiveness'],quote,
                        int(row['discourse_start']),int(row['discourse_end']),loc['start'],loc['end'],loc['basis'],
                        row.get('hierarchical_id'),row.get('hierarchical_label'),loc['verified_text']))
                    if rows%10000==0:print(json.dumps({'parsed_source_rows':rows,'unique_essays_so_far':len({k for v in ids.values() for k in v})}),flush=True)
    score_counts=dict(cx.execute('SELECT holistic_score,count(*) FROM essay GROUP BY holistic_score'))
    roles=dict(cx.execute('SELECT discourse_type,count(*) FROM discourse GROUP BY discourse_type'))
    quality=dict(cx.execute('SELECT effectiveness,count(*) FROM discourse GROUP BY effectiveness'))
    examples=[]
    for prompt, in cx.execute('SELECT prompt_name FROM essay GROUP BY prompt_name ORDER BY count(*) DESC LIMIT 3').fetchall():
        for order in ('DESC','ASC'):
            e=cx.execute('SELECT text_key,native_id,holistic_score,full_text,assignment FROM essay WHERE prompt_name=? ORDER BY holistic_score '+order+',text_key LIMIT 1',(prompt,)).fetchone()
            segs=cx.execute('SELECT discourse_type,effectiveness,verified_start,verified_end,discourse_text FROM discourse WHERE text_key=? ORDER BY source_start,source_row',(e[0],)).fetchall()
            examples.append({'text_key':e[0],'native_id':e[1],'holistic_score':e[2],'prompt':prompt,'assignment':e[4],
                             'full_text':e[3],'elements':[{'type':s[0],'effectiveness':s[1],'start':s[2],'end':s[3],'text':s[4]} for s in segs]})
    essay_count=cx.execute('SELECT count(*) FROM essay').fetchone()[0]
    result={'at_kst':datetime.now(KST).isoformat(),'source_archive':str(path),'source_archive_sha256':digest,
            'archive_bytes':path.stat().st_size,'csv_member':member.filename,'csv_uncompressed_bytes':member.file_size,
            'source_csv_rows':rows,'unique_full_essays':essay_count,'native_essay_ids':len(ids),
            'native_id_conflicts':sum(len(v)>1 for v in ids.values()),'consistency_flags':dict(consistency),
            'discourse_annotations':cx.execute('SELECT count(*) FROM discourse').fetchone()[0],
            'prompts':cx.execute('SELECT count(DISTINCT prompt_name) FROM essay').fetchone()[0],
            'holistic_score_counts':score_counts,'discourse_role_counts':roles,'element_effectiveness_counts':quality,
            'span_validation':dict(spans),'source_url':'https://github.com/scrosseye/persuade_corpus_2.0',
            'genre':'US school argumentative essays, grades 6 to 12','source_split':'test',
            'admission_success_cases':0,'approved_application_patterns':0,'model_training_performed':False,
            'database':str(database),'source_rows_are_not_essays':True,
            'reuse_scope':'Official public author distribution. Dataset license not declared in repository metadata; redistribution not approved by this import.'}
    cx.close()
    atomic_json(root/'examples.json',examples);atomic_json(root/'status.json',result)
    return result


def reindex_spans(root):
    status=json.loads((root/'status.json').read_text())
    source=Path(status['source_archive'])
    if sha256(source.read_bytes()).hexdigest()!=status['source_archive_sha256']:
        raise ValueError('Original archive changed before reindexing')
    c=sqlite3.connect(root/'persuade-test.sqlite')
    rows=c.execute("SELECT source_row,full_text,discourse_text,source_start,source_end FROM discourse JOIN essay USING(text_key) WHERE span_basis='unresolved_source_span'").fetchall()
    with c:
        for row,text,quote,start,end in rows:
            loc=span_locator(text,quote,start,end)
            if loc['start'] is not None:
                c.execute('UPDATE discourse SET verified_start=?,verified_end=?,span_basis=?,verified_text=? WHERE source_row=?',
                          (loc['start'],loc['end'],loc['basis'],loc['verified_text'],row))
    status['span_validation']=dict(c.execute('SELECT span_basis,count(*) FROM discourse GROUP BY span_basis'))
    status['rated_elements']=c.execute("SELECT count(*) FROM discourse WHERE effectiveness!=''").fetchone()[0]
    status['rated_elements_with_verified_span']=c.execute("SELECT count(*) FROM discourse WHERE effectiveness!='' AND verified_start IS NOT NULL").fetchone()[0]
    status['span_reindexed_at_kst']=datetime.now(KST).isoformat();c.close()
    atomic_json(root/'status.json',status);return status


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--root',type=Path,default=CACHE/'writing-foundations-v1/persuade')
    p.add_argument('--reindex-spans',action='store_true')
    a=p.parse_args();a.root.resolve().relative_to(CACHE.resolve());a.root.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(a.root).free<2*1024**3:raise ValueError('Shared cache reserve')
    with single_writer(a.root):
        result=reindex_spans(a.root) if a.reindex_spans else process_archive(a.archive,a.root)
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
