"""Bounded HTML discovery for public application experiences, without a model."""
from __future__ import annotations

from hashlib import sha256
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from run_benefitlens_korea import Held, compact


def official_alumni_binding(document, spec, page_url, raw_sha256, full_text, body):
    """Bind a named official grantee story; do not infer application-writing skill."""
    name = spec.get('discovered_subject_name')
    parent = (spec.get('discovery') or {}).get('parent_url', '')
    if (not document or not name or not body or spec.get('program_id') != 'fulbright-korea-gsp'
            or urlsplit(page_url).hostname != 'www.fulbright.or.kr'
            or not re.fullmatch(r'https://www\.fulbright\.or\.kr/alumni-stories/gsp/(?:page/\d+/)?', parent)):
        return None
    article = document.select_one('article.category-gsp')
    if article is None:
        return None
    if not re.search(r'(?:Fulbright\s+)?Graduate\s+Student\s+Program', body, re.I):
        return None
    normalize = lambda value: re.sub(r'[^a-z0-9]', '', value.casefold())
    normalized = normalize(name)
    if len(normalized) < 5:
        return None
    quotes = [line for line in body.splitlines() if normalized in normalize(line)]
    if not quotes or quotes[0] not in full_text:
        return None
    quote = quotes[0]
    classes = article.get('class', [])
    tag = next((c for c in classes if re.match(r'tag-20\d{2}-fulbright-graduate-student-program', c)), None)
    tag_year = re.search(r'20\d{2}', tag)[0] if tag else None
    match = re.search(r'(20\d{2})\s+(?:Fulbright\s+)?Graduate\s+Student\s+Program', body[:500], re.I)
    body_year = match[1] if match else None
    conflicts = []
    if tag_year and body_year and tag_year != body_year:
        conflicts = [{'value':tag_year, 'basis':'html_article_class', 'exact_quote':tag},
                     {'value':body_year, 'basis':'detail_body_header', 'exact_quote':match[0]}]
    return {'case_id':'focus-fulbright-alumni-'+sha256(page_url.encode()).hexdigest()[:16],
            'program_id':'fulbright-korea-gsp', 'person_name':name,
            'cycle':None if conflicts else body_year, 'outcome':'selected',
            'outcome_basis':'official_program_grantee_story', 'outcome_phase':'recipient_experience',
            'case_scope':'post_award_experience', 'cycle_conflicts':conflicts,
            'cycle_basis':'conflicting_public_fields' if conflicts else 'detail_body_header' if body_year else 'unconfirmed',
            'registry_url':parent, 'binding_status':'structured_official_identity_checked',
            'binding_evidence':{'document_id':'writing-'+raw_sha256[:24],
                'text_sha256':sha256(full_text.encode()).hexdigest(), 'url':page_url,
                'line':full_text[:full_text.index(quote)].count('\n')+1, 'exact_quote':quote},
            'experience_profile':{'material_availability':'post_award_narrative; submitted_application_not_collected',
                'outcome_basis_detail':'official_program_alumni_category_and_named_detail_story'}}


def experience_content(document, spec):
    selector = spec.get('content_selector')
    if not selector:
        return None
    nodes = document.select(selector) if document else []
    nodes = [node for node in nodes if not any(parent in nodes for parent in node.parents)]
    for node in nodes:
        for unwanted in node.select('script,style,noscript,nav,footer,.revenue_unit_wrap'):
            unwanted.decompose()
    text = '\n'.join(compact(line) for node in nodes for line in node.stripped_strings)
    if len(text.strip()) < spec.get('minimum_content_characters', 300):
        raise Held('Experience body missing or too short; navigation and footer do not qualify')
    return text


