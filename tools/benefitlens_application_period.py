"""Verify application periods independently of publication and database integrity."""
from __future__ import annotations
from datetime import date, datetime, timedelta
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from benefitlens_source_storage import read_source_text

MONTHS = {}
for number, names in enumerate([
    'jan january januar janvier enero janeiro', 'feb february februar fevrier février febrero fevereiro',
    'mar march maerz märz mars marzo março', 'apr april avril abril', 'may mai mayo maio',
    'jun june juni juin junio junho', 'jul july juli juillet julio julho',
    'aug august aout août agosto', 'sep sept september septembre septiembre setembro',
    'oct october oktober octobre octubre outubro', 'nov november novembre noviembre novembro',
    'dec december dez dezember decembre décembre diciembre dezembro',
], 1):
    for name in names.split(): MONTHS[name] = number
MONTH = '(?:' + '|'.join(sorted(MONTHS, key=len, reverse=True)) + ')'
SEMANTIC = re.compile(r'\b(?:application|applications|applying|apply|deadline|closing|closes?|funding deadline|ongoing|candidatures?|postuler)\b|bewerbung|bewerbungsfrist|schlie[ßs]|einsend|申请|申請|접수|지원|신청|서류\s*제출\s*마감', re.I)
ROLLING = re.compile(r'(?:accept(?:s|ing)? applications|applications (?:will be |are |being )?accepted|(?:can |may )?apply).{0,160}(?:ongoing basis|year.?round|all year|throughout the year|any time|anytime|until (?:the (?:role|position) is )?filled|until further notice|until we (?:receive|select))|(?:role|position|vacancy).{0,100}(?:remain|remains|is|will be)\s+open.{0,80}until filled|(?:wir suchen jederzeit|bewerbungen.{0,100}(?:jederzeit|ganzjährig))', re.I|re.S)
VOLUME_CLOSURE = re.compile(r'accept ongoing applications|(?:close (?:this|the) (?:vacancy|position)|(?:vacancy|position) will be closed).{0,100}(?:enough applications|enough matching candidates)|fermerons.{0,100}suffisamment de candidatures|cerraremos.{0,100}suficientes solicitudes', re.I|re.S)
POOL = re.compile(r'(?:this|the|exact)\s+(?:role|position).{0,240}(?:not (?:currently )?open|may not be open today)|it[’\']s not currently open|(?:this|the)\s+(?:role|position).{0,160}(?:talent pipeline|future opportunities)', re.I|re.S)


def extract_dates(text, date_order=None):
    """Return only explicit date spellings; ambiguous numeric dates need an attested order."""
    found = set()
    def add(y, m, d):
        try: found.add(date(int(y) if len(str(y)) == 4 else 2000 + int(y), int(m), int(d)))
        except ValueError: pass
    for m in re.finditer(r'(?<!\d)(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?(?!\d)', text): add(*m.groups())
    korean_date = r'(?<!\d)(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일'
    for m in re.finditer(korean_date, text): add(*m.groups())
    # The omitted year belongs only to the immediately joined range endpoint.
    # Never borrow a publication year, the run date, or an inferred next year.
    range_gap = r'\s*(?:\([월화수목금토일]\))?\s*(?:\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?|\d{1,2}:\d{2})?\s*[~∼～]\s*'
    for m in re.finditer(korean_date + range_gap + r'(\d{1,2})\s*월\s*(\d{1,2})\s*일', text):
        try:
            start = date(int(m[1]), int(m[2]), int(m[3]))
            end = date(int(m[1]), int(m[4]), int(m[5]))
        except ValueError:
            continue
        if end >= start: found.add(end)
    for m in re.finditer(r'(?<!\d)(\d{1,2})[./](\d{1,2})[./]((?:20)?\d{2})(?!\d)', text):
        a, b, y = m.groups();a,b=int(a),int(b)
        if a > 12: add(y,b,a)
        elif b > 12: add(y,a,b)
        elif a == b: add(y,a,b)
        elif date_order == 'DMY': add(y,b,a)
        elif date_order == 'MDY': add(y,a,b)
    for m in re.finditer(r'(?<!\d)(\d{1,2})(?:st|nd|rd|th)?[\s.]+('+MONTH+r')\.?\s*,?\s*(20\d{2})(?!\d)',text,re.I): add(m[3],MONTHS[m[2].lower()],m[1])
    for m in re.finditer(r'\b('+MONTH+r')\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(20\d{2})(?!\d)',text,re.I): add(m[3],MONTHS[m[1].lower()],m[2])
    return found


