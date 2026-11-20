"""Executable stage contracts for the bounded, offline student demo replay."""
from __future__ import annotations

from datetime import date
import hashlib
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


def leaves(tree):
    if tree is None:
        return
    for kind in ('all', 'any'):
        if kind in tree:
            for child in tree[kind]:
                yield from leaves(child)
            return
    if 'not' in tree:
        yield from leaves(tree['not'])
        return
    yield tree


def validate_notice(item):
    schema = json.loads((ROOT / 'schemas/opportunity.schema.json').read_text())
    Draft202012Validator(schema).validate(item)
    fields = json.loads((ROOT / 'config/eligibility_fields.json').read_text())['fields']
    refs = {r['id'] for r in item['source_refs']}
    if len(refs) != len(item['source_refs']):
        raise ValueError('Duplicate source reference ID')
    checks = item['additional_checks']
    if len({c['check_id'] for c in checks}) != len(checks):
        raise ValueError('Duplicate additional check ID')
    conditions = list(leaves(item['eligibility']))
    for value in conditions + item['benefits'] + checks + item['requirements']:
        if value['evidence_ref'] not in refs:
            raise ValueError('Unknown local evidence reference: ' + value['evidence_ref'])
    for leaf in conditions:
        definition = fields.get(leaf['field'])
        if definition is None:
            raise ValueError('Unregistered eligibility field: ' + leaf['field'])
        op, value = leaf['op'], leaf['value']
        if op in {'in', 'not_in', 'between'}:
            if not isinstance(value, list) or not value or (op == 'between' and len(value) != 2):
                raise ValueError('Operator needs a non-empty array or two range bounds')
            values = value
        else:
            values = [value]
        kind = definition['type']
        valid = {'string': lambda v: isinstance(v, str),
                 'string_array': lambda v: isinstance(v, str),
                 'boolean': lambda v: type(v) is bool,
                 'integer': lambda v: type(v) is int,
                 'number': lambda v: type(v) in {int, float} and math.isfinite(v)}[kind]
        if not all(valid(v) for v in values):
            raise ValueError('Wrong value type for ' + leaf['field'])
        if definition.get('values') and not set(values) <= set(definition['values']):
            raise ValueError('Unknown categorical value for ' + leaf['field'])
        if kind in {'boolean', 'string_array'} and op not in ({'eq'} if kind == 'boolean' else {'in', 'not_in'}):
            raise ValueError('Unsupported operator for field type')
        if kind == 'string' and op not in {'eq', 'in', 'not_in'}:
            raise ValueError('String ordering is not supported by this contract')
        if op == 'between' and values[0] > values[1]:
            raise ValueError('Reversed range')
        if leaf['field'] == 'education.gpa':
            if op != 'gte' or not all(k in leaf for k in ('scale', 'degree_scope', 'period_scope')):
                raise ValueError('GPA needs threshold, scale, degree and period')
            if not 0 <= value <= leaf['scale']:
                raise ValueError('GPA threshold is outside its scale')
    application = item['application']
    dates = [date.fromisoformat(application[k]) for k in ('opens_on', 'closes_on') if application.get(k)]
    if len(dates) == 2 and dates[0] > dates[1]:
        raise ValueError('Application dates reversed')
    return {'condition_leaves': len(conditions), 'eligibility_available': item['eligibility'] is not None}


def verify_document(document):
    for path_key, hash_key in (('raw_path', 'sha256'), ('text_path', 'text_sha256')):
        path = Path(document[path_key])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != document[hash_key]:
            raise ValueError('Original missing or changed: ' + document['id'] + '/' + path_key)
    return document


def build_outline(pattern):
    if len(pattern['sections']) != len(pattern['required_inputs']):
        raise ValueError('Pattern sections and requested facts differ')
    return '\n\n'.join(f'{i}. {section}\n[{fact}]' for i, (section, fact) in
                       enumerate(zip(pattern['sections'], pattern['required_inputs']), 1))
