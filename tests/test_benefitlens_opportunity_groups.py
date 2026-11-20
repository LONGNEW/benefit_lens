"""Regression cases use actual titles seen in the 2026-11-09 secured cohort."""
import importlib.util
from pathlib import Path
import unittest

P=Path(__file__).resolve().parents[1]/'tools'/'benefitlens_opportunity_groups.py'
spec=importlib.util.spec_from_file_location('opportunity_groups',P)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def row(native,title,issuer='Walgreens',country='US',**kwargs):
    return {'id':native,'title':title,'issuer':issuer,'country':country,
            'source_family':'fixture','raw_sha256':'a'*64,
            'official_url':'https://example.org/'+native,**kwargs}


class OpportunityGroups(unittest.TestCase):
    def test_same_role_bonus_summer_country_are_variants(self):
        rows=[row('1','Pharmacy Intern'),row('2','Pharmacy Intern Sign On Bonus Available'),
              row('3','Summer Pharmacy Intern',country='CA')]
        groups,queue,_=m.group_opportunities(rows)
        self.assertEqual((len(groups),len(queue)),(1,0))
        self.assertEqual(groups[0]['variant_count'],3)
        self.assertEqual(groups[0]['countries'],['ca','us'])
        self.assertEqual({x['id'] for x in groups[0]['variants']},{'1','2','3'})

    def test_different_employers_do_not_merge(self):
        groups,_,_=m.group_opportunities([row('1','Pharmacy Intern'),row('2','Pharmacy Intern',issuer='CVS Health')])
        self.assertEqual(len(groups),2)

    def test_specialty_alias_preserves_distinct_regular_role(self):
        rows=[row('1','Pharmacy Intern - Specialty'),row('2','Pharmacy Intern Specialty Pharmacy'),row('3','Pharmacy Intern')]
        groups,_,_=m.group_opportunities(rows)
        self.assertEqual(len(groups),2)
        self.assertEqual(sorted(g['variant_count'] for g in groups),[1,2])

    def test_cvs_locations_merge_but_operations_role_does_not(self):
        rows=[row('1','Retail Store Management Internship Summer 2027 - Long Island',issuer='CVS Health'),
              row('2','Retail Store Management Internship Summer 2027 - Hudson County, NJ',issuer='CVS Health'),
              row('3','Retail Operations Strategy Corporate Internship (Graduate) - Summer 2027',issuer='CVS Health')]
        groups,_,_=m.group_opportunities(rows)
        self.assertEqual(len(groups),2)
        self.assertEqual(sorted(g['variant_count'] for g in groups),[1,2])

    def test_esa_project_differences_are_preserved(self):
        groups,queue,_=m.group_opportunities([
            row('1','Intern in the Space Safety Programme - Mission Implementation Division (Clear Space 1)',issuer='European Space Agency'),
            row('2','Intern in the Space Safety Programme - Mission Implementation Division (RISE)',issuer='European Space Agency')])
        self.assertEqual(len(groups),2);self.assertEqual(queue,[])

    def test_similar_roles_are_reviewed_not_merged_or_counted(self):
        groups,queue,_=m.group_opportunities([row('1','Software Engineer Intern',issuer='Example'),row('2','Software Engineering Intern',issuer='Example')])
        self.assertEqual(len(groups),2);self.assertTrue(queue)
        self.assertTrue(all(g['decision']=='review_required' for g in groups))

    def test_structured_location_only_no_arbitrary_technical_suffix_strip(self):
        a=row('1','Finance Intern - Paris',issuer='Example',location='Paris')
        b=row('2','Finance Internship - Lyon',issuer='Example',location='Lyon')
        self.assertEqual(m.role_key(a)[0],m.role_key(b)[0])
        c=row('3','Software Development Engineer Intern (USA), Amazon Dedicated Cloud (ADC)',issuer='Amazon',source_family='Amazon.jobs')
        d=row('4','Software Development Engineer Intern (USA)',issuer='Amazon',source_family='Amazon.jobs')
        self.assertNotEqual(m.role_key(c)[0],m.role_key(d)[0])

    def test_workday_structured_region_city_prefix(self):
        a=row('1','Sacramento - Tax JD - Intern - Summer 2027',issuer='PwC',location='CA-Sacramento')
        b=row('2','San Francisco - Tax JD - Intern - Summer 2027',issuer='PwC',location='CA-San Francisco')
        self.assertEqual(m.role_key(a)[0],m.role_key(b)[0])

    def test_named_funds_preserved_and_same_fund_cycles_group(self):
        a=row('1','UNSW Sport Scholarships 2027',issuer='UNSW',opportunity_type='scholarship')
        b=row('2','UNSW Sport Scholarships 2028',issuer='UNSW',opportunity_type='scholarship')
        c=row('3','UNSW Veterans Scholarship',issuer='UNSW',opportunity_type='scholarship')
        groups,_,_=m.group_opportunities([a,b,c]);self.assertEqual(len(groups),2)

    def test_generic_missing_information_is_unconfirmed(self):
        groups,queue,_=m.group_opportunities([row('1','Intern'),row('2','Research Intern')])
        self.assertTrue(all(g['decision']=='review_required' for g in groups));self.assertEqual(len(queue),2)

    def test_workday_native_id_is_scoped_to_actual_tenant(self):
        a=row('R1','Finance Intern',issuer='Shared Employer',source_family='workday_public_cxs',official_url='https://alpha.wd1.myworkdayjobs.com/en/job/R1')
        b=row('R1','Finance Intern',issuer='Shared Employer',source_family='workday_public_cxs',official_url='https://wd3.myworkdaysite.com/recruiting/beta/Careers/job/R1')
        self.assertNotEqual(m.identity(a),m.identity(b))
        groups,_,duplicates=m.group_opportunities([a,b])
        self.assertEqual(groups[0]['variant_count'],2);self.assertEqual(duplicates,[])

    def test_native_collision_fails_and_identical_copy_is_not_new_variant(self):
        a=row('1','Pharmacy Intern')
        groups,_,duplicates=m.group_opportunities([a,dict(a)])
        self.assertEqual(groups[0]['variant_count'],1);self.assertEqual(len(duplicates),1)
        with self.assertRaises(ValueError):m.group_opportunities([a,{**a,'title':'Supply Chain Intern'}])

    def test_group_id_stable_when_input_order_changes(self):
        a=[row('1','Pharmacy Intern'),row('2','Supply Chain Intern')]
        self.assertEqual({g['group_id'] for g in m.group_opportunities(a)[0]},
                         {g['group_id'] for g in m.group_opportunities(list(reversed(a)))[0]})


if __name__=='__main__':unittest.main()
