"""Diagnose explicit writing annotations without interpreting or rating prose.

No model calls, service writes or quality acceptance. Input declarations and
source text are supplied by the caller; this module never follows source paths.
"""
from collections import Counter, defaultdict, deque
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
import argparse
import json
import re


CONFIG = Path(__file__).resolve().parents[1] / 'pipeline/config/writing_nonllm_checks_v1.json'


def canonical_sha256(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class _Check:
    def __init__(self, check_id):
        self.check_id = check_id
        self.denominator = 0
        self.numerator = 0
        self.unobserved = 0
        self.excluded = 0
        self.missing_scopes = set()
        self.missing_reasons = Counter()
        self.findings = []

    def observe(self, unit, *issues):
        self.denominator += 1
        self.numerator += bool(issues)
        for issue in issues:
            self.findings.append({'unit': unit, 'code': issue})

    def missing(self, unit, reason):
        self.unobserved += 1
        self.missing_reasons[reason] += 1

    def unavailable(self, scope):
        self.missing_scopes.add(scope)

    def result(self):
        status = ('observed' if self.denominator else 'not_observable'
                  if self.missing_scopes or self.unobserved else 'not_applicable')
        return {'check_id': self.check_id, 'status': status,
                'numerator': self.numerator, 'denominator': self.denominator,
                'violation_fraction': self.numerator / self.denominator if self.denominator else None,
                'unobserved_count': None if self.missing_scopes else self.unobserved,
                'known_unobserved_units': self.unobserved,
                'not_applicable_count': self.excluded,
                'missing_annotation_scopes': sorted(self.missing_scopes),
                'missing_reasons': dict(sorted(self.missing_reasons.items())),
                'findings': self.findings}


def _records(payload, name, id_key):
    rows = payload.get(name)
    if rows is None:
        return None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(name + ' must be a list of annotation objects or null')
    ids = [row.get(id_key) for row in rows]
    if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError(name + ' requires unique nonempty ' + id_key)
    return rows


def _ids(value, name):
    if value is None:
        return None
    if (not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value)
            or len(value) != len(set(value))):
        raise ValueError(name + ' requires unique nonempty string IDs')
    return value


def _temporal(value):
    """Retain precision and zone; never infer a date from the current clock."""
    if isinstance(value, dict):
        if value.get('status') != 'known':
            return None, 'time_not_known'
        value = value.get('value')
    if value is None:
        return None, 'time_not_supplied'
    if not isinstance(value, str):
        return None, 'invalid_iso_date'
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return date.fromisoformat(value), None
        if 'T' not in value:
            return None, 'invalid_iso_date'
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.utcoffset() is None:
            return None, 'timezone_not_supplied'
        return result, None
    except ValueError:
        return None, 'invalid_iso_date'


def _comparable(a, b):
    return type(a) is type(b)


