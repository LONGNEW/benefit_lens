"""Interpret the public Jobbnorge UI's published, deadline and PhD evidence."""
from pathlib import Path
from hashlib import sha256
from datetime import date
from urllib.parse import urlsplit,parse_qs
import json,re
from benefitlens_source_storage import read_source_bytes,read_source_text,store_source_bytes


def jobbnorge_id(url):
    u=urlsplit(url or '')
    host=u.hostname or ''
    if host!='jobbnorge.no' and not host.endswith('.jobbnorge.no'):return None
    m=re.search(r'(?:/job/|/stilling/|/apply/)(\d+)',u.path+'#'+u.fragment)
    return m[1] if m else None


def locator(text,quote):
    if not quote or text.count(quote)!=1:raise ValueError('Literal absent or ambiguous')
    n=text[:text.index(quote)].count('\n')+1
    return f'L{n}-L{n+quote.count(chr(10))}'


def doctoral_scope(text):
    title,_,body=text.partition('\n')
    role=r"(?:Ph\.?\s?D\.?[\s-]*(?:(?:Research\s+)?(?:Candidate|Fellow(?:ship)?|Student|Position)s?)|doctoral\s+(?:research\s+)?(?:candidate|fellow|student|position)s?|(?:doktorgrads)?stipendiat(?:er|stilling)?)"
    # A degree programme mentioned in a university footer is not a recruited role.
    if not re.search(role,title,re.I) and not re.search(r'(?:looking for|invite applications for|position as|has a vacancy for)[^\n]{0,75}'+role,body,re.I):
        raise ValueError('No explicit recruited doctoral role')
    degree=r"(?:Ph\.?\s?D\.?[ -]*(?:programme?|degree|studies)?|doctoral (?:programme?|degree|studies)|doctorate)"
    patterns=[r'\b(?:will|must|shall|should)\s+(?:be\s+)?enroll?ed\s+(?:in|on)\b[\s\S]{0,75}?'+degree,
        r'\b(?:admission|admitted)\b[\s\S]{0,85}?'+degree,
        r'\b(?:enroll?ed|enrolment|enrollment|registered|pursue|pursuing)\b[\s\S]{0,85}?'+degree,
        r'\bresearch training\b[\s\S]{0,110}?'+degree,
        r'\b(?:opptak|opptatt)\b[\s\S]{0,80}?(?:ph\.?d|doktorgrad)(?:[ -]?(?:program(?:met)?|utdanning))?']
    for pattern in patterns:
        for m in re.finditer(pattern,body,re.I):
            context=body[max(0,m.start()-55):m.end()]
            if re.search(r'not (?:hold|have|already)|already (?:hold|have)|must not|post.?doc|(?:have|has|had)\s+[^.\n]{0,35}completed|completed\s+(?:a |the )?Ph\.?D',context,re.I):continue
            if text.count(m[0])==1:return m[0],locator(text,m[0])
    raise ValueError('No positive doctoral training/admission statement')


def source_native_matches(record,native):
    for key,path in [('detail','/api/joblisting'),('info','/api/joblisting/job-info')]:
        u=urlsplit(record[key]['url'])
        if u.scheme!='https' or u.hostname!='id.jobbnorge.no' or u.path!=path or parse_qs(u.query).get('jobId')!=[native]:
            raise ValueError('Source URL/native join mismatch')
    u=urlsplit(record['page']['url'])
    if u.scheme!='https' or u.hostname not in {'www.jobbnorge.no','jobbnorge.no'} or jobbnorge_id(record['page']['url'])!=native:
        raise ValueError('Source URL/native join mismatch')


