from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import db
from .benefitlens import collect


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MIGRATION = PROJECT_ROOT / "migrations"
DEFAULT_BENEFITLENS_CONFIG = PROJECT_ROOT / "config" / "benefitlens_sources.json"
DEFAULT_EXPORT = PROJECT_ROOT / "data" / "exports" / "collection_summary.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BenefitLens 수집 및 PostgreSQL 적재 도구")
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrate_parser = subparsers.add_parser("migrate")
    migrate_parser.add_argument("--migration", type=Path, default=DEFAULT_MIGRATION)

    benefitlens_parser = subparsers.add_parser("collect-benefitlens")
    benefitlens_parser.add_argument("--config", type=Path, default=DEFAULT_BENEFITLENS_CONFIG)
    benefitlens_parser.add_argument("--source", action="append", help="특정 공식 출처만 재수집, 반복 지정 가능")
    benefitlens_parser.add_argument(
        "--codex",
        "--llm",
        dest="codex",
        action="store_true",
        help="로컬 Codex가 만든 원문 인용 검증 후보를 추가 저장",
    )

    export_parser = subparsers.add_parser("export-summary")
    export_parser.add_argument("--output", type=Path, default=DEFAULT_EXPORT)
    subparsers.add_parser("classify-benefitlens", help="저장된 후보의 금전 지원을 근거와 함께 재분류")
    reprocess_parser = subparsers.add_parser("reprocess-benefitlens", help="저장 원문만 재처리, 새 네트워크 수집 없음")
    reprocess_parser.add_argument("--config", type=Path, default=DEFAULT_BENEFITLENS_CONFIG)
    review_export = subparsers.add_parser("review-packet", help="현재 공고 버전의 검토 입력 파일 생성")
    review_export.add_argument("--version", required=True)
    review_export.add_argument("--output", type=Path, required=True)
    review_apply = subparsers.add_parser("review-benefitlens", help="원문과 버전 검증 후 후보 승격 또는 검토 결정")
    review_apply.add_argument("--packet", type=Path, required=True)
    review_apply.add_argument("--reviewer", required=True)
    review_apply.add_argument("--apply", action="store_true", help="명시한 검토 결정을 실제 저장, 기본은 검증만")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "migrate":
        db.migrate(args.migration)
        result: object = {"status": "migrated", "migration": str(args.migration)}
    elif args.command == "collect-benefitlens":
        result = collect(
            args.config,
            set(args.source) if args.source else None,
            codex_enabled=args.codex,
        )
    elif args.command == "export-summary":
        result = db.write_summary(args.output)
    elif args.command == "classify-benefitlens":
        result = db.classify_saved_opportunities()
    elif args.command == "reprocess-benefitlens":
        from .reprocess import reprocess_saved
        result = reprocess_saved(args.config)
    elif args.command == "review-packet":
        from .review import make_packet
        result = make_packet(args.version)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
    elif args.command == "review-benefitlens":
        from .review import process_packet
        result = process_packet(json.loads(args.packet.read_text(encoding="utf-8")), args.reviewer, args.apply)
    else:
        raise AssertionError(f"unsupported command: {args.command}")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
