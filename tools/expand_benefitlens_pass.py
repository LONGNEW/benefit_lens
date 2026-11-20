"""Strict current French public-sector student notices from preserved public HTML."""
from pathlib import Path
from hashlib import sha256
from datetime import date
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
import re
from benefitlens_application_period import extract_dates,verify_application_period
from benefitlens_source_storage import read_source_bytes,read_source_text

HOST='www.pass.fonction-publique.gouv.fr'
DATE=r'(?:\d{1,2}[/.]\d{1,2}[/.]20\d{2}|\d{1,2}\s+(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre)\s+20\d{2})'

def locator(text,quote):
 if not quote or text.count(quote)!=1:raise ValueError('Evidence absent or ambiguous')
 n=text[:text.index(quote)].count('\n')+1
 return f'L{n}-L{n+quote.count(chr(10))}'

def field(soup,key):
 node=soup.select_one('.field--name-field-'+key)
 if node is None:return ''
 label=node.select_one('.field__label')
 if label:label.decompose()
 return node.get_text('\n',strip=True)

def application_period(text,source,as_of):
 parts=[]
 for m in re.finditer(r'date limite[^\n]{0,90}|(?:date[^\n]{0,20})?candidatures[^\n]{0,60}(?:fix[ée]e?|avant)|avant le',text,re.I):
  window=text[m.start():m.start()+260]
  rolling=re.match(r'Date limite de candidature\s*:\s*Offre ouverte en continu',window,re.I)
  if rolling:
   parts.append({'kind':'explicit_rolling','verification_state':'verified','semantics':'explicit_application_rolling','source':source,'quote':rolling[0],'locator':locator(text,rolling[0]),'opens_on':None,'closes_on':None,'deadline_at':None,'context_evidence':[]});continue
  dm=re.search(DATE,window,re.I)
  if not dm:continue
  q=window[:dm.end()];dates=extract_dates(q)
  if not dates:raise ValueError('Ambiguous numeric date requires explicit locale attestation')
  if len(dates)!=1:raise ValueError('Conflicting dates in application deadline clause')
  closing=next(iter(dates))
  if closing<=as_of:raise ValueError('Application deadline passed or same-day cutoff unknown')
  context=[]
  if q.casefold().startswith('avant le'):
   st=text.rfind('\n',0,m.start());prior=text[max(0,st-250):m.start()]
   cm=re.search(r'Les candidatures[^\n]{0,200}',prior,re.I)
   if not cm:raise ValueError('Before-date lacks actual application context')
   cq=cm[0].split(' (',1)[0];context=[{'quote':cq,'locator':locator(text,cq)}]
  parts.append({'kind':'fixed_deadline','verification_state':'verified','semantics':'explicit_application_deadline','source':source,'quote':q,'locator':locator(text,q),'opens_on':None,'closes_on':closing.isoformat(),'deadline_at':None,'context_evidence':context})
 if not parts:raise ValueError('Actual application period unpublished or unparsed')
 keys={(x['kind'],x['closes_on']) for x in parts}
 if len(keys)!=1:raise ValueError('Conflicting explicit application periods')
 return parts[0]


def first_locator(text,q):
 if not q or q not in text:raise ValueError('Literal route absent')
 n=text[:text.index(q)].count('\n')+1
 return f'L{n}-L{n+q.count(chr(10))}'


def application_route(soup,text,official_url):
 fields=soup.select('.field--name-field-informations-complementair,.field--name-field-commentaires,.field--name-field-descriptif-du-profil-reche,.field--name-field-description-du-poste')
 blocks=[x for x in fields if re.search(r'candidatur|postuler|candidater|postulez|dépos|envo[yi]|adress[ée]',x.get_text(' ',strip=True),re.I)]
 for node in blocks:
  nt=node.get_text(' ',strip=True)
  if not re.search(r'(?:candidat|postul|dépos)',nt,re.I):continue
  urls=[a['href'] for a in node.select('a[href^="https://"]')]+re.findall(r'https://[^\s<>]+',nt)
  for u in dict.fromkeys(urls):
   u=u.rstrip('.,;')
   if re.search(r'apply|candid|recrut|demarche|demarches|simplifie',u,re.I) and u in text:
    return {'application_url':u,'method':'online','link_presentation':'anchor' if node.select_one('a[href="'+u+'"]') else 'literal_visible_url','actual_href':u,'instructions_quote':u,'instructions_locator':first_locator(text,u)}
 for node in blocks:
  nt=node.get_text(' ',strip=True)
  submit=re.search(r'(?:candidatur[\s\S]{0,320}(?:adress|envo|transmett)|(?:adress|envo|transmett)[\s\S]{0,320}(?:candidatur|curriculum|CV|lettre)|pour postuler|courriel de candidature)',nt,re.I)
  if not submit:continue
  mails=re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',nt)
  if not mails and re.search(r'courriel de candidature',nt,re.I):
   mails=[a['href'][7:].split('?')[0] for a in soup.select('.field--name-field-contact-email a[href^="mailto:"]')]
  mails=list(dict.fromkeys(mails))
  if len(mails)!=1:continue
  mail=mails[0]
  anchors=[a['href'] for a in soup.select('a[href^="mailto:"]') if a['href'][7:].split('?')[0]==mail]
  if not soup.find(id='block-pass-content'):raise ValueError('Actual instruction anchor absent')
  return {'application_url':official_url+'#block-pass-content','method':'email','actual_href':anchors[0] if anchors else None,'mailto_link':'mailto:'+mail,'email_presentation':'anchor' if anchors else 'literal_visible_address','submission_email':mail,'instructions_quote':mail,'instructions_locator':first_locator(text,mail),'submission_context_quote':submit[0],'submission_context_locator':first_locator(text,submit[0]) if submit[0] in text else None}
 raise ValueError('Explicit actual submission route absent or only enquiry contacts')


