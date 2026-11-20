"""Import the reliable official ELLIPSE train/test release without demographics."""
import csv
from datetime import datetime
from hashlib import sha256
import io
import json
from pathlib import Path
import sqlite3
import zipfile

from benefitlens_source_storage import read_source_bytes
from collect_benefitlens_writing_foundations import ROOT
from import_benefitlens_scored_writing import essay_key
from run_benefitlens_campaign import KST,atomic_json,single_writer

TRAITS=('Overall','Cohesion','Syntax','Vocabulary','Phraseology','Grammar','Conventions')


def read_rows(stream,split):
    rows=csv.DictReader(io.TextIOWrapper(stream,encoding='utf-8-sig',newline=''))
    if not {'full_text','prompt',*TRAITS}<=set(rows.fieldnames):
        raise ValueError('Unexpected ELLIPSE columns')
    for row in rows:
        text=row['full_text']
        native=row.get('text_id_kaggle')
        if not native or not text.strip():raise ValueError('Missing native ID or complete essay')
        scores={trait:float(row[trait]) if row[trait] else None for trait in TRAITS}
        yield {'text_key':essay_key(text),'native_id':native,'full_text':text,
               'text_sha256':sha256(text.encode()).hexdigest(),'prompt':row['prompt'],
               'source_split':split,'scores':scores}


def main():
    root=ROOT/'ellipse';root.mkdir(exist_ok=True)
    target='https://api.github.com/repos/scrosseye/ELLIPSE-Corpus/zipball/'
    matches=[]
    for p in (ROOT/'open-guides/receipts').glob('*.json'):
        meta=json.loads(p.read_text())
        if meta.get('status')==200 and meta['url'].startswith(target):matches.append(meta)
    if not matches:raise ValueError('Collect official pinned ELLIPSE archive first')
    meta=sorted(matches,key=lambda m:m['at_kst'])[-1]
    original=read_source_bytes(meta['path'],storage=meta['storage'])
    if sha256(original).hexdigest()!=meta['sha256']:raise ValueError('Official archive hash mismatch')
    with single_writer(root):
        cx=sqlite3.connect(root/'ellipse.sqlite')
        cx.executescript('''CREATE TABLE IF NOT EXISTS essay(
          text_key TEXT PRIMARY KEY,native_id TEXT,full_text TEXT,text_sha256 TEXT,
          prompt TEXT,source_split TEXT,scores_json TEXT);
          CREATE TABLE IF NOT EXISTS source_row(native_id TEXT,source_split TEXT,text_key TEXT,
          PRIMARY KEY(native_id,source_split));''')
        counts={};conflicts=[]
        with cx,zipfile.ZipFile(io.BytesIO(original)) as archive:
            cx.execute('DELETE FROM essay');cx.execute('DELETE FROM source_row')
            names=archive.namelist()
            train=next(n for n in names if n.endswith('/ELLIPSE_Final_github_train.csv'))
            test=next(n for n in names if n.endswith('/ELLIPSE_Final_github_test.zip'))
            def keep(row):
                key=row['text_key'];scores=json.dumps(row['scores'],sort_keys=True)
                old=cx.execute('SELECT prompt,scores_json FROM essay WHERE text_key=?',(key,)).fetchone()
                if old and old!=(row['prompt'],scores):conflicts.append(row['native_id'])
                cx.execute('INSERT OR IGNORE INTO essay VALUES(?,?,?,?,?,?,?)',(
                    key,row['native_id'],row['full_text'],row['text_sha256'],row['prompt'],row['source_split'],scores))
                cx.execute('INSERT INTO source_row VALUES(?,?,?)',(row['native_id'],row['source_split'],key))
            with archive.open(train) as stream:
                counts['train']=0
                for row in read_rows(stream,'train'):keep(row);counts['train']+=1
            with zipfile.ZipFile(io.BytesIO(archive.read(test))) as inner:
                csvs=[n for n in inner.namelist() if n.endswith('.csv')]
                if len(csvs)!=1:raise ValueError('Expected one official test CSV')
                with inner.open(csvs[0],pwd=b'ellipse_test') as stream:
                    counts['test']=0
                    for row in read_rows(stream,'test'):keep(row);counts['test']+=1
        if conflicts:raise ValueError('Conflicting scores or prompt in deduplicated essays')
        unique=cx.execute('SELECT count(*) FROM essay').fetchone()[0]
        missing=cx.execute('SELECT count(*) FROM source_row WHERE text_key NOT IN (SELECT text_key FROM essay)').fetchone()[0]
        if missing:raise ValueError('Unbound original rows')
        old=sqlite3.connect('file:'+str(ROOT/'persuade/persuade-test.sqlite')+'?mode=ro',uri=True)
        prior={r[0] for r in old.execute('SELECT text_key FROM essay')};old.close()
        keys={r[0] for r in cx.execute('SELECT text_key FROM essay')}
        for text,checksum in cx.execute('SELECT full_text,text_sha256 FROM essay'):
            if sha256(text.encode()).hexdigest()!=checksum:raise ValueError('Essay hash mismatch')
        report={'at_kst':datetime.now(KST).isoformat(),'source_url':'https://github.com/scrosseye/ELLIPSE-Corpus',
                'official_archive':meta,'source_revision':meta['url'].rsplit('/',1)[-1],
                'license':'CC BY 4.0, explicitly stated in the official README',
                'source_rows':counts,'unique_essays':unique,'prompt_count':cx.execute('SELECT count(DISTINCT prompt) FROM essay').fetchone()[0],
                'traits':list(TRAITS),'whole_essay_overlap_with_persuade':len(keys&prior),
                'unique_essays_across_reliable_corpora':len(keys|prior),'quality_scope':'Official reliable train/test release only',
                'raw_unreliable_essay_pool_imported':False,'demographics_copied_to_derived_table':False,
                'test_password_basis':'Explicit public author README','database':str(root/'ellipse.sqlite'),
                'model_called':False,'admission_outcomes_inferred':False}
        atomic_json(root/'status.json',report);cx.close();print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
