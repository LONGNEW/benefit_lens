from datetime import date
import pytest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from expand_benefitlens_varbi import doctoral_scope,exact_locator


def test_body_admission_required_title_alone_rejected():
 with pytest.raises(ValueError):doctoral_scope('PhD student in physics','Our university has 300 doctoral students. Administrative support is provided.')


def test_programme_administration_is_not_student_recruitment():
 with pytest.raises(ValueError):doctoral_scope('PhD programme coordinator','Only those admitted to doctoral studies may be employed as doctoral students.')


def test_completed_phd_criterion_is_not_current_training():
 with pytest.raises(ValueError):doctoral_scope('PhD position in science','The applicant must have enrolled in and completed a PhD programme.')


def test_direct_training_works_with_line_breaks():
 text='You will primarily devote yourself to your doctoral education, which includes research.'
 assert doctoral_scope('Doctoral position in physics',text)=='devote yourself to your doctoral education'


def test_admitted_student_employment_not_title_only():
 text='As a doctoral student, you will be both admitted as a student and employed at the university.'
 assert doctoral_scope('PhD student in biology',text)=='you will be both admitted as a student and employed'


def test_repeated_literal_evidence_is_not_silently_reanchored():
 with pytest.raises(ValueError):exact_locator('date\ndate','date')


def test_professor_supervision_title_is_not_student_role():
 with pytest.raises(ValueError):doctoral_scope('Professor in doctoral student supervision','you are both admitted as a student and employed')


def test_business_administration_is_field_not_administrator_role():
 assert doctoral_scope('Doctoral student in Business Administration','you are both admitted as a student and employed')=='you are both admitted as a student and employed'
