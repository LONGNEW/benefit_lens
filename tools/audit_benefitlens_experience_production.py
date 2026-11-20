"""Read-only production corpus audit, structural inputs and shared-catalog demands.

Source text segmentation is preprocessing. It does not create semantic events,
approve a writing pattern, infer applicant facts or decide eligibility.
"""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import gzip
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from lxml import etree, html as lxml_html

import project
from benefitlens_source_storage import read_source_bytes, store_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json, checked_root, single_writer

CONFIG = project.ROOT/'pipeline/config/personalization_production_v2.json'


def digest(value):
    return sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def excluded_case(row):
    payload = row['payload']
    if payload.get('legacy_case_id') or row['case_key'].startswith('writing-corpus-legacy-'):
        return 'legacy_case_not_production_metric'
    if payload.get('example_kind') == 'synthetic' or payload.get('sample_scope') == 'demo':
        return 'synthetic_or_demo_not_production_metric'
    return None


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def jsonl(path):
    with gzip.open(path, 'rt', encoding='utf8') as stream:
        for line in stream:
            yield json.loads(line)


class JsonLines:
    def __init__(self, path):
        self.path, self.temporary = path, path.with_suffix(path.suffix+'.tmp')
        self.stream = None

    def __enter__(self):
        self.stream = gzip.open(self.temporary, 'wt', encoding='utf8')
        return self

    def write(self, value):
        self.stream.write(encoded(value)+'\n')

    def __exit__(self, kind, value, traceback):
        self.stream.close()
        if kind is None:
            self.temporary.replace(self.path)


def structural_segments(text, html=None, *, max_characters=2000, source_url=None):
    """Retain exact text offsets, distinguishing layout and aligned DOM units."""
    if max_characters < 1:
        raise ValueError('Positive segment bound required')
    line_ends = [match.start() for match in re.finditer('\n', text)]
    result = []

    def append(start, end, kind):
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end-1].isspace():
            end -= 1
        if start < end:
            result.append({'char_start': start, 'char_end': end,
                'line_start': bisect_right(line_ends, start-1)+1,
                'line_end': bisect_right(line_ends, end-1)+1, 'kind': kind,
                'segment_text_sha256': digest(text[start:end])})

    cursor = 0
    breaks = list(re.finditer(r'\n[ \t]*\n+', text))
    blocks = []
    for match in breaks:
        blocks.append((cursor, match.start()))
        cursor = match.end()
    blocks.append((cursor, len(text)))
    for start, end in blocks:
        position = start
        while position < end:
            boundary = min(end, position+max_characters)
            if boundary < end:
                newline = text.rfind('\n', position+1, boundary+1)
                if newline > position:
                    boundary = newline+1
            kind = 'blank_line_block_candidate' if breaks and end-start <= max_characters else 'bounded_text_layout_chunk'
            append(position, boundary, kind)
            position = boundary
    if html:
        try:
            document = lxml_html.fromstring(html)
        except (etree.ParserError, ValueError):
            return result
        containers = document.xpath('//main | //article | //*['
            'contains(concat(" ",normalize-space(@class)," ")," entry-content ") or '
            'contains(concat(" ",normalize-space(@class)," ")," post-content ")]')
        main = containers[0] if containers else document
        search_from = 0
        paragraph_xpath = './/p'
        if urlsplit(source_url or '').hostname in {'higherin.com', 'www.higherin.com'}:
            # Review answers use div/span containers, while p elements hold
            # ratings and metadata. Preserve their exact source text offsets.
            # Containers with p children already have paragraph boundaries.
            paragraph_xpath += (' | .//div['
                'contains(concat(" ",normalize-space(@class)," ")," bg-web-white-smoke ") and '
                'contains(concat(" ",normalize-space(@class)," ")," whitespace-pre-line ") and '
                'contains(concat(" ",normalize-space(@class)," ")," break-words ") and not(.//p)]')
        for paragraph in main.xpath(paragraph_xpath):
            tokens = ' '.join(paragraph.itertext()).split()
            if not tokens or sum(map(len, tokens)) > 20000:
                continue
            pattern = r'\s+'.join(re.escape(token) for token in tokens)
            match = re.search(pattern, text[search_from:])
            if match:
                start, end = search_from+match.start(), search_from+match.end()
                append(start, end, 'source_dom_paragraph_aligned')
                search_from = end
    return result


