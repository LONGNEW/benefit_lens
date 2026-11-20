"""Import five publisher-linked application bundles separately from experience reviews."""
from datetime import datetime
from hashlib import sha256
import json
import sqlite3
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb

import project
from benefitlens_source_storage import read_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json
from run_benefitlens_korea import soup

ROOT=CACHE/'experience-application-expansion-v1/ucr-application-samples'
PARENT='https://international.ucr.edu/abroad/scholarship'


def prepare(row,parent_ref):
    """Require exact publisher-to-file attribution and complete local originals."""
    if row.get('state')!='body_staged' or row.get('program_id') not in {
            'fulbright-us-student-eta','fulbright-us-student-research'}:
        raise ValueError('Unexpected application material scope')
    raw=read_source_bytes(parent_ref['path'],storage=parent_ref.get('storage'))
    if sha256(raw).hexdigest()!=parent_ref['sha256']:
        raise ValueError('University page hash mismatch')
    document=soup(raw)
    anchors=[a for a in document.select('a[href]')
             if '/file/d/'+row['id']+'/' in a['href']]
    first_name=row['person_name'].split()[0]
    year=row['application_round'].split('-')[0]
    matches=[a for a in anchors if a.find_parent('li')
             and first_name in a.find_parent('li').get_text(' ',strip=True)
             and year in a.find_parent('li').get_text(' ',strip=True)]
    if not matches: raise ValueError('Named recipient and cycle not bound to exact file link')
    anchor=matches[0]
    quote=anchor.find_parent('li').get_text(' ',strip=True).split('|')[0].strip()
    if 'Fulbright' not in quote or 'Recipient' not in quote:
        raise ValueError('Publisher does not identify this application as recipient material')
    display_cycle=year+'-'+row['application_round'].split('-')[1][-2:]
    expected_role='English Teaching Assistant' if row['program_id'].endswith('-eta') else 'Researcher'
    if display_cycle not in quote or expected_role not in quote or row['host_country'] not in quote:
        raise ValueError('Program type, complete cycle or host country attribution differs')
    for key in ('original','text'):
        ref=row[key];content=read_source_bytes(ref['path'],storage=ref.get('storage'))
        if sha256(content).hexdigest()!=ref['sha256']:
            raise ValueError('Application snapshot hash mismatch')
        if key=='original' and not content.startswith(b'%PDF-'):
            raise ValueError('Expected PDF original')
        if key=='text' and len(content.strip())<400:
            raise ValueError('Incomplete extracted application text')
    canonical='https://drive.google.com/file/d/'+row['id']+'/view'
    docid='application-doc-'+sha256((canonical+'\n'+row['original']['sha256']).encode()).hexdigest()[:32]
    caseid='application-ucr-'+sha256(json.dumps([row['person_name'],row['program_id'],
                                               row['application_round']]).encode()).hexdigest()[:24]
    payload={k:v for k,v in row.items() if k not in {'id','url','original','text'}}
    payload.update(active=True,case_key=caseid,cycle=row['application_round'],outcome='selected',
       source_family='ucr_published_application_samples',
       document_ids=[docid],document_roles=['application_bundle'],
       evidence_roles=['application_document'],case_scope='public_application_document',
       identity_status='publisher_attributed_partial_name',attribution_status='document_bound',
       author_handle=None,semantic_review='unreviewed',approved_pattern=False,
       text_key=row['text']['sha256'],quality_audit={
          'version':'publisher-file-attribution-v1','attribution_status':'document_bound',
          'identity_status':'publisher_attributed_partial_name','unresolved_document_ids':[],
          'semantic_review':'unreviewed','approved_pattern':False},
       source_attribution={'url':PARENT,'snapshot':parent_ref,
          'exact_quote':quote,'file_link':anchor['href'],
          'review_basis':'agent_checked_publisher_recipient_label; hashes_checked_by_code',
          'reviewer':'codex','current_prompt_not_reviewed':True})
    document={'document_id':docid,'url':canonical,'raw':row['original'],'text':row['text'],
              'kind':'pdf','fetched_at':row['collected_at']}
    return caseid,document,payload


def main():
    records=json.loads((ROOT/'index.json').read_text())
    with sqlite3.connect((ROOT/'korea.sqlite').as_uri()+'?mode=ro',uri=True) as local:
        captures=[json.loads(r[0]) for r in local.execute('SELECT meta FROM captures')]
    parent=next(c for c in reversed(captures) if c['url']==PARENT and c['status']==200)
    prepared=[(row,*prepare(row,parent)) for row in records]
    added=0
    with project.connect() as conn:
        for row,key,doc,payload in prepared:
            # URLs and content hashes are both reconciled before creating a new case.
            prior=conn.execute("""SELECT case_key FROM benefitlens.experience_discovery_case
              WHERE case_key=%s OR source_url=%s OR payload->>'text_key'=%s""",
              (key,doc['url'],row['text']['sha256'])).fetchall()
            if prior:
                if any(p[0]!=key for p in prior):
                    raise ValueError('Existing material needs cross-source identity review')
                continue
            conn.execute("""INSERT INTO benefitlens.pipeline_document
              (document_id,canonical_url,content_sha256,text_sha256,kind,raw_path,text_path,fetched_at,metadata)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(document_id) DO NOTHING""",
              (doc['document_id'],doc['url'],row['original']['sha256'],row['text']['sha256'],'pdf',
               row['original']['path'],row['text']['path'],row['collected_at'],
               Jsonb({'raw_storage':row['original']['storage'],'text_storage':row['text']['storage'],
                      'source_attribution':payload['source_attribution'],
                      'material_role':'application_document','access_basis':row['access_basis']})))
            conn.execute("""INSERT INTO benefitlens.experience_discovery_case
              (case_key,program_id,person_name,application_round,outcome_claim,evidence_status,source_url,payload)
              VALUES(%s,%s,%s,%s,'selected','public_original_collected',%s,%s)""",
              (key,row['program_id'],row['person_name'],row['application_round'],doc['url'],Jsonb(payload)))
            added+=1
    receipt={'at_kst':datetime.now(KST).isoformat(),'checked_bundles':len(prepared),
      'new_application_document_cases':added,'new_review_experiences':0,
      'approved_patterns_added':0,'case_keys':[p[1] for p in prepared],
      'scope':'US outbound recipients; exact final submission version unconfirmed; current prompt review pending',
      'index':str(ROOT/'index.json')}
    atomic_json(ROOT/'import_receipt.json',receipt)
    print(json.dumps(receipt,ensure_ascii=False))


if __name__=='__main__':
    main()
