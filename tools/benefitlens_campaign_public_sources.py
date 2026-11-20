"""Official European catalogue adapters; use original evidence gates for every detail."""
from datetime import datetime
from hashlib import sha256
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
from benefitlens_campaign_adapters import listing_jobs as base_list, parse_detail as base_detail
from benefitlens_source_storage import read_source_bytes
from build_benefitlens_live_batch import verify_posting
from expand_benefitlens_euraxess import parse_notice
from expand_benefitlens_pass import parse_record

DOCTORAL=re.compile(r'\bph\.?\s?d\.?\b|\bdoctoral\b|doctorant|pre.?doc|studentship|master.*(?:student|fellow)',re.I)
POSTDOC=re.compile(r'post[ -]?doc|assistant professor|first level doctorate researcher',re.I)


def same_path(s, base, href, path):
    u=urljoin(base,href);v=urlsplit(u)
    if v.scheme=='https' and v.hostname in s['allowed_hosts'] and not v.username and not v.password and v.port in (None,443) and re.fullmatch(path,v.path):
        return v._replace(fragment='').geturl()
    return None


def listing_jobs(source, cap, payload):
    kind=source['adapter']
    if kind=='jobbnorge':
        from benefitlens_campaign_jobbnorge import listing_jobs as jobbnorge_list
        return jobbnorge_list(source,cap,payload)
    if kind not in ('euraxess','fr_pass'): return base_list(source,cap,payload)
    if cap.get('status',cap.get('http_status'))!=200:raise ValueError('Listing is not an HTTP 200 capture')
    raw=read_source_bytes(cap['raw_path'],storage=cap.get('raw_storage'))
    if sha256(raw).hexdigest()!=cap['raw_sha256']:raise ValueError('Catalogue original hash mismatch')
    by_url={};pages=[]
    if kind=='euraxess':
        soup=BeautifulSoup(raw,'lxml')
        if not soup.select_one('main') or not soup.title or 'EURAXESS' not in soup.title.get_text(' ',strip=True):
            raise ValueError('Missing public EURAXESS catalogue')
        for a in soup.select('main h3 a[href]'):
            title=a.get_text(' ',strip=True);u=same_path(source,cap['url'],a['href'],r'/jobs/\d+')
            if not u or not DOCTORAL.search(title) or POSTDOC.search(title):continue
            u=urlsplit(u)._replace(query='').geturl();native=u.rsplit('/',1)[1]
            by_url[u]={'url':u,'native_id':native,'issuer':source['issuer'],'title_hint':title,'listing':cap,'listing_target_url':a['href']}
        for a in soup.select('.pager a[href],.ecl-pagination a[href],a[rel=next][href]'):
            if 'next' not in a.get('rel',[]) and not re.fullmatch('next',a.get_text(' ',strip=True),re.I):continue
            u=same_path(source,cap['url'],a['href'],r'/jobs/search')
            if u and u!=cap['url']:pages.append({'url':u,'method':'GET'});break
    else:
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('Unsafe RSS declaration')
        try:root=ET.fromstring(raw)
        except ET.ParseError as e:raise ValueError('Invalid official RSS XML') from e
        if root.tag!='rss' or root.find('channel') is None:raise ValueError('Expected official PASS RSS channel')
        for item in root.findall('./channel/item'):
            link=(item.findtext('link') or '').strip();title=(item.findtext('title') or '').strip()
            u=same_path(source,cap['url'],link,r'/offre/[^/?#]+') if link else None
            if not u:continue
            u=urlsplit(u)._replace(query='').geturl()
            by_url[u]={'url':u,'native_id':urlsplit(u).path,'issuer':source['issuer'],'title_hint':title or urlsplit(u).path,'listing':cap,'listing_target_url':link}
    return list(by_url.values()),pages


def parse_detail(source,cap,candidate,as_of,blob_dir):
    kind=source['adapter']
    if kind not in ('euraxess','fr_pass'):return base_detail(source,cap,candidate,as_of,blob_dir)
    if cap.get('status',cap.get('http_status'))!=200 or cap['url']!=candidate['url']:raise ValueError('Official detail identity or HTTP status mismatch')
    if kind=='fr_pass':
        row=parse_record(cap,as_of);row['listing_source']=candidate['listing'];row['listing_item_url']=candidate['url']
        row['opportunity_type']='internship' if row['source_fields']['contract_type']=='Stage' else 'other'
    else:
        orig=parse_notice({'id':candidate['native_id'],'title':candidate['title_hint'],'official_url':candidate['url'],'capture':cap},blob_dir,as_of)
        captured=orig.pop('capture');row={**captured,**orig,'source_family':'EURAXESS','source_id':'EURAXESS',
            'native_id':candidate['native_id'],'status':'open','opportunity_type':'other',
            'open_basis':'Explicit future EURAXESS application deadline with exact cutoff, current listing and public application route',
            'listing_source':candidate['listing'],'listing_item_url':candidate['url']}
        row['application_period']={'kind':'fixed_deadline','verification_state':'verified','source':captured,
            'quote':row['deadline_quote'],'locator':row['deadline_locator'],'opens_on':None,'closes_on':row['closes_on'],
            'deadline_at':row['deadline_at'],'semantics':'explicit_application_deadline',
            'context_evidence':[{'quote':row['status_quote'],'locator':row['status_locator']}]}
    verify_posting(row,as_of,{})
    row['semantic_review_state']='automated_rules_only_not_independent_review'
    return row,None
