"""Report retained inventory and downstream work with their original units.

This entry point reads existing JSON summaries and the semantic queue. It does
not collect sources, call a model, write source databases, or promote records.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

DEFAULT_CACHE = Path('/home/longnew/.cache/datasets/benefitlens/scale50k-20261109')


def load_evidence(path: Path) -> tuple[dict, dict]:
    ref = {'path': str(path), 'available': False}
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('Expected an object summary')
    except (OSError, ValueError) as exc:
        ref['error'] = type(exc).__name__
        return {}, ref
    ref.update(available=True, sha256=sha256(raw).hexdigest(),
               source_as_of=value.get('at') or value.get('at_kst') or
               value.get('generated_at') or value.get('created_at'))
    return value, ref


def nested(value: dict, keys: tuple[str, ...]):
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def queue_counts(path: Path) -> dict:
    result = {'path': str(path), 'counted_at': datetime.now(timezone.utc).isoformat(),
              'available': False, 'unit': 'semantic_groups', 'rows': []}
    if not path.exists():
        result['error'] = 'FileNotFoundError'
        return result
    try:
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10) as cx:
            result['rows'] = [dict(kind=k, state=s, groups=n) for k, s, n in cx.execute(
                'SELECT kind,state,COUNT(*) FROM groups GROUP BY kind,state ORDER BY kind,state')]
    except sqlite3.Error as exc:
        result['error'] = type(exc).__name__
        return result
    result['available'] = True
    return result


def source_concentration(counts) -> dict:
    if not isinstance(counts, dict):
        return {'available': False}
    valid = {k: v for k, v in counts.items() if isinstance(v, int) and v >= 0}
    total = sum(valid.values())
    if not total:
        return {'available': True, 'source_case_rows': total, 'largest_source': None,
                'largest_share': None, 'top_sources': []}
    ranked = sorted(valid.items(), key=lambda pair: (-pair[1], pair[0]))
    return {'available': True, 'source_case_rows': total, 'registered_keys_with_rows': len(valid),
            'largest_source': ranked[0][0], 'largest_source_rows': ranked[0][1],
            'largest_share': ranked[0][1] / total,
            'top_sources': [dict(source=k, rows=v) for k, v in ranked[:15]],
            'remaining_rows': sum(v for _, v in ranked[15:]),
            'unit': 'case_rows_by_source_key_not_independent_people'}


def task_stage_states(sources) -> dict:
    """Keep success, reference material and holds separate at each task stage."""
    if not isinstance(sources, dict):
        return {'available': False}
    totals = {}
    for stages in sources.values():
        if not isinstance(stages, dict):
            continue
        for stage, states in stages.items():
            if not isinstance(states, dict):
                continue
            for state, count in states.items():
                if isinstance(count, int) and count >= 0:
                    totals.setdefault(stage, {})[state] = totals.get(stage, {}).get(state, 0) + count
    return {'available': True, 'unit': 'task_rows_not_case_episodes',
            'registered_source_keys': len(sources), 'by_stage_and_state': totals,
            'state_labels_are_not_root_cause_diagnoses': True}


def collect(cache: Path, production: Path | None = None) -> dict:
    production = production or cache / 'team-production-20261119'
    expansion = production / 'team-expansion-20261120'
    paths = {
        'coverage': cache / 'experience-scale-v1/coverage.json',
        'collection': cache / 'experience-scale-v1/status.json',
        'foundations': cache / 'writing-foundations-v1/audit.json',
        'writing_corpus': cache / 'writing-corpus-expansion-v1/status.json',
        'writing_library': cache / 'writing-material-library-v1/status.json',
        'conditions': expansion / 'semantic-connections/conditions/summary.json',
        'writing_links': expansion / 'semantic-connections/writing/summary.json',
        'profile': expansion / 'semantic-connections/profile/summary.json',
    }
    values, refs = {}, {}
    for name, path in paths.items():
        values[name], refs[name] = load_evidence(path)
    specifications = [
        ('retained_experience_records', 'coverage', ('experience_records_active',), 'active_case_records', 'inventory'),
        ('counted_experience_episodes', 'coverage', ('counted_experience_episodes',), 'counted_experience_episodes', 'inventory'),
        ('collector_accepted_cases', 'collection', ('accepted_cases',), 'collector_case_rows', 'collection'),
        ('collector_original_documents', 'collection', ('collected_original_documents',), 'documents', 'collection'),
        ('writing_corpus_documents', 'writing_corpus', ('unique_collected_documents',), 'corpus_documents', 'writing_inventory'),
        ('writing_corpus_cases', 'writing_corpus', ('collected_cases',), 'corpus_case_rows', 'writing_inventory'),
        ('writing_application_document_cases', 'writing_corpus', ('cases_with_application_documents',), 'corpus_cases_with_application_material', 'writing_inventory'),
        ('writing_feedback_document_cases', 'writing_corpus', ('cases_with_feedback_documents',), 'corpus_cases_with_feedback_material', 'writing_inventory'),
        ('writing_material_candidates', 'writing_library', ('distinct_material_candidates',), 'distinct_material_candidates', 'writing_inventory'),
        ('writing_reviewed_showcase', 'writing_library', ('reviewed_showcase_total',), 'showcase_materials_only', 'content_review'),
        ('writing_foundation_records', 'foundations', ('record_count',), 'foundation_record_rows', 'writing_inventory'),
        ('source_linked_condition_mappings', 'conditions', ('counts', 'verified_relevant_mapping_instances'), 'condition_mapping_instances', 'source_linking'),
        ('approved_canonical_rules', 'conditions', ('counts', 'content_approved_canonical_rules'), 'approved_rules', 'content_review'),
        ('analyzed_experience_observations', 'writing_links', ('counts', 'analyzed_compact_observations_seen'), 'semantic_observations', 'semantic_analysis'),
        ('relevant_experience_observations', 'writing_links', ('counts', 'verified_relevant_compact_observations'), 'semantic_observations', 'semantic_analysis'),
        ('source_occurrence_links', 'writing_links', ('counts', 'source_bound_occurrence_links'), 'source_occurrences', 'source_linking'),
        ('paragraph_case_links', 'writing_links', ('counts', 'paragraph_case_linked_occurrences'), 'source_occurrence_case_links', 'paragraph_linking'),
        ('full_paragraph_analyses', 'writing_links', ('counts', 'source_verified_full_paragraph_analyses_materialized'), 'full_paragraph_contexts', 'paragraph_analysis'),
        ('approved_writing_patterns', 'writing_links', ('content_approved_patterns',), 'approved_patterns', 'content_review'),
        ('live_user_questions', 'profile', ('live_user_questions_issued',), 'issued_questions', 'product'),
    ]
    metrics = []
    for key, source, keys, unit, stage in specifications:
        value = nested(values[source], keys)
        metrics.append(dict(metric=key, value=value, unit=unit, stage=stage,
                            source=source, json_keys=list(keys),
                            status='observed' if value is not None else 'unavailable'))
    return {
        'version': 'corpus-layer-report-v1',
        'reported_at': datetime.now(timezone.utc).isoformat(),
        'metrics': metrics,
        'source_concentration': source_concentration(values['collection'].get('source_case_counts')),
        'collection_task_states': task_stage_states(values['collection'].get('sources')),
        'source_roles_overlap': values['coverage'].get('evidence_role_counts_overlap'),
        'writing_document_roles': values['writing_corpus'].get('document_roles'),
        'writing_material_candidate_roles': values['writing_library'].get('candidate_roles'),
        'coverage_exclusions': values['coverage'].get('excluded'),
        'foundations': {key: values['foundations'].get(key) for key in
                        ('source_counts', 'source_units', 'unique_source_texts',
                         'validated_explicit_principles', 'corpus_is_application_experience')},
        'queue': queue_counts(production / 'compact/queue.sqlite'),
        'evidence': refs,
        'aggregation_policy': {
            'missing_is_zero': False,
            'layers_are_one_to_one': False,
            'sum_documents_paragraphs_and_episodes': False,
            'unconfirmed_source_role_is_approved_writing_pattern': False,
            'cross_stage_timestamps_are_atomic_snapshot': False,
        },
        'database_writes': 0, 'model_calls': 0, 'network_requests': 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-root', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--production-root', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = collect(args.cache_root, args.production_root)
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
        print(json.dumps({'output': str(args.output), 'metrics': len(result['metrics'])}))
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
