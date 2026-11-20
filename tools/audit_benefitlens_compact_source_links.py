"""Check source bytes and every reused focal/context span without model calls."""
import json
from pathlib import Path
import sqlite3
import unicodedata

import run_benefitlens_compact as runner


def audit(root=runner.BASE/'compact'):
    references={}
    for row in runner.jsonl(runner.BASE/'notice/notices.jsonl.gz'):
        ref=row.get('original_ref')
        if ref:references[(ref['document_id'],ref['text_sha256'])]={'path':ref['text_path'],'sha256':ref['text_sha256']}
    for row in runner.jsonl(runner.BASE/'experience/documents.jsonl.gz'):
        references[(row['document_id'],row['text_ref']['sha256'])]=row['text_ref']
    cx=sqlite3.connect('file:'+str(root/'queue.sqlite')+'?mode=ro',uri=True);cx.row_factory=sqlite3.Row
    cx.execute('BEGIN')
    if cx.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='document_sources'").fetchone():
        for row in cx.execute('SELECT * FROM document_sources'):
            references[(row['document_id'],row['text_sha256'])]={'path':row['text_path'],'sha256':row['text_sha256']}
    rows=cx.execute("""SELECT o.*,g.representative_json FROM occurrences o JOIN groups g ON o.group_id=g.group_id
      WHERE g.state='analyzed' AND ((g.kind='notice' AND json_array_length(json_extract(g.result_json,'$.mappings'))>0)
      OR (g.kind='experience' AND json_extract(g.result_json,'$.relevance')='relevant'))
      ORDER BY o.document_id,o.occurrence_id""")
    counts={};errors=[];docs={};current_id=None;text=None
    for row in rows:
        kind=row['kind'];counts.setdefault(kind,{'checked_occurrences':0,'matching_occurrences':0,'mismatching_occurrences':0})
        docs.setdefault(kind,set());counts[kind]['checked_occurrences']+=1
        try:
            source_key=(row['document_id'],row['text_sha256'])
            if source_key!=current_id:
                ref=references[source_key]
                if ref['sha256']!=row['text_sha256']:raise ValueError('source_version_differs')
                text=runner.source_text(ref);current_id=source_key
            if not 0<=row['context_start']<=row['char_start']<row['char_end']<=row['context_end']<=len(text):
                raise ValueError('invalid_source_span')
            rep=json.loads(row['representative_json'])
            def normalize(value):
                if kind=='experience':value=unicodedata.normalize('NFC',value)
                return ' '.join(value.split())
            for actual,expected in [(text[row['char_start']:row['char_end']],rep['text']),
                    (text[row['context_start']:row['context_end']],rep['context_text'])]:
                if normalize(actual)!=normalize(expected):raise ValueError('reused_expression_or_context_differs')
            counts[kind]['matching_occurrences']+=1;docs[kind].add(row['document_id'])
        except (ValueError,KeyError,OSError,UnicodeError) as error:
            counts[kind]['mismatching_occurrences']+=1
            if len(errors)<20:errors.append({'occurrence_id':row['occurrence_id'],'error':str(error)})
    for kind in counts:counts[kind]['matching_source_documents']=len(docs[kind])
    result={'source_link_audit':counts,'errors':errors,'network_requests':0,'model_calls':0,
        'scope':'source_hash_and_reversible_whitespace_or_nfc_context_reuse_not_semantic_approval'}
    runner.save(root/'source-link-audit.json',result)
    return result


if __name__=='__main__':print(json.dumps(audit(),ensure_ascii=False))
