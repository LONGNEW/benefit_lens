"""Whole stored-corpus source zones and exact-context quality projection.

Only new derived SQLite/JSONL files are written. Lexical hints are not semantic
completion. No model, crawler, live service or source database writes occur.
"""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
from urllib.parse import urlparse
from itertools import groupby

from lxml import html, etree

from audit_benefitlens_experience_production import JsonLines, jsonl
from benefitlens_source_storage import read_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json, checked_root, single_writer

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO/'pipeline/config/experience_quality_expansion_v1.json'
STRUCTURE_VERSION = 'source-zone-preprocessing-v2-20261120'
PARAGRAPH_VERSION = 'paragraph-boundary-v1-20261120'
STRUCTURE_FILES = ('documents.jsonl.gz','zones.jsonl.gz','segments.jsonl.gz')


def quality_writer(path):
    """Readers retain their source snapshot while derived updates commit."""
    connection=sqlite3.connect(path,timeout=60)
    connection.execute('PRAGMA busy_timeout=60000')
    connection.execute('PRAGMA journal_mode=WAL')
    return connection


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def normalize(text):
    return ' '.join(unicodedata.normalize('NFC', text).split())


def readonly(path):
    connection = sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    connection.execute('BEGIN')
    return connection


def verified_bytes(reference):
    warning = None
    try:
        value = read_source_bytes(reference['path'], storage=reference.get('storage'))
    except ValueError as error:
        value = read_source_bytes(reference['path'])
        warning = str(error)
    if digest(value) != reference['sha256']:
        raise ValueError('Decoded original SHA mismatch')
    return value, warning


class WhitespaceView:
    def __init__(self, text):
        self.text = text
        self.tokens = list(re.finditer(r'\S+', text))
        self.starts, self.parts = [], []
        position = 0
        for token in self.tokens:
            self.starts.append(position)
            self.parts.append(token[0])
            position += len(token[0])+1
        self.normalized = ' '.join(self.parts)

    def locate_unique(self, value):
        needle = ' '.join(value.split())
        if len(needle) < 12:
            return None
        at = self.normalized.find(needle)
        if at < 0 or self.normalized.find(needle, at+1) >= 0:
            return None
        end = at+len(needle)
        i = max(0, bisect_right(self.starts, at)-1)
        j = max(0, bisect_right(self.starts, end-1)-1)
        return (self.tokens[i].start()+at-self.starts[i],
                self.tokens[j].start()+end-self.starts[j])


def raw_html(raw, url):
    if raw.startswith((b'%PDF', b'\xd0\xcf', b'PK')):
        return None
    if raw.lstrip().startswith((b'{', b'[')):
        try:
            item = json.loads(raw)
            if isinstance(item, dict):
                content = item.get('content')
                value = item.get('body_html') or (content.get('rendered') if isinstance(content, dict) else None)
                if value:
                    return value
                for post in item.get('posts') or []:
                    if post.get('url') == url or post.get('URL') == url:
                        return post.get('content')
        except (ValueError, TypeError, KeyError):
            return None
        return None
    return raw if b'<' in raw[:2000] else None


