"""Durable search work for the tool-using agent, separate from HTTP crawling.

The agent executes real web searches, saves responses, imports them here and
returns on a heartbeat. A budget or blocked URL never becomes a zero wave.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter,defaultdict
from pathlib import Path

from .broad_collection import digest,utc_now
from .exploration import Frontier,frontier_url,saturation

PROGRAMS={
 'google-phd-fellowship':'Google PhD Fellowship',
 'nvidia-graduate-fellowship':'NVIDIA Graduate Fellowship',
 'gates-cambridge':'Gates Cambridge',
 'chevening-scholarship':'Chevening Scholarship',
 'fulbright-korea-graduate':'Fulbright Korea graduate',
 'meta-phd-fellowship':'Meta PhD Fellowship',
 'microsoft-phd-fellowship':'Microsoft PhD Fellowship',
 'kfas-overseas-study':'한국고등교육재단 해외유학 장학금',
 'nsf-grfp':'NSF GRFP',
}
EXPERIENCE_TERMS=('research statement','personal statement','research proposal','reviewer comments','mock interview','합격 수기','지원 후기','자기소개서','연구계획서')


def normalized(value):
    value=' '.join(unicodedata.normalize('NFKC',value).casefold().split())
    return re.sub(r'\b(scholarships|fellowships|grants|awards|studentships|bursaries|internships)\b',lambda m:{'bursaries':'bursary'}.get(m[0],m[0][:-1]),value)


def metric_delta(before,after):
    return {k:len(set(after.get(k,[]))-set(before.get(k,[]))) for k in ('terms','programs','cases','documents')}


class SearchLoop:
    def __init__(self,folder):
        self.frontier=Frontier(Path(folder))
        with self.frontier.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS saturation_term(term_key TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS saturation_query(query_key TEXT PRIMARY KEY,wave INTEGER,scope TEXT,status TEXT,payload TEXT);
            CREATE TABLE IF NOT EXISTS saturation_stage(wave INTEGER PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS saturation_entity(entity_key TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS saturation_review(url TEXT PRIMARY KEY,payload TEXT NOT NULL);
            ''')

    def snapshot(self):
        with self.frontier.connect() as c:
            return dict(terms=[r[0] for r in c.execute('SELECT term_key FROM saturation_term')],
                        programs=[r[0] for r in c.execute('SELECT entity_key FROM saturation_entity')],
                        cases=[r[0] for r in c.execute('SELECT case_key FROM experience')],
                        documents=[r[0] for r in c.execute('SELECT document_id FROM document')])

    def stage(self):
        with self.frontier.connect() as c:
            row=c.execute('SELECT payload FROM saturation_stage ORDER BY wave DESC LIMIT 1').fetchone()
        if row:
            d=json.loads(row[0])
            if d['phase'] in {'open','ready_for_close'} or d.get('saturated'):
                return d
            wave=d['wave']+1
        else:
            with self.frontier.connect() as c:
                waves=[json.loads(raw)['wave'] for raw, in c.execute('SELECT payload FROM batch')]
            wave=max(waves,default=0)+1
        d=dict(wave=wave,phase='open',started_at=utc_now(),baseline=self.snapshot(),search_scope='Recorded KR/EN/DE/FR/JA tool-returned results and linked public documents; no claim of whole-web coverage')
        with self.frontier.connect() as c:
            c.execute('INSERT INTO saturation_stage VALUES(?,?)',(wave,json.dumps(d,ensure_ascii=False)))
        return d

    def add_term(self,term,lane,source,program=None,*,seed=False):
        source=frontier_url(source)
        with self.frontier.connect() as c:
            row=c.execute("SELECT payload FROM document WHERE json_extract(payload,'$.canonical_url')=?",(source,)).fetchone()
        if not row:
            raise ValueError('Term requires a collected original: '+str(source))
        doc=json.loads(row[0])
        text=doc['title']+'\n'+Path(doc['text_path']).read_text(encoding='utf-8')
        if normalized(term) not in normalized(text):
            raise ValueError('Term not found in original: '+term)
        language='ko' if re.search('[가-힣]',term) else 'en'
        key=digest(json.dumps([normalized(term),lane,program,language],ensure_ascii=False))
        payload=dict(term=term,lane=lane,program_id=program,language=language,evidence_url=source,evidence_sha256=doc['content_sha256'],accepted_at=utc_now(),seed=seed)
        with self.frontier.connect() as c:
            return c.execute('INSERT OR IGNORE INTO saturation_term VALUES(?,?)',(key,json.dumps(payload,ensure_ascii=False))).rowcount

    def seed(self):
        with self.frontier.connect() as c:
            rows=c.execute('SELECT term,min(source) FROM term GROUP BY term').fetchall()
        for term,source in rows:
            lane='experience' if term in EXPERIENCE_TERMS or term in ['broader impacts','intellectual merit','statement of purpose'] else 'announcement'
            self.add_term(term,lane,source,seed=True)

    def candidates(self,limit=40):
        values=defaultdict(dict)
        pattern=re.compile(r'(?i)\b(?:[A-Za-z][A-Za-z-]*\s+){0,3}(?:scholarships?|fellowships?|studentships?|bursar(?:y|ies)|travel grants?|research internships?|tuition waivers?)\b')
        korean=re.compile(r'[가-힣A-Za-z]+(?:\s+[가-힣A-Za-z]+){0,2}\s*(?:장학금|장학생|연구장려금|지원사업|지원후기|합격수기)')
        with self.frontier.connect() as c:
            accepted={normalized(json.loads(s)['term']) for s, in c.execute('SELECT payload FROM saturation_term')}
            rows=c.execute('SELECT payload FROM document').fetchall()
        for raw, in rows:
            d=json.loads(raw)
            for match in list(pattern.finditer(d['title']))+list(korean.finditer(d['title'])):
                term=match[0].strip()
                term=re.sub(r'(?i)^(?:the|a|an|and|for|of|in|to|with)\s+','',term)
                norm=normalized(term)
                if len(term)<5 or norm in accepted:
                    continue
                values[norm][d['canonical_url']]=dict(term=term,evidence_url=d['canonical_url'],title=d['title'],lane=d['lane'])
        return [dict(normalized=key,source_count=len(rows),examples=[{**x,'title':x['title'][:240]} for x in list(rows.values())[:2]]) for key,rows in sorted(values.items(),key=lambda item:(-len(item[1]),item[0]))[:limit]]

    def plan(self):
        stage=self.stage();wave=stage['wave']
        with self.frontier.connect() as c:
            terms=[json.loads(s) for s, in c.execute("SELECT payload FROM saturation_term ORDER BY CASE WHEN json_extract(payload,'$.seed')=0 THEN 0 ELSE 1 END,rowid")]
            previous_queries={normalized(q['q']) for raw, in c.execute('SELECT payload FROM batch') for q in json.loads(raw)['queries']}
        work=[]
        for term in terms:
            if term['lane']=='announcement':
                qs=[f'"{term["term"]}" 모집 지원 자격',f'"{term["term"]}" 재단 대학원'] if term['language']=='ko' else [f'"{term["term"]}" eligibility apply international',f'"{term["term"]}" funding application students']
                for q in qs:
                    work.append(dict(q=q,lane='announcement',program_id=None,term=term['term'],evidence_url=term['evidence_url']))
            else:
                programs={term['program_id']:PROGRAMS[term['program_id']]} if term['program_id'] in PROGRAMS else PROGRAMS
                for program,name in programs.items():
                    q=f'"{name}" "{term["term"]}"'
                    work.append(dict(q=q,lane='experience',program_id=program,term=term['term'],evidence_url=term['evidence_url']))
        added=0
        with self.frontier.connect() as c:
            existing=c.execute('SELECT count(*) FROM saturation_query WHERE wave=?',(wave,)).fetchone()[0]
        if work and not existing and all(normalized(item['q']) in previous_queries for item in work):
            stage['rescan']=True
            with self.frontier.connect() as c:
                c.execute('UPDATE saturation_stage SET payload=? WHERE wave=?',(json.dumps(stage,ensure_ascii=False),wave))
        if stage.get('rescan'):
            with self.frontier.connect() as c:
                for raw, in c.execute('SELECT payload FROM batch'):
                    batch=json.loads(raw)
                    for query in batch['queries']:
                        work.append(dict(q=query['q'],lane=batch.get('lane','mixed'),program_id=batch.get('program_id'),term=None,evidence_url='recorded-search-dictionary'))
        for item in work:
            if normalized(item['q']) in previous_queries and not stage.get('rescan'):
                continue
            key=digest(str(wave)+'\n'+normalized(item['q']))
            item.update(query_key=key,wave=wave,status='pending',scope=item['program_id'] or 'announcement-world')
            with self.frontier.connect() as c:
                added+=c.execute('INSERT OR IGNORE INTO saturation_query VALUES(?,?,?,?,?)',(key,wave,item['scope'],'pending',json.dumps(item,ensure_ascii=False))).rowcount
        return {**self.status(),'new_queued_queries':added}

    def next(self,limit=12):
        with self.frontier.connect() as c:
            rows=[json.loads(s) for s, in c.execute("SELECT payload FROM saturation_query WHERE status='pending' ORDER BY CASE WHEN scope='announcement-world' THEN 0 ELSE 1 END,rowid")]
        # Round-robin scopes, so general announcements do not starve experiences.
        groups=defaultdict(list)
        for row in rows:
            groups[row['scope']].append(row)
        result=[]
        while groups and len(result)<limit:
            for scope in list(groups):
                result.append(groups[scope].pop(0))
                if not groups[scope]:del groups[scope]
                if len(result)>=limit:break
        return result

    def reconcile(self):
        self.frontier.import_searches()
        done=0
        with self.frontier.connect() as c:
            batches=[json.loads(raw) for raw, in c.execute('SELECT payload FROM batch')]
            for b in batches:
                ids=b.get('query_keys',[])
                if not ids:continue
                for key in ids:
                    row=c.execute('SELECT payload FROM saturation_query WHERE query_key=?',(key,)).fetchone()
                    if not row:raise ValueError('Unknown query receipt: '+key)
                    p=json.loads(row[0])
                    if p['q'] not in {q['q'] for q in b['queries']}:
                        raise ValueError('Receipt does not match query text')
                    if b.get('status')!='executed':continue
                    p.update(status='executed',response_sha256=digest(b['response']),batch=b.get('batch'))
                    done+=c.execute("UPDATE saturation_query SET status='executed',payload=? WHERE query_key=? AND status!='executed'",(json.dumps(p,ensure_ascii=False),key)).rowcount
        return dict(new_search_receipts=done,**self.status())

    def status(self):
        stage=self.stage();now=self.snapshot();delta=metric_delta(stage['baseline'],now)
        baseline_documents=set(stage['baseline']['documents'])
        with self.frontier.connect() as c:
            queries=dict(c.execute('SELECT status,count(*) FROM saturation_query WHERE wave=? GROUP BY status',(stage['wave'],)))
            url_counts=dict(c.execute('SELECT status,count(*) FROM url GROUP BY status'))
            # Every newly fetched document needs its entity/case role reviewed.
            reviewed={u for u, in c.execute('SELECT url FROM saturation_review')}
            unreviewed=sum(doc_id not in baseline_documents and json.loads(raw)['canonical_url'] not in reviewed for doc_id,raw in c.execute('SELECT document_id,payload FROM document'))
        metrics=dict(new_terms=delta['terms'],new_programs=delta['programs'],new_cases=delta['cases'],new_documents=delta['documents'],
                     pending_queries=queries.get('pending',0),pending_urls=url_counts.get('pending',0),failures=url_counts.get('failed',0),
                     inherited_pending=url_counts.get('pending',0),unreviewed_documents=unreviewed,rescan_complete=False)
        ready=queries.get('executed',0)>0 and not any(metrics[k] for k in ('pending_queries','pending_urls','failures','unreviewed_documents'))
        if stage['phase']!='closed':
            stage.update(metrics=metrics,queries=queries,updated_at=utc_now(),phase='ready_for_close' if ready else 'open')
        with self.frontier.connect() as c:
            c.execute('UPDATE saturation_stage SET payload=? WHERE wave=?',(json.dumps(stage,ensure_ascii=False),stage['wave']))
        summary=dict(wave=stage['wave'],phase=stage['phase'],**metrics,queries=queries,saturated=stage.get('saturated',False),baseline_counts={k:len(v) for k,v in stage['baseline'].items()})
        (self.frontier.folder/'saturation-status.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        return summary

    def close(self):
        current=self.status()
        if current['phase']!='ready_for_close':
            raise ValueError('Cannot close: searches, source links, failures or document reviews remain')
        stage=self.stage();stage['metrics']['rescan_complete']=True
        stage.update(phase='closed',closed_at=utc_now())
        with self.frontier.connect() as c:
            previous=[json.loads(s) for s, in c.execute("SELECT payload FROM saturation_stage WHERE wave<? ORDER BY wave",(stage['wave'],)) if json.loads(s)['phase']=='closed']
            compatible=[x for x in previous if x['search_scope']==stage['search_scope']]
            stage['saturated']=saturation([x['metrics'] for x in compatible]+[stage['metrics']])
            c.execute('UPDATE saturation_stage SET payload=? WHERE wave=?',(json.dumps(stage,ensure_ascii=False),stage['wave']))
        return dict(wave=stage['wave'],phase='closed',saturated=stage['saturated'],metrics=stage['metrics'])

    def review(self,items):
        for item in items:
            url=frontier_url(item['url'])
            with self.frontier.connect() as c:
                row=c.execute("SELECT payload FROM document WHERE json_extract(payload,'$.canonical_url')=?",(url,)).fetchone()
            if not row:raise ValueError('Review requires a fetched original')
            doc=json.loads(row[0]);text=doc['title']+'\n'+Path(doc['text_path']).read_text(encoding='utf-8')
            if not item.get('evidence') or any(normalized(e) not in normalized(text) for e in item['evidence']):
                raise ValueError('Every review requires exact evidence from the original')
            if item['role']=='notice':
                if not all(item.get(k) for k in ('issuer','program')):raise ValueError('Notice entity needs issuer and program')
                key=digest(json.dumps([normalized(item['issuer']),normalized(item['program']),item.get('round'),url if not item.get('round') else None],ensure_ascii=False))
                with self.frontier.connect() as c:
                    c.execute('INSERT OR REPLACE INTO saturation_entity VALUES(?,?)',(key,json.dumps({**item,'url':url,'document_id':doc['document_id']},ensure_ascii=False)))
            with self.frontier.connect() as c:
                c.execute('INSERT OR REPLACE INTO saturation_review VALUES(?,?)',(url,json.dumps({**item,'reviewed_at':utc_now()},ensure_ascii=False)))
        return dict(reviewed=len(items),**self.status())


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('action',choices=['seed','candidates','accept','plan','next','reconcile','status','close','review'])
    p.add_argument('--file',type=Path)
    p.add_argument('--limit',type=int,default=12)
    a=p.parse_args();loop=SearchLoop(a.output)
    if a.action=='seed':loop.seed();result=loop.status()
    elif a.action=='candidates':result=loop.candidates(a.limit)
    elif a.action=='accept':
        loop.stage()
        result=dict(accepted=sum(loop.add_term(x['term'],x['lane'],x['evidence_url'],x.get('program_id')) for x in json.loads(a.file.read_text())))
    elif a.action=='plan':result=loop.plan()
    elif a.action=='next':result=loop.next(a.limit)
    elif a.action=='reconcile':result=loop.reconcile()
    elif a.action=='close':result=loop.close()
    elif a.action=='review':result=loop.review(json.loads(a.file.read_text()))
    else:result=loop.status()
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
