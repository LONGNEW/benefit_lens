import json
from pathlib import Path

from data_platform.db import should_retire_missing
from data_platform.parsers import DETAIL_ENRICHERS, PARSERS


MIGRATION_DIR = Path(__file__).parents[1] / "migrations"
SOURCE_CONFIG = Path(__file__).parents[1] / "config" / "benefitlens_sources.json"


def test_migration_contains_required_layers() -> None:
    sql = "\n".join(path.read_text(encoding="utf-8") for path in sorted(MIGRATION_DIR.glob("*.sql")))
    required = [
        "platform.data_source",
        "source_group",
        "platform.collection_run",
        "platform.raw_document",
        "platform.document_version",
        "benefitlens.opportunity_version",
        "benefitlens.benefit",
        "benefitlens.eligibility_rule",
        "benefitlens.eligibility_rule_group",
        "benefitlens.criteria_definition",
        "benefitlens.profile_snapshot",
        "benefitlens.profile_attribute",
        "benefitlens.evaluation",
        "benefitlens.evaluation_rule_result",
        "benefitlens.evidence_span",
        "benefitlens.required_document",
        "benefitlens.application_prompt",
        "benefitlens.extraction_candidate",
        "benefitlens.funding_classification",
        "benefitlens.writing_style_profile",
        "benefitlens.experience_fact",
        "benefitlens.draft_version",
        "benefitlens.draft_claim",
        "benefitlens.claim_provenance",
        "latest_publishable_opportunity",
    ]
    for name in required:
        assert name in sql


def test_public_views_require_rights_and_review() -> None:
    sql = "\n".join(path.read_text(encoding="utf-8") for path in sorted(MIGRATION_DIR.glob("*.sql")))
    assert "s.rights_status = 'approved'" in sql
    assert "c.publication_status = 'reviewed'" in sql


def test_draft_claims_require_explicit_provenance() -> None:
    sql = "\n".join(path.read_text(encoding="utf-8") for path in sorted(MIGRATION_DIR.glob("*.sql")))
    assert "provenance_kind = 'official_requirement'" in sql
    assert "provenance_kind = 'experience_fact'" in sql
    assert "provenance_kind = 'needs_confirmation'" in sql


def test_source_registry_covers_agreed_portfolio_groups() -> None:
    config = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
    sources = [*config["sources"], *config["api_sources"]]
    expected = {"school", "foundation", "company", "national"}
    assert {source["source_group"] for source in sources} == expected


def test_school_source_is_restricted_to_postech() -> None:
    config = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
    sources = [*config["sources"], *config["api_sources"]]
    school_sources = [source for source in sources if source["source_group"] == "school"]
    assert len(school_sources) == 4
    assert all(source["provider_name"] == "POSTECH" for source in school_sources)
    assert config["school_directory"]["enabled"] is True


def test_source_registry_has_external_and_schoolwide_collectors() -> None:
    config = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
    sources = [*config["sources"], *config["api_sources"]]
    assert len(sources) == 13
    active = [source for source in sources if source.get("enabled", True)]
    assert {source["source_id"] for source in active} == {
        "postech-ax-funding-guide",
        "postech-cse-opportunity-notices",
        "posco-foundation-notices",
        "kosaf-application-notices",
        "postech-central-internal-scholarships",
        "postech-graduate-financial-support",
    }
    for source in active:
        assert source["parser"] in PARSERS or source["parser"] in {"postech_school_internal", "postech_department_funding", "postech_ax_funding"}
        if source.get("detail_parser"):
            assert source["detail_parser"] in DETAIL_ENRICHERS
        assert source["complete_snapshot"] is False


def test_only_complete_snapshots_can_retire_missing_records() -> None:
    assert should_retire_missing({"complete_snapshot": True})
    assert not should_retire_missing({"complete_snapshot": False})
    assert not should_retire_missing({})
