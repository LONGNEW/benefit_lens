"""Build a non-destructive deduplicated view of reviewed and candidate inventories."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from benefitlens_opportunity_groups import role_key
from benefitlens_source_storage import store_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json, single_writer

ROOT = CACHE / 'deduplicated-inventory-v1'
WORK = Path('/home/longnew/Desktop/course_work/tmp')
DROP_QUERY = {'page', 'pageindex', 'pageno', 'cpage', 'article.offset', 'articlelimit',
              'searchkeyword', 'searchcondition', 'searchtype', 'searchval',
              'ref', 'source', 'utm_source', 'utm_medium', 'utm_campaign'}


def canonical_url(value):
    if not value:
        return ''
    try:
        u = urlsplit(value)
        if u.scheme not in ('http', 'https') or not u.hostname:
            return ''
        # Download identities, tenant IDs and unknown query fields are preserved.
        args = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
                if k.casefold() not in DROP_QUERY and not k.casefold().startswith('utm_')]
        return urlunsplit((u.scheme.lower(), u.netloc.lower().removesuffix(':443'),
                           u.path, urlencode(sorted(args)), ''))
    except (ValueError, TypeError):
        return ''


def key(value):
    value = unicodedata.normalize('NFKC', str(value or '')).casefold()
    return re.sub(r'[^\w]+', '', value)


def issuer_name(value):
    value = re.sub(r'^(?:재단법인|주식회사|\(재\)|\(주\))\s*', '', str(value or ''))
    return value.strip()


def program_key(row):
    title, issuer = row.get('title', ''), issuer_name(row.get('issuer', ''))
    # These two aliases are attested by the reviewed issuer documents.
    if '통영시' in title and '저소득' in title and '대학생' in title:
        return ('통영시', '저소득대학생장학금'), 'reviewed_tongyeong_issuer_program_alias'
    if '거창군' in title and '지역출신' in title and '등록금' in title:
        return ('거창군장학회', '지역출신대학생등록금지원'), 'reviewed_geochang_issuer_program_alias'
    if not issuer or len(key(title)) < 8:
        return None, None
    kind = row.get('kind', '')
    funding = bool(re.search(r'장학|등록금|학자금|scholarship|fellowship|bursary|grant', title, re.I))
    if funding:
        clean = re.sub(r'(?<!\d)20\d{2}(?:년도|년|도)?', '', title)
        clean = re.sub(r'\s*(?:신청\s*안내|선발\s*공고|모집\s*공고|안내|공고)\s*$', '', clean)
        value = key(clean)
        ikey = key(issuer)
        if value.startswith(ikey):
            value = value[len(ikey):]
        return (ikey, value), 'same_issuer_named_funding_program'
    # A youth portal or university reposting an internship is not the employer.
    # Employer-role grouping requires an explicitly identified company source.
    if kind == 'company':
        location = row.get('location')
        label = location.get('name', '') if isinstance(location, dict) else location or ''
        city = label.split(',')[0].strip()
        if len(city) >= 3:
            # Use the actual structured city. Preserve role specializations in
            # other parentheses, such as (Machine Learning) or (Security).
            title = re.sub(r'\(\s*' + re.escape(city) + r',\s*'
                           r'(?:United Kingdom|UK|United States|USA|Germany|France|Canada)\s*\)',
                           '', title, flags=re.I)
        role, _ = role_key({'title': title, 'issuer': issuer, 'location': location,
                            'country': row.get('country')})
        if role in ('intern', 'student intern', 'research intern', 'graduate intern'):
            return None, None
        return (key(issuer), key(role)), 'same_issuer_role_with_location_and_cohort_variants'
    return None, None


def group_rows(rows):
    parent = list(range(len(rows)))
    owners, reasons = {}, defaultdict(set)
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    def merge(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[b] = a
    by_url = defaultdict(list)
    for row in rows:
        if url := canonical_url(row.get('url')):
            by_url[url].append(row)
    safe_urls = set()
    for url, members in by_url.items():
        native_ids = defaultdict(set)
        for row in members:
            if row.get('native_scope') and row.get('native'):
                native_ids[row['native_scope']].add(str(row['native']))
        # Some POST detail pages and catalogue homepages serve many programs at
        # one URL. A repeated address cannot override distinct native IDs.
        if any(len(ids) > 1 for ids in native_ids.values()):
            continue
        titles = {key(row.get('title')) for row in members}
        identities = {program_key(row)[0] for row in members}
        if len(members) == 1 or (len(titles) == 1 and '' not in titles) or (
                len(identities) == 1 and None not in identities):
            safe_urls.add(url)
    for i, row in enumerate(rows):
        tokens = []
        if (url := canonical_url(row.get('url'))) in safe_urls:
            tokens.append((('notice_url', url), 'same_notice_url'))
        # The provider scope includes the original institution or ATS board.
        if row.get('native') and row.get('native_scope'):
            tokens.append((('native', row['native_scope'], str(row['native'])), 'same_provider_native'))
        identity, rule = program_key(row)
        if identity:
            tokens.append((('program', *identity), rule))
        for token, reason in tokens:
            if token in owners:
                merge(i, owners[token])
                reasons[i].add(reason)
            else:
                owners[token] = i
    buckets = defaultdict(list)
    for i in range(len(rows)):
        buckets[find(i)].append(i)
    result = []
    for indices in buckets.values():
        members = [rows[i] for i in indices]
        urls = sorted({r['url'] for r in members if r.get('url')})
        signature = sorted(r['record_id'] for r in members)
        result.append({'group_id': 'program-' + sha256(json.dumps(signature).encode()).hexdigest()[:20],
            'title': members[0].get('title'), 'issuer': members[0].get('issuer'),
            'record_count': len(members), 'notice_urls': urls,
            'rules': sorted({rule for i in indices for rule in reasons[i]}),
            'countries': sorted({str(r['country']) for r in members if r.get('country')}),
            'round_variants': sorted({str(r['round']) for r in members if r.get('round')}),
            'historically_reviewed': any(r.get('historically_reviewed') for r in members),
            'current_source_reverification': 'not_performed_by_deduplication',
            'members': members})
    return sorted(result, key=lambda r: r['group_id'])


def normalized(row, record_id, provenance, *, reviewed=False, native_scope=None):
    source = row.get('source') or row.get('source_family') or ''
    return {'record_id': record_id, 'title': row.get('program') or row.get('role') or row.get('title') or row.get('title_hint') or '',
            'issuer': row.get('issuer') or (source if reviewed else '') or '',
            'url': row.get('official_url') or row.get('url') or '',
            'application_url': row.get('application_url'),
            'native': row.get('native_id') or row.get('native'), 'native_scope': native_scope,
            'country': row.get('country'), 'location': row.get('location'),
            'round': row.get('round') or row.get('award_round'),
            'opens': row.get('opens') or row.get('opens_at') or row.get('opens_on'),
            'closes': row.get('closes') or row.get('closes_at') or row.get('closes_on'),
            'source_snapshot': row.get('detail') or row.get('original') or row.get('source_snapshot'),
            'text_snapshot': row.get('text') or row.get('text_projection'),
            'kind': 'company' if any(s in str(source).lower() for s in ('company', 'workday', 'greenhouse')) else row.get('type'),
            'state': row.get('state') or row.get('status'),
            'historically_reviewed': reviewed, 'input': provenance}


def reviewed_rows():
    ref = json.loads((WORK/'benefitlens_academicworks_20261114/combined_ref.json').read_text())['record']
    raw = Path(ref['path']).read_bytes()
    if sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('Reviewed index hash mismatch')
    index = json.loads(raw)
    out, inputs = [], []
    for item in index['prior_inputs']:
        data = Path(item['path']).read_bytes()
        if sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Reviewed source hash mismatch')
        obj = json.loads(data)
        rows = obj.get('rows')
        if rows is None:
            rows = list(obj.values())
        for i, row in enumerate(rows):
            out.append(normalized(row, f"{item['sha256']}:{i}", item, reviewed=True))
        inputs.append(item)
    for i, row in enumerate(index['rows']):
        out.append(normalized(row, f"{ref['sha256']}:{i}", ref, reviewed=True))
    pilot_ref = json.loads((WORK/'benefitlens_catalogue_expansion_20261115/review_ref.json').read_text())
    data = Path(pilot_ref['path']).read_bytes()
    if sha256(data).hexdigest() != pilot_ref['sha256']:
        raise ValueError('Pilot review hash mismatch')
    for i, row in enumerate(json.loads(data)['rows']):
        out.append(normalized(row, f"{pilot_ref['sha256']}:{i}", pilot_ref, reviewed=True))
    return out, inputs + [ref, pilot_ref]


def candidate_rows(day):
    out, inputs = [], []
    roots = [p for p in CACHE.rglob('korea.sqlite') if not any(
        token.startswith(('test', 'storage-validation')) for token in p.relative_to(CACHE).parts)]
    for dbpath in roots:
        with sqlite3.connect('file:' + str(dbpath) + '?mode=ro', uri=True) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidates'").fetchone():
                continue
            for source, native, raw in db.execute('SELECT source,native,data FROM candidates WHERE day=?', (day,)):
                row = json.loads(raw)
                row['native'] = native
                out.append(normalized(row, f'{dbpath}:{day}:{source}:{native}',
                    {'path': str(dbpath), 'day': day, 'source': source, 'native': native}, native_scope=source))
        inputs.append({'sqlite': str(dbpath), 'day': day})
    dbpath = CACHE/'python-campaign-v1/frontier.sqlite'
    with sqlite3.connect('file:' + str(dbpath) + '?mode=ro', uri=True) as db:
        for source, jid, payload, state in db.execute(
                "SELECT source,id,payload,state FROM jobs WHERE day=? AND stage='detail'", (day,)):
            data = json.loads(payload)
            row = data.get('candidate', {}) | {'state': state}
            row.setdefault('url', data.get('url', ''))
            out.append(normalized(row, f'{dbpath}:{jid}', {'path': str(dbpath), 'job_id': jid}, native_scope=source))
    # Split per-job API records are not represented in the capture ledger's candidates table.
    p = CACHE/'public-board-expansion-v1'/day/'review_candidates.json'
    if p.exists():
        for i, row in enumerate(json.loads(p.read_text())):
            row['issuer'] = row['source']
            row['type'] = 'company'
            out.append(normalized(row, f'{p}:{i}', {'path': str(p), 'index': i}, native_scope=row['source']))
    return out, inputs


def erasmus_documents(day):
    """Deduplicate document captures while retaining every seed association."""
    path = CACHE/'erasmus-campaign-v1'/day/'status.json'
    if not path.exists():
        return [], 0
    groups, count = {}, 0
    for seed_id, program in json.loads(path.read_text())['programmes'].items():
        for doc in program.get('documents', []):
            count += 1
            identity = (canonical_url(doc['url']), doc['original']['sha256'])
            group = groups.setdefault(identity, {'url': doc['url'], 'original': doc['original'],
                'text': doc['text'], 'title': doc.get('title'), 'seed_associations': []})
            group['seed_associations'].append({'seed_id': seed_id, 'seed_url': program['seed_url']})
    return list(groups.values()), count


def run():
    ROOT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(KST)
    with single_writer(ROOT):
        reviewed, references = reviewed_rows()
        reviewed_groups = group_rows(reviewed)
        candidates, databases = candidate_rows(now.date().isoformat())
        candidate_groups = group_rows(candidates)
        document_groups, document_count = erasmus_documents(now.date().isoformat())
        records = {}
        for label, groups in [('reviewed_groups', reviewed_groups), ('candidate_groups', candidate_groups),
                              ('erasmus_document_groups', document_groups)]:
            raw = json.dumps(groups, ensure_ascii=False, sort_keys=True).encode()
            records[label] = store_source_bytes(raw, ROOT/'blobs', suffix='.json')
        status = {'at_kst': now.isoformat(), 'reviewed_input_records': len(reviewed),
            'reviewed_program_families': len(reviewed_groups),
            'reviewed_duplicate_records': len(reviewed) - len(reviewed_groups),
            'candidate_input_records': len(candidates), 'candidate_program_groups': len(candidate_groups),
            'candidate_duplicate_records': len(candidates) - len(candidate_groups),
            'erasmus_document_records': document_count,
            'erasmus_unique_document_snapshots': len(document_groups),
            'erasmus_duplicate_document_records': document_count - len(document_groups),
            'reviewed_inputs': references, 'candidate_inputs': databases, 'records': records,
            'current_open_verified_by_this_run': 0, 'production_database_writes': 0,
            'scope': 'deduplicated view; original records preserved; candidate groups still require source review'}
        atomic_json(ROOT/'status.json', status)
        print(json.dumps({k:v for k,v in status.items() if k not in ('reviewed_inputs','candidate_inputs','records')}, ensure_ascii=False))


if __name__ == '__main__':
    run()
