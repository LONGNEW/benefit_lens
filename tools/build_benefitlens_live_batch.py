"""Build an evidence-linked cohort from same-day official open student postings."""
from __future__ import annotations

import argparse
from collections import Counter, deque
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from benefitlens_batch_db import LOCATOR, validate_sources
from run_benefitlens_batch import KEYS, export_shards, validate_open_cohort
from benefitlens_source_storage import read_source_bytes, read_source_text, source_suffix

KST = ZoneInfo('Asia/Seoul')
OPPORTUNITY_TYPES = {'scholarship', 'fellowship', 'admission_funding', 'internship', 'other'}


def classify_type(row):
    declared = row.get('opportunity_type') or row.get('type')
    if declared in OPPORTUNITY_TYPES:
        return declared, 'explicit_source_category'
    if declared == 'doctoral_studentship':
        return 'admission_funding', 'doctoral_studentship_alias'
    if declared is not None:
        raise ValueError(f'Unmapped opportunity category {declared!r}')
    title = row['title']
    internship = re.search(r'\bintern(?:ship|s)?\b|\bco[ -]?op\b|praktik|stagiaire|\bstage\b|est[áa]gio|estagi[áa]r|\bbecari[oa]\b|\bpr[áa]ctic(?:as?|ante)\b|стаж|实习|實習|인턴', title, re.I)
    student_work = re.search(r'working[ -]?student|werkstud|student(?:ische|ischer|ischen)?[ -]?(?:assistant|worker|aushilfe|hilfskraft)', title, re.I)
    if student_work and not internship:
        return 'other', 'explicit_student_employment_title'
    if internship:
        return 'internship', 'explicit_internship_title'
    return 'other', 'category_not_resolved'


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def locator(value, text_path=None):
    if isinstance(value, dict):
        if 'line' in value:
            return f"L{int(value['line'])}"
        content = read_source_text(text_path)
        start, end = int(value['char_start']), int(value['char_end'])
        if not 0 <= start < end <= len(content):
            raise ValueError('Character locator outside captured text')
        return f"L{content[:start].count(chr(10)) + 1}-L{content[:end - 1].count(chr(10)) + 1}"
    if not isinstance(value, str) or not LOCATOR.fullmatch(value):
        raise ValueError(f'Unsupported locator {value!r}')
    return value


def short_quote(value, words=10):
    return ' '.join(value.split()[:words])


def source_id(row):
    provider = row.get('source_id') or row['source_family']
    native = str(row.get('native_id') or row.get('id') or '')
    if not native:
        raise ValueError('No native posting identity')
    if row['source_family'] == 'workday_public_cxs':
        parsed_url = urlsplit(row['official_url'])
        host = parsed_url.hostname or ''
        match = re.fullmatch(r'([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com', host)
        site_match = re.fullmatch(r'wd\d+\.myworkdaysite\.com', host)
        tenant_path = re.match(r'/recruiting/([a-z0-9-]+)/', parsed_url.path)
        if match:
            provider = 'workday/' + match[1]
        elif site_match and tenant_path:
            provider = 'workday/' + tenant_path[1]
        else:
            raise ValueError('Workday identity requires its official public tenant URL')
    elif row['source_family'] == 'Mercedes-Benz/jobs.api.mercedes-benz.com':
        native = native.upper()
    return str(provider), native


def file_bytes(path, digest, cache, storage=None):
    p = Path(path).resolve(strict=True)
    if p not in cache:
        content = read_source_bytes(p, storage=storage)
        cache[p] = (sha256(content).hexdigest(), content)
    elif storage is not None:
        read_source_bytes(p, storage=storage)
    actual, content = cache[p]
    if actual != digest:
        raise ValueError(f'Hash mismatch {p}')
    return content


def native_in_source(row, native, raw):
    # Avoid accepting a shorter ID merely because it is part of a different ID.
    if re.search(rb'(?<![A-Za-z0-9])' + re.escape(native.encode()) + rb'(?![A-Za-z0-9])', raw, re.I):
        return True
    # UNSW identifies a call by two numeric URL path components, not a hyphenated ID.
    match = re.fullmatch(r'(\d+)-(\d+)', native)
    if row['source_family'] == 'UNSW' and match:
        expected = '/scholarships/id/' + '/'.join(match.groups())
        url = urlsplit(row['official_url'])
        return url.hostname == 'www.scholarships.unsw.edu.au' and url.path == expected and expected.encode() in raw
    return False


