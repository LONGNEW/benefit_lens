"""Student contracts on the public territorial employment portal, preserved HTML only."""
from pathlib import Path
from hashlib import sha256
from datetime import date
from urllib.parse import urlsplit,urljoin
from bs4 import BeautifulSoup
import re
from benefitlens_source_storage import read_source_bytes,read_source_text
from expand_benefitlens_pass import application_period,first_locator,DATE
from benefitlens_application_period import verify_application_period,extract_dates

HOST='www.emploi-territorial.fr'

def territorial_period(text,source,as_of):
 try:return application_period(text,source,as_of)
 except ValueError as exc:
  if str(exc) not in ['Actual application period unpublished or unparsed','Before-date lacks actual application context']:raise
 heading='Contact et modalités de candidature'
 if text.count(heading)!=1:raise ValueError('Application instruction section absent or ambiguous')
 periods=[]
 for line in text[text.index(heading):].splitlines():
  if not (re.search(r'candidature|\bCV\b|curriculum|lettre de motivation',line,re.I) and re.search(r'adress|envo|transmett|parvenir',line,re.I)):continue
  if re.search(r'entretien|prise de poste|démarrage|début du (?:contrat|stage)',line,re.I):continue
  m=re.search(r'(?:avant le|au plus tard le)[^\n]{0,35}?'+DATE,line,re.I)
  if not m:continue
  dates=extract_dates(m[0])
  if len(dates)!=1:raise ValueError('Application instruction date ambiguous')
  closing=next(iter(dates))
  if closing<=as_of:raise ValueError('Application deadline passed or same-day cutoff unknown')
  periods.append({'kind':'fixed_deadline','verification_state':'verified','semantics':'explicit_application_deadline','source':source,'quote':m[0],'locator':first_locator(text,m[0]),'opens_on':None,'closes_on':closing.isoformat(),'deadline_at':None,'context_evidence':[{'quote':heading,'locator':first_locator(text,heading)}]})
 if not periods:raise ValueError('Actual application period unpublished or unparsed')
 if len({p['closes_on'] for p in periods})!=1:raise ValueError('Conflicting explicit application instructions')
 for m in re.finditer(r'Date limite de candidature[^\n]*\n?\s*('+DATE+')',text,re.I):
  dates=extract_dates(m[1])
  if dates and dates!={date.fromisoformat(periods[0]['closes_on'])}:raise ValueError('Official deadline field conflicts with submission instructions')
 return periods[0]


def parse_record(cap,as_of,verify=True):
 if cap['status']!=200:raise ValueError('Official detail HTTP failed')
 u=urlsplit(cap['url'])
 if u.scheme!='https' or u.hostname!=HOST or not u.path.startswith('/offre/'):raise ValueError('Wrong official source')
 raw=read_source_bytes(cap['raw_path'],storage=cap.get('raw_storage'));text=read_source_text(cap['text_path'],storage=cap.get('text_storage'))
 if sha256(raw).hexdigest()!=cap['raw_sha256'] or sha256(text.encode()).hexdigest()!=cap['text_sha256']:raise ValueError('Immutable source mismatch')
 soup=BeautifulSoup(raw,'lxml');fields={}
 for node in soup.select('.offre-item'):
  k=node.select_one('.offre-item-label');v=node.select_one('.offre-item-value')
  if k and v:fields[k.get_text(' ',strip=True)]=v.get_text('\n',strip=True)
 kind=fields.get("Type d'emploi",'');grade=fields.get('Grade(s) recherché(s)','')
 if kind=='Stage étudiant':scope="Type d'emploi\nStage étudiant"
 elif kind=="Contrat d'apprentissage / PACTE" and grade=='Apprenti(e)':scope='Grade(s) recherché(s)\nApprenti(e)'
 else:raise ValueError('Student training contract is unproven; ordinary employment excluded')
 nm=re.search(r'Offre n°\s*(O\d{15})',text)
 if not nm or nm[1].casefold() not in u.path.casefold():raise ValueError('Actual native ID/URL binding absent')
 native=nm[1];heading=soup.select_one('main h2');title=heading.get_text(' ',strip=True) if heading else ''
 issuer=fields.get('Employeur')
 if not title or not issuer:raise ValueError('Actual title or employer absent')
 src={k:cap[k] for k in ['url','fetched_at','raw_path','raw_sha256','text_path','text_sha256']};src.update({k:cap[k] for k in ['raw_storage','text_storage'] if k in cap});src['request_url']=cap['url']
 period=territorial_period(text,src,as_of)
 links=[a for a in soup.select('a[href]') if a.get_text(' ',strip=True)=='Déposer ma candidature']
 if not links:raise ValueError('Actual online submission route absent; contact not assumed')
 a=links[0];app=urljoin(cap['url'],a['href'])
 if urlsplit(app).hostname!=HOST or not urlsplit(app).path.startswith('/candidature/') or native.casefold() not in app.casefold():raise ValueError('Submission/native ID differs')
 row={'source_family':'FR_EMPLOI_TERRITORIAL','source_id':'FR_EMPLOI_TERRITORIAL','id':native,'native_id':native,'title':title,'issuer':issuer,'official_url':cap['url'],'application_url':app,'fetched_at':cap['fetched_at'],'request_url':cap['url'],**{k:cap[k] for k in ['raw_path','raw_sha256','text_path','text_sha256','raw_storage','text_storage'] if k in cap},'status':'open','open_basis':'Current student contract, explicit official application deadline and native online submission link','opens_on':None,'closes_on':period['closes_on'],'application_period':period,'deadline_quote':period['quote'],'deadline_locator':period['locator'],'student_quote':scope,'student_locator':first_locator(text,scope),'status_quote':period['quote'],'status_locator':period['locator'],'country':None,'location':fields.get('Lieu de travail'),'source_fields':{'title':title,'native_id':native,'entity':issuer,'contract_type':kind,'grade':grade,'activity_location_raw':fields.get('Lieu de travail'),'contract_start_raw':fields.get('Poste à pourvoir le'),'all_official_fields':fields,'application_route':{'method':'online','actual_href':a['href'],'application_url':app,'instructions_quote':a.get_text(' ',strip=True),'instructions_locator':first_locator(text,a.get_text(' ',strip=True))},'opportunity_type_normalization':'held_other'},'support_scope':'Same native official portal notice binds student contract, application deadline and native submission route.'}
 if verify:verify_application_period(row,as_of,{})
 return row
