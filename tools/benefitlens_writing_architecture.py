"""Isolated, source-bound Korean writing architecture and deterministic preview.

No service writes, model calls, semantic judgments or submission verification.
"""
from copy import deepcopy
from datetime import datetime
from difflib import SequenceMatcher
from hashlib import sha256
import argparse
import json
from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO / 'pipeline/config/writing_architecture_contract_v1.json'


def fingerprint(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             allow_nan=False).encode()).hexdigest()


def load_contract():
    return json.loads(CONTRACT_PATH.read_text())


def resolve_genre_spec(task, contract=None):
    """Expand only explicitly declared research tasks, without inventing a pair."""
    contract = contract or load_contract()
    genre = contract['genre_aliases'].get(task.get('genre'), task.get('genre'))
    if genre not in contract['genres']:
        raise ValueError('Unsupported genre; request a genre-specific architecture')
    spec = deepcopy(contract['genres'][genre])
    block = spec.pop('iterative_block', None)
    if block:
        tasks = task.get('research_tasks', [])
        if not isinstance(tasks, list) or len(tasks) > 10:
            raise ValueError('research_tasks must be an explicit list of at most ten IDs')
        if any(not isinstance(t, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', t) for t in tasks) or len(tasks) != len(set(tasks)):
            raise ValueError('Research task IDs must be unique simple identifiers')
        expanded = []
        for tid in tasks:
            fields = {role: tid + '.' + role for role in block['field_kinds']}
            expanded.append({'section_id': tid, 'purpose': block['purpose'],
                             'opening': fields['question'], 'body': [fields['contribution']],
                             'closing': fields['boundary'], 'bridge': 'contribution_boundary_to_next_task_or_open_problem'})
            for role, field in fields.items():
                spec['field_kinds'][field] = block['field_kinds'][role]
                spec['field_roles_ko'][field] = block['field_roles_ko'][role]
                spec['relation_from_previous'][field] = block['relation_from_previous'][role]
        spec['sections'][1:1] = expanded
    return genre, spec


def _statement(fact):
    value = fact['value']
    if fact['field'] == 'coursework' and isinstance(value, bool):
        text = '저는 관련 정규 수업을 수료했습니다.' if value else '저는 관련 정규 수업을 수료하지 않았습니다.'
        as_of = fact.get('as_of') or {}
        return str(as_of['value']) + ' 기준, ' + text if as_of.get('status') == 'known' else text
    if not isinstance(value, str):
        raise ValueError('Narrative facts require string values, except coursework boolean')
    return value.strip() + ('' if value.strip().endswith(('.', '?', '!', '다')) else '.')


def _facts(task, facts, contract):
    accepted, held, seen = {}, [], set()
    allowed = task.get('allowed_experience_ids', [task['experience_id']] if task.get('experience_id') else [])
    if not isinstance(allowed, list) or any(not isinstance(x, str) or not x for x in allowed):
        raise ValueError('allowed_experience_ids must contain declared experience IDs')
    for raw in facts:
        f = deepcopy(raw)
        fid = f.get('fact_id')
        if not fid or fid in seen:
            raise ValueError('Missing or duplicate fact_id')
        seen.add(fid)
        source = f.get('source_ref') or {}
        reason = None
        if not task.get('subject_id') or not allowed:
            reason = 'target_identity_missing'
        elif f.get('subject_id') != task['subject_id']:
            reason = 'different_person'
        elif f.get('experience_id') not in allowed:
            reason = 'different_experience'
        elif f.get('status') != 'confirmed':
            reason = 'fact_not_confirmed'
        elif f.get('provenance') == 'public_source' or source.get('kind') not in contract['accepted_sources']:
            reason = 'not_personal_confirmed_source'
        elif source.get('owner_subject_id') != task['subject_id']:
            reason = 'source_owner_mismatch'
        elif not source.get('evidence_id') or not f.get('field'):
            reason = 'evidence_or_field_missing'
        elif f.get('value') is None or f.get('value') == '':
            reason = 'empty_evidence'
        elif f.get('kind') not in contract['fact_kinds']:
            reason = 'fact_kind_missing_or_unsupported'
        elif source.get('kind') == 'synthetic_confirmed_fact' and f.get('provenance') != 'synthetic_fixture':
            reason = 'synthetic_provenance_missing'
        if reason:
            held.append({'fact_id': fid, 'reason': reason})
        else:
            if f['kind'] == 'state':
                as_of = deepcopy(f.get('as_of') or {'status': 'unknown', 'value': None})
                if not isinstance(as_of, dict) or as_of.get('status') not in ('known', 'unknown'):
                    raise ValueError('State as_of requires known/unknown status')
                if as_of['status'] == 'known':
                    if not isinstance(as_of.get('value'), str):
                        raise ValueError('Known state as_of requires an explicit ISO date/time')
                    datetime.fromisoformat(as_of['value'])
                elif as_of.get('value') is not None:
                    raise ValueError('Unknown state as_of cannot contain an inferred date')
                f['as_of'] = as_of
            _statement(f)
            accepted[fid] = f
    conflicts = []
    exclusive = [f for f in accepted.values() if f['kind'] == 'state' and
                 (f['field'] in contract['exclusive_state_fields'] or f.get('exclusive_state') is True)]
    dated = [f for f in exclusive if f['as_of']['status'] == 'known']
    for experience, field, as_of in sorted({(f['experience_id'], f['field'], f['as_of']['value']) for f in dated}):
        group = [f for f in dated if f['field'] == field and f['experience_id'] == experience and f['as_of']['value'] == as_of]
        if len({fingerprint(f['value']) for f in group}) > 1:
            conflicts.append({'field': field, 'fact_ids': [f['fact_id'] for f in group],
                              'experience_id': experience, 'as_of': as_of, 'scope': 'exclusive_state_within_same_experience_and_as_of',
                              'reason': 'contradictory_confirmed_values'})
    return accepted, held, conflicts


def _select_facts(task, accepted, contradictions, optional_state_fields, relevant_fields):
    """Selection is explicit when a narrative slot has several personal facts."""
    questions, mapping = [], {}
    blocked_ids = {fid for row in contradictions for fid in row['fact_ids']}
    selected = task.get('selected_fact_ids')
    if selected is not None and (not isinstance(selected, list) or
            any(not isinstance(x, str) for x in selected) or len(selected) != len(set(selected))):
        raise ValueError('selected_fact_ids must be a list of unique fact IDs')
    if selected is not None:
        for fid in selected:
            if fid not in accepted:
                questions.append({'field': 'selected_fact_ids', 'fact_id': fid,
                                  'reason': 'selected_fact_unavailable', 'question': '선택한 본인 사실의 확인 근거와 경험 범위를 확인해 주세요.'})
    bindings = task.get('personal_fact_bindings') or {}
    if not isinstance(bindings, dict):
        raise ValueError('personal_fact_bindings must map a field to fact IDs')
    def writing_relevant(field):
        ids = {f['fact_id'] for f in accepted.values() if f['field'] == field}
        return (field not in optional_state_fields or field in relevant_fields or field in bindings
                or bool(ids.intersection(selected or [])))
    for field in sorted({f['field'] for f in accepted.values()}):
        if not writing_relevant(field):
            continue
        candidates = [f for f in accepted.values() if f['field'] == field and f['fact_id'] not in blocked_ids]
        explicit = field in bindings or selected is not None
        if field in bindings:
            ids = bindings[field] if isinstance(bindings[field], list) else [bindings[field]]
            if any(not isinstance(x, str) for x in ids) or len(ids) != len(set(ids)):
                raise ValueError('personal_fact_bindings must contain unique fact IDs')
            byid = {f['fact_id']: f for f in candidates}
            if any(fid not in byid for fid in ids):
                questions.append({'field': field, 'reason': 'personal_fact_binding_unavailable',
                                  'question': '해당 문장 역할과 선언한 경험 범위에 속하는 확인 사실 ID를 선택해 주세요.'})
            candidates = [byid[fid] for fid in ids if fid in byid]
        elif selected is not None:
            byid = {f['fact_id']: f for f in candidates}
            candidates = [byid[fid] for fid in selected if fid in byid]
        if len(candidates) > 1 and not explicit:
            questions.append({'field': field, 'fact_ids': [f['fact_id'] for f in candidates],
                              'reason': 'fact_selection_required_not_a_contradiction',
                              'question': '이 역할에 쓸 본인 사실을 selected_fact_ids 또는 personal_fact_bindings로 선택해 주세요.'})
            continue
        if candidates:
            mapping[field] = candidates
    for row in contradictions:
        questions.append({'field': row['field'], 'experience_id': row['experience_id'], 'reason': row['reason'],
                          'as_of': row['as_of'], 'scope': 'state_ledger', 'blocks_preview': writing_relevant(row['field']),
                          'question': row['field'] + '의 같은 경험 범위에 있는 상충 상태를 근거와 함께 확인해 주세요.'})
    selected_ids = {f['fact_id'] for values in mapping.values() for f in values}
    for f in accepted.values():
        if f['kind'] == 'state' and f['as_of']['status'] == 'unknown':
            questions.append({'field': f['field'], 'fact_id': f['fact_id'], 'scope': 'state_ledger',
                              'reason': 'state_as_of_unknown', 'blocks_preview': f['fact_id'] in selected_ids,
                              'question': '이 상태가 어느 시점의 사실인지 근거와 함께 확인해 주세요.'})
    return mapping, questions


def _constraints(task):
    constraints = deepcopy(task.get('constraints') or {})
    constraints.update(task.get('prompt_constraints') or {})
    constraints.update(task.get('official_constraints') or {})
    for key in ('max_words', 'max_characters', 'max_paragraphs', 'max_pages'):
        n = constraints.get(key)
        if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 1):
            raise ValueError(key + ' must be a positive integer')
    if constraints.get('character_counting', 'unicode_codepoints_including_spaces_and_newlines') != 'unicode_codepoints_including_spaces_and_newlines':
        raise ValueError('Unsupported character counting rule; request explicit counting specification')
    if constraints.get('word_counting', 'whitespace_delimited_tokens') != 'whitespace_delimited_tokens':
        raise ValueError('Unsupported word counting rule; request explicit counting specification')
    order = constraints.get('section_order', [])
    if not isinstance(order, list) or any(not isinstance(x, str) for x in order):
        raise ValueError('section_order must be a list of section IDs')
    return constraints


def _strategy(strategy, genre):
    if not strategy:
        return {'status': 'missing', 'observations': [], 'inferred_intent': None}, []
    genre_map = {'researchfit': 'research_fit', 'research_vision': 'research_fit',
                 'executionproposal': 'execution_proposal', 'development_proposal': 'execution_proposal',
                 'contribution/leadership': 'contribution_leadership',
                 'personal_contribution': 'contribution_leadership'}
    sg = genre_map.get(strategy.get('genre'), strategy.get('genre'))
    if genre == 'research_vision_iterative' and strategy.get('genre') == 'research_vision':
        sg = genre
    requests = []
    if sg != genre:
        requests.append({'to': 'T2', 'reason': 'strategy_genre_mismatch',
                         'question': '공식 문항 장르에 맞는 섹션 전략으로 재구성해 주세요.'})
    obs = []
    for s in strategy.get('sections', []):
        row = {k: deepcopy(s.get(k)) for k in ('id', 'order', 'purpose', 'evidence_span')}
        row['observed_edge_ref'] = {'strategy_card_id': strategy.get('id'), 'section_id': s.get('id')}
        obs.append(row)
    transfer = strategy.get('target_transfer') or {}
    if transfer.get('status') != 'hypothesis' or not transfer.get('section_mapping'):
        requests.append({'to': 'T2', 'reason': 'source_strategy_transfer_mapping_missing',
                         'question': '공개 원문의 각 섹션을 목표 문항의 어느 섹션에 전이하거나 제외할지 가설과 이유를 명시해 주세요.'})
    return {'status': 'source_strategy_retained_transfer_hypothesis',
            'strategy_card_id': strategy.get('id'),
            'strategy_card_path': strategy.get('source_card_path'),
            'strategy_card_origin_path': strategy.get('source_card_origin_path'),
            'strategy_card_file_sha256': strategy.get('source_card_file_sha256'),
            'source_provenance': strategy.get('provenance', 'public_source'),
            'source_document_id': strategy.get('source_document_id'),
            'source_sha256': strategy.get('sha256') or strategy.get('source_sha256'),
            'program_id': strategy.get('program_id'), 'round_id': strategy.get('round_id', 'unknown'),
            'original_prompt_id': strategy.get('original_prompt_id', 'unknown'),
            'submission_status': strategy.get('submission_status', 'unknown'),
            'source_language': strategy.get('source_language'),
            'observations': obs, 'selected_materials': deepcopy(strategy.get('selected_materials', [])),
            'inferred_intent': {'value': deepcopy(strategy.get('inferred_intent')), 'status': 'hypothesis'},
            'target_transfer': deepcopy(transfer),
            'target_transfer_status': strategy.get('target_transfer_status', 'unknown'),
            'personal_fact_binding_policy': 'source_selected_materials_are_not_personal_facts'}, requests


def compile_architecture(task, confirmed_facts, strategy_card=None, style_profile=None):
    """Compile literal personal fact statements into an inspectable architecture.

    The selected genre supplies an editable proposal. T2 source observations are
    retained as hypotheses and cannot bind personal fact slots.
    """
    task, facts = deepcopy(task), deepcopy(confirmed_facts)
    contract = load_contract()
    genre, spec = resolve_genre_spec(task, contract)
    accepted, held, contradictions = _facts(task, facts, contract)
    constraints = _constraints(task)
    strategy, requests = _strategy(strategy_card, genre)
    optional_states = {f['field'] for f in accepted.values() if f['kind'] == 'state' and f['field'] in spec['optional_fields']}
    relevant = set(constraints.get('required_fact_fields', [])) | set(constraints.get('relevant_fact_fields', []))
    fieldmap, need_info = _select_facts(task, accepted, contradictions, optional_states, relevant)
    if genre == 'research_vision_iterative' and not task.get('research_tasks'):
        need_info.append({'field': 'research_tasks', 'reason': 'explicit_research_tasks_missing',
                          'question': '이번 글에서 다룰 연구 과제를 확인해 주세요. 과제 수를 추정하지 않습니다.'})
    questioned_fields = {row['field'] for row in need_info}
    order = constraints.get('section_order', [])
    ids = [s['section_id'] for s in spec['sections']]
    mapping = strategy.get('target_transfer', {}).get('section_mapping', [])
    if mapping:
        source_ids = {s['id'] for s in strategy.get('observations', [])}
        mapped_sources = {m.get('source_section_id') for m in mapping}
        mapped_targets = {m.get('target_section_id') for m in mapping if m.get('target_section_id')}
        if source_ids != mapped_sources or not mapped_targets.issubset(set(ids)):
            requests.append({'to': 'T2', 'reason': 'invalid_source_target_section_mapping',
                             'question': '빠진 원문 섹션과 잘못된 목표 섹션 참조를 수정해 주세요.'})
        target_order = list(dict.fromkeys(m['target_section_id'] for m in mapping if m.get('target_section_id')))
        if not order and set(target_order) == set(ids):
            spec['sections'].sort(key=lambda s: target_order.index(s['section_id']))
    if order:
        if len(order) != len(set(order)) or set(order) != set(ids):
            requests.append({'to': 'T2', 'reason': 'official_order_requires_reconstruction',
                             'question': '공식 섹션 순서와 요구 항목에 맞게 전략카드를 재구성해 주세요.'})
        else:
            spec['sections'].sort(key=lambda s: order.index(s['section_id']))
            for section in spec['sections']:
                section['bridge'] = 'explicit_official_sequence_relation_unassessed'
    paragraphs, sentence_relations = [], []
    previous_sentence = None
    for n, section in enumerate(spec['sections'], 1):
        pid = 'p' + str(n)
        sentences = []
        for phase, fields in [('opening', [section['opening']]), ('body', section['body']),
                              ('closing', [section['closing']])]:
            for field in fields:
                selected_facts = fieldmap.get(field, [])
                if not selected_facts:
                    if field not in spec['optional_fields'] and field not in questioned_fields:
                        need_info.append({'field': field, 'reason': 'confirmed_fact_missing',
                                          'question': field + '에 해당하는 본인의 확인 사실 또는 명시적 계획과 근거를 알려 주세요.'})
                    continue
                for index, fact in enumerate(selected_facts):
                    if fact['kind'] == 'state' and fact['as_of']['status'] == 'unknown':
                        continue
                    if fact['kind'] not in spec['field_kinds'][field]:
                        need_info.append({'field': field, 'fact_id': fact['fact_id'], 'reason': 'fact_kind_conflicts_with_sentence_role',
                                          'question': '이미 수행한 일과 결과, 향후 계획을 구분해 ' + field + '를 확인해 주세요.'})
                        continue
                    contribution = genre == 'research_vision_iterative' and field.endswith('.contribution')
                    if contribution and fact.get('activity_status') not in {
                            'performed_action': ('completed', 'ongoing'), 'result': ('completed',),
                            'future_plan': ('planned',)}[fact['kind']]:
                        need_info.append({'field': field, 'fact_id': fact['fact_id'], 'reason': 'contribution_activity_status_missing_or_conflicting',
                                          'question': '본인 기여가 완료, 진행, 계획 중 어느 상태인지 근거와 함께 확인해 주세요.'})
                        continue
                    actual_phase = phase
                    if (phase == 'opening' and index > 0) or (phase == 'closing' and index < len(selected_facts)-1):
                        actual_phase = 'body'
                    sid = pid + '-s' + str(len(sentences) + 1)
                    purpose = spec['field_roles_ko'][field]
                    relation = {'from_sentence_id': previous_sentence,
                                'relation': 'start' if previous_sentence is None else spec['relation_from_previous'][field],
                                'relation_basis': 'genre_slot_sequence_hypothesis', 'semantic_validity': 'unknown'}
                    text = _statement(fact)
                    if contribution:
                        text = {'completed': '완료한 기여: ', 'ongoing': '진행 중인 기여: ', 'planned': '계획한 기여: '}[fact['activity_status']] + text
                    sentences.append({'sentence_id': sid, 'phase': actual_phase, 'role_field': field, 'purpose_ko': purpose,
                                      'text': text, 'fact_ids': [fact['fact_id']], 'experience_id': fact['experience_id'],
                                      'fact_kind': fact['kind'], 'inference_status': 'literal_input_statement'})
                    if contribution:
                        sentences[-1]['activity_status'] = fact['activity_status']
                        sentences[-1]['inference_status'] = 'literal_statement_with_declared_activity_label'
                    sentence_relations.append({'sentence_id': sid, 'paragraph_id': pid, 'role_field': field,
                                               'purpose_ko': purpose, 'relation_to_previous': relation})
                    previous_sentence = sid
        for required_phase in ('opening', 'body', 'closing'):
            if not any(s['phase'] == required_phase for s in sentences):
                requests.append({'to': 'T2', 'reason': 'paragraph_role_unfilled', 'paragraph_id': pid,
                                 'phase': required_phase, 'question': '문단의 ' + required_phase + ' 역할을 채울 확인 사실을 확보하거나 구조를 재구성해 주세요.'})
        paragraphs.append({'paragraph_id': pid, 'section_id': section['section_id'],
                           'purpose': section['purpose'], 'opening_intent': spec['field_roles_ko'][section['opening']],
                           'body_evidence_ids': [fid for s in sentences if s['phase'] == 'body' for fid in s['fact_ids']],
                           'reasoning_link': {'relation': section.get('bridge'), 'status': 'hypothesis',
                                              'semantic_validity': 'unknown', 'inference_text': None},
                           'closing_intent': spec['field_roles_ko'][section['closing']],
                           'sentences': sentences, 'next_paragraph_id': None, 'bridge_relation': None})
    short = constraints.get('answer_format') in ('single_paragraph', 'single_sentence', 'short_answer') or constraints.get('max_paragraphs') == 1
    if short:
        sentences = [s for p in paragraphs for s in p['sentences']]
        for row in sentence_relations:
            row['paragraph_id'] = 'p1'
        paragraphs = [{'paragraph_id': 'p1', 'section_id': 'short_answer', 'purpose': '공식 짧은 답변 형식 안에서 문항에 답한다',
                       'opening_intent': sentences[0]['purpose_ko'] if sentences else '질문의 대상을 제시한다', 'body_evidence_ids': [f for s in sentences for f in s['fact_ids']],
                       'reasoning_link': {'relation': 'compressed_genre_sequence', 'status': 'hypothesis', 'semantic_validity': 'unknown', 'inference_text': None},
                       'closing_intent': sentences[-1]['purpose_ko'] if sentences else '문항이 요구한 답의 범위에서 닫는다', 'sentences': sentences,
                       'next_paragraph_id': None, 'bridge_relation': None}]
    else:
        for left, right in zip(paragraphs, paragraphs[1:]):
            left['next_paragraph_id'] = right['paragraph_id']
            endings = [s for s in left['sentences'] if s['phase'] == 'closing']
            openings = [s for s in right['sentences'] if s['phase'] == 'opening']
            if not endings or not openings:
                left['bridge_status'] = 'unfilled_endpoint_needs_info'
                continue
            left['bridge_relation'] = {'from_sentence_id': endings[-1]['sentence_id'],
                                      'to_sentence_id': openings[0]['sentence_id'],
                                      'relation': left['reasoning_link']['relation'], 'structural_reference': 'declared',
                                      'semantic_validity': 'unknown'}
    preview = '\n\n'.join(' '.join(s['text'] for s in p['sentences']) for p in paragraphs)
    if constraints.get('answer_format') == 'single_sentence' and sum(len(p['sentences']) for p in paragraphs) != 1:
        requests.append({'to': 'T2', 'reason': 'single_sentence_requires_reconstruction', 'question': '한 문장에 필요한 주장과 사실 하나로 전략을 재구성해 주세요.'})
    metrics = {'characters': len(preview), 'words': len(preview.split()), 'paragraphs': len(paragraphs),
               'word_counting': 'whitespace_delimited_tokens',
               'character_counting': 'unicode_codepoints_including_spaces_and_newlines'}
    for key, actual in [('max_characters', metrics['characters']), ('max_words', metrics['words']), ('max_paragraphs', metrics['paragraphs'])]:
        if constraints.get(key) is not None and actual > constraints[key]:
            requests.append({'to': 'T2', 'reason': 'explicit_limit_exceeded', 'constraint': key,
                             'actual': actual, 'maximum': constraints[key],
                             'question': '공식 분량 안에 들어가도록 주장과 재료를 줄여 전략을 재구성해 주세요.'})
    if task.get('source_ref', {}).get('status') != 'verified_current':
        need_info.append({'field': 'official_source', 'reason': 'current_official_source_not_verified',
                          'question': '현재 회차의 공식 문항과 형식 출처를 확인해 주세요.'})
    for field in constraints.get('required_fact_fields', []):
        if field not in fieldmap:
            need_info.append({'field': field, 'reason': 'explicit_required_fact_missing',
                              'question': '명시 문항의 ' + field + '에 답할 본인 사실과 근거를 알려 주세요.'})
    for request in task.get('selection_evidence_requests', []):
        if request.get('field') not in fieldmap:
            need_info.append({'field': request.get('field'), 'reason': 'selection_criterion_evidence_not_provided',
                              'mandatory': False, 'criterion_ref': request.get('criterion_ref'),
                              'question': request.get('question'), 'eligibility_judgment': 'not_performed'})
    constraint_checks = [{'constraint': k, 'status': 'checked_literal_unit', 'actual': metrics[v],
                          'maximum': constraints[k]} for k, v in [('max_characters', 'characters'), ('max_words', 'words'), ('max_paragraphs', 'paragraphs')] if k in constraints]
    if 'max_pages' in constraints:
        constraint_checks.append({'constraint': 'max_pages', 'maximum': constraints['max_pages'],
                                  'status': 'unknown_requires_rendered_layout', 'actual': None})
        need_info.append({'field': 'rendered_page_count', 'reason': 'page_limit_requires_layout_check',
                          'question': '공식 양식으로 렌더링한 뒤 참고문헌 제외 등 원문 규칙에 따라 페이지 수를 확인해 주세요.'})
    sections = [{'section_id': p['section_id'], 'order': i, 'purpose': p['purpose'], 'paragraphs': [p]}
                for i, p in enumerate(paragraphs, 1)]
    for section in sections:
        section['strategy_transfer_hypotheses'] = mapping if short else [m for m in mapping if m.get('target_section_id') == section['section_id']]
    incomplete = any(x.get('blocks_preview', True) and x['field'] not in ('official_source', 'rendered_page_count') for x in need_info)
    draft_mode = constraints.get('draft_mode', task.get('draft_mode', 'preview'))
    review_only = draft_mode in ('review_only', 'outline_review_only')
    synthetic_production = task.get('execution_mode') == 'production' and (
        task.get('provenance') == 'synthetic_fixture' or task.get('source_ref', {}).get('status') == 'synthetic_fixture' or any(
            f.get('provenance') == 'synthetic_fixture' or f.get('source_ref', {}).get('kind') == 'synthetic_confirmed_fact'
            for f in facts))
    if review_only or synthetic_production:
        for section in sections:
            for p in section['paragraphs']:
                for sentence in p['sentences']:
                    sentence['text'] = None
                    sentence['inference_status'] = 'blocked_synthetic_production' if synthetic_production else 'bound_fact_reference_only_review_mode'
    preview_status = 'blocked_synthetic_production' if synthetic_production else 'disabled_by_explicit_review_only_policy' if review_only else 'blocked' if incomplete or requests else 'synthetic_or_unverified_preview'
    return {'sentence_purpose_relations_ko': sentence_relations,
            'version': contract['version'], 'input_fingerprint': fingerprint([task, facts, strategy_card, style_profile]),
            'genre': genre, 'status': 'blocked_synthetic_production' if synthetic_production else 'needs_reconstruction' if requests else 'need_info' if need_info else 'architecture_ready_for_review',
            'sections': sections, 'effective_constraints': constraints,
            'constraint_checks': constraint_checks,
            'layout_verification': {'status': 'not_performed', 'max_pages': constraints.get('max_pages')},
            'official_textbox_prompt_status': task.get('official_textbox_prompt_status', 'unknown'),
            'official_source_trace': deepcopy(task.get('source_ref') or {}),
            'official_condition_refs': deepcopy(task.get('official_condition_refs') or []),
            'selection_evidence_requests': deepcopy(task.get('selection_evidence_requests') or []),
            'constraint_precedence': contract['precedence'], 'style_profile': deepcopy(style_profile or {}),
            'style_application': 'defaults_only_explicit_constraints_take_precedence',
            'accepted_fact_ids': sorted(accepted), 'held_facts': held, 'contradictions': contradictions,
            'declared_experience_ids': task.get('allowed_experience_ids', [task.get('experience_id')]),
            'selected_personal_fact_ids': [f['fact_id'] for values in fieldmap.values() for f in values],
            'preserved_explicit_states': [{'fact_id': f['fact_id'], 'experience_id': f['experience_id'], 'field': f['field'], 'value': f['value']}
                                          for f in accepted.values() if isinstance(f['value'], bool)],
            'state_ledger': [{'fact_id': f['fact_id'], 'subject_id': f['subject_id'], 'experience_id': f['experience_id'],
                              'field': f['field'], 'value': f['value'], 'as_of': deepcopy(f['as_of']),
                              'source_ref': deepcopy(f['source_ref']),
                              'selected_for_text': f['fact_id'] in {v['fact_id'] for values in fieldmap.values() for v in values}}
                             for f in accepted.values() if f['kind'] == 'state'],
            'strategy_trace': strategy, 'need_info': need_info, 'reconstruction_requests': requests,
            'preview_status': preview_status, 'draft_text_preview': None if synthetic_production or review_only or incomplete or requests else preview,
            'preview_metrics': metrics, 'draft_mode': draft_mode,
            'provenance': 'synthetic_fixture' if any(f.get('provenance') == 'synthetic_fixture' for f in accepted.values()) else 'personal_confirmed_input',
            'source_submission_identity': 'unknown', 'ready_for_real_submission': False,
            'semantic_validity': 'unknown', 'persuasiveness_effect': 'unmeasured',
            'execution': {'generator': 'deterministic_python_no_model_call', 'model_identifier': None, 'model_calls': 0}}


def validate_architecture(result, task, facts, strategy_card=None, style_profile=None):
    expected = compile_architecture(task, facts, strategy_card, style_profile)
    errors = []
    pids = {p['paragraph_id'] for s in result.get('sections', []) for p in s['paragraphs']}
    sids = {x['sentence_id'] for s in result.get('sections', []) for p in s['paragraphs'] for x in p['sentences']}
    for section in result.get('sections', []):
        for p in section['paragraphs']:
            b = p.get('bridge_relation')
            if p.get('next_paragraph_id') and p['next_paragraph_id'] not in pids:
                errors.append('dangling_paragraph_link')
            if b and (b.get('from_sentence_id') not in sids or b.get('to_sentence_id') not in sids):
                errors.append('dangling_sentence_bridge')
    if result != expected:
        errors.append('deterministic_contract_or_fact_trace_changed')
    return {'valid': not errors, 'errors': sorted(set(errors)), 'scope': 'structure_and_literal_fact_preservation',
            'semantic_validity': 'unknown', 'persuasiveness_effect': 'unmeasured'}


def audit_long_matches(text, sources, minimum_characters=40):
    """Return long literal-match candidates, not a plagiarism decision."""
    if isinstance(minimum_characters, bool) or minimum_characters < 20:
        raise ValueError('minimum_characters must be at least 20')
    normalized = re.sub(r'\s+', ' ', text or '').strip()
    candidates = []
    for source in sources:
        body = re.sub(r'\s+', ' ', source['text']).strip()
        for block in SequenceMatcher(None, normalized, body, autojunk=False).get_matching_blocks():
            if block.size >= minimum_characters:
                candidates.append({'source_document_id': source['source_document_id'],
                                   'source_sha256': sha256(source['text'].encode()).hexdigest(),
                                   'output_offset': block.a, 'source_offset': block.b,
                                   'normalized_match_characters': block.size})
    return {'method': 'whitespace_normalized_long_literal_match', 'minimum_characters': minimum_characters,
            'candidates': candidates, 'interpretation': 'candidates_require_review_zero_does_not_prove_no_plagiarism',
            'limitations': ['does_not_detect_translation_or_semantic_paraphrase', 'common_phrases_and_credited_quotations_require_review']}


def write_bundle(result, out_dir):
    """Split JSON artifacts so each written document stays below 10000 chars."""
    path = Path(out_dir)
    path.mkdir(parents=True, exist_ok=True)
    pieces = []
    summary = {k: v for k, v in result.items() if k not in ('sections', 'sentence_purpose_relations_ko', 'state_ledger')}
    values = [('summary.json', summary), ('state-ledger.json', result['state_ledger']),
              ('sentence-purposes.json', result['sentence_purpose_relations_ko'])]
    values += [('section-' + str(i) + '.json', s) for i, s in enumerate(result['sections'], 1)]
    for name, value in values:
        text = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
        if len(text) > 10000:
            raise ValueError(name + ' exceeds document size; reduce strategy or split before export')
        (path / name).write_text(text)
        pieces.append({'path': name, 'characters': len(text), 'sha256': sha256(text.encode()).hexdigest()})
    manifest = {'version': result['version'], 'input_fingerprint': result['input_fingerprint'],
                'pieces': pieces, 'reconstruction': 'summary plus state-ledger, sections in numeric order and sentence-purposes'}
    (path / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest


def load_case(input_path):
    path = Path(input_path)
    case = json.loads(path.read_text())
    if case.get('source_strategy_path'):
        adapter_path = Path(case['source_strategy_path'])
        if not adapter_path.is_absolute():
            adapter_path = path.parent / adapter_path
        adapter = json.loads(adapter_path.read_text())
        card_path = Path(adapter['source_card_path'])
        cards = json.loads(card_path.read_text())['strategy_cards']
        card = deepcopy(next(c for c in cards if c['id'] == adapter['strategy_card_id']))
        card['target_transfer'] = adapter['target_transfer']
        card['source_card_path'] = str(card_path)
        card['source_card_origin_path'] = adapter.get('source_card_origin_path', str(card_path))
        card['source_card_file_sha256'] = sha256(card_path.read_bytes()).hexdigest()
        case['source_strategy'] = card
    return case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    case = load_case(args.input)
    result = compile_architecture(case['task'], case['confirmed_facts'], case.get('source_strategy'), case.get('style_profile'))
    receipt = validate_architecture(result, case['task'], case['confirmed_facts'], case.get('source_strategy'), case.get('style_profile'))
    manifest = write_bundle(result, args.output_dir)
    print(json.dumps({'manifest': str(args.output_dir / 'manifest.json'), 'validation': receipt,
                      'pieces': len(manifest['pieces'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