def visible_source(raw, cache, path):
    key = ('visible_source', str(path))
    if key not in cache:
        if raw.lstrip().startswith(b'{'):
            def strings(value):
                if isinstance(value, str):
                    yield value
                elif isinstance(value, dict):
                    for item in value.values():
                        yield from strings(item)
                elif isinstance(value, list):
                    for item in value:
                        yield from strings(item)
            parts = list(strings(json.loads(raw)))
        elif raw.lstrip().startswith((b'<?xml', b'<partial-response')):
            parts = list(ET.fromstring(raw).itertext())
        else:
            parts = [raw.decode('utf-8')]
        variants = []
        for part in parts:
            if '<' not in part:
                variants.append(normalize_source_evidence(part))
                continue
            soup = BeautifulSoup(part, 'lxml')
            variants.append(normalize_source_evidence(soup.get_text(' ', strip=True)))
            # Styled inline spans can divide a word (e.g. "202" + "7").
            # Recover the rendered spelling while retaining block boundaries.
            for tag in soup.find_all(['p', 'div', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'tr', 'section']):
                tag.insert_before(' ')
                tag.insert_after(' ')
            variants.append(normalize_source_evidence(soup.get_text('', strip=False)))
        cache[key] = '\nSOURCE_FIELD_BOUNDARY\n'.join(variants)
    return cache[key]


def normalize_source_evidence(text):
    # HTML inline spans may introduce spaces beside punctuation; preserve the exact word/punctuation sequence.
    return ' '.join(re.findall(r'\w+|[^\w\s]', text, re.UNICODE))


def capture_url(capture):
    return capture.get('request_url') or capture.get('url') or capture.get('final_url') or capture.get('official_url')


def support_capture(row, label):
    return row.get(label + '_source') or row


def verify_support_capture(row, capture, as_of, cache):
    checked = datetime.fromisoformat(capture['fetched_at'])
    if checked.tzinfo is None or checked.astimezone(KST).date() != as_of:
        raise ValueError('Support document not captured on cohort date with explicit timezone')
    source_host = urlsplit(capture_url(capture) or '').hostname or ''
    main_host = urlsplit(row['official_url']).hostname or ''
    same_org = source_host == main_host
    for domains in (('cam.ac.uk',), ('bmwgroup.jobs', 'bmwgroup.com'), ('jobbnorge.no',), ('mycareersfuture.gov.sg',)):
        if any(main_host == domain or main_host.endswith('.' + domain) for domain in domains):
            same_org = any(source_host == domain or source_host.endswith('.' + domain) for domain in domains)
    if not same_org or not (row.get('support_scope') or capture.get('scope')):
        raise ValueError('Support document needs verified same-organization origin and explicit applicability scope')
    file_bytes(capture['text_path'], capture['text_sha256'], cache, capture.get('text_storage'))
    return file_bytes(capture['raw_path'], capture['raw_sha256'], cache, capture.get('raw_storage'))


def official_application_link(row, raw, cache, base_url=None):
    if row['source_family'] == 'FR_PASS' and row['application_url'].endswith('#block-pass-content'):
        from expand_benefitlens_pass import verify_email_instructions
        return verify_email_instructions(row, raw)
    soup = BeautifulSoup(raw, 'lxml')
    links = {urljoin(base_url or row['official_url'], anchor['href']) for anchor in soup.select('a[href]')}
    if row['application_url'] in links:
        return True
    # DAAD's public frontend creates the link from an active button's rel fields.
    derivation = row.get('application_url_derivation', {})
    if row['source_family'] != 'DAAD' or not derivation:
        return False
    client_url = urlsplit(derivation['source_client_url'])
    if client_url.hostname != 'www2.daad.de':
        return False
    client = file_bytes(derivation['source_client_raw_path'], derivation['source_client_raw_sha256'], cache).decode()
    button = soup.select_one('button.gtm-stipdb-application.active')
    if button is None:
        return False
    params = button['rel'].split('-')
    if len(params) != 6 or params[5] != 'SAP_SCP':
        return False
    prefix = 'https://funding.meindaad.de/sites/personalfunding#PBFApplication-Start?'
    fragments = [prefix, "'fundingProgramInstance='+params[1]", "'&originCountry='+params[2]",
                 "'&targetCountry='+params[4]", "'&targetGroup='+params[3]"]
    if not all(fragment in client for fragment in fragments):
        return False
    expected = prefix + 'fundingProgramInstance=' + params[1] + '&originCountry=' + params[2] + '&targetCountry=' + params[4] + '&targetGroup=' + params[3]
    return row['application_url'] == expected


