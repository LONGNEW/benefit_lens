from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_benefitlens_writing_patterns import ranks,label_agreement,fraction


def test_tied_ordinal_scores_receive_average_rank():
    assert ranks([3,1,3,5])==[2.5,1.0,2.5,4.0]


def test_unpredicted_human_class_remains_visible_in_agreement_report():
    result=label_agreement([('style','clarity'),('clarity','clarity')])
    assert result['matched_pairs']==2 and result['agreement']==0.5
    assert result['per_label']['style']['human_support']==1
    assert result['per_label']['style']['predicted_count']==0
    assert result['per_label']['style']['f1']==0


def test_empty_denominator_is_not_reported_as_a_zero_quality_rate():
    assert fraction(0,0)['percent'] is None
    assert label_agreement([])['agreement'] is None
