import json
from pathlib import Path
from types import SimpleNamespace

from data_platform.cli import build_parser
from data_platform.codex_extraction import LocalCodexExtractionClient
from data_platform.parsers import ParsedOpportunity


SCHEMA_PATH = (
    Path(__file__).parents[1] / "config" / "benefitlens_extraction.schema.json"
)


class FakeRunner:
    def __init__(self, candidates, stderr=""):
        self.candidates = candidates
        self.stderr = stderr
        self.calls = []

    def __call__(self, args, **kwargs):
        image_paths = [
            Path(args[index + 1])
            for index, value in enumerate(args)
            if value == "--image"
        ]
        self.calls.append(
            {
                "args": args,
                "input": kwargs["input"],
                "images_existed": all(path.is_file() for path in image_paths),
            }
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"candidates": self.candidates}, ensure_ascii=False),
            stderr=self.stderr,
        )


def _record(key: str = "notice:1") -> ParsedOpportunity:
    return ParsedOpportunity(
        canonical_key=key,
        title="장학생 모집",
        summary="장학생 모집 공고",
        official_url="https://official.test/notice/1",
        benefit_type="education_cost",
        benefit_description="장학금",
        evidence_sources=[
            {
                "url": "https://official.test/poster.png",
                "kind": "image",
                "status": "ocr_review",
                "text": "지원동기를 500자 이내로 작성하세요. 재학생만 지원할 수 있습니다.",
                "rawDocumentId": 17,
            }
        ],
    )


def _client(runner: FakeRunner, **kwargs) -> LocalCodexExtractionClient:
    return LocalCodexExtractionClient(
        command="/bin/true",
        schema_path=SCHEMA_PATH,
        model="test-model",
        runner=runner,
        **kwargs,
    )


def test_codex_candidate_uses_read_only_structured_output_and_ocr_image() -> None:
    runner = FakeRunner(
        [
            {
                "record_index": 0,
                "field_kind": "application_prompt",
                "proposed_value": "지원동기",
                "source_index": 0,
                "source_quote": "지원동기를 500자 이내로 작성하세요.",
                "confidence": 0.94,
                "character_limit": 500,
            }
        ]
    )
    client = _client(runner)

    records, stats = client.enrich(
        [_record()],
        image_loader=lambda raw_id: (b"fake-png", "image/png")
        if raw_id == 17
        else None,
    )

    candidate = records[0].extraction_candidates[0]
    assert candidate["field_kind"] == "application_prompt"
    assert candidate["source_kind"] == "ocr"
    assert candidate["raw_document_id"] == 17
    assert candidate["character_limit"] == 500
    assert candidate["extractor_kind"] == "local_codex"
    assert stats["backend"] == "local_codex"
    assert stats["candidates"] == 1
    args = runner.calls[0]["args"]
    assert ["--sandbox", "read-only"] == args[
        args.index("--sandbox") : args.index("--sandbox") + 2
    ]
    assert "--ephemeral" in args
    assert "--ignore-user-config" in args
    assert "--ignore-rules" in args
    assert "--output-schema" in args
    assert runner.calls[0]["images_existed"] is True


def test_codex_batches_multiple_records_into_one_process() -> None:
    runner = FakeRunner([])
    client = _client(runner, max_records_per_batch=20)

    _, stats = client.enrich([_record("notice:1"), _record("notice:2")])

    assert len(runner.calls) == 1
    assert stats["attempted"] == 2
    assert stats["batches_succeeded"] == 1
    assert '<RECORD index="0">' in runner.calls[0]["input"]
    assert '<RECORD index="1">' in runner.calls[0]["input"]


def test_codex_skips_structured_policy_without_ocr() -> None:
    runner = FakeRunner([])
    client = _client(runner)
    policy = ParsedOpportunity(
        **{
            **_record().__dict__,
            "record_kind": "policy",
            "evidence_sources": [
                {
                    "url": "https://official.test/policy",
                    "kind": "html",
                    "status": "collected",
                    "text": "정규식으로 구조화된 장학 제도",
                }
            ],
        }
    )

    _, stats = client.enrich([policy])

    assert runner.calls == []
    assert stats["skipped_not_target"] == 1


def test_codex_candidate_without_matching_source_quote_is_rejected() -> None:
    runner = FakeRunner(
        [
            {
                "record_index": 0,
                "field_kind": "benefit",
                "proposed_value": "매월 100만원",
                "source_index": 0,
                "source_quote": "매월 100만원을 지급합니다.",
                "confidence": 0.99,
                "character_limit": 0,
            }
        ]
    )
    client = _client(runner)

    records, stats = client.enrich([_record()])

    assert records[0].extraction_candidates == []
    assert stats["rejected_candidates"] == 1


def test_codex_records_actual_default_model_from_cli_metadata() -> None:
    runner = FakeRunner(
        [
            {
                "record_index": 0,
                "field_kind": "eligibility",
                "proposed_value": "재학생",
                "source_index": 0,
                "source_quote": "재학생만 지원할 수 있습니다.",
                "confidence": 0.9,
                "character_limit": 0,
            }
        ],
        stderr="model: gpt-test-local\nreasoning effort: low\n",
    )
    client = LocalCodexExtractionClient(
        command="/bin/true",
        schema_path=SCHEMA_PATH,
        runner=runner,
    )

    records, _ = client.enrich([_record()])

    assert records[0].extraction_candidates[0]["model_name"] == "gpt-test-local"


def test_codex_candidates_do_not_change_canonical_fingerprint() -> None:
    record = _record()
    with_candidate = ParsedOpportunity(
        **{
            **record.__dict__,
            "extraction_candidates": [
                {
                    "field_kind": "eligibility",
                    "proposed_value": "재학생",
                }
            ],
        }
    )
    assert record.fingerprint == with_candidate.fingerprint


def test_collect_cli_requires_explicit_codex_flag_and_keeps_llm_alias() -> None:
    parser = build_parser()
    default = parser.parse_args(["collect-benefitlens"])
    codex = parser.parse_args(["collect-benefitlens", "--codex"])
    alias = parser.parse_args(["collect-benefitlens", "--llm"])
    assert default.codex is False
    assert codex.codex is True
    assert alias.codex is True