def reconcile_capture(previous, incoming):
    """Choose the newest independently validated observation, with deterministic ties."""
    if previous == incoming:
        return previous, None
    before, after = datetime.fromisoformat(previous['fetched_at']), datetime.fromisoformat(incoming['fetched_at'])
    if before == after:
        if previous['raw_sha256'] != incoming['raw_sha256'] or previous['text_sha256'] != incoming['text_sha256']:
            raise ValueError('Conflicting source contents at identical capture time')
        critical = ('official_url', 'application_url', 'closes_on', 'opens_on', 'opportunity_type')
        if any(previous.get(key) != incoming.get(key) for key in critical):
            raise ValueError('Conflicting interpreted fields at identical capture time')
        # Metadata enrichment has a stable tie-break independent of input order.
        chosen = min((previous, incoming), key=encode)
        reason = 'same_capture_enriched_metadata'
    else:
        chosen = incoming if after > before else previous
        reason = 'newest_validated_same_day_capture'
    return chosen, {'identity': source_id(chosen), 'reason': reason,
                    'captures': [{'fetched_at': row['fetched_at'], 'raw_sha256': row['raw_sha256']} for row in (previous, incoming)],
                    'chosen_raw_sha256': chosen['raw_sha256']}


def verify_candidate_posting(row, as_of, cache):
    classify_type(row)
    timestamp = datetime.fromisoformat(row['fetched_at'])
    if timestamp.tzinfo is None or timestamp.astimezone(KST).date() != as_of:
        raise ValueError('Posting not fetched on cohort date')
    if row.get('status') != 'open' or not row.get('open_basis'):
        raise ValueError('Missing positive current application evidence')
    if not all(urlsplit(row.get(key, '')).scheme == 'https' for key in ('official_url', 'application_url')):
        raise ValueError('Missing HTTPS posting or application URL')
    for field in ('opens_on', 'closes_on'):
        value = row.get(field)
        if value:
            day = date.fromisoformat(value)
            if (field == 'opens_on' and day > as_of) or (field == 'closes_on' and day < as_of):
                raise ValueError('Application window not current')
    if row.get('closes_on') and not (row.get('deadline_quote') and row.get('deadline_locator')):
        raise ValueError('Published deadline requires exact source deadline evidence')
    if row['source_family'] == 'JOBBNORGE':
        from benefitlens_campaign_jobbnorge import verify_candidate
        verify_candidate(row, as_of, cache)
        return
    if source_id(row)[0] == 'workday/walmart' and not row.get('frontend_status_source'):
        raise ValueError('Walmart requires the independently verified official frontend status capture')
    raw = file_bytes(row['raw_path'], row['raw_sha256'], cache, row.get('raw_storage'))
    file_bytes(row['text_path'], row['text_sha256'], cache, row.get('text_storage'))
    if row['source_family'] == 'UniversityOfFlorida/PageUp' and re.search(
            r'do not apply to this posting',
            BeautifulSoup(raw, 'lxml').get_text(' ', strip=True), re.I):
        raise ValueError('UF posting forbids its standard Apply route; direct submission instructions need separate verification')
    _, native = source_id(row)
    if not native_in_source(row, native, raw):
        raise ValueError('Native posting identifier absent from detail response')
    for label in ('student', 'status', 'deadline', 'opening', 'application_link', 'department', 'payment', 'institution_restriction', 'fws'):
        if row.get(label + '_source'):
            verify_support_capture(row, row[label + '_source'], as_of, cache)
    student_document = support_capture(row, 'student')
    student_raw = file_bytes(student_document['raw_path'], student_document['raw_sha256'], cache)
    if normalize_source_evidence(row['student_quote']) not in visible_source(student_raw, cache, student_document['raw_path']):
        raise ValueError('Student evidence absent from original visible source')
    for label in ('payment', 'institution_restriction', 'fws'):
        if row.get(label + '_quote'):
            source = support_capture(row, label)
            source_raw = file_bytes(source['raw_path'], source['raw_sha256'], cache)
            if normalize_source_evidence(row[label + '_quote']) not in visible_source(source_raw, cache, source['raw_path']):
                raise ValueError(f'{label} evidence absent from original visible source')
            locator(row[label + '_locator'], source['text_path'])
    if row.get('department_source'):
        department = row['department_source']
        raw_department = file_bytes(department['raw_path'], department['raw_sha256'], cache)
        if not row.get('department_quote') or normalize_source_evidence(row['department_quote']) not in visible_source(raw_department, cache, department['raw_path']):
            raise ValueError('Department binding absent from original official response')
        locator(row['department_locator'], department['text_path'])
    # Verify the original provider status fields, independently of the generated text.
    if raw.lstrip().startswith(b'{'):
        value = json.loads(raw)
        if 'jobPostingInfo' in value:
            info = value['jobPostingInfo']
            if info.get('canApply') is not True or info.get('posted') is not True or info.get('jobReqId') != native:
                raise ValueError('Workday detail is not published and applicable')
            if info.get('externalUrl') != row['official_url'] or row['application_url'] != row['official_url']:
                raise ValueError('Workday public posting/application entrypoint mismatch')
            exact_end = info.get('endDate') or info.get('applicationEndDate')
            if exact_end and 'T' in exact_end:
                end_at = datetime.fromisoformat(exact_end)
                if end_at.tzinfo is not None and end_at < timestamp:
                    raise ValueError('Workday explicit deadline instant precedes the capture')
        elif 'jobAd' in value:
            if str(value.get('id')) != native or value.get('active') is not True or value.get('visibility') != 'PUBLIC':
                raise ValueError('SmartRecruiters detail is not active and public')
            if value.get('postingUrl') != row['official_url'] or value.get('applyUrl') != row['application_url']:
                raise ValueError('SmartRecruiters original posting/application URL mismatch')
        elif row['source_family'] == 'SchneiderElectric/Jibe':
            if (str(value.get('req_id')) != native or value.get('applyable') is not True or
                    value.get('searchable') is not True or value.get('internal') is not False or
                    value.get('apply_url') != row['application_url']):
                raise ValueError('Jibe original public status or application URL mismatch')
            if value.get('posting_expiry_date'):
                expiry = datetime.fromisoformat(value['posting_expiry_date'])
                if expiry.tzinfo is None or expiry < timestamp or row.get('publication_ends_at') != value['posting_expiry_date']:
                    raise ValueError('Jibe posting publication has expired or has no exact expiry provenance')
            elif row.get('publication_ends_at') is not None:
                raise ValueError('Jibe source has no published posting expiry')
        elif row['source_family'] == 'Mercedes-Benz/jobs.api.mercedes-benz.com':
            items = value['SearchResult']['SearchResultItems']
            matching = [item['MatchedObjectDescriptor'] for item in items
                        if item['MatchedObjectDescriptor']['PositionID'].casefold() == native.casefold()]
            if not matching or not any(row['application_url'] in item.get('ApplyURI', []) and
                                       item['PositionURI'] == row['official_url'] and
                                       item.get('PublicationEndDate') == row.get('closes_on') for item in matching):
                raise ValueError('Mercedes native publication or application fields do not match')
            detail = row['detail_capture']
            detail_raw = file_bytes(detail['path'], detail['sha256'], cache)
            detail_at = datetime.fromisoformat(detail['fetched_at'])
            if detail_at.tzinfo is None or detail_at.astimezone(KST).date() != as_of:
                raise ValueError('Mercedes detail capture is not current')
            if detail['http_status'] != 200 or not native_in_source(row, native, detail_raw):
                raise ValueError('Mercedes public detail capture invalid')
        elif row['source_family'] == 'Singapore/MyCareersFuture':
            from benefitlens_mcf_period import verify_mcf_deadline
            verify_mcf_deadline(row, row['application_period'], raw, as_of, cache)
        else:
            raise ValueError('No verified adapter for original JSON publication status')
    else:
        link_document = support_capture(row, 'application_link')
        link_raw = file_bytes(link_document['raw_path'], link_document['raw_sha256'], cache)
        if not official_application_link(row, link_raw, cache, capture_url(link_document)):
            raise ValueError('Application URL absent from original posting HTML')
    if row.get('frontend_status_source'):
        frontend = row['frontend_status_source']
        checked = datetime.fromisoformat(frontend['fetched_at'])
        if (source_id(row)[0] != 'workday/walmart' or
                urlsplit(capture_url(frontend)).hostname != 'careers.walmart.com' or
                checked.tzinfo is None or checked.astimezone(KST).date() != as_of):
            raise ValueError('Walmart frontend capture has invalid origin or capture date')
        frontend_raw = file_bytes(frontend['raw_path'], frontend['raw_sha256'], cache)
        file_bytes(frontend['text_path'], frontend['text_sha256'], cache)
        script = BeautifulSoup(frontend_raw, 'lxml').select_one('script#__NEXT_DATA__')
        job = json.loads(script.string)['props']['pageProps']['jobDetails']
        if (job.get('jobId') != native or job.get('source') != 'WD' or
                job.get('requisitionStatus', {}).get('id') != 'OPEN' or
                not isinstance(job.get('positionAvailable'), (int, float)) or job['positionAvailable'] <= 0):
            raise ValueError('Walmart frontend does not confirm this requisition as open with positions')
    listing = row.get('listing_source')
    if listing:
        listed_at = listing['fetched_at']
        listing_raw = file_bytes(listing['raw_path'], listing['raw_sha256'], cache)
    elif row.get('listing_raw_path'):
        listed_at = row.get('listing_fetched_at') or row.get('listing_checked_at')
        listing_raw = file_bytes(row['listing_raw_path'], row['listing_raw_sha256'], cache)
    elif row.get('list_source'):
        listing = row['list_source']
        listed_at = listing['fetched_at']
        listing_raw = file_bytes(listing['path'], listing['sha256'], cache)
    else:
        raise ValueError('Missing same-day official listing provenance')
    if not listed_at or datetime.fromisoformat(listed_at).astimezone(KST).date() != as_of:
        raise ValueError('Listing not fetched on cohort date')
    if datetime.fromisoformat(listed_at).tzinfo is None:
        raise ValueError('Listing capture requires an explicit timezone')
    if not native_in_source(row, native, listing_raw):
        listed_url = row.get('listing_item_url') or row['official_url']
        listing_base = row.get('listing_request_url') or row.get('listing_url') or (listing or {}).get('url', '')
        if listed_url not in {urljoin(listing_base, a['href']) for a in BeautifulSoup(listing_raw, 'lxml').select('a[href]')}:
            raise ValueError('Native posting identifier or exact item URL absent from claimed listing')
    for label in ('student', 'status'):
        if not row.get(f'{label}_quote'):
            raise ValueError(f'Missing {label} evidence')
        locator(row[f'{label}_locator'], support_capture(row, label)['text_path'])
    if row.get('opens_on') and row.get('closes_on') and row['opens_on'] > row['closes_on']:
        raise ValueError('Application window closes before it opens')


