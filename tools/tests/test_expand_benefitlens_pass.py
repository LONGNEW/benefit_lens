import sys,unittest
from pathlib import Path
from datetime import date
from bs4 import BeautifulSoup
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from expand_benefitlens_pass import application_route,application_period,verify_email_instructions

class PassNoticeTests(unittest.TestCase):
 def route(self,body):
  soup=BeautifulSoup('<div id="block-pass-content">'+body+'</div>','lxml')
  return application_route(soup,soup.get_text('\n',strip=True),'https://www.pass.fonction-publique.gouv.fr/offre/example')
 def test_enquiry_contact_is_not_submission_route(self):
  with self.assertRaises(ValueError):self.route('<div class="field--name-field-informations-complementair">Pour tout renseignement, contacter <a href="mailto:enquiry@example.fr">enquiry@example.fr</a>.</div>')
 def test_email_instruction_is_not_an_online_form(self):
  r=self.route('<div class="field--name-field-informations-complementair">Envoyer votre candidature à <a href="mailto:apply@example.fr">apply@example.fr</a>.</div>')
  self.assertEqual(r['method'],'email');self.assertTrue(r['application_url'].endswith('#block-pass-content'))
 def test_two_addresses_require_role_resolution(self):
  with self.assertRaises(ValueError):self.route('<div class="field--name-field-informations-complementair">Envoyer votre candidature à a@example.fr ou b@example.fr</div>')
 def test_literal_online_submission_is_preserved_exactly(self):
  r=self.route('<div class="field--name-field-informations-complementair">Déposer votre candidature via https://demarche.numerique.gouv.fr/commencer/exact-native</div>')
  self.assertEqual(r['application_url'],'https://demarche.numerique.gouv.fr/commencer/exact-native')
  self.assertEqual(r['link_presentation'],'literal_visible_url')
 def test_email_instructions_bind_native_and_declared_route(self):
  raw=b'<div id="block-pass-content"><div class="field--name-field-numero-d-offre"><div class="field__item">S-2026-123456</div></div><div class="field--name-field-informations-complementair">Envoyer votre candidature a <a href="mailto:apply@example.fr">apply@example.fr</a>.</div></div>'
  soup=BeautifulSoup(raw,'lxml');url='https://www.pass.fonction-publique.gouv.fr/offre/example';route=application_route(soup,soup.get_text('\n',strip=True),url)
  row={'official_url':url,'application_url':route['application_url'],'native_id':'S-2026-123456','source_fields':{'application_route':route}}
  self.assertTrue(verify_email_instructions(row,raw))
  wrong=dict(row,native_id='S-2026-987654');self.assertFalse(verify_email_instructions(wrong,raw))
  wrong=dict(row,source_fields={'application_route':dict(route,submission_email='other@example.fr')});self.assertFalse(verify_email_instructions(wrong,raw))
 def test_generic_continuous_review_is_not_application_window(self):
  with self.assertRaises(ValueError):application_period('Examen des candidatures en continu',{},date(2026,11,9))
 def test_application_labelled_rolling_is_explicit(self):
  p=application_period('Date limite de candidature : Offre ouverte en continu',{},date(2026,11,9));self.assertEqual(p['kind'],'explicit_rolling');self.assertIsNone(p['closes_on'])
 def test_previous_deadline_is_not_current(self):
  with self.assertRaises(ValueError):application_period('Date limite de candidature : 1 novembre 2026',{},date(2026,11,9))
 def test_numeric_day_month_ambiguity_is_held(self):
  with self.assertRaisesRegex(ValueError,'Ambiguous numeric date'):application_period('Date limite de candidature : 06/12/2026',{},date(2026,11,9))
 def test_start_date_does_not_replace_application_deadline(self):
  with self.assertRaises(ValueError):application_period('Début du contrat : 15 décembre 2026',{},date(2026,11,9))

if __name__=='__main__':unittest.main()