def source_html(raw, metadata):
    if raw.startswith(b'%PDF') or raw.startswith(b'\xd0\xcf'):
        return None
    if raw.lstrip().startswith((b'{', b'[')):
        try:
            obj = json.loads(raw)
            pointer = metadata.get('experience_scale', {}).get('record_pointer', '') or ''
            if pointer.startswith('/posts/'):
                return obj['posts'][int(pointer.rsplit('/', 1)[1])].get('content')
            if isinstance(obj, dict):
                content = obj.get('content')
                return obj.get('body_html') or (content.get('rendered') if isinstance(content, dict) else None)
        except (ValueError, TypeError, KeyError, IndexError):
            return None
        return None
    return raw if b'<' in raw[:2000] else None


def snapshot_database():
    keys = ['source_family', 'source_id', 'active', 'semantic_review', 'approved_pattern',
            'document_ids', 'case_scope', 'document_roles', 'evidence_roles', 'quality_audit',
            'identity_status', 'author_handle', 'program_binding_level', 'document_finality',
            'experience_passages', 'experience_profile', 'evidence', 'evidence_spans',
            'event_decomposition', 'semantic_observations', 'model_called', 'legacy_case_id',
            'example_kind', 'sample_scope', 'merged_into', 'outcome_basis']
    projection = ','.join("'"+key+"',payload->'"+key+"'" for key in keys)
    with project.connect() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        at = conn.execute('SELECT CURRENT_TIMESTAMP').fetchone()[0].astimezone(KST).isoformat()
        rows = conn.execute('SELECT case_key,program_id,application_round,outcome_claim,evidence_status,'
            'source_url,person_name IS NOT NULL AND person_name!=\'\',jsonb_build_object('+projection+') '
            'FROM benefitlens.experience_discovery_case ORDER BY case_key').fetchall()
        cases = [dict(zip(('case_key', 'program_id', 'round_id', 'outcome', 'evidence_status',
                           'source_url', 'person_name_present', 'payload'), row)) for row in rows]
        identifiers = sorted({key for row in cases if not excluded_case(row)
                              for key in row['payload'].get('document_ids') or []})
        docrows = conn.execute("""SELECT document_id,canonical_url,kind,raw_path,text_path,
            content_sha256,text_sha256,fetched_at,jsonb_build_object(
            'raw_storage',metadata->'raw_storage','text_storage',metadata->'text_storage',
            'experience_scale',coalesce(metadata->'experience_scale','{}'::jsonb)-'full_text',
            'writing_corpus',coalesce(metadata->'writing_corpus','{}'::jsonb)-'full_text')
            FROM benefitlens.pipeline_document WHERE document_id=ANY(%s) ORDER BY document_id""", (identifiers,)).fetchall()
        documents = [dict(zip(('document_id', 'url', 'kind', 'raw_path', 'text_path',
                              'raw_sha256', 'text_sha256', 'fetched_at', 'metadata'), row)) for row in docrows]
        for doc in documents:
            doc['fetched_at'] = doc['fetched_at'].isoformat()
        tables = ['experience_fact', 'profile_snapshot', 'evaluation', 'draft_version',
                  'opportunity_version', 'eligibility_rule']
        counts = {table: conn.execute('SELECT count(*) FROM benefitlens.'+table).fetchone()[0] for table in tables}
        scopes = conn.execute("SELECT record_type,count(*) FROM benefitlens.support_discovery_record GROUP BY record_type").fetchall()
    return at, cases, documents, counts, dict(scopes)


