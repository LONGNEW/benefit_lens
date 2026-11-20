"""Deterministic paragraph plans from one person's confirmed experience facts.

This module creates a source-bound plan and information requests. It neither
writes a finished application nor assigns semantic writing quality scores.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
VERSION = 'writing-plan-v1'
SLOT = re.compile(r'\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}')


def _read(name):
    return json.loads((REPO / 'pipeline/config' / name).read_text())


def _fingerprint(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _template(value):
    if isinstance(value, str):
        row = deepcopy(_read('paragraph_writing_templates_v1.json')['patterns'][value])
        row['template_id'] = value
        return row
    if not isinstance(value, dict) or not isinstance(value.get('paragraphs'), list):
        raise ValueError('Expected a registered template ID or a paragraph template object')
    return deepcopy(value)


def _slots(paragraph):
    texts = [paragraph.get('opening', ''), *paragraph.get('body', []), paragraph.get('closing', '')]
    return sorted({slot for text in texts for slot in SLOT.findall(text)})


def _facts(task, facts):
    policy = _read('writing_execution_policy_v1.json')
    accepted, held, seen = {}, [], set()
    for fact in facts:
        fid = fact.get('fact_id')
        if not isinstance(fid, str) or not fid or fid in seen:
            raise ValueError('Missing or duplicate fact_id')
        seen.add(fid)
        source = fact.get('source') or {}
        reason = None
        if not task.get('subject_id') or not task.get('experience_id'):
            reason = 'target_identity_missing'
        elif fact.get('subject_id') != task['subject_id']:
            reason = 'different_person'
        elif fact.get('experience_id') != task['experience_id']:
            reason = 'different_experience'
        elif fact.get('status') != policy['accepted_fact_status']:
            reason = 'fact_not_confirmed'
        elif source.get('kind') not in policy['accepted_fact_source_kinds']:
            reason = 'unsupported_fact_source'
        elif source.get('owner_subject_id') != task['subject_id']:
            reason = 'source_owner_mismatch'
        elif not source.get('evidence_id'):
            reason = 'confirmation_evidence_missing'
        elif not isinstance(fact.get('field'), str) or not fact['field']:
            reason = 'fact_field_missing'
        elif fact.get('value') in (None, ''):
            reason = 'fact_value_missing'
        if reason:
            held.append({'fact_id': fid, 'reason': reason})
        else:
            accepted[fid] = deepcopy(fact)
    return accepted, held


def _requirements(task):
    output = []
    for item in task.get('requirements', []):
        basis = item.get('basis', 'inference')
        if basis not in ('literal', 'official_guidance', 'inference'):
            raise ValueError('Requirement basis must preserve literal, guidance or inference')
        ref = item.get('source_ref') or task.get('source_ref') or {}
        official = (ref.get('publisher_role') == 'official_program'
                    and ref.get('status') == 'verified_current')
        row = deepcopy(item)
        row.update(basis=basis, mandatory=bool(item.get('required') is True
                    and basis == 'literal' and official),
                   evidence_status='input_source_ref_retained_not_reverified')
        output.append(row)
    return output


def _bind(slot, task, facts):
    explicit = task.get('slot_bindings', {}).get(slot)
    candidates = ([facts[explicit]] if explicit in facts else []) if explicit else [
        fact for fact in facts.values() if fact['field'] == slot]
    values = {_fingerprint(fact['value']) for fact in candidates}
    if explicit and explicit not in facts:
        return None, 'bound_fact_unavailable'
    if len(values) > 1:
        return None, 'conflicting_confirmed_values'
    if not candidates:
        return None, 'confirmed_fact_missing'
    return candidates[0], None


def _constraint_conflicts(task, template, profile):
    conflicts = []
    constraints = task.get('constraints') or {}
    count = len(template['paragraphs'])
    maximum = constraints.get('max_paragraphs')
    for key in ('max_paragraphs', 'max_words', 'max_characters'):
        value = constraints.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            raise ValueError('Official limits must be positive integers')
    if maximum is not None and count > maximum:
        conflicts.append({'code': 'template_exceeds_official_paragraph_limit',
                          'actual': count, 'maximum': maximum,
                          'resolution': 'adapt_template_before_drafting'})
    if constraints.get('answer_format') in ('single_sentence', 'single_paragraph') and count > 1:
        conflicts.append({'code': 'official_short_answer_conflicts_with_template',
                          'resolution': 'official_format_takes_precedence'})
    preferred = profile.get('preferred_paragraph_count')
    if preferred and maximum and preferred > maximum:
        conflicts.append({'code': 'style_preference_overridden_by_official_limit',
                          'resolution': 'suppress_preference_preserve_official_limit'})
    return conflicts


def _declared_task_coverage(task):
    """Preserve caller-declared gaps without inferring full-question coverage."""
    constraints = task.get('constraints') or {}
    aspects = []
    for source in (task, constraints):
        declared = source.get('unfilled_task_aspects', [])
        if not isinstance(declared, list) or any(
                not isinstance(item, str) or not item.strip() for item in declared):
            raise ValueError('unfilled_task_aspects must be a list of nonempty strings')
        for item in declared:
            if item not in aspects:
                aspects.append(item)
    return {'scope': constraints.get('coverage_scope', task.get('coverage_scope', 'not_declared')),
            'declared_unfilled_aspects': aspects,
            'assessment': 'input_declaration_only_no_semantic_inference',
            'full_task_coverage_confirmed': False}


def build_paragraph_plan(task_spec, confirmed_facts, template, style_profile=None):
    """Build a plan; field names or explicit slot_bindings select fact IDs.

    Facts require fact_id, subject_id, experience_id, field, value, confirmed
    status, and source kind, owner_subject_id and evidence_id. Source corpus
    facts and different experiences cannot fill personal slots.
    """
    task = deepcopy(task_spec)
    profile = deepcopy(style_profile or {})
    pattern = _template(template)
    nonpersuasive = (task.get('answer_use') == 'data_collection'
                     or task.get('question_kind') == 'administrative_instruction')
    facts, held = _facts(task, confirmed_facts)
    rubric = _read('writing_quality_rubric_v1.json')
    principles = _read('writing_principles_v1.json')
    conflicts = [] if nonpersuasive else _constraint_conflicts(task, pattern, profile)
    missing, paragraphs = {}, []
    for i, paragraph in enumerate([] if nonpersuasive else pattern['paragraphs']):
        pid = 'p' + str(i + 1)
        bound = []
        for slot in _slots(paragraph):
            fact, reason = _bind(slot, task, facts)
            if reason:
                request = missing.setdefault(slot, {'field': slot, 'reason': reason,
                    'question': '본인 경험에서 ' + slot + '에 해당하는 확인 사실을 알려주세요.',
                    'required_by_paragraphs': []})
                request['required_by_paragraphs'].append(pid)
            else:
                bound.append({'slot': slot, 'fact_id': fact['fact_id'],
                              'value': deepcopy(fact['value']),
                              'evidence_id': fact['source']['evidence_id']})
        paragraphs.append({'paragraph_id': pid, 'purpose': paragraph['purpose'],
            'claim': {'intent': paragraph['purpose'], 'grounded_facts': bound,
                      'statement_text': None, 'status': 'planned_not_semantically_assessed'},
            'evidence_fact_ids': sorted({fact['fact_id'] for fact in bound}),
            'required_slots': _slots(paragraph),
            'opening_intent': paragraph['opening_intent'],
            'closing_intent': paragraph['closing_intent'],
            'previous_link': None if i == 0 else {'paragraph_id': 'p' + str(i),
                'handoff_slot': pattern['paragraphs'][i-1].get('bridge_to_next'),
                'relation': 'editorial_bridge_candidate_not_semantic_judgment'},
            'next_link': None if i + 1 == len(pattern['paragraphs']) else {
                'paragraph_id': 'p' + str(i + 2), 'handoff_slot': paragraph.get('bridge_to_next'),
                'relation': 'editorial_bridge_candidate_not_semantic_judgment'}})
    if not nonpersuasive and (not task.get('subject_id') or not task.get('experience_id')):
        missing['target_identity'] = {'field': 'target_identity', 'reason': 'target_identity_missing',
            'question': '이번 계획에 사용할 본인과 경험의 ID를 확인해주세요.',
            'required_by_paragraphs': []}
    ref = task.get('source_ref') or {}
    if not nonpersuasive and (ref.get('status') != 'verified_current'
                              or ref.get('publisher_role') != 'official_program'):
        missing['current_task_source'] = {'field': 'current_task_source',
            'reason': 'current_official_task_not_verified',
            'question': '현재 회차의 공식 문항과 제출 조건을 확인해주세요.',
            'required_by_paragraphs': []}
    if not nonpersuasive and not task.get('question_text'):
        missing['question_text'] = {'field': 'question_text', 'reason': 'task_question_missing',
            'question': '답변할 문항을 확인해주세요.', 'required_by_paragraphs': []}
    coverage = _declared_task_coverage(task)
    if not nonpersuasive:
        for aspect in coverage['declared_unfilled_aspects']:
            request = missing.setdefault(aspect, {'field': aspect,
                'reason': 'explicit_unfilled_task_aspect',
                'question': '전체 문항에서 아직 채워지지 않은 ' + aspect + '의 확인 정보와 작성 계획을 알려주세요.',
                'required_by_paragraphs': []})
            request['declared_unfilled_task_aspect'] = True
    synthetic = any(fact['source']['kind'] == 'synthetic_confirmed_fact' for fact in facts.values())
    return {'version': VERSION, 'policy_version': 'writing-execution-policy-v1',
        'task_id': task.get('task_id'), 'subject_id': task.get('subject_id'),
        'experience_id': task.get('experience_id'), 'program_id': task.get('program_id'),
        'round_id': task.get('round_id'), 'task_spec': task,
        'template_id': pattern.get('template_id', 'provided-template'),
        'template_snapshot': pattern,
        'template_fingerprint': _fingerprint(pattern),
        'input_fingerprint': _fingerprint([task, confirmed_facts, pattern, profile]),
        'question_text': task.get('question_text'), 'source_ref': ref,
        'meaning_observations': _requirements(task),
        'intent_observations': deepcopy(task.get('intent_observations', [])),
        'intent_validation': 'source_binding_and_semantic_review_remain_upstream_responsibilities',
        'writing_basis': {'general_principle_ids': [p['id'] for p in principles],
            'genre_scope': pattern.get('scope'), 'style_profile': profile,
            'style_scope': 'user_preference_not_universal_quality'},
        'style_application': {'first_last_links_requested': bool(profile.get('first_last_links')),
            'status': 'not_applicable' if nonpersuasive else 'requires_resolution' if conflicts else 'plan_links_only',
            'prose_coherence_assessed': False},
        'paragraphs': paragraphs, 'need_info': list(missing.values()),
        'held_facts': held, 'constraint_conflicts': conflicts,
        'constraints': deepcopy(task.get('constraints') or {}),
        'task_coverage': coverage,
        'status': 'not_applicable' if nonpersuasive else 'needs_resolution' if conflicts else 'need_info' if missing else 'plan_ready',
        'writing_applicability': 'administrative_field_not_essay' if nonpersuasive else 'writing_plan_candidate',
        'input_synthetic': synthetic, 'ready_for_real_application': False,
        'quality_diagnostics': [{'criterion_id': c['id'], 'score': None,
            'state': 'not_evaluated', 'applies': c['applies'], 'source_basis': c['basis']}
            for c in rubric['criteria']],
        'required_checks': [dict(check, state='not_evaluated', reason='No finished draft was generated')
                            for check in rubric['required_checks']],
        'semantic_review': 'not_performed', 'approved_pattern': False,
        'draft_text': None, 'is_finished_application': False,
        'model_called': False, 'network_called': False, 'database_written': False}


def validate_plan(plan, task_spec, confirmed_facts):
    """Check attribution and plan consistency, without grading writing quality."""
    errors = []
    facts, _ = _facts(task_spec, confirmed_facts)
    if plan.get('subject_id') != task_spec.get('subject_id') or plan.get('experience_id') != task_spec.get('experience_id'):
        errors.append('plan_identity_changed')
    if plan.get('task_spec') != task_spec or plan.get('source_ref') != (task_spec.get('source_ref') or {}):
        errors.append('task_source_or_constraints_changed')
    if plan.get('meaning_observations') != _requirements(task_spec):
        errors.append('official_requirement_or_observation_changed')
    if plan.get('intent_observations') != task_spec.get('intent_observations', []):
        errors.append('upstream_semantic_observation_changed')
    paragraphs = plan.get('paragraphs', [])
    template = plan.get('template_snapshot') or {}
    if _fingerprint(template) != plan.get('template_fingerprint'):
        errors.append('template_snapshot_changed')
    nonpersuasive = (task_spec.get('answer_use') == 'data_collection'
                     or task_spec.get('question_kind') == 'administrative_instruction')
    expected_paragraphs = [] if nonpersuasive else template.get('paragraphs', [])
    if len(paragraphs) != len(expected_paragraphs):
        errors.append('paragraph_plan_coverage_changed')
    for i, paragraph in enumerate(paragraphs):
        required = set(paragraph.get('required_slots', []))
        if i < len(expected_paragraphs):
            expected = expected_paragraphs[i]
            if required != set(_slots(expected)):
                errors.append('template_material_requirements_changed')
            if any(paragraph.get(key) != expected.get(key) for key in (
                    'purpose', 'opening_intent', 'closing_intent')):
                errors.append('paragraph_purpose_or_sentence_function_changed')
        if paragraph.get('paragraph_id') != 'p' + str(i+1):
            errors.append('paragraph_identifier_invalid')
        bound = paragraph.get('claim', {}).get('grounded_facts', [])
        if paragraph.get('claim', {}).get('statement_text') is not None:
            errors.append('unperformed_claim_generation')
        used = set()
        for binding in bound:
            fact = facts.get(binding.get('fact_id'))
            if not fact:
                errors.append('unconfirmed_or_foreign_fact_in_plan')
                continue
            if _fingerprint(fact['value']) != _fingerprint(binding.get('value')) or fact['source']['evidence_id'] != binding.get('evidence_id'):
                errors.append('fact_value_or_evidence_changed')
            expected, _ = _bind(binding['slot'], task_spec, facts)
            if expected is None or expected['fact_id'] != fact['fact_id']:
                errors.append('fact_not_bound_to_requested_slot')
            used.add(binding['slot'])
        if used - required or paragraph.get('evidence_fact_ids') != sorted({b['fact_id'] for b in bound}):
            errors.append('paragraph_evidence_index_inconsistent')
        unanswered = {n['field'] for n in plan.get('need_info', [])}
        if required - used - unanswered:
            errors.append('missing_material_not_requested')
        previous, following = paragraph.get('previous_link'), paragraph.get('next_link')
        if (previous is None) != (i == 0) or (following is None) != (i == len(paragraphs)-1):
            errors.append('paragraph_boundary_link_invalid')
        if previous and previous.get('paragraph_id') != paragraphs[i-1]['paragraph_id']:
            errors.append('previous_paragraph_link_invalid')
        if following and following.get('paragraph_id') != paragraphs[i+1]['paragraph_id']:
            errors.append('next_paragraph_link_invalid')
        if following and following.get('handoff_slot') and following['handoff_slot'] not in required:
            errors.append('bridge_slot_not_supported_by_paragraph')
        if following and i+1 < len(paragraphs):
            next_paragraph = paragraphs[i+1]
            if following.get('handoff_slot') != (next_paragraph.get('previous_link') or {}).get('handoff_slot'):
                errors.append('adjacent_paragraph_bridge_mismatch')
            if following.get('handoff_slot') and following['handoff_slot'] not in next_paragraph.get('required_slots', []):
                errors.append('next_paragraph_does_not_receive_bridge')
    rubric = _read('writing_quality_rubric_v1.json')
    diagnostics = plan.get('quality_diagnostics', [])
    if (len(diagnostics) != len(rubric['criteria']) or
            {d.get('criterion_id') for d in diagnostics} != {c['id'] for c in rubric['criteria']}):
        errors.append('rubric_criteria_incomplete')
    if any(d.get('score') is not None or d.get('state') != 'not_evaluated' for d in diagnostics):
        errors.append('unperformed_quality_evaluation_assigned_score')
    checks = plan.get('required_checks', [])
    if (len(checks) != len(rubric['required_checks']) or
            {c.get('id') for c in checks} != {c['id'] for c in rubric['required_checks']} or
            any(c.get('state') != 'not_evaluated' for c in checks)):
        errors.append('unperformed_required_check_or_checklist_changed')
    if plan.get('draft_text') is not None or plan.get('is_finished_application') is not False:
        errors.append('plan_misrepresented_as_finished_draft')
    if plan.get('approved_pattern') is not False or plan.get('semantic_review') != 'not_performed':
        errors.append('unperformed_semantic_approval')
    synthetic = any(fact['source']['kind'] == 'synthetic_confirmed_fact' for fact in facts.values())
    if plan.get('input_synthetic') != synthetic or plan.get('ready_for_real_application') is not False:
        errors.append('synthetic_or_application_readiness_misrepresented')
    if plan.get('constraints') != (task_spec.get('constraints') or {}):
        errors.append('official_constraints_changed')
    expected_coverage = _declared_task_coverage(task_spec)
    if plan.get('task_coverage') != expected_coverage:
        errors.append('declared_task_coverage_changed')
    expected_conflicts = [] if nonpersuasive else _constraint_conflicts(
        task_spec, {'paragraphs': paragraphs}, plan.get('writing_basis', {}).get('style_profile') or {})
    if plan.get('constraint_conflicts') != expected_conflicts:
        errors.append('constraint_conflict_diagnostics_changed')
    if plan.get('status') == 'plan_ready' and (plan.get('need_info') or plan.get('constraint_conflicts')):
        errors.append('unresolved_plan_marked_ready')
    if not nonpersuasive:
        requested = {item['field'] for item in plan.get('need_info', [])}
        if not set(expected_coverage['declared_unfilled_aspects']) <= requested:
            errors.append('declared_task_aspect_not_requested')
        ref = task_spec.get('source_ref') or {}
        if (ref.get('status') != 'verified_current' or ref.get('publisher_role') != 'official_program') and 'current_task_source' not in requested:
            errors.append('unverified_official_task_not_requested')
        if not task_spec.get('question_text') and 'question_text' not in requested:
            errors.append('missing_question_not_requested')
        if (not task_spec.get('subject_id') or not task_spec.get('experience_id')) and 'target_identity' not in requested:
            errors.append('missing_identity_not_requested')
    return {'valid': not errors, 'errors': sorted(set(errors)),
            'ready_for_drafting': not errors and plan.get('status') == 'plan_ready',
            'ready_for_real_application': False,
            'full_task_coverage_confirmed': False,
            'quality_review': 'not_performed', 'approved_pattern': False}
