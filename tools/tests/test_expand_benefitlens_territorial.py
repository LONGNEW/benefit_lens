import sys,unittest
from pathlib import Path
from datetime import date
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from expand_benefitlens_territorial import territorial_period

class TerritorialPeriodTests(unittest.TestCase):
 def test_actual_submission_before_date_is_a_deadline(self):
  text='Contact et modalités de candidature\nAdressez votre CV et votre lettre de motivation par e-mail avant le 20 novembre 2026 inclus à :'
  p=territorial_period(text,{},date(2026,11,9));self.assertEqual(p['closes_on'],'2026-11-20');self.assertEqual(p['context_evidence'][0]['quote'],'Contact et modalités de candidature')
 def test_au_plus_tard_submission_date_is_recovered(self):
  text='Contact et modalités de candidature\nAdressez votre CV et votre lettre de motivation au plus tard le dimanche 06 décembre 2026.'
  self.assertEqual(territorial_period(text,{},date(2026,11,9))['closes_on'],'2026-12-06')
 def test_interview_schedule_is_not_submission_deadline(self):
  text='Contact et modalités de candidature\nEnvoyez votre candidature. Les entretiens seront organisés avant le 20 novembre 2026.'
  with self.assertRaises(ValueError):territorial_period(text,{},date(2026,11,9))
 def test_conflicting_deadline_field_is_held(self):
  text='Date limite de candidature\n30/11/2026\nContact et modalités de candidature\nAdressez votre CV avant le 20 novembre 2026.'
  with self.assertRaisesRegex(ValueError,'conflicts'):territorial_period(text,{},date(2026,11,9))
 def test_ambiguous_month_day_is_not_resolved_by_country(self):
  text='Date limite de candidature\n04/12/2026\nContact et modalités de candidature\nPostuler en ligne.'
  with self.assertRaisesRegex(ValueError,'Ambiguous'):territorial_period(text,{},date(2026,11,9))

if __name__=='__main__':unittest.main()
