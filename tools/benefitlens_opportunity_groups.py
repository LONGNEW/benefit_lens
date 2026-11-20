"""Issuer and role grouping."""
import argparse
from collections import defaultdict
from difflib import SequenceMatcher
from hashlib import sha256
import json
from pathlib import Path
from benefitlens_source_storage import read_source_bytes
import re
import unicodedata
from urllib.parse import urlsplit

VERSION = 'issuer-role-v1'


def norm(value):
    value = unicodedata.normalize('NFKC', str(value)).casefold()
    return re.sub(r'[^\w]+', ' ', value, flags=re.UNICODE).strip()


def issuer_key(row):
    value = norm(row.get('issuer') or '')
    aliases = {'cvs health corporation': 'cvs health', 'cvs health inc': 'cvs health',
               'walgreen co': 'walgreens', 'walgreen company': 'walgreens'}
    if row.get('source_family') == 'Amazon.jobs':
        return 'amazon'
    return aliases.get(value, value)


def category(row):
    value = norm(row.get('opportunity_type') or row.get('type') or '')
    title = norm(row.get('title', ''))
    if any(x in value for x in ('scholar', 'fellow', 'bursary', 'funding', 'grant', 'loan')):
        return 'funding_scheme'
    if any(x in title for x in ('scholarship', 'studentship', 'bursary', 'fellowship')):
        return 'funding_scheme'
    return 'student_role'


def role_key(row):
    title = unicodedata.normalize('NFKC', row.get('title', '')).strip()
    issuer = issuer_key(row)
    rules = []
    if category(row) == 'funding_scheme':
        title = re.sub(r'\s*(?:[-,]\s*)?(?:Commencing\s+)?Term\s+\d+,?\s+20\d{2}\s*$', '', title, flags=re.I)
        title = re.sub(r'\s+20\d{2}\s*$', '', title)
        return norm(title), ['named_scheme_preserved_cycle_to_variant']
    value = norm(title)
    if issuer in {'walgreens', 'cvs health'}:
        if re.fullmatch(r'(?:summer )?pharmacy (?:pre grad )?intern(?: wm| sign on bonus available)?', value):
            return 'pharmacy intern', ['attested_pharmacy_student_role_alias']
        if value in {'pharmacy intern specialty', 'pharmacy intern specialty pharmacy'}:
            return 'specialty pharmacy intern', ['attested_specialty_pharmacy_alias']
        if issuer == 'cvs health' and re.match(r'^Retail Store Management Internship Summer 2027\s*[-–]', title):
            return 'retail store management intern', ['cvs_retail_2027_location_suffix']
    location = row.get('location')
    locations = [location] if isinstance(location, str) else []
    if isinstance(location, dict):
        locations = [str(v) for k,v in location.items() if k in {'city','name','location','displayName','addressLocality'}]
    locations += [re.sub(r'^[A-Z]{2}-', '', x) for x in locations if re.match(r'^[A-Z]{2}-.+', x)]
    for place in locations:
        place = place.strip()
        if len(place) < 3:
            continue
        p = re.escape(place)
        cleaned = re.sub(r'\s*[-–|]\s*'+p+r'\s*$', '', title, flags=re.I)
        cleaned = re.sub(r'^'+p+r'\s*[-–|]\s*', '', cleaned, flags=re.I)
        if cleaned != title:
            rules.append('structured_location_affix');title = cleaned
    geo = r'USA|United States|United Kingdom|UK|Germany|France|Canada|Nationwide'
    if row.get('country'):
        geo += '|'+re.escape(str(row['country']))
    cleaned = re.sub(r'\(\s*(?:'+geo+r')\s*\)', '', title, flags=re.I)
    if cleaned != title:
        rules.append('explicit_country_parenthetical');title = cleaned
    cleaned = re.sub(r'\((?:m/w/d|f/m/d|m/f/d|all genders|all gender)\)', '', title, flags=re.I)
    if cleaned != title:
        rules.append('gender_inclusive_suffix');title = cleaned
    cleaned = re.sub(r'\b(?:summer|winter|autumn|fall|spring)\s*(?:20\d{2})?\b', '', title, flags=re.I)
    cleaned = re.sub(r'\b20\d{2}\b', '', cleaned)
    if cleaned != title:
        rules.append('cohort_timing_to_variant');title = cleaned
    value = norm(title)
    value = re.sub(r'\binternship\b', 'intern', value)
    value = re.sub(r'\bsign on bonus available\b', '', value)
    value = ' '.join(value.split())
    return value, rules or ['full_role_title']


def identity(row):
    native = row.get('native_id') or row.get('id')
    provider=row.get('source_id') or row.get('source_family') or ''
    if row.get('source_family')=='workday_public_cxs':
        u=urlsplit(row.get('official_url',''));host=u.hostname or ''
        a=re.fullmatch(r'([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com',host)
        b=re.match(r'/recruiting/([a-z0-9-]+)/',u.path)
        if a:provider='workday/'+a[1]
        elif re.fullmatch(r'wd\d+\.myworkdaysite\.com',host) and b:provider='workday/'+b[1]
        else:raise ValueError('Workday requires an attested tenant URL')
    return str(provider), str(native or '')


