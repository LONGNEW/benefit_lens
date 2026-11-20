#!/usr/bin/env python3
"""Load BenefitLens v1 JSON rows into disposable PostgreSQL and verify the result."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from benefitlens_source_storage import read_source_bytes, read_source_text
import re
import subprocess
import time


DEFAULT = Path(__file__).resolve().parents[1] / 'proposal/결과물/NVIDIA_2027-2028'
KEYS = {
    'program': ('program_id',),
    'opportunity_round': ('program_id', 'round_id'),
    'source_document': ('document_id',),
    'evidence': ('evidence_id',),
    'claim': ('claim_id',),
    'claim_evidence': ('claim_id', 'evidence_id'),
    'experience_case': ('case_id',),
    'case_document': ('case_id', 'document_id', 'document_role'),
    'writing_pattern': ('pattern_id',),
    'pattern_evidence': ('pattern_id', 'evidence_id'),
}
SOURCE_STORAGE_FIELDS = {'raw_storage', 'text_storage'}
OFFICIAL = "('official_notice','official_guideline','official_form','application_portal')"
LOCATOR = re.compile(r'(?:L|(?:text\s+)?lines?[:\s]+)(\d+)(?:\s*[-–]\s*L?(\d+))?', re.I)
INSERT_BLOCK_BYTES = 512 * 1024


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def read_json(path):
    def distinct_object(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'{path.name}: duplicate JSON key {key}')
            result[key] = value
        return result

    return json.loads(read_source_text(path), object_pairs_hook=distinct_object)


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_rows(data_dir, order, columns):
    rows = {t: {} for t in order}
    duplicates = 0
    for path in sorted(data_dir.glob('*.json')):
        block = read_json(path)
        require(isinstance(block, dict), f'{path.name}: expected table-to-rows object')
        for table, records in block.items():
            require(table in rows and isinstance(records, list), f'{path.name}: invalid table {table}')
            for record in records:
                allowed = set(columns[table]) | (SOURCE_STORAGE_FIELDS if table == 'source_document' else set())
                require(isinstance(record, dict) and set(record) <= allowed,
                        f'{path.name}: unexpected column(s) in {table}')
                key = tuple(record.get(k) for k in KEYS[table])
                require(all(isinstance(v, str) and v.strip() for v in key), f'{path.name}: invalid {table} key')
                current = compact(record)
                if key in rows[table]:
                    require(compact(rows[table][key]) == current, f'{path.name}: conflicting {table} key {key}')
                    duplicates += 1
                rows[table][key] = record
    require(rows['opportunity_round'], 'no opportunity_round rows found')
    return {t: list(records.values()) for t, records in rows.items()}, duplicates


def validate_sources(records, root):
    texts = {}
    file_hashes, text_lines = {}, {}
    counts = Counter()

    def identity(path):
        s = path.stat()
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

    for doc in records['source_document']:
        key = doc['document_id']
        mode = doc.get('capture_mode')
        require(mode in ('full_document', 'public_web_excerpt'), f'{key}: invalid capture_mode')
        for kind in ('raw', 'text'):
            path = doc.get(f'{kind}_path')
            checksum = doc.get(f'{kind}_sha256')
            if kind == 'raw' and mode == 'public_web_excerpt':
                require(path is None and checksum is None, f'{key}: excerpt cannot claim original bytes')
                continue
            require(isinstance(path, str) and path and isinstance(checksum, str), f'{key}: missing {kind} artifact')
            f = (root / path).resolve()
            require(f.is_file(), f'{key}: {kind} file/hash mismatch: {f}')
            stamp = identity(f)
            if f not in file_hashes:
                file_hashes[f] = (stamp, hashlib.sha256(read_source_bytes(f, storage=doc.get(kind + '_storage'))).hexdigest())
            elif doc.get(kind + '_storage') is not None:
                read_source_bytes(f, storage=doc[kind + '_storage'])
            require(file_hashes[f] == (stamp, checksum), f'{key}: {kind} file/hash mismatch: {f}')
            counts[f'{kind}_hashes'] += 1
            if kind == 'text':
                # Delivery locators count LF lines, not PDF form-feed separators.
                if f not in text_lines:
                    text_lines[f] = read_source_text(f).split('\n')
                texts[key] = text_lines[f]
            require(identity(f) == stamp, f'{key}: artifact changed during validation: {f}')
    for e in records['evidence']:
        key = e['evidence_id']
        lines = texts.get(e.get('document_id'))
        locator, text = e.get('locator'), e.get('quote')
        m = LOCATOR.fullmatch(locator) if isinstance(locator, str) else None
        require(lines is not None and m is not None, f'{key}: missing source or unsupported locator')
        start, end = int(m[1]), int(m[2] or m[1])
        quote = ' '.join(text.split()) if isinstance(text, str) else ''
        actual = ' '.join(' '.join(lines[start - 1:end]).split())
        require(1 <= start <= end <= len(lines) and quote and quote in actual,
                f'{key}: quote not found at stated locator')
        counts['spans'] += 1
    return {k: counts[k] for k in ('raw_hashes', 'text_hashes', 'spans')} | {
        'unique_files_hashed': len(file_hashes), 'unique_text_files_read': len(text_lines),
    }


def insert_blocks(rows):
    """Yield escaped JSON arrays without making one statement for an entire table."""
    part, size = [], 2
    for row in rows:
        escaped = compact(row).replace("'", "''")
        added = len(escaped.encode('utf-8')) + bool(part)
        if part and size + added > INSERT_BLOCK_BYTES:
            yield '[' + ','.join(part) + ']'
            part, size = [], 2
            added -= 1  # No comma before the first row of a new block.
        part.append(escaped)
        size += added
    if part:
        yield '[' + ','.join(part) + ']'


QA = f"""
SELECT 'reviewed_claim_missing_official_grounding' AS violation, c.claim_id AS id
FROM claim c WHERE c.review_state='reviewed' AND NOT EXISTS (
 SELECT 1 FROM claim_evidence l JOIN evidence e USING(evidence_id)
 WHERE l.claim_id=c.claim_id AND e.source_role IN {OFFICIAL})
