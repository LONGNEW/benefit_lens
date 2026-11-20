"""Offline Jobbnorge campaign adapter for the observed public PhD search."""
from datetime import date, datetime
import json
import re
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from benefitlens_application_period import extract_dates, verify_application_period
from benefitlens_source_storage import read_source_text, store_source_bytes
from expand_benefitlens_jobbnorge import doctoral_scope, locator, parse_record

KST = ZoneInfo('Asia/Seoul')
LIST_URL = 'https://publicapi.jobbnorge.no/v1/Jobs?term=PhD&results=100'
TEMPLATE_URL = 'https://www.jobbnorge.no/Client/packages/clients/web/views/jobListing.html'
ALLOWED_HOSTS = ['publicapi.jobbnorge.no', 'id.jobbnorge.no', 'www.jobbnorge.no']


def _detail_url(native):
    return f'https://id.jobbnorge.no/api/joblisting?jobId={native}&languageId=2'


def _support_requests(native):
    return {
        'info': {'url': f'https://id.jobbnorge.no/api/joblisting/job-info?jobId={native}&languageId=2', 'method': 'GET'},
        'page': {'url': f'https://www.jobbnorge.no/ledige-stillinger/stilling/{native}', 'method': 'GET'},
        'template': {'url': TEMPLATE_URL, 'method': 'GET'},
    }


def canonical_notice_redirect(request_url, location):
    """Accept one official slug redirect only for the identical public vacancy."""
    u = urlsplit(request_url)
    native = re.fullmatch(r'/ledige-stillinger/stilling/([0-9]+)', u.path)
    if not native or u.scheme != 'https' or u.netloc != 'www.jobbnorge.no' or u.query or u.fragment or not location:
        return None
    target = urlsplit(urljoin(request_url, location))
    if (target.scheme == 'https' and target.netloc == u.netloc and not target.query and not target.fragment
            and re.fullmatch(re.escape(u.path)+r'/[A-Za-z0-9][A-Za-z0-9-]*/?', target.path)):
        return target.geturl()
    return None


def _capture(cap, url, as_of, cache):
    from build_benefitlens_live_batch import file_bytes
    final = cap.get('final_url', url)
    if (cap.get('http_status', cap.get('status')) != 200
            or cap.get('status', 200) not in (200, 'open')
            or cap.get('url', cap.get('request_url')) != url
            or cap.get('request_url', url) != url
            or (final != url and (canonical_notice_redirect(url, final) != final or cap.get('canonical_notice_redirect') != final))
            or cap.get('method', 'GET') != 'GET'):
        raise ValueError('Jobbnorge public capture origin, method or HTTP status mismatch')
    fetched = datetime.fromisoformat(cap['fetched_at'])
    if fetched.tzinfo is None or (as_of is not None and fetched.astimezone(KST).date() != as_of):
        raise ValueError('Jobbnorge capture not from the same KST verification day')
    return file_bytes(cap['raw_path'], cap['raw_sha256'], cache, cap.get('raw_storage'))


def _listing_rows(listing, as_of, cache):
    data = json.loads(_capture(listing, LIST_URL, as_of, cache))
    _stored_text(listing, cache)
    if not isinstance(data, list) or len(data) > 100:
        raise ValueError('Unexpected Jobbnorge public PhD search shape')
    result = {}
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get('title'), str) or not item['title'].strip():
            raise ValueError('Invalid Jobbnorge search item')
        native = str(item.get('id'))
        if (not re.fullmatch(r'[0-9]+', native) or native in result
                or item.get('link') != _support_requests(native)['page']['url']):
            raise ValueError('Jobbnorge public listing/native link mismatch or duplicate ID')
        result[native] = item
    return result


def listing_jobs(source, cap, payload):
    if source.get('url') != LIST_URL or payload.get('url') != LIST_URL or payload.get('method', 'GET') != 'GET':
        raise ValueError('Only the observed bounded Jobbnorge listing is allowed')
    return [
        {'url': _detail_url(native), 'native_id': native, 'title_hint': item['title'],
         'issuer': item.get('employer'), 'listing': cap, 'support_requests': _support_requests(native)}
        for native, item in _listing_rows(cap, None, {}).items()
    ], []  # No next page has been attested for this public query.


def _detail_projection(body):
    parts = []
    for component in body.get('components', []):
        for key in ('heading', 'text', 'column1', 'column2', 'columnText'):
            if isinstance(component.get(key), str):
                parts.append(BeautifulSoup(component[key], 'lxml').get_text('\n', strip=True))
    return '\n'.join(parts + ['isPublished: '+json.dumps(body.get('isPublished'))])+'\n'


def _with_projection(cap, text, blob_dir):
    ref = store_source_bytes(text.encode(), blob_dir, suffix='.txt')
    return cap | {'text_path': ref['path'], 'text_sha256': ref['sha256'], 'text_storage': ref['storage']}


def _apply_config(html, native):
    matches = re.findall(r'\bjob:\s*\{[\s\S]*?\n\s*\}', html)
    if len(matches) != 1:
        raise ValueError('Jobbnorge online application config missing or ambiguous')
    config = matches[0]
    ids = re.findall(r"\bid:\s*'([0-9]+)'", config)
    urls = re.findall(r"\bapplyUrl:\s*'([^']+)'", config)
    if (ids != [native] or len(urls) != 1
            or len(re.findall(r'\bcanApplyOnline\s*:', config)) != 1
            or not re.search(r"\bcanApplyOnline:\s*'True'\s*===\s*'True'", config)):
        raise ValueError('Jobbnorge online application config does not match the posting')
    allowed = {f'https://www.jobbnorge.no/jobseeker/#/application/apply/{native}',
               f'https://jobseeker.jobbnorge.no/client/apply/{native}'}
    if urls[0] not in allowed:
        raise ValueError('Unattested Jobbnorge application route')
    return urls[0], config+'\n'