def zone_candidates(raw, text, url, config):
    """Emit original offsets only for unambiguous source structure matches."""
    zones, failed = [], Counter()
    view = WhitespaceView(text)

    def add(start, end, zone, basis, locator=None):
        if 0 <= start < end <= len(text):
            zones.append({'char_start': start, 'char_end': end, 'zone': zone,
                'basis': basis, 'source_locator': locator, 'auto_excludable': zone != 'body',
                'span_sha256': digest(text[start:end].encode())})

    content = raw_html(raw, url)
    if content:
        try:
            document = html.fromstring(content)
            tree = document.getroottree()
            marker = re.compile(config['noise_marker_regex'], re.I)
            body_marker = re.compile(config.get('body_marker_regex', r'(?!)'), re.I)
            for node in document.iter():
                if not isinstance(node.tag, str):
                    continue
                tag, role = node.tag.lower(), node.get('role', '')
                attributes = ' '.join(node.get(key, '') for key in ('class', 'id', 'aria-label', 'data-testid'))
                ancestors = list(node.iterancestors())
                inside_article = any(parent.tag == 'article' for parent in ancestors)
                zone = None
                if marker.search(attributes):
                    zone = 'cookie_metadata' if re.search(r'cookie|onetrust', attributes, re.I) else 'related_or_navigation'
                elif tag == 'nav' or role == 'navigation':
                    zone = 'navigation'
                elif tag == 'footer' or role == 'contentinfo':
                    zone = 'footer'
                elif tag == 'header' and not inside_article:
                    zone = 'page_header'
                elif tag == 'aside' and not inside_article:
                    zone = 'sidebar'
                elif tag == 'section':
                    headings = node.xpath('./h1|./h2|./h3|./div/h1|./div/h2|./div/h3')
                    known = any(normalize(' '.join(h.itertext())).casefold() in config['related_section_headings'] for h in headings)
                    if known and len(node.xpath('.//article')) >= 2:
                        zone = 'related_posts'
                if zone is None and urlparse(url or '').hostname in config['higherin_menu_hosts'] and 'swiper-wrapper' in attributes:
                    destinations={link.get('href') for link in node.xpath('.//a[contains(@href,"/review/")]')}
                    if len(destinations)>=2:
                        zone='recommended_reviews'
                if zone is None and body_marker.search(attributes):
                    zone='body'
                if zone is None and (tag in ('main', 'article') or role == 'main'):
                    zone = 'body'
                if zone is None:
                    continue
                value = ' '.join(str(t) for t in node.xpath('.//text()[not(ancestor::script) and not(ancestor::style) and not(ancestor::noscript)]'))
                found = view.locate_unique(value)
                if found:
                    add(*found, zone, 'unique_dom_structure_alignment', tree.getpath(node))
                else:
                    failed[zone] += 1
                    # Paragraph/list fragments inherit a clear excluded container,
                    # but ambiguous repeated fragments stay unresolved.
                    if zone != 'body':
                        for child in node.xpath('.//p|.//li'):
                            fragment = ' '.join(child.itertext())
                            match = view.locate_unique(fragment)
                            if match:
                                add(*match, zone, 'unique_child_of_structural_noise_container', tree.getpath(child))
        except (etree.ParserError, ValueError, TypeError):
            failed['dom_parse'] += 1
    # This exact ordered menu was observed in the Codex-reviewed Higherin data.
    if urlparse(url or '').hostname in config['higherin_menu_hosts']:
        pattern = r'\s+'.join(re.escape(label).replace(r'\ ', r'\s+') for label in config['higherin_menu_lines'])
        for match in re.finditer(pattern, text):
            add(*match.span(), 'navigation', 'codex_observed_ordered_ui_menu_structure', 'higherin_review_section_menu_v1')
    lines = [(m.start(), m.end(), m[0]) for m in re.finditer(r'[^\r\n]+', text)]
    header = re.compile(r'^(?:From|To|Cc|Bcc|Sent|Subject|Date|보낸\s*사람|받는\s*사람|참조)\s*:', re.I)
    mail = re.compile(r'@|\[(?:e-?mail|recipient)\]', re.I)
    stamp = re.compile(r'\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),|\d{1,2}:\d{2}\s*(?:AM|PM)', re.I)
    for start, end, value in lines:
        if header.match(value.strip()) and (mail.search(value) or re.search(r'^(?:To|Cc|Bcc|From|Sent|Date)\s*:', value.strip(), re.I)):
            add(start, end, 'email_metadata', 'anchored_email_header_field')
        elif mail.search(value) and stamp.search(value) and len(value) < 300:
            add(start, end, 'email_metadata', 'email_sender_and_timestamp_line')
    pages = []
    position = 0
    for page in text.split('\f'):
        candidates = [(position+m.start(), position+m.end(), normalize(m[0])) for m in re.finditer(r'[^\r\n]+', page) if m[0].strip()]
        pages.append(candidates[:3]+candidates[-2:])
        position += len(page)+1
    if len(pages) >= 3:
        frequency = Counter(value for page in pages for value in {x[2] for x in page})
        for page in pages:
            for start, end, value in page:
                if frequency[value] >= 3 and 8 <= len(value) <= 160:
                    add(start, end, 'repeated_page_metadata', 'same_line_at_three_or_more_page_boundaries')
    unique = {}
    for zone in zones:
        key = (zone['char_start'], zone['char_end'], zone['zone'])
        unique.setdefault(key, zone)
    return list(unique.values()), dict(failed)


