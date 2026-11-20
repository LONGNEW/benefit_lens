"""Observed-link discovery and role-aware collection for the experience queue.

No search result, organization record or external project link is an episode.
The legacy collector owns original storage, publisher policy and DB handoff.
"""
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode, urljoin, urlsplit, parse_qs
import xml.etree.ElementTree as ET

from benefitlens_source_storage import read_source_bytes, store_source_bytes
from benefitlens_report_projection import project_report
from run_benefitlens_campaign import CACHE, KST
from run_benefitlens_korea import Held, compact, soup
from scale_benefitlens_experiences import (
    body_text, canonical, capture, digest, excerpt, failure, process as legacy_process,
)

VERSION = 'experience-expansion-v3'
BLOCKED = ('helsinki.fi', 'bath.ac.uk', 'kcl.ac.uk', 'geeksforgeeks.org', 'linkareer.com')
REPORT = re.compile(r'erfahrungsbericht|testimonial|experience[\s_-]*(?:report|stor)|'
                    r'personal[\s_-]*stor|internship[\s_-]*(?:report|reflection)|'
                    r'outreachy|gsoc|google.summer.of.code|후기|수기|체험|体験|合格', re.I)
DISCOVER = re.compile(REPORT.pattern + r'|international|outgoing|exchange|erasmus|'
                      r'abroad|mobilit|ausland|stipend|scholarship|fellowship|'
                      r'국제|교류|교환|파견|장학|海外|留学', re.I)
PERSONAL = re.compile(
    r'\b(?:I\s+(?:was|am|applied|submitted|received|joined|started|worked|completed|'
    r'have|had|learned|spent|studied|chose|decided)|my\s+(?:application|internship|'
    r'experience|project|exchange)|ich\s+(?:habe|war|bin|wurde|konnte|studiere|'
    r'hatte|verbrachte|entschied)|mein(?:e|en)?\s+(?:Bewerbung|Auslandssemester|'
    r'Praktikum|Aufenthalt))\b[^\n]{8,350}|'
    r'(?:저는|제가|저의|나는|私[はの])[^\n]{8,350}', re.I)
PROGRAMS = [
    ('google-summer-of-code', r'\bGSoC\b|Google\s+Summer\s+of\s+Code'),
    ('outreachy', r'\bOutreachy\b'),
    ('erasmus-student-mobility', r'\bErasmus\+?\b'),
    ('fulbright', r'\bFulbright\b|풀브라이트'),
    ('chevening', r'\bChevening\b'),
    ('daad-rise', r'\bDAAD\s+RISE\b|\bRISE\s+(?:Germany|Professional)\b'),
    ('mlh-fellowship', r'\bMLH\s+Fellowship\b'),
    ('lfx-mentorship', r'\bLFX\s+Mentorship\b'),
    ('campus-asia', r'CAMPUS\s+Asia'),
    ('daad-promos', r'\bPROMOS\b'),
]


def admitted(url):
    if not canonical(url):
        return False
    host = urlsplit(url).hostname
    return not any(host == b or host.endswith('.' + b) for b in BLOCKED)


def in_scope(url, source):
    if not admitted(url):
        return False
    host = urlsplit(url).hostname
    return host in source.get('allowed_hosts', []) or any(
        host == base or host.endswith('.' + base)
        for base in source.get('domain_suffixes', []))


def definition(url, *, source_id=None, institution=None, official=False, **extra):
    if not admitted(url):
        raise ValueError('Unapproved source URL')
    host = urlsplit(url).hostname
    return {'id': source_id or 'observed-' + digest(host)[:20],
            'adapter': 'hierarchical_reports', 'allowed_hosts': [host],
            'domain_suffixes': [host.removeprefix('www.')] if official else [],
            'institution': institution, 'official_publisher': official,
            'max_depth': 5 if official else 3, 'max_pages': 250 if official else 50,
            'source_admission': 'observed_public_link; policy_checked_per_request', **extra}


