"""Preserved university-linked Varbi doctoral advertisements, no HTTP side effects."""
from datetime import date
from hashlib import sha256
from urllib.parse import urlsplit,unquote
import re
from bs4 import BeautifulSoup
from benefitlens_source_storage import read_source_bytes,read_source_text
from benefitlens_application_period import extract_dates,verify_application_period


def exact_locator(text,q):
 if not q or text.count(q)!=1:raise ValueError('Evidence is absent or ambiguous')
 a=text.index(q);return f'L{text[:a].count(chr(10))+1}-L{text[:a+len(q)].count(chr(10))+1}'


def doctoral_scope(title,text):
 role=r'^(?:doctoral\s+(?:\(PhD\)\s+)?(?:student|position|candidate)|doctoral\s+\(Licentiate\)\s+in|PhD\s+(?:student|position|candidate|in\b))'
 if not re.search(role,title,re.I) or re.search(r'\b(?:professor|coordinator|administrator|secretary)\b',title,re.I):raise ValueError('Actual advertised role is not doctoral student recruitment')
 patterns=[r'four years of full-time doctoral education is required',r'you (?:are|will be) both admitted as a student and employed',r'Only those admitted to (?:doctoral|third-cycle|postgraduate) (?:studies|education)[^.\n]{0,100}',r'(?:will|must) be (?:admitted|enrolled)[^.\n]{0,100}(?:doctoral|PhD)[^.\n]{0,80}',r'devote (?:yourself|(?:your|their) time)[^.\n]{0,70}(?:doctoral (?:education|programme|studies))',r'you will primarily undertake doctoral studies',r'To meet the general entry requirements for doctoral studies, you must:']
 for pattern in patterns:
  matches=list(re.finditer(pattern,text,re.I))
  if len(matches)==1:
   q=matches[0][0]
   if len(q.split())<=17:return q
 raise ValueError('Explicit doctoral training or admission scope not parsed')


def parse_record(cap,candidate,as_of,verify=True):
 if cap['status']!=200:raise ValueError('Public detail HTTP failed')
 u=urlsplit(cap['url']);is_kth=u.hostname=='kth.se' and bool(re.fullmatch(r'/lediga-jobb/\d+',u.path))
 m=re.search(r'jobID:(\d+)',unquote(u.path)) if not is_kth else re.search(r'/lediga-jobb/(\d+)',u.path)
 if u.scheme!='https' or not (is_kth or re.fullmatch(r'[a-z0-9-]+\.varbi\.com',u.hostname or '')) or not m:raise ValueError('Wrong official recruitment detail route')
 native=m[1]
 if native!=candidate['native_id'] or cap['url']!=candidate['url']:raise ValueError('Candidate/detail native URL mismatch')
 listing=candidate['listing'];lraw=read_source_bytes(listing['raw_path'],storage=listing.get('raw_storage'))
 if sha256(lraw).hexdigest()!=listing['raw_sha256']:raise ValueError('Listing hash mismatch')
 links={a['href'] for a in BeautifulSoup(lraw,'lxml').select('a[href]')}
 if candidate.get('listing_target_url',cap['url']) not in links:raise ValueError('Actual university listing does not link this detail')
 raw=read_source_bytes(cap['raw_path'],storage=cap.get('raw_storage'));text=read_source_text(cap['text_path'],storage=cap.get('text_storage'))
 if sha256(raw).hexdigest()!=cap['raw_sha256'] or sha256(text.encode()).hexdigest()!=cap['text_sha256']:raise ValueError('Immutable source mismatch')
 soup=BeautifulSoup(raw,'lxml');title=soup.h1.get_text(' ',strip=True) if soup.h1 else ''
 scope=doctoral_scope(title,text);table={}
 for tr in soup.select('tr'):
  th=tr.find('th');td=tr.find('td')
  if th and td:table[th.get_text(' ',strip=True)]=td.get_text(' ',strip=True)
 for node in soup.select('.details-list'):
  label=node.find('b');value=node.find('p')
  if label and value:table[label.get_text(' ',strip=True)]=value.get_text(' ',strip=True)
 deadline=table.get('Last application date')
 if not deadline:raise ValueError('Last application date field absent')
 dates=extract_dates(deadline)
 if len(dates)!=1:raise ValueError('Application deadline ambiguous')
 closes=next(iter(dates))
 if closes<=as_of:raise ValueError('Deadline passed or same-day cutoff unknown')
 q='Last application date\n'+deadline;loc=exact_locator(text,q)
 apps=[a['href'] for a in soup.select('a[href]') if a.get_text(' ',strip=True) in ['Login and apply','Apply here']]
 apps=list(dict.fromkeys(apps))
 if len(apps)!=1:raise ValueError('Actual Login and apply route absent or ambiguous')
 app=apps[0];au=urlsplit(app)
 if au.scheme!='https' or not ((is_kth and au.hostname=='kth.varbi.com' and au.path==f'/en/apply/positionquick/{native}/') or (not is_kth and au.hostname==u.hostname and re.search(r'jobID:'+re.escape(native)+r'(?:/|$)',unquote(au.path)) and 'what:login' in unquote(au.path))):raise ValueError('Login application/native mismatch')
 label='Apply here' if is_kth else 'Login and apply'
 source={k:cap[k] for k in ['url','fetched_at','raw_path','raw_sha256','text_path','text_sha256','raw_storage','text_storage']};source['request_url']=cap['url']
 period={'kind':'fixed_deadline','verification_state':'verified','semantics':'explicit_application_deadline','source':source,'quote':q,'locator':loc,'opens_on':None,'closes_on':closes.isoformat(),'deadline_at':None,'context_evidence':[]}
 row={'source_family':'SE_VARBI_UNIVERSITY','source_id':'varbi/'+('kth' if is_kth else u.hostname.split('.')[0]),'id':native,'native_id':native,'title':title,'issuer':candidate['issuer'],'official_url':cap['url'],'application_url':app,'request_url':cap['url'],'fetched_at':cap['fetched_at'],**{k:cap[k] for k in ['raw_path','raw_sha256','raw_storage','text_path','text_sha256','text_storage']},'status':'open','open_basis':'University-linked doctoral recruitment with explicit future application deadline and matching actual login/apply route','opens_on':None,'closes_on':closes.isoformat(),'application_period':period,'deadline_quote':q,'deadline_locator':loc,'student_quote':scope,'student_locator':exact_locator(text,scope),'student_scope_context':'Doctoral training or admission attached to this recruited student role; personal entry requirements unresolved','status_quote':q,'status_locator':loc,'country':None,'location':table.get('City'),'listing_raw_path':listing['raw_path'],'listing_raw_sha256':listing['raw_sha256'],'listing_fetched_at':listing['fetched_at'],'listing_url':listing['url'],'source_fields':{'title':title,'native_id':native,'entity':candidate['issuer'],'university_listing':listing,'listing_target_url':candidate.get('listing_target_url',cap['url']),'language_representation':candidate.get('language_representation'),'language_selector_ref':candidate.get('language_selector_ref'),'contract_type':table.get('Type of employment'),'all_official_fields':table,'activity_country_raw':table.get('Country'),'application_route':{'method':'online','access_status':'login_required' if label=='Login and apply' else 'unverified','application_url':app,'actual_href':app,'instructions_quote':label,'instructions_locator':f'L{text[:text.index(label)].count(chr(10))+1}-L{text[:text.index(label)].count(chr(10))+1}','internal_fields_status':'unverified'},'opportunity_type_normalization':'held_other'}}
 # Repeated labels are retained as route metadata, not fabricated unique evidence.
 if verify:verify_application_period(row,as_of,{})
 return row