def classify_span(start, end, zones, text=None):
    noise = [z for z in zones if z['auto_excludable'] and z['char_start'] < end and z['char_end'] > start]
    intervals = sorted((max(start, z['char_start']), min(end, z['char_end'])) for z in noise)
    covered, cursor, gaps = 0, start, []
    for a, b in intervals:
        if b <= cursor:
            continue
        if a>cursor:gaps.append((cursor,a))
        covered += b-max(cursor, a)
        cursor = b
    if cursor<end:gaps.append((cursor,end))
    if intervals and text is not None and all(not text[a:b].strip() for a,b in gaps):
        covered=end-start
    if covered == end-start:
        return {'zone': 'structural_noise', 'auto_excluded': True,
            'zone_ids': [z['zone_id'] for z in noise], 'noise_characters': covered}
    if covered:
        return {'zone': 'mixed_body_and_noise', 'auto_excluded': False,
            'zone_ids': [z['zone_id'] for z in noise], 'noise_characters': covered}
    body = [z for z in zones if z['zone'] == 'body' and z['char_start'] <= start and z['char_end'] >= end]
    return {'zone': 'body' if body else 'unclassified', 'auto_excluded': False,
            'zone_ids': [z['zone_id'] for z in body], 'noise_characters': 0}


def sense_hints(text, base):
    """Compound expressions are hints, not newly approved word meanings."""
    patterns = {
        'application_as_software_candidate': r'\b(?:web|mobile|social media|e-commerce)\s+application\b|\bapplication layer protocol\b',
        'application_as_support_process_candidate': r'\bapplication process\b|지원\s*과정|장학금\s*신청',
        'feedback_as_product_function_candidate': r'\b(?:anonymous\w*|peer\w*)\b.{0,100}\b(?:review|feedback)\b|\b(?:review|feedback)\b.{0,100}\b(?:anonymous\w*|peer\w*)\b',
        'feedback_as_person_response_candidate': r'\b(?:mentor|supervisor|received)\b.{0,60}\bfeedback\b|피드백을\s*받',
        'writing_as_code_candidate': r'\bwriting\s+(?:the\s+)?tests\b|\bwrite\s+(?:the\s+)?tests\b',
    }
    rows = []
    for hint, pattern in patterns.items():
        for match in re.finditer(pattern, text, re.I|re.S):
            rows.append({'hint': hint, 'status': 'lexical_candidate',
                'char_start': base+match.start(), 'char_end': base+match.end(), 'exact_quote': match[0]})
    return rows


def project_occurrence(row, representative, annotation, text, zones, reference_provider='codex_subagent'):
    a, b, ca, cb = (row[x] for x in ('char_start', 'char_end', 'context_start', 'context_end'))
    valid = row['text_sha256']==digest(text.encode()) and 0 <= ca <= a < b <= cb <= len(text)
    if valid:
        valid = normalize(text[a:b]) == normalize(representative['text']) and normalize(text[ca:cb]) == normalize(representative['context_text'])
    quality = classify_span(a, b, zones, text) if valid else {'zone': 'source_binding_failed', 'auto_excluded': False, 'zone_ids': [], 'noise_characters': 0}
    basis = 'source_structure' if quality['auto_excluded'] else 'not_excluded'
    semantic_state, relevance = 'not_semantically_reviewed', None
    if annotation and valid:
        relevance = annotation.get('relevance')
        semantic_state = 'direct_codex_reference' if row['model_representative'] else 'code_reuse_of_exact_codex_context'
        if reference_provider!='codex_subagent':
            semantic_state='direct_semantic_model_reference' if row['model_representative'] else 'code_reuse_of_exact_model_context'
        if relevance == 'irrelevant':
            quality['auto_excluded'] = True
            basis = 'source_verified_exact_context_of_codex_irrelevant_group'
    return {key: row[key] for key in ('occurrence_id', 'group_id', 'record_key', 'document_id', 'text_sha256',
        'char_start', 'char_end', 'context_start', 'context_end', 'sentence_index', 'source_role', 'genre', 'source_url')} | {
        'source_binding_verified': valid, 'quality': quality, 'exclusion_basis': basis,
        'semantic_state': semantic_state, 'reference_relevance': relevance,
        'manual_reference_group_id': row['group_id'] if annotation else None,
        'semantic_reference_provider':reference_provider if annotation else None,
        'sense_hints': sense_hints(text[ca:cb], ca) if valid else [],
        'semantic_review': 'unreviewed', 'approved_pattern': False}