def classify(text, task, source, author=None):
    """A machine collection decision stays separate from semantic approval."""
    aliases = PROGRAMS + [(p['program_id'], p['body_pattern'])
                          for p in source.get('program_bindings', [])]
    matches = {}
    for key, pattern in aliases:
        match = re.search(pattern, text, re.I)
        if match and key not in matches:
            matches[key] = match
    programs = list(matches.items())
    personal = PERSONAL.search(text)
    result = {'text': text, 'status': 'reference_material',
              'reason': 'Original retained for document-role and semantic review',
              'evidence_roles': ['writing_reference_candidate'],
              'program_candidates': [key for key, _ in programs],
              'semantic_review': 'unreviewed', 'approved_pattern': False}
    # A public code repository is not a personal narrative by default.
    code_host = urlsplit(task['url']).hostname in ('github.com', 'gitlab.com', 'bitbucket.org')
    if (task.get('material_only') or source.get('material_only') or code_host or len(programs) != 1 or not personal
            or len(text.strip()) < 500 or re.search(r'captcha|access denied|verify you are human', text[:1000], re.I)):
        return result
    program, match = programs[0]
    # Unknown rounds are coalesced for known authors; publication dates are not rounds.
    cycle = None
    if program == 'google-summer-of-code':
        years = set(re.findall(r'(?:GSoC|Google\s+Summer\s+of\s+Code)\s*[-:]?\s*(20\d{2})', text, re.I))
        cycle = next(iter(years)) if len(years) == 1 else None
    elif program == 'erasmus-student-mobility':
        years = set(re.findall(r'(?:Wintersemester|Sommersemester|WiSe|SoSe|WS|SS)\s*\d{2,4}(?:\s*/\s*\d{2,4})?', text, re.I))
        cycle = next(iter(years)) if len(years) == 1 else None
    author_key = (author or {}).get('key')
    identity = [program, author_key, cycle] if author_key else [program, 'anonymous', digest(compact(text).casefold())]
    result.update(status='accepted', reason='Program and first-person evidence retained; semantic review pending',
        case_key='experience-expanded-' + digest(json.dumps(identity))[:28],
        native_id=task.get('native_id') or digest(task['url'])[:24],
        program_id=program, program_name=match[0], person_name=(author or {}).get('name'),
        author_handle=author_key, identity_status='public_source_author' if author_key else 'anonymous_source_text',
        program_binding_level='program_explicit_in_original_body',
        cycle=cycle, cycle_basis='explicit_program_round_in_body' if cycle else 'unknown_not_publication_date',
        published_at=task.get('published_at'), outcome='unknown',
        outcome_phase='application_or_participation_narrative', outcome_basis='not_inferred_from_directory',
        funding_status='not_confirmed', opportunity_type='student_mobility' if program == 'erasmus-student-mobility' else 'support_program',
        case_scope='application_process' if re.search(r'Bewerbung|申請|지원서|면접|지원\s*(?:동기|과정|준비)|신청\s*(?:절차|과정)|\bapplied\b|\bapplication\b', text, re.I) else 'participation_experience',
        evidence=[excerpt(text, personal[0]) | {'claim_type': 'personal_action'},
                  excerpt(text, match[0]) | {'claim_type': 'program_binding'}],
        normalized_experience=compact(text).casefold())
    result['evidence_roles'] = ['participation_experience']
    if result['case_scope'] == 'application_process':
        result['evidence_roles'].append('application_process')
    return result