def parse_record(capture,as_of,verify=True):
 if capture['status']!=200:raise ValueError('Public detail HTTP failed')
 u=urlsplit(capture['url'])
 if u.scheme!='https' or u.hostname!=HOST or not u.path.startswith('/offre/'):raise ValueError('Wrong official notice host/path')
 raw=read_source_bytes(capture['raw_path'],storage=capture.get('raw_storage'));text=read_source_text(capture['text_path'],storage=capture.get('text_storage'))
 if sha256(raw).hexdigest()!=capture['raw_sha256'] or sha256(text.encode()).hexdigest()!=capture['text_sha256']:raise ValueError('Immutable capture hash mismatch')
 soup=BeautifulSoup(raw,'lxml');native=field(soup,'numero-d-offre')
 if not re.fullmatch(r'[AS]-20\d{2}-\d+',native):raise ValueError('Exact public notice ID absent')
 if capture.get('native_id') and capture['native_id']!=native:raise ValueError('RSS/native notice ID mismatch')
 h=soup.h1
 if h is None:raise ValueError('Public notice title absent')
 title=h.get_text(' ',strip=True);kind=field(soup,'type-de-contrat');degree=field(soup,'niveau-de-diplome-prepare')
 if kind not in ['Stage','Apprentissage'] or not degree or 'Niveau' not in degree:raise ValueError('Degree-linked student training scope unproven')
 scope='Niveau de diplôme préparé\n'+degree
 sloc=locator(text,scope)
 src={k:capture[k] for k in ['url','fetched_at','raw_path','raw_sha256','text_path','text_sha256']};src['request_url']=capture['url']
 for k in ['raw_storage','text_storage']:
  if k in capture:src[k]=capture[k]
 period=application_period(text,src,as_of);route=application_route(soup,text,capture['url'])
 issuer=field(soup,'entite') or field(soup,'administration-de-rattache')
 if not issuer:raise ValueError('Actual issuing entity absent')
 row={'source_family':'FR_PASS','source_id':'FR_PASS','id':native,'native_id':native,'title':title,'issuer':issuer,'official_url':capture['url'],'application_url':route['application_url'],'fetched_at':capture['fetched_at'],'request_url':capture['url'],**{k:capture[k] for k in ['raw_path','raw_sha256','text_path','text_sha256']},'status':'open','open_basis':'Explicit official application deadline or expressly continuous application acceptance, degree-prepared field and actual submission instructions','deadline_quote':period['quote'],'deadline_locator':period['locator'],'opens_on':None,'closes_on':period['closes_on'],'application_period':period,'student_quote':scope,'student_locator':sloc,'student_scope_context':'Official field: degree being prepared; current enrollment or degree-linked training, full personal eligibility unresolved','status_quote':period['quote'],'status_locator':period['locator'],'country':None,'location':field(soup,'ville'),'source_fields':{'title':title,'native_id':native,'entity':issuer,'administration':field(soup,'administration-de-rattache'),'department':field(soup,'service-d-affectation'),'contract_type':kind,'degree_prepared':degree,'degree_role':'currently_preparing_target_degree','activity_country_raw':field(soup,'pays'),'contract_start_raw':field(soup,'debut-du-contrat'),'application_route':route,'opportunity_type_normalization':'held_other'},**{k:capture[k] for k in ['raw_storage','text_storage'] if k in capture},'support_scope':'Same native PASS notice binds student scope, application period and submission instructions.'}
 if verify:verify_application_period(row,as_of,{})
 return row


def verify_email_instructions(row,raw):
 """Verify an instruction anchor and reparse the same explicit email submission route."""
 try:
  u=urlsplit(row['official_url']);app=urlsplit(row['application_url'])
  if u.scheme!='https' or u.hostname!=HOST or not u.path.startswith('/offre/'):return False
  if app.scheme!='https' or app.hostname!=HOST or app.path!=u.path or app.query!=u.query or app.fragment!='block-pass-content':return False
  soup=BeautifulSoup(raw,'lxml')
  if not soup.find(id='block-pass-content'):return False
  text=soup.get_text('\n',strip=True)
  actual=application_route(soup,text,row['official_url'])
  declared=row.get('source_fields',{}).get('application_route',{})
  if actual.get('method')!='email' or actual!=declared:return False
  if actual['mailto_link']!='mailto:'+actual['submission_email']:return False
  if actual['submission_email'] not in text:return False
  if actual['actual_href'] is not None and actual['actual_href'] not in {a['href'] for a in soup.select('a[href^="mailto:"]')}:return False
  native=soup.select_one('.field--name-field-numero-d-offre .field__item')
  if native is None or native.get_text(strip=True)!=str(row.get('native_id') or row.get('id')):return False
  return True
 except (KeyError,ValueError,TypeError):return False
