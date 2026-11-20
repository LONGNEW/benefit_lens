"""New snapshots retain source versions without duplicating unchanged anchors."""
from copy import deepcopy
from unittest.mock import patch
import benefitlens_compact_experience as experience
import run_benefitlens_compact as runner
from test_benefitlens_compact_experience import passage


def store(root):
    with patch.object(runner,'BASE',root):return runner.connect(root/'compact')


def test_rescanning_same_anchor_under_another_case_owner_does_not_duplicate(tmp_path):
    cx=store(tmp_path);first=experience.tag_passage(passage('I learned to explain the result.'))
    runner.add(cx,'experience',first,experience)
    another=deepcopy(first);another['case_key']='another-case';another['record_key']='another-case'
    runner.add(cx,'experience',another,experience)
    assert cx.execute('SELECT count(*) FROM occurrences').fetchone()[0]==1
    assert cx.execute('SELECT count(*) FROM groups').fetchone()[0]==1


def test_legacy_passage_id_does_not_discard_an_updated_source_version(tmp_path):
    cx=store(tmp_path)
    first=experience.tag_passage(passage('I learned to explain the result.'))
    changed=experience.tag_passage(passage('I learned to explain the choice.'))
    assert first['passage_id']==changed['passage_id']
    runner.add(cx,'experience',first,experience);runner.add(cx,'experience',changed,experience)
    runner.add(cx,'experience',changed,experience)
    assert cx.execute('SELECT count(*) FROM occurrences').fetchone()[0]==2
    assert cx.execute('SELECT count(*) FROM groups WHERE occurrence_count=0').fetchone()[0]==0
    assert cx.execute('SELECT count(distinct text_sha256) FROM occurrences').fetchone()[0]==2


def test_new_source_role_gets_a_distinct_context_binding(tmp_path):
    cx=store(tmp_path);first=experience.tag_passage(passage('I learned to explain the result.'))
    changed=deepcopy(first);changed['source_role']='application_document'
    runner.add(cx,'experience',first,experience);runner.add(cx,'experience',changed,experience)
    assert cx.execute('SELECT count(*) FROM occurrences').fetchone()[0]==2
    assert cx.execute('SELECT count(*) FROM groups').fetchone()[0]==2
