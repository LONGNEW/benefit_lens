"""Resumable Python-only collection of public support and internship experiences.

Candidate URLs, readable originals, accepted episodes and writing materials have
separate counters. Source holds and robots are enforced by SharedEvidence. No
agent or model is invoked; semantic review and pattern approval stay explicit.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
from hashlib import sha256, sha1
import base64
from functools import lru_cache
import io
import json
from pathlib import Path
import random
import re
import sqlite3
import subprocess
import sys
import signal
import tarfile
import threading
import time
from urllib.parse import urljoin, urlsplit, parse_qs, urlencode
import uuid
import xml.etree.ElementTree as ET

import requests

import project
sys.path.insert(0, str(project.ROOT/'pipeline/src'))
from data_platform.exploration import Frontier
from benefitlens_source_storage import read_source_bytes, store_source_bytes, source_fetched_at
from collect_benefitlens_crossborder import project_original
from import_benefitlens_full_inventory import SharedEvidence, public_url
from benefitlens_experience_transport import ExperienceEvidence
from run_benefitlens_campaign import CACHE, KST, atomic_json, single_writer
from run_benefitlens_korea import Held, compact, soup

ROOT = CACHE/'experience-scale-v1'
CONFIG = project.ROOT/'pipeline/config/experience_scale_sources.json'
VERSION = 'experience-scale-v1.4'
FAMILY = 'experience_scale_v1'


def digest(value):
    return sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def canonical(url):
    if not public_url(url):
        return None
    return urlsplit(url)._replace(fragment='').geturl()


def jsonld(document):
    result = []
    def add(node):
        if isinstance(node, list):
            for child in node: add(child)
        elif isinstance(node, dict):
            result.append(node)
            if '@graph' in node: add(node['@graph'])
    for script in document.select('script[type="application/ld+json"]'):
        try: add(json.loads(script.get_text()))
        except (ValueError, TypeError): pass
    return result


def body_text(node):
    for child in node.select('script,style,nav,footer,noscript'):
        child.decompose()
    return '\n'.join(compact(line) for line in node.stripped_strings)


def excerpt(text, needle):
    position = text.find(needle)
    if not needle or position < 0:
        raise ValueError('Experience evidence absent from stored text')
    return {'exact_quote':needle, 'char_start':position, 'char_end':position+len(needle),
            'line_start':text[:position].count('\n')+1,
            'line_end':text[:position+len(needle)].count('\n')+1}


def higherin_detail(raw, url):
    document = soup(raw)
    main = document.select_one('main')
    review = next((x for x in jsonld(document) if x.get('@type') == 'Review'), None)
    if main is None or not review:
        raise Held('parse_failed: original has no structured review')
    text = body_text(main)
    # Related cards contain other job types. Inspect only the review header.
    header = text.split('Overall Rating', 1)[0]
    kind = 'internship' if re.search(r'\bInternship\s*\(', header, re.I) else \
           'placement' if re.search(r'\bPlacement\s*\(', header, re.I) else None
    if kind is None and re.search(r'\b(?:Degree|Level\s*[2345](?:\s*/\s*[45])?)\s+Apprenticeship\b', header, re.I):
        kind = 'apprenticeship'
    if kind is None:
        return {'status':'rejected_scope','reason':'Review is not an internship, student placement or educational apprenticeship',
                'text':text}
    match = re.search(r'/review/(\d+)/([^/]+)/([^/]+)', urlsplit(url).path)
    if not match:
        raise Held('parse_failed: missing native review ID')
    native, company_slug, role_slug = match.groups()
    pairs = []
    for heading in main.select('h3,h4'):
        answer = heading.find_next_sibling('div')
        if answer is None:
            continue
        question = compact(heading.get_text(' ', strip=True))
        answer_text = '\n'.join(compact(x) for x in answer.stripped_strings)
        if len(answer_text) >= 20 and re.search(r'[a-zA-Z]{3}', answer_text):
            pairs.append({'question':question,'answer':answer_text})
    if not pairs or sum(len(x['answer']) for x in pairs) < 150:
        raise Held('insufficient_body: rating or navigation does not qualify')
    company = next((x.get('itemReviewed', {}).get('name') for x in jsonld(document)
                    if x.get('@type') == 'EmployerAggregateRating'), None) or company_slug
    # Do not relabel general recommendations as an application narrative.
    application = [x for x in pairs if re.search(r'applying|application|recruit', x['question'], re.I)
        and re.search(r'\bI\b|\bmy\b|\bwe\b', x['answer'], re.I)
        and re.search(r'appl|interview|assess|submit|revis|CV|resume|cover letter', x['answer'], re.I)]
    roles = ['participation_experience'] + (['application_process'] if application else [])
    return {'status':'accepted','text':text, 'native_id':native,
        'case_key':'experience-higherin-'+native,
        'program_id':'higherin:'+company_slug+':'+kind+':'+role_slug,
        'program_name':company+' / '+role_slug.replace('-', ' '),
        'program_binding_level':'employer_role_and_type', 'person_name':None,
        'author_handle':compact((review.get('author') or {}).get('name')) or 'Anonymous',
        'identity_status':'anonymous_source_record', 'cycle':None,
        'cycle_basis':'not_reported; datePublished_is_not_participation_round',
        'published_at':review.get('datePublished'), 'opportunity_type':kind,
        'outcome':'unknown', 'outcome_phase':'participation_reported',
        'outcome_basis':'author_participation_report; selection_result_not_assumed',
        'funding_status':'not_confirmed', 'evidence_roles':roles,
        'case_scope':'participation_experience',
        'evidence':[excerpt(text, pair['answer']) | {'claim_type':'personal_experience',
            'question':pair['question']} for pair in pairs],
        # Compare substantive answers; identical survey questions are templates.
        'normalized_experience':compact(' '.join(x['answer'] for x in pairs)).casefold(),
        'related_urls':[urljoin(url,a['href']) for a in main.select('a[href]')
                        if re.fullmatch(r'/review/\d+/[^/]+/[^/]+/?',urlsplit(urljoin(url,a['href'])).path)]}


def dev_detail(raw, url):
    data=json.loads(raw)
    if not data.get('id') or not data.get('body_html'):
        raise Held('parse_failed: API did not return a complete published article')
    text=body_text(soup(data['body_html'].encode()))
    programs=[('google-summer-of-code',r'\bGSoC\b|Google Summer of Code'),
              ('outreachy',r'\bOutreachy\b'),
              ('mlh-fellowship',r'\bMLH\s+Fellowship\b|Major League Hacking Fellowship'),
              ('lfx-mentorship',r'\bLFX\s+Mentorship\b|Linux Foundation Mentorship'),
              ('google-season-of-docs',r'\bGoogle\s+Season\s+of\s+Docs\b'),
              ('summer-of-bitcoin',r'\bSummer\s+of\s+Bitcoin\b'),
              ('xros-fellowship',r'\bXROS\s+Fellowship\b'),
              ('fulbright',r'\bFulbright\b'),
              ('chevening',r'\bChevening\b'),
              ('daad-rise',r'\b(?:DAAD\s+RISE|RISE\s+(?:Germany|Professional))\b'),
              ('erasmus-student-mobility',r'\bErasmus\+?\b')]
    matches=[(program,re.search(pattern,text,re.I)) for program,pattern in programs]
    matches=[(program,m) for program,m in matches if m]
    personal=re.search(r'\bI (?:was|applied|submitted|received|joined|started|worked|completed|have been|had)\b[^\n]{8,500}',text,re.I)
    if len(matches)!=1 or not personal or len(text)<300:
        return {'status':'needs_review','reason':'Original stored; personal episode and one program need review','text':text}
    program,program_match=matches[0]
    user=data.get('user',{});handle=user.get('username')
    if not handle:
        raise Held('identity_unresolved: article lacks a public author handle')
    # A publication year or cohort/batch year is never an application round.
    years=set(re.findall(r'(?:GSoC|Google Summer of Code)\s*[-:]?\s*(20\d{2})',text,re.I))
    cycle=next(iter(years)) if program=='google-summer-of-code' and len(years)==1 else None
    process_story=bool(re.search(r'\b(?:I|my)\b[^\n]{0,100}(?:appl|submit|propos|interview|reject)',text,re.I))
    key='experience-dev-'+digest(json.dumps([handle,program,cycle]))[:24]
    return {'status':'accepted','text':text,'native_id':str(data['id']),'case_key':key,
        'public_article_url':data.get('url'), 'program_id':program,'program_name':program,
        'program_binding_level':'program_name_in_author_body','person_name':user.get('name'),
        'author_handle':handle,'identity_status':'public_platform_handle',
        'cycle':cycle,'cycle_basis':'program_year_in_body' if cycle else 'unknown_round_author_cluster',
        'published_at':data.get('published_at'),'opportunity_type':
            'scholarship' if program in {'fulbright','chevening'} else
            'student_mobility' if program=='erasmus-student-mobility' else
            'research_internship' if program=='daad-rise' else 'open_source_stipend_program',
        'outcome':'unknown','outcome_phase':'application_or_participation_narrative',
        'outcome_basis':'not_inferred_from_celebratory_title','funding_status':'not_confirmed',
        'evidence_roles':['application_process'] if process_story else ['participation_experience'],
        'case_scope':'application_process' if process_story else 'participation_experience',
        'evidence':[excerpt(text,personal[0])|{'claim_type':'personal_action'},
                    excerpt(text,program_match[0])|{'claim_type':'program_binding'}],
        'normalized_experience':compact(text).casefold(),'related_urls':[]}


def reference_episode(raw, url, source_id, root):
    _,text=project_original(raw,root)
    personal=re.search(r'\b(?:I (?:was|applied|spent|received|joined|worked|completed|had)|'
        r'Ich (?:habe|war|bin|wurde|konnte)|ich (?:habe|war|bin|wurde|konnte))\b[^\n]{8,500}',text)
    pattern=r'\bErasmus\+?\b' if source_id=='heidelberg-erasmus' else r'\bRISE\b'
    program_match=re.search(pattern,text,re.I)
    if not personal or not program_match or len(text)<500:
        return {'status':'needs_review','reason':'Full original stored; personal action and program binding unresolved',
                'text':text,'evidence_roles':['unbound_reference_document']}
    if source_id=='daad-rise-germany' and re.search(r'RISE\s+Professional',text,re.I):
        return {'status':'needs_review','reason':'Program in body conflicts with source category','text':text}
    cycles=set(re.findall(r'(?:Wintersemester|Sommersemester)\s*\d{4}(?:\s*/\s*\d{2,4})?',text,re.I))
    cycle=next(iter(cycles)) if source_id=='heidelberg-erasmus' and len(cycles)==1 else None
    return {'status':'accepted','text':text,'native_id':digest(url)[:24],
        'case_key':'experience-report-'+digest(url)[:28],
        'program_id':'erasmus-student-mobility' if source_id=='heidelberg-erasmus' else source_id,
        'program_name':source_id,'program_binding_level':'official_program_index_and_body',
        'person_name':None,'author_handle':None,'identity_status':'anonymous_publisher_report',
        'cycle':cycle,'cycle_basis':'explicit_semester_in_body' if cycle else 'not_confirmed',
        'published_at':None,'opportunity_type':'student_mobility' if source_id=='heidelberg-erasmus' else 'research_internship',
        'outcome':'unknown','outcome_phase':'participation_reported',
        'outcome_basis':'not_inferred_from_program_report','funding_status':'not_confirmed',
        'evidence_roles':['participation_experience'],'case_scope':'participation_experience',
        'evidence':[excerpt(text,personal[0])|{'claim_type':'personal_action'},
                    excerpt(text,program_match[0])|{'claim_type':'program_binding'}],
        'normalized_experience':compact(text).casefold(),'related_urls':[]}


def github_proposal(raw, task, root):
    data=json.loads(raw)
    if data.get('encoding')!='base64' or not data.get('content'):
        raise Held('parse_failed: GitHub blob is not a complete base64 original')
    decoded=base64.b64decode(re.sub(r'\s','',data['content']),validate=True)
    return github_proposal_pdf(decoded,task,root,data.get('sha'))


def github_proposal_pdf(decoded, task, root, expected_sha):
    git_hash=sha1(b'blob '+str(len(decoded)).encode()+b'\0'+decoded).hexdigest()
    if git_hash!=expected_sha or not decoded.startswith(b'%PDF'):
        raise Held('parse_failed: Git blob hash or PDF signature mismatch')
    _,text=project_original(decoded,root)
    program=re.search(r'\bGSoC\b|Google\s+Summer\s+of\s+Code',text,re.I)
    if not program or len(text)<500:
        return {'status':'needs_review','reason':'Proposal PDF collected; program binding needs review','text':text}
    years=set(re.findall(r'(?:GSoC|Google\s+Summer\s+of\s+Code)\s*[-:]?\s*(20\d{2})',text,re.I))
    cycle=next(iter(years)) if len(years)==1 else None
    path=task.get('file_path','')
    # A filename supplies a candidate name; the PDF must contain that name.
    name_match=re.search(r' - 20\d{2} - (.+)\.pdf$',path,re.I)
    name=name_match[1] if name_match and compact(name_match[1]).casefold() in compact(text).casefold() else None
    identity=['google-summer-of-code',name.casefold(),cycle] if name and cycle else [task['source'],path]
    return {'status':'accepted','text':text,'native_id':path,
        'case_key':'experience-proposal-'+digest(json.dumps(identity))[:24],
        'program_id':'google-summer-of-code','program_name':'Google Summer of Code',
        'program_binding_level':'program_in_public_proposal_pdf','person_name':name,'author_handle':None,
        'identity_status':'name_in_pdf_and_repository_path' if name else 'anonymous_repository_document',
        'cycle':cycle,'cycle_basis':'program_year_in_pdf' if cycle else 'not_confirmed',
        'published_at':None,'opportunity_type':'open_source_stipend_program',
        'outcome':'unknown','outcome_phase':'public_application_document',
        'outcome_basis':'repository_accepted_label_not_independently_verified',
        'funding_status':'not_confirmed','evidence_roles':['application_document'],
        'case_scope':'application_document','document_finality':'repository_application_copy; final_submission_unverified',
        'decoded_document':store_source_bytes(decoded,root/'blobs',suffix='.pdf'),
        'evidence':[excerpt(text,program[0])|{'claim_type':'program_binding'}],
        'normalized_experience':compact(text).casefold(),'related_urls':[]}


def wordpress_detail(data, source):
    """Bind a named first-person Fulbright story from the content, not WP tags."""
    html=(data.get('content') or {}).get('rendered','')
    text=body_text(soup(html.encode()))
    title=compact(soup((data.get('title') or {}).get('rendered','').encode()).get_text(' ',strip=True))
    header=text[:900]
    programs=[('fulbright-graduate-student-program',r'Graduate\s+Student\s+Program|대학원\s*장학\s*프로그램'),
              ('fulbright-english-teaching-assistant',r'English\s+Teaching\s+Assistant(?:ship)?(?:\s+Program)?')]
    bound=[(key,re.search(pattern,header,re.I)) for key,pattern in programs]
    bound=[(key,m) for key,m in bound if m]
    personal=re.search(r'\bI (?:was|applied|spent|received|joined|worked|completed|had)\b[^\n]{8,500}|'
        r'(?:저는|제가|저에게|저의|제\s+(?:삶|연구|경험))[^\n]{8,500}',text,re.I)
    name_pattern=r'^[A-Z][\w.\-]+(?:[ -][A-Z][\w.\-]+){1,5}$|^[가-힣]{2,6}$'
    name_match=re.search(re.escape(title),header,re.I) if title and re.fullmatch(name_pattern,title) else None
    if len(bound)!=1 or not personal or not name_match or len(text)<500:
        return {'status':'needs_review','reason':'Full API content stored; single named participant and program need review',
                'text':text,'public_article_url':data.get('link')}
    program,pm=bound[0]
    rounds=set(re.findall(r'(20\d{2})\s+(?:Fulbright\s+)?(?:Korean\s+)?'+
        r'(?:Graduate Student Program|English Teaching Assistant(?:ship)?(?: Program)?)',header,re.I))
    cycle=next(iter(rounds)) if len(rounds)==1 else None
    roles=['participation_experience']
    if re.search(r'(?:저는|제가|\bI\b)[^\n]{0,200}(?:지원서|면접|자기소개서|에세이|applied|submitted|interview|proposal)',text,re.I):
        roles.append('application_process')
    identity=[title.casefold(),program,cycle]
    return {'status':'accepted','text':text,'native_id':str(data['id']),
        'case_key':'experience-fulbright-'+digest(json.dumps(identity))[:24],
        'public_article_url':data.get('link'),'program_id':program,'program_name':pm[0],
        'program_binding_level':'named_program_in_story_header','person_name':title,'author_handle':None,
        'identity_status':'named_first_person_publisher_story','cycle':cycle,
        'cycle_basis':'explicit_program_year_in_story_header' if cycle else 'not_confirmed',
        'published_at':data.get('date'),'opportunity_type':'scholarship' if 'graduate' in program else 'teaching_assistantship',
        'outcome':'unknown','outcome_phase':'participation_reported',
        'outcome_basis':'not_inferred_from_alumni_category','funding_status':'not_confirmed',
        'evidence_roles':roles,'case_scope':'participation_experience',
        'evidence':[excerpt(text,personal[0])|{'claim_type':'personal_action'},
                    excerpt(text,pm[0])|{'claim_type':'program_binding'},
                    excerpt(text,name_match[0])|{'claim_type':'person_binding'}],
        'normalized_experience':compact(text).casefold(),'related_urls':[]}


@lru_cache(maxsize=1)
def archive_members(path, expected_hash):
    """Read bounded regular files only; never extract archive paths to disk."""
    raw=read_source_bytes(path)
    if digest(raw)!=expected_hash:
        raise ValueError('Archive original hash mismatch')
    result={};total=0
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:*') as archive:
        for member in archive:
            if not member.isfile() or not member.name.lower().endswith('.pdf'):
                continue
            parts=member.name.split('/')
            if len(parts)<2 or any(x in ('','..','.') for x in parts) or member.name.startswith('/'):
                raise ValueError('Unsafe archive member path')
            if member.size>128*1024**2:
                raise ValueError('Archive member exceeds byte ceiling')
            total+=member.size
            if total>512*1024**2 or len(result)>=10000:
                raise ValueError('Expanded archive exceeds byte ceiling')
            content=archive.extractfile(member).read(member.size+1)
            if len(content)!=member.size:
                raise ValueError('Truncated archive member')
            result['/'.join(parts[1:])]=content
    return result


def list_children(raw, task, source, meta=None):
    url, adapter = task['url'], source['adapter']
    if task['kind']=='rss_index':
        if meta is None: raise Held('RSS original receipt missing')
        items=ET.fromstring(raw).findall('./channel/item')
        return [{'url':item.findtext('link'),'kind':'detail','feed_original':meta,'record_index':i,
                 'record_guid':item.findtext('guid')}
                for i,item in enumerate(items) if canonical(item.findtext('link') or '')
                and urlsplit(item.findtext('link')).hostname in source['allowed_hosts']
                and item.findtext('{http://purl.org/rss/1.0/modules/content/}encoded')]
    if task['kind']=='wordpress_index':
        data=json.loads(raw)
        if not isinstance(data,list) or meta is None:
            raise Held('parse_failed: WordPress list or original receipt missing')
        children=[]
        for position,item in enumerate(data):
            record_url=((item.get('_links') or {}).get('self') or [{}])[0].get('href')
            if record_url and canonical(record_url) and urlsplit(record_url).hostname in source['allowed_hosts']:
                children.append({'url':record_url,'kind':'detail','batch_original':meta,
                                 'record_index':position,'record_id':item['id']})
        q=parse_qs(urlsplit(url).query);page=int(q.get('page',['1'])[0])
        pages=int(meta.get('headers',{}).get('x-wp-totalpages',page+(1 if data else 0)))
        if data and page<min(pages,source.get('max_index_pages',250)):
            q['page']=[str(page+1)]
            children.append({'url':urlsplit(url)._replace(query=urlencode(q,doseq=True)).geturl(),
                             'kind':'wordpress_index'})
        return children
    if task['kind']=='github_index':
        data=json.loads(raw)
        if data.get('truncated'):
            raise Held('index_incomplete: recursive tree truncated; split enumeration required')
        return [{'url':node['url'],'kind':'detail','file_path':node['path']}
                for node in data.get('tree',[]) if node.get('type')=='blob'
                and node.get('path','').lower().endswith('.pdf')
                and canonical(node.get('url','')) and urlsplit(node['url']).hostname=='api.github.com']
    if task['kind']=='api_index':
        data=json.loads(raw)
        if not isinstance(data,list):
            raise Held('parse_failed: API article list is not an array')
        result=[{'url':'https://dev.to/api/articles/'+str(x['id']),'kind':'detail',
                 'public_article_url':x.get('url')} for x in data if isinstance(x.get('id'),int)]
        query=parse_qs(urlsplit(url).query)
        page=int(query.get('page',['1'])[0])
        if data and page<source.get('max_index_pages',250):
            query['page']=[str(page+1)]
            result.append({'url':urlsplit(url)._replace(query=urlencode(query,doseq=True)).geturl(),
                           'kind':'api_index'})
        return result
    if task['kind'] == 'sitemap':
        tree = ET.fromstring(raw)
        if tree.tag.rsplit('}',1)[-1] == 'sitemapindex':
            return [{'url':node.text,'kind':'sitemap'} for node in tree.findall('{*}sitemap/{*}loc')
                    if canonical(node.text or '') and
                    urlsplit(node.text).hostname in source['allowed_hosts']]
        children = [{'url':node.text,'kind':'detail'} for node in tree.findall('{*}url/{*}loc')
                    if canonical(node.text or '') and re.search(source['detail_pattern'],node.text)]
        # Fixed random order prevents sampling only the newest scheme or IDs.
        random.Random(20261115).shuffle(children)
        return children
    document = soup(raw)
    children = []
    for a in document.select(source.get('link_selector', 'a[href]')):
        child = canonical(urljoin(url,a.get('href','')))
        if not child or urlsplit(child).hostname not in source['allowed_hosts']:
            continue
        if re.search(source['detail_pattern'], child):
            children.append({'url':child,'kind':'detail','label':compact(a.get_text(' ',strip=True))})
        elif source.get('pagination_pattern') and re.search(source['pagination_pattern'],child):
            children.append({'url':child,'kind':'index'})
    return children


class ExperienceFrontier(Frontier):
    """Reuse the repository frontier and add durable leases and import receipts."""
    def __init__(self, folder):
        super().__init__(folder)
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS experience_task(
              key TEXT PRIMARY KEY,source TEXT NOT NULL,url TEXT NOT NULL,kind TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending',payload TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,next_at REAL NOT NULL DEFAULT 0,
              lease_until REAL,run_id TEXT,result TEXT,error TEXT);
            CREATE INDEX IF NOT EXISTS experience_task_state ON experience_task(state,next_at);
            CREATE TABLE IF NOT EXISTS experience_result(
              case_key TEXT PRIMARY KEY,source TEXT NOT NULL,document_id TEXT NOT NULL,
              text_key TEXT NOT NULL,result TEXT NOT NULL,pg_imported INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS experience_result_text ON experience_result(text_key);
            CREATE TABLE IF NOT EXISTS experience_case_role(case_key TEXT,role TEXT,PRIMARY KEY(case_key,role));
            INSERT OR IGNORE INTO experience_case_role
              SELECT case_key,value FROM experience_result,json_each(experience_result.result,'$.evidence_roles');
            CREATE TABLE IF NOT EXISTS experience_run(run_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS experience_document_import(document_id TEXT PRIMARY KEY,pg_id TEXT NOT NULL);
            ''')

    def add(self, source, child, parent='', cohort='expansion'):
        url = canonical(child['url'])
        if url is None:
            return False
        payload = dict(child, url=url, source=source, parent=parent, cohort=cohort)
        with self.lock, self.connect() as c:
            changed = c.execute('INSERT OR IGNORE INTO experience_task(key,source,url,kind,payload) VALUES(?,?,?,?,?)',
                (digest(source+'\n'+url),source,url,child['kind'],json.dumps(payload,ensure_ascii=False))).rowcount
            c.execute('INSERT OR IGNORE INTO url VALUES(?,?,?,?)',
                (url,'experience_scale','pending',json.dumps(payload,ensure_ascii=False)))
            if parent:
                c.execute('INSERT OR IGNORE INTO edge VALUES(?,?,?)',
                    (parent,url,json.dumps({'source':source,'discovery':'observed_publisher_link'})))
        return bool(changed)

    def prior_sitemap(self, source, entry):
        """Reuse a previously captured public discovery list, never fake a fetch.

        A current policy hold stays on the remote index task. Historical links
        are just candidates; each original detail still needs a permitted fetch.
        """
        raw=Path(entry['path']).read_bytes()
        if digest(raw)!=entry['sha256'] or entry['status']!=200:
            raise ValueError('Prior public discovery receipt failed integrity')
        children=list_children(raw,{'url':entry['url'],'kind':'sitemap'},source)
        with self.lock,self.connect() as c:
            batch='prior-discovery:'+digest(raw)
            if c.execute('SELECT 1 FROM batch WHERE batch_key=?',(batch,)).fetchone():
                return 0
            for child in children:
                payload=dict(child,source=source['id'],parent=entry['url'],cohort='prior_public_sitemap',
                    discovery_receipt={'sha256':entry['sha256'],'observed_at':entry['observed_at'],
                                       'origin':'previous_public_snapshot'})
                c.execute('INSERT OR IGNORE INTO experience_task(key,source,url,kind,payload) VALUES(?,?,?,?,?)',
                    (digest(source['id']+'\n'+child['url']),source['id'],child['url'],child['kind'],json.dumps(payload)))
                c.execute('INSERT OR IGNORE INTO url VALUES(?,?,?,?)',
                    (child['url'],'experience_scale','pending',json.dumps(payload)))
            c.execute('INSERT INTO batch VALUES(?,?)',(batch,json.dumps(entry)))
        return len(children)

    def lease(self, run_id, limit=8, *, details=True, busy_hosts=()):
        now = time.time()
        with self.lock, self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE experience_task SET state='pending',lease_until=NULL WHERE state='processing' AND lease_until<?",(now,))
            rows=c.execute("""SELECT key,payload FROM (
                SELECT key,payload,rowid AS sequence,
                  row_number() OVER(PARTITION BY source ORDER BY CASE WHEN kind='detail' THEN 1 ELSE 0 END,
                  CASE WHEN url LIKE '%intern%' OR url LIKE '%placement%' THEN 0 ELSE 1 END,rowid) AS source_rank
                FROM experience_task WHERE state IN ('pending','retry') AND next_at<=? AND (? OR kind!='detail'))
                ORDER BY source_rank,sequence LIMIT ?""",
                (now,details,max(100,limit*32))).fetchall()
            chosen=[];occupied=set(busy_hosts)
            for key,payload in rows:
                task=json.loads(payload);host=urlsplit(task['url']).hostname
                local=task.get('kind')=='cached_material' or any(task.get(k) for k in ('archive_original','batch_original','feed_original','reuse_document'))
                if host in occupied and not local:
                    continue
                chosen.append((key,payload))
                if not local: occupied.add(host)
                if len(chosen)>=limit: break
            rows=chosen
            for key,_ in rows:
                c.execute("UPDATE experience_task SET state='processing',run_id=?,lease_until=?,attempts=attempts+1 WHERE key=?",
                    (run_id,now+600,key))
        return [dict(json.loads(payload),key=key) for key,payload in rows]

    def defer_rate_hosts(self, holds):
        with self.connect() as c:
            for host,until in holds.items():
                if until<=time.time(): continue
                c.execute("""UPDATE experience_task SET state='retry',next_at=max(next_at,?)
                    WHERE state IN ('pending','retry') AND url LIKE ?
                    AND kind!='cached_material'
                    AND json_type(payload,'$.archive_original') IS NULL
                    AND json_type(payload,'$.batch_original') IS NULL
                    AND json_type(payload,'$.feed_original') IS NULL
                    AND json_type(payload,'$.reuse_document') IS NULL""",(until,'https://'+host+'/%'))

    def next_ready_at(self):
        with self.connect() as c:
            return c.execute("SELECT min(next_at) FROM experience_task WHERE state IN ('pending','retry')").fetchone()[0]

    def reparse_unresolved(self):
        with self.connect() as c:
            rows=c.execute("SELECT key,payload,result FROM experience_task WHERE state='needs_review'").fetchall()
            for key,payload,result in rows:
                doc=json.loads(result or '{}').get('document')
                if doc and doc.get('parser_version')!=VERSION:
                    item=json.loads(payload);item['reuse_document']=doc
                    c.execute("UPDATE experience_task SET state='pending',payload=? WHERE key=?",
                        (json.dumps(item),key))

    def reparse_expanded_scope(self):
        """Revisit complete Higherin originals once per parser version, locally.

        Preserve the earlier decision in the task payload. No fresh network
        request is needed, and general graduate jobs remain outside the scope.
        """
        count=0
        with self.connect() as c:
            rows=c.execute("""SELECT key,payload,result FROM experience_task
                WHERE source='higherin' AND state='rejected_scope'""").fetchall()
            for key,payload,result in rows:
                previous=json.loads(result or '{}'); doc=previous.get('document')
                if not doc or doc.get('parser_version')==VERSION:
                    continue
                item=json.loads(payload)
                item.update(reuse_document=doc, previous_scope_decision={
                    'status':previous['status'],'reason':previous.get('reason'),
                    'parser_version':doc.get('parser_version')})
                c.execute("UPDATE experience_task SET state='pending',payload=?,next_at=0 WHERE key=?",
                          (json.dumps(item),key))
                count+=1
        return count

    def recover_interrupted(self):
        # Caller owns the root's exclusive process lock; no other worker lives.
        with self.connect() as c:
            c.execute("UPDATE experience_task SET state='pending',lease_until=NULL WHERE state='processing'")

    def finish(self, task, result, *, children=()):
        # Child discovery and parent checkpoint are one transaction.
        with self.lock, self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for child in children:
                url = canonical(child['url'])
                if url:
                    p=dict(child,url=url,source=task['source'],parent=task['url'],cohort='expansion')
                    c.execute('INSERT OR IGNORE INTO experience_task(key,source,url,kind,payload) VALUES(?,?,?,?,?)',
                        (digest(task['source']+'\n'+url),task['source'],url,child['kind'],json.dumps(p,ensure_ascii=False)))
                    if child.get('archive_original') or child.get('batch_original') or child.get('feed_original'):
                        c.execute("""UPDATE experience_task SET payload=?,state='pending',next_at=0
                            WHERE key=? AND state IN ('waiting_bulk','pending','retry','policy_held','needs_review')""",
                            (json.dumps(p,ensure_ascii=False),digest(task['source']+'\n'+url)))
                    c.execute('INSERT OR IGNORE INTO url VALUES(?,?,?,?)',
                        (url,'experience_scale','pending',json.dumps(p,ensure_ascii=False)))
                    c.execute('INSERT OR IGNORE INTO edge VALUES(?,?,?)',
                        (task['url'],url,json.dumps({'source':task['source']})))
            status=result['status']
            if status == 'accepted':
                duplicate=c.execute('SELECT case_key FROM experience_result WHERE text_key=? AND case_key!=?',
                    (result['text_key'],result['case_key'])).fetchone()
                if duplicate:
                    status='duplicate'
                    result.update(status=status,duplicate_of=duplicate[0])
                else:
                    previous=c.execute('SELECT result FROM experience_result WHERE case_key=?',(result['case_key'],)).fetchone()
                    documents={d['document_id']:d for d in (json.loads(previous[0]).get('documents',[]) if previous else [])}
                    documents[result['document']['document_id']]=result['document']
                    result['documents']=list(documents.values())
                    if previous:
                        old=json.loads(previous[0])
                        evidence={(e['document_id'],e['char_start'],e['char_end']):e for e in old['evidence']+result['evidence']}
                        result['evidence']=list(evidence.values())
                        result['evidence_roles']=sorted(set(old['evidence_roles']+result['evidence_roles']))
                    c.executemany('INSERT OR IGNORE INTO experience_case_role VALUES(?,?)',
                        [(result['case_key'],role) for role in result['evidence_roles']])
                    c.execute('''INSERT INTO experience_result VALUES(?,?,?,?,?,0)
                        ON CONFLICT(case_key) DO UPDATE SET result=excluded.result,
                        document_id=excluded.document_id,text_key=excluded.text_key,pg_imported=0''',
                        (result['case_key'],task['source'],result['document']['document_id'],
                         result['text_key'],json.dumps(result,ensure_ascii=False)))
                    c.execute('INSERT OR REPLACE INTO experience VALUES(?,?)',
                        (result['case_key'],json.dumps(result,ensure_ascii=False)))
            retry_at=result.get('next_at',0)
            c.execute('UPDATE experience_task SET state=?,result=?,error=?,lease_until=NULL,next_at=? WHERE key=?',
                (status,json.dumps(result,ensure_ascii=False),result.get('reason'),retry_at,task['key']))
            c.execute('UPDATE url SET status=? WHERE url=?',(status,task['url']))
            if result.get('document'):
                doc=result['document']
                encoded_doc=json.dumps(doc,ensure_ascii=False)
                previous_doc=c.execute('SELECT payload FROM document WHERE document_id=?',
                                       (doc['document_id'],)).fetchone()
                if previous_doc and previous_doc[0] != encoded_doc:
                    c.execute('DELETE FROM experience_document_import WHERE document_id=?',
                              (doc['document_id'],))
                c.execute('INSERT OR REPLACE INTO document VALUES(?,?)',
                    (doc['document_id'],encoded_doc))

    def snapshot(self):
        with self.connect() as c:
            sources={}
            for source,kind,state,n in c.execute('SELECT source,kind,state,count(*) FROM experience_task GROUP BY source,kind,state'):
                sources.setdefault(source,{}).setdefault(kind,{})[state]=n
            accepted=c.execute('SELECT count(*) FROM experience_result').fetchone()[0]
            imported=c.execute('SELECT count(*) FROM experience_result WHERE pg_imported=1').fetchone()[0]
            originals=c.execute('SELECT count(*) FROM document').fetchone()[0]
            original_imports=c.execute('SELECT count(*) FROM experience_document_import').fetchone()[0]
            source_cases=dict(c.execute('SELECT source,count(*) FROM experience_result GROUP BY source').fetchall())
            roles=dict(c.execute('SELECT role,count(*) FROM experience_case_role GROUP BY role').fetchall())
        return {'sources':sources,'accepted_cases':accepted,'postgres_imported_cases':imported,
                'collected_original_documents':originals,'postgres_imported_documents':original_imports,
                'source_case_counts':source_cases,
                'evidence_role_case_counts':dict(roles)}


RunEvidence = ExperienceEvidence


def capture(ledger,url):
    visited=set()
    for _ in range(6):
        if not canonical(url) or url in visited:
            raise Held('Unsafe public redirect or redirect loop')
        visited.add(url)
        raw,meta=ledger.request(url,redirect_metadata=True)
        if meta['status'] in (301,302,303,307,308):
            url=urljoin(url,meta['headers'].get('location',''))
            continue
        if re.search(rb'verify you are human|captcha required|access denied',raw[:25000],re.I):
            raise Held('access_challenge')
        return raw,meta,url
    raise Held('Redirect limit')


def failure(error, task):
    reason=str(error)[:500]
    if getattr(error,'next_at',None):
        return {'status':'retry','reason':reason,'next_at':error.next_at}
    if re.search('budget reached',reason,re.I):
        return {'status':'pending','reason':reason}
    if re.search('429|timeout|connection|502|503|504',reason,re.I):
        return {'status':'retry','reason':reason,'next_at':time.time()+1800}
    if re.search('robots|401|403|Existing campaign source hold|Publisher paused',reason,re.I):
        return {'status':'policy_held','reason':reason}
    return {'status':'needs_review','reason':reason}


def process(task, source, ledger):
    try:
        previous=task.get('reuse_document')
        if task.get('archive_original'):
            origin=task['archive_original']
            raw=archive_members(origin['path'],origin['sha256'])[task['file_path']]
            meta=dict(url=origin['url'],status=200,at=origin['at'],transport='verified_archive_member',
                      source_archive=origin,archive_member=task['file_path'],
                      **store_source_bytes(raw,ledger.root/'blobs',suffix='.pdf'))
            url=origin['url']
        elif task.get('batch_original') or task.get('feed_original'):
            meta=task.get('batch_original') or task['feed_original'];url=meta['url']
            raw=read_source_bytes(meta['path'],storage=meta.get('storage'))
            if digest(raw)!=meta['sha256']:
                raise ValueError('Batch original integrity failed')
        elif previous:
            meta=previous['raw'];url=previous['final_url']
            raw=read_source_bytes(meta['path'],storage=meta.get('storage'))
            if digest(raw)!=meta['sha256'] or meta['status']!=200:
                raise ValueError('Prior complete original integrity failed')
        else:
            raw,meta,url=capture(ledger,task['url'])
        if task['kind']=='github_archive':
            tree_raw,_,_=capture(ledger,source['tree_url'])
            expected=list_children(tree_raw,{'url':source['tree_url'],'kind':'github_index'},source)
            tree_data=json.loads(tree_raw)
            by_path={n['path']:n for n in tree_data['tree'] if n['type']=='blob'}
            members=archive_members(meta['path'],meta['sha256']);children=[]
            for item in expected:
                path=item['file_path'];node=by_path[path];content=members.get(path)
                if content is None: continue
                if sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()!=node['sha']:
                    continue
                children.append(dict(item,archive_original=meta,expected_sha=node['sha']))
            return {'status':'indexed','original':meta,'observed_children':len(children),
                    'archive_pdf_count':len(members),'expected_pdf_count':len(expected),
                    'tree_original_sha256':digest(tree_raw)},children
        if task['kind']!='detail':
            children=list_children(raw,task,source,meta)
            return {'status':'indexed','original':meta,'observed_children':len(children)},children
        if source['adapter']=='higherin':
            result=higherin_detail(raw,url)
        elif source['adapter']=='dev_api':
            result=dev_detail(raw,url)
        elif source['adapter']=='github_proposals':
            result=github_proposal_pdf(raw,task,ledger.root,task['expected_sha']) if task.get('archive_original') else \
                   github_proposal(raw,task,ledger.root)
        elif source['adapter']=='wordpress_api':
            record=json.loads(raw)[task['record_index']]
            if record['id']!=task['record_id']:
                raise ValueError('Batch record pointer mismatch')
            result=wordpress_detail(record,source)
        elif source['adapter']=='rss_fulltext':
            item=ET.fromstring(raw).findall('./channel/item')[task['record_index']]
            if item.findtext('guid')!=task['record_guid'] or item.findtext('link')!=task['url']:
                raise ValueError('RSS record pointer mismatch')
            text=body_text(soup(item.findtext('{http://purl.org/rss/1.0/modules/content/}encoded').encode()))
            result={'status':'reference_material','text':text,
                'reason':'Full publisher story stored; individual subjects require attribution review',
                'evidence_roles':['public_interview_or_program_story'],'published_at':item.findtext('pubDate')}
        else:
            result=reference_episode(raw,url,task['source'],ledger.root)
        text=result.pop('text')
        if '\x00' in text:
            raise Held('parse_failed: source contains NUL characters')
        textref=store_source_bytes(text.encode(),ledger.root/'blobs',suffix='.txt')
        rawhash=digest(raw)
        document={'document_id':'experience-doc-'+digest(task['url']+'\n'+rawhash)[:32],
            'url':result.pop('public_article_url',None) or task['url'],'final_url':url,'raw':meta,'text':textref,
            'kind':'json' if raw.lstrip().startswith((b'{',b'[')) else 'pdf' if raw.startswith(b'%PDF') else 'html',
            'parser_version':VERSION,'source_id':task['source'],'discovery_parent':task.get('parent'),
            'collection_status':result['status'],'evidence_roles':result.get('evidence_roles',[]),
            'access_scope':'public','reuse_license':'not_inferred_from_public_access'}
        if task.get('batch_original'):
            document.update(record_pointer='/'+str(task['record_index']),native_record_id=task['record_id'])
        if task.get('feed_original'):
            document.update(record_pointer='/channel/item/'+str(task['record_index']),native_record_guid=task['record_guid'],
                            kind='xml')
        if task.get('archive_original'):
            document.update(archive_member=task['file_path'],git_blob_sha1=task['expected_sha'])
        result['document']=document
        if result.get('decoded_document'):
            document['decoded_document']=result.pop('decoded_document')
        children=[]
        if result['status']=='accepted':
            normalized=result.pop('normalized_experience')
            result['text_key']=digest(normalized)
            result.update(source_id=task['source'],semantic_review='unreviewed',approved_pattern=False,
                scope_validation='source_specific_program_and_personal_content_checks',
                attribution_status='single_native_review',cross_source_identity_status='unconfirmed')
            for item in result['evidence']:
                item.update(document_id=document['document_id'],text_sha256=textref['sha256'])
            for child in result.pop('related_urls',[]):
                if urlsplit(child).hostname in source['allowed_hosts']:
                    children.append({'url':child,'kind':'detail'})
        return result,children
    except (Held,ValueError,OSError,requests.RequestException,ET.ParseError,tarfile.TarError,subprocess.SubprocessError) as error:
        held_until=getattr(ledger,'rate_holds',{}).get(urlsplit(task['url']).hostname,0)
        if held_until>time.time():
            return {'status':'retry','reason':'Publisher rate limit; existing hold retained',
                    'next_at':held_until},[]
        return failure(error,task),[]


def import_originals(frontier,limit=100):
    from psycopg.types.json import Jsonb
    with frontier.connect() as c:
        rows=c.execute('''SELECT d.document_id,d.payload FROM document d
            LEFT JOIN experience_document_import i ON d.document_id=i.document_id
            WHERE i.document_id IS NULL LIMIT ?''',(limit,)).fetchall()
    if not rows: return 0
    receipts=[]
    with project.connect() as conn:
        for key,encoded in rows:
            doc=json.loads(encoded)
            raw=read_source_bytes(doc['raw']['path'],storage=doc['raw'].get('storage'))
            text=read_source_bytes(doc['text']['path'],storage=doc['text'].get('storage')).decode()
            if digest(raw)!=doc['raw']['sha256'] or digest(text)!=doc['text']['sha256'] or '\x00' in text:
                raise ValueError('Complete original failed hash validation')
            metadata={'experience_scale':dict(doc,full_text=text,semantic_review='unreviewed',approved_pattern=False)}
            pgid=conn.execute('''INSERT INTO benefitlens.pipeline_document
                (document_id,canonical_url,content_sha256,text_sha256,kind,raw_path,text_path,fetched_at,metadata)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(canonical_url_hash,content_sha256) DO UPDATE
                SET metadata=benefitlens.pipeline_document.metadata || excluded.metadata RETURNING document_id''',
                (doc['document_id'],doc['url'],doc['raw']['sha256'],doc['text']['sha256'],doc['kind'],
                 doc['raw']['path'],doc['text']['path'],source_fetched_at(doc['raw']),Jsonb(metadata))).fetchone()[0]
            receipts.append((key,pgid))
    with frontier.connect() as c:
        c.executemany('INSERT OR REPLACE INTO experience_document_import VALUES(?,?)',receipts)
    return len(receipts)


def import_pending(frontier, limit=100):
    from psycopg.types.json import Jsonb
    with frontier.connect() as c:
        rows=c.execute('SELECT case_key,result FROM experience_result WHERE pg_imported=0 LIMIT ?',(limit,)).fetchall()
    if not rows:
        return 0
    with project.connect() as conn:
        imported=[]
        for key,encoded in rows:
            result=json.loads(encoded);doc=result['document']
            originals=result.get('documents') or [doc]
            texts={}
            db_ids={}
            for original in originals:
                raw=read_source_bytes(original['raw']['path'],storage=original['raw'].get('storage'))
                text=read_source_bytes(original['text']['path'],storage=original['text'].get('storage')).decode()
                if digest(raw)!=original['raw']['sha256'] or digest(text)!=original['text']['sha256'] or '\x00' in text:
                    raise ValueError('Original or text integrity failed during PostgreSQL import')
                texts[original['document_id']]=text
                metadata={'experience_scale':dict(original,full_text=text,evidence_roles=result['evidence_roles'])}
                docid=conn.execute('''INSERT INTO benefitlens.pipeline_document
                    (document_id,canonical_url,content_sha256,text_sha256,kind,raw_path,text_path,fetched_at,metadata)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(canonical_url_hash,content_sha256) DO UPDATE
                    SET metadata=benefitlens.pipeline_document.metadata || excluded.metadata RETURNING document_id''',
                    (original['document_id'],original['url'],original['raw']['sha256'],original['text']['sha256'],original['kind'],
                     original['raw']['path'],original['text']['path'],source_fetched_at(original['raw']),Jsonb(metadata))).fetchone()[0]
                db_ids[original['document_id']]=docid
            for claim in result['evidence']:
                text=texts[claim['document_id']]
                if text[claim['char_start']:claim['char_end']] != claim['exact_quote']:
                    raise ValueError('Case claim failed source-bound quote validation')
            # Existing snapshots of the same URL are one case even if page bytes changed.
            existing=conn.execute('''SELECT case_key FROM benefitlens.experience_discovery_case
                WHERE source_url=%s AND case_key!=%s AND COALESCE(payload->>'active','true')='true' LIMIT 1''',
                (doc['url'],key)).fetchone()
            if not existing:
                existing=conn.execute('''SELECT c.case_key FROM benefitlens.pipeline_document d
                    JOIN benefitlens.experience_discovery_case c ON (c.payload->'document_ids') ? d.document_id
                    WHERE d.text_sha256=ANY(%s) AND c.case_key!=%s
                    AND COALESCE(c.payload->>'active','true')='true' LIMIT 1''',
                    ([original['text']['sha256'] for original in originals],key)).fetchone()
            if existing:
                result.update(status='duplicate',duplicate_of=existing[0])
                # Preserve newly fetched originals and quotes when a legacy case wins.
                old_payload=conn.execute('SELECT payload FROM benefitlens.experience_discovery_case WHERE case_key=%s',
                    (existing[0],)).fetchone()[0]
                conn.execute('''UPDATE benefitlens.experience_discovery_case SET payload=payload || %s,updated_at=now()
                    WHERE case_key=%s''',(Jsonb(supporting_material_patch(old_payload,result,db_ids,key)),existing[0]))
                conn.execute('''UPDATE benefitlens.experience_discovery_case
                    SET evidence_status='merged_duplicate',payload=payload || %s,updated_at=now()
                    WHERE case_key=%s AND payload->>'source_family'=%s''',
                    (Jsonb({'active':False,'merged_into':existing[0]}),key,FAMILY))
                with frontier.connect() as c:
                    c.execute('DELETE FROM experience_result WHERE case_key=?',(key,))
                    c.execute('DELETE FROM experience_case_role WHERE case_key=?',(key,))
                    c.execute('UPDATE experience SET payload=? WHERE case_key=?',(json.dumps(result),key))
                    c.execute("""UPDATE experience_task SET state='duplicate',result=?
                        WHERE source=? AND json_extract(result,'$.case_key')=?""",
                        (json.dumps(result),doc['source_id'],key))
                continue
            payload={**result,'source_family':FAMILY,'document_ids':list(db_ids.values()),
                'collector_document_ids':list(db_ids),
                'evidence':[dict(e,document_id=db_ids[e['document_id']]) for e in result['evidence']],
                'active':True,'model_called':False,'collection_validation':'original_quote_scope_and_exact_duplicate_checked'}
            conn.execute('''INSERT INTO benefitlens.experience_discovery_case
                (case_key,program_id,person_name,application_round,outcome_claim,evidence_status,source_url,payload)
                VALUES(%s,%s,%s,%s,%s,'public_original_collected',%s,%s)
                ON CONFLICT(case_key) DO UPDATE SET payload=excluded.payload,
                evidence_status=excluded.evidence_status,updated_at=now()''',
                (key,result['program_id'],result.get('person_name'),result.get('cycle'),result['outcome'],doc['url'],Jsonb(payload)))
            imported.append(key)
    # PostgreSQL commit precedes the receipt. Replay is safe via the case key.
    with frontier.connect() as c:
        c.executemany('UPDATE experience_result SET pg_imported=1 WHERE case_key=?',[(key,) for key in imported])
    return len(rows)


def supporting_material_patch(old_payload, result, db_ids, key):
    claims=[dict(e,document_id=db_ids[e['document_id']]) for e in result['evidence']]
    evidence={json.dumps(e,sort_keys=True,ensure_ascii=False):e
              for e in old_payload.get('evidence',[])+claims}
    collections=dict(old_payload.get('supporting_collections',{}))
    collections[key]={'source_family':FAMILY,'collector_case_key':key,
        'document_ids':list(db_ids.values()),'evidence_roles':result['evidence_roles']}
    return {'document_ids':sorted(set(old_payload.get('document_ids',[])+list(db_ids.values()))),
            'evidence':list(evidence.values()),'supporting_collections':collections}


def export_model_inputs(frontier, root):
    """One episode per JSONL record, with every complete source document."""
    import gzip
    folder=root/'model-inputs'
    folder.mkdir(exist_ok=True)
    final=folder/'episodes-full-text.jsonl.gz'
    temporary=folder/'episodes-full-text.jsonl.gz.tmp'
    count=0
    with frontier.connect() as c,gzip.open(temporary,'wt',encoding='utf-8') as out:
        for (encoded,) in c.execute('SELECT result FROM experience_result WHERE pg_imported=1 ORDER BY case_key'):
            result=json.loads(encoded)
            documents=[]
            for doc in result.get('documents') or [result['document']]:
                text=read_source_bytes(doc['text']['path'],storage=doc['text'].get('storage')).decode()
                if digest(text)!=doc['text']['sha256']:
                    raise ValueError('Model input text hash mismatch')
                documents.append({'document_id':doc['document_id'],'url':doc['url'],
                    'text_sha256':doc['text']['sha256'],'full_text':text})
            out.write(json.dumps({'schema':'episode-full-text-v1','model_called':False,
                'case':{k:v for k,v in result.items() if k not in ('document','documents')},
                'instructions':['Read every full document; attribute claims to the cited person and episode.',
                    'Separate application documents, author reflections, interviews and participation reports.',
                    'Keep unknown outcomes and rounds unknown; never infer why selection occurred.',
                    'Every observation requires a source-bound exact quote; approved_pattern remains false.'],
                'documents':documents},ensure_ascii=False)+'\n')
            count+=1
    temporary.replace(final)
    manifest={'path':str(final),'case_records':count,'model_called':False,
              'sha256':digest(final.read_bytes()),'at_kst':datetime.now(KST).isoformat()}
    atomic_json(folder/'manifest.json',manifest)
    return manifest


def initialize(frontier, config):
    for source in config['sources']:
        if not source.get('enabled',True):
            continue
        for seed in source['seeds']:
            frontier.add(source['id'],seed,cohort='registered_seed')
        if source.get('bulk_archive_url'):
            frontier.add(source['id'],{'url':source['bulk_archive_url'],'kind':'github_archive'},cohort='bulk_transport')
            with frontier.connect() as c:
                c.execute("""UPDATE experience_task SET state='waiting_bulk'
                    WHERE source=? AND kind='detail' AND
                    (state IN ('pending','retry','policy_held') OR
                     state='needs_review' AND json_type(result,'$.document') IS NULL)
                    AND json_type(payload,'$.archive_original') IS NULL""",(source['id'],))
        manifest=source.get('prior_sitemap_manifest')
        if manifest:
            path=CACHE.parent/manifest
            if path.exists():
                for entry in json.loads(path.read_text())['sitemaps']:
                    frontier.prior_sitemap(source,entry)


def run(args):
    root=Path(args.root)
    root.mkdir(parents=True,exist_ok=True)
    import shutil
    if shutil.disk_usage(root).free < 2*1024**3:
        raise ValueError('Shared dataset free space below 2 GiB reserve')
    with single_writer(root):
        config=json.loads(Path(args.config).read_text())
        sources={s['id']:s for s in config['sources'] if s.get('enabled',True)}
        frontier=ExperienceFrontier(root)
        frontier.recover_interrupted()
        initialize(frontier,config)
        if args.reparse:
            frontier.reparse_unresolved()
        if getattr(args,'reparse_scope',False):
            print(json.dumps({'scope_originals_requeued':frontier.reparse_expanded_scope()}),flush=True)
        coverage=None;last_coverage=0
        if not args.no_postgres:
            from benefitlens_experience_coverage import database_coverage
            coverage=database_coverage(floor=args.target)
            args.target=coverage['target_experience_episodes']
            atomic_json(root/'coverage.json',coverage)
            last_coverage=time.time()
        run_id=str(uuid.uuid4())
        ledger=RunEvidence(root,run_id,args.max_requests)
        # A recorded rate-limit response is different from authentication denial.
        # Requeue only after the existing publisher hold expires, never before it.
        for host,until in ledger.rate_holds.items():
            with frontier.connect() as c:
                c.execute("""UPDATE experience_task SET state='retry',next_at=?
                    WHERE url LIKE ? AND (state='policy_held' OR state='retry' AND
                      (error LIKE '%429%' OR error LIKE '%rate limit%'))""",(until,'https://'+host+'/%'))
        stop=threading.Event()
        signal.signal(signal.SIGTERM,lambda *_:stop.set())
        started=time.time();processed=0
        state={'run_id':run_id,'started_at':datetime.now(KST).isoformat(),
               'state':'running','target_cases':args.target,'request_budget_per_run':args.max_requests,
               'parser_version':VERSION,'model_called':False,'approved_patterns_added':0}
        if coverage:
            state['experience_coverage']=coverage
        reason=''
        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures={};last_checkpoint=0
                while True:
                    if not reason:
                        if stop.is_set(): reason='requested_checkpoint'
                        elif processed>=args.max_items: reason='batch_limit'
                        elif ledger.network_requests>=args.max_requests: reason='request_budget'
                        elif (coverage['counted_experience_episodes'] if coverage else
                              frontier.snapshot()['accepted_cases'])>=args.target:
                            reason='target_reached' if coverage is None else 'global_target_reached_linkage_pending'
                    if not reason:
                        frontier.defer_rate_hosts(ledger.rate_holds)
                        busy=[urlsplit(t['url']).hostname for t in futures.values() if not any(
                              t.get(k) for k in ('archive_original','batch_original','feed_original','reuse_document'))]
                        capacity=min(args.workers-len(futures),args.max_items-processed-len(futures))
                        tasks=frontier.lease(run_id,capacity,busy_hosts=busy) if capacity>0 else []
                        futures.update({pool.submit(process,t,sources[t['source']],ledger):t for t in tasks})
                    if not futures and reason: break
                    if not futures:
                        due=frontier.next_ready_at()
                        if due is None:
                            reason='queue_exhausted_with_unresolved';break
                        state.update(at_kst=datetime.now(KST).isoformat(),state='waiting_for_provider',
                            next_resume_at=datetime.fromtimestamp(max(time.time(),due),KST).isoformat(),
                            **frontier.snapshot())
                        atomic_json(root/'status.json',state)
                        stop.wait(min(30,max(1,due-time.time())))
                        continue
                    completed,_=wait(futures,timeout=1,return_when=FIRST_COMPLETED)
                    for future in completed:
                        task=futures.pop(future)
                        result,children=future.result()
                        frontier.finish(task,result,children=children)
                        processed+=1
                    if not completed or time.time()-last_checkpoint<5 and futures:
                        continue
                    last_checkpoint=time.time()
                    db_error=None
                    if not args.no_postgres:
                        try:
                            import_originals(frontier)
                            import_pending(frontier)
                        except Exception as error: db_error=type(error).__name__+': '+str(error)[:300]
                    if coverage and time.time()-last_coverage>=60:
                        try:
                            coverage=database_coverage(floor=args.target)
                            args.target=coverage['target_experience_episodes']
                            state.update(experience_coverage=coverage,target_cases=args.target)
                            atomic_json(root/'coverage.json',coverage)
                            last_coverage=time.time()
                        except Exception as error:
                            db_error='coverage: '+type(error).__name__+': '+str(error)[:300]
                    snap=frontier.snapshot()
                    accepted=snap['accepted_cases']
                    stage=next((n for n in [1000,5000,10000,50000] if accepted<n),50000)
                    state.update(at_kst=datetime.now(KST).isoformat(),state='running',processed_this_run=processed,
                        network_requests_this_run=ledger.network_requests,
                        network_requests_before_run=ledger.lifetime_requests_at_start,
                        next_milestone=stage,elapsed_seconds=round(time.time()-started,1),
                        postgres_error=db_error,**snap)
                    state['provider_backoff_until']={host:datetime.fromtimestamp(until,KST).isoformat()
                        for host,until in ledger.rate_holds.items() if until>time.time()}
                    atomic_json(root/'status.json',state)
                    print(json.dumps({k:state[k] for k in ('at_kst','processed_this_run','accepted_cases',
                        'postgres_imported_cases','network_requests_this_run')},ensure_ascii=False),flush=True)
            if not args.no_postgres:
                while import_originals(frontier): pass
                while import_pending(frontier): pass
                state['full_text_model_input']=export_model_inputs(frontier,root)
                coverage=database_coverage(floor=args.target)
                state['experience_coverage']=coverage
                atomic_json(root/'coverage.json',coverage)
            state.update(frontier.snapshot(),state='complete' if reason=='target_reached' else 'checkpoint',
                         stop_reason=reason,at_kst=datetime.now(KST).isoformat(),processed_this_run=processed,
                         network_requests_this_run=ledger.network_requests)
        finally:
            state.setdefault('stop_reason','interrupted_or_failed')
            if state.get('state') in ('running','waiting_for_provider'): state['state']='failed'
            atomic_json(root/'status.json',state)
            with frontier.connect() as c:
                c.execute('INSERT OR REPLACE INTO experience_run VALUES(?,?)',(run_id,json.dumps(state)))
        print(json.dumps(state,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default=str(ROOT))
    parser.add_argument('--config',default=str(CONFIG))
    parser.add_argument('--target',type=int,default=50000)
    parser.add_argument('--max-requests',type=int,default=100000)
    parser.add_argument('--max-items',type=int,default=100000)
    parser.add_argument('--workers',type=int,default=6)
    parser.add_argument('--no-postgres',action='store_true')
    parser.add_argument('--reparse',action='store_true',help='Reparse unresolved complete originals without new downloads')
    parser.add_argument('--reparse-scope',action='store_true',help='Revisit stored education-apprenticeship originals under expanded scope')
    run(parser.parse_args())