UNION ALL
SELECT 'reviewed_claim_has_nonofficial_grounding', c.claim_id
FROM claim c JOIN claim_evidence l USING(claim_id) JOIN evidence e USING(evidence_id)
WHERE c.review_state='reviewed' AND e.source_role NOT IN {OFFICIAL}
UNION ALL
SELECT 'pattern_missing_target_or_grounding', p.pattern_id
FROM writing_pattern p LEFT JOIN claim c ON c.claim_id=p.target_claim_id
WHERE c.kind IS DISTINCT FROM 'submission' OR NOT EXISTS (
 SELECT 1 FROM pattern_evidence l WHERE l.pattern_id=p.pattern_id)
UNION ALL
SELECT 'pattern_evidence_scope_mismatch', l.pattern_id
FROM pattern_evidence l JOIN evidence e USING(evidence_id)
WHERE e.round_id IS DISTINCT FROM l.source_round_id OR
 (l.purpose='case_observation' AND l.case_document_role<>e.source_role) OR
 (l.purpose<>'case_observation' AND e.source_role NOT IN {OFFICIAL})
UNION ALL
SELECT 'pattern_case_scope_mislabeled', p.pattern_id
FROM writing_pattern p JOIN pattern_evidence l USING(pattern_id)
JOIN experience_case x USING(case_id)
WHERE p.scope_status='official_only' OR
 (p.scope_status='same_round' AND
  (x.program_id<>p.program_id OR x.experience_round_id IS DISTINCT FROM p.target_round_id)) OR
 (p.scope_status='historical_transfer' AND
  (x.program_id<>p.program_id OR x.experience_round_id=p.target_round_id))
