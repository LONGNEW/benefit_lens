from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect_benefitlens_participant_documents import selected_members, git_blob, gist_groups, document_role


def test_tree_truncation_and_unsafe_members_are_not_silently_accepted():
    with pytest.raises(ValueError, match='Truncated'):
        selected_members({'truncated': True}, r'\.md$')
    rows = [{'type': 'blob', 'path': p} for p in ['../escape.md', '2026/a/proposal.md', 'README.md', 'AUTHORS.md']]
    assert [r['path'] for r in selected_members({'tree': rows}, r'\.md$')] == ['2026/a/proposal.md']


def test_gist_queue_requires_observed_public_gist_path_and_preserves_multiple_associations():
    good = {'candidate_report_url': 'https://gist.github.com/alice/'+'a'*32, 'person_name': 'Alice'}
    bad = {'candidate_report_url': 'https://gist.github.com.evil.test/alice/'+'a'*32}
    groups = gist_groups([good, good, bad])
    assert len(groups) == 1
    assert len(groups['a'*32]['project_candidates']) == 2


def test_document_role_keeps_weekly_reports_separate_from_proposals():
    assert document_role('docs/2026/student/updates/week1.md', 'participant_reports') == 'participation_progress_report'
    assert document_role('2026/proposal.md', 'application_proposals') == 'application_proposal'
    assert git_blob(b'hello\n') == 'ce013625030ba8dba906f756967f9e9ca394464a'
