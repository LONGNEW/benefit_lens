"""Convert captured USAJOBS Historic JOA pages to reviewed BenefitLens v1 shards."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from benefitlens_source_storage import read_source_bytes,store_source_bytes

API = "https://data.usajobs.gov/api/historicjoa"
KST = ZoneInfo("Asia/Seoul")
STUDENT_TRAINEE = re.compile(r"\bstudent[\s-]+trainee\b", re.I)
HIGH_SCHOOL_ONLY = re.compile(r"\bhigh[\s-]+school(?:\s+students?)?\s+only\b", re.I)


@dataclass
class Posting:
    source: dict
    line: int
    row: dict
    control: str
    agency: str
    announcement: str
    opened: date
    closed: date


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_pages(manifest):
    for page in json.loads(manifest.read_text(encoding="utf-8")):
        url = page["request_url"]
        parsed = urlsplit(url)
        if f"{parsed.scheme}://{parsed.netloc}{parsed.path.lower()}" != API:
            raise ValueError(f"Unexpected source URL: {url}")
        raw_path = Path(page["raw_path"]).resolve(strict=True)
        raw = read_source_bytes(raw_path,storage=page.get('raw_storage'))
        digest = sha256(raw).hexdigest()
        if digest != page["raw_sha256"]:
            raise ValueError(f"Raw source hash mismatch: {raw_path}")
        rows = json.loads(raw)["data"]
        if not isinstance(rows, list) or len(rows) != page["received_rows"]:
            raise ValueError(f"Unexpected row count: {raw_path}")
        # One physical line per array item, including unselected items, in source order.
        lines = "".join(compact(row) + "\n" for row in rows).encode("utf-8")
        text_sha = sha256(lines).hexdigest()
        text_ref = store_source_bytes(lines,raw_path.parent/'text-blobs',suffix='.txt')
        yield {
            "document_id": "usajobs-historic-" + sha256(f"{url}\n{digest}".encode()).hexdigest()[:24],
            "title": f"USAJOBS Historic JOA data, series {page['series']}",
            "url": url, "kind": "json", "capture_mode": "full_document",
            "fetched_at": page["checked_at"], "raw_path": str(raw_path), "raw_sha256": digest,
            "text_path": text_ref['path'], "text_sha256": text_sha,"text_storage":text_ref['storage'],
            **({'raw_storage':page['raw_storage']} if 'raw_storage' in page else {}),
        }, rows


def student_posting(source, line, row):
    if not any(path.get("hiringPath") == "Students" for path in (row.get("hiringpaths") or [])):
        return None, "without_student_hiring_path"
    title = row.get("positionTitle") or ""
    if not STUDENT_TRAINEE.search(title) or HIGH_SCHOOL_ONLY.search(
        f"{title} {row.get('whoMayApply') or ''}"
    ):
        return None, "outside_student_trainee_scope"
    if "cancel" in (row.get("positionOpeningStatus") or "").casefold():
        return None, "canceled"

    control = str(row.get("usajobsControlNumber") or "")
    agency = (row.get("hiringAgencyCode") or "").strip().casefold()
    announcement = (row.get("announcementNumber") or "").strip().casefold()
    issuer = row.get("hiringAgencyName") or row.get("hiringDepartmentName")
    if not (control.isascii() and control.isdecimal() and agency and announcement and issuer):
        return None, "incomplete_identity"
    try:
        opened = date.fromisoformat(row["positionOpenDate"])
        closed = date.fromisoformat(row["positionCloseDate"])
        if row.get("positionExpireDate"):
            closed = min(closed, date.fromisoformat(row["positionExpireDate"]))
    except (KeyError, TypeError, ValueError):
        return None, "invalid_dates"
    checked = datetime.fromisoformat(source["fetched_at"]).astimezone(KST).date()
    if not opened <= closed < checked:
        return None, "outside_historical_dates"
    return Posting(source, line, row, control, agency, announcement, opened, closed), None


def shard(posting):
    row = posting.row
    program_id = f"usajobs-joa-{posting.control}"
    round_id = posting.control
    keys = {"program_id": program_id, "round_id": round_id}
    claim_id = f"{program_id}-students"
    evidence_id = f"{program_id}-students-evidence"
    checked = datetime.fromisoformat(posting.source["fetched_at"]).astimezone(KST).date()
    return {
        "program": [{
            "program_id": program_id, "title": row["positionTitle"],
            "issuer": row.get("hiringAgencyName") or row["hiringDepartmentName"],
            "opportunity_type": "internship" if row.get("appointmentType") == "Internships" else "other",
        }],
        "opportunity_round": [{
            **keys, "opportunity_id": program_id,
            "notice_version": f"usajobs-historic-v1-{posting.source['raw_sha256'][:12]}",
            "status": "historical", "official_url": f"{API}?USAJOBSControlNumbers={posting.control}",
            "application_url": None, "opens_on": posting.opened.isoformat(),
            "closes_on": posting.closed.isoformat(), "announces_on": None, "checked_on": checked.isoformat(),
        }],
        "claim": [{
            **keys, "claim_id": claim_id, "kind": "eligibility", "field": "student_hiring_path",
            "at": "application", "review_state": "reviewed",
            "value": {
                "hiring_path": "Students", "full_eligibility_verified": False,
                "appointment_type": row.get("appointmentType"), "agency_code": row["hiringAgencyCode"],
                "announcement_number": row["announcementNumber"],
                "control_number": posting.control, "source_position": posting.line - 1,
                "opening_status": row.get("positionOpeningStatus"),
                "job_series": [category["series"] for category in (row.get("jobcategories") or [])],
            },
        }],
        "evidence": [{
            **keys, "evidence_id": evidence_id, "document_id": posting.source["document_id"],
            "source_role": "official_notice", "locator": f"L{posting.line}", "quote": "Students",
        }],
        "claim_evidence": [{**keys, "claim_id": claim_id, "evidence_id": evidence_id}],
    }


def build(manifest, count, output):
    candidates = []
    excluded = Counter()
    received = 0
    for source, rows in source_pages(manifest):
        received += len(rows)
        for line, row in enumerate(rows, 1):
            posting, reason = student_posting(source, line, row)
            if posting is None:
                excluded[reason] += 1
            else:
                candidates.append(posting)

    # Prefer the latest posting when the agency and announcement number repeat.
    candidates.sort(key=lambda item: (item.opened, int(item.control)), reverse=True)
    controls, announcements = set(), set()
    unique = []
    for item in candidates:
        key = (item.agency, item.announcement)
        if item.control in controls or key in announcements:
            excluded["duplicate"] += 1
            continue
        controls.add(item.control)
        announcements.add(key)
        unique.append(item)
    if not 0 < count <= len(unique):
        raise ValueError(f"Requested {count} records, {len(unique)} eligible distinct records available")

    selected = unique[:count]
    documents = {item.source["document_id"]: item.source for item in selected}
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temp:
        stage = Path(temp)
        for item in selected:
            (stage / f"joa-{item.control}.json").write_text(compact(shard(item)) + "\n", encoding="utf-8")
        (stage / "sources.json").write_text(compact({"source_document": list(documents.values())}) + "\n",
                                            encoding="utf-8")
        if output.exists():
            existing = {path.name for path in output.iterdir()}
            if existing != {path.name for path in stage.iterdir()} or any(
                (output / path.name).read_bytes() != path.read_bytes() for path in stage.iterdir()
            ):
                raise ValueError(f"Output already contains a different batch: {output}")
        else:
            stage.rename(output)
    return {
        "received": received, "eligible_distinct": len(unique), "excluded": dict(excluded),
        "selected": len(selected), "selected_issuers": len({item.agency for item in selected}),
        "selected_open_years": dict(sorted(Counter(item.opened.year for item in selected).items())),
        "selected_appointment_types": dict(Counter((item.row.get("appointmentType") or "unknown") for item in selected)),
        "latest_close": max(item.closed for item in selected).isoformat(),
        "earliest_close": min(item.closed for item in selected).isoformat(),
        "source_documents": list(documents.values()), "output": str(output.resolve()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.manifest, args.count, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