def parse_detail(source, cap, candidate, as_of, blob_dir, support):
    native = candidate['native_id']
    expected = _support_requests(native)
    if (source.get('url') != LIST_URL or candidate.get('url') != _detail_url(native)
            or candidate.get('support_requests') != expected or set(support) != set(expected)):
        raise ValueError('Invalid Jobbnorge campaign capture plan')
    cache = {}
    item = _listing_rows(candidate['listing'], as_of, cache).get(native)
    if item is None:
        raise ValueError('Jobbnorge detail ID absent from its same-day public listing')
    body = json.loads(_capture(cap, _detail_url(native), as_of, cache))
    original = {k: _capture(support[k], spec['url'], as_of, cache) for k, spec in expected.items()}
    _apply_config(original['page'].decode(), native)
    detail = _with_projection(cap, _detail_projection(body), blob_dir)
    info = _with_projection(support['info'], json.dumps(json.loads(original['info']), ensure_ascii=False, indent=2)+'\n', blob_dir)
    page = _with_projection(support['page'], BeautifulSoup(original['page'], 'lxml').get_text('\n', strip=True)+'\n', blob_dir)
    template = support['template'] | {'scope': 'Jobbnorge deadline field UI binding'}
    parsed = parse_record({'listing': item, 'detail': detail, 'info': info, 'page': page}, template, as_of)
    primary = parsed.pop('capture')
    row = primary | parsed | {
        'listing_source': candidate['listing'],
        'deadline_source': parsed['application_period']['source'],
        'open_basis': 'Official published doctoral recruitment with same-ID apply config and UI-bound future deadline',
        'application_form_interaction': 'not_attempted',
        'semantic_review_state': 'automated_rules_only_not_independent_review',
    }
    verify_candidate(row, as_of, cache)
    return row, None


def verify_candidate(row, as_of, cache):
    """Recheck source evidence without writing or trusting the generated row."""
    native = row.get('native_id')
    if (not isinstance(native, str) or not re.fullmatch(r'[0-9]+', native)
            or row.get('id') != native or row.get('source_id') != 'JOBBNORGE'
            or row.get('source_family') != 'JOBBNORGE' or row.get('status') != 'open'
            or not row.get('open_basis') or row.get('opens_on') is not None):
        raise ValueError('Jobbnorge row identity or open-state assertion invalid')
    listing = _listing_rows(row['listing_source'], as_of, cache).get(native)
    if not listing or row['official_url'] != listing['link'] or row['title'] != listing['title']:
        raise ValueError('Jobbnorge row not bound to same-day list item')
    original_body = json.loads(_capture(row, _detail_url(native), as_of, cache))
    if original_body.get('isPublished') is not True:
        raise ValueError('Jobbnorge body not published')
    expected = _support_requests(native)
    period = row['application_period']
    if (period.get('semantics') != 'jobbnorge_application_deadline'
            or period.get('kind') != 'fixed_deadline' or period.get('opens_on') is not None
            or not row.get('closes_on') or date.fromisoformat(row['closes_on']) <= as_of):
        raise ValueError('Jobbnorge exact future application deadline missing')
    original_info = json.loads(_capture(period['source'], expected['info']['url'], as_of, cache))
    if str(original_info.get('id')) != native or not row['issuer'] or original_info.get('employerName') != row['issuer']:
        raise ValueError('Jobbnorge employer or info record does not bind the native ID')
    if (row['deadline_source'] != period['source'] or
            _stored_text(period['source'], cache) != json.dumps(original_info, ensure_ascii=False, indent=2)+'\n'):
        raise ValueError('Jobbnorge deadline text projection is not derived from the original')
    page = row['application_link_source']
    html = _capture(page, expected['page']['url'], as_of, cache).decode()
    application, config = _apply_config(html, native)
    if row['application_url'] != application or _stored_text(page, cache) != config:
        raise ValueError('Jobbnorge actual application route/projection mismatch')
    _capture(period['field_semantics_source'], TEMPLATE_URL, as_of, cache)
    text = _detail_projection(original_body)
    if _stored_text(row, cache) != text:
        raise ValueError('Jobbnorge body projection is not derived from the original')
    if re.search(r'post.?doc|professor|førsteamanuensis', row['title'], re.I):
        raise ValueError('Postdoctoral/faculty role excluded')
    scope, scope_loc = doctoral_scope(text)
    if (len(scope.split()) > 18 or row['student_quote'] != scope or row['student_locator'] != scope_loc
            or row['status_quote'] != 'true' or row['status_locator'] != locator(text, 'isPublished: true')):
        raise ValueError('Jobbnorge student or publication evidence does not match source')
    for statement in re.findall(r'(?:start(?:ing)? date|commencement)[^.\n]{0,85}', text, re.I):
        if any(d < as_of for d in extract_dates(statement)):
            raise ValueError('Past specified commencement date held')
    if row['deadline_quote'] != period['quote'] or row['deadline_locator'] != period['locator']:
        raise ValueError('Jobbnorge deadline evidence mismatch')
    verify_application_period(row, as_of, cache)


def _stored_text(cap, cache):
    from build_benefitlens_live_batch import file_bytes
    return file_bytes(cap['text_path'], cap['text_sha256'], cache, cap.get('text_storage')).decode('utf-8')
