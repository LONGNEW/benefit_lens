"""Verify MyCareersFuture's actual public deadline and application UI contract.

A catalog hit or expiry field alone is insufficient. Every accepted record must
bind its own UUID, current status, full student text, date and actual apply route
to the publisher's captured frontend. No HTTP or authentication occurs here.
"""
from __future__ import annotations
from datetime import date, datetime
from hashlib import sha256
import json
from benefitlens_source_storage import read_source_bytes, read_source_text
import re
from urllib.parse import quote, urlsplit

FAMILY = 'Singapore/MyCareersFuture'
SEMANTICS = 'mycareersfuture_application_deadline'


def _pointer(value, pointer):
    if pointer == '':
        return value
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ValueError('MCF record needs an explicit JSON pointer')
    for key in pointer.split('/')[1:]:
        key = key.replace('~1', '/').replace('~0', '~')
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def _bytes(capture, cache):
    from build_benefitlens_live_batch import file_bytes
    return file_bytes(capture['raw_path'], capture['raw_sha256'], cache)


def _literal(capture, phrase, locator):
    raw = read_source_bytes(capture['text_path'])
    if sha256(raw).hexdigest() != capture['text_sha256']:
        raise ValueError('MCF text hash mismatch')
    m = re.fullmatch(r'L(\d+)(?:-L?(\d+))?', locator or '')
    lines = raw.decode('utf-8').split('\n')
    if not m:
        raise ValueError('MCF evidence needs an LF line locator')
    a, z = int(m[1]), int(m[2] or m[1])
    if not 1 <= a <= z <= len(lines) or ' '.join(phrase.split()) not in ' '.join(' '.join(lines[a-1:z]).split()):
        raise ValueError('MCF evidence not at its exact LF span')


def _binding_sources(period, as_of, cache):
    from build_benefitlens_live_batch import KST
    captures = period.get('field_semantics_sources')
    if not isinstance(captures, list) or len(captures) < 3:
        raise ValueError('MCF deadline requires UI, status constants and formatter source captures')
    sources = []
    for capture in captures:
        url = urlsplit(capture.get('request_url') or capture.get('url') or '')
        at = datetime.fromisoformat(capture['fetched_at'])
        if (url.scheme != 'https' or url.hostname != 'www.mycareersfuture.gov.sg'
                or not re.fullmatch(r'/(?:app-[\w.-]+|npm\.mcf-[\w.-]+)\.js', url.path)
                or at.tzinfo is None or at.astimezone(KST).date() != as_of):
            raise ValueError('MCF official frontend origin/date mismatch')
        source = _bytes(capture, cache)
        if sha256(read_source_bytes(capture['text_path'])).hexdigest() != capture['text_sha256']:
            raise ValueError('MCF frontend text hash mismatch')
        sources.append(source.decode('utf-8'))
    ui = next((s for s in sources if all(x in s for x in [
        'expiryDate:r.metadata.expiryDate', 'job-details-info-job-expiry-date', 'Closing on', 'Closed on',
        'job-details-apply-button', 'concat(t,"/apply")',
        'isClosed:r.status&&!(r.status.id===u.Pm.OPEN||r.status.id===u.Pm.RE_OPEN)',
        'formatJobUrl({jobTitle:n.title,company:n.hiringEmployerName||n.companyName,uuid:n.uuid})',
        'Log in to Apply'])), None)
    constants = next((s for s in sources if 'OPEN:102,RE_OPEN:103,CLOSED:9,DRAFT:83' in s), None)
    formatter = next((s for s in sources if 't.formatJobUrl=function(e)' in s and 't.processStringToUrlFormat=function(e)' in s), None)
    if not ui or not constants or not formatter:
        raise ValueError('MCF frontend does not attest exact expiry/status/apply bindings')
    return formatter


def _format_component(text, source):
    custom = re.search(r't\.CUSTOM_WORD_LIST=(\[[^\]]*\])', source)
    regular = re.search(r't\.STOP_WORDS=o\(o\(\[\],t.CUSTOM_WORD_LIST,!0\),(\[[^\]]*\]),![01]\)', source)
    if not custom or not regular:
        raise ValueError('Unsupported MCF official stopword declaration')
    steps = ('return[t.removeWordsInBracket,t.removeStopWords,t.removePunctuations,'
             't.removeExcessWhitespaces,t.joinWords,t.removeRepeatedHyphens,encodeURIComponent]')
    if steps not in source or r'e.replace(/\(.*?\)/gi,"")' not in source:
        raise ValueError('Unsupported MCF official formatter sequence')
    stopwords = set(json.loads(custom[1]) + json.loads(regular[1]))
    text = re.sub(r'\(.*?\)', '', (text or '').lower(), flags=re.I)
    text = ' '.join(word for word in text.split(' ') if word not in stopwords)
    text = text.translate(str.maketrans('', '', '~`!@#$%^&*(){}[];:\"\'<,.>?/\\|_+='))
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s', '-', text.strip())
    text = re.sub('-+', '-', text)
    return quote(text, safe="-_.!~*'()")