def quadratic_weighted_kappa(ratings_a, ratings_b, categories=(0, 1, 2), minimum_pairs=2):
    """Arithmetic only; these numbers are not authenticated human evaluations.

    O is the count matrix, E_ij = row_i * col_j / N, and
    w_ij = ((i-j)/(K-1))**2. Kappa = 1 - sum(w*O)/sum(w*E).
    The project's conservative policy also withholds kappa if either rater is
    constant, although some such cases have an arithmetic kappa of zero.
    """
    categories = list(categories)
    if len(categories) < 2 or len(set(categories)) != len(categories):
        raise ValueError('At least two ordered, distinct categories are required')
    if any(isinstance(x, bool) for x in categories):
        raise ValueError('Boolean categories are not supported')
    if isinstance(minimum_pairs, bool) or not isinstance(minimum_pairs, int) or minimum_pairs < 2:
        raise ValueError('minimum_pairs must be an integer of at least two')
    if len(ratings_a) != len(ratings_b):
        raise ValueError('Rater arrays must have the same length')
    index = {value: i for i, value in enumerate(categories)}
    pairs, missing = [], 0
    for a, b in zip(ratings_a, ratings_b):
        for value in (a, b):
            if value is not None and (isinstance(value, bool) or value not in index):
                raise ValueError('Rating outside declared categories')
        if a is None or b is None:
            missing += 1
        else:
            pairs.append((index[a], index[b]))
    size, count = len(categories), len(pairs)
    matrix = [[0 for _ in categories] for _ in categories]
    for a, b in pairs:
        matrix[a][b] += 1
    rows = [sum(row) for row in matrix]
    cols = [sum(matrix[i][j] for i in range(size)) for j in range(size)]
    result = {'status': 'not_measured', 'reason': None, 'categories': categories,
              'paired_units': count, 'excluded_missing_pairs': missing,
              'minimum_pairs_for_arithmetic': minimum_pairs,
              'minimum_pairs_is_not_statistical_guarantee': True,
              'quadratic_weighted_kappa': None,
              'exact_agreement': sum(matrix[i][i] for i in range(size)) / count if count else None,
              'confusion_matrix': matrix, 'rater_a_counts': rows, 'rater_b_counts': cols,
              'calculation_scope': 'supplied_numbers_not_authenticated_human_data'}
    if count < minimum_pairs:
        result['reason'] = 'insufficient_paired_units'
    elif sum(value > 0 for value in rows) < 2 or sum(value > 0 for value in cols) < 2:
        result['reason'] = 'constant_rater_distribution'
    else:
        observed = sum((i - j) ** 2 * matrix[i][j] for i in range(size) for j in range(size))
        expected = sum((i - j) ** 2 * rows[i] * cols[j] / count
                       for i in range(size) for j in range(size))
        if expected == 0:
            result['reason'] = 'zero_expected_disagreement'
        else:
            result.update(status='calculated', reason=None,
                          quadratic_weighted_kappa=1 - observed / expected)
    return result


def aggregate_human_reviews(rows, categories=(0, 1, 2), minimum_pairs=2,
                            document_provenance='unknown'):
    """Summarize declared human records, never model or synthetic ratings."""
    if rows is not None and (not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows)):
        raise ValueError('human_reviews must be a list of records or null')
    result = {'status': 'not_measured', 'reason': 'no_human_ratings',
              'input_records': len(rows or []), 'eligible_records': 0,
              'excluded_records': [], 'criteria': {},
              'origin_authentication': 'declaration_and_reference_only_not_authenticated',
              'alpha': None, 'alpha_status': 'not_implemented'}
    if not rows:
        return result
    if document_provenance == 'synthetic_fixture':
        result['reason'] = 'synthetic_document_cannot_establish_human_agreement'
        result['excluded_records'] = [{'row': i, 'reason': 'synthetic_document'} for i in range(len(rows))]
        return result
    by_criterion = defaultdict(list)
    for i, row in enumerate(rows):
        source = row.get('source_ref') or {}
        reason = None
        if not isinstance(source, dict):
            reason = 'invalid_review_source_reference'
        elif row.get('reviewer_type') != 'human' or row.get('provenance') != 'actual_human_review':
            reason = 'not_declared_actual_human_review'
        elif not row.get('review_record_id') or not source.get('evidence_id'):
            reason = 'missing_review_evidence_reference'
        elif any(not isinstance(row.get(k), str) or not row[k] for k in ('criterion_id', 'unit_id', 'reviewer_id')):
            reason = 'missing_review_identity'
        elif row.get('rating_state') in ('not_observable', 'not_applicable') and row.get('score') is not None:
            reason = 'unobserved_rating_must_be_null'
        elif row.get('score') is not None and (isinstance(row['score'], bool) or row['score'] not in categories):
            reason = 'rating_outside_categories'
        if reason:
            result['excluded_records'].append({'row': i, 'reason': reason})
        else:
            by_criterion[row['criterion_id']].append(row)
            result['eligible_records'] += 1
    for criterion, records in sorted(by_criterion.items()):
        raters = sorted({r['reviewer_id'] for r in records})
        ids = [(r['unit_id'], r['reviewer_id']) for r in records]
        if len(ids) != len(set(ids)):
            result['criteria'][criterion] = {'status': 'not_measured', 'reason': 'duplicate_unit_rater_record',
                                             'quadratic_weighted_kappa': None}
        elif len(raters) != 2:
            result['criteria'][criterion] = {'status': 'not_measured', 'reason': 'requires_exactly_two_raters',
                                             'rater_count': len(raters), 'quadratic_weighted_kappa': None}
        else:
            scores = {(r['unit_id'], r['reviewer_id']): r.get('score') for r in records}
            units = sorted({r['unit_id'] for r in records})
            stats = quadratic_weighted_kappa([scores.get((u, raters[0])) for u in units],
                                            [scores.get((u, raters[1])) for u in units], categories, minimum_pairs)
            stats.update(rater_ids=raters, annotated_units=len(units))
            result['criteria'][criterion] = stats
    if by_criterion:
        result['status'] = 'provided_records_summarized'
        result['reason'] = None
    return result


