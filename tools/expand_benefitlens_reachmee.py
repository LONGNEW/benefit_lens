"""Official Gothenburg doctoral advertisements from preserved public ReachMee HTML."""
from urllib.parse import urlsplit,parse_qs
from hashlib import sha256
from datetime import date
import re
from bs4 import BeautifulSoup
from benefitlens_source_storage import read_source_bytes,read_source_text
from benefitlens_application_period import extract_dates,verify_application_period
from expand_benefitlens_varbi import exact_locator


def doctoral_scope(title,text):
 role=r'^(?:Doctoral\s+student|(?:Up to\s+\w+\s+)?PhD\s+Positions?)\b'
 if not re.search(role,title,re.I) or re.search(r'\b(?:professor|coordinator|administrator|secretary)\b',title,re.I):raise ValueError('Not an actual recruited doctoral student role')
 patterns=[r'program leads to a PhD in [\w ]{1,35}(?= and|\.)',r'To be eligible for admission to doctoral studies',r'you have been admitted to doctoral studies, you will be employed as a doctoral student',r'apply to be admitted for education at third-cycle level',r'admitted to (?:third-cycle|doctoral) (?:education|studies)']
 for p in patterns:
  ms=list(re.finditer(p,text,re.I))
  if len(ms)==1 and len(ms[0][0].split())<=17:return ms[0][0]
 raise ValueError('Explicit degree admission or training scope unparsed')


def parse_record(cap,candidate,as_of,verify=True):
 if cap['status']!=200:raise ValueError('Public source HTTP failed')
 u=urlsplit(cap['url']);q=parse_qs(u.query);native=candidate['native_id']
 if u.scheme!='https' or u.hostname!='web103.reachmee.com' or u.path!='/ext/I005/1035/main' or q.get('rmjob')!=[native] or q.get('rmpage')!=['job'] or cap['url']!=candidate['url']:raise ValueError('Wrong official tenant, native or detail route')
 listing=candidate['listing']
 if listing['url']!='https://www.gu.se/en/work-at-the-university-of-gothenburg/vacancies':raise ValueError('Wrong official university listing origin')
 lr=read_source_bytes(listing['raw_path'],storage=listing.get('raw_storage'))
 if sha256(lr).hexdigest()!=listing['raw_sha256'] or cap['url'] not in {a['href'] for a in BeautifulSoup(lr,'lxml').select('a[href]')}:raise ValueError('Official university listing binding failed')
 raw=read_source_bytes(cap['raw_path'],storage=cap.get('raw_storage'));text=read_source_text(cap['text_path'],storage=cap.get('text_storage'))
 if sha256(raw).hexdigest()!=cap['raw_sha256'] or sha256(text.encode()).hexdigest()!=cap['text_sha256']:raise ValueError('Immutable capture hash mismatch')
 soup=BeautifulSoup(raw,'lxml');title=soup.h1.get_text(' ',strip=True) if soup.h1 else ''
 if re.search('collaborative doctoral students',title,re.I):raise ValueError('Externally funded collaboration admission; funding opportunity scope unresolved')
 scope=doctoral_scope(title,text)
 periods=[]
 for p in [r'Closing date:\s*\n[^\n]+',r'Applications must be received by[:\s]+[^\n.]+',r'The application\s+must be received no later than [^\n.]+']:
  for m in re.finditer(p,text,re.I):
   dates=extract_dates(m[0])
   if len(dates)==1:periods.append((m[0],next(iter(dates))))
 if not periods:raise ValueError('Actual application deadline unpublished or unparsed')
 if len({d for _,d in periods})!=1:raise ValueError('Explicit application deadlines conflict')
 quote,closing=periods[0]
 if closing<=as_of:raise ValueError('Application deadline passed or same-day cutoff unknown')
 links=[a['href'] for a in soup.select('a[href]') if a.get_text(' ',strip=True)=='Apply'];links=list(dict.fromkeys(links))
 if len(links)!=1:raise ValueError('Actual Apply path absent or ambiguous')
 app=links[0];ap=urlsplit(app)
 if ap.scheme!='https' or ap.hostname!=u.hostname or ap.path!='/ext/I005/1035/apply' or parse_qs(ap.query).get('job_id')!=[native]:raise ValueError('Actual Apply native mismatch')
 source={k:cap[k] for k in ['url','fetched_at','raw_path','raw_sha256','raw_storage','text_path','text_sha256','text_storage']};source['request_url']=cap['url']
 period={'kind':'fixed_deadline','verification_state':'verified','semantics':'explicit_application_deadline','source':source,'quote':quote,'locator':exact_locator(text,quote),'opens_on':None,'closes_on':closing.isoformat(),'deadline_at':None,'context_evidence':[]};aloc=exact_locator(text,'Apply')
 row={'source_family':'SE_GOTHENBURG_REACHMEE','source_id':'reachmee/gu/1035','id':native,'native_id':native,'title':title,'issuer':candidate['issuer'],'official_url':cap['url'],'application_url':app,'request_url':cap['url'],'fetched_at':cap['fetched_at'],**{k:cap[k] for k in ['raw_path','raw_sha256','raw_storage','text_path','text_sha256','text_storage']},'status':'open','open_basis':'Official university-linked doctoral recruitment, explicit future application deadline and same-native actual Apply route','opens_on':None,'closes_on':closing.isoformat(),'application_period':period,'deadline_quote':quote,'deadline_locator':period['locator'],'student_quote':scope,'student_locator':exact_locator(text,scope),'student_scope_context':'Doctoral admission/training role; complete personal entry criteria unresolved','status_quote':quote,'status_locator':period['locator'],'country':None,'location':None,'listing_raw_path':listing['raw_path'],'listing_raw_sha256':listing['raw_sha256'],'listing_fetched_at':listing['fetched_at'],'listing_url':listing['url'],'source_fields':{'title':title,'native_id':native,'entity':candidate['issuer'],'university_listing':listing,'opportunity_type_normalization':'held_other','application_route':{'method':'online','access_status':'unverified','application_url':app,'actual_href':app,'instructions_quote':'Apply','instructions_locator':aloc,'internal_fields_status':'unverified'}}}
 if verify:verify_application_period(row,as_of,{})
 return row
