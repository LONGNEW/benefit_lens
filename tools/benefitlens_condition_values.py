"""Conservative offline typed candidates; never an eligibility/approval engine.

Only selected stored mapping quotes are parsed. Existing source bindings are
preserved by the batch caller, not replayed against newly opened source text.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re
from datetime import date

CONTRACT = Path(__file__).resolve().parents[1] / 'pipeline/config/condition_value_contract_v2.json'
NUMBER = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
WORDS = {'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
         'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
         'first': 1, 'second': 2, 'third': 3, '한': 1, '두': 2, '세': 3}
PHASES = {'application', 'benefit_start', 'throughout_activity'}
OPS = {'eq', 'in', 'not_in', 'gte', 'lte', 'gt', 'lt', 'between', 'exists'}
DEGREES = {
    'associate': r'\bassociate(?:[’\']s)?\b|전문학사',
    'bachelor': r'\bbachelor(?:[’\']s)?\b|\bbaccalaureate\b|\bundergraduate\s+degree\b|\bB\.?[AS]\.?\b|(?<!전문)학사',
    'master': r'\bmaster(?:[’\']s)?\b|\bM\.?[AS]\.?\b|석사',
    'doctorate': r'\b(?:doctorate|doctoral|Ph\.?D\.?)\b|박사',
}


@lru_cache(maxsize=1)
def contract():
    return json.loads(CONTRACT.read_text())


def norm(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def tri_all(values):
    values = list(values)
    if any(v is not True and v is not False and v is not None for v in values):
        raise ValueError('truth values must be bool or None')
    return False if False in values else (None if None in values else True)


def tri_any(values):
    values = list(values)
    if any(v is not True and v is not False and v is not None for v in values):
        raise ValueError('truth values must be bool or None')
    return True if True in values else (None if None in values else False)


def tri_not(value):
    if value is None:
        return None
    if type(value) is not bool:
        raise ValueError('truth value must be bool or None')
    return not value


def normalize_degree(value, namespace='shared_v2'):
    """Explicit adapter only: callers must select a namespace."""
    found = [k for k, pattern in DEGREES.items() if re.search(pattern, value, re.I)]
    if len(found) != 1:
        return None
    if namespace == 'shared_v2':
        return found[0]
    if namespace == 'demo':
        return 'phd' if found[0] == 'doctorate' else found[0]
    raise ValueError('unknown degree namespace')


def compare_gpa(actual, required, actual_scale, required_scale):
    """Diagnostic comparison helper, not used to decide corpus eligibility."""
    if None in (actual, required, actual_scale, required_scale) or actual_scale != required_scale:
        return None
    if not 0 <= actual <= actual_scale or not 0 <= required <= required_scale:
        return None
    return actual >= required


def numbers(text):
    """Accept finite numeric tokens and a small explicit number-word grammar."""
    converted = text.lower().replace('–', '-').replace('—', '-').replace('~', '-')
    for word, value in sorted(WORDS.items(), key=lambda x: -len(x[0])):
        converted = re.sub(r'(?<!\w)' + re.escape(word) + r'(?!\w)', str(value), converted)
    # A partial parse of an unsupported hyphenated number-word is unsafe.
    if re.search(r'[a-z]+-\d|\d-[a-z]+', converted):
        return []
    return [float(x.replace(',', '')) for x in re.findall(NUMBER, converted)]


def scalar_or_range(text, operator):
    prefix = re.split(r'\d', text, maxsplit=1)[0]
    if re.search(r'-\s*$', prefix) or re.search(r'-\s*-', text):
        return None
    vals = numbers(text)
    if operator == 'between':
        if len(vals) == 2 and vals[0] <= vals[1]:
            return vals
        return None
    return vals[0] if len(vals) == 1 else None


def countries(text):
    return [code for code, aliases in contract()['country_alias_scope'].items()
            if any(re.search(r'(?<!\w)' + re.escape(a) + r'(?!\w)', text, re.I) for a in aliases)]


def duration_unit(text):
    for unit, pattern in [('semester_hour', r'semester\s+hours?'), ('credit', r'credits?|학점'),
                          ('semester', r'semesters?|학기'), ('month', r'months?|개월'),
                          ('year', r'years?|(?<!학)년')]:
        if re.search(pattern, text, re.I):
            return unit
    return None


def date_candidate(text):
    found = re.findall(r'(?<!\d)(\d{4})[./년-]\s*(\d{1,2})[./월-]\s*(\d{1,2})(?:일)?', text)
    if len(found) != 1:
        return None
    try:
        return date(*(int(n) for n in found[0])).isoformat()
    except ValueError:
        return None


def amount_attributes(evidence):
    attrs = {}
    codes = re.findall(r'\b(?:USD|CAD|AUD|NZD|SGD|HKD|EUR|GBP|KRW|JPY|CNY|INR)\b', evidence, re.I)
    if re.search(r'원|₩', evidence):
        codes.append('KRW')
    if '€' in evidence:
        codes.append('EUR')
    if '£' in evidence:
        codes.append('GBP')
    if len(set(x.upper() for x in codes)) == 1:
        attrs['currency'] = codes[0].upper()
    periods = {'hour': r'per\s+hour|an?\s+hour|hourly|시급|시간당',
               'day': r'per\s+day|daily|일당', 'week': r'per\s+week|weekly|주당',
               'month': r'per\s+month|monthly|월급|월\s*지급|매월',
               'semester': r'per\s+semester|학기당',
               'year': r'per\s+year|annually|annual\s+(?:salary|pay)|연봉|연간',
               'one_time': r'one[ -]time|일시금|1회\s*지급'}
    found = [k for k, pat in periods.items() if re.search(pat, evidence, re.I)]
    if len(found) == 1:
        attrs['payment_period'] = found[0]
    # No currency is inferred from a bare dollar sign or an unverified unit label.
    return attrs


def parse_candidate(mapping):
    """Return a syntactic candidate plus local holds without mutating input."""
    field = mapping['field_id']
    definition = contract()['field_definitions'][field]
    quote, evidence = norm(mapping.get('value_quote')), norm(mapping.get('evidence_quote'))
    op = mapping.get('operator')
    reasons, attrs, proposals = [], {}, []
    value, kind, unit = None, definition['type'], definition.get('unit')
    if field == 'education.degree':
        vals = [k for k, pat in DEGREES.items() if re.search(pat, quote, re.I)]
        # Preserve professional doctorates and equivalent alternatives for review.
        if re.search(r'PharmD|Juris|equivalent|GED|high\s+school', quote, re.I):
            reasons.append('degree_outside_supported_namespace_or_equivalence')
        elif vals:
            value = vals if len(vals) > 1 or op in ('in', 'not_in') else vals[0]
        if re.search(r'currently|pursu|enroll|在学|재학', evidence, re.I):
            attrs['degree_state'] = 'pursuing'
        elif re.search(r'hold|earned|awarded|취득|학위\s*소지', evidence, re.I):
            attrs['degree_state'] = 'earned'
    elif field == 'education.status':
        patterns = {'enrolled': r'enrolled|currently\s+attending|재학', 'on_leave': r'leave\s+of\s+absence|휴학',
                    'expected_graduate': r'expected\s+graduat|졸업\s*예정', 'graduated': r'graduated|졸업자'}
        vals = [k for k, pat in patterns.items() if re.search(pat, quote, re.I)]
        if re.search(r'not\s+enrolled|미재학|재학.*아니', quote, re.I):
            reasons.append('status_negation_requires_review')
        elif vals:
            value = vals if len(vals) > 1 else vals[0]
    elif kind == 'boolean':
        if duration_unit((mapping.get('unit') or '') + ' ' + quote) or op in ('gte', 'lte', 'gt', 'lt', 'between'):
            reasons.append('quantity_is_not_boolean')
            u = duration_unit((mapping.get('unit') or '') + ' ' + quote)
            n = scalar_or_range(quote, op)
            if field == 'education.coursework_completed' and n is not None and u:
                proposals.append({'field': 'education.earned_credits' if u in ('credit', 'semester_hour') else 'education.study_duration',
                                  'type': 'number_or_range', 'value': n, 'unit': u,
                                  'attributes': {'reference_phase': mapping.get('reference_phase')},
                                  'state': 'unreviewed_redirect_candidate_not_replacement'})
        elif '?' in evidence or re.search(r'unknown|not\s+(?:known|specified)|미상|미기재', quote, re.I):
            reasons.append('boolean_polarity_unknown')
        elif field == 'education.full_time':
            full = bool(re.search(r'full[ -]time|전일제', evidence, re.I))
            part = bool(re.search(r'part[ -]time|비전일제', evidence, re.I))
            if full and part:
                reasons.append('boolean_multiple_alternatives_unscoped')
            elif re.search(r'not\s+required|필수.*아니', evidence, re.I):
                reasons.append('boolean_negation_scope_unresolved')
            elif part or re.search(r'not\s+full[ -]time', evidence, re.I):
                value = False
            elif full:
                value = True
        elif field == 'education.coursework_completed':
            attrs['coursework_scope'] = 'coursework' if re.search(r'coursework|교과', evidence, re.I) else None
            if re.search(r'not\s+completed\s+(?:the\s+)?coursework|교과.*미수료', evidence, re.I):
                value = False
            elif re.search(r'completed\s+(?:all\s+|the\s+)?coursework|coursework\s+(?:is\s+)?completed|교과.*수료', evidence, re.I):
                value = True
        elif field == 'work.authorization':
            if re.search(r'not\s+(?:legally\s+)?authorized|취업\s*불가', evidence, re.I):
                value = False
            elif re.search(r'(?:legally\s+authorized\s+to\s+work|valid\s+work\s+authorization|취업\s*자격)', evidence, re.I):
                value = True
            cs = countries(evidence)
            if len(cs) == 1:
                attrs['jurisdiction'] = cs[0]
        elif field == 'work.visa_sponsorship':
            if mapping.get('subject') != 'applicant':
                reasons.append('sponsorship_actor_is_not_applicant_need')
            elif re.search(r'without\s+(?:visa\s+)?sponsorship|not\s+require.*sponsor|비자\s*지원.*불필요', evidence, re.I):
                value = False
            elif re.search(r'(?:need|require)\s+(?:visa\s+)?sponsorship|비자\s*지원.*필요', evidence, re.I):
                value = True
            cs = countries(evidence)
            if len(cs) == 1:
                attrs['jurisdiction'] = cs[0]
    elif field in ('education.gpa', 'person.age', 'experience.years', 'benefit.amount'):
        if re.search(r'\bGDP\b|gross\s+domestic\s+product|국내\s*총생산', evidence, re.I):
            reasons.append('numeric_context_is_gdp_not_field')
        elif field != 'benefit.amount' and re.search(r'salary|pay\s+range|연봉|시급|급여', evidence, re.I):
            reasons.append('numeric_context_is_compensation_not_field')
        elif field == 'education.gpa' and not re.search(r'\bGPA\b|grade\s+point|평점|학점', evidence, re.I):
            reasons.append('gpa_context_missing')
        elif field == 'person.age' and not re.search(r'\bage[ds]?\b|years?\s+old|세\b|만\s*\d', evidence, re.I):
            reasons.append('age_context_missing')
        elif field == 'experience.years' and not re.search(r'experience|경력|경험', evidence, re.I):
            reasons.append('experience_context_missing')
        else:
            numeric_quote = quote
            # A denominator is a scale, not a second threshold.
            if field == 'education.gpa':
                scale = re.search(r'(?:out\s+of\s+|on\s+(?:a\s+)?)(\d+(?:\.\d+)?)(?:\s*point|\s*scale)?|/\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*만점', evidence, re.I)
                if scale:
                    attrs['scale'] = float(next(x for x in scale.groups() if x))
                    numeric_quote = re.split(r'/|out\s+of', quote, flags=re.I)[0]
                for attr, pat, label in [('degree_scope', r'undergraduate|학부', 'undergraduate'),
                                         ('degree_scope', r'graduate|대학원', 'graduate'),
                                         ('period_scope', r'cumulative|누적|전체\s*학기', 'cumulative')]:
                    if attr not in attrs and re.search(pat, evidence, re.I):
                        attrs[attr] = label
            value = scalar_or_range(numeric_quote, op)
            if field == 'benefit.amount':
                attrs.update(amount_attributes(evidence))
                if re.search(r'만원', quote) and value is not None:
                    value = [x * 10000 for x in value] if isinstance(value, list) else value * 10000
                elif re.search(r'천원', quote) and value is not None:
                    value = [x * 1000 for x in value] if isinstance(value, list) else value * 1000
            if field in ('experience.years', 'person.age'):
                raw_unit = norm(mapping.get('unit'))
                if raw_unit.lower() not in ('year', 'years', '세', '년', 'age', 'years old'):
                    reasons.append('numeric_unit_not_verified_year')
                    value = None
            if value is not None:
                vals = value if isinstance(value, list) else [value]
                lo, hi = definition['bounds']
                if any(x < lo or x > hi for x in vals):
                    reasons.append('numeric_value_out_of_supported_range')
                    value = None
                if field == 'education.gpa' and attrs.get('scale') is not None and any(x > attrs['scale'] for x in vals):
                    reasons.append('gpa_exceeds_explicit_scale')
                    value = None
    elif field == 'application.deadline':
        value = date_candidate(quote)
        if value:
            attrs['precision'] = 'day'
        timezone = re.search(r'\b(?:UTC|GMT|KST|JST|PST|EST)(?:[+-]\d{1,2}(?::\d{2})?)?\b', evidence)
        if timezone:
            attrs['timezone'] = timezone.group()
    elif field == 'person.nationalities':
        if quote:
            value = [quote]  # A source label array, never inferred citizenship.
            cs = countries(quote)
            if cs and not re.search(r'other|eligible\s+countries|기타|다른', quote, re.I):
                attrs['country_codes'] = cs
    elif field == 'program_specific.requirement':
        reasons.append('program_specific_type_unresolved')
        key = mapping.get('program_specific_key') or ''
        if key in ('academic_year', 'year_of_study', 'study_year'):
            n = scalar_or_range(quote, op)
            if n is not None:
                proposals.append({'field': 'education.academic_year', 'type': 'number_or_range', 'value': n,
                                  'unit': 'academic_year', 'attributes': {'reference_phase': mapping.get('reference_phase')},
                                  'state': 'unreviewed_redirect_candidate_not_replacement'})
    elif quote:
        value = quote
    if value is None:
        reasons.append('typed_value_not_parsed')
    candidate = None if value is None else {'field': field, 'type': kind, 'value': value, 'unit': unit,
                                          'attributes': attrs, 'namespace': 'condition-value-candidates-v2'}
    return candidate, sorted(set(reasons)), proposals


def assess(mapping, semantic_review='unreviewed'):
    candidate, parse_holds, proposals = parse_candidate(mapping)
    definition = contract()['field_definitions'][mapping['field_id']]
    missing = []
    attrs = (candidate or {}).get('attributes', {})
    for key in definition['required_attributes']:
        if attrs.get(key) is None:
            missing.append('missing_attribute:' + key)
    if mapping.get('reference_phase') not in PHASES:
        missing.append('reference_phase_unknown')
    if mapping.get('subject') in (None, 'unknown'):
        missing.append('subject_unknown')
    if mapping.get('modality') in (None, 'unknown'):
        missing.append('modality_unknown')
    if mapping.get('operator') not in OPS:
        missing.append('operator_unknown')
    if mapping.get('ambiguities'):
        missing.append('source_ambiguities_preserved')
    cues = mapping.get('logic_cues') or []
    if any(c.get('kind') in ('and', 'or', 'not', 'exception') for c in cues):
        missing.append('scoped_logic_tree_missing')
    if parse_holds:
        missing.extend(parse_holds)
    return {'candidate': candidate, 'additional_type_proposals': proposals,
            'type_parse_success': candidate is not None,
            'required_attributes_complete_local': candidate is not None and not missing,
            'human_meaning_review_match': False,
            'human_review_state': 'not_performed_by_this_audit',
            'stored_semantic_review': semantic_review,
            'execution_possible': False,
            'execution_holds': ['offline_candidate_only', 'meaning_approval_not_created',
                                'program_round_identity_not_resolved', 'full_notice_logic_not_reviewed',
                                'executor_not_attached'],
            'parse_holds': parse_holds, 'local_attribute_holds': sorted(set(missing)),
            'logic': {'cues': cues, 'tree': None, 'scope': 'selected_mapping_only'},
            'predicate_role': ('preference_only' if mapping.get('statement_type') == 'selection_preference'
                               else 'applicant_requirement_candidate' if mapping.get('subject') == 'applicant'
                               and mapping.get('statement_type') == 'eligibility_requirement'
                               else 'not_applicant_eligibility_predicate')}
