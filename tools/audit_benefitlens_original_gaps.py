"""Explain stored original gaps without making network requests or changing data."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import project


def reason_family(reason: str | None) -> str:
    text = (reason or '').lower()
    checks = [
        ('inherited_source_403_hold', 'http403_manual_source_review'),
        ('url_requires_verified_https_or_public_route', 'requires https or public-route resolution'),
        ('rate_limit_429', '429'),
        ('parser_or_ocr_missing', 'another parser or ocr'),
        ('robots_explicit_path_disallow', 'robots disallows'),
        ('http_404', '404'),
    ]
    for family, marker in checks:
        if marker in text:
            return family
    for family, markers in [
        ('robots_policy_unresolved', ('robots redirect', 'unusable robots policy')),
        ('redirect_requires_login_or_leaves_https', ('redirect enters authentication',)),
        ('tls_certificate_error', ('ssl', 'certificate')),
        ('dns_or_malformed_host', ('nameresolutionerror', 'failed to resolve')),
        ('transient_connection_or_timeout', ('timed out', 'timeout', 'connection')),
        ('direct_http_403', ('403',)),
        ('http_400', ('400',)),
    ]:
        if any(marker in text for marker in markers):
            return family
    return 'other'


def audit(connection) -> dict:
    rows = connection.execute("""SELECT candidate_key, source_url,
        payload->>'original_reason' FROM benefitlens.support_discovery_record
        WHERE record_type='candidate' AND capture_status='original_unresolved'
        ORDER BY candidate_key"""
    ).fetchall()
    counts, schemes = Counter(), Counter()
    hosts, examples = defaultdict(Counter), {}
    for key, url, reason in rows:
        family = reason_family(reason)
        counts[family] += 1
        parsed = urlsplit(url or '')
        hosts[family][parsed.hostname or 'unknown'] += 1
        if family == 'url_requires_verified_https_or_public_route':
            schemes[parsed.scheme or 'missing'] += 1
        examples.setdefault(family, {'candidate_key': key, 'source_url': url,
                                    'stored_reason': reason})
    assert sum(counts.values()) == len(rows)
    return {
        'as_of_kst': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
        'scope': 'stored_record_reasons_not_new_http_verification',
        'total_unresolved_records': len(rows),
        'exclusive_reason_counts': dict(counts),
        'url_scheme_counts_in_url_validation_hold': dict(schemes),
        'top_hosts_by_reason': {k: v.most_common(3) for k, v in hosts.items()},
        'examples': examples,
        'network_requests': 0, 'database_writes': 0,
        'attempt_status_reconstructed': False,
        'limits': [
            'An inherited hold is not a newly observed per-page HTTP 403.',
            'A combined reason is assigned to the first matching family.',
            'Stored reasons do not reconstruct every prior HTTP attempt.',
            'Missing originals are not automatically queued for retries here.',
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with project.connect() as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        result = audit(connection)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'total': result['total_unresolved_records'],
                      'reason_counts': result['exclusive_reason_counts'],
                      'network_requests': 0, 'database_writes': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