def process_document(doc, root, max_characters):
    meta = doc['metadata']
    es, wc = meta.get('experience_scale') or {}, meta.get('writing_corpus') or {}
    raw_ref = {'path': doc['raw_path'], 'sha256': doc['raw_sha256'],
        'storage': meta.get('raw_storage') or (es.get('raw') or {}).get('storage') or (wc.get('raw') or {}).get('storage')}
    text_ref = {'path': doc['text_path'], 'sha256': doc['text_sha256'],
        'storage': meta.get('text_storage') or (es.get('text') or {}).get('storage') or (wc.get('text') or {}).get('storage')}
    result = {'document_id': doc['document_id'], 'source_url': doc['url'], 'kind': doc['kind'],
        'raw_ref': raw_ref, 'text_ref': text_ref, 'fetched_at': doc['fetched_at'],
        'source_role_metadata': {'material_role_hint': es.get('material_role_hint') or wc.get('document_role'),
            'evidence_roles': es.get('evidence_roles') or [], 'source_role': wc.get('role'),
            'source_id': es.get('source_id') or wc.get('source_id')},
        'semantic_review': 'unreviewed', 'source_verification': {}, 'segment_count': 0}
    try:
        if not raw_ref['path'] or not text_ref['path'] or not raw_ref['sha256'] or not text_ref['sha256']:
            raise ValueError('Original and text paths with checksums are required')
        raw = read_source_bytes(raw_ref['path'], storage=raw_ref['storage'])
        text_raw = read_source_bytes(text_ref['path'], storage=text_ref['storage'])
        if digest(raw) != raw_ref['sha256'] or digest(text_raw) != text_ref['sha256']:
            raise ValueError('Original or projected text checksum mismatch')
        text = text_raw.decode('utf8')
        if not text.strip() or '\x00' in text:
            raise ValueError('Stored text is empty or contains a zero byte')
        segments = structural_segments(text, source_html(raw, meta), max_characters=max_characters,
                                       source_url=doc['url'])
        for segment in segments:
            segment['segment_id'] = 'segment-'+digest(encoded([doc['document_id'], doc['text_sha256'],
                segment['char_start'], segment['char_end'], segment['kind']]))[:28]
            segment['document_id'] = doc['document_id']
        package = {'contract_version': 'source-segments-v1', 'document_id': doc['document_id'],
            'text_ref': text_ref, 'semantic_analysis_performed': False,
            'offset_unit': 'Python Unicode code points in unchanged stored text', 'segments': segments}
        ref = store_source_bytes(encoded(package).encode(), root/'segment-inputs', suffix='.json')
        result.update(source_verification={'state': 'raw_and_text_hash_verified',
            'raw_sha256_verified': True, 'text_sha256_verified': True}, segments_ref=ref,
            segment_count=len(segments), segment_kinds=dict(Counter(row['kind'] for row in segments)),
            text_characters=len(text))
        return result, segments
    except (ValueError, OSError, UnicodeError, etree.ParserError) as error:
        result['source_verification'] = {'state': 'source_repair_required',
                                         'reason': type(error).__name__+': '+str(error)[:240]}
        return result, []