def variant(row, line):
    fields = ('source_family','source_id','native_id','id','title','issuer','country','location',
              'official_url','application_url','fetched_at','raw_path','raw_sha256',
              'text_path','text_sha256','opens_on','closes_on','application_period',
              'education_levels','student_quote','student_locator','student_source','opportunity_subtype')
    return {'input_line': line, **{k:row[k] for k in fields if k in row}}


def group_opportunities(rows):
    buckets = {};native_seen = {};duplicates = []
    for line,row in enumerate(rows,1):
        native = identity(row)
        if native[1] and native in native_seen:
            if row != native_seen[native]:
                raise ValueError('Conflicting rows with the same source/native identity: '+str(native))
            duplicates.append({'input_line':line,'identity':list(native)});continue
        if native[1]:native_seen[native]=row
        issuer = issuer_key(row);role,rules = role_key(row);kind=category(row)
        token = (issuer,kind,role) if issuer and role else (issuer,kind,role,line)
        if token not in buckets:
            gid='opportunity-group-'+sha256(json.dumps(token,ensure_ascii=False).encode()).hexdigest()[:24]
            buckets[token]={'group_id':gid,'issuer_key':issuer,'role_key':role,'category':kind,
                            'decision':'confirmed','rule_ids':set(),'variants':[]}
        g=buckets[token];g['rule_ids'].update(rules);g['variants'].append(variant(row,line))
        if not issuer or not native[1] or len(role.split())<2 or role in {'summer intern','student intern','research intern','graduate intern'}:
            g['decision']='review_required'
    groups=list(buckets.values());reviews=[];byissuer=defaultdict(list)
    for g in groups:byissuer[g['issuer_key']].append(g)
    for issuer, candidates in byissuer.items():
        for i,a in enumerate(candidates):
            for b in candidates[i+1:]:
                if a['category']!=b['category']:continue
                if issuer=='european space agency':continue
                ta,tb=set(a['role_key'].split()),set(b['role_key'].split())
                jac=len(ta&tb)/max(1,len(ta|tb))
                ratio=SequenceMatcher(None,a['role_key'],b['role_key']).ratio()
                if (jac>=.78 and len(ta|tb)>=4) or ratio>=.92:
                    a['decision']=b['decision']='review_required'
                    reviews.append({'group_ids':[a['group_id'],b['group_id']], 'issuer_key':issuer,
                                    'reason':'similar_residual_roles_not_automatically_merged',
                                    'role_keys':[a['role_key'],b['role_key']]})
    for g in groups:
        g['rule_ids']=sorted(g['rule_ids']);g['variant_count']=len(g['variants'])
        g['countries']=sorted({str(v['country']).casefold() for v in g['variants'] if v.get('country')})
        g['country_unknown_variants']=sum(not v.get('country') for v in g['variants'])
        g['country_variant_count']=len(g['countries'])
        if g['decision']=='review_required' and not any(g['group_id'] in x['group_ids'] for x in reviews):
            reviews.append({'group_ids':[g['group_id']],'issuer_key':g['issuer_key'],
                            'reason':'insufficiently_specific_role_or_missing_identity'})
    groups.sort(key=lambda x:x['group_id']);reviews.sort(key=lambda x:x['group_ids'])
    return groups,reviews,duplicates


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);args=p.parse_args()
    raw=args.input.read_bytes();rows=[json.loads(x) for x in read_source_bytes(args.input).splitlines() if x.strip()]
    groups,review,duplicates=group_opportunities(rows)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    files={}
    for name,values in [('groups',groups),('review_queue',review),('duplicate_native_ids',duplicates)]:
        content=''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in values).encode();digest=sha256(content).hexdigest();dest=args.output_dir/(name+'-'+digest[:16]+'.jsonl')
        if dest.exists():
            if dest.read_bytes()!=content:raise ValueError('Immutable output conflict')
        else:
            with dest.open('xb') as f:f.write(content)
        files[name]={'path':str(dest.resolve()),'sha256':digest,'rows':len(values)}
    confirmed=[g for g in groups if g['decision']=='confirmed']
    result={'version':VERSION,'input':{'path':str(args.input.resolve()),'sha256':sha256(raw).hexdigest(),'rows':len(rows)},'parent_groups':len(groups),'confirmed_opportunity_count':len(confirmed),'review_required_groups':len(groups)-len(confirmed),'confirmed_variant_count':sum(g['variant_count'] for g in confirmed),'all_variant_count':sum(g['variant_count'] for g in groups),'country_variant_count':sum(g['country_variant_count'] for g in confirmed),'issuer_count':len({g['issuer_key'] for g in confirmed}),'files':files,'scope':'issuer-role rules; unresolved equivalence excluded'}
    encoded=json.dumps(result,ensure_ascii=False,indent=2).encode();dest=args.output_dir/('manifest-'+sha256(encoded).hexdigest()[:16]+'.json')
    if not dest.exists():
        with dest.open('xb') as f:f.write(encoded)
    print(json.dumps({'manifest':str(dest.resolve()),**result},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