"""

ROUNDS = """
SELECT r.*, p.title, p.issuer, p.opportunity_type,
CASE WHEN EXISTS (
 SELECT 1 FROM claim c JOIN claim_evidence l USING(claim_id)
 JOIN evidence e USING(evidence_id) JOIN source_document d ON d.document_id=e.document_id
 WHERE (c.program_id,c.round_id)=(r.program_id,r.round_id)
 AND c.review_state='reviewed' AND d.capture_mode='full_document'
 AND e.source_role IN ('official_notice','official_guideline')
) THEN 'source_backed' ELSE 'summary_only' END AS basis,
COALESCE((SELECT jsonb_agg(to_jsonb(c)||jsonb_build_object('evidence_ids',
  COALESCE((SELECT jsonb_agg(l.evidence_id ORDER BY l.evidence_id)
    FROM claim_evidence l WHERE l.claim_id=c.claim_id),'[]'::jsonb)) ORDER BY c.claim_id)
 FROM claim c WHERE (c.program_id,c.round_id)=(r.program_id,r.round_id)), '[]'::jsonb) AS claims
FROM opportunity_round r JOIN program p USING(program_id)
"""

PATTERNS = """
SELECT p.*, r.opportunity_id, r.notice_version, c.field AS target_field,
COALESCE((SELECT jsonb_agg(jsonb_build_object(
 'evidence_id',e.evidence_id,'case_id',l.case_id,'purpose',l.purpose,
 'document_id',d.document_id,'url',d.url,'capture_mode',d.capture_mode,
 'source_role',e.source_role,'source_program_id',e.program_id,'source_round_id',e.round_id,
 'text_sha256',d.text_sha256,'locator',e.locator,'quote',e.quote
) ORDER BY e.evidence_id) FROM pattern_evidence l JOIN evidence e USING(evidence_id)
JOIN source_document d ON d.document_id=e.document_id WHERE l.pattern_id=p.pattern_id),'[]'::jsonb) AS evidence
FROM writing_pattern p JOIN opportunity_round r
 ON (r.program_id,r.round_id)=(p.program_id,p.target_round_id)
LEFT JOIN claim c ON c.claim_id=p.target_claim_id
"""

CASES = """
SELECT x.*, o.quote AS outcome_quote, od.url AS outcome_url,
od.capture_mode AS outcome_capture_mode,
COALESCE((SELECT jsonb_agg(to_jsonb(cd)||jsonb_build_object('url',d.url,'capture_mode',d.capture_mode)
 ORDER BY cd.document_id,cd.document_role) FROM case_document cd JOIN source_document d USING(document_id)
 WHERE cd.case_id=x.case_id),'[]'::jsonb) AS case_documents