def case_projection(row, documents, config):
    payload = row['payload']
    ids = payload.get('document_ids') or []
    inputs, missing = [], []
    for key in ids:
        doc = documents.get(key)
        if not doc:
            missing.append(key)
        else:
            inputs.append({name: doc.get(name) for name in ('document_id', 'source_url', 'kind',
                'raw_ref', 'text_ref', 'segments_ref', 'source_role_metadata', 'source_verification')})
    blockers = []
    if not ids:
        blockers.append('no_document_ids')
    if missing:
        blockers.append('document_ids_unresolved')
    if any(doc['source_verification']['state'] != 'raw_and_text_hash_verified' for doc in inputs):
        blockers.append('original_or_text_integrity_failed')
    active = payload.get('active') is not False and row['evidence_status'] != 'merged_duplicate'
    if not active:
        blockers.append('inactive_or_merged_case')
    decomposition = payload.get('event_decomposition')
    prior_semantic = payload.get('semantic_observations')
    stages = {'source_linked': bool(ids) and not missing,
        'original_integrity_verified': bool(inputs) and not missing and not any('integrity' in x for x in blockers),
        'structure_preprocessed': bool(inputs) and not missing and all(doc.get('segments_ref') for doc in inputs),
        'existing_passages_present': bool(payload.get('experience_passages')),
        'existing_experience_profile_present': bool(payload.get('experience_profile')),
        'event_decomposition_present': bool(decomposition),
        'case_semantic_observations_present': bool(prior_semantic),
        'meaning_approved': payload.get('semantic_review') == 'accepted',
        'pattern_approved': payload.get('approved_pattern') is True}
    unresolved = []
    if not row['round_id']:
        unresolved.append('program_round')
    if not row['person_name_present']:
        unresolved.append('named_person_identity')
    if not decomposition:
        unresolved.extend(config['writing_experience_channel']['optional_event_slots'])
    stage = 'skip_inactive_or_merged' if not active else 'repair_source_inputs' if blockers else 'extract_event_candidates'
    result = {'case_key': row['case_key'], 'program_id': row['program_id'], 'round_id': row['round_id'],
        'source_family': payload.get('source_family'), 'source_url': row['source_url'],
        'active': active, 'evidence_status': row['evidence_status'],
        'source_roles': {'case_scope': payload.get('case_scope'), 'document_roles': payload.get('document_roles') or [],
                         'evidence_roles': payload.get('evidence_roles') or []},
        'person_identity': {'named_person_present': row['person_name_present'],
            'identity_status': payload.get('identity_status'), 'cross_source_identity_verified': False},
        'source_outcome': {'claim': row['outcome'], 'basis': payload.get('outcome_basis'),
                           'causal_writing_success_label': False},
        'input_documents': inputs, 'missing_document_ids': missing,
        'existing_unreviewed_annotations': {
            key: payload.get(key) for key in ('experience_passages', 'experience_profile',
                'evidence', 'evidence_spans', 'event_decomposition', 'semantic_observations')
            if payload.get(key)},
        'analysis_stages': stages, 'unresolved_fields': unresolved,
        'unresolved_event_slots_are_not_universally_required': True,
        'semantic_review': payload.get('semantic_review') or 'unreviewed',
        'new_semantic_judgment': False, 'production_notice_links': [],
        'production_notice_binding': 'requires_reviewed_program_and_round_mapping',
        'next_stage': stage, 'blockers': blockers}
    job = {'job_id': 'experience-next-'+digest(encoded([row['case_key'],
           [(x['document_id'], x['text_ref']['sha256']) for x in inputs], config['contract_version']]))[:28],
        'case_key': row['case_key'], 'program_id': row['program_id'], 'round_id': row['round_id'],
        'next_stage': stage, 'inputs': inputs, 'blockers': blockers,
        'pending_steps': ['case_event_candidate_extraction', 'source_question_and_use_binding',
                          'meaning_review', 'production_notice_round_binding'],
        'semantic_review': 'unreviewed', 'automatic_approval_allowed': False}
    return result, job