def diagnose_writing(payload):
    """Return diagnostics and coverage denominators for caller-supplied annotations."""
    if not isinstance(payload, dict):
        raise ValueError('Input must be an object')
    contract = json.loads(CONFIG.read_text())
    checks = {key: _Check(key) for key in contract['checks']}
    names = {'sentences': 'sentence_id', 'paragraphs': 'paragraph_id', 'facts': 'fact_id',
             'sources': 'source_id', 'requirements': 'requirement_id', 'tasks': 'task_id',
             'claim_ground_links': 'link_id', 'paragraph_links': 'link_id'}
    records = {name: _records(payload, name, key) for name, key in names.items()}
    indices = {name: {r[names[name]]: r for r in rows or []} for name, rows in records.items()}
    sentences, facts, paragraphs = records['sentences'], records['facts'], records['paragraphs']
    sindex, findex, pindex = indices['sentences'], indices['facts'], indices['paragraphs']
    per_fact = ['sentence_fact_references', 'sentence_subject_alignment', 'sentence_experience_alignment',
                'sentence_fact_confirmation', 'sentence_plan_status', 'sentence_activity_status', 'sentence_state_time',
                'sentence_state_value', 'literal_fact_text']
    if sentences is None or facts is None:
        for key in per_fact:
            checks[key].unavailable('sentences' if sentences is None else 'facts')
    else:
        for sentence in sentences:
            sid = sentence['sentence_id']
            fids = _ids(sentence.get('fact_ids'), 'sentence.fact_ids')
            if fids is None:
                for key in per_fact:
                    checks[key].missing(sid, 'sentence_fact_ids_not_supplied')
                continue
            for fid in fids:
                unit = sid + '->' + fid
                fact = findex.get(fid)
                checks['sentence_fact_references'].observe(unit, *(['unknown_fact_id'] if fact is None else []))
                if fact is None:
                    for key in per_fact[1:]:
                        checks[key].missing(unit, 'fact_reference_unresolved')
                    continue
                for key, attribute in [('sentence_subject_alignment', 'subject_id'),
                                       ('sentence_experience_alignment', 'experience_id')]:
                    if sentence.get(attribute) is None or fact.get(attribute) is None:
                        checks[key].missing(unit, attribute + '_not_supplied')
                    else:
                        checks[key].observe(unit, *([attribute + '_mismatch'] if sentence[attribute] != fact[attribute] else []))
                if fact.get('status') is None:
                    checks['sentence_fact_confirmation'].missing(unit, 'fact_confirmation_not_supplied')
                else:
                    checks['sentence_fact_confirmation'].observe(unit, *(['unconfirmed_fact_used'] if fact['status'] != 'confirmed' else []))
                kinds = ('observation', 'performed_action', 'result', 'state', 'future_plan')
                if sentence.get('claim_kind') not in kinds or fact.get('kind') not in kinds:
                    checks['sentence_plan_status'].missing(unit, 'claim_or_fact_kind_not_supplied_or_unknown')
                else:
                    wrong = fact['kind'] == 'future_plan' and sentence['claim_kind'] in ('observation', 'performed_action', 'result', 'state')
                    checks['sentence_plan_status'].observe(unit, *(['future_plan_presented_as_actual'] if wrong else []))
                activity_states = ('completed', 'ongoing', 'planned')
                if sentence.get('activity_status') not in activity_states or fact.get('activity_status') not in activity_states:
                    checks['sentence_activity_status'].missing(unit, 'activity_status_not_supplied_or_unknown')
                else:
                    checks['sentence_activity_status'].observe(unit, *(['declared_activity_status_mismatch']
                        if sentence['activity_status'] != fact['activity_status'] else []))
                if fact.get('kind') == 'state':
                    a, ae = _temporal(fact.get('as_of'))
                    b, be = _temporal(sentence.get('as_of'))
                    if ae == 'invalid_iso_date' or be == 'invalid_iso_date':
                        checks['sentence_state_time'].observe(unit, 'invalid_iso_date')
                    elif ae or be or not _comparable(a, b):
                        checks['sentence_state_time'].missing(unit, ae or be or 'incomparable_time_precision')
                    else:
                        checks['sentence_state_time'].observe(unit, *(['state_as_of_mismatch'] if a != b else []))
                    if sentence.get('state_value') is None or fact.get('value') is None:
                        checks['sentence_state_value'].missing(unit, 'state_value_not_supplied')
                    else:
                        same = type(sentence['state_value']) is type(fact['value']) and sentence['state_value'] == fact['value']
                        checks['sentence_state_value'].observe(unit, *(['state_value_mismatch'] if not same else []))
                if sentence.get('literal_fact_binding') is True:
                    if len(fids) != 1 or not isinstance(fact.get('value'), str) or not isinstance(sentence.get('text'), str):
                        checks['literal_fact_text'].missing(unit, 'literal_text_comparison_not_applicable')
                    else:
                        value = fact['value'].strip()
                        expected = value + ('' if value.endswith(('.', '?', '!', '다')) else '.')
                        checks['literal_fact_text'].observe(unit, *(['declared_literal_text_changed'] if sentence['text'] != expected else []))

    sources = records['sources']
    if sources is None:
        checks['source_text_hashes'].unavailable('sources')
    for source in sources or []:
        if not isinstance(source.get('text'), str) or source.get('sha256') is None:
            checks['source_text_hashes'].missing(source['source_id'], 'source_text_or_hash_not_supplied')
        else:
            bad = sha256(source['text'].encode()).hexdigest() != source['sha256']
            checks['source_text_hashes'].observe(source['source_id'], *(['source_text_hash_mismatch'] if bad else []))
    if facts is None:
        checks['fact_source_spans'].unavailable('facts')
        checks['fact_subject_scope'].unavailable('facts')
        checks['fact_experience_scope'].unavailable('facts')
    allowed_experiences = _ids(payload.get('allowed_experience_ids'), 'allowed_experience_ids')
    for fact in facts or []:
        ref, fid = fact.get('source_ref') or {}, fact['fact_id']
        if not isinstance(ref, dict):
            raise ValueError('fact.source_ref must be an object or null')
        if payload.get('subject_id') is None or fact.get('subject_id') is None:
            checks['fact_subject_scope'].missing(fid, 'subject_scope_not_supplied')
        else:
            checks['fact_subject_scope'].observe(fid, *(['fact_outside_target_subject']
                if fact['subject_id'] != payload['subject_id'] else []))
        if allowed_experiences is None or fact.get('experience_id') is None:
            checks['fact_experience_scope'].missing(fid, 'experience_scope_not_supplied')
        else:
            checks['fact_experience_scope'].observe(fid, *(['fact_outside_declared_experiences']
                if fact['experience_id'] not in allowed_experiences else []))
        if not ref.get('source_id') or sources is None:
            checks['fact_source_spans'].missing(fid, 'source_text_binding_not_supplied')
            continue
        source = indices['sources'].get(ref['source_id'])
        if source is None:
            checks['fact_source_spans'].observe(fid, 'unknown_source_id')
            continue
        start, end, quote, text = ref.get('char_start'), ref.get('char_end'), ref.get('quote'), source.get('text')
        if any(v is None for v in (start, end, quote, text)):
            checks['fact_source_spans'].missing(fid, 'source_span_annotation_incomplete')
        elif (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int)
              or not isinstance(end, int) or not isinstance(text, str) or not isinstance(quote, str)
              or not 0 <= start < end <= len(text)):
            checks['fact_source_spans'].observe(fid, 'invalid_source_span')
        else:
            issues = ['source_quote_mismatch'] if text[start:end] != quote else []
            if ref.get('text_sha256') is not None and ref['text_sha256'] != sha256(text.encode()).hexdigest():
                issues.append('bound_source_hash_mismatch')
            checks['fact_source_spans'].observe(fid, *issues)

    if paragraphs is None or sentences is None:
        checks['paragraph_sentence_references'].unavailable('paragraphs_or_sentences')
    for paragraph in paragraphs or []:
        ids = _ids(paragraph.get('sentence_ids'), 'paragraph.sentence_ids')
        if ids is None:
            checks['paragraph_sentence_references'].missing(paragraph['paragraph_id'], 'sentence_membership_not_supplied')
        elif sentences is not None:
            for sid in ids:
                sentence = sindex.get(sid)
                issues = ['unknown_sentence_id'] if sentence is None else (['sentence_paragraph_mismatch']
                          if sentence.get('paragraph_id') != paragraph['paragraph_id'] else [])
                checks['paragraph_sentence_references'].observe(paragraph['paragraph_id'] + '->' + sid, *issues)

    if paragraphs is None or sentences is None:
        checks['sentence_paragraph_references'].unavailable('paragraphs_or_sentences')
    else:
        for sentence in sentences:
            sid, pid = sentence['sentence_id'], sentence.get('paragraph_id')
            if pid is None:
                checks['sentence_paragraph_references'].missing(sid, 'sentence_paragraph_not_supplied')
            elif pid not in pindex:
                checks['sentence_paragraph_references'].observe(sid, 'unknown_paragraph_id')
            else:
                ids = _ids(pindex[pid].get('sentence_ids'), 'paragraph.sentence_ids')
                if ids is None:
                    checks['sentence_paragraph_references'].missing(sid, 'sentence_membership_not_supplied')
                else:
                    checks['sentence_paragraph_references'].observe(sid, *(['sentence_omitted_from_declared_paragraph'] if sid not in ids else []))

    links = records['paragraph_links']
    if links is None or paragraphs is None or sentences is None:
        checks['paragraph_link_references'].unavailable('paragraph_links_or_text_units')
        checks['paragraph_link_order'].unavailable('paragraph_links_or_text_units')
    else:
        positions = {row['paragraph_id']: i for i, row in enumerate(paragraphs)}
        for link in links:
            unit = link['link_id']
            keys = ('from_paragraph_id', 'to_paragraph_id', 'from_sentence_id', 'to_sentence_id')
            if any(link.get(k) is None for k in keys):
                checks['paragraph_link_references'].missing(unit, 'paragraph_link_endpoints_incomplete')
                checks['paragraph_link_order'].missing(unit, 'paragraph_link_endpoints_incomplete')
                continue
            a, b = link['from_paragraph_id'], link['to_paragraph_id']
            x, y = sindex.get(link['from_sentence_id']), sindex.get(link['to_sentence_id'])
            issues = []
            if a not in pindex or b not in pindex or x is None or y is None:
                issues.append('dangling_paragraph_link')
            elif x.get('paragraph_id') != a or y.get('paragraph_id') != b:
                issues.append('paragraph_link_membership_mismatch')
            checks['paragraph_link_references'].observe(unit, *issues)
            if a not in positions or b not in positions:
                checks['paragraph_link_order'].missing(unit, 'paragraph_reference_unresolved')
            else:
                checks['paragraph_link_order'].observe(unit, *(['declared_next_paragraph_order_violated']
                        if positions[b] != positions[a] + 1 else []))

    links = records['claim_ground_links']
    if links is None or sentences is None or facts is None:
        checks['claim_ground_references'].unavailable('claim_ground_links_or_references')
    else:
        for link in links:
            fids = _ids(link.get('fact_ids'), 'claim_ground_link.fact_ids')
            if link.get('claim_sentence_id') is None or fids is None:
                checks['claim_ground_references'].missing(link['link_id'], 'claim_ground_annotation_incomplete')
            else:
                issues = []
                if link['claim_sentence_id'] not in sindex:
                    issues.append('unknown_claim_sentence_id')
                if not fids or any(fid not in findex for fid in fids):
                    issues.append('claim_ground_fact_reference_missing')
                checks['claim_ground_references'].observe(link['link_id'], *issues)

    requirements = records['requirements']
    if requirements is None or sentences is None:
        checks['requirement_sentence_references'].unavailable('requirements_or_sentences')
        checks['required_annotation_binding'].unavailable('requirements_or_sentences')
    else:
        for requirement in requirements:
            rid = requirement['requirement_id']
            if requirement.get('applicable') is False:
                checks['required_annotation_binding'].excluded += 1
                continue
            ids = _ids(requirement.get('sentence_ids'), 'requirement.sentence_ids')
            if ids is None:
                checks['requirement_sentence_references'].missing(rid, 'requirement_binding_not_supplied')
                checks['required_annotation_binding'].missing(rid, 'requirement_binding_not_supplied')
                continue
            for sid in ids:
                checks['requirement_sentence_references'].observe(rid + '->' + sid, *(['unknown_requirement_sentence_id'] if sid not in sindex else []))
            if not isinstance(requirement.get('required'), bool):
                checks['required_annotation_binding'].missing(rid, 'requiredness_not_supplied')
            elif requirement['required']:
                checks['required_annotation_binding'].observe(rid, *(['required_annotation_has_no_existing_sentence']
                        if not ids or not any(sid in sindex for sid in ids) else []))
            else:
                checks['required_annotation_binding'].excluded += 1

    order = payload.get('outline_order')
    if order is None or paragraphs is None:
        checks['outline_order'].unavailable('outline_order_or_paragraphs')
    elif not isinstance(order, dict):
        raise ValueError('outline_order must be an object or null')
    else:
        expected = _ids(order.get('expected_paragraph_ids'), 'outline_order.expected_paragraph_ids')
        if expected is None or not order.get('basis'):
            checks['outline_order'].missing('document', 'order_or_basis_not_supplied')
        elif expected:
            actual = [p['paragraph_id'] for p in paragraphs]
            checks['outline_order'].observe('document', *(['declared_outline_order_violated'] if actual != expected else []))

    text = payload.get('text')
    if text is not None and not isinstance(text, str):
        raise ValueError('text must be a string or null')
    if text is None or paragraphs is None or sentences is None:
        checks['text_annotation_consistency'].unavailable('text_or_text_units')
    else:
        pieces = []
        complete = True
        for p in paragraphs:
            ids = _ids(p.get('sentence_ids'), 'paragraph.sentence_ids')
            if ids is None or any(sid not in sindex or not isinstance(sindex[sid].get('text'), str) for sid in ids):
                complete = False
                break
            pieces.append(' '.join(sindex[sid]['text'] for sid in ids))
        if not complete:
            checks['text_annotation_consistency'].missing('document', 'text_unit_reconstruction_incomplete')
        else:
            checks['text_annotation_consistency'].observe('document', *(['text_differs_from_annotated_units'] if text != '\n\n'.join(pieces) else []))

    constraints = payload.get('constraints')
    if constraints is None:
        checks['length_constraints'].unavailable('constraints')
        checks['page_constraints'].unavailable('constraints')
    elif not isinstance(constraints, dict):
        raise ValueError('constraints must be an object or null')
    else:
        for key in ('max_characters', 'max_words', 'max_paragraphs', 'max_pages'):
            maximum = constraints.get(key)
            if maximum is None:
                continue
            if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
                raise ValueError(key + ' must be a positive integer')
            if key == 'max_pages':
                checks['page_constraints'].missing(key, 'rendered_layout_not_provided_or_verified')
                continue
            counting = constraints.get('character_counting', 'unicode_codepoints_including_spaces_and_newlines') if key == 'max_characters' else constraints.get('word_counting', 'whitespace_delimited_tokens') if key == 'max_words' else None
            if ((key == 'max_characters' and counting != 'unicode_codepoints_including_spaces_and_newlines')
                    or (key == 'max_words' and counting != 'whitespace_delimited_tokens')):
                checks['length_constraints'].missing(key, 'unsupported_counting_rule')
                continue
            actual = len(text) if key == 'max_characters' and text is not None else len(text.split()) if key == 'max_words' and text is not None else len(paragraphs) if key == 'max_paragraphs' and paragraphs is not None else None
            if actual is None:
                checks['length_constraints'].missing(key, 'text_or_paragraphs_not_supplied')
            else:
                checks['length_constraints'].observe(key, *(['explicit_length_limit_exceeded'] if actual > maximum else []))

    tasks = records['tasks']
    if tasks is None:
        for key in ('task_dependency_references', 'task_dependency_cycle', 'task_dates', 'dependency_dates'):
            checks[key].unavailable('tasks')
    else:
        graph, complete = {}, True
        for task in tasks:
            tid = task['task_id']
            deps = _ids(task.get('depends_on'), 'task.depends_on')
            if deps is None:
                checks['task_dependency_references'].missing(tid, 'dependencies_not_supplied')
                checks['dependency_dates'].missing(tid, 'dependencies_not_supplied')
                complete = False
            else:
                graph[tid] = [dep for dep in deps if dep in indices['tasks']]
                for dep in deps:
                    checks['task_dependency_references'].observe(tid + '->' + dep, *(['unknown_dependency_task_id'] if dep not in indices['tasks'] else []))
                    if dep not in indices['tasks']:
                        complete = False
                        checks['dependency_dates'].missing(tid + '->' + dep, 'dependency_reference_unresolved')
                        continue
                    start, se = _temporal(task.get('start'))
                    end, ee = _temporal(indices['tasks'][dep].get('end'))
                    if se == 'invalid_iso_date' or ee == 'invalid_iso_date':
                        checks['dependency_dates'].observe(tid + '->' + dep, 'invalid_iso_date')
                    elif se or ee or not _comparable(start, end):
                        checks['dependency_dates'].missing(tid + '->' + dep, se or ee or 'incomparable_time_precision')
                    else:
                        checks['dependency_dates'].observe(tid + '->' + dep, *(['dependency_finishes_after_task_start'] if end > start else []))
            start, se = _temporal(task.get('start'))
            end, ee = _temporal(task.get('end'))
            if se == 'invalid_iso_date' or ee == 'invalid_iso_date':
                checks['task_dates'].observe(tid, 'invalid_iso_date')
            elif se or ee or not _comparable(start, end):
                checks['task_dates'].missing(tid, se or ee or 'incomparable_time_precision')
            else:
                checks['task_dates'].observe(tid, *(['task_end_precedes_start'] if end < start else []))
        if tasks and not complete:
            checks['task_dependency_cycle'].missing('graph', 'dependency_graph_incomplete')
        elif tasks:
            incoming = {tid: len(deps) for tid, deps in graph.items()}
            children = defaultdict(list)
            for tid, deps in graph.items():
                for dep in deps:
                    children[dep].append(tid)
            queue, visited = deque(tid for tid, count in incoming.items() if count == 0), 0
            while queue:
                tid = queue.popleft()
                visited += 1
                for child in children[tid]:
                    incoming[child] -= 1
                    if incoming[child] == 0:
                        queue.append(child)
            checks['task_dependency_cycle'].observe('graph', *(['dependency_cycle_detected'] if visited != len(tasks) else []))

    agreement = contract['human_agreement']
    return {'version': contract['version'], 'document_id': payload.get('document_id'),
            'input_canonical_sha256': canonical_sha256(payload),
            'config_sha256': sha256(CONFIG.read_bytes()).hexdigest(),
            'scope': contract['scope'], 'provenance': payload.get('provenance', 'unknown'),
            'annotation_provenance': payload.get('annotation_provenance'),
            'checks': [check.result() for check in checks.values()],
            'semantic_quality_scores': None, 'semantic_quality_status': 'not_measured',
            'automatic_quality_acceptance': False,
            'human_review_summary': aggregate_human_reviews(payload.get('human_reviews'),
                agreement['categories'], agreement['minimum_pairs_for_arithmetic'], payload.get('provenance', 'unknown')),
            'limitations': contract['limitations'],
            'execution': {'model_calls': 0, 'network_calls': 0, 'service_or_database_writes': 0}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    raw = args.input.read_bytes()
    result = diagnose_writing(json.loads(raw))
    result['input_file_sha256'] = sha256(raw).hexdigest()
    serialized = json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n'
    if len(serialized) > 10000:
        raise ValueError('Output exceeds 10000 characters; split the annotated document before export')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'input_sha256': result['input_file_sha256'],
                      'semantic_quality_status': result['semantic_quality_status']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