def process_document(document, root, role_map, config):
    result = dict(document_id=document['document_id'], source_url=document.get('source_url'),
        kind=document['kind'], raw_ref=document['raw_ref'], text_ref=document['text_ref'],
        declared_source_roles=sorted(role_map.get(document['document_id'], [])),
        declared_source_families=sorted({config['source_role_families'].get(role, 'unknown') for role in role_map.get(document['document_id'], [])}),
        source_role_status='metadata_not_content_approval')
    try:
        raw, raw_warning = verified_bytes(document['raw_ref'])
        text_bytes, text_warning = verified_bytes(document['text_ref'])
        text = text_bytes.decode('utf8')
        zones, unaligned = zone_candidates(raw, text, document.get('source_url'), config)
        for zone in zones:
            zone['document_id'] = document['document_id']
            zone['text_sha256'] = document['text_ref']['sha256']
            zone['zone_id'] = 'zone-'+digest(encoded([document['document_id'], zone]))[:28]
        result.update(integrity='decoded_original_hashes_verified', storage_warnings=[x for x in (raw_warning,text_warning) if x],
            text_characters=len(text), zones_count=len(zones), unaligned_structure_candidates=unaligned)
        return result, zones
    except (ValueError, OSError, UnicodeError, TypeError) as error:
        result.update(integrity='source_repair_required', error=type(error).__name__+': '+str(error)[:250])
        return result, []


def structure_fingerprint(experience_root, config_path=CONFIG, version=STRUCTURE_VERSION):
    sources={name:digest((experience_root/name).read_bytes()) for name in
        ('documents.jsonl.gz','cases.jsonl.gz','segments.jsonl.gz')}
    sources.update(config_sha256=digest(Path(config_path).read_bytes()),preprocessing_version=version)
    return {'sha256':digest(encoded(sources)),'inputs':sources}


def can_reuse_structure(root,fingerprint):
    try:
        manifest=json.loads((root/'structure-cache.json').read_text())
        if manifest['fingerprint']!=fingerprint:return False
        if any(digest((root/name).read_bytes())!=value for name,value in manifest['artifact_sha256'].items()):return False
        cx=sqlite3.connect('file:'+str(root/'quality.sqlite')+'?mode=ro',uri=True)
        try:
            return all(cx.execute('SELECT count(*) FROM '+name).fetchone()[0]==count
                for name,count in manifest['table_counts'].items())
        finally:cx.close()
    except (OSError,ValueError,KeyError,sqlite3.Error):return False


def record_structure_cache(root,experience_root):
    """Also bootstraps a completed first run without reparsing raw DOM bytes."""
    fingerprint=structure_fingerprint(experience_root)
    summary=json.loads((root/'summary.json').read_text())
    if summary['source_snapshot']['documents_sha256']!=fingerprint['inputs']['documents.jsonl.gz'] or summary['source_snapshot']['config_sha256']!=fingerprint['inputs']['config_sha256']:
        raise ValueError('Completed structure differs from current source/config fingerprint')
    if 'cases_sha256' not in summary['source_snapshot']:
        expected=defaultdict(set)
        for case in jsonl(experience_root/'cases.jsonl.gz'):
            for doc in case['input_documents']:
                expected[doc['document_id']].add(case['source_roles'].get('case_scope') or 'unknown')
        for doc in jsonl(root/'documents.jsonl.gz'):
            if set(doc['declared_source_roles'])!=expected[doc['document_id']]:
                raise ValueError('Case roles changed after the completed structure run')
    else:
        if summary['source_snapshot']['cases_sha256']!=fingerprint['inputs']['cases.jsonl.gz']:
            raise ValueError('Case source snapshot differs')
    cx=sqlite3.connect('file:'+str(root/'quality.sqlite')+'?mode=ro',uri=True)
    try:counts={name:cx.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('documents','zones','segments')}
    finally:cx.close()
    manifest={'fingerprint':fingerprint,'at_kst':datetime.now(KST).isoformat(),
        'artifact_sha256':{name:digest((root/name).read_bytes()) for name in STRUCTURE_FILES},'table_counts':counts}
    atomic_json(root/'structure-cache.json',manifest)
    return manifest


def paragraph_registry(segments):
    dom=[s for s in segments if s['kind']=='source_dom_paragraph_aligned']
    blocks=[s for s in segments if s['kind']=='blank_line_block_candidate' and not any(
        p['char_start']<s['char_end'] and p['char_end']>s['char_start'] for p in dom)]
    selected={ (s['char_start'],s['char_end'],s['kind']):s for s in dom+blocks }
    rows=[]
    for key,s in sorted(selected.items()):
        rows.append({'paragraph_id':'paragraph-'+digest(encoded([s['document_id'],s['text_sha256'],*key]))[:28],
            'document_id':s['document_id'],'text_sha256':s['text_sha256'],
            'paragraph_char_start':s['char_start'],'paragraph_char_end':s['char_end'],
            'paragraph_structure_kind':s['kind'],'segment_id':s['segment_id'],
            'source_span_hash_verified':s['source_span_hash_verified'],
            'semantic_paragraph_approval':False})
    for i,row in enumerate(rows):
        row['previous_paragraph_id']=rows[i-1]['paragraph_id'] if i else None
        row['next_paragraph_id']=rows[i+1]['paragraph_id'] if i+1<len(rows) else None
    return rows