def document_result(text, raw, meta, task, source, author=None):
    result = classify(text, task, source, author)
    ref = store_source_bytes(text.encode(), Path(meta['path']).parent, suffix='.txt')
    doc = {'document_id': 'experience-doc-' + digest(task['url'] + '\n' + meta['sha256'])[:32],
           'url': task['url'], 'final_url': meta['url'], 'raw': meta, 'text': ref,
           'kind': task.get('document_kind') or ('pdf' if raw.startswith(b'%PDF') else 'json' if raw.lstrip().startswith((b'{', b'[')) else 'html'), 'parser_version': VERSION,
           'source_id': source['id'], 'discovery_parent': task.get('parent'),
           'collection_status': result['status'], 'evidence_roles': result['evidence_roles'],
           'material_role_hint': task.get('document_role') or source.get('material_role_hint'),
           'author': author, 'author_display_name': task.get('author_display_name'),
           'author_evidence': task.get('author_evidence'),
           'document_finality': task.get('document_finality'),
           'source_label': task.get('label'), 'material_only': source.get('material_only', False),
           'access_scope': 'public', 'reuse_license': task.get('reuse_license') or 'not_inferred_from_public_access',
           'discovery_context': task.get('discovery_context'), 'record_pointer': task.get('record_pointer')}
    result.pop('text')
    result['document'] = doc
    if result['status'] == 'accepted':
        result['text_key'] = digest(result.pop('normalized_experience'))
        result.update(source_id=source['id'], attribution_status='source_record; semantic_review_pending',
                      cross_source_identity_status='unconfirmed',
                      scope_validation='explicit_program_and_personal_source_spans')
        for evidence in result['evidence']:
            evidence.update(document_id=doc['document_id'], text_sha256=ref['sha256'])
    return result


def report_links(raw, task, source):
    doc = soup(raw)
    node = (doc.select_one('main, article, #content, .content') or doc) if task.get('report_scope') else doc
    depth = task.get('depth', 0)
    scope = task.get('report_scope', False)
    children = []
    for a in node.select('a[href]'):
        url = canonical(urljoin(task['url'], a['href']))
        if not url or not in_scope(url, source) or url == task['url']:
            continue
        label = compact(a.get_text(' ', strip=True))
        topic = label + ' ' + urlsplit(url).path
        if re.search(r'\.(?:zip|xlsx?)(?:$|\s|\?)', topic, re.I):
            continue
        # Korean university attachments often encode the file only in link text.
        asset = bool(re.search(r'\.(?:pdf|hwp|hwpx|docx)(?:$|\?)|/download/?(?:$|\?)', url, re.I)
                     or re.search(r'\.(?:pdf|hwp|hwpx|docx)(?:$|\s)', label, re.I))
        pagination = 'next' in a.get('rel', []) or bool(re.search(
            r'[?&](?:page|paged|pageno|article\.offset)=\d+', url, re.I))
        article = bool(re.search(source.get('article_pattern', r'(?!)'), url))
        if source.get('include_link_pattern') and not pagination and not re.search(
                source['include_link_pattern'], topic, re.I):
            continue
        if asset and (scope or REPORT.search(topic)) or article:
            children.append({'url': url, 'kind': 'detail', 'depth': depth + 1,
                             'label': label, 'report_scope': True})
        elif depth < source.get('max_depth', 5) and (DISCOVER.search(topic) or pagination and scope):
            children.append({'url': url, 'kind': 'index',
                'depth': depth if pagination else depth + 1,
                'label': label, 'report_scope': scope or bool(REPORT.search(topic))})
    # Publisher-advertised RSS, never guessed feed URLs.
    for a in doc.select('link[rel="alternate"][href]'):
        if 'rss' in a.get('type', '') or 'atom' in a.get('type', ''):
            url = canonical(urljoin(task['url'], a['href']))
            if url and in_scope(url, source):
                children.append({'url': url, 'kind': 'public_feed', 'depth': depth + 1})
    return list({(x['url'], x['kind']): x for x in children}.values())


