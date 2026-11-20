"""Project verified mapping rows into a bounded user vocabulary and review queue."""
import argparse
from collections import Counter,defaultdict
from datetime import datetime,timezone
import fcntl,json,sqlite3
from pathlib import Path

from benefitlens_profile_questions import load_contract,initial_form,requirement_type_group,CONTRACT
from run_benefitlens_codex_corpus import BASE,encoded,digest,save


def connect(path):
    db=sqlite3.connect(path,timeout=60);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA busy_timeout=60000')
    db.executescript('''CREATE TABLE IF NOT EXISTS type_tasks(task_id TEXT PRIMARY KEY,group_json TEXT,frequency INTEGER,payload TEXT,state TEXT DEFAULT 'pending');
    CREATE TABLE IF NOT EXISTS type_results(task_id TEXT PRIMARY KEY,type_id TEXT,reason TEXT,result_json TEXT,receipt_path TEXT);
    CREATE TABLE IF NOT EXISTS type_calls(invocation_id TEXT PRIMARY KEY,state TEXT,folder TEXT,task_ids TEXT,model_called INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS profile_links(mapping_id TEXT PRIMARY KEY,candidate_key TEXT,field_id TEXT,type_id TEXT,question_group TEXT,source_group TEXT,subject TEXT,source_route TEXT,payload TEXT);
    CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value_json TEXT);''')
    if 'active' not in {r['name'] for r in db.execute('PRAGMA table_info(type_tasks)')}:db.execute('ALTER TABLE type_tasks ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
    db.commit()
    return db


def project(base=BASE,output=None):
    base=Path(base);output=Path(output or base/'team-expansion-20261120/semantic-connections/profile');output.mkdir(parents=True,exist_ok=True)
    with (output/'.projection.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        source=sqlite3.connect('file:'+str((base/'team-expansion-20261120/semantic-connections/conditions/conditions.sqlite').resolve())+'?mode=ro',uri=True,timeout=30);source.row_factory=sqlite3.Row;source.execute('BEGIN')
        c=load_contract();db=connect(output/'profile.sqlite')
        stat=Path(base/'team-expansion-20261120/semantic-connections/conditions/conditions.sqlite').stat()
        result_stamp=list(db.execute('SELECT count(*),coalesce(max(rowid),0) FROM type_results').fetchone())
        signature=encoded({'source':[stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns],'contract':digest(CONTRACT.read_bytes()),'type_results':result_stamp,'task_states':[list(x) for x in db.execute('SELECT state,count(*) FROM type_tasks WHERE active=1 GROUP BY state')], 'calls':[list(x) for x in db.execute('SELECT state,count(*),sum(model_called) FROM type_calls GROUP BY state')], 'provider_waiting':db.execute("SELECT value_json FROM metadata WHERE key='provider_waiting'").fetchone()[0] if db.execute("SELECT value_json FROM metadata WHERE key='provider_waiting'").fetchone() else None})
        previous=db.execute("SELECT value_json FROM metadata WHERE key='projection_signature'").fetchone()
        if previous and previous[0]==signature and (output/'summary.json').exists():
            source.close();db.close();return json.loads((output/'summary.json').read_text())
        lookup={r['task_id']:r['type_id'] for r in db.execute('SELECT * FROM type_results')}
        taxonomy_sha=digest(encoded(c['condition_types']))
        tasks=defaultdict(lambda:{'frequency':0,'examples':[],'quotes':set()});counts=Counter();field_types=Counter();key_types=set()
        db.execute('DELETE FROM profile_links');db.execute('UPDATE type_tasks SET active=0')
        for row in source.execute('SELECT m.*,r.route FROM mappings m JOIN requirement_routes r USING(mapping_id) ORDER BY m.mapping_id'):
            record=json.loads(row['record_json']);m=record['mapping'];counts['mapping_instances']+=1;field_types[m['field_id']]+=1
            tid=c['legacy_field_types'].get(m['field_id']);task_id=None
            if m['field_id']=='program_specific.requirement':
                group=requirement_type_group(m);task_id='requirement-type-'+digest(encoded([taxonomy_sha,group]))[:36];key_types.add(m.get('program_specific_key'))
                task=tasks[task_id];task['group']=group;task['frequency']+=1
                quote=m['evidence_quote']
                if quote not in task['quotes'] and len(task['examples'])<3:
                    task['quotes'].add(quote);task['examples'].append({'mapping_id':row['mapping_id'],'quote':quote,'unit':m.get('unit'),'reference_phase':m['reference_phase'],'modality':m['modality'],'source_url':record.get('source_url')})
                tid=lookup.get(task_id,'unresolved')
            definition=c['condition_types'][tid]
            group=definition['question_group'];counts['type:'+tid]+=1
            if row['subject']!='applicant' or group=='notice_only' or m['statement_type'] in ('description','benefit_description'):
                counts['not_user_questions']+=1;group='notice_only'
            elif tid=='unresolved':counts['needs_type_analysis']+=1
            else:counts['conditional_user_information_instances']+=1
            db.execute('INSERT INTO profile_links VALUES(?,?,?,?,?,?,?,?,?)',(row['mapping_id'],row['candidate_key'],row['field_id'],tid,group,task_id,row['subject'],row['route'],encoded({'mapping_id':row['mapping_id'],'source_validation_fingerprint':row['validation_fingerprint'],'source_schema_database':str(base/'team-expansion-20261120/semantic-connections/conditions/conditions.sqlite'),'semantic_review':row['semantic_review'],'original_clause_and_logic_preserved_in_source':True})))
        for ident,t in tasks.items():
            payload={'group_id':ident,'taxonomy_sha256':taxonomy_sha,'semantic_key':t['group'][0],'subject':t['group'][1],'statement_type':t['group'][2],'examples':t['examples']}
            # Retain the original evidence sample for an immutable task. New raw
            # clauses remain attached to their own source mapping, never erased.
            db.execute('INSERT INTO type_tasks(task_id,group_json,frequency,payload,active) VALUES(?,?,?,?,1) ON CONFLICT(task_id) DO UPDATE SET frequency=excluded.frequency,active=1',
                (ident,encoded(t['group']),t['frequency'],encoded(payload)))
        db.commit();source.close()
        states={r['state']:r['n'] for r in db.execute('SELECT state,count(*) n FROM type_tasks WHERE active=1 GROUP BY state')}
        summary={'version':c['version'],'at':datetime.now(timezone.utc).isoformat(),'shared_field_types':len(field_types),
            'shared_common_field_types':sum(f!='program_specific.requirement' for f in field_types),
            'reusable_user_fact_field_types':len(c['user_fact_field_ids']),'initial_fields':len(c['initial_fields']),
            'initial_profile_fact_fields':sum(f['kind']=='profile_fact' for f in c['initial_fields']),
            'initial_search_preference_fields':sum(f['kind']=='search_preference' for f in c['initial_fields']),
            'question_chunks':len(c['groups']),'configured_condition_type_cap':c['max_condition_types'],
            'configured_condition_types':len(c['condition_types']),'configured_input_field_types':len(c['input_field_types']),'distinct_program_specific_keys':len(key_types),
            'semantic_type_tasks':states,'actual_type_model_calls':db.execute('SELECT count(*) FROM type_calls WHERE model_called=1').fetchone()[0],
            'provider_waiting':json.loads(db.execute("SELECT value_json FROM metadata WHERE key='provider_waiting'").fetchone()[0]) if db.execute("SELECT value_json FROM metadata WHERE key='provider_waiting'").fetchone() else None,'analyzed_semantic_type_groups':db.execute('SELECT count(*) FROM type_results r JOIN type_tasks t USING(task_id) WHERE t.active=1').fetchone()[0],
            'observed_type_mapping_counts':{k[5:]:v for k,v in counts.items() if k.startswith('type:')},
            'counts':{k:v for k,v in counts.items() if not k.startswith('type:')},'first_screen':initial_form(c),
            'live_user_questions_issued':0,'source_fields_changed':False,'source_mappings_dropped':0,
            'cap_does_not_establish_semantic_coverage':True,'unknown_types_remain_reviewable':True}
        save(output/'summary.json',summary);save(output/'input-contract.json',c)
        db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('projection_signature',signature));db.commit();db.close();return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',type=Path,default=BASE);p.add_argument('--output',type=Path);a=p.parse_args();print(encoded(project(a.base,a.output)))
if __name__=='__main__':main()