def pointer(value, path):
    if not isinstance(path,str) or not path.startswith('/'): raise ValueError('Expected exact JSON pointer')
    for key in path.split('/')[1:]:
        key=key.replace('~1','/').replace('~0','~')
        value=value[int(key)] if isinstance(value,list) else value[key]
    return value


def verified_date_order(period, raw):
    """Require locale proof only when a numeric spelling is actually ambiguous."""
    order = period.get('date_order')
    if not order or extract_dates(period['quote']):
        return None
    e = period.get('date_order_evidence') or {}
    if e.get('quote') and extract_dates(e['quote']):
        # This spelling is validated at its own exact source locator below.
        return None
    if e.get('source_pointer') != '/application_period/source':
        raise ValueError('Ambiguous numeric date lacks explicit source-locale evidence')
    if e.get('json_pointer'):
        value = pointer(json.loads(raw), e['json_pointer'])
    elif e.get('html_selector') == 'html' and e.get('attribute') == 'lang':
        from bs4 import BeautifulSoup
        value = BeautifulSoup(raw, 'lxml').html.get('lang')
    else:
        raise ValueError('Unsupported source-locale attestation')
    expected = {'en-GB':'DMY','en-AU':'DMY','en-US':'MDY','fr-FR':'DMY','de-DE':'DMY'}.get(value)
    if value != e.get('value') or expected != order:
        raise ValueError('Date order conflicts with original explicit locale')
    return order


def verify_jobbnorge_deadline(row, period, raw, as_of, cache):
    """Attest the exact public JSON field using the publisher's real UI binding."""
    from bs4 import BeautifulSoup
    from build_benefitlens_live_batch import capture_url, verify_support_capture
    source = urlsplit(capture_url(period['source']))
    native = str(row.get('native_id') or row.get('id') or '')
    info = json.loads(raw)
    query = parse_qs(source.query)
    if (source.scheme != 'https' or source.hostname != 'id.jobbnorge.no'
            or source.path != '/api/joblisting/job-info'
            or query.get('jobId') != [native] or str(info.get('id')) != native):
        raise ValueError('Jobbnorge deadline source/native identity mismatch')
    if (not isinstance(info.get('deadlineOn'), str)
            or period['quote'] != info['deadlineOn']
            or not re.fullmatch(r"\$date\('20\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}'\)", info['deadlineOn'])):
        raise ValueError('Jobbnorge closing date is not the exact deadlineOn field')
    binding = period.get('field_semantics_source') or {}
    url = urlsplit(capture_url(binding) or '')
    if (url.scheme != 'https' or url.hostname != 'www.jobbnorge.no'
            or url.path != '/Client/packages/clients/web/views/jobListing.html'):
        raise ValueError('Jobbnorge deadline field lacks its official UI source')
    template = verify_support_capture(row, binding, as_of, cache)
    block = BeautifulSoup(template.decode('utf-8'), 'lxml').select_one('.partial.deadline')
    label = block.select_one('h3[data-jn-localize="Søknadsfrist"]') if block else None
    expressions = [node.get('data-ng-bind', '') for node in block.select('[data-ng-bind]')] if block else []
    if label is None or not any(re.match(r"^jobInfo\.deadlineOn\s*\|\s*jnDateToISO\s*\|\s*date:", expression) for expression in expressions):
        raise ValueError('Jobbnorge UI does not bind deadlineOn to the application deadline')


