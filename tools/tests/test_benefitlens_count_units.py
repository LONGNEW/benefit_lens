from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_benefitlens_live_batch import select_counted_rows


def posting(native, country='US', title='Pharmacy Intern'):
    return {'source_family':'official','native_id':native,'issuer':'CVS Health','country':country,'title':title}


def test_regional_posting_ids_do_not_satisfy_parent_group_target():
    rows = [posting('one'), posting('two', 'CA')]
    with pytest.raises(ValueError, match='only 1 pass grouping rules'):
        select_counted_rows(rows, 2)
    selected, groups, reviews = select_counted_rows(rows, 1)
    assert len(selected) == 2 and len(groups) == 1
    assert groups[0]['countries'] == ['ca', 'us']
    assert {v['country'] for v in groups[0]['variants']} == {'CA', 'US'}


def test_raw_posting_count_requires_an_explicit_mode():
    selected, groups, _ = select_counted_rows([posting('one'), posting('two')], 2, 'source_posting')
    assert len(selected) == 2 and len(groups) == 1


def test_distinct_roles_remain_separate_even_at_the_same_company():
    selected, groups, _ = select_counted_rows([posting('one'), posting('two', title='Software Engineer Intern')], 2)
    assert len(selected) == 2 and len(groups) == 2