FROM experience_case x LEFT JOIN evidence o ON o.evidence_id=x.outcome_evidence_id
LEFT JOIN source_document od ON od.document_id=o.document_id
"""

LIVE_IDENTITY = """
SELECT
 (SELECT count(*) FROM opportunity_round) AS round_rows,
 (SELECT count(DISTINCT (program_id,round_id)) FROM opportunity_round) AS distinct_round_keys,
 (SELECT count(*) FROM opportunity_round WHERE status='open') AS open_rows,
 (SELECT count(DISTINCT checked_on) FROM opportunity_round) AS distinct_check_dates,
 (SELECT count(*) FROM claim WHERE field='student_scope') AS student_scope_claims,
 (SELECT count(DISTINCT (value->>'source_id',value->>'native_id')) FROM claim
   WHERE field='student_scope' AND value->>'source_id' IS NOT NULL AND value->>'native_id' IS NOT NULL) AS distinct_provider_native,
 (SELECT count(DISTINCT value->>'source_id') FROM claim WHERE field='student_scope') AS distinct_providers,
 (SELECT count(*) FROM claim WHERE field='student_scope' AND value->>'student_scope_verified'='true') AS student_scope_verified_rows,
 (SELECT count(*) FROM claim WHERE field='student_scope' AND value->>'full_eligibility_verified'='true') AS full_eligibility_verified_rows,
 (SELECT count(*) FROM claim WHERE field='verified_application_period' AND review_state='reviewed') AS verified_application_period_claims,
 (SELECT count(*) FROM claim WHERE field='verified_application_period' AND value->'application_period'->>'kind'='fixed_deadline') AS fixed_deadline_periods,
 (SELECT count(*) FROM claim WHERE field='verified_application_period' AND value->'application_period'->>'kind'='explicit_rolling') AS explicit_rolling_periods,
 (SELECT count(*) FROM opportunity_round r WHERE NOT EXISTS (SELECT 1 FROM claim c
  WHERE (c.program_id,c.round_id)=(r.program_id,r.round_id) AND c.field='verified_application_period'
  AND c.review_state='reviewed' AND c.value->'application_period'->>'verification_state'='verified')) AS rounds_without_verified_period,
 (SELECT count(*) FROM opportunity_round r JOIN claim c USING(program_id,round_id)
  WHERE c.field='verified_application_period' AND r.closes_on IS DISTINCT FROM
  NULLIF(c.value->'application_period'->>'closes_on','')::date) AS period_date_mismatches,
 (SELECT count(*) FROM experience_case) AS experience_cases,
 (SELECT count(*) FROM writing_pattern) AS writing_patterns