def verify_posting(row, as_of, cache):
    verify_candidate_posting(row, as_of, cache)
    from benefitlens_application_period import verify_application_period
    verify_application_period(row, as_of, cache)


def tables_for(rows):
    tables = {name: [] for name in KEYS}
    documents, formats = {}, {}
    def document(capture, title):
        url = capture_url(capture)
        document_id = 'live-document-' + sha256(encode([capture['raw_sha256'], capture['text_sha256'], url]).encode()).hexdigest()[:24]
        suffix = source_suffix(capture['raw_path'])
        kind = {'.json': 'json', '.xml': 'xml', '.js': 'javascript', '.pdf': 'pdf'}.get(suffix, 'html')
        if suffix == '.raw':
            if capture['raw_sha256'] not in formats:
                content = file_bytes(capture['raw_path'], capture['raw_sha256'], {}, capture.get('raw_storage'))
                try:
                    json.loads(content)
                    formats[capture['raw_sha256']] = 'json'
                except (ValueError, UnicodeError):
                    formats[capture['raw_sha256']] = 'html'
            kind = formats[capture['raw_sha256']]
        documents[document_id] = {
            'document_id': document_id, 'title': capture.get('title') or title, 'url': url,
            'kind': kind,
            'capture_mode': 'full_document', 'fetched_at': capture['fetched_at'],
            **{key: capture[key] for key in ('raw_path', 'raw_sha256', 'text_path', 'text_sha256')},
            **{key: capture[key] for key in ('raw_storage', 'text_storage') if key in capture},
        }
        return document_id
    for row in rows:
        provider, native = source_id(row)
        key = sha256(encode([provider, native]).encode()).hexdigest()[:24]
        pid, rid = f'live-{key}', native
        keys = {'program_id': pid, 'round_id': rid}
        document_id = document(row, row['title'])
        if row.get('application_link_source'):
            document(row['application_link_source'], row['title'] + ' application route')
        if row.get('opening_source'):
            document(row['opening_source'], row['title'] + ' application opening')
        tables['program'].append({
            'program_id': pid, 'title': row['title'], 'issuer': row['issuer'],
            'opportunity_type': classify_type(row)[0],
        })
        tables['opportunity_round'].append({
            **keys, 'opportunity_id': pid + '-' + native, 'notice_version': row['raw_sha256'][:24],
            'status': 'open', 'official_url': row['official_url'], 'application_url': row['application_url'],
            'opens_on': row['application_period'].get('opens_on'),
            'closes_on': application_deadline(row), 'announces_on': None,
            'checked_on': datetime.fromisoformat(row['fetched_at']).astimezone(KST).date().isoformat(),
        })
        fields = [('student', 'eligibility', 'student_scope', {
            'student_scope_verified': True, 'full_eligibility_verified': False,
            'source_family': row['source_family'], 'source_id': provider, 'native_id': native,
            'student_scope_basis': row.get('student_scope_basis', 'student_targeted_official_source'),
            'support_scope': row.get('support_scope') or support_capture(row, 'student').get('scope'),
            'opportunity_subtype': row.get('opportunity_subtype'),
            'opportunity_type_basis': classify_type(row)[1],
        }), ('status', 'schedule', 'current_application_status', {
            'status': 'open', 'checked_at': row['fetched_at'], 'open_basis': row['open_basis'],
            'deadline': application_deadline(row), 'deadline_timezone': row.get('deadline_timezone'),
            'deadline_kind': row['application_period']['kind'],
            'application_period_verified': True,
            'publication_starts_on': row.get('opens_on') if row['source_family'] == 'Mercedes-Benz/jobs.api.mercedes-benz.com' else None,
            'publication_ends_on': row.get('publication_ends_on') or (row.get('closes_on') if row.get('closes_on_kind') == 'source_publication_end' else None),
            'publication_ends_at': row.get('publication_ends_at'),
            'listing_url': row.get('listing_request_url') or row.get('listing_url') or
                           row.get('listing_source', {}).get('url') or row.get('list_source', {}).get('url'),
            'application_group_url': row.get('application_group_url'),
            'application_form_interaction': row.get('application_form_interaction', 'not_attempted'),
            'application_form_state': row.get('form_state', 'not_interacted'),
            'application_scope': row.get('application_scope'),
            'program_family': row.get('program_family'),
            'student_application_route': row.get('student_application_route'),
            'login_required': row.get('login_required'),
        })]
        period = row['application_period']
        period_source = period['source']
        period_evidence_id = f'{pid}-application-period-evidence'
        period_claim_id = f'{pid}-application-period'
        posting = {key: row[key] for key in ('raw_path', 'raw_sha256', 'text_path', 'text_sha256',
                                              'fetched_at', 'official_url', 'closes_on') if key in row}
        for optional in ('request_url', 'url', 'support_scope', 'raw_storage', 'text_storage',
                         'native_id', 'source_family', 'application_url', 'application_url_derivation',
                         'record_pointer', 'student_quote', 'student_locator', 'status_quote',
                         'status_locator', 'issuer', 'opens_on', 'application_requires_login'):
            if optional in row:
                posting[optional] = row[optional]
        tables['claim'].append({
            **keys, 'claim_id': period_claim_id, 'kind': 'schedule', 'field': 'verified_application_period',
            'at': 'observed', 'review_state': 'reviewed',
            'value': {'application_period': period, 'posting': posting},
        })
        for index, evidence in enumerate([{'quote': period['quote'], 'locator': period['locator']},
                                           *period.get('context_evidence', [])]):
            eid = period_evidence_id + f'-{index}'
            tables['evidence'].append({
                **keys, 'evidence_id': eid, 'document_id': document(period_source, row['title'] + ' application period'),
                'source_role': 'official_notice', 'locator': locator(evidence['locator'], period_source['text_path']),
                'quote': short_quote(evidence['quote'], 4),
            })
            tables['claim_evidence'].append({**keys, 'claim_id': period_claim_id, 'evidence_id': eid})
        if row.get('payment_quote'):
            fields.append(('payment', 'benefit', 'payment_description', {
                'paid_student_employment': True, 'normalization': 'source_statement_only',
                'amount': None, 'currency': None,
            }))
        for label, field in (('institution_restriction', 'institution_enrollment'), ('fws', 'financial_work_study')):
            if row.get(label + '_quote'):
                fields.append((label, 'eligibility', field, {
                    'source_status': row.get(label + '_status'), 'normalization': 'source_statement_only',
                    'full_eligibility_verified': False,
                }))
        for label, kind, field, value in fields:
            source = support_capture(row, label)
            claim_id, evidence_id = f'{pid}-{label}', f'{pid}-{label}-evidence'
            tables['evidence'].append({
                **keys, 'evidence_id': evidence_id, 'document_id': document(source, row['title']),
                'source_role': 'official_guideline' if source is not row else 'official_notice',
                'locator': locator(row[f'{label}_locator'], source['text_path']),
                'quote': short_quote(row[f'{label}_quote']),
            })
            tables['claim'].append({
                **keys, 'claim_id': claim_id, 'kind': kind, 'field': field,
                'at': 'observed', 'value': value, 'review_state': 'reviewed',
            })
            tables['claim_evidence'].append({**keys, 'claim_id': claim_id, 'evidence_id': evidence_id})
        if row.get('closes_on') and row.get('deadline_quote') and row.get('deadline_locator'):
            source = support_capture(row, 'deadline')
            evidence_id = f'{pid}-deadline-evidence'
            tables['evidence'].append({
                **keys, 'evidence_id': evidence_id, 'document_id': document(source, row['title']),
                'source_role': 'official_guideline' if source is not row else 'official_notice',
                'locator': locator(row['deadline_locator'], source['text_path']),
                'quote': short_quote(row['deadline_quote'], 4),
            })
            tables['claim_evidence'].append({**keys, 'claim_id': f'{pid}-status', 'evidence_id': evidence_id})
        if row.get('department_source'):
            source = row['department_source']
            evidence_id = f'{pid}-department-evidence'
            tables['evidence'].append({
                **keys, 'evidence_id': evidence_id, 'document_id': document(source, row['title'] + ' department eligibility'),
                'source_role': 'official_guideline', 'locator': locator(row['department_locator'], source['text_path']),
                'quote': short_quote(row['department_quote']),
            })
            tables['claim_evidence'].append({**keys, 'claim_id': f'{pid}-student', 'evidence_id': evidence_id})
        if row.get('opening_source') and row.get('opening_quote') and row.get('opening_locator'):
            source = row['opening_source']
            evidence_id = f'{pid}-opening-evidence'
            tables['evidence'].append({
                **keys, 'evidence_id': evidence_id, 'document_id': document(source, row['title'] + ' application opening'),
                'source_role': 'official_guideline', 'locator': locator(row['opening_locator'], source['text_path']),
                'quote': short_quote(row['opening_quote']),
            })
            tables['claim_evidence'].append({**keys, 'claim_id': f'{pid}-status', 'evidence_id': evidence_id})
        if row.get('frontend_status_source'):
            source = row['frontend_status_source']
            evidence_id = f'{pid}-frontend-status-evidence'
            tables['evidence'].append({
                **keys, 'evidence_id': evidence_id, 'document_id': document(source, row['title'] + ' official frontend status'),
                'source_role': 'official_notice', 'locator': locator(source['status_locator'], source['text_path']),
                'quote': short_quote(source['status_quote']),
            })
            tables['claim_evidence'].append({**keys, 'claim_id': f'{pid}-status', 'evidence_id': evidence_id})
    tables['source_document'] = list(documents.values())
    return tables


