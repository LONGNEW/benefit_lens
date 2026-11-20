from copy import deepcopy
from datetime import datetime, timezone
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benefitlens_source_storage import store_source_bytes
from collect_benefitlens_application_materials import checked_url, html_text
from import_benefitlens_ucr_samples import prepare


@pytest.fixture
def material(tmp_path):
    parent=b'''<ul><li>Nathan A., English Teaching Assistant to Kyrgyz Republic,
      2026-27 Fulbright Recipient | <a href="https://drive.google.com/file/d/example/view">
      Read Nathan's application</a></li></ul>'''
    row={'id':'example','state':'body_staged','program_id':'fulbright-us-student-eta',
         'person_name':'Nathan A.','application_round':'2026-2027','host_country':'Kyrgyz Republic',
         'original':store_source_bytes(b'%PDF-example',tmp_path),
         'text':store_source_bytes(('A bounded application sample. '*30).encode(),tmp_path),
         'collected_at':datetime.now(timezone.utc).isoformat()}
    return row,store_source_bytes(parent,tmp_path)


def test_application_material_does_not_become_participation_review(material):
    key,doc,payload=prepare(*material)
    assert key.startswith('application-ucr-')
    assert payload['evidence_roles']==['application_document']
    assert payload['document_ids']==[doc['document_id']]
    assert payload['semantic_review']=='unreviewed'
    assert payload['approved_pattern'] is False


@pytest.mark.parametrize('field,value',[
    ('person_name','David S.'),('application_round','2026-2028'),
    ('program_id','fulbright-us-student-research'),('host_country','India'),('id','otherfile')])
def test_attribution_requires_same_person_program_cycle_country_and_file(material,field,value):
    row,parent=deepcopy(material)
    row[field]=value
    with pytest.raises(ValueError): prepare(row,parent)


def test_source_snapshot_mutation_rejected(material):
    row,parent=deepcopy(material)
    row['text']['sha256']='0'*64
    with pytest.raises(ValueError,match='hash mismatch'): prepare(row,parent)


def test_discovery_rejects_unregistered_hosts_and_credentials():
    prefix='https://publisher.example/archive/'
    assert checked_url(prefix,'paper.pdf',prefix)==prefix+'paper.pdf'
    assert checked_url(prefix,'https://publisher.example.evil/archive/paper.pdf',prefix) is None
    assert checked_url(prefix,'https://user@publisher.example/archive/paper.pdf',prefix) is None
    assert checked_url(prefix,'/private/paper.pdf',prefix) is None


def test_article_extraction_omits_navigation_and_form_text():
    text,_=html_text(b'<main><nav>Navigation</nav><p>Applicant account</p><form>Login</form></main>','main')
    assert text=='Applicant account'
