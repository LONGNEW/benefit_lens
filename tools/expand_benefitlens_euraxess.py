"""Read-only EURAXESS notice interpretation for bounded public captures.

PhD titles, R1 or an Apply link alone do not establish a current student call.
Original documents, published authority and conditional UI state stay separate.
"""
from datetime import datetime,timezone,date
from hashlib import sha256
from pathlib import Path
from urllib.parse import urljoin,urlsplit
import re
from bs4 import BeautifulSoup
from benefitlens_application_period import extract_dates
from benefitlens_source_storage import read_source_bytes,store_source_bytes


def visible_projection(raw,title):
    soup=BeautifulSoup(raw,'lxml');main=soup.select_one('main') or soup.body
    for e in list(main.select('script,style,noscript,[hidden],[aria-hidden="true"]')):
        e.decompose()
    for e in list(main.select('[style]')):
        if re.search(r'display\s*:\s*none|visibility\s*:\s*hidden',e.get('style',''),re.I):e.decompose()
    return title+'\n'+main.get_text('\n',strip=True)+'\n'


def label_values(soup):
    heading=soup.select_one('#job-information')
    if heading is None:raise ValueError('Official information block absent')
    dl=heading.find_next('dl');out={}
    for dt in dl.find_all('dt',recursive=False):
        dd=dt.find_next_sibling('dd')
        if dd:out[dt.get_text(' ',strip=True)]=dd.get_text(' ',strip=True)
    return out,dl


def exact_locator(text,quote):
    if not quote or text.count(quote)!=1:raise ValueError('Evidence is absent or ambiguous')
    start=text.index(quote);a=text[:start].count('\n')+1
    return f'L{a}-L{a+quote.count(chr(10))}'


def student_scope(text):
    # The matched scope must occur below the metadata and contain a degree path,
    # not a negated doctorate exclusion or a generic R1 field.
    content=text[text.find('Offer Description',text.find('Job Information')+15):]
    patterns=[r'(?:enrol\w*|register\w*|admi\w*|pursu\w*)[^.\n]{0,95}(?:Ph\.?\s?D\.?|doctoral|doctorate|third.cycle)[^.\n]{0,55}',
        r'(?:Ph\.?\s?D\.?|doctoral)\s+(?:students?|candidates?|studies|programme|program|training)\b[^.\n]{0,90}',
        r'employment as a doctoral student[^.\n]{0,80}',
        r'PhD thesis in[^.\n]{0,80}',r'Throughout the PhD[^.\n]{0,80}',
        r'(?:obtain|complete|undertake|work towards|leading to)[^.\n]{0,90}(?:Ph\.?\s?D\.?|doctoral degree)[^.\n]{0,40}']
    for pattern in patterns:
        for m in re.finditer(pattern,content,re.I):
            context=content[max(0,m.start()-45):m.end()]
            if re.search(r'post.?doc|not (?:hold|have|already|be)|already (?:hold|have)|must not|cannot|no doctoral',context,re.I):continue
            q=m[0].strip()
            if len(q.split())<2 or text.count(q)!=1:continue
            return q,exact_locator(text,q)
    raise ValueError('No unambiguous positive doctoral degree/training/student scope in body')