def application_deadline(row):
    return None if row.get('closes_on_kind') == 'source_publication_end' else row.get('closes_on')


def select_counted_rows(rows, count, count_unit='opportunity_group'):
    """Default targets count issuer/role parents, never regional posting variants."""
    from benefitlens_opportunity_groups import group_opportunities
    if count_unit not in ('opportunity_group', 'source_posting'):
        raise ValueError('Unsupported count unit')
    if count_unit == 'source_posting':
        pools = {}
        for row in rows:
            pools.setdefault(source_id(row)[0], deque()).append(row)
        if len(rows) < count:
            raise ValueError(f'{count} source postings requested, {len(rows)} available')
        selected = []
        while len(selected) < count:
            for pool in pools.values():
                if pool and len(selected) < count:
                    selected.append(pool.popleft())
        groups, reviews, _ = group_opportunities(selected)
        return selected, groups, reviews
    groups, reviews, _ = group_opportunities(rows)
    eligible = [g for g in groups if g['decision'] == 'confirmed']
    if len(eligible) < count:
        raise ValueError(f'{count} issuer/role groups requested, only {len(eligible)} pass grouping rules; '
                         f'{len(rows)} source postings are not distinct opportunities')
    pools = {}
    for group in eligible:
        pools.setdefault(group['issuer_key'], deque()).append(group)
    selected_groups = []
    while len(selected_groups) < count:
        for pool in pools.values():
            if pool and len(selected_groups) < count:
                selected_groups.append(pool.popleft())
    selected = [rows[v['input_line'] - 1] for g in selected_groups for v in g['variants']]
    return selected, selected_groups, reviews