def parse_record(record,template,as_of):
    detail,info,page=record['detail'],record['info'],record['page']
    for source in [detail,info,page,template]:
        for k in ['raw','text']:
            if sha256(read_source_bytes(source[k+'_path'],storage=source.get(k+'_storage'))).hexdigest()!=source[k+'_sha256']:raise ValueError('Immutable source mismatch')
    body=json.loads(read_source_text(detail['raw_path']));meta=json.loads(read_source_text(info['raw_path']));html=read_source_text(page['raw_path']);text=read_source_text(detail['text_path']);itext=read_source_text(info['text_path'])
    native=str(record['listing']['id'])
    if str(meta.get('id'))!=native or body.get('isPublished') is not True:raise ValueError('Native/status mismatch')
    source_native_matches(record,native)
    title=record['listing']['title']
    if re.search(r'post.?doc|professor|førsteamanuensis',title,re.I):raise ValueError('Postdoctoral/faculty role excluded')
    value=meta.get('deadlineOn','');m=re.fullmatch(r"\$date\('(20\d{2}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})'\)",value)
    if not m or date.fromisoformat(m[1])<=as_of:raise ValueError('Deadline unknown/expired/today cutoff unknown')
    # These are values from the observed public page config, never guessed endpoints.
    aid=re.search(r"\bid:\s*'([0-9]+)'",html);aurl=re.search(r"\bapplyUrl:\s*'([^']+)'",html);can=re.search(r"canApplyOnline:\s*'True'\s*===\s*'True'",html)
    if not aid or aid[1]!=native or not aurl or not can or jobbnorge_id(aurl[1])!=native:raise ValueError('Actual online application config absent')
    config=re.search(r'\bjob:\s*\{[\s\S]*?\n\s*\}',html)
    if not config:raise ValueError('Original application config object absent')
    cb=(config[0]+'\n').encode();ref=store_source_bytes(cb,Path(page['text_path']).parent/'applyconfig-blobs',suffix='.txt')
    page=page|{'text_path':ref['path'],'text_sha256':ref['sha256'],'text_storage':ref['storage']}
    scope,scope_loc=doctoral_scope(text);short=scope
    if len(short.split())>18:raise ValueError('Doctoral proof exceeds per-document quote allowance')
    # Exact past commencement statements require review, while historical statute years do not.
    from benefitlens_application_period import extract_dates
    for st in re.findall(r'(?:start(?:ing)? date|commencement)[^.\n]{0,85}',text,re.I):
        if any(d<as_of for d in extract_dates(st)):raise ValueError('Past specified commencement date held')
    benefits=[]
    for x in re.finditer(r'(?:salary|gross annual|annual salary|NOK|lønn|løn)[^.\n]{0,130}',text,re.I):
        q=x[0].strip();ts=list(re.finditer(r'\S+',q));q=q[:ts[min(5,len(ts)-1)].end()] if ts else q
        if text.count(q)==1:benefits.append({'quote':q,'locator':locator(text,q)});break
    period={'kind':'fixed_deadline','verification_state':'verified','source':info|{'scope':'Jobbnorge job-info application deadline for native '+native},'quote':value,'locator':locator(itext,value),'closes_on':m[1],'opens_on':None,'deadline_at':None,'semantics':'jobbnorge_application_deadline','context_evidence':[],'field_semantics_source':template}
    return {'capture':detail,'source_family':'JOBBNORGE','source_id':'JOBBNORGE','id':native,'native_id':native,'title':title,'issuer':meta['employerName'],'official_url':record['listing']['link'],'application_url':aurl[1],'application_link_source':page,'status':'open','opens_on':None,'closes_on':m[1],'application_period':period,'period_source':info,'country':None,'country_role':'activity_location_not_yet_normalized','location':record['listing'].get('location'),'student_quote':short,'student_locator':scope_loc,'student_scope_context':scope,'status_quote':'true','status_locator':locator(text,'isPublished: true'),'period_literal':value,'deadline_quote':value,'deadline_locator':period['locator'],'period_label':None,'period_label_locator':None,'source_fields':{'title':title,'employer_name':meta['employerName'],'employer_id':body.get('employerId'),'isPublished':True,'canApplyOnline':True,'application_config':'configOptions.job.applyUrl','actual_application_url':aurl[1],'api_job_id':meta['id'],'source_category':'doctoral_recruitment','raw_position_count':body.get('positionCount'),'position_count_counted_as_opportunities':False,'opportunity_type_normalization':'held_other','benefit_candidates':benefits},'support_scope':'Public Jobbnorge job-info, notice body and page config refer to the same native recruitment '+native}