def paragraph_position(start,end,paragraphs):
    containing=[p for p in paragraphs if p['paragraph_char_start']<=start<end<=p['paragraph_char_end']]
    base={'paragraph_id':None,'paragraph_char_start':None,'paragraph_char_end':None,
        'paragraph_structure_kind':None,'previous_paragraph_id':None,'next_paragraph_id':None,
        'opening_or_closing_boundary_match':'unknown','paragraph_order_is_logical_connection':False}
    if len(containing)==1:
        p=containing[0]
        for key in ('paragraph_id','paragraph_char_start','paragraph_char_end','paragraph_structure_kind','previous_paragraph_id','next_paragraph_id'):
            base[key]=p[key]
        first=start==p['paragraph_char_start'];last=end==p['paragraph_char_end']
        base['opening_or_closing_boundary_match']='only' if first and last else 'opening' if first else 'closing' if last else 'middle'
    elif any(p['paragraph_char_start']<end and p['paragraph_char_end']>start for p in paragraphs):
        base['opening_or_closing_boundary_match']='fragment'
        base['overlapping_paragraph_ids']=[p['paragraph_id'] for p in paragraphs if p['paragraph_char_start']<end and p['paragraph_char_end']>start]
    return base


def attach_paragraphs(root):
    """Postprocess existing source segments only; never parse raw DOM again."""
    db=quality_writer(root/'quality.sqlite')
    segment_hash=digest((root/'segments.jsonl.gz').read_bytes())
    cache_path=root/'paragraph-cache.json'
    cache=json.loads(cache_path.read_text()) if cache_path.exists() else {}
    reusable=cache.get('source_segments_sha256')==segment_hash and cache.get('policy_version')==PARAGRAPH_VERSION and (root/'paragraphs.jsonl.gz').exists()
    if reusable:
        try:
            reusable=digest((root/'paragraphs.jsonl.gz').read_bytes())==cache['paragraph_artifact_sha256'] and db.execute('SELECT count(*) FROM paragraphs').fetchone()[0]==cache['paragraphs']
        except (KeyError,sqlite3.Error):reusable=False
    if not reusable:
        db.executescript('DROP TABLE IF EXISTS paragraphs;CREATE TABLE paragraphs(paragraph_id TEXT PRIMARY KEY,document_id TEXT,char_start INTEGER,char_end INTEGER,payload TEXT);CREATE INDEX paragraphs_doc ON paragraphs(document_id);')
        count=0
        with JsonLines(root/'paragraphs.jsonl.gz') as output:
            for did,spans in groupby(jsonl(root/'segments.jsonl.gz'),key=lambda s:s['document_id']):
                for paragraph in paragraph_registry(list(spans)):
                    output.write(paragraph)
                    db.execute('INSERT INTO paragraphs VALUES(?,?,?,?,?)',(paragraph['paragraph_id'],did,paragraph['paragraph_char_start'],paragraph['paragraph_char_end'],encoded(paragraph)))
                    count+=1
        db.commit()
        cache={'source_segments_sha256':segment_hash,'policy_version':PARAGRAPH_VERSION,'paragraphs':count,
            'paragraph_artifact_sha256':digest((root/'paragraphs.jsonl.gz').read_bytes())}
        atomic_json(cache_path,cache)
    @lru_cache(maxsize=12)
    def paragraphs(did):
        return [json.loads(row[0]) for row in db.execute('SELECT payload FROM paragraphs WHERE document_id=? ORDER BY char_start,char_end',(did,))]
    stats=Counter();last=0
    with JsonLines(root/'occurrences.jsonl.gz') as output:
        while True:
            batch=db.execute('SELECT rowid,occurrence_id,payload FROM occurrences WHERE rowid>? ORDER BY rowid LIMIT 1000',(last,)).fetchall()
            if not batch:break
            for rid,oid,payload in batch:
                row=json.loads(payload)
                row.update(paragraph_position(row['char_start'],row['char_end'],paragraphs(row['document_id'])))
                output.write(row)
                db.execute('UPDATE occurrences SET payload=? WHERE occurrence_id=?',(encoded(row),oid))
                stats[row['opening_or_closing_boundary_match']]+=1;last=rid
            db.commit()
    last=0
    with JsonLines(root/'groups.jsonl.gz') as output:
        while True:
            batch=db.execute('SELECT rowid,group_id,payload FROM groups WHERE rowid>? ORDER BY rowid LIMIT 1000',(last,)).fetchall()
            if not batch:break
            for rid,gid,payload in batch:
                row=json.loads(payload);rep=row['representative_source_ref']
                row['representative_paragraph_structure']=paragraph_position(rep['char_start'],rep['char_end'],paragraphs(rep['document_id']))
                output.write(row);db.execute('UPDATE groups SET payload=? WHERE group_id=?',(encoded(row),gid));last=rid
            db.commit()
    db.close()
    result={'at_kst':datetime.now(KST).isoformat(),'source_paragraph_locators':cache['paragraphs'],
        'structural_registry_reused':reusable,'occurrence_boundary_counts':dict(stats),
        'scope':'source_dom_paragraph_or_blank_line_block_boundaries_not_semantic_paragraph_approval',
        'logical_connections_inferred_from_order':0,'raw_dom_reparsed':False,'model_calls':0}
    atomic_json(root/'paragraph-coverage.json',result)
    return result


