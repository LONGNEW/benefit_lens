"""Offline discovery adapter."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_benefitlens_live_batch as live
from benefitlens_opportunity_groups import identity
from benefitlens_source_storage import read_source_text,read_source_bytes

TABLES={'program':'programs','opportunity_round':'opportunity_rounds','source_document':'source_documents',
        'evidence':'evidence','claim':'claims','experience_case':'experience_cases',
        'case_document':'case_documents','writing_pattern':'writing_patterns','pattern_evidence':'pattern_evidence'}
APPROVABLE={'student_scope','current_application_status','verified_application_period'}


def digest(value):
    return sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def source_keys(row):
    provider,native=live.source_id(row)
    return 'live-'+sha256(live.encode([provider,native]).encode()).hexdigest()[:24],native


def _literal(evidence, document, repairs):
    text=read_source_text(document['text_path'],storage=document.get('text_storage'));lines=text.split('\n');q=' '.join(evidence['quote'].split())
    m=live.LOCATOR.fullmatch(evidence['locator'])
    a,z=(int(m[1]),int(m[2] or m[1])) if m else (0,0)
    if 1<=a<=z<=len(lines) and q in ' '.join(' '.join(lines[a-1:z]).split()):
        return
    found=list(re.finditer(r'\s+'.join(map(re.escape,q.split())),text)) if q else []
    if len(found)!=1:
        raise ValueError('Unresolved LF: '+evidence['evidence_id'])
    match=found[0];new=f'L{text[:match.start()].count(chr(10))+1}-L{text[:match.end()].count(chr(10))+1}'
    repairs.append({'evidence_id':evidence['evidence_id'],'previous_locator':evidence['locator'],
                    'locator':new,'basis':'unique_exact_span'})
    evidence['locator']=new


def family_links(rows,groups):
    index={};ids=set();links=[]
    for group in groups:
        if group['group_id'] in ids:raise ValueError('Duplicate family ID')
        ids.add(group['group_id'])
        for variant in group['variants']:
            key=identity(variant)
            if key in index:raise ValueError('Multiple families: '+str(key))
            index[key]=group
    for row in rows:
        group=index.get(identity(row))
        if not group:raise ValueError('Missing family membership: '+str(identity(row)))
        pid,rid=source_keys(row)
        links.append({'program_id':pid,'round_id':rid,'opportunity_family_id':group['group_id'],
                      'rule_version':'issuer-role-v1','review_state':group['decision']})
    if len(index)!=len(rows):raise ValueError('Family/input mismatch')
    return links


def build_handoff(rows,groups,*,input_ref=None,group_ref=None,created_at=None,curated=False,verify=True,as_of=None):
    links=family_links(rows,groups)
    if len({identity(r) for r in rows})!=len(rows):raise ValueError('Duplicate posting')
    cache={};checks={};rule_hash=sha256(Path(live.__file__).read_bytes()).hexdigest()
    for row in rows:
        key=source_keys(row)
        if not verify:
            checks[key]={'verified':False,'reason':'not_executed'};continue
        try:
            cohort_day=as_of or datetime.fromisoformat(row['fetched_at']).astimezone(live.KST).date()
            live.verify_posting(row,cohort_day,cache)
            checks[key]={'verified':True,'reason':'student_scope_status_period_rule_pass'}
        except Exception as error:
            checks[key]={'verified':False,'reason':type(error).__name__+': '+str(error)}
    tables=live.tables_for(rows)
    bundle={'contract_version':'team-handoff-v1','producer':'discovery_team',
            'created_at':created_at or datetime.now(timezone.utc).isoformat()}
    bundle.update({plural:tables[singular] for singular,plural in TABLES.items()})
    for r in bundle['opportunity_rounds']:
        if not checks[r['program_id'],r['round_id']]['verified']:r['status']='unknown'
    documents={d['document_id']:d for d in bundle['source_documents']};repairs=[];literal_errors={}
    for d in documents.values():
        d['access_scope']='public'
        for field in ('raw','text'):
            live.file_bytes(d[field+'_path'],d[field+'_sha256'],cache)
    for e in bundle['evidence']:
        try:_literal(e,documents[e['document_id']],repairs)
        except ValueError as error:literal_errors[e['evidence_id']]=str(error)
    edges=defaultdict(list)
    for edge in tables['claim_evidence']:edges[edge['claim_id']].append(edge['evidence_id'])
    for claim in bundle['claims']:
        key=claim['program_id'],claim['round_id'];eids=[e for e in edges[claim['claim_id']] if e not in literal_errors]
        checked=checks[key]['verified'] and eids and not any(e in literal_errors for e in edges[claim['claim_id']])
        accepted=bool(checked and claim['field'] in APPROVABLE)
        claim['evidence_ids']=eids
        claim['semantic_review']={'state':'accepted' if accepted else 'candidate',
            'reviewer':'discovery.local_rules',
            'decision_id':'mapping-'+digest([claim['claim_id'],rule_hash,checks[key]])[:24],
            'reason':checks[key]['reason'] if claim['field'] in APPROVABLE else 'source_statement_only'}
        if accepted:
            claim['semantic_review'].update(basis='existing_rule_review',rule_id='student-scope-and-application-period-v1',rule_version=rule_hash)
        claim['review_state']='reviewed' if accepted else 'candidate'
    for program in bundle['programs']:program['opportunity_type']='other'
    original=[]
    excluded={'raw_path','raw_sha256','text_path','text_sha256','application_period'}
    for i,row in enumerate(rows,1):
        pid,rid=source_keys(row)
        original.append({'program_id':pid,'round_id':rid,'input_line':i,
                         'fields':{k:v for k,v in row.items() if k not in excluded},
                         'country_role':'unresolved_source_field_not_applicant_nationality',
                         'semantic_mapping_state':'candidate'})
    held_evidence=[e for e in bundle['evidence'] if e['evidence_id'] in literal_errors]
    bundle['evidence']=[e for e in bundle['evidence'] if e['evidence_id'] not in literal_errors]
    states=Counter(c['semantic_review']['state'] for c in bundle['claims'])
    counted={x['opportunity_family_id'] for x in links if x['review_state']=='confirmed'}
    bundle['metadata']={'run_id':'discovery-adapter-'+digest([input_ref,group_ref,bundle['created_at']])[:24],'input_refs':[r for r in (input_ref,group_ref) if r],
        'identity_units':{'source_posting':'recruitment_variant_not_confirmed_selection_cycle',
                          'opportunity_family':'issuer_role_rule_group_not_exhaustive'},
        'family_memberships':links,'source_specific':original,'locator_repairs':repairs,
        'source_rule_checks':[{'program_id':p,'round_id':r,**v} for (p,r),v in checks.items()],
        'literal_errors':literal_errors,'held_evidence':held_evidence,'processing_scope':'curated_subset' if curated else 'all_supplied_postings',
        'counts':{'processed_source_postings':len(rows),'parent_family_candidates':len(set(x['opportunity_family_id'] for x in links)),
                  'rule_passed_families':len(counted),'review_families':len({x['opportunity_family_id'] for x in links})-len(counted),
                  'accepted_claims':states['accepted'],'candidate_claims':states['candidate']}}
    bundle['bundle_id']='discovery-handoff-'+digest([input_ref,group_ref,bundle['created_at'],rows and [r['raw_sha256'] for r in rows],links,curated])[:24]
    from benefitlens_handoff import build_handoff_index,claim_scope_fingerprint
    ix=build_handoff_index(bundle)
    for c in bundle['claims']:
        if c['semantic_review']['state']=='accepted':
            c['semantic_review']['input_scope_fingerprint']=claim_scope_fingerprint(bundle,c,indexes=ix)
    return bundle


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True)
    p.add_argument('--groups',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--curated',action='store_true');args=p.parse_args()
    refs=[];values=[]
    for path in (args.input,args.groups):
        content=path.read_bytes();refs.append({'path':str(path.resolve()),'sha256':sha256(content).hexdigest()})
        values.append([json.loads(line) for line in read_source_bytes(path).splitlines() if line.strip()])
    bundle=build_handoff(*values,input_ref=refs[0],group_ref=refs[1],curated=args.curated)
    from benefitlens_handoff import validate_handoff,review_mapping
    result=validate_handoff(bundle,artifact_root=Path('/'))
    args.output_dir.mkdir(parents=True,exist_ok=True)
    content=(json.dumps(bundle,ensure_ascii=False,sort_keys=True)+'\n').encode();checksum=sha256(content).hexdigest()
    path=args.output_dir/('handoff-'+checksum[:16]+'.json')
    if path.exists():
        if path.read_bytes()!=content:raise ValueError('Immutable destination conflict')
    else:
        with path.open('xb') as f:f.write(content)
    receipt={'bundle':{'path':str(path.resolve()),'sha256':checksum},'input_refs':refs,
             'validation':result,'mapping':{k:{'count':len(v),'ids':[r['id'] for r in v]} for k,v in review_mapping(bundle).items()},'counts':bundle['metadata']['counts'],
             'processing_scope':bundle['metadata']['processing_scope'],
             'type_mapping':{'held':len(values[0]),'legacy':dict(Counter(live.classify_type(r)[0] for r in values[0])),'reason':'classification pending; source labels preserved'},
             'adapter_sha256':sha256(Path(__file__).read_bytes()).hexdigest()}
    receipt_path=args.output_dir/('receipt-'+checksum[:16]+'.json')
    with receipt_path.open('x') as f:json.dump(receipt,f,ensure_ascii=False,indent=2)
    print(receipt_path.resolve())
    if not result['valid']:raise SystemExit(2)


if __name__=='__main__':main()
