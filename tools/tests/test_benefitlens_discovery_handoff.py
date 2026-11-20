"""Offline contract migration regressions; fixtures are synthetic public captures."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import benefitlens_discovery_handoff as adapter
from benefitlens_opportunity_groups import group_opportunities


class DiscoveryHandoff(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def fixture(self,native='R1',country='GB',duplicate_period=False):
        apply='https://example.org/apply/'+native
        period='Applications are accepted on an ongoing basis'
        raw=f'<h1>Software Intern {native}</h1><p>Must be enrolled as a university student.</p><a href="{apply}">Apply</a><p>{period}</p>'
        text=f'Software Intern {native}\nMust be enrolled as a university student.\nApply\n{period}\n'
        if duplicate_period:raw+=f'<p>{period}</p>';text+=period+'\n'
        rp=self.root/(native+'.html');tp=self.root/(native+'.txt');rp.write_text(raw);tp.write_text(text)
        capture={'raw_path':str(rp),'raw_sha256':sha256(rp.read_bytes()).hexdigest(),
                 'text_path':str(tp),'text_sha256':sha256(tp.read_bytes()).hexdigest(),
                 'fetched_at':'2026-11-09T01:00:00+00:00','request_url':'https://example.org/jobs/'+native}
        row={**capture,'source_family':'fixture','id':native,'title':'Software Intern','issuer':'Example',
             'country':country,'opportunity_type':'internship','official_url':capture['request_url'],
             'application_url':apply,'status':'open','open_basis':'preserved_public_listing_and_application_statement',
             'student_quote':'Must be enrolled as a university student.','student_locator':'L2',
             'status_quote':'Apply','status_locator':'L3','opens_on':None,'closes_on':None,
             'listing_source':{**capture,'url':capture['request_url']},
             'application_period':{'kind':'explicit_rolling','verification_state':'verified','source':{**capture,'url':capture['request_url']},
                 'quote':period,'locator':'L4','opens_on':None,'closes_on':None,'deadline_at':None,
                 'semantics':'explicit_application_rolling','context_evidence':[]}}
        return row

    def build(self,rows,**kwargs):
        groups,_,_=group_opportunities(rows)
        return adapter.build_handoff(rows,groups,created_at='2026-11-09T07:00:00+00:00',**kwargs)

    def test_full_offline_conversion_and_narrow_approval(self):
        row=self.fixture()
        with patch('urllib.request.urlopen',side_effect=AssertionError('No HTTP')):
            b=self.build([row])
        self.assertEqual(b['contract_version'],'team-handoff-v1')
        self.assertEqual(len(b['programs']),1);self.assertEqual(len(b['opportunity_rounds']),1)
        self.assertEqual(b['programs'][0]['opportunity_type'],'other')
        self.assertEqual({c['field'] for c in b['claims'] if c['semantic_review']['state']=='accepted'},adapter.APPROVABLE)
        for c in b['claims']:
            if c['semantic_review']['state']=='accepted':
                self.assertEqual(c['semantic_review']['basis'],'existing_rule_review')
                self.assertTrue(c['semantic_review']['rule_version']);self.assertTrue(c['evidence_ids'])
        self.assertEqual(row,self.fixture())

    def test_country_remains_unmapped_source_specific(self):
        b=self.build([self.fixture(country='GB')]);record=b['metadata']['source_specific'][0]
        self.assertEqual(record['fields']['country'],'GB')
        self.assertEqual(record['country_role'],'unresolved_source_field_not_applicant_nationality')
        self.assertFalse(any('country' in c['field'] for c in b['claims']))

    def test_unverified_existing_reviewed_flag_cannot_approve(self):
        row=self.fixture();row['review_state']='reviewed';b=self.build([row],verify=False)
        self.assertTrue(all(c['semantic_review']['state']=='candidate' for c in b['claims']))
        self.assertEqual(b['metadata']['counts']['accepted_claims'],0)
        self.assertEqual(b['opportunity_rounds'][0]['status'],'unknown')

    def test_family_links_preserve_both_source_postings(self):
        rows=[self.fixture('R1','GB'),self.fixture('R2','US')];b=self.build(rows)
        memberships=b['metadata']['family_memberships']
        self.assertEqual(len(b['opportunity_rounds']),2);self.assertEqual(len(memberships),2)
        self.assertEqual(len({m['opportunity_family_id'] for m in memberships}),1)
        self.assertEqual(b['metadata']['counts']['rule_passed_families'],1)
        self.assertEqual(b['metadata']['counts']['processed_source_postings'],2)
        self.assertEqual(len({m['program_id'] for m in memberships}),2)

    def test_missing_or_double_family_assignment_fails(self):
        row=self.fixture();groups,_,_=group_opportunities([row])
        with self.assertRaises(ValueError):adapter.build_handoff([row],[],verify=False)
        duplicate=deepcopy(groups[0]);duplicate['group_id']='other-family'
        with self.assertRaises(ValueError):adapter.build_handoff([row],[groups[0],duplicate],verify=False)

    def test_artifact_hash_tamper_is_not_hidden_by_old_review(self):
        row=self.fixture();Path(row['text_path']).write_text('tampered')
        with self.assertRaises(ValueError):self.build([row])

    def test_unique_wrong_locator_repair_is_audited(self):
        row=self.fixture();row['student_locator']='L1';b=self.build([row])
        repair=b['metadata']['locator_repairs'][0]
        self.assertEqual(repair['previous_locator'],'L1');self.assertEqual(repair['locator'],'L2-L2')
        student=next(c for c in b['claims'] if c['field']=='student_scope')
        self.assertEqual(student['semantic_review']['state'],'accepted')

    def test_ambiguous_locator_is_held_not_arbitrarily_relocated(self):
        row=self.fixture(duplicate_period=True);row['application_period']['locator']='L1'
        b=self.build([row]);period=next(c for c in b['claims'] if c['field']=='verified_application_period')
        self.assertEqual(period['semantic_review']['state'],'candidate')
        self.assertEqual(period['evidence_ids'],[]);self.assertTrue(b['metadata']['held_evidence'])
        self.assertFalse(any(e['evidence_id'] in b['metadata']['literal_errors'] for e in b['evidence']))

    def test_curated_and_full_processing_are_distinct(self):
        row=self.fixture();full=self.build([row]);curated=self.build([row],curated=True)
        self.assertEqual(full['metadata']['processing_scope'],'all_supplied_postings')
        self.assertEqual(curated['metadata']['processing_scope'],'curated_subset')
        self.assertNotEqual(full['bundle_id'],curated['bundle_id'])

    def test_common_validator_and_exact_variant_evidence_binding(self):
        import benefitlens_handoff as common
        b=self.build([self.fixture('R1'),self.fixture('R2')])
        self.assertTrue(common.validate_handoff(b,artifact_root=Path('/'))['valid'])
        first=b['claims'][0];other=next(c for c in b['claims'] if c['program_id']!=first['program_id'])
        first['evidence_ids']=list(other['evidence_ids'])
        report=common.validate_handoff(b,artifact_root=Path('/'))
        self.assertFalse(report['valid'])
        self.assertTrue(any(e['code']=='evidence_scope' for e in report['errors']))

    def test_review_fingerprint_invalidates_modified_value(self):
        import benefitlens_handoff as common
        b=self.build([self.fixture()]);claim=b['claims'][0]
        self.assertEqual(len(claim['semantic_review']['input_scope_fingerprint']),64)
        claim['value']={**claim['value'],'unreviewed_change':True}
        result=common.review_mapping(b)
        self.assertFalse(any(r['id']==claim['claim_id'] for r in result['accepted']))
        item=next(r for r in result['held'] if r['id']==claim['claim_id'])
        self.assertIn('scope_fingerprint_mismatch',item['reasons'])

    def test_source_documents_keep_snapshot_and_public_scope(self):
        row=self.fixture();b=self.build([row])
        for doc in b['source_documents']:
            self.assertEqual(doc['access_scope'],'public');self.assertEqual(doc['raw_sha256'],row['raw_sha256'])
        ids={d['document_id'] for d in b['source_documents']}
        self.assertTrue(all(e['document_id'] in ids for e in b['evidence']))


if __name__=='__main__':unittest.main()
