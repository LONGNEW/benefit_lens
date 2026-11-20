"""Import official before/after revisions, preserving human and predicted labels."""
from collections import Counter
from datetime import datetime
from hashlib import sha256
import io
import json
from pathlib import Path
import sqlite3
import zipfile

from benefitlens_source_storage import read_source_bytes
from collect_benefitlens_writing_foundations import ROOT
from run_benefitlens_campaign import KST,atomic_json,single_writer


def pair_key(before,after,granularity):
    return sha256(json.dumps([granularity,before,after],ensure_ascii=False).encode()).hexdigest()


def archive_for(source_id):
    status=json.loads((ROOT/'open-guides/status.json').read_text())
    source=next(s for s in status['sources'] if s['id']==source_id)
    ref=source['archive'];raw=read_source_bytes(ref['path'],storage=ref['storage'])
    if sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('Source archive hash mismatch')
    return zipfile.ZipFile(io.BytesIO(raw)),source


def main():
    root=ROOT/'revisions';root.mkdir(exist_ok=True)
    with single_writer(root):
        cx=sqlite3.connect(root/'revisions.sqlite')
        cx.executescript('''CREATE TABLE IF NOT EXISTS revision(
          pair_key TEXT PRIMARY KEY,granularity TEXT,before_text TEXT,after_text TEXT,
          before_sha256 TEXT,after_sha256 TEXT);
          CREATE TABLE IF NOT EXISTS observation(
          corpus TEXT,variant TEXT,source_split TEXT,source_row INTEGER,reference_id TEXT,
          pair_key TEXT,doc_id TEXT,revision_depth TEXT,edit_intent TEXT,label_origin TEXT,
          confidence TEXT,context_json TEXT,
          PRIMARY KEY(corpus,variant,source_split,source_row,reference_id));''')
        counts=Counter();sources=[]
        def keep(before,after,granularity,corpus,variant,split,row,reference='',doc_id=None,depth=None,
                 intent=None,origin=None,confidence=None,context=None):
            if not isinstance(before,str) or not isinstance(after,str):raise ValueError('Revision pair requires two original strings')
            key=pair_key(before,after,granularity)
            cx.execute('INSERT OR IGNORE INTO revision VALUES(?,?,?,?,?,?)',(
                key,granularity,before,after,sha256(before.encode()).hexdigest(),sha256(after.encode()).hexdigest()))
            cx.execute('INSERT INTO observation VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(
                corpus,variant,split,row,str(reference),key,doc_id,str(depth) if depth is not None else None,
                json.dumps(intent,ensure_ascii=False),origin,str(confidence) if confidence is not None else None,
                json.dumps(context,ensure_ascii=False) if context else None))
            counts[(corpus,variant,split)]+=1
        with cx:
            cx.execute('DELETE FROM observation');cx.execute('DELETE FROM revision')
            outer,source=archive_for('iterater-revisions');sources.append(source)
            with outer:
                name=next(n for n in outer.namelist() if n.endswith('/dataset/IteraTeR.zip'))
                with zipfile.ZipFile(io.BytesIO(outer.read(name))) as archive:
                    if sum(x.file_size for x in archive.infolist())>512*1024**2:raise ValueError('Revision archive byte ceiling')
                    for member in archive.namelist():
                        if member.startswith('__MACOSX/') or not member.endswith('.json'):continue
                        _,variant,filename=member.split('/');split=Path(filename).stem
                        human=variant.startswith('human_');sentence='_sent_' in variant
                        origin='human_edit_intent_annotation' if human else 'model_predicted_edit_intent'
                        with archive.open(member) as handle:
                            for row,line in enumerate(handle,1):
                                data=json.loads(line)
                                before=data['before_sent'] if sentence else data['before_revision']
                                after=data['after_sent'] if sentence else data['after_revision']
                                keep(before,after,'sentence' if sentence else 'document','iterater',variant,split,row,
                                     doc_id=str(data['doc_id']),depth=data['revision_depth'],intent=data.get('labels'),
                                     origin=origin,confidence=data.get('confidence'),
                                     context={k:data[k] for k in ('edit_actions','sents_char_pos','domain') if k in data})
                        print(json.dumps({'variant':variant,'split':split,'rows':counts[('iterater',variant,split)]}),flush=True)
            archive,source=archive_for('jfleg-revisions');sources.append(source)
            with archive:
                prefix=archive.namelist()[0].split('/')[0]+'/'
                for split in ('dev','test'):
                    stem=prefix+split+'/'+split
                    before=archive.read(stem+'.src').decode().splitlines()
                    refs=[archive.read(stem+'.ref'+str(n)).decode().splitlines() for n in range(4)]
                    if any(len(ref)!=len(before) for ref in refs):raise ValueError('JFLEG source/reference alignment differs')
                    for row,original in enumerate(before,1):
                        for reference in range(4):
                            keep(original,refs[reference][row-1],'sentence','jfleg','four_human_references',split,row,
                                 reference=reference,doc_id=split+':'+str(row),intent='fluency',origin='human_revision_reference')
        missing=cx.execute('SELECT count(*) FROM observation WHERE pair_key NOT IN (SELECT pair_key FROM revision)').fetchone()[0]
        if missing:raise ValueError('Unbound revision observation')
        for before,after,bh,ah in cx.execute('SELECT before_text,after_text,before_sha256,after_sha256 FROM revision'):
            if sha256(before.encode()).hexdigest()!=bh or sha256(after.encode()).hexdigest()!=ah:
                raise ValueError('Revision body hash mismatch')
        units={variant:cx.execute('SELECT count(DISTINCT pair_key) FROM observation WHERE corpus=? AND variant=?',(corpus,variant)).fetchone()[0]
               for corpus,variant in cx.execute('SELECT DISTINCT corpus,variant FROM observation').fetchall()}
        origins=dict(cx.execute('SELECT label_origin,count(*) FROM observation GROUP BY label_origin'))
        report={'at_kst':datetime.now(KST).isoformat(),'sources':sources,
                'source_rows':[{'corpus':c,'variant':v,'split':s,'count':n} for (c,v,s),n in counts.items()],
                'unique_pairs_by_variant':units,'annotation_origin_counts':origins,
                'unique_sentence_pairs':cx.execute("SELECT count(*) FROM revision WHERE granularity='sentence'").fetchone()[0],
                'unique_document_pairs':cx.execute("SELECT count(*) FROM revision WHERE granularity='document'").fetchone()[0],
                'distinct_iterater_source_documents':cx.execute("SELECT count(DISTINCT doc_id) FROM observation WHERE corpus='iterater'").fetchone()[0],
                'distinct_jfleg_source_sentences':cx.execute("SELECT count(DISTINCT doc_id) FROM observation WHERE corpus='jfleg'").fetchone()[0],
                'unchanged_unique_pairs':cx.execute('SELECT count(*) FROM revision WHERE before_text=after_text').fetchone()[0],
                'sentence_rows_are_not_applicants':True,'meaning_changed_is_not_automatically_a_quality_improvement':True,
                'repository_license_is_not_assumed_to_cover_all_source_texts':True,
                'database':str(root/'revisions.sqlite'),'model_called':False,'canonical_database_promoted':False}
        examples=[]
        for intent in ('clarity','coherence','fluency','style'):
            row=cx.execute('''SELECT r.before_text,r.after_text,o.doc_id,o.source_split,o.source_row,o.variant
                FROM observation o JOIN revision r USING(pair_key)
                WHERE o.corpus='iterater' AND o.variant='human_sent_level' AND o.edit_intent=?
                AND length(r.before_text)<280 AND length(r.after_text)<280
                AND r.before_text!=r.after_text ORDER BY o.source_split,o.source_row LIMIT 1''',(json.dumps(intent),)).fetchone()
            if row:examples.append({'intent':intent,'before':row[0],'after':row[1],'doc_id':row[2],
                                    'split':row[3],'source_row':row[4],'variant':row[5],
                                    'label_origin':'human_edit_intent_annotation'})
        atomic_json(root/'reading-examples.json',examples);atomic_json(root/'status.json',report)
        cx.close();print(json.dumps({k:v for k,v in report.items() if k!='sources'},ensure_ascii=False))


if __name__=='__main__':main()