def ror_children(raw, task):
    data = json.loads(raw)
    sources = {}
    children = []
    for row in data.get('items', []):
        if row.get('status') != 'active' or 'education' not in row.get('types', []):
            continue
        for link in row.get('links', []):
            url = canonical(link.get('value', ''))
            if link.get('type') != 'website' or not url or not admitted(url):
                continue
            source = definition(url, source_id='institution-' + digest(row['id'])[:20],
                                institution=row['id'], official=True)
            sources[source['id']] = source
            children.append({'url': url, 'kind': 'index', 'source_id': source['id'],
                'depth': 0, 'report_scope': False,
                'discovery_context': {'ror_id': row['id'], 'registry_url': task['url']}})
    q = parse_qs(urlsplit(task['url']).query)
    page = int(q.get('page', ['1'])[0])
    # These are bounded search results, never a complete country inventory.
    if data.get('items') and page < task.get('max_registry_pages', 50) and page * 20 < data.get('number_of_results', 0):
        q['page'] = [str(page + 1)]
        children.append({'url': urlsplit(task['url'])._replace(query=urlencode(q, doseq=True)).geturl(),
                         'kind': 'ror_index', 'max_registry_pages': task.get('max_registry_pages', 50)})
    return children, sources


def outreachy_children(raw, task):
    doc = soup(raw)
    sources = {}
    children = []
    for a in doc.select('a[href]'):
        url = canonical(urljoin(task['url'], a['href']))
        if not url or not admitted(url):
            continue
        if re.fullmatch(r'/alums/\d{4}-\d{2}/?', urlsplit(url).path) and urlsplit(url).hostname == 'www.outreachy.org':
            children.append({'url': url, 'kind': 'outreachy_index'})
        elif a.select_one('img[alt="Blog"], img[alt="RSS"]'):
            source = definition(url)
            sources[source['id']] = source
            children.append({'url': url, 'kind': 'index', 'source_id': source['id'], 'depth': 0,
                'report_scope': True, 'discovery_context': {'program': 'outreachy', 'cohort_directory': task['url']}})
    return children, sources


def collect(task, source, ledger):
    if source['adapter'] not in ('hierarchical_reports', 'institution_registry', 'outreachy_directory', 'cached_material'):
        return legacy_process(task, source, ledger)
    try:
        if task['kind'] == 'cached_material':
            meta = task['original']
            raw = read_source_bytes(meta['path'], storage=meta.get('storage'))
            ref = task['text_ref']
            text_raw = read_source_bytes(ref['path'], storage=ref.get('storage'))
            if digest(raw) != meta['sha256'] or digest(text_raw) != ref['sha256']:
                raise ValueError('Staged material integrity failed')
            if str(task.get('record_pointer', '')).startswith('/posts/'):
                post = json.loads(raw)['posts'][int(task['record_pointer'].split('/')[-1])]
                derived = soup(post['content'].encode()).get_text('\n', strip=True)
                if post['URL'] != task['url'] or derived != text_raw.decode():
                    raise ValueError('Staged WordPress body pointer does not match original')
            return document_result(text_raw.decode(), raw, meta, task, source, task.get('author')), []
        if not in_scope(task['url'], source):
            raise Held('Discovered URL outside registered source scope')
        raw, meta, final = capture(ledger, task['url'])
        if not in_scope(final, source):
            raise Held('Final URL outside registered source scope')
        if task['kind'] == 'ror_index':
            children, discovered = ror_children(raw, task)
        elif task['kind'] == 'outreachy_index':
            children, discovered = outreachy_children(raw, task)
        elif task['kind'] == 'public_feed':
            tree = ET.fromstring(raw)
            children = [{'url': x.findtext('link'), 'kind': 'detail', 'depth': task.get('depth', 0),
                         'report_scope': True} for x in tree.findall('./channel/item')
                        if in_scope(x.findtext('link') or '', source)]
            discovered = {}
        elif task['kind'] == 'detail':
            kind, text = project_report(raw, ledger.root)
            author = None
            if kind == 'html':
                doc = soup(raw)
                author_tag = doc.select_one('meta[name="author"]')
                name = compact(author_tag.get('content', '')) if author_tag else ''
                author = {'key': urlsplit(final).hostname + ':' + name.casefold(), 'name': name} if name else None
            if len(text.strip()) < 150 or '\x00' in text:
                raise Held('Original has no usable narrative or writing text')
            return document_result(text, raw, meta, dict(task, document_kind=kind), source, author), []
        else:
            children, discovered = report_links(raw, dict(task, url=final), source), {}
        return {'status': 'indexed', 'original': meta, 'observed_children': len(children),
                '_discovered_sources': discovered}, children
    except (ValueError, OSError, ET.ParseError) as error:
        until = ledger.rate_holds.get(urlsplit(task['url']).hostname, 0)
        if until > time.time():
            return {'status': 'retry', 'reason': str(error)[:300], 'next_at': until}, []
        return failure(error, task), []