def verify_application_period(row, as_of, cache):
    from build_benefitlens_live_batch import (file_bytes,capture_url,verify_support_capture,
        visible_source,normalize_source_evidence,locator,LOCATOR,KST)
    period=row.get('application_period')
    if not period or period.get('verification_state')!='verified':
        raise ValueError('Missing independently verified application period; retain as candidate')
    kind=period.get('kind')
    if kind not in ('fixed_deadline','explicit_rolling'):
        raise ValueError('Unresolved or publication-only period is not secured')
    if period.get('opens_on') and date.fromisoformat(period['opens_on']) > as_of:
        raise ValueError('Verified application period has not opened')
    source=period['source'];at=datetime.fromisoformat(source['fetched_at'])
    if at.tzinfo is None or at.astimezone(KST).date()!=as_of:
        raise ValueError('Application-period source not captured on verification date')
    is_primary=(source['raw_sha256']==row['raw_sha256'] and capture_url(source)==capture_url(row))
    if is_primary:
        raw=file_bytes(source['raw_path'],source['raw_sha256'],cache,source.get('raw_storage'))
        file_bytes(source['text_path'],source['text_sha256'],cache,source.get('text_storage'))
    else:
        raw=verify_support_capture(row,source,as_of,cache)
    lines=read_source_text(source['text_path'],storage=source.get('text_storage')).split('\n')
    context=[]
    extra = period.get('date_order_evidence') or {}
    evidences = [{'quote':period['quote'],'locator':period['locator']},*period.get('context_evidence',[])]
    if extra.get('quote') and extra.get('locator'):
        evidences.append(extra)
    for evidence in evidences:
        loc=locator(evidence['locator'],source['text_path']);m=LOCATOR.fullmatch(loc);a,z=int(m[1]),int(m[2]or m[1])
        q=' '.join(evidence['quote'].split());actual=' '.join(' '.join(lines[a-1:z]).split())
        if not(1<=a<=z<=len(lines) and q and q in actual): raise ValueError('Application-period quote not at exact LF locator')
        if normalize_source_evidence(evidence['quote']) not in visible_source(raw,cache,source['raw_path']):
            raise ValueError('Application-period evidence absent from original source')
        context.append(evidence['quote'])
    main_raw=file_bytes(row['raw_path'],row['raw_sha256'],cache)
    if POOL.search(visible_source(main_raw,cache,row['raw_path'])):
        raise ValueError('Future talent pool is not a currently available opportunity')
    quote='\n'.join(context)
    if kind=='explicit_rolling':
        if period.get('closes_on') is not None or row.get('closes_on') is not None:
            raise ValueError('Explicit rolling acceptance must not invent a closing date')
        labelled_ongoing = (re.fullmatch(r'on-?going', period['quote'].strip(), re.I)
                            and any(re.fullmatch(r'(?:application )?clos(?:e|ing) date', e['quote'].strip(), re.I)
                                    for e in period.get('context_evidence', [])))
        labelled_french_ongoing = bool(re.fullmatch(
            r'Date limite de candidature\s*:\s*Offre ouverte en continu',
            period['quote'].strip(), re.I))
        if period.get('semantics')!='explicit_application_rolling' or not (ROLLING.search(quote) or VOLUME_CLOSURE.search(quote) or labelled_ongoing or labelled_french_ongoing):
            raise ValueError('Publication, ongoing study, or rolling review is not rolling application acceptance')
        return
    closing=period.get('closes_on')
    if not closing or row.get('closes_on')!=closing:
        raise ValueError('SQL closing date differs from verified application date')
    closed=date.fromisoformat(closing)
    if closed<as_of: raise ValueError('Verified application deadline has passed')
    date_order=verified_date_order(period, raw)
    dates=extract_dates(period['quote'],date_order)
    if extra.get('quote'):
        dates |= extract_dates(extra['quote'])
    semantics=period.get('semantics')
    portal_info = None
    if semantics=='portal_application_window':
        portal_info=json.loads(raw)['jobPostingInfo']
        if not(portal_info.get('timeLeftToApply') and portal_info.get('jobPostingEndDateAsText') and portal_info.get('endDate')):
            raise ValueError('Portal endDate without application-window display is unconfirmed')
        dates |= extract_dates(portal_info['jobPostingEndDateAsText'])
    rule=period.get('normalization_rule')
    if rule:
        if rule.get('kind')!='calendar_day_before_portal_end': raise ValueError('Unsupported date adjustment')
        if not re.search(r'night before.{0,60}(?:posted|end date)',quote,re.I|re.S): raise ValueError('Missing exact day-before deadline rule')
        dates={d-timedelta(days=1) for d in dates}
    if closed not in dates:
        raise ValueError('Normalized closing date not supported by exact source date spelling')
    if semantics=='portal_application_window':
        info=portal_info
        actual=date.fromisoformat(info['endDate'][:10]);actual=actual-timedelta(days=1)if rule else actual
        if actual!=closed or date.fromisoformat(info['endDate'][:10]) not in extract_dates(info['jobPostingEndDateAsText']):
            raise ValueError('Official portal application date fields disagree')
    elif semantics == 'jobbnorge_application_deadline':
        verify_jobbnorge_deadline(row, period, raw, as_of, cache)
    elif semantics == 'mycareersfuture_application_deadline':
        from benefitlens_mcf_period import verify_mcf_deadline
        verify_mcf_deadline(row, period, raw, as_of, cache)
    elif semantics in ('explicit_application_deadline','official_catalog_deadline'):
        if not SEMANTIC.search(quote): raise ValueError('Date context does not identify an application deadline')
    else: raise ValueError('Unverified period semantics')
    end_at=period.get('deadline_at')
    if end_at:
        end=datetime.fromisoformat(end_at)
        if end.tzinfo is None or end<max(datetime.fromisoformat(row['fetched_at']), datetime.now().astimezone()):
            raise ValueError('Exact deadline instant has passed or lacks timezone')
    elif closed==as_of:
        raise ValueError('Same-day deadline without exact cutoff is held for revalidation')