def mcf_apply_url(record, formatter_source):
    """Reproduce the captured button's category-free formatJobUrl call."""
    company = (record.get('hiringCompany') or record['postedCompany'])['name']
    title = _format_component(record['title'], formatter_source)
    company = _format_component(company, formatter_source)
    slug = (title + '-' if title else '') + (company + '-' if company else '') + record['uuid']
    return 'https://www.mycareersfuture.gov.sg/job/' + slug + '/apply'




def external_application_urls(record):
    """Read publisher-supplied application URLs, including explicit plain text."""
    from bs4 import BeautifulSoup
    body = BeautifulSoup(record.get('description') or '', 'html.parser')
    urls = set()
    for element in body.select('p,li,div'):
        if element.name == 'div' and element.select('p,li,div'):
            continue
        text = element.get_text(' ', strip=True)
        for match in re.finditer(r"https://[^\s<>\"']+", text):
            context = text[max(0, match.start()-120):match.end()+40]
            if re.search(r'apply|application|career portal|job portal', context, re.I):
                urls.add(match[0].rstrip(').,;]'))
    for anchor in body.select('a[href]'):
        parent = anchor.find_parent(['p','li']) or anchor.parent
        context = parent.get_text(' ', strip=True)
        if anchor['href'].startswith('https://') and len(context) <= 650 and re.search(r'apply|application|career portal|job portal', context, re.I):
            urls.add(anchor['href'])
    return urls

def student_eligibility_quote(record):
    """Return an explicit applicant-study clause, never a student beneficiary mention."""
    from bs4 import BeautifulSoup
    body = BeautifulSoup((record.get('description') or '')+'\n'+(record.get('otherRequirements') or ''), 'html.parser')
    lines = body.get_text('\n', strip=True).split('\n')
    patterns = [
        r"\bcurrently\s+(?:enrolled|pursuing|studying)\b",
        r"\bpursuing\s+(?:(?:a|an)\s+)?(?:degree|diploma|bachelor|master|undergraduate|postgraduate)\b",
        r"\b(?:current|final[- ]year|university|college|undergraduate|postgraduate|tertiary|polytechnic)\s+(?:[\w/-]+\s+){0,2}students?\b",
        r"\b(?:open to|welcome|suitable for|ideal for|looking for|seeking|motivated)\s+(?:(?:a|an|current|motivated)\s+){0,3}students?\b",
        r"\b(?:undergraduates?|undergraduate/[\w-]+)\b",
        r"\bstudents?\s+(?:or fresh graduates|and fresh graduates|who|majoring|pursuing|studying|from|in|are welcome|are encouraged|will be required)\b",
        r"\b(?:poly|uni|degree|diploma)[/ ,]+(?:university|poly|uni|degree|diploma)?\s*students?\b",
    ]
    for pattern in patterns:
        for line in lines:
            match = re.search(pattern, line, re.I)
            if not match:
                continue
            prefix = re.split(r'[.!?;]\s+', line[:match.start()])[-1]
            beneficiary = (r'(?:teach(?:ing)?|tutor(?:ing)?|coach(?:ing)?|minding|liais\w*|interact\w*|support\w*|track\w*|mentor\w*|train\w*)\b.{0,80}$'
                           r'|(?:guidance|advice|counsell?ing|assistance|services?|programmes?|programs?)\s+(?:for|to)\b.{0,80}$'
                           r'|\bfor\s+(?:(?:primary|secondary|tertiary|school|education)[,\s]*(?:and\s+)?){1,8}$'
                           r'|for local and international school.{0,35}$')
            if re.search(beneficiary, prefix, re.I):
                continue
            words = list(re.finditer(r'\S+', line))
            index = next(i for i, word in enumerate(words) if word.end() > match.start())
            sentence = list(re.finditer(r'[.!?;]\s+', line[:match.start()]))
            boundary = sentence[-1].end() if sentence else 0
            first_sentence_word = next((i for i, word in enumerate(words) if word.start() >= boundary), 0)
            first = max(first_sentence_word, index-4)
            last = min(len(words), first+22)
            return line[words[first].start():words[last-1].end()]
    return None

