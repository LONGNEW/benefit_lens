"""Typed condition envelope and conservative three-valued evaluation.

Field names describe semantic dimensions. A filled JSON property alone never
authorizes a personal decision. OR/AND/NOT remain explicit trees.
"""
from __future__ import annotations
import math

COMMON = {
 'education.degree':'enum_or_set','education.status':'enum_or_set',
 'education.full_time':'boolean','education.coursework_completed':'boolean',
 'education.study_duration':'number','education.earned_credits':'number',
 'education.academic_year':'number','education.gpa':'number',
 'education.major_group':'string','education.institution_id':'string',
 'person.nationalities':'array','person.age':'number','person.residency':'string',
 'work.authorization':'boolean','work.visa_sponsorship':'boolean',
 'work.availability':'object','experience.years':'number',
 'language.proficiency':'object','application.deadline':'date',
 'application.nomination':'object','application.document_requirement':'array',
 'benefit.amount':'number',
}
OPS={'eq','in','not_in','gte','lte','gt','lt','between','exists'}


def validate_leaf(node):
    required={'field','namespace','operator','value','value_type','unit','attributes','evidence',
              'subject','role','reference_phase','review_status'}
    if required-node.keys():raise ValueError('missing condition envelope keys')
    if node['namespace'] not in {'common','specific'}:raise ValueError('namespace')
    if node['namespace']=='common' and node['field'] not in COMMON:raise ValueError('unknown common dimension')
    if node['namespace']=='specific' and not node.get('parent_dimension'):raise ValueError('specific parent dimension')
    if node['operator'] not in OPS:raise ValueError('operator')
    value=node['value'];typ=node['value_type'];attrs=node['attributes']
    if value is not None:
        if typ=='boolean' and not isinstance(value,bool):raise ValueError('boolean type')
        if typ=='number' and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)):
            raise ValueError('finite numeric type')
        if typ=='array' and not isinstance(value,list):raise ValueError('array type')
    if node['field']=='education.gpa' and not attrs.get('scale'):raise ValueError('gpa denominator required')
    if node['field']=='education.study_duration' and node['unit'] not in {'year','month','semester'}:raise ValueError('study duration unit')
    if node['field']=='person.age' and not attrs.get('as_of_date'):raise ValueError('age reference date')
    if node['field']=='benefit.amount' and not all(attrs.get(k) for k in ['currency','payment_period']):raise ValueError('amount basis')
    for ev in node['evidence']:
        if not all(k in ev for k in ['document_id','text_sha256','char_start','char_end','quote']):raise ValueError('source binding')
        if not 0<=ev['char_start']<ev['char_end']:raise ValueError('source span')
    return node


def evaluate(tree, profile):
    """Return True, False or None. Similarity is never read by this function."""
    if 'all' in tree:
        values=[evaluate(x,profile) for x in tree['all']]
        return False if False in values else (None if None in values else True)
    if 'any' in tree:
        values=[evaluate(x,profile) for x in tree['any']]
        return True if True in values else (None if None in values else False)
    if 'not' in tree:
        v=evaluate(tree['not'],profile);return None if v is None else not v
    validate_leaf(tree)
    if tree['review_status']!='reviewed' or tree['role']!='eligibility' or tree['subject']!='applicant':return None
    actual=profile.get(tree['field'])
    if actual is None or tree['value'] is None:return None
    if actual.get('attributes',{})!=tree['attributes']:return None
    if actual.get('unit')!=tree['unit']:return None
    v=actual['value'];wanted=tree['value'];op=tree['operator']
    if v is None:return None
    if isinstance(v,(list,dict)) and op!='eq':return None
    try:
        return {'eq':lambda:v==wanted,'in':lambda:v in wanted,'not_in':lambda:v not in wanted,
          'gte':lambda:v>=wanted,'lte':lambda:v<=wanted,'gt':lambda:v>wanted,'lt':lambda:v<wanted,
          'between':lambda:wanted[0]<=v<=wanted[1], 'exists':lambda:True}[op]()
    except (TypeError,KeyError,IndexError):return None