def parse_notice(candidate,cache_dir,as_of):
    cap=candidate['capture'];raw=read_source_bytes(cap['raw_path'],storage=cap.get('raw_storage'))
    if sha256(raw).hexdigest()!=cap['raw_sha256']:raise ValueError('Raw snapshot hash changed')
    soup=BeautifulSoup(raw,'lxml');info,dl=label_values(soup)
    title=soup.select_one('h1.ecl-content-block__title')
    title=title.get_text(' ',strip=True) if title else candidate['title']
    if re.search(r'post.?doc',title,re.I):raise ValueError('Postdoctoral role excluded')
    deadline_term=next((d for d in dl.select('dt') if d.get_text(' ',strip=True)=='Application Deadline'),None)
    time=deadline_term.find_next_sibling('dd').select_one('time[datetime]') if deadline_term else None
    if time is None:raise ValueError('Explicit official application deadline absent')
    dt=datetime.fromisoformat(time['datetime'].replace('Z','+00:00'))
    fetched=datetime.fromisoformat(cap['fetched_at'])
    if dt.tzinfo is None or dt<=fetched:raise ValueError('Application cutoff passed or timezone missing')
    printed=info.get('Application Deadline','');dates=extract_dates(printed)
    if len(dates)!=1:raise ValueError('Missing/ambiguous printed application date')
    closing=next(iter(dates))
    if closing<as_of:raise ValueError('Application deadline passed')
    expiry=soup.select_one('#expiration')
    exp={'hidden':bool(expiry and re.search(r'display\s*:\s*none',expiry.get('style',''),re.I)),
         'timestamp':expiry.get('data-date-time') if expiry else None}
    if exp['timestamp'] and abs(float(exp['timestamp'])-dt.timestamp())>1:raise ValueError('Conditional expiry and official deadline conflict')
    if expiry and not exp['hidden'] and 'EXPIRED' in expiry.get_text():raise ValueError('Visible expired status')
    text=visible_projection(raw,title)
    body_scope,loc=student_scope(text)
    # Scope, not a claim that all qualifications have been met by a particular user.
    where=soup.select_one('#where-to-apply');section=where.find_next_sibling() if where else None
    if not where:raise ValueError('Application instruction anchor absent')
    links=[]
    for a in soup.select('a[href]'):
        if a.get_text(' ',strip=True).startswith('Apply now'):
            links.append(urljoin(candidate['official_url'],a['href']))
    application=next((u for u in links if u.startswith(('http://','https://'))),None)
    email=next((u[7:].split('?')[0] for u in links if u.startswith('mailto:')),None)
    if not application:
        for a in where.find_next('dl').select('a[href]') if where.find_next('dl') else []:
            u=urljoin(candidate['official_url'],a['href'])
            if u.startswith(('http://','https://')):application=u;break
            if u.startswith('mailto:'):email=u[7:].split('?')[0]
    if not application:
        # The actual named section contains the email address as source text.
        block=where.find_next('dl')
        if not email and block:
            match=re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',block.get_text(' ',strip=True))
            if match:email=match[0]
        if not email:raise ValueError('Actual public application route absent')
        application=candidate['official_url']+'#where-to-apply'
    # A declared earlier offer start is a potential stale call, despite a future UI date.
    starts=extract_dates(info.get('Offer Starting Date',''))
    if starts and max(starts)<as_of:raise ValueError('Past mandatory offer starting date: semantic review held')
    ref=store_source_bytes(text.encode(),Path(cache_dir)/'visible-blobs',suffix='.txt')
    cap={k:v for k,v in cap.items() if k!='links'}|{'text_path':ref['path'],'text_sha256':ref['sha256'],'text_storage':ref['storage']}
    deadline_quote=next(m[0] for m in re.finditer(r'\d{1,2} [A-Za-z]+ 20\d{2}',printed))
    # A short exact body anchor carries a wider LF context for semantic review.
    tokens=list(re.finditer(r'\S+',body_scope))
    anchor=body_scope[:tokens[min(6,len(tokens)-1)].end()]
    return {**candidate,'capture':cap,'title':title,'issuer':info['Organisation/Company'],'country':info.get('Country'),'country_role':'activity_location','location':None,
        'student_scope_context':body_scope,'student_quote':anchor,'student_locator':loc,
        'status_quote':'Application Deadline','status_locator':f'L{text[:text.index(printed)].count(chr(10))}',
        'deadline_quote':deadline_quote,'deadline_locator':exact_locator(text,printed),'deadline_at':dt.isoformat(),'closes_on':closing.isoformat(),'opens_on':None,
        'application_url':application,'application_method':'email' if application.endswith('#where-to-apply') and email else 'external_portal',
        'application_email':email,'application_instructions_url':candidate['official_url']+'#where-to-apply',
        'application_anchor_verified':True,'conditional_expiry':exp,'source_fields':info,
        'source_publisher_type':'official_EC_catalog_employer_notice','direct_issuer_status_rechecked':False}