def build(inputs, count, output, report, as_of, reconcile_captures=False, count_unit='opportunity_group'):
    selected_path = report.with_name(report.stem + '-selected.jsonl')
    groups_path = report.with_name(report.stem + '-groups.jsonl')
    review_path = report.with_name(report.stem + '-group-review.jsonl')
    index_path = report.with_name(report.stem + '-group-source-index.jsonl')
    for destination in (output, report, selected_path, groups_path, review_path, index_path):
        if destination.exists():
            raise ValueError(f'Use a new immutable cohort destination: {destination}')
    cache, seen = {}, {}
    supplied = 0
    reconciliations = []
    for path in inputs:
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            row = json.loads(line)
            supplied += 1
            try:
                verify_posting(row, as_of, cache)
            except (ValueError, KeyError, OSError) as error:
                raise ValueError(f'{path}:{number}: {error}') from error
            identity = source_id(row)
            if identity in seen:
                if seen[identity] != row:
                    if not reconcile_captures:
                        raise ValueError(f'Conflicting native posting identity {identity}')
                    seen[identity], audit = reconcile_capture(seen[identity], row)
                    if audit:
                        reconciliations.append(audit)
                continue
            seen[identity] = row
    source_rows = [row for _,row in sorted(seen.items())]
    selected, opportunity_groups, group_reviews = select_counted_rows(source_rows, count, count_unit)
    tables = tables_for(selected)
    basis = {row['opportunity_id']: 'source_backed' for row in tables['opportunity_round']}
    validate_open_cohort(tables, basis, as_of)
    validation = validate_sources(tables, output)
    if output.exists():
        raise ValueError(f'Use a new immutable cohort output directory: {output}')
    export_shards(output, tables)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        'as_of': str(as_of), 'supplied_rows': supplied, 'validated_source_postings': len(seen),
        'requested_count_unit': count_unit, 'requested_count': count,
        'selected_source_postings': len(selected), 'selected_parent_groups': len(opportunity_groups),
        'rule_passed_parent_groups': sum(g['decision']=='confirmed' for g in opportunity_groups),
        'group_semantics_fully_adjudicated': False,
        'grouping_reviews': len(group_reviews),
        'group_files': {'groups': str(groups_path), 'review_queue': str(review_path), 'source_index': str(index_path)},
        'provider_counts': dict(Counter(source_id(row)[0] for row in selected)),
        'types': dict(Counter(classify_type(row)[0] for row in selected)),
        'published_deadline': sum(bool(application_deadline(row)) for row in selected),
        'publication_end_only': sum(bool(row.get('publication_ends_on') or row.get('closes_on_kind') == 'source_publication_end')
                                    and not application_deadline(row) for row in selected),
        'explicit_rolling_acceptance': sum(row['application_period']['kind']=='explicit_rolling' for row in selected),
        'unresolved_application_period': 0,
        'validation': validation, 'inputs': [{'path': str(path), 'sha256': sha256(path.read_bytes()).hexdigest()} for path in inputs],
        'capture_reconciliations': reconciliations,
        'output': str(output.resolve()),
    }, ensure_ascii=False, indent=2) + '\n')
    selected_path.write_text(''.join(encode(row) + '\n' for row in selected))
    groups_path.write_text(''.join(encode(row) + '\n' for row in opportunity_groups))
    review_path.write_text(''.join(encode(row) + '\n' for row in group_reviews))
    index_rows = selected if count_unit == 'source_posting' else source_rows
    index_path.write_text(''.join(encode({'input_line': i,'identity': source_id(row),'raw_sha256':row['raw_sha256']}) + '\n'
                                   for i,row in enumerate(index_rows,1)))
    print(report.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, action='append', required=True)
    parser.add_argument('--count', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--as-of', type=date.fromisoformat, required=True)
    parser.add_argument('--count-unit', choices=('opportunity_group', 'source_posting'), default='opportunity_group',
                        help='Default issuer/role parent groups; source_posting is an explicit raw-record audit mode')
    parser.add_argument('--reconcile-captures', action='store_true',
                        help='Reconcile independently validated same-day recaptures and retain a version audit')
    args = parser.parse_args()
    if args.count <= 0:
        parser.error('count must be positive')
    build(args.input, args.count, args.output.resolve(), args.report.resolve(), args.as_of, args.reconcile_captures, args.count_unit)


if __name__ == '__main__':
    main()