"""


def postgres(schema_path, ns, order, records):
    cid = subprocess.check_output([
        'docker', 'run', '--rm', '-d', '--pull=never', '--network=none',
        '--tmpfs', '/var/lib/postgresql/data', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
        '-e', 'POSTGRES_DB=benefitlens_batch', 'postgres:16-alpine',
    ], text=True, timeout=30).strip()

    def sql(statement):
        prefix = f'SET search_path TO {ns}, pg_catalog; SET standard_conforming_strings TO on;\n'
        r = subprocess.run([
            'docker', 'exec', '-i', cid, 'psql', '-X', '-U', 'postgres',
            '-d', 'benefitlens_batch', '-qAt', '-v', 'ON_ERROR_STOP=1', '-v', 'VERBOSITY=verbose',
        ], input=prefix + statement, text=True, capture_output=True, timeout=120)
        if r.returncode:
            raise RuntimeError(r.stderr.strip())
        return r.stdout.strip()

    def array(query, key):
        return json.loads(sql(f"SELECT COALESCE(jsonb_agg(to_jsonb(q) ORDER BY {key}),'[]'::jsonb) FROM ({query}) q;"))

    def insert(replay=False):
        chunks = []
        for table in order:
            conflict = ' ON CONFLICT DO NOTHING' if replay else ''
            sql_rows = ({k:v for k,v in row.items() if k not in SOURCE_STORAGE_FIELDS}
                        if table == 'source_document' else row for row in records[table])
            for block in insert_blocks(sql_rows):
                chunks.append(f"INSERT INTO {ns}.{table} SELECT * FROM jsonb_populate_recordset(NULL::{ns}.{table},'{block}'::jsonb){conflict};")
        sql('BEGIN;\n' + '\n'.join(chunks) + '\nCOMMIT;')

    def snapshot():
        branches = [f"SELECT '{t}' AS tbl, count(*) AS n, md5(COALESCE(string_agg(to_jsonb(x)::text,chr(10) ORDER BY to_jsonb(x)::text),'')) AS checksum FROM {ns}.{t} x" for t in order]
        return {r['tbl']: (r['n'], r['checksum']) for r in array(' UNION ALL '.join(branches), 'q.tbl')}

    try:
        end = time.monotonic() + 25
        while True:
            try:
                version = sql("SELECT current_setting('server_version');")
                break
            except RuntimeError:
                if time.monotonic() >= end:
                    raise
                time.sleep(.4)
        require(version.startswith('16.'), f'unexpected PostgreSQL version {version}')
        if ns != 'public':
            sql(f'CREATE SCHEMA {ns};')
        sql(schema_path.read_text())
        insert()
        first = snapshot()
        require(all(first[t][0] == len(records[t]) for t in order), 'load row counts differ from input')
        insert(replay=True)
        require(snapshot() == first, 'same-packet replay changed the database')
        violations = array(QA, 'q.violation,q.id')
        require(not violations, f'delivery contract violations: {violations[:5]}')
        return version, {t: v[0] for t, v in first.items()}, {
            'rounds': array(ROUNDS, 'q.opportunity_id'),
            'patterns': array(PATTERNS, 'q.opportunity_id,q.pattern_id'),
            'experiences': array(CASES, 'q.program_id,q.case_id'),
            'live_identity_audit': array(LIVE_IDENTITY, 'q.round_rows'),
        }
    finally:
        subprocess.run(['docker', 'stop', '-t', '2', cid], capture_output=True, check=False, timeout=20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--artifact-root', type=Path, help='Relative source path base; defaults to parent of data-dir')
    parser.add_argument('--schema', type=Path, default=DEFAULT / 'schema.sql')
    parser.add_argument('--contract', type=Path, default=DEFAULT / 'contract.json')
    parser.add_argument('--metadata', type=Path, help='Optional JSON opportunity_basis map keyed by opportunity_id')
    a = parser.parse_args()
    try:
        require(a.output.resolve() != a.data_dir.resolve(), 'output must differ from the input directory')
        c = read_json(a.contract)
        ns, order = c['schema'], c['load_order']
        require(re.fullmatch('[a-z][a-z_0-9]*', ns) and tuple(order) == tuple(KEYS), 'not a supported v1 table contract')
        records, duplicates = load_rows(a.data_dir, order, c['column_order'])
        source_checks = validate_sources(records, a.artifact_root or a.data_dir.parent)
        metadata = read_json(a.metadata) if a.metadata else {}
        require(isinstance(metadata, dict), 'metadata must be a JSON object')
        overrides = metadata.get('opportunity_basis', {})
        known = {r['opportunity_id'] for r in records['opportunity_round']}
        require(isinstance(overrides, dict) and set(overrides) <= known and
                all(b in ('source_backed', 'summary_only') for b in overrides.values()),
                'invalid opportunity_basis metadata')
        version, counts, results = postgres(a.schema, ns, order, records)
        for r in results['rounds']:
            basis = overrides.get(r['opportunity_id'], r['basis'])
            require(basis != 'source_backed' or r['basis'] == 'source_backed',
                    f"{r['opportunity_id']}: source_backed needs reviewed same-round official evidence")
            r['basis'] = basis
        report = {
            'postgres_version': version, 'rows': counts, **source_checks,
            'schema_sha256': digest(a.schema), 'packet_sha256': hashlib.sha256(compact(records).encode()).hexdigest(),
            'exact_input_duplicates_collapsed': duplicates, 'same_input_replay_unchanged': True,
            'contract_violation_rows': 0, 'round_basis_counts': dict(Counter(r['basis'] for r in results['rounds'])),
            'live_identity_audit': results['live_identity_audit'][0],
            'basis_definition': 'At least one reviewed claim with complete same-round official notice/guidance evidence; not a completeness assertion.',
            'scope': 'disposable PostgreSQL, no published ports and no network; no production writes',
        }
        a.output.mkdir(parents=True, exist_ok=True)
        artifacts = {f'{k}.result.json': v for k, v in results.items()}
        artifacts['verification.json'] = report
        for filename, payload in artifacts.items():
            target = a.output / filename
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
            temporary.replace(target)
        print(compact(report))
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'benefitlens_batch_db: {exc}\n')


if __name__ == '__main__':
    main()
