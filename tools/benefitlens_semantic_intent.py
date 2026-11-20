"""Source-bound, open semantic intent observations and cautious overlap checks."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[1]
VERSION = 'semantic-writing-intent-v1'
SCHEMA = REPO/'pipeline/schemas/writing-intent-observation.schema.json'
POLICY = REPO/'pipeline/config/semantic_answer_intents_v1.json'


def text_hash(text):
    return sha256(text.encode('utf-8')).hexdigest()


def locate_quote(text, quote, start=0, end=None):
    """Allow whitespace normalization while retaining exact original offsets."""
    end = len(text) if end is None else end
    if not quote.strip() or len(quote.split()) > 20:
        raise ValueError('Quote must have 1..20 words')
    pattern = r'\s+'.join(re.escape(x) for x in quote.split())
    matches = list(re.finditer(pattern, text[start:end]))
    if len(matches) != 1:
        raise ValueError('Quote is absent or ambiguous inside task context')
    a, b = start + matches[0].start(), start + matches[0].end()
    return {'char_start': a, 'char_end': b, 'exact_quote': text[a:b],
            'line_start': text[:a].count('\n')+1, 'line_end': text[:b].count('\n')+1}


def validate_observations(result, bundle):
    Draft202012Validator(json.loads(SCHEMA.read_text())).validate(result)
    documents = {d['document_id']: d for d in bundle['documents']}
    tasks = {t['task_id']: t for t in bundle['tasks']}
    if len(tasks) != len(bundle['tasks']) or len(documents) != len(bundle['documents']):
        raise ValueError('Duplicate input identifiers')
    rows = {r['task_id']: r for r in result['observations']}
    if len(rows) != len(result['observations']) or rows.keys() != tasks.keys():
        raise ValueError('Every input task must have exactly one output')
    for doc in documents.values():
        if text_hash(doc['text']) != doc['text_sha256']:
            raise ValueError('Source text hash mismatch')
    known = json.loads(POLICY.read_text())['initial_axes']
    checked = deepcopy(result)
    for row in checked['observations']:
        task = tasks[row['task_id']]
        doc = documents[task['document_id']]
        a, b = task['context_start'], task['context_end']
        if not 0 <= a < b <= len(doc['text']):
            raise ValueError('Invalid source context bounds')
        if row['purpose_status'] == 'explicit_guidance' and doc['publisher_role'] != 'official_program':
            raise ValueError('Official meaning requires an official-program source')
        expected = task.get('declared_use')
        if expected and row['answer_use'] != expected:
            raise ValueError('Analysis changed the declared source use')
        if expected == 'data_collection' and (row['purpose_status'] != 'declared_non_selection'
                or row['axes'] or row['answer_strategy']['status'] != 'not_needed'):
            raise ValueError('Declared statistical data must not acquire selection intent')
        if row['answer_strategy']['status'] == 'not_needed' and (
                row['answer_strategy']['opening_function'] is not None or
                row['answer_strategy']['closing_function'] is not None or
                row['answer_strategy']['logical_links']):
            raise ValueError('Non-persuasive tasks must not receive a forced essay structure')
        if len(set(row['axes'])) != len(row['axes']):
            raise ValueError('Duplicate intent axis')
        roles = [r['role'] for r in row['evidence_demands']]
        if len(roles) != len(set(roles)):
            raise ValueError('Duplicate evidence-demand role')
        for claim in row['source_evidence']:
            if claim['document_id'] != doc['document_id']:
                raise ValueError('Evidence belongs to a different task source')
            claim.update(locate_quote(doc['text'], claim['quote'], a, b), text_sha256=doc['text_sha256'])
        row.update(question_text=task['question_text'], question_kind=task['question_kind'],
                   source_role=doc['source_role'], source_url=doc['url'],
                   new_axis_candidates=[x for x in row['axes'] if x not in known],
                   semantic_review='unreviewed', approved_pattern=False,
                   validation='schema_task_coverage_hash_and_quote_spans')
        if chr(183) in json.dumps(row, ensure_ascii=False):
            raise ValueError('Authored Korean output must not contain U+00B7')
    return checked


def compare_intents(left, right):
    """Shared meaning is a retrieval aid, never automatic question equivalence."""
    a, b = set(left['axes']), set(right['axes'])
    common = sorted(a & b)
    lr = {x['role'] for x in left['evidence_demands']}
    rr = {x['role'] for x in right['evidence_demands']}
    compatible = (a == b and lr == rr and
                  left['answer_use'] == right['answer_use'] and
                  left['temporal_scope'] == right['temporal_scope'])
    return {'left': left['task_id'], 'right': right['task_id'], 'shared_axes': common,
            'left_only_demands': sorted(lr-rr), 'right_only_demands': sorted(rr-lr),
            'relation': 'same_dimensions_candidate' if common and compatible else
                        'partial_overlap' if common else 'no_shared_axis',
            'equivalence_review': 'context_and_constraints_still_required'}