def run(args):
    root = checked_root(args.root)/'quality'
    root.mkdir(parents=True, exist_ok=True)
    config = json.loads(CONFIG.read_text())
    fingerprint=structure_fingerprint(args.experience_root)
    with single_writer(root):
        if (root/'summary.json').exists():
            previous=(root/'summary.json').read_bytes();history=root/'history';history.mkdir(exist_ok=True)
            (history/('summary-'+digest(previous)+'.json')).write_bytes(previous)
        dbpath = root/'quality.sqlite'
        db = quality_writer(dbpath)
        reuse=bool(getattr(args,'reuse_structure',False) and can_reuse_structure(root,fingerprint))
        db.executescript('DROP TABLE IF EXISTS occurrences; DROP TABLE IF EXISTS groups;'
            'CREATE TABLE occurrences(occurrence_id TEXT PRIMARY KEY,group_id TEXT,document_id TEXT,text_sha256 TEXT,sentence_index INTEGER,zone TEXT,auto_excluded INTEGER,semantic_state TEXT,payload TEXT);'
            'CREATE INDEX occurrence_group ON occurrences(group_id);'
            'CREATE TABLE groups(group_id TEXT PRIMARY KEY,state TEXT,reference_relevance TEXT,auto_excluded_occurrences INTEGER,occurrences INTEGER,payload TEXT);')
        inventory,stats={},defaultdict(Counter)
        if reuse:
            inventory={row['document_id']:row for row in jsonl(root/'documents.jsonl.gz')}
            documents=list(inventory.values())
            previous=json.loads((root/'summary.json').read_text())['counters']
            for key in ('document_integrity','declared_document_roles','declared_document_families','zone_types','segment_zones','segment_hash_checks'):
                stats[key].update(previous.get(key) or {})
            print(encoded({'stage':'reuse_verified_structure','documents':len(documents),'fingerprint':fingerprint['sha256']}),flush=True)
        else:
            documents=list(jsonl(args.experience_root/'documents.jsonl.gz'))
            role_map=defaultdict(set)
            for case in jsonl(args.experience_root/'cases.jsonl.gz'):
                for doc in case['input_documents']:
                    role_map[doc['document_id']].add(case['source_roles'].get('case_scope') or 'unknown')
            db.executescript('DROP TABLE IF EXISTS documents; DROP TABLE IF EXISTS zones; DROP TABLE IF EXISTS segments;'
                'CREATE TABLE documents(document_id TEXT PRIMARY KEY,text_sha256 TEXT,payload TEXT);'
                'CREATE TABLE zones(zone_id TEXT PRIMARY KEY,document_id TEXT,char_start INTEGER,char_end INTEGER,zone TEXT,auto_excludable INTEGER,payload TEXT);'
                'CREATE INDEX zones_doc ON zones(document_id);'
                'CREATE TABLE segments(segment_id TEXT PRIMARY KEY,document_id TEXT,zone TEXT,auto_excluded INTEGER,payload TEXT);')
            with JsonLines(root/'documents.jsonl.gz') as docout, JsonLines(root/'zones.jsonl.gz') as zoneout:
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    iterator = pool.map(lambda d: process_document(d, root, role_map, config), documents)
                    for number, (doc, zones) in enumerate(iterator, 1):
                        inventory[doc['document_id']] = doc
                        docout.write(doc)
                        db.execute('INSERT INTO documents VALUES(?,?,?)',(doc['document_id'],doc['text_ref']['sha256'],encoded(doc)))
                        stats['document_integrity'][doc['integrity']] += 1
                        stats['declared_document_roles'].update(doc['declared_source_roles'])
                        stats['declared_document_families'].update(doc['declared_source_families'])
                        for zone in zones:
                            zoneout.write(zone)
                            db.execute('INSERT INTO zones VALUES(?,?,?,?,?,?,?)',(zone['zone_id'],doc['document_id'],zone['char_start'],zone['char_end'],zone['zone'],int(zone['auto_excludable']),encoded(zone)))
                            stats['zone_types'][zone['zone']] += 1
                        if number % 1000 == 0:
                            db.commit();atomic_json(root/'progress.json',{'stage':'source_zones','documents':number,'total':len(documents)})
                            print(encoded({'stage':'source_zones','documents':number,'total':len(documents)}),flush=True)
            db.commit()

        @lru_cache(maxsize=12)
        def doc_input(did):
            doc = inventory[did]
            text = verified_bytes(doc['text_ref'])[0].decode('utf8')
            zones = [json.loads(row[0]) for row in db.execute('SELECT payload FROM zones WHERE document_id=?',(did,))]
            return text, zones

        if not reuse:
            with JsonLines(root/'segments.jsonl.gz') as output:
                for span in jsonl(args.experience_root/'segments.jsonl.gz'):
                    text, zones = doc_input(span['document_id'])
                    a,b = span['char_start'],span['char_end']
                    valid = digest(text[a:b].encode()) == span['segment_text_sha256']
                    classification = classify_span(a,b,zones,text)
                    record = dict(span, text_sha256=inventory[span['document_id']]['text_ref']['sha256'],
                        source_span_hash_verified=valid, quality=classification, semantic_review='not_performed')
                    output.write(record)
                    db.execute('INSERT INTO segments VALUES(?,?,?,?,?)',(span['segment_id'],span['document_id'],classification['zone'],int(classification['auto_excluded']),encoded(record)))
                    stats['segment_zones'][classification['zone']] += 1
                    stats['segment_hash_checks']['matched' if valid else 'failed'] += 1
            db.commit()
        cx = readonly(args.compact_db)
        manual_groups,reference_providers = {},{}
        for row in cx.execute("SELECT group_id,result_json,receipt_path FROM groups WHERE kind='experience' AND state='analyzed'"):
            annotation = json.loads(row['result_json'])
            manual_groups[row['group_id']] = annotation
            try:reference_providers[row['group_id']]=json.loads(Path(row['receipt_path']).read_text()).get('provider','unknown')
            except (OSError,ValueError,TypeError):reference_providers[row['group_id']]='unknown'
        query = """SELECT o.*,g.representative_json FROM occurrences o JOIN groups g ON g.group_id=o.group_id
            WHERE o.kind='experience' ORDER BY o.rowid"""
        with JsonLines(root/'occurrences.jsonl.gz') as output:
            for number,row in enumerate(cx.execute(query),1):
                try:
                    text,zones = doc_input(row['document_id'])
                    record = project_occurrence(row,json.loads(row['representative_json']),manual_groups.get(row['group_id']),text,zones,reference_providers.get(row['group_id']))
                except (ValueError,KeyError,OSError,UnicodeError) as error:
                    record = {key:row[key] for key in row.keys() if key!='representative_json'}
                    record.update(source_binding_verified=False,quality={'zone':'source_unavailable','auto_excluded':False},semantic_state='not_semantically_reviewed',error=str(error)[:250],sense_hints=[])
                output.write(record)
                q=record['quality']
                db.execute('INSERT INTO occurrences VALUES(?,?,?,?,?,?,?,?,?)',(row['occurrence_id'],row['group_id'],row['document_id'],row['text_sha256'],row['sentence_index'],q['zone'],int(q['auto_excluded']),record['semantic_state'],encoded(record)))
                stats['occurrence_zones'][q['zone']] += 1
                stats['occurrence_semantic_states'][record['semantic_state']] += 1
                stats['occurrence_source_binding']['verified' if record['source_binding_verified'] else 'failed'] += 1
                stats['occurrence_exclusion']['excluded' if q['auto_excluded'] else 'retained_or_unknown'] += 1
                stats['occurrence_source_roles'][row['source_role'] or 'unknown'] += 1
                stats['word_sense_hints'].update(x['hint'] for x in record['sense_hints'])
                if number % 10000 == 0:
                    db.commit();atomic_json(root/'progress.json',{'stage':'compact_occurrences','occurrences':number})
        db.commit()
        aggregate = {r[0]:(r[1],r[2]) for r in db.execute('SELECT group_id,count(*),sum(auto_excluded) FROM occurrences GROUP BY group_id')}
        with JsonLines(root/'groups.jsonl.gz') as output:
            for row in cx.execute("SELECT * FROM groups WHERE kind='experience'"):
                rep=json.loads(row['representative_json']);annotation=manual_groups.get(row['group_id'])
                count,excluded=aggregate.get(row['group_id'],(0,0))
                record={'group_id':row['group_id'],'source_group_state':row['state'],
                    'representative_source_ref':{key:rep.get(key) for key in ('document_id','text_sha256','char_start','char_end','sentence_index','case_key','source_url','source_role','genre')},
                    'occurrences':count,'auto_excluded_occurrences':excluded,
                    'semantic_reference':annotation,'manual_interpretation_present':reference_providers.get(row['group_id'])=='codex_subagent',
                    'semantic_reference_provider':reference_providers.get(row['group_id']),
                    'semantic_interpretation_reference_present':bool(annotation),
                    'interpretation_scope':'selected_passage_only' if annotation else 'not_semantically_interpreted'}
                output.write(record)
                db.execute('INSERT INTO groups VALUES(?,?,?,?,?,?)',(row['group_id'],row['state'],annotation.get('relevance') if annotation else None,excluded,count,encoded(record)))
                stats['group_semantic_coverage']['semantic_reference' if annotation else 'not_interpreted'] += 1
        db.commit();cx.close()
        counters={key:dict(value) for key,value in stats.items()}
        summary={'version':config['version'],'at_kst':datetime.now(KST).isoformat(),
            'scope':'all_stored_experience_documents_segments_and_compact_occurrences',
            'source_snapshot':{'experience_summary':str(args.experience_root/'summary.json'),'compact_db':str(args.compact_db),
                'documents_sha256':digest((args.experience_root/'documents.jsonl.gz').read_bytes()),
                'config_sha256':digest(CONFIG.read_bytes()),
                'cases_sha256':fingerprint['inputs']['cases.jsonl.gz'],
                'preprocessing_version':STRUCTURE_VERSION,'structure_fingerprint':fingerprint['sha256']},
            'structure_reused':reuse,
            'source_documents':len(documents),'reference_semantic_groups':len(manual_groups),
            'reference_interpretation_providers':dict(Counter(reference_providers.values())),
            'new_manual_semantic_groups_this_run':0,'new_model_calls':0,'external_requests':0,
            'source_database_writes':0,'canonical_database_writes':0,
            'whole_document_latent_semantics_completed':0,'counters':counters,
            'derived_database':str(dbpath),
            'distinct_occurrence_source_sentences':db.execute('SELECT count(*) FROM (SELECT document_id,text_sha256,sentence_index FROM occurrences GROUP BY 1,2,3)').fetchone()[0],
            'source_role_classification':'declared_metadata_counts; passage meanings require their own semantic reference',
            'automatic_exclusion_scope':config['automatic_exclusion']}
        atomic_json(root/'summary.json',summary);db.close()
        summary['paragraph_structure']=attach_paragraphs(root)
        atomic_json(root/'summary.json',summary)
        if not reuse:record_structure_cache(root,args.experience_root)
        atomic_json(root/'progress.json',{'stage':'complete','at_kst':summary['at_kst']})
        print(encoded(summary),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=CACHE/'team-production-20261119/team-expansion-20261120/experience-personalization')
    parser.add_argument('--experience-root',type=Path,default=CACHE/'team-production-20261119/experience')
    parser.add_argument('--compact-db',type=Path,default=CACHE/'team-production-20261119/compact/queue.sqlite')
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--reuse-structure',action='store_true')
    parser.add_argument('--attach-paragraphs-only',action='store_true')
    options=parser.parse_args()
    if options.attach_paragraphs_only:
        quality_root=checked_root(options.root)/'quality'
        with single_writer(quality_root):
            result=attach_paragraphs(quality_root)
            summary=json.loads((quality_root/'summary.json').read_text());summary['paragraph_structure']=result
            atomic_json(quality_root/'summary.json',summary);print(encoded(result),flush=True)
    else:run(options)