def personalization_requirements(notice_root, root, catalog, catalog_sha):
    path = notice_root/'notices.jsonl.gz'
    if not path.exists():
        return {'state': 'waiting_for_notice_projection', 'path': str(path)}
    field_defs = catalog['fields']
    field_counts, stage_counts, unknown_counts, phase_counts = Counter(), Counter(), Counter(), Counter()
    candidates_by_field = defaultdict(set)
    seen_candidates, seen_conditions = set(), {}
    count = 0
    with JsonLines(root/'personalization-requirements.jsonl.gz') as output:
        for row in jsonl(path):
            key = row['candidate_key']
            if key in seen_candidates:
                raise ValueError('Duplicate candidate identity in notice projection')
            seen_candidates.add(key)
            channels = {name: [] for name in ('common_profile', 'program_followup', 'not_applicant_input')}
            for condition in row.get('conditions') or []:
                field = condition['field_id']
                if field not in field_defs:
                    raise ValueError('Condition field is absent from the shared catalog')
                missing = set(catalog['condition_contract']['required'])-condition.keys()
                if missing or condition['candidate_key'] != key:
                    raise ValueError('Condition contract or candidate binding mismatch')
                if condition['status'] not in catalog['condition_contract']['status_values']:
                    raise ValueError('Condition status is outside the shared candidate contract')
                cid = condition['condition_id']
                if cid in seen_conditions:
                    raise ValueError('Duplicate condition identity')
                seen_conditions[cid] = digest(encoded(condition))
                stage = field_defs[field]['applicant_input_stage']
                if stage not in channels:
                    raise ValueError('Catalog applicant stage is unsupported')
                channels[stage].append(condition)
                field_counts[field] += 1
                candidates_by_field[field].add(key)
                stage_counts[stage] += 1
                unknown_counts.update(condition.get('unknown_reasons') or [])
                phase_counts[(condition.get('at') or {}).get('phase') or 'unknown'] += 1
            output.write({'candidate_key': key, 'catalog_sha256': catalog_sha,
                'condition_ids': [c['condition_id'] for group in channels.values() for c in group],
                'input_channels': channels, 'source_projection_path': str(path),
                'source_projection_state': row.get('projection_state') or row.get('status') or row.get('extraction_status'),
                'notice_scope': row.get('scope'), 'source_original_ref': row.get('original_ref'),
                'source_origin': row.get('source_origin'),
                'current_open_verified': row.get('current_open_verified') is True,
                'variant_count': row.get('variant_count'),
                'variant_scope': 'condition mentions belong only to the source document linked by T1',
                'decision': 'unknown', 'user_questions_issued': 0,
                'why_unknown': ['unreviewed_mentions_do_not_define_complete_condition_logic',
                                'no_provenance_bound_user_profile_supplied'],
                'unresolved_source_reasons': row.get('unknown_reasons') or [],
                'unresolved_fields': row.get('unresolved_fields') or [],
                'writing_experience_channel': {'state': 'reviewed_official_task_spec_required',
                    'required_applicant_fact_fields': [],
                    'optional_event_slot_config': str(CONFIG)+'#/writing_experience_channel/optional_event_slots',
                    'document_mentions_are_not_essay_requirements': True},
                'meaning_approved': False, 'is_demo_performance_record': False})
            count += 1
    flat_path = notice_root/'conditions.jsonl.gz'
    flat_ids, flat_count = set(), 0
    for row in jsonl(flat_path):
        flat_ids.add(row['condition_id'])
        flat_count += 1
        if seen_conditions.get(row['condition_id']) != digest(encoded(row)):
            raise ValueError('Embedded and flat shared condition content differs')
    if flat_count != len(flat_ids):
        raise ValueError('Duplicate condition identity in flat projection')
    if flat_ids != set(seen_conditions):
        raise ValueError('Embedded and flat shared condition identities differ')
    distribution = {'state': 'candidate_demand_inventory_not_confirmed_requirements',
        'candidate_records': count, 'condition_mentions': sum(field_counts.values()),
        'catalog_sha256': catalog_sha, 'field_counts': {key: field_counts[key] for key in field_defs},
        'candidate_counts_by_field': {key: len(candidates_by_field[key]) for key in field_defs},
        'input_stage_condition_counts': dict(stage_counts), 'unknown_reason_counts': dict(unknown_counts),
        'reference_phase_condition_counts': dict(phase_counts),
        'no_condition_detected_is_not_no_requirement': True,
        'personal_eligibility_decisions_issued': 0, 'user_questions_issued': 0}
    atomic_json(root/'condition-demand-distribution.json', distribution)
    return distribution


