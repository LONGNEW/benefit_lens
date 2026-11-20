"""Preserve every company capture and keep the first reviewable row stable for its KST day."""
import json

from run_benefitlens_campaign import encoded


def preserve_capture(ledger, source, native, row):
    """Write immutable revisions and seed the current day's review pointer atomically."""
    with ledger.db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS company_capture_revisions (
                day TEXT NOT NULL,
                source TEXT NOT NULL,
                native TEXT NOT NULL,
                digest TEXT NOT NULL,
                data TEXT NOT NULL,
                PRIMARY KEY(day, source, native, digest)
            )
        """)
        db.execute("BEGIN IMMEDIATE")
        key = (ledger.day, source, native)
        existing = db.execute(
            "SELECT data FROM candidates WHERE day=? AND source=? AND native=?", key
        ).fetchone()
        revisions = [row]
        if existing:
            previous = json.loads(existing[0])
            if previous.get("detail", {}).get("sha256"):
                revisions.append(previous)
        for revision in revisions:
            digest = revision["detail"]["sha256"]
            db.execute(
                "INSERT OR IGNORE INTO company_capture_revisions VALUES(?,?,?,?,?)",
                (*key, digest, encoded(revision)),
            )
        if not existing:
            db.execute("INSERT INTO candidates VALUES(?,?,?,?)", (*key, encoded(row)))
        elif len(revisions) == 1:
            db.execute(
                "UPDATE candidates SET data=? WHERE day=? AND source=? AND native=?",
                (encoded(row), *key),
            )