def discover_experience_links(document, spec, page_url):
    rule = spec.get('follow_experience_links') or {}
    if not document or not rule.get('selector'):
        return []
    host = urlsplit(page_url).hostname
    result, seen = [], set()
    for anchor in document.select(rule['selector']):
        target = urlsplit(urljoin(page_url, anchor.get('href', '')))
        published_document = (rule.get('allow_published_google_docs')
            and target.hostname == 'docs.google.com'
            and re.fullmatch(r'/document/d/(?:e/)?[^/]+/pub', target.path))
        if (target.scheme != 'https' or (target.hostname != host and not published_document) or target.username
                or target.password or target.port not in (None, 443)
                or re.search(r'/(login|signin|oauth|account)(/|$)', target.path, re.I)):
            continue
        url = urlunsplit(target._replace(fragment=''))
        if url == page_url or url in seen:
            continue
        label = compact(anchor.get_text(' ', strip=True))
        if rule.get('label_pattern') and not re.search(rule['label_pattern'], label, re.I):
            continue
        if rule.get('url_pattern') and not re.search(rule['url_pattern'], url, re.I):
            continue
        if rule.get('exclude_url_pattern') and re.search(rule['exclude_url_pattern'], url, re.I):
            continue
        seen.add(url)
        pagination = bool(rule.get('pagination_pattern') and re.search(rule['pagination_pattern'], url))
        child = {'url': url, 'program_id': spec.get('program_id'),
                 'role': 'experience_index' if pagination else rule.get('child_role', 'experience_review'),
                 'discovery': {'parent_url': page_url, 'anchor_label': label,
                               'selector': rule['selector']},
                 'content_selector': None if pagination else rule.get('child_content_selector'),
                 'minimum_content_characters': rule.get('minimum_content_characters', 300)}
        if pagination:
            child['follow_experience_links'] = rule
        elif rule.get('inherit_case_context'):
            for key in ('case_id','program_id','person_name','cycle','outcome','outcome_basis',
                        'author_handle','case_scope','outcome_phase','binding_evidence'):
                if key in spec:
                    child[key] = spec[key]
            if rule.get('follow_series'):
                child['follow_experience_links'] = rule
        if rule.get('subject_from_anchor') and not pagination:
            child['discovered_subject_name'] = label
            # The name in an index is discovery evidence. Do not attach an
            # outcome or count a case before the detail body has been reviewed.
        if rule.get('pdf_role') and target.path.lower().endswith('.pdf'):
            child['role'] = rule['pdf_role']
            child['content_selector'] = None
        if published_document:
            child['role'] = 'application_statement'
            child['content_selector'] = '#contents, .doc-content'
        result.append(child)
        if len(result) >= rule.get('max_links', 30):
            break
    return result


def literal_experience_passages(text, document_id, url, limit=18):
    """Index source passages for review; these are not inferred writing patterns."""
    topics = {
        'application_material': r'자기소개서|연구계획서|학업계획서|지원서|statement|proposal|motivation letter|essay',
        'preparation_or_feedback': r'첨삭|피드백|수정|준비|면접|feedback|revis|interview|prepar',
        'outcome_or_reapplication': r'탈락|불합격|재지원|재도전|합격|reject|unsuccessful|reappl|accept|select',
    }
    passages = []
    for number, line in enumerate(text.splitlines(), 1):
        if len(line.strip()) < 18:
            continue
        matched = [topic for topic, pattern in topics.items() if re.search(pattern, line, re.I)]
        if not matched:
            continue
        # Keep exact whole source lines, including long paragraphs. The full
        # document remains the subsequent model input rather than this index.
        passages.append({'document_id': document_id, 'source_url': url,
                         'line_start': number, 'line_end': number,
                         'exact_quote': line, 'topics': matched,
                         'interpretation_status': 'literal_passage_unreviewed'})
        if len(passages) >= limit:
            break
    return passages


def attribute_case_passages(cases, documents, read_text):
    """Keep document cues separate from passages attributed to one episode.

    A multi-person or multi-round source needs an explicit, source-bound span.
    Unreviewed keyword hits are never copied indiscriminately to all subjects.
    """
    subjects = {}
    for case in cases:
        for doc_id in case['document_ids']:
            subjects.setdefault(doc_id, set()).add(case['case_id'])
    texts = {}
    for case in cases:
        case['experience_passages'], case['evidence_spans'] = [], []
        unresolved = []
        for doc_id in case['document_ids']:
            doc = documents[doc_id]
            if doc_id not in texts:
                texts[doc_id] = read_text(doc)
            text = texts[doc_id]
            lines = text.splitlines()
            span = case.get('case_text_span') or {}
            if isinstance(span, list):
                span = next((s for s in span if s.get('document_id') == doc_id), {})
            explicit = bool(span and span.get('document_id') == doc_id
                            and span.get('text_sha256') == sha256(text.encode()).hexdigest())
            if explicit:
                start, end = span.get('line_start', 0), span.get('line_end', 0)
                if not (isinstance(start, int) and isinstance(end, int) and 1 <= start <= end <= len(lines)):
                    explicit = False
            if explicit:
                basis = 'explicit_source_bound_span'
            elif len(subjects[doc_id]) == 1:
                start, end, basis = 1, len(lines), 'single_episode_document'
            else:
                unresolved.append(doc_id)
                continue
            passages = literal_experience_passages('\n'.join(lines[start-1:end]), doc_id, doc['url'])
            for passage in passages:
                passage['line_start'] += start - 1
                passage['line_end'] += start - 1
                passage['attribution_basis'] = basis
            case['experience_passages'].extend(passages)
            case['evidence_spans'].append({'document_id':doc_id, 'line_start':start,
                'line_end':end, 'text_sha256':sha256(text.encode()).hexdigest(), 'basis':basis})
        unconfirmed = 'not_confirmed_distinct_person' in str(case.get('binding_status', ''))
        case['quality_audit'] = {'version':'episode-attribution-v1',
            'attribution_status':'needs_review' if unresolved else 'document_bound',
            'unresolved_document_ids':unresolved,
            'identity_status':'distinct_person_unconfirmed' if unconfirmed else
                'public_name_or_handle' if case.get('person_name') or case.get('author_handle') else 'anonymous',
            'semantic_review':'unreviewed', 'approved_pattern':False}
