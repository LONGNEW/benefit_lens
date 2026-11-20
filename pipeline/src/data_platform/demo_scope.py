"""The active project scope is ten student opportunities, with collection paused."""
import json
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[2] / 'config/student_demo_scope.json'


def student_scope():
    scope = json.loads(CONFIG.read_text())
    if len(scope['opportunity_ids']) != 10 or len(set(scope['opportunity_ids'])) != 10:
        raise ValueError('Student demo must contain ten distinct opportunities.')
    return scope


def require_collection_enabled():
    if not student_scope()['collection_enabled']:
        raise ValueError('Collection is paused. Process the ten stored student examples only.')


def validate_student_examples(items):
    scope = student_scope()
    if len(items) != 10 or {x['opportunity_id'] for x in items} != set(scope['opportunity_ids']):
        raise ValueError('Examples must match the ten selected student opportunities.')
    for item in items:
        if item.get('recipient_type') != 'individual' or item.get('applicant_scope') != 'student':
            raise ValueError('Only student individual opportunities are in scope.')
        if item['category'] in scope['excluded_categories']:
            raise ValueError('General research grants are outside the student demo.')
