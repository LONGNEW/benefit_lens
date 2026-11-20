"""Audit experience-to-notice coverage without counting documents as people.

The planning denominator includes unresolved discovery candidates. It is a
capacity target, not a claim that these are currently open opportunities.
Only exact program bindings count for individual notice coverage.
"""
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import re
import unicodedata

import project

POLICY = project.ROOT / 'pipeline/config/experience_coverage_policy.json'


def norm(value):
    return re.sub(r'\W+', '', unicodedata.normalize('NFKC', value or '').casefold())


def coverage_report(notices, cases, inventory_count, verified_open_count, policy, *, floor=0):
    ratio = policy['minimum_experiences_per_notice']
    if not isinstance(ratio, int) or isinstance(ratio, bool) or ratio < 3:
        raise ValueError('Experience ratio must be an integer of at least three')
    aliases = policy.get('program_aliases', {})
    program = lambda value: aliases.get(value, value)
    kept = {}; held = Counter(); material = Counter(); writing_only=set()
    # Unknown rounds merge conservatively with all known rounds for that person.
    unknown = set()
    for c in cases:
        p = c['payload']; name = norm(c.get('person_name') or p.get('author_handle'))
        if p.get('active') is False:
            continue
        if name and name not in {'anonymous', 'anonymoususer'} and not c.get('application_round'):
            unknown.add((program(c['program_id']), name))
    for c in cases:
        p = c['payload']; audit = p.get('quality_audit') or {}
        if p.get('active') is False:
            held['inactive'] += 1; continue
        if not p.get('document_ids') or c.get('evidence_status') != 'public_original_collected':
            held['missing_original'] += 1; continue
        if audit.get('unresolved_document_ids') or audit.get('attribution_status') in {'unresolved', 'needs_review'}:
            held['unresolved_attribution'] += 1; continue
        roles = set(p.get('evidence_roles') or [])
        scope = p.get('case_scope')
        if scope:
            roles.add(scope)
        if scope in {'application_or_interview_preparation','application_preparation','multi_round_application_narrative'}:
            roles.add('application_process')
        if scope == 'post_award_experience':
            roles.add('participation_experience')
        source_roles=set(p.get('document_roles') or [])
        if source_roles.intersection({'application_bundle','application_statement','personal_statement','research_statement'}):
            roles.add('application_document')
        if source_roles.intersection({'experience_review','official_interview'}):
            roles.add('participation_experience')
        if not roles.intersection(policy['include_material_roles']):
            if 'application_document' in roles:
                writing_only.add(c['case_key'])
            held['not_a_review_or_process_experience'] += 1; continue
        pr = program(c['program_id']); name = norm(c.get('person_name') or p.get('author_handle'))
        if name and name not in {'anonymous', 'anonymoususer'}:
            cycle = None if (pr, name) in unknown else c.get('application_round')
            identity = ('named', pr, name, cycle)
        else:
            identity = ('anonymous', pr, p.get('text_key') or c['case_key'])
        if identity in kept:
            held['same_person_program_round'] += 1; continue
        kept[identity] = c
        for role in roles.intersection(policy['include_material_roles']):
            material[role] += 1
    by_program = defaultdict(list)
    for c in kept.values():
        by_program[program(c['program_id'])].append(c['case_key'])
    linked = []
    for notice in notices:
        ids = sorted(by_program[program(notice['program_id'])])
        linked.append(dict(notice, experience_count=len(ids), missing=max(0, ratio-len(ids)),
                           case_keys=ids, link_basis='exact_program_id',
                           round_scope='historical_or_unknown_round; current_prompt_transfer_requires_review'))
    target = max(floor, policy['minimum_total_experiences'], ratio * inventory_count)
    counted = len(kept)
    return {'schema': policy['schema'], 'at': datetime.now().astimezone().isoformat(),
            'planning_notice_candidates': inventory_count,
            'current_open_verified_discovery_records': verified_open_count,
            'registered_notice_records': len(notices), 'required_ratio': ratio,
            'experience_records_active': sum(c['payload'].get('active') is not False for c in cases),
            'counted_experience_episodes': counted, 'excluded': dict(held),
            'writing_document_only_records_separate':len(writing_only),
            'evidence_role_counts_overlap': dict(material),
            'target_experience_episodes': target, 'remaining': max(0, target-counted),
            'ratio_to_planning_candidates': counted/inventory_count if inventory_count else None,
            'global_ratio_met': counted >= ratio*inventory_count if inventory_count else None,
            'per_notice_coverage_met': bool(linked) and all(x['missing']==0 for x in linked),
            'all_inventory_notices_linked': False,
            'completion_met': False,
            'linkage_limitation': 'Discovery inventory has no reviewed program bindings; exact linking is available for registered notices only.',
            'registered_notice_coverage': linked}


def database_coverage(policy=None, *, floor=0):
    policy = policy or json.loads(POLICY.read_text())
    with project.connect() as c:
        # A consistent read prevents a growing inventory and cases from mixing snapshots.
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        inventory, verified = c.execute("""SELECT count(*), count(*) FILTER
            (WHERE payload->>'current_open_verified'='true')
            FROM benefitlens.support_discovery_record WHERE record_type!='sample'""").fetchone()
        notices = [dict(zip(('opportunity_id','program_id','title'), r)) for r in
                   c.execute('SELECT opportunity_id,program_id,title FROM benefitlens.opportunities')]
        rows = c.execute("""SELECT case_key,program_id,person_name,application_round,evidence_status,
            jsonb_build_object('active',payload->'active', 'document_ids',payload->'document_ids',
            'quality_audit',payload->'quality_audit','author_handle',payload->'author_handle',
            'evidence_roles',payload->'evidence_roles','document_roles',payload->'document_roles','case_scope',payload->'case_scope',
            'text_key',payload->'text_key') FROM benefitlens.experience_discovery_case""").fetchall()
        cases = [dict(zip(('case_key','program_id','person_name','application_round','evidence_status','payload'),r))
                 for r in rows]
    return coverage_report(notices,cases,inventory,verified,policy,floor=floor)


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=database_coverage()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='registered_notice_coverage'},ensure_ascii=False))
