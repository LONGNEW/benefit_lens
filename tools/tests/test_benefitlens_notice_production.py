from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from audit_benefitlens_notice_production import extract_conditions, load_catalog


def project(text):
    return extract_conditions(text,candidate_key='real-source-key',
        document={'document_id':'d','text_sha256':'original-text-hash'},
        source_url='https://issuer.example/posting',catalog=load_catalog())


def test_alternatives_and_preference_never_become_an_approved_conjunction():
    text="A Bachelor's or Master's degree is preferred; applicants must not require visa sponsorship."
    rows=project(text)
    assert {row['value'] for row in rows if row['field_id']=='education.degree'}=={'bachelor','master'}
    for row in rows:
        assert row['status']=='candidate' and row['logical_context']['tree'] is None
        assert 'logical_scope_not_reviewed' in row['unknown_reasons']
        for e in row['evidence']:
            assert text[e['char_start']:e['char_end']]==e['exact_quote']


def test_partial_coursework_is_not_a_current_completed_predicate():
    rows=project('Applicants must have completed most of their graduate coursework by the start of the fellowship.')
    row=next(row for row in rows if row['field_id']=='education.coursework_completed')
    assert 'most' in row['matched_text']
    assert row['at']['phase']=='benefit_start'
    assert row['operator']=='unknown' and row['status']=='candidate'


def test_won_per_semester_retains_amount_without_fabricated_total_or_dollar_currency():
    rows=project('지원 금액은 600만원, 학기당 지급한다.\nSalary: $100,000 per year.')
    money=[row for row in rows if row['field_id']=='benefit.amount']
    assert any(row['value']==6000000 and row['unit']=='KRW'
               and row['qualifiers']['frequency_candidate']=='per_semester' for row in money)
    dollar=next(row for row in money if row['matched_text'].startswith('$'))
    assert dollar['value']==100000 and dollar['unit']=='currency'
    assert 'unit_not_identified' in dollar['unknown_reasons']
    assert all(row['value']!=24000000 for row in money)


def test_unmapped_specific_requirements_survive_with_an_unknown_field_reason():
    rows=project('Applicants must submit an original portfolio containing 3D animation work.')
    row=next(row for row in rows if row['field_id']=='program_specific.requirement')
    assert '3D animation' in row['value']
    assert 'field_not_in_normalized_catalog' in row['unknown_reasons']
    assert row['operator']=='unknown' and row['applicant_input_stage']=='program_followup'


def test_source_country_or_generic_text_does_not_imply_citizenship():
    rows=project('Our office is located in South Korea. We are an international team.')
    assert not any(row['field_id']=='person.nationalities' for row in rows)


def test_missing_threshold_and_reference_time_are_explicit():
    rows=project('Applicants may be asked to provide TOEFL documentation.')
    row=next(row for row in rows if row['field_id']=='language.proficiency')
    assert row['value'] is None and row['at']=={'phase':'unknown','date':None}
    assert 'value_not_normalized' in row['unknown_reasons']
    assert 'reference_time_not_stated' in row['unknown_reasons']


def test_no_duplicate_ids_for_repeated_same_lines_and_all_fields_share_one_catalog():
    rows=project('GPA at least 3.5\nGPA at least 3.5\nExperience: 5+ years of relevant experience.')
    assert len({row['condition_id'] for row in rows})==len(rows)
    fields=load_catalog()['fields']
    assert all(row['field_id'] in fields for row in rows)
    assert all(fields[row['field_id']]['applicant_input_stage']==row['applicant_input_stage'] for row in rows)
