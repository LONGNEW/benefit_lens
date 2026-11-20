import sys,unittest,json
from tempfile import TemporaryDirectory
from hashlib import sha256
from datetime import date
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from expand_benefitlens_euraxess import visible_projection,student_scope
from expand_benefitlens_jobbnorge import doctoral_scope,jobbnorge_id,parse_record,source_native_matches

class EuropeanSourceTests(unittest.TestCase):
 def test_hidden_expiry_template_is_not_visible_expired_state(self):
  raw=b'<main><p>Apply now</p><div id="expiration" style="display:none" data-date-time="9999999999">STATUS: EXPIRED</div></main>'
  self.assertNotIn('EXPIRED',visible_projection(raw,'Doctoral call'))
  raw=b'<main><p>Apply now</p><div id="expiration">STATUS: EXPIRED</div></main>'
  self.assertIn('STATUS: EXPIRED',visible_projection(raw,'Doctoral call'))
 def test_metadata_or_negated_doctorate_is_not_student_scope(self):
  text='Job Information\nPositions\nPhD Positions\nOffer Description\nApplicants must not hold a doctoral degree.'
  with self.assertRaises(ValueError):student_scope(text)
  with self.assertRaises(ValueError):doctoral_scope('Applicants must not hold a doctoral degree.')
 def test_explicit_degree_training_context(self):
  q='PhD Research Fellow\nThe main purpose is research training leading to completion of a PhD degree.'
  self.assertIn('PhD',doctoral_scope(q)[0])
 def test_role_title_and_generic_university_footer_are_not_degree_evidence(self):
  text='PhD Candidate in Psychology\nUniversity profile\nOur university offers many PhD programmes and has 28000 students.'
  with self.assertRaises(ValueError):doctoral_scope(text)
 def test_explicit_admission_survives_inline_link_line_break(self):
  text='PhD Candidate in Psychology\nThe employee must gain admission to the\nPhD programme and remain enrolled throughout the appointment.'
  q,_=doctoral_scope(text)
  self.assertIn('admission to the\nPhD',q)
 def test_administration_and_completed_degree_are_not_admission(self):
  for text in ['Programme Coordinator\nAdministrative support for doctoral students is required.',
               'Research Scientist\nThe applicant must have enrolled in and completed a PhD programme.',
               'PhD Candidate\nThe applicant must have enrolled in and completed a PhD programme.']:
   with self.assertRaises(ValueError):doctoral_scope(text)
 def test_actual_enrolment_anchor_includes_degree(self):
  text="PhD Position\nPublic Administration and Governance at Oslo Business School and will be enrolled in OsloMet's PhD programme in Social Sciences."
  q,_=doctoral_scope(text)
  self.assertEqual(q,"will be enrolled in OsloMet's PhD programme")
 def test_source_native_join_and_allowed_api_host(self):
  record={'detail':{'url':'https://id.jobbnorge.no/api/joblisting?jobId=310313&languageId=2'},'info':{'url':'https://id.jobbnorge.no/api/joblisting/job-info?jobId=310313&languageId=2'},'page':{'url':'https://www.jobbnorge.no/ledige-stillinger/stilling/310313'}}
  source_native_matches(record,'310313')
  for key,url in [('detail','https://id.jobbnorge.no/api/joblisting?jobId=308403&languageId=2'),('info','https://other.example/api/joblisting/job-info?jobId=310313'),('page','https://www.jobbnorge.no/ledige-stillinger/stilling/308403')]:
   wrong={k:dict(v) for k,v in record.items()};wrong[key]['url']=url
   with self.assertRaisesRegex(ValueError,'Source URL/native join mismatch'):source_native_matches(wrong,'310313')
 def test_localized_job_ids_and_real_application_fragment_match(self):
  self.assertEqual(jobbnorge_id('https://www.jobbnorge.no/en/available-jobs/job/310313'), '310313')
  self.assertEqual(jobbnorge_id('https://www.jobbnorge.no/jobseeker/#/application/apply/310313'),'310313')
  self.assertIsNone(jobbnorge_id('https://eviljobbnorge.no/en/available-jobs/job/310313'))
 def test_aria_hidden_expiry_is_not_counted(self):
  self.assertNotIn('EXPIRED',visible_projection(b'<main><span aria-hidden="true">EXPIRED</span><p>Application Deadline</p></main>','Call'))
 def test_published_flag_requires_json_boolean_true(self):
  with TemporaryDirectory() as folder:
   p=Path(folder)/'record.json'
   for flag in ['false','true',1,False,None]:
    raw=json.dumps({'id':123,'isPublished':flag}).encode();p.write_bytes(raw);h=sha256(raw).hexdigest()
    cap={'raw_path':str(p),'text_path':str(p),'raw_sha256':h,'text_sha256':h}
    record={'detail':cap,'info':cap,'page':cap,'listing':{'id':123,'title':'PhD Candidate'}}
    with self.assertRaisesRegex(ValueError,'Native/status mismatch'):parse_record(record,cap,date(2026,11,9))

if __name__=='__main__':unittest.main()
