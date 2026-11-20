"""Project every non-demo intake notice into an evidence-bound candidate contract.

Reads the database in a repeatable-read read-only transaction and existing cache
bytes. Does not collect sources, change services, publish claims or write SQL.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import project
from benefitlens_source_storage import read_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json, checked_root, single_writer

CATALOG = project.ROOT/'pipeline/config/shared_condition_catalog_v1.json'
HISTORY_VALIDATION = project.ROOT/'proposal/결과물/범위추가확장_2026-11-09/validation.json'


def digest(value):
    return sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def load_catalog(path=CATALOG):
    data = json.loads(Path(path).read_text())
    if data.get('contract_version') != 'shared-condition-catalog-v1':
        raise ValueError('Unsupported shared condition catalog')
    return data


def compile_catalog(catalog):
    return [(field, rule, pattern, re.compile(pattern['regex'], re.I))
            for field, rule in catalog['fields'].items() for pattern in rule['patterns']]


def quote_span(text, start, end, ceiling=800):
    left, right = text.rfind('\n', 0, start)+1, text.find('\n', end)
    if right < 0:
        right = len(text)
    if right-left > ceiling:
        left, right = max(left, start-180), min(right, max(end, start+ceiling-180))
    return left, right, text[left:right]


def parsed_value(pattern, match, quote):
    parser, groups = pattern.get('parser'), match.groupdict()
    value, unit, qualifiers = pattern.get('value'), None, {}
    if parser in ('number', 'amount'):
        raw = groups.get('value') or groups.get('value_alt')
        value = float(raw.replace(',', '')) if raw else None
        if value is not None and value.is_integer():
            value = int(value)
        if parser == 'amount':
            krw = groups.get('korean_unit')
            if krw:
                value *= {'만원': 10000, '천원': 1000, '원': 1}[krw]
                unit = 'KRW'
            else:
                unit = {'€': 'EUR', '£': 'GBP'}.get(groups.get('currency'))
                qualifiers['raw_currency_symbol'] = groups.get('currency')
    elif parser == 'date_in_quote':
        date = re.search(r'\b(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\b', quote)
        if date:
            try:
                value = datetime(*map(int, date.groups())).date().isoformat()
            except ValueError:
                pass
    operator = 'unknown'
    observed = match[0]
    if value is not None and parser == 'number':
        if re.search(r'>=|≥|at\s+least|이상|\d\+\s*years?', observed, re.I):
            operator = 'gte'
        elif re.search(r'<=|≤|이하', observed):
            operator = 'lte'
        elif '미만' in observed:
            operator = 'lt'
        elif '초과' in observed:
            operator = 'gt'
    if re.search(r'per\s+year|per\s+annum|annual(?:ly)?', quote, re.I):
        qualifiers['frequency_candidate'] = 'per_year'
    elif re.search(r'per\s+month|monthly|월\s*급|매월', quote, re.I):
        qualifiers['frequency_candidate'] = 'per_month'
    elif re.search(r'학기(?:당|별)|per\s+semester', quote, re.I):
        qualifiers['frequency_candidate'] = 'per_semester'
    return value, operator, unit, qualifiers


def extract_conditions(text, *, candidate_key, document, source_url, catalog, compiled=None):
    conditions, covered_lines, seen = [], set(), set()
    logic = re.compile(catalog['logic_marker_regex'], re.I)
    def emit(field, rule, start, end, matched_text, value=None, operator='unknown', unit=None, qualifiers=None):
        left, right, quote = quote_span(text, start, end)
        identity = (field, start, end, str(value))
        if identity in seen:
            return
        seen.add(identity)
        reasons = ['meaning_not_reviewed']
        if operator == 'unknown': reasons.append('operator_not_identified')
        if value is None: reasons.append('value_not_normalized')
        phase = 'unknown'
        if re.search(r'by\s+(?:the\s+)?(?:start|time).{0,30}(?:award|fellowship)|수혜\s*시작|지원\s*시작\s*시점', quote, re.I):
            phase = 'benefit_start'
        elif re.search(r'throughout|during.{0,30}(?:studentship|award|fellowship)|활동\s*기간\s*(?:중|내)|수혜\s*기간\s*중', quote, re.I):
            phase = 'throughout_activity'
        elif re.search(r'at\s+(?:the\s+)?(?:time\s+of\s+)?application|신청일|지원일|접수일', quote, re.I):
            phase = 'application'
        if phase == 'unknown': reasons.append('reference_time_not_stated')
        markers = [m[0] for m in logic.finditer(quote)]
        if markers: reasons.append('logical_scope_not_reviewed')
        if field == 'program_specific.requirement': reasons.append('field_not_in_normalized_catalog')
        if rule.get('unit') and unit is None: unit = rule['unit']
        if rule['value_type'] == 'number' and unit in (None, 'currency'):
            reasons.append('unit_not_identified')
        evidence = {'document_id': document['document_id'], 'text_sha256': document['text_sha256'],
                    'char_start': left, 'char_end': right, 'exact_quote': quote, 'source_url': source_url}
        conditions.append({'condition_id': 'condition-candidate-'+digest(json.dumps([candidate_key, field, start, end, value]))[:28],
            'candidate_key': candidate_key, 'field_id': field, 'operator': operator, 'value': value,
            'value_type': rule['value_type'], 'unit': unit, 'at': {'phase': phase, 'date': None},
            'status': 'candidate', 'unknown_reasons': reasons, 'evidence': [evidence],
            'matched_text': matched_text, 'qualifiers': qualifiers or {},
            'constraint_modality': 'preferred_candidate' if re.search(r'prefer|우대', quote, re.I)
                                  else 'required_candidate' if re.search(r'\bmust\b|\brequired\b|필수', quote, re.I) else 'unclear',
            'logical_context': {'markers': markers, 'tree': None, 'status': 'unreviewed'},
            'condition_role': rule['condition_role'], 'applicant_input_stage': rule['applicant_input_stage']})
    for field, rule, pattern, regex in (compiled or compile_catalog(catalog)):
        for match in regex.finditer(text):
            left, right, quote = quote_span(text, *match.span())
            value, op, unit, qualifiers = parsed_value(pattern, match, quote)
            emit(field, rule, *match.span(), match[0], value, op, unit, qualifiers)
            covered_lines.add((left, right))
    offset = 0
    marker = re.compile(catalog['constraint_marker_regex'], re.I)
    for line in text.splitlines(keepends=True):
        content = line.rstrip('\r\n')
        if len(content.strip()) >= 15 and marker.search(content) and (offset, offset+len(content)) not in covered_lines:
            emit('program_specific.requirement', catalog['fields']['program_specific.requirement'],
                 offset, offset+len(content), content, content)
        offset += len(line)
    return conditions


@lru_cache(maxsize=1024)
def verified_bytes(path, checksum, storage_json):
    raw = read_source_bytes(path, storage=json.loads(storage_json) if storage_json else None)
    if digest(raw) != checksum:
        raise ValueError('Cached original checksum mismatch')
    return raw


def source_document(doc):
    metadata = doc.get('metadata') or {}
    raw = verified_bytes(doc['raw_path'], doc['content_sha256'], json.dumps(metadata.get('raw_storage'), sort_keys=True))
    text = verified_bytes(doc['text_path'], doc['text_sha256'], json.dumps(metadata.get('text_storage'), sort_keys=True)).decode('utf8')
    if '\x00' in text or not text.strip():
        raise ValueError('Original projection has no readable text')
    return raw, text


def canonical_url(url):
    from run_benefitlens_campaign import app_key
    return app_key(url)


def historical_index():
    """Read preserved lineage and approval IDs; do not rerun its coordinator."""
    by_hash, by_url = defaultdict(list), defaultdict(list)
    metadata = {'status': 'not_loaded'}
    if not HISTORY_VALIDATION.exists():
        return by_hash, by_url, metadata
    validation = json.loads(HISTORY_VALIDATION.read_text())
    def pinned(ref):
        raw = Path(ref['path']).read_bytes()
        if digest(raw) != ref['sha256']:
            raise ValueError('Historical reference checksum mismatch')
        return json.loads(raw)
    manifest = pinned(validation['manifest'])
    receipt = pinned(validation['full']['receipt'])
    review_ref = receipt['artifacts']['review']
    review = pinned(review_ref)
    accepted_ids = set(review['accepted']['ids'])
    bundles = [pinned(manifest['baseline'])] + [pinned(ref) for ref in manifest['deltas'] if ref['role'] == 'current_opportunities']
    docs, evidences, claims, rounds = {}, {}, {}, {}
    for bundle in bundles:
        docs.update({doc['document_id']: doc for doc in bundle['source_documents']})
        evidences.update({e['evidence_id']: e for e in bundle['evidence']})
        claims.update({claim['claim_id']: claim for claim in bundle['claims']})
        rounds.update({(row['program_id'], row['round_id']): row for row in bundle['opportunity_rounds']})
    semantic_refs = ([manifest['semantic_patch']] if manifest.get('semantic_patch') else []) + manifest.get('semantic_patches', [])
    overlay_refs = []
    for ref in semantic_refs:
        overlay = pinned(ref)
        claims.update({claim['claim_id']: claim for claim in overlay.get('claims',[])})
        evidences.update({e['evidence_id']: e for e in overlay.get('evidence',[])})
        overlay_refs.append({'path':ref['path'],'sha256':ref['sha256'],'claim_rows':len(overlay.get('claims',[]))})
    fields, claim_docs = Counter(), defaultdict(set)
    for claim in claims.values():
        if claim['claim_id'] in accepted_ids:
            fields[claim['field']] += 1
            for eid in claim.get('evidence_ids', []):
                if eid in evidences:
                    claim_docs[evidences[eid]['document_id']].add(claim['field'])
    enrichment_fields = Counter()
    for ref in manifest.get('enrichments',[]):
        pinned(ref['review_receipt'])
        for mapping in pinned(ref):
            enrichment_fields[mapping['field']] += 1
            claim_docs[mapping['document_id']].add(mapping['field'])
    for doc in docs.values():
        linked = {'document_id': doc['document_id'], 'url': doc['url'],
                  'raw_sha256': doc['raw_sha256'], 'fetched_at': doc['fetched_at'],
                  'historically_accepted_fields': sorted(claim_docs.get(doc['document_id'], [])),
                  'historical_validation_as_of': manifest['as_of'],
                  'current_meaning_approval_inherited': False}
        by_hash[doc['raw_sha256']].append(linked)
        by_url[canonical_url(doc['url'])].append(linked)
    metadata = {'status': 'historical_inputs_and_recorded_receipt_indexed', 'as_of': manifest['as_of'],
        'validation_path': str(HISTORY_VALIDATION), 'manifest': validation['manifest'],
        'recorded_reported_current_variants': 4492,
        'recorded_complete_bundle_projected_rows': validation['full']['projected_rows'],
        'indexed_notice_input_rounds': len(rounds), 'indexed_source_documents': len(docs),
        'recorded_accepted_claim_fields_in_input_index': dict(fields),
        'recorded_complete_bundle_common_fields': receipt.get('common_fields',{}),
        'pinned_semantic_overlay_refs':overlay_refs,'pinned_enrichment_field_counts':dict(enrichment_fields),
        'scope': 'baseline/current-opportunity deltas and pinned semantic mappings; historical-writing deltas excluded; coordinator and approval logic not rerun',
        'not_current_open_verification': True}
    return by_hash, by_url, metadata


def origin_class(url):
    host = (urlsplit(url).hostname or '').lower()
    return ('korean_government_domain' if host.endswith('.go.kr') else
            'academic_domain' if host.endswith(('.ac.kr', '.edu', '.ac.jp')) else
            'employer_ats_domain' if 'myworkdayjobs.com' in host or host.endswith(('greenhouse.io','ashbyhq.com')) else
            'academicworks_platform' if host.endswith('.academicworks.com') else 'other_public_domain')


def existing_structure_details():
    """Distinguish an existing clauses object from actual nonempty arrays."""
    with project.connect() as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        result={}
        for group in ('eligibility','benefits','application'):
            row=connection.execute("""SELECT count(*) FILTER (WHERE jsonb_array_length(
                coalesce(payload->'extracted_clauses'->%s,'[]'::jsonb))>0),
                coalesce(sum(jsonb_array_length(coalesce(payload->'extracted_clauses'->%s,'[]'::jsonb))),0)
                FROM benefitlens.support_discovery_record WHERE record_type!='sample'""",(group,group)).fetchone()
            result[group]={'records_with_nonempty_array':row[0],'existing_clause_rows':int(row[1])}
        return result


@contextmanager
def jsonl_sink(path):
    temporary = path.with_suffix(path.suffix+'.tmp')
    with temporary.open('wb') as file:
        with gzip.GzipFile(filename='', mode='wb', fileobj=file, mtime=0) as compressed:
            def write(row):
                compressed.write((json.dumps(row, ensure_ascii=False, separators=(',', ':'))+'\n').encode())
            yield write
    os.replace(temporary, path)


def run(root, catalog_path=CATALOG):
    root = checked_root(root)
    catalog = load_catalog(catalog_path)
    compiled = compile_catalog(catalog)
    old_hash, old_url, history = historical_index()
    counts, existing, projection, authority = Counter(), Counter(), Counter(), Counter()
    field_stats = {field: Counter() for field in catalog['fields']}
    surfaces = {field: Counter() for field in catalog['fields']}
    operators, units, phases, unknowns, capture_states = Counter(), Counter(), Counter(), Counter(), Counter()
    source_hosts, historical_links = Counter(), Counter()
    errors, evidence_errors = [], []
    with single_writer(root), project.connect() as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        table_names = {row[0] for row in connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='benefitlens'")}
        canonical_counts = {name: connection.execute('SELECT count(*) FROM benefitlens.'+name).fetchone()[0]
                            for name in ('opportunity','opportunity_version','eligibility_rule') if name in table_names}
        expected = connection.execute("SELECT count(*) FROM benefitlens.support_discovery_record WHERE record_type!='sample'").fetchone()[0]
        excluded_demos = connection.execute("SELECT count(*) FROM benefitlens.support_discovery_record WHERE record_type='sample'").fetchone()[0]
        atomic_json(root/'progress.json', {'state': 'running', 'expected_candidates': expected, 'processed': 0})
        with connection.cursor(name='notice_production_audit') as cursor:
            cursor.itersize = 300
            cursor.execute("""SELECT r.candidate_key,r.source_url,r.title,r.title_basis,r.issuer_name,r.issuer_kind,
                r.source_country,r.funding_hints,r.capture_status,r.eligibility_status,r.rights_status,
                r.payload,r.detail_text,d.document_id
                FROM benefitlens.support_discovery_record r LEFT JOIN benefitlens.pipeline_document d
                ON d.document_id=r.payload->>'document_id' WHERE r.record_type!='sample' ORDER BY r.candidate_key""")
            with jsonl_sink(root/'notices.jsonl.gz') as write_notice, jsonl_sink(root/'conditions.jsonl.gz') as write_condition, jsonl_sink(root/'next-processing-queue.jsonl.gz') as write_queue:
                for values in cursor:
                    (key, url, title, title_basis, issuer, issuer_kind, country, hints,
                     capture, eligibility, rights, payload, detail, linked_document_id) = values
                    counts['candidates'] += 1
                    capture_states[capture] += 1
                    for name, value in [('title',title),('issuer_name',issuer),('issuer_kind',issuer_kind),
                                        ('source_country',country),('funding_hints',hints),('detail_text',detail),
                                        ('payload_countries', [x for x in payload.get('countries',[]) if x]),
                                        ('source_document',payload.get('source_document')),
                                        ('pipeline_document_link',linked_document_id),('existing_extracted_clauses',payload.get('extracted_clauses'))]:
                        if value: existing[name] += 1
                    if payload.get('semantic_review') not in (None, 'unreviewed', 'candidate'):
                        counts['intake_nondefault_semantic_review'] += 1
                    if payload.get('current_open_verified') is True: counts['intake_current_open_verified'] += 1
                    document = payload.get('source_document')
                    result = {'candidate_key':key,'record_type':'production_candidate','scope':'group_representative_original',
                        'title':title,'issuer_name':issuer,'source_url':url,'source_country':country,
                        'source_countries':payload.get('countries',[]),'variant_count':payload.get('variant_count'),
                        'round_variants':payload.get('round_variants'),
                        'intake_capture_status':capture,'intake_semantic_review':payload.get('semantic_review'),
                        'intake_current_open_verified':payload.get('current_open_verified',False),
                        'current_open_verified':False,'semantic_review_status':'not_performed',
                        'conditions':[],'unknown_reasons':[],
                        'original_ref':{k:document.get(k) for k in ('document_id','canonical_url','content_sha256','text_sha256','raw_path','text_path','fetched_at')} if document else None,
                        'source_origin':{'domain_class':origin_class(url),'official_source_meaning_review':'not_established_by_domain_pattern'}}
                    source_hosts[urlsplit(url).hostname or 'unknown'] += 1
                    authority[result['source_origin']['domain_class']] += 1
                    old = old_hash.get(document.get('content_sha256',''),[]) if document else []
                    match_kind = 'same_raw_hash' if old else 'same_url_only' if old_url.get(canonical_url(url)) else 'no_match'
                    if not old: old = old_url.get(canonical_url(url),[])
                    result['historical_handoff_links'] = {'match_kind':match_kind,'references':old,
                         'scope':'historical_validation_only; no current approval or open-status inheritance'}
                    historical_links[match_kind] += 1
                    if payload.get('variant_count', 1) > 1:
                        result['unknown_reasons'].append('not_all_round_or_location_variants_reviewed')
                    text = None
                    if not document:
                        result['projection_state']='original_unavailable'
                        result['unknown_reasons'].append('original_unavailable')
                    else:
                        try:
                            _, text = source_document(document)
                            counts['raw_and_text_hashes_verified'] += 1
                            result['conditions'] = extract_conditions(text, candidate_key=key, document=document,
                                source_url=document.get('canonical_url') or url,catalog=catalog,compiled=compiled)
                            result['projection_state']='condition_candidates_projected' if result['conditions'] else 'original_readable_no_condition_detected'
                            if not result['conditions']: result['unknown_reasons'].append('no_condition_detected_not_proof_of_no_requirement')
                        except (OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
                            result['projection_state']='original_integrity_or_projection_failed'
                            result['unknown_reasons'].append(type(error).__name__+': '+str(error)[:180])
                            if len(errors)<30:errors.append({'candidate_key':key,'error':str(error)[:160]})
                    present=set()
                    for condition in result['conditions']:
                        write_condition(condition)
                        field=condition['field_id'];present.add(field)
                        stat=field_stats[field];stat['condition_candidates']+=1
                        if condition['value'] is not None:
                            stat['value_present_candidates']+=1
                            if condition['value_type']!='unresolved_condition':stat['value_normalized_candidates']+=1
                        if condition['operator']=='unknown':stat['operator_unknown']+=1
                        if condition['at']['phase']=='unknown':stat['reference_time_unknown']+=1
                        surfaces[field][' '.join(condition['matched_text'].split()).casefold()]+=1
                        operators[condition['operator']]+=1;units[str(condition['unit'])]+=1;phases[condition['at']['phase']]+=1
                        unknowns.update(condition['unknown_reasons'])
                        for e in condition['evidence']:
                            counts['evidence_spans_checked']+=1
                            if text[e['char_start']:e['char_end']]!=e['exact_quote']:
                                evidence_errors.append(condition['condition_id'])
                    for field in present:field_stats[field]['notices_with_candidates']+=1
                    result['unresolved_fields']=[{'field_id':field,'unknown_reason':'not_detected_not_evidence_of_absence' if text is not None else 'original_not_readable'} for field in catalog['fields'] if field not in present]
                    projection[result['projection_state']]+=1
                    counts['condition_candidates']+=len(result['conditions'])
                    write_notice(result)
                    write_queue({'candidate_key':key,'next_step':'recover_original' if text is None else 'meaning_review',
                                 'projection_state':result['projection_state'],'condition_ids':[x['condition_id'] for x in result['conditions']],
                                 'unknown_reasons':result['unknown_reasons'],'automated_user_question_authorized':False})
                    if counts['candidates']%1000==0:
                        atomic_json(root/'progress.json',{'state':'running','processed':counts['candidates'],'expected_candidates':expected,'condition_candidates':counts['condition_candidates']})
                        print(json.dumps({'processed':counts['candidates'],'conditions':counts['condition_candidates']}),flush=True)
    for field, stats in field_stats.items():
        for name in ('condition_candidates','notices_with_candidates','value_present_candidates',
                     'value_normalized_candidates','operator_unknown','reference_time_unknown'):
            stats.setdefault(name,0)
        stats['not_detected_or_unreadable']=counts['candidates']-stats['notices_with_candidates']
        stats['surface_expression_variants']=len(surfaces[field])
        stats['top_surface_expressions']=[{'text':t[:180],'count':n} for t,n in surfaces[field].most_common(8)]
    summary={'at_kst':datetime.now(KST).isoformat(),'state':'complete','scope':'all_non_sample_discovery_records',
        'catalog':str(catalog_path),'catalog_sha256':digest(Path(catalog_path).read_bytes()),
        'counts':dict(counts),'excluded_sample_records':excluded_demos,'capture_states':dict(capture_states),
        'existing_structured_field_presence':{field:existing[field] for field in ['title','issuer_name','issuer_kind','source_country','payload_countries','funding_hints','detail_text','source_document','pipeline_document_link','existing_extracted_clauses']},
        'existing_clause_structure':existing_structure_details(),'projection_states':dict(projection),
        'canonical_operating_table_rows':canonical_counts,'historical_handoff':history,
        'historical_matches':dict(historical_links),'source_domain_classes_unreviewed':dict(authority),
        'source_top_hosts':dict(source_hosts.most_common(15)), 'field_coverage':field_stats,
        'operators':dict(operators),'units':dict(units),'reference_times':dict(phases),'unknown_reasons':dict(unknowns),
        'http_requests':0,'database_writes':0,'meaning_reviews_performed':0,
        'limitations':['Regex candidates are not eligibility decisions.','Group representatives do not cover all geographic/round variants.',
                      'Official authority is not established by a hostname match.','Historical validation is not current-open verification.',
                      'Existing structured intake and preserved historical handoffs are distinct from operating canonical tables.']}
    summary['existing_structured_field_missing']={field:counts['candidates']-present
        for field,present in summary['existing_structured_field_presence'].items()}
    checks={'all_non_sample_rows_processed':counts['candidates']==expected,
            'sample_rows_excluded':excluded_demos,'evidence_spans_checked':counts['evidence_spans_checked'],
            'invalid_evidence_spans':len(evidence_errors),'error_samples':errors,'semantic_approval_claimed':False,
            'source_collection_performed':False,'database_mode':'repeatable_read_read_only'}
    atomic_json(root/'summary.json',summary);atomic_json(root/'audit-checks.json',checks)
    alias=root/'records.jsonl.gz'
    alias.unlink(missing_ok=True);os.link(root/'notices.jsonl.gz',alias)
    artifacts={}
    for name in ('notices.jsonl.gz','conditions.jsonl.gz','next-processing-queue.jsonl.gz','summary.json','audit-checks.json'):
        path=root/name
        checksum=sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024**2),b''):checksum.update(chunk)
        artifacts[name]={'path':str(path),'sha256':checksum.hexdigest(),'stored_bytes':path.stat().st_size}
    atomic_json(root/'manifest.json',{'contract_version':'notice-production-audit-v1',
        'producer_code_sha256':digest(Path(__file__).read_bytes()),
        'catalog_sha256':digest(Path(catalog_path).read_bytes()),'artifacts':artifacts,
        'database_mode':'repeatable_read_read_only','sample_records_excluded':excluded_demos,
        'meaning_reviews_performed':0,'source_collection_performed':False})
    atomic_json(root/'progress.json',{'state':'complete','processed':counts['candidates'],'expected_candidates':expected})
    print(json.dumps({'summary':str(root/'summary.json'),'counts':dict(counts),'checks':checks},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root','--output-root',type=Path,default=CACHE/'team-production-20261119/notice')
    parser.add_argument('--catalog',type=Path,default=CATALOG)
    args=parser.parse_args();run(args.root,args.catalog)
