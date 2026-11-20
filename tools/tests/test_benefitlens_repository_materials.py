from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect_benefitlens_repository_materials import proposal_members, source_program_candidates


def test_truncated_tree_cannot_be_treated_as_complete():
    with pytest.raises(ValueError, match='truncated'):
        proposal_members({'truncated': True, 'tree': []})


def test_inventory_does_not_treat_directories_or_arbitrary_files_as_proposals():
    entries = [{'type': kind, 'path': name} for kind, name in [
        ('blob', '2025/Essay.PDF'), ('blob', '../escape.pdf'),
        ('blob', 'README.md'), ('tree', 'directory.pdf')]]
    assert [r['path'] for r in proposal_members({'tree': entries})] == ['2025/Essay.PDF']


def test_repository_name_does_not_assign_every_document_to_gsoc():
    assert source_program_candidates('LFX/example.pdf') == ['lfx-mentorship']
    assert source_program_candidates('Summer of Bitcoin/example.pdf') == ['summer-of-bitcoin']
    assert source_program_candidates('GSoC/2025/example.pdf') == ['google-summer-of-code']
    assert source_program_candidates('other/example.pdf') == []