def verify_mcf_deadline(row, period, raw, as_of, cache):
    """Raise on any unbound status, application period, body quote or route.

    ``cache`` is the common verified-file cache. ``raw`` must contain the actual
    original JSON response, either one record or a public search response.
    Source hash verification and full LF projection remain mandatory.
    """
    from build_benefitlens_live_batch import KST
    if row.get('source_family') != FAMILY or period.get('semantics') != SEMANTICS:
        raise ValueError('MCF source family or period semantics mismatch')
    src = period['source']
    url = urlsplit(src.get('request_url') or src.get('url') or '')
    native = str(row.get('native_id') or row.get('id') or '')
    if not re.fullmatch(r'[0-9a-f]{32}', native) or url.scheme != 'https' or url.hostname != 'api.mycareersfuture.gov.sg':
        raise ValueError('MCF official API/native ID mismatch')
    at = datetime.fromisoformat(src['fetched_at'])
    if at.tzinfo is None or at.astimezone(KST).date() != as_of:
        raise ValueError('MCF record capture is not same-day')
    original = _bytes(src, cache)
    if original != raw:
        raise ValueError('MCF supplied raw differs from its attested original')
    data = json.loads(raw)
    rp = period.get('record_pointer')
    if rp != row.get('record_pointer') or not isinstance(rp, str):
        raise ValueError('MCF row and period record pointers disagree')
    if 'results' in data:
        if url.path != '/v2/jobs' or not re.fullmatch(r'/results/\d+', rp):
            raise ValueError('MCF search response needs its exact results pointer')
    elif url.path != '/v2/jobs/' + native or rp != '':
        raise ValueError('MCF detail API URL/pointer mismatch')
    record = _pointer(data, rp)
    if record.get('uuid') != native or record.get('status', {}).get('id') not in {102, 103}:
        raise ValueError('MCF record is not this native public open/re-open posting')
    metadata = record['metadata']
    closing = metadata.get('expiryDate')
    opening = metadata.get('newPostingDate')
    if (not isinstance(closing, str) or not re.fullmatch(r'20\d{2}-\d{2}-\d{2}', closing)
            or closing != period.get('quote') or closing != period.get('closes_on') or closing != row.get('closes_on')
            or date.fromisoformat(closing) <= as_of):
        raise ValueError('MCF actual closing day is closed, same-day without a time, or mismatched')
    if (not opening or date.fromisoformat(opening) > as_of
            or opening != row.get('opens_on') or opening != period.get('opens_on')):
        raise ValueError('MCF current posting period is not bound')
    if period.get('field_pointer') != '/metadata/expiryDate':
        raise ValueError('MCF deadline field pointer is not its UI-bound application deadline')
    _literal(src, closing, period['locator'])
    _literal(src, row['student_quote'], row['student_locator'])
    _literal(src, row['status_quote'], row['status_locator'])
    from bs4 import BeautifulSoup
    body = BeautifulSoup((record.get('description') or '')+'\n'+(record.get('otherRequirements') or ''), 'html.parser')
    text = body.get_text(' ', strip=True)
    applicant_clause = student_eligibility_quote(record)
    from build_benefitlens_live_batch import normalize_source_evidence, visible_source
    original_body = ((record.get('description') or '')+'\n'+(record.get('otherRequirements') or '')).encode('utf-8')
    own_visible = visible_source(original_body, cache, 'mcf-body-' + sha256(original_body).hexdigest())
    if (not applicant_clause
            or ' '.join(applicant_clause.split()) not in ' '.join(row['student_quote'].split())
            or normalize_source_evidence(row['student_quote']) not in own_visible):
        raise ValueError('MCF student evidence belongs to another record')
    if row['status_quote'] != record['status']['jobStatus']:
        raise ValueError('MCF status evidence belongs to another record')
    if metadata.get('jobDetailsUrl') != row['official_url'] or urlsplit(row['official_url']).hostname != 'www.mycareersfuture.gov.sg':
        raise ValueError('MCF actual public posting URL mismatch')
    if row.get('issuer') != record.get('postedCompany', {}).get('name'):
        raise ValueError('MCF issuer borrowed from another posting')
    formatter = _binding_sources(period, as_of, cache)
    application = row['application_url']
    if urlsplit(application).scheme != 'https':
        raise ValueError('MCF application route is not HTTPS')
    external = external_application_urls(record)
    if re.search(r"(?:do not|don't) (?:only )?apply (?:via|through|on) (?:this|the) portal|must.{0,30}email application|we must receive an email", text, re.I):
        raise ValueError('MCF employer requires a non-HTTPS email-only application route')
    if external:
        if application not in external:
            raise ValueError('MCF explicit external application instruction not followed')
    elif record.get('psdUrl'):
        if application != record['psdUrl'].strip():
            raise ValueError('MCF Careers@Gov route mismatch')
    elif application != mcf_apply_url(record, formatter) or row.get('application_requires_login') is not True:
        raise ValueError('MCF application route not the actual login-required official Apply button')
    # Current header dates do not override a contradictory deadline in the body.
    from benefitlens_application_period import extract_dates
    for match in re.finditer(r'(?:applications?\s+(?:will\s+)?clos(?:e|es|ing)|apply\s+by|application\s+deadline)[^\n.]{0,110}', text, re.I):
        dates = extract_dates(match[0])
        if dates and any(d != date.fromisoformat(closing) for d in dates):
            raise ValueError('MCF body application deadline conflicts with catalog deadline')
    return record
