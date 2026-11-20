"""Validate proposed personal atoms and bind explicit material/role proposals.

Candidate extraction, relevance judgments and confirmation authentication are
outside this standalone adapter. No model, network, database or service writes.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import argparse
import json
import math
from pathlib import Path

from benefitlens_writing_architecture import (
    compile_architecture, fingerprint, load_case, resolve_genre_spec, write_bundle,
)

CONFIG_PATH = Path(__file__).resolve().parents[1] / 'pipeline/config/writing_material_bridge_v1.json'


def load_config():
    return json.loads(CONFIG_PATH.read_text())


def text_hash(text):
    return sha256(text.encode()).hexdigest()


def _index(rows, key):
    result = {}
    for row in rows:
        identity = row.get(key)
        if not isinstance(identity, str) or not identity or identity in result:
            raise ValueError('Missing or duplicate ' + key)
        result[identity] = row
    return result


def _atom_value(atom, quote):
    """Keep unknown distinct from false; numeric units never become booleans."""
    value, value_type = atom.get('value'), atom.get('value_type', 'text')
    if value is None:
        return None, 'unknown_value_requires_confirmation'
    if value_type == 'text':
        if not isinstance(value, str) or not value.strip() or value != quote:
            return None, 'text_value_must_equal_evidence_quote'
    elif value_type == 'boolean':
        if type(value) is not bool or atom.get('semantic_key') != 'coursework':
            return None, 'boolean_requires_literal_bool_and_coursework_key'
    elif value_type in ('quantity', 'score'):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None, 'numeric_value_invalid'
        if not isinstance(atom.get('unit'), str) or not atom['unit'].strip():
            return None, 'numeric_unit_missing'
        if value_type == 'score':
            maximum = atom.get('maximum')
            if isinstance(maximum, bool) or not isinstance(maximum, (int, float)) or not math.isfinite(maximum) or maximum <= 0 or not 0 <= value <= maximum:
                return None, 'score_maximum_missing_or_invalid'
        return quote, None
    else:
        return None, 'unsupported_value_type'
    return value, None


def confirm_atoms(payload):
    """Validate declared evidence and input confirmation records, not truth."""
    subject = payload['task'].get('subject_id')
    sources = _index(payload.get('raw_experiences', []), 'experience_id')
    atoms = _index(payload.get('proposed_atoms', []), 'atom_id')
    events = payload.get('confirmation_events', [])
    _index(events, 'event_id')
    facts, held = [], []
    config = load_config()
    allowed = payload['task'].get('allowed_experience_ids', [payload['task'].get('experience_id')])
    for aid, atom in atoms.items():
        source = sources.get(atom.get('experience_id')) or {}
        ref, span = source.get('source_ref') or {}, atom.get('source_span') or {}
        body = source.get('text')
        reason, value = None, None
        if not subject or source.get('subject_id') != subject or atom.get('subject_id') != subject:
            reason = 'different_or_missing_person'
        elif source.get('experience_id') not in allowed:
            reason = 'undeclared_experience'
        elif (source.get('provenance') == 'public_source' or ref.get('kind') not in config['personal_source_kinds'] or
              (isinstance(body, str) and text_hash(body) in config.get('known_public_source_hashes', {}))):
            reason = 'public_or_unsupported_source_cannot_be_personal'
        elif ref.get('owner_subject_id') != subject or not ref.get('evidence_id'):
            reason = 'source_owner_or_evidence_missing'
        elif not isinstance(body, str) or not body:
            reason = 'source_text_missing'
        elif atom.get('source_sha256') != text_hash(body):
            reason = 'source_hash_mismatch'
        elif (type(span.get('start')) is not int or type(span.get('end')) is not int or
              not 0 <= span['start'] < span['end'] <= len(body) or
              span.get('quote') != body[span['start']:span['end']]):
            reason = 'invalid_source_span'
        elif atom.get('kind') not in config['activity_status_by_kind']:
            reason = 'unsupported_fact_kind'
        elif atom.get('activity_status') not in config['activity_status_by_kind'][atom['kind']]:
            reason = 'activity_status_conflicts_with_fact_kind'
        else:
            value, reason = _atom_value(atom, span['quote'])
        if not reason and atom['kind'] == 'state':
            as_of = atom.get('as_of') or {'status': 'unknown', 'value': None}
            if (not isinstance(as_of, dict) or as_of.get('status') not in ('known', 'unknown') or
                    (as_of['status'] == 'unknown' and as_of.get('value') is not None)):
                reason = 'state_as_of_invalid'
            elif as_of['status'] == 'known':
                try:
                    datetime.fromisoformat(as_of['value'])
                except (KeyError, TypeError, ValueError):
                    reason = 'state_as_of_invalid'
        matching = [e for e in events if e.get('atom_id') == aid]
        event = matching[0] if len(matching) == 1 else {}
        if not reason and (len(matching) != 1 or event.get('decision') != 'confirm' or
                           event.get('confirmer_subject_id') != subject or
                           event.get('atom_fingerprint') != fingerprint(atom)):
            reason = 'confirmation_event_missing_ambiguous_or_unbound'
        if not reason:
            try:
                datetime.fromisoformat(event['confirmed_at'])
            except (KeyError, TypeError, ValueError):
                reason = 'confirmation_time_missing_or_invalid'
        if reason:
            held.append({'atom_id': aid, 'reason': reason})
            continue
        synthetic = (ref['kind'] == 'synthetic_fixture' or source.get('provenance') == 'synthetic_fixture' or
                     atom.get('provenance') == 'synthetic_fixture' or event.get('provenance') == 'synthetic_fixture' or
                     payload.get('provenance') == 'synthetic_fixture' or payload['task'].get('provenance') == 'synthetic_fixture' or
                     payload['task'].get('source_ref', {}).get('status') == 'synthetic_fixture')
        if ref['kind'] == 'synthetic_fixture' and source.get('provenance') != 'synthetic_fixture':
            held.append({'atom_id': aid, 'reason': 'synthetic_provenance_missing'})
            continue
        facts.append({'fact_id': 'F:' + aid, 'atom_id': aid, 'subject_id': subject,
                      'experience_id': source['experience_id'], 'field': atom.get('semantic_key', 'unassigned'),
                      'value': value, 'typed_value': {k: deepcopy(atom.get(k)) for k in ('value_type', 'value', 'unit', 'maximum')},
                      'kind': atom['kind'], 'activity_status': atom['activity_status'], 'status': 'confirmed',
                      'as_of': deepcopy(atom.get('as_of') or {'status': 'unknown', 'value': None}),
                      'provenance': 'synthetic_fixture' if synthetic else 'personal_confirmed_input',
                      'source_ref': {'kind': 'synthetic_confirmed_fact' if synthetic else ref['kind'],
                                     'owner_subject_id': subject, 'evidence_id': ref['evidence_id'],
                                     'source_sha256': atom['source_sha256'], 'source_span': deepcopy(span)},
                      'confirmation_event_id': event['event_id'],
                      'confirmation_basis': 'input_event_not_authenticated_by_standalone_adapter'})
    return facts, held


def select_materials(payload, facts):
    """Rank caller-proposed relevance; never infer relevance from raw text."""
    byid = {f['fact_id']: f for f in facts}
    requirements = _index(payload['task'].get('requirements', []), 'id')
    proposals = _index(payload.get('material_proposals', []), 'fact_id')
    selected, excluded, questions = [], [], []
    for fid, row in proposals.items():
        links = row.get('requirement_ids', [])
        if (fid not in byid or not isinstance(links, list) or
                any(x not in requirements for x in links) or not row.get('reason') or
                row.get('decision') not in ('include', 'exclude')):
            questions.append({'fact_id': fid, 'reason': 'material_proposal_invalid_or_unconfirmed'})
            continue
        if row['decision'] == 'exclude':
            excluded.append({'fact_id': fid, 'reason': row['reason'], 'decision_by': 'caller'})
        else:
            selected.append({'fact_id': fid, 'requirement_ids': links, 'reason': row['reason'],
                             'required_links': sum(requirements[x].get('required') is True for x in set(links)),
                             'relevance_status': 'caller_proposal_not_semantically_verified'})
    selected.sort(key=lambda r: (-r['required_links'], -len(set(r['requirement_ids']))))
    covered = {x for row in selected for x in row['requirement_ids']}
    for rid, req in requirements.items():
        if req.get('required') is True and rid not in covered:
            questions.append({'requirement_id': rid, 'reason': 'declared_required_material_missing'})
    for fid in byid.keys() - proposals.keys():
        excluded.append({'fact_id': fid, 'reason': 'not_proposed_for_this_prompt', 'decision_by': 'default_selection_policy'})
    return {'selected_fact_ids': [r['fact_id'] for r in selected], 'ranked_materials': selected,
            'excluded': sorted(excluded, key=lambda x: x['fact_id']), 'missing_questions': questions,
            'coverage_scope': 'caller_declared_requirement_links_not_semantic_coverage',
            'deletion_authority': 'caller_revises_selection_on_length_failure_no_automatic_fact_deletion',
            'selection_status': 'proposed_for_review'}, questions


def bind_roles(payload, facts, selection, spec):
    byid = {f['fact_id']: deepcopy(f) for f in facts}
    selected = set(selection['selected_fact_ids'])
    seen, bindings, questions = set(), {}, []
    for row in payload.get('role_bindings', []):
        role, ids = row.get('role'), row.get('fact_ids')
        if role not in spec['field_kinds'] or role in bindings or not isinstance(ids, list) or not ids or not row.get('reason'):
            questions.append({'role': role, 'reason': 'role_proposal_invalid'})
            continue
        bindings[role] = []
        for fid in ids:
            if fid not in selected or fid in seen:
                questions.append({'role': role, 'fact_id': fid, 'reason': 'role_fact_unselected_or_reused'})
                continue
            fact = byid[fid]
            if fact['kind'] not in spec['field_kinds'][role] or (type(fact['value']) is bool and role != 'coursework'):
                questions.append({'role': role, 'fact_id': fid, 'reason': 'fact_kind_conflicts_with_sentence_role'})
                continue
            fact['field'] = role
            seen.add(fid)
            bindings[role].append(fid)
    for fid in selected - seen:
        questions.append({'fact_id': fid, 'reason': 'selected_material_role_missing'})
    return list(byid.values()), bindings, questions


def bridge_materials(payload):
    payload = deepcopy(payload)
    mode = payload.get('execution_mode', 'preview')
    if mode not in ('preview', 'production'):
        raise ValueError('execution_mode must be preview or production')
    result = {'version': load_config()['version'], 'input_fingerprint': fingerprint(payload),
              'execution_mode': mode, 'ready_for_real_submission': False,
              'automatic_stages': {'candidate_atoms': 'caller_supplied_not_extracted',
                                   'confirmation': 'mechanical_evidence_and_event_checks_input_events_not_authenticated',
                                   'material_relevance': 'caller_proposed_then_deterministically_ranked',
                                   'role_assignment': 'caller_proposed_then_validated_and_bound',
                                   'preview': 'existing_deterministic_architecture_no_model_call'},
              'model_calls': 0, 'semantic_validity': 'unknown'}
    marked_atoms_or_events = any(row.get('provenance') == 'synthetic_fixture'
                                 for key in ('proposed_atoms', 'confirmation_events') for row in payload.get(key, []))
    if mode == 'production' and (payload.get('provenance') == 'synthetic_fixture' or marked_atoms_or_events or
            payload['task'].get('provenance') == 'synthetic_fixture' or
            payload['task'].get('source_ref', {}).get('status') == 'synthetic_fixture' or any(
            s.get('provenance') == 'synthetic_fixture' or s.get('source_ref', {}).get('kind') == 'synthetic_fixture'
            for s in payload.get('raw_experiences', []))):
        return dict(result, status='blocked_synthetic_production', confirmed_facts=[], held_atoms=[],
                    material_selection=None, role_binding_trace=[], architecture=None,
                    draft_text_preview=None, missing_questions=[{'reason': 'synthetic_production_forbidden'}])
    try:
        genre, spec = resolve_genre_spec(payload['task'])
    except ValueError as error:
        return dict(result, status='unsupported_genre_or_structure', confirmed_facts=[], held_atoms=[],
                    material_selection=None, role_binding_trace=[], architecture=None,
                    draft_text_preview=None, missing_questions=[{'reason': 'unsupported_genre_or_structure', 'detail': str(error)}])
    facts, held = confirm_atoms(payload)
    selection, questions = select_materials(payload, facts)
    bound, bindings, role_questions = bind_roles(payload, facts, selection, spec)
    questions = questions + role_questions
    task = deepcopy(payload['task'])
    task.update(execution_mode=mode, selected_fact_ids=selection['selected_fact_ids'], personal_fact_bindings=bindings)
    architecture = compile_architecture(task, bound, payload.get('source_strategy'), payload.get('style_profile'))
    if questions:
        architecture['draft_text_preview'] = None
        architecture['preview_status'] = 'blocked_by_material_bridge_questions'
    return dict(result, status='preview_ready' if architecture['draft_text_preview'] else 'need_info', genre=genre,
                confirmed_facts=facts, held_atoms=held, material_selection=selection,
                role_binding_trace=deepcopy(payload.get('role_bindings', [])), compiler_task=task,
                compiler_facts=bound, architecture=architecture,
                draft_text_preview=architecture['draft_text_preview'],
                missing_questions=questions + architecture['need_info'] + architecture['reconstruction_requests'])


def write_bridge_bundle(result, out_dir):
    """Write inspectable pieces; no JSON document exceeds 10000 characters."""
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    pieces = []
    def write(name, value):
        encoded = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
        if len(encoded) > 9500:
            if isinstance(value, list) and len(value) > 1:
                half = len(value) // 2
                write(name + '-a', value[:half]); write(name + '-b', value[half:])
                return
            if isinstance(value, dict) and len(value) > 1:
                for key, child in value.items():
                    write(name + '-' + key, child)
                return
            raise ValueError('Artifact too large to split: ' + name)
        path = root / (name + '.json')
        path.write_text(encoded)
        pieces.append({'path': path.name, 'sha256': text_hash(encoded), 'characters': len(encoded)})
    for key, value in result.items():
        if key != 'architecture':
            write(key, value)
    if result.get('architecture'):
        write_bundle(result['architecture'], root / 'architecture')
    manifest = {'version': result['version'], 'pieces': deepcopy(pieces),
                'architecture_manifest': 'architecture/manifest.json' if result.get('architecture') else None,
                'input_fingerprint': result['input_fingerprint']}
    write('manifest', manifest)
    return manifest


def load_bridge_case(path):
    path = Path(path)
    payload = load_case(path)
    for source in payload.get('raw_experiences', []):
        if 'text_path' in source:
            raw_path = Path(source['text_path'])
            if not raw_path.is_absolute():
                raw_path = path.parent / raw_path
            source['text'] = raw_path.read_text()
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    result = bridge_materials(load_bridge_case(args.input))
    manifest = write_bridge_bundle(result, args.output_dir)
    print(json.dumps({'status': result['status'], 'manifest': str(args.output_dir / 'manifest.json'),
                      'pieces': len(manifest['pieces']), 'model_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