class Expansion:
    """Durable streams add new work instead of treating empty queues as success."""
    def __init__(self, frontier, sources, config):
        self.frontier, self.sources, self.config = frontier, sources, config
        with frontier.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS expansion_cursor(stream TEXT PRIMARY KEY,position INTEGER NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS expansion_source(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            for sid, payload in c.execute('SELECT id,payload FROM expansion_source'):
                sources[sid] = json.loads(payload)

    def register(self, source):
        self.sources[source['id']] = source
        with self.frontier.connect() as c:
            c.execute('INSERT OR REPLACE INTO expansion_source VALUES(?,?)',
                      (source['id'], json.dumps(source, ensure_ascii=False)))

    def finish(self, task, result, children):
        for source in result.pop('_discovered_sources', {}).values():
            self.register(source)
        same = []
        for child in children:
            sid = child.pop('source_id', task['source'])
            source = self.sources[sid]
            with self.frontier.connect() as c:
                n = c.execute("SELECT count(*) FROM experience_task WHERE source=? AND kind!='detail'", (sid,)).fetchone()[0]
            if child['kind'] != 'detail' and n >= source.get('max_pages', 10000):
                # Keep the observed branch for a later budget increase.
                self.frontier.add(sid, child, parent=task['url'], cohort='discovery_depth_budget')
                with self.frontier.connect() as c:
                    c.execute("UPDATE experience_task SET state='discovery_budget_held',error=? WHERE key=? AND state='pending'",
                              ('Source index budget; branch retained', digest(sid+'\n'+child['url'])))
                continue
            if sid == task['source']:
                same.append(child)
            else:
                self.frontier.add(sid, child, parent=task['url'], cohort='observed_source_expansion')
        self.frontier.finish(task, result, children=same)

    def consume(self, stream, rows, limit):
        with self.frontier.connect() as c:
            found = c.execute('SELECT position FROM expansion_cursor WHERE stream=?', (stream,)).fetchone()
        position = found[0] if found else 0
        added = 0
        for row in rows[position:position + limit]:
            position += 1
            source, task = row
            if not admitted(task['url']):
                continue
            self.register(source)
            inserted = self.frontier.add(source['id'], task, parent=task.get('parent', ''), cohort=stream)
            added += inserted
            if not inserted and task.get('kind') == 'cached_material':
                key = digest(source['id'] + '\n' + canonical(task['url']))
                with self.frontier.connect() as c:
                    previous = c.execute("SELECT payload,error FROM experience_task WHERE key=? AND state IN ('needs_review','reference_material')", (key,)).fetchone()
                    if previous:
                        old = json.loads(previous[0])
                        # A producer can repair missing receipt fields without
                        # changing the preserved original or retrying a denial.
                        same_bytes = all(old.get(field, {}).get('sha256') == task[field]['sha256']
                                         for field in ('original', 'text_ref'))
                        changed = any(old.get(field) != task.get(field)
                                      for field in ('original', 'text_ref', 'record_pointer', 'document_role',
                                                    'author', 'author_display_name', 'author_evidence',
                                                    'document_finality', 'reuse_license'))
                        if same_bytes and changed:
                            payload = dict(task, source=source['id'], cohort=stream,
                                staging_previous_failure={'error': previous[1],
                                                          'payload_sha256': digest(previous[0])})
                            c.execute("UPDATE experience_task SET payload=?,state='pending',error=NULL,next_at=0 WHERE key=? AND state IN ('needs_review','reference_material')",
                                      (json.dumps(payload, ensure_ascii=False), key))
                            added += 1
        with self.frontier.connect() as c:
            c.execute('INSERT OR REPLACE INTO expansion_cursor VALUES(?,?)', (stream, position))
        return added

    def seed(self, limit=200):
        added = 0
        seeds = []
        for source in self.config['sources']:
            for seed in source['seeds']:
                seeds.append((source, seed))
        # A newly inserted source must not be skipped by an older list offset.
        config_stream = 'configured-expansion-seeds:' + digest(json.dumps(seeds, sort_keys=True))[:16]
        added += self.consume(config_stream, seeds, limit)
        if not hasattr(self, 'gsoc_rows'):
            self.gsoc_rows = []
            path = CACHE/'experience-bulk-discovery-v1/gsoc-shared-directory/inventory.json'
            if path.exists():
                ref = json.loads(path.read_text())['candidate_index_jsonl']
                raw = read_source_bytes(ref['path'], storage=ref.get('storage'))
                if digest(raw) != ref['sha256']:
                    raise ValueError('Project directory integrity failed')
                for line in raw.splitlines():
                    row = json.loads(line)
                    url = canonical(row.get('candidate_report_url') or '')
                    if url and admitted(url):
                        src = definition(url)
                        self.gsoc_rows.append((src, {'url': url, 'kind': 'detail',
                            'discovery_context': row, 'parent': row['official_project_url']}))
        added += self.consume('gsoc-observed-report-links', self.gsoc_rows, limit)
        return added

    def staged_collections(self):
        collections = []
        for entry in self.config.get('staged_collections', []):
            path = Path(entry['index'])
            if not path.is_absolute():
                path = CACHE / path
            if not path.resolve().is_relative_to(CACHE.resolve()):
                raise ValueError('Staged collections must use the shared dataset cache')
            collections.append((entry, path))
        return collections

    def stage_rows(self):
        rows = []
        root = CACHE/'experience-wordpress-com-v1'
        status = root/'status.json'
        if status.exists():
            ref = json.loads(status.read_text()).get('records_jsonl')
            if ref:
                raw = read_source_bytes(ref['path'], storage=ref.get('storage'))
                if digest(raw) != ref['sha256']:
                    raise ValueError('Staged WordPress manifest integrity failed')
                for line in raw.splitlines():
                    row = json.loads(line)
                    url = row['original_url']
                    if not admitted(url):
                        continue
                    author = row.get('author', {})
                    rows.append(({'id': 'staged-wordpress', 'adapter': 'cached_material'},
                        {'url': url, 'kind': 'cached_material', 'original': row['batch_original'],
                         'text_ref': row['text'], 'record_pointer': row['record_pointer'],
                         'native_id': row['source_record_id'], 'published_at': row.get('published_at'),
                         'author': {'key': row['source_site'] + ':' + str(author.get('ID')),
                                    'name': author.get('name')} if author.get('ID') else None}))
        report_root = CACHE/'experience-gsoc-publisher-reports-v1'
        for path in sorted(report_root.glob('*/index.json')):
            for row in json.loads(path.read_text()):
                if row.get('state') == 'body_staged' and admitted(row['url']):
                    rows.append(({'id': 'staged-gsoc-reports', 'adapter': 'cached_material'},
                        {'url': row['url'], 'kind': 'cached_material', 'original': row['original'],
                         'text_ref': row['text'],
                         'material_stream': 'publisher-reports:' + path.parent.name,
                         'discovery_context': row.get('discovery') or row.get('project_candidates')}))
        for entry, path in self.staged_collections():
            if not path.exists():
                continue
            records = json.loads(path.read_text())
            if not isinstance(records, list):
                raise ValueError('Staged index must contain a list of document records')
            for row in records:
                if row.get('state') != 'body_staged' or not admitted(row.get('url', '')):
                    continue
                definition = row.get('source_definition') or {}
                source = dict(definition,
                    id=entry['id'] + (':' + definition['id'] if definition.get('id') else ''),
                    adapter='cached_material',
                    material_only=bool(entry.get('material_only', True) or
                                       definition.get('material_only') or row.get('material_only')),
                    material_role_hint=entry.get('material_role_hint'))
                rows.append((source, {
                    'url': row['url'], 'kind': 'cached_material',
                    'original': row['original'], 'text_ref': row['text'],
                    'document_kind': row.get('kind'), 'author': row.get('author'),
                    'document_role': row.get('document_role') or row.get('role_hint'),
                    'author_display_name': row.get('author_display_name'),
                    'author_evidence': row.get('author_evidence'),
                    'document_finality': row.get('document_finality'),
                    'native_id': row.get('native_id'), 'published_at': row.get('published_at'),
                    'reuse_license': row.get('reuse_license'),
                    'material_stream': 'registered-stage:' + entry['id'],
                    'discovery_context': row.get('discovery'),
                    'record_pointer': row.get('record_pointer')}))
        # Previously acquired unresolved bodies become searchable writing material.
        with self.frontier.connect() as c:
            prior = c.execute("SELECT url,source,result FROM experience_task WHERE state='needs_review' AND source!='recovered-material'").fetchall()
        for url, sid, encoded in prior:
            doc = json.loads(encoded or '{}').get('document')
            if doc:
                rows.append(({'id': 'recovered-material', 'adapter': 'cached_material'},
                    {'url': doc['url'], 'kind': 'cached_material', 'original': doc['raw'],
                     'text_ref': doc['text'], 'material_only': True,
                     'discovery_context': {'previous_source': sid, 'previous_status': 'needs_review'}}))
        return rows

    def add_materials(self, limit=200):
        paths = [CACHE/'experience-wordpress-com-v1/status.json',
                 *sorted((CACHE/'experience-gsoc-publisher-reports-v1').glob('*/index.json')),
                 *(path for _, path in self.staged_collections())]
        signature = (json.dumps(self.config.get('staged_collections', []), sort_keys=True),
                     [(str(p), p.stat().st_mtime_ns, p.stat().st_size)
                      for p in paths if p.exists()])
        if not hasattr(self, 'cached_rows') or signature != getattr(self, 'material_signature', None):
            self.cached_rows = self.stage_rows()
            self.material_signature = signature
        groups = {}
        for source, task in self.cached_rows:
            groups.setdefault(task.get('material_stream', source['id']), []).append((source, task))
        added = 0
        for group, rows in groups.items():
            if added >= limit:
                break
            identities = [(task['url'], task['original']['sha256'], task['text_ref']['sha256'],
                           digest(json.dumps(task, sort_keys=True)))
                          for _, task in rows]
            stream = 'cached-materials:' + group + ':' + digest(json.dumps(identities))[:16]
            added += self.consume(stream, rows, limit - added)
        return added

    def refresh_indices(self, day):
        """Revisit permitted completed indices daily, without retrying denials."""
        with self.frontier.connect() as c:
            rows = c.execute("""SELECT key FROM experience_task
                WHERE state='indexed' AND kind IN ('index','outreachy_index','ror_index','public_feed')
                AND substr(json_extract(result,'$.original.at'),1,10) < ? LIMIT 500""", (day,)).fetchall()
            c.executemany("UPDATE experience_task SET state='pending',next_at=0 WHERE key=?", rows)
        return len(rows)
