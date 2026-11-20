"""A browser reload must preserve evidence already pinned to SHA-256."""
from concurrent.futures import ThreadPoolExecutor
from json import loads
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_company_evidence import preserve_capture


class Ledger:
    day = "2026-11-14"

    def __init__(self, root):
        self.path = root / "korea.sqlite"
        with self.db() as database:
            database.execute(
                "CREATE TABLE candidates(day, source, native, data, PRIMARY KEY(day, source, native))"
            )

    def db(self):
        return sqlite3.connect(self.path, timeout=10)


def test_reviewed_row_and_later_originals_are_retained(tmp_path):
    ledger = Ledger(tmp_path)
    with ledger.db() as db:
        db.execute(
            "INSERT INTO candidates VALUES(?,?,?,?)",
            (ledger.day, "company", "123", '{"detail":{"sha256":"first-original"}}'),
        )

    def add(digest):
        preserve_capture(ledger, "company", "123", {"detail": {"sha256": digest}})

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(add, ("another-original", "third-original", "first-original")))
    with ledger.db() as db:
        pointer = loads(db.execute("SELECT data FROM candidates").fetchone()[0])
        revisions = {row[0] for row in db.execute("SELECT digest FROM company_capture_revisions")}
    assert pointer["detail"]["sha256"] == "first-original"
    assert revisions == {"first-original", "another-original", "third-original"}


def test_first_valid_fetch_replaces_pending_record(tmp_path):
    ledger = Ledger(tmp_path)
    with ledger.db() as db:
        db.execute(
            "INSERT INTO candidates VALUES(?,?,?,?)",
            (ledger.day, "company", "123", '{"state":"held"}'),
        )
    row = {"detail": {"sha256": "reviewable-original"}}
    preserve_capture(ledger, "company", "123", row)
    with ledger.db() as db:
        assert loads(db.execute("SELECT data FROM candidates").fetchone()[0]) == row
