"""Pure parsers for public collection. No network, subprocess, or model calls."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
import re
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from benefitlens_application_period import extract_dates, ROLLING
from benefitlens_source_storage import read_source_bytes, read_source_text, store_source_bytes
from build_benefitlens_live_batch import verify_posting
from expand_benefitlens_varbi import parse_record as parse_varbi
from expand_benefitlens_reachmee import parse_record as parse_reachmee


def plain(value):
    return BeautifulSoup(value or '', 'lxml').get_text('\n', strip=True)


def attested_search_terms(row):
    """Expand a bounded vocabulary using exact phrases in a verified document."""
    text = read_source_text(row['text_path'], storage=row.get('text_storage'))
    patterns = [r'\binternship\b', r'\bintern\b', r'\bundergraduate\b',
                r'\bgraduate student\b', r'\bdoctoral student\b', r'\bPhD\b',
                r'\bco-op\b', r'\bworking student\b', r'\bWerkstudent\b',
                r'\btrainee\b', r'\bscholarship\b', r'\bfellowship\b']
    result = []
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            result.append({'term': match[0].casefold(), 'quote': match[0],
                           'source_url': row['official_url'], 'text_path': row['text_path'],
                           'text_sha256': row['text_sha256'], 'locator':
                           f'L{text[:match.start()].count(chr(10))+1}'})
    return result


def locate(text, quote):
    matches = list(re.finditer(r'\s+'.join(map(re.escape, quote.split())), text))
    if len(matches) != 1:
        raise ValueError('Evidence absent or ambiguous in LF projection')
    m = matches[0]
    return f'L{text[:m.start()].count(chr(10))+1}-L{text[:m.end()-1].count(chr(10))+1}'


_QUALIFICATIONS = re.compile(
    r'(?:what (?:you need to succeed|we need to see)|other qualifying skills|program eligibility requirements|'
    r'(?:basic|minimum|required) (?:qualifications|requirements)|'
    r'in order to participate in Berklee Student Employment, a student must fulfill the following requirements)\s*:?', re.I)
_OTHER_SECTION = re.compile(
    r'^(?:(?:preferred|desired)\b|(?:essential |primary |job |duties and )?responsibilities\b|'
    r'what (?:you(?:’|\x27)ll do|you will do)|ways to stand out|hiring unit|job description|benefits\b)', re.I)
_EDUCATION = re.compile(
    r'\b(?:degrees?|universit(?:y|ies)|colleges?|bachelors?|masters?|undergraduate|graduate program|'
    r'doctoral|ph\.?d|pharm\.?d|m\.?b\.?a|school of pharmacy)\b', re.I)
_DEGREE_PURSUIT = re.compile(
    r'pursuing\s+(?:(?:an?|the)\s+)?(?:bachelors?|masters?|undergrad(?:uate)?|graduate (?:degree|program)|'
    r'ph\.?d|pharm\.?d|doctor(?:al|ate)|m\.?b\.?a|b\.?s|m\.?s|b\.?eng|m\.?eng)\b', re.I)
_ENROLLMENT_EXCLUSION = re.compile(
    r'\b(?:not|no longer|need not|preferred|desirable|or equivalent (?:professional |work )?experience|'
    r'or (?:have )?(?:completed|earned|graduated)|or (?:new |recent )?graduates?|health plan|insurance plan)\b', re.I)


def student_quote(lines):
    """Require applicant wording or an academic clause in an observed qualifications section."""
    remaining = 0
    optional = False
    for i, line in enumerate(lines):
        if _QUALIFICATIONS.fullmatch(line.strip()):
            remaining = 10
            optional = False
            continue
        if _OTHER_SECTION.match(line.strip()):
            remaining = 0
            optional = bool(re.match(r'^(?:preferred|desired|ways to stand out)\b', line.strip(), re.I))
            continue
        in_qualifications = remaining > 0
        remaining -= 1
        if optional:
            continue
        quote = line
        # Inline markup can split one qualification and hide an alternative.
        if i+1 < len(lines) and (re.search(r'\b(?:or|and|in|a|an)\s*$', line, re.I)
                                 or re.match(r'\s*or\b', lines[i+1], re.I)):
            quote += '\n'+lines[i+1]
        compact = ' '.join(quote.split())
        if _ENROLLMENT_EXCLUSION.search(compact):
            continue
        clause = re.sub(r'^(?:[\s\u2022*\u00b7-]+|\d+[.)]\s*)+', '', compact)
        if re.match(r'^(?:(?:eligible|successful)\s+)?(?:applicants?|candidates?|you)\s+'
                    r'(?:must|should|are required to)\s+(?:currently\s+)?be\s+(?:an?\s+)?'
                    r'(?:registered\s+)?(?:undergraduate\s+|graduate\s+|university\s+)?students?\b', clause, re.I):
            return quote
        if re.match(r'^this (?:position|role) is (?:only )?open to\b.{0,90}\b'
                    r'(?:undergraduate|graduate|university|college) students?\b', clause, re.I):
            return quote
        direct = re.match(r'^(?:applicants?|candidates?|you)\s+(?:must\s+be\s+|are\s+)?'
                          r'currently\s+(?:enrolled|pursuing|studying)\b', clause, re.I)
        if direct and _EDUCATION.search(clause):
            return quote
        if not in_qualifications:
            continue
        if (re.match(r'^(?:(?:(?:must (?:currently )?be|must) )?(?:(?:currently|actively) )?'
                     r'enrolled\s+(?:in|at|as|full[- ]time|part[- ]time)\b|'
                     r'(?:expected|required) continued enrollment (?:in|at)\b|'
                     r'current enrollment (?:in|at)\b|registered and enrolled in\b|'
                     r'accepted into, or actively enrolled in,)\s*', clause, re.I)
                and _EDUCATION.search(clause)):
            return quote
        if (prefix := re.match(r'^(?:(?:must be|must) )?(?:currently )?', clause, re.I)) and _DEGREE_PURSUIT.match(clause, prefix.end()):
            return quote
        if re.match(r'^(?:Indigenous )?(?:undergraduate|graduate|university|college|PhD|Ph\.D\.) '
                    r'student currently enrolled\b', clause, re.I):
            return quote
        if re.match(r'^current (?:(?:full-time|undergrad(?:uate)?|graduate) )*student at\b', clause, re.I):
            return quote
        if re.match(r'^education:\s*current (?:(?:undergrad(?:uate)?|graduate|(?-i:[A-Z]{2,8})) )*'
                    r'student\.?$', clause, re.I):
            return quote
        if re.match(r'^education:.*\. registered as (?:an? )?(?:(?-i:[A-Z]{2,8}) )?'
                    r'(?:undergraduate|graduate) student\b', clause, re.I):
            return quote
    raise ValueError('No unambiguous explicit applicant enrollment clause')


def _workday_path_id(path, native, *, alternate=True):
    if (not isinstance(native, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', native)
            or not re.search(r'\d', native)):
        return False
    slug = unquote(urlsplit(path).path).rsplit('/', 1)[-1]
    return bool(re.search('_'+re.escape(native)+(r'(?:-\d+)?$' if alternate else '$'), slug))


def _workday_list_hint(item):
    """Workday bulletFields can start with a location or employee category."""
    values = item.get('bulletFields')
    if not isinstance(values, list):
        return None
    path = item.get('externalPath', '')
    exact = {v for v in values if _workday_path_id(path, v, alternate=False)}
    matches = exact or {v for v in values if _workday_path_id(path, v)}
    return next(iter(matches)) if len(matches) == 1 else None


def _workday_identity(cap, candidate, info):
    native = info.get('jobReqId')
    endpoint = candidate['endpoint']
    url = candidate['url']
    relative = url.removeprefix(endpoint)
    if not relative.startswith('/job/') or not _workday_path_id(relative, native):
        raise ValueError('Workday requisition identity mismatch')
    if (cap.get('request_url') or cap.get('url')) != url or (cap.get('final_url') or url) != url:
        raise ValueError('Workday capture does not match the listed detail URL')
    listing = candidate['listing']
    if (listing.get('request_url') or listing.get('url')) != endpoint+'/jobs':
        raise ValueError('Workday listing origin does not match the configured endpoint')
    raw = read_source_bytes(listing['raw_path'], storage=listing.get('raw_storage'))
    if sha256(raw).hexdigest() != listing['raw_sha256']:
        raise ValueError('Workday listing capture hash mismatch')
    matches = [item for item in json.loads(raw).get('jobPostings', []) if item.get('externalPath') == relative]
    if len(matches) != 1:
        raise ValueError('Exact Workday detail path absent or ambiguous in listing')
    hint = _workday_list_hint(matches[0])
    if hint is not None and hint != native:
        raise ValueError('Workday requisition identity mismatch')
    official = urlsplit(info.get('externalUrl', ''))
    if (official.scheme != 'https' or official.hostname != urlsplit(endpoint).hostname
            or '/job/' not in official.path
            or unquote(official.path.split('/job/', 1)[1]) != unquote(relative[len('/job/'):])):
        raise ValueError('External posting is not the listed public tenant detail')
    return native


def workday_period(lines, as_of):
    # A region-specific disclosure is not an unqualified application window.
    # Its applicability needs an explicit location binding before it can count.
    split = next((i for i, line in enumerate(lines) if re.fullmatch(r'State[- ]Specific Notices\s*:?\s*', line, re.I)), len(lines))
    scoped = lines[split:]
    lines = lines[:split]
    results = []
    for i, line in enumerate(lines[:-1]):
        if re.fullmatch(r'(?:Deadline to Apply|Application Deadline|Deadline for Application)\s*:', line, re.I):
            for value in lines[i+1:i+3]:
                dates = extract_dates(value)
                if len(dates) == 1:
                    results.append((line+'\n'+value, next(iter(dates)), []))
                    break
        if re.fullmatch(r'Posting End Date\s*:', line, re.I):
            dates = extract_dates(lines[i+1])
            rule = next((x for x in lines[i+1:i+7] if re.search(
                r'applications will be accepted until.{0,80}on the Posting End Date', x, re.I)), None)
            if len(dates) == 1 and rule:
                results.append((line+'\n'+lines[i+1], next(iter(dates)), [rule]))
    if not results:
        for line in lines:
            if ROLLING.search(line):
                return line, None, []
        if any(ROLLING.search(line) for line in scoped):
            raise ValueError('Application period only in a region-specific notice; scope unverified')
        raise ValueError('No explicit body application deadline; backend endDate is not substituted')
    if len({d for _, d, _ in results}) != 1:
        raise ValueError('Conflicting body application deadlines')
    quote, closing, context = results[0]
    if closing <= as_of:
        raise ValueError('Deadline passed or same-day cutoff unverified')
    return quote, closing, context


def parse_workday(cap, candidate, as_of, blob_dir):
    data = json.loads(read_source_bytes(cap['raw_path'], storage=cap['raw_storage']))
    info = data.get('jobPostingInfo', {})
    native = _workday_identity(cap, candidate, info)
    if info.get('canApply') is not True or info.get('posted') is not True:
        raise ValueError('Not published and applicable')
    lines = plain(info.get('jobDescription')).splitlines()
    if re.search(r'post[ -]?doc|professor|\bphysician\b|\bnurse\b', info.get('title', ''), re.I):
        raise ValueError('Staff/postdoctoral title outside student scope')
    student = student_quote(lines)
    quote, closing, context = workday_period(lines, as_of)
    for i, line in enumerate(lines[:-1]):
        if re.fullmatch(r'(?:Position|Job) End Date\s*(?:\(If applicable\))?\s*:', line, re.I):
            if any(d < as_of for d in extract_dates(lines[i+1])):
                raise ValueError('Employment end already passed')
    for term, year in re.findall(r'\b(spring|summer|fall|autumn|winter)\s+(20\d{2})\b', '\n'.join(lines), re.I):
        if int(year) < as_of.year or (int(year) == as_of.year and term.lower() in {'spring', 'summer'} and as_of.month >= 10):
            raise ValueError('Past term in body requires context review')
    text = '\n'.join([info['title'], 'jobReqId: '+native,
                      'canApply: true', 'posted: true',
                      'externalUrl: '+info['externalUrl']] + lines)+'\n'
    blob = store_source_bytes(text.encode(), blob_dir, suffix='.txt')
    cap = {**cap, 'text_path': blob['path'], 'text_sha256': blob['sha256'], 'text_storage': blob['storage']}
    row = {**cap, 'source_family': 'workday_public_cxs', 'source_id': candidate['tenant'],
           'native_id': native, 'id': native, 'title': info['title'], 'issuer': candidate['issuer'],
           'official_url': info['externalUrl'], 'application_url': info['externalUrl'],
           'status': 'open', 'open_basis': 'Published applicable public posting with explicit applicant enrollment and body application deadline or rolling acceptance',
           'opens_on': None, 'published_on': info.get('startDate'), 'closes_on': closing.isoformat() if closing else None,
           'student_quote': student, 'student_locator': locate(text, student),
           'status_quote': 'canApply: true\nposted: true', 'status_locator': 'L3-L4',
           'deadline_quote': quote, 'deadline_locator': locate(text, quote),
           'listing_source': candidate['listing'], 'location': info.get('location'),
           'country': (info.get('country') or {}).get('descriptor'),
           'application_requires_login': True,
           'application_access': 'official_public_entrypoint_account_not_opened',
           'eligibility_scope': 'Student audience only; complete personal eligibility unresolved',
           'semantic_review_state': 'automated_rules_only_not_independent_review',
           'portal_metadata_end_date': info.get('endDate')}
    row['application_period'] = {
        'kind': 'fixed_deadline' if closing else 'explicit_rolling', 'verification_state': 'verified',
        'source': cap, 'quote': quote, 'locator': row['deadline_locator'],
        'closes_on': row['closes_on'], 'opens_on': None, 'deadline_at': None,
        'semantics': 'explicit_application_deadline' if closing else 'explicit_application_rolling',
        'context_evidence': [{'quote': q, 'locator': locate(text, q)} for q in context]}
    routes = [x for x in lines if re.search(r'internal applicants|current UBC employees|active McGill employee|do not apply through this Career Site|Find Jobs report', x, re.I)]
    if routes:
        row['conditional_application_route_evidence'] = routes
        row['missing_profile_fields_for_route'] = ['current_employee_of_issuing_university']
    verify_posting(row, as_of, {})
    return row


def academicworks_fields(cap):
    """Extract visible material, but never promote a catalogue deadline to open status."""
    raw = read_source_bytes(cap['raw_path'], storage=cap['raw_storage'])
    text = read_source_text(cap['text_path'], storage=cap['text_storage'])
    soup = BeautifulSoup(raw, 'lxml')
    fields = {'title': soup.h1.get_text(' ', strip=True) if soup.h1 else None}
    lines = text.splitlines()
    for key in ('Award', 'Deadline'):
        at = [i for i, line in enumerate(lines[:-1]) if line == key]
        if len(at) == 1:
            fields[key.lower()] = lines[at[0]+1]
    fields['application_routes'] = [urljoin(cap['url'], a['href']) for a in soup.select('a[href]')
                                    if a.get_text(' ', strip=True).casefold() in {'sign in', 'apply now'}]
    fields['questions'] = lines[lines.index('Supplemental Questions')+1:] if 'Supplemental Questions' in lines else []
    return fields


def listing_jobs(source, cap, payload):
    """Return observed detail candidates and observed/attested pagination requests."""
    raw = read_source_bytes(cap['raw_path'], storage=cap['raw_storage'])
    kind = source['adapter']
    candidates, pages = [], []
    if kind == 'workday':
        data = json.loads(raw)
        if not isinstance(data.get('jobPostings'), list) or not isinstance(data.get('total'), int):
            raise ValueError('Workday public search response schema changed')
        items = data['jobPostings']
        for item in items:
            path = item.get('externalPath', '')
            if not path.startswith('/job/') or '://' in path or '..' in path:
                raise ValueError('Unrecognized public Workday externalPath')
            candidates.append({'url': source['endpoint']+path, 'endpoint': source['endpoint'],
                               'tenant': source['tenant'], 'issuer': source['issuer'],
                               'listing': cap, 'title_hint': item.get('title'),
                               'native_id': _workday_list_hint(item)})
        body = payload['body']
        offset = body['offset']+len(items)
        if items and offset < data['total']:
            pages.append({'url': cap['url'], 'body': {**body, 'offset': offset}, 'method': 'POST'})
        elif not items and body['offset'] < data['total']:
            raise ValueError('Workday returned empty page before reported total')
        return candidates, pages
    soup = BeautifulSoup(raw, 'lxml')
    for a in soup.select('a[href]'):
        href = a['href']; url = urljoin(cap['url'], href); u = urlsplit(url)
        title = a.get_text(' ', strip=True)
        native = None
        if kind == 'academicworks':
            if u.hostname == urlsplit(source['url']).hostname and re.fullmatch(r'/opportunities/\d+', u.path):
                native = u.path.rsplit('/', 1)[1]
            if title == 'Next' or a.get('rel') == ['next']:
                if u.hostname == urlsplit(source['url']).hostname and u.path == urlsplit(source['url']).path:
                    pages.append({'url': url, 'method': 'GET'})
        elif re.search(r'doctoral|phd|doktorand', title, re.I) and not re.search('post.?doc', title, re.I):
            if kind == 'varbi':
                m = re.search(r'jobID:(\d+)', unquote(u.path))
                if m and u.hostname in source['detail_hosts']:
                    native = m[1]
                elif u.hostname == 'kth.se' and re.fullmatch(r'/lediga-jobb/\d+', u.path):
                    native = u.path.rsplit('/', 1)[1]
            elif kind == 'reachmee' and u.hostname == 'web103.reachmee.com':
                native = (parse_qs(u.query).get('rmjob') or [None])[0]
        if native and u.scheme == 'https':
            candidates.append({'url': url, 'native_id': native, 'issuer': source['issuer'],
                               'title_hint': title, 'listing': cap, 'listing_target_url': href})
    return candidates, pages


def parse_detail(source, cap, candidate, as_of, blob_dir):
    kind = source['adapter']
    if kind == 'academicworks':
        return None, {'reason': 'scholarship_open_status_student_scope_and_shared_application_need_verified_adapter',
                      'extracted_fields': academicworks_fields(cap)}
    if kind == 'workday':
        row = parse_workday(cap, candidate, as_of, blob_dir)
    else:
        row = {'varbi': parse_varbi, 'reachmee': parse_reachmee}[kind](cap, candidate, as_of)
        verify_posting(row, as_of, {})
    row['semantic_review_state'] = 'automated_rules_only_not_independent_review'
    return row, None