def analysis_artifacts():
    exports = read_json(CACHE/'experience-scale-v1/model-inputs/manifest.json', {})
    semantic_status = read_json(CACHE/'semantic-intent-v1/status.json', {})
    receipt_path = Path(semantic_status['prior_run_receipt']) if semantic_status.get('prior_run_receipt') else None
    semantic_receipt = read_json(receipt_path, {}) if receipt_path else {}
    observed_path = Path(semantic_status['result_path']) if semantic_status.get('result_path') else None
    observations = read_json(observed_path, {}).get('observations', []) if observed_path else []
    descriptive = read_json(CACHE/'writing-foundations-v1/pattern-analysis-v1/analysis.json', {})
    examples = read_json(CACHE/'experience-patterns-v1/examples.json', {})
    return {'full_case_text_export': exports,
        'case_text_export_is_semantic_analysis': False,
        'source_bound_question_semantics': {'receipt_path': str(receipt_path),
            'model_called_in_original_run': semantic_receipt.get('model_called'),
            'original_run_at': semantic_receipt.get('at_kst'), 'observations': len(observations),
            'approved_observations': sum(row.get('semantic_review') == 'accepted' for row in observations),
            'not_a_production_case_event_decomposition': True},
        'descriptive_writing_analysis': {'path': str(CACHE/'writing-foundations-v1/pattern-analysis-v1/analysis.json'),
            'at_kst': descriptive.get('at_kst'), 'persuade_essays': descriptive.get('persuade', {}).get('essays'),
            'ellipse_essays': descriptive.get('ellipse', {}).get('essays'),
            'semantic_classification_of_all_applications_performed': descriptive.get('semantic_classification_of_all_applications_performed')},
        'assistant_interpreted_examples': {'source_count': examples.get('source_count'),
            'pattern_count': examples.get('pattern_count'), 'model_batch_called': examples.get('model_batch_called'),
            'interpretation_author': examples.get('interpretation_author')}}


def run(args):
    root = checked_root(args.root)
    config = read_json(CONFIG)
    catalog_path = project.ROOT/config['shared_condition_catalog']
    catalog = read_json(catalog_path)
    if not catalog or not isinstance(catalog.get('fields'), dict):
        raise ValueError('The shared condition catalog is required')
    catalog_sha = digest(catalog_path.read_bytes())
    with single_writer(root):
        at, all_cases, docs, table_counts, notice_types = snapshot_database()
        excluded = Counter(excluded_case(row) for row in all_cases if excluded_case(row))
        cases = [row for row in all_cases if not excluded_case(row)]
        processed = {}
        document_kinds, segment_kinds, integrity_states = Counter(), Counter(), Counter()
        with JsonLines(root/'documents.jsonl.gz') as docout, JsonLines(root/'segments.jsonl.gz') as segout:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                work = pool.map(lambda doc: process_document(doc, root,
                    config['structural_preprocessing']['max_segment_characters']), docs)
                for number, (doc, segments) in enumerate(work, 1):
                    processed[doc['document_id']] = doc
                    docout.write(doc)
                    for segment in segments:
                        segout.write(segment)
                    document_kinds[doc['kind']] += 1
                    segment_kinds.update(doc.get('segment_kinds') or {})
                    integrity_states[doc['source_verification']['state']] += 1
                    if number % 1000 == 0:
                        atomic_json(root/'progress.json', {'at_kst': datetime.now(KST).isoformat(),
                            'state': 'verifying_and_structurally_segmenting', 'documents_done': number,
                            'documents_total': len(docs), 'semantic_analysis_performed': False})
                        print(encoded({'documents_done': number, 'documents_total': len(docs)}), flush=True)
        family_counts, scope_counts, role_counts, stage_counts, queue_counts = Counter(), Counter(), Counter(), Counter(), Counter()
        named, rounds, active, unreviewed, missing_refs = 0, 0, 0, 0, 0
        with JsonLines(root/'cases.jsonl.gz') as output, JsonLines(root/'next-processing-queue.jsonl.gz') as queue:
            for row in cases:
                result, job = case_projection(row, processed, config)
                output.write(result); queue.write(job)
                family_counts[row['payload'].get('source_family') or 'unknown'] += 1
                scope_counts[row['payload'].get('case_scope') or 'unknown'] += 1
                role_counts.update(result['source_roles']['evidence_roles'])
                stage_counts.update({key: int(value) for key, value in result['analysis_stages'].items()})
                queue_counts[job['next_stage']] += 1
                named += result['person_identity']['named_person_present']
                rounds += bool(row['round_id'])
                active += result['active']
                unreviewed += result['semantic_review'] != 'accepted'
                missing_refs += len(result['missing_document_ids'])
        requirements = personalization_requirements(args.notice_root, root, catalog, catalog_sha)
        summary = {'contract_version': 'experience-personalization-production-v2',
            'at_kst': datetime.now(KST).isoformat(), 'database_snapshot_at': at,
            'database_access': 'repeatable_read_read_only', 'network_requests': 0, 'model_calls': 0,
            'database_writes': 0, 'is_demo_performance_report': False,
            'implementation': {'path': str(Path(__file__).resolve()),
                'sha256': digest(Path(__file__).read_bytes()), 'config_path': str(CONFIG),
                'config_sha256': digest(CONFIG.read_bytes())},
            'condition_catalog': {'path': str(catalog_path), 'sha256': catalog_sha, 'fields': len(catalog['fields'])},
            'case_inventory': {'all_source_rows': len(all_cases), 'excluded_by_reason': dict(excluded),
                'production_scope_rows': len(cases), 'active_production_scope_rows': active,
                'named_person_rows': named, 'known_round_rows': rounds,
                'meaning_review_not_approved_rows': unreviewed, 'missing_document_references': missing_refs,
                'source_families': dict(family_counts), 'case_scopes': dict(scope_counts),
                'evidence_role_counts_overlap': dict(role_counts)},
            'document_inventory': {'linked_existing_documents': len(docs), 'document_kinds': dict(document_kinds),
                'integrity_states': dict(integrity_states), 'segment_kinds': dict(segment_kinds),
                'structural_segment_rows': sum(segment_kinds.values())},
            'case_stage_counts': dict(stage_counts), 'next_processing_queue_counts': dict(queue_counts),
            'stage_count_denominator': 'production_scope_rows; inactive or merged rows remain flagged',
            'semantic_extraction_of_all_cases_performed_this_run': False,
            'analysis_execution_evidence': analysis_artifacts(),
            'canonical_table_counts': table_counts, 'notice_record_types': notice_types,
            'personalization_requirements': requirements,
            'outputs': {key: str(root/value) for key, value in config['output_contract'].items()},
            'limitations': ['Source role metadata and structural spans are not semantic event labels.',
                'A shared program string is not a reviewed opportunity-and-round link.',
                'Candidate condition mentions do not authorize eligibility decisions or user questions.']}
        atomic_json(root/'summary.json', summary)
        atomic_json(root/'progress.json', {'at_kst': summary['at_kst'], 'state': 'complete',
            'documents_done': len(docs), 'case_rows': len(cases), 'semantic_analysis_performed': False})
        print(encoded({key: summary[key] for key in ('at_kst', 'case_inventory', 'document_inventory',
            'case_stage_counts', 'canonical_table_counts')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=CACHE/'team-production-20261119/experience')
    parser.add_argument('--notice-root', type=Path, default=CACHE/'team-production-20261119/notice')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error('workers must be between 1 and 8')
    run(args)
