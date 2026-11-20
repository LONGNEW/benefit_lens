"""Automatically project saved bridge metadata, without semantic annotation."""
from hashlib import sha256
from pathlib import Path
import argparse
import json

from benefitlens_writing_diagnostics import canonical_sha256, diagnose_writing


def adapt_bridge_to_diagnostics(bridge_input, bridge_result):
    """Copy supplied IDs, source spans and declared states; add no semantic labels."""
    task = bridge_result.get('compiler_task') or {}
    architecture = bridge_result.get('architecture') or {}
    facts = bridge_result.get('compiler_facts') or []
    findex = {f['fact_id']: f for f in facts}
    sources, source_ids = [], {}
    for i, raw in enumerate(bridge_input.get('raw_experiences', []), 1):
        source_id = 'source-' + str(i)
        source_ids[raw['experience_id']] = source_id
        hashes = {f.get('source_ref', {}).get('source_sha256') for f in facts
                  if f.get('experience_id') == raw['experience_id']}
        hashes.discard(None)
        # Differing declarations are preserved as separate per-fact expected
        # hashes below; no preferred hash is silently chosen.
        sources.append({'source_id': source_id, 'text': raw.get('text'),
                        'sha256': next(iter(hashes)) if len(hashes) == 1 else None})
    result = {'document_id': task.get('task_id'), 'provenance': bridge_input.get('provenance', 'unknown'),
              'subject_id': task.get('subject_id'),
              'allowed_experience_ids': task.get('allowed_experience_ids',
                  [task['experience_id']] if task.get('experience_id') else None),
              'text': bridge_result.get('draft_text_preview'), 'facts': [], 'sources': sources,
              'sentences': [], 'paragraphs': [], 'paragraph_links': [],
              'claim_ground_links': bridge_result.get('claim_ground_links'),
              'tasks': bridge_result.get('diagnostic_tasks'),
              'outline_order': bridge_result.get('outline_order'),
              'constraints': architecture.get('effective_constraints', task.get('constraints')),
              'requirements': [], 'human_reviews': bridge_result.get('human_reviews'),
              'annotation_provenance': {'origin': 'automatic_saved_bridge_metadata_projection',
                  'added_semantic_annotations': 0, 'candidate_extraction': 'not_performed',
                  'missing_semantic_coverage_and_schedules': 'unobserved',
                  'actor': 'copied through existing sentence-fact ID binding, not inferred from prose'}}
    for fact in facts:
        row = {k: fact[k] for k in ('fact_id', 'subject_id', 'experience_id', 'kind', 'status',
                                    'value', 'as_of', 'activity_status') if k in fact}
        ref = fact.get('source_ref') or {}
        span = ref.get('source_span') or {}
        row['source_ref'] = {'source_id': source_ids.get(fact.get('experience_id')),
            'text_sha256': ref.get('source_sha256'), 'char_start': span.get('start'),
            'char_end': span.get('end'), 'quote': span.get('quote')}
        result['facts'].append(row)
    for section in architecture.get('sections', []):
        for paragraph in section.get('paragraphs', []):
            pid = paragraph['paragraph_id']
            result['paragraphs'].append({'paragraph_id': pid,
                'sentence_ids': [s['sentence_id'] for s in paragraph.get('sentences', [])]})
            for sentence in paragraph.get('sentences', []):
                fids = sentence.get('fact_ids')
                fact = findex.get(fids[0]) if fids else None
                row = {'sentence_id': sentence['sentence_id'], 'paragraph_id': pid,
                       'text': sentence.get('text'), 'fact_ids': fids,
                       'subject_id': sentence.get('subject_id', fact.get('subject_id') if fact else None),
                       'experience_id': sentence.get('experience_id'), 'claim_kind': sentence.get('fact_kind'),
                       'activity_status': sentence.get('activity_status')}
                if sentence.get('inference_status') == 'literal_input_statement':
                    row['literal_fact_binding'] = True
                # Do not copy a fact's state into the sentence annotation: only
                # sentence-supplied state metadata can be compared independently.
                for key in ('as_of', 'state_value'):
                    if key in sentence:
                        row[key] = sentence[key]
                result['sentences'].append(row)
            link = paragraph.get('bridge_relation')
            if link:
                result['paragraph_links'].append({'link_id': 'bridge:' + pid,
                    'from_paragraph_id': pid, 'to_paragraph_id': paragraph.get('next_paragraph_id'),
                    'from_sentence_id': link.get('from_sentence_id'), 'to_sentence_id': link.get('to_sentence_id')})
    # No invented requirement-to-sentence bindings, expected order or schedules.
    requirements = task.get('requirements')
    if requirements is None:
        result['requirements'] = None
    else:
        for requirement in requirements:
            result['requirements'].append({'requirement_id': requirement.get('requirement_id', requirement.get('id')),
                'required': requirement.get('required'), 'applicable': requirement.get('applicable', True),
                'sentence_ids': requirement.get('sentence_ids')})
    return result


def load_saved_bridge(bridge_input_path, output_directory):
    """Read known local bundle pieces and return data plus exact source hashes."""
    from benefitlens_writing_material_bridge import fingerprint, load_bridge_case

    root, hashes = Path(output_directory), {}

    def read(path):
        raw = path.read_bytes()
        hashes[str(path.resolve())] = sha256(raw).hexdigest()
        return json.loads(raw)

    read(Path(bridge_input_path))
    stored_fingerprint = read(root / 'input_fingerprint.json')
    # The bridge expands declared local strategy/text references before hashing.
    # Reuse its exact loader and fingerprint rather than hashing raw JSON alone.
    bridge_input = load_bridge_case(bridge_input_path)
    if not isinstance(stored_fingerprint, str) or fingerprint(bridge_input) != stored_fingerprint:
        raise ValueError('bridge_input_fingerprint_mismatch: input and saved output bundle differ')
    result = {'compiler_task': read(root / 'compiler_task.json'),
              'draft_text_preview': read(root / 'draft_text_preview.json'),
              'compiler_facts': [f for p in sorted(root.glob('compiler_facts*.json')) for f in read(p)]}
    architecture = read(root / 'architecture/summary.json')
    architecture['sections'] = [read(p) for p in sorted((root / 'architecture').glob('section-*.json'),
                                      key=lambda p: int(p.stem.split('-')[1]))]
    result['architecture'] = architecture
    return bridge_input, result, hashes


def diagnose_bridge(bridge_input, bridge_result):
    payload = adapt_bridge_to_diagnostics(bridge_input, bridge_result)
    result = diagnose_writing(payload)
    result['bridge_adapter'] = {'version': 'bridge-diagnostics-v1', 'added_semantic_annotations': 0,
                               'input_projection_sha256': canonical_sha256(payload)}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bridge-input', type=Path, required=True)
    parser.add_argument('--bridge-output-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    original, bridge_result, hashes = load_saved_bridge(args.bridge_input, args.bridge_output_dir)
    result = diagnose_bridge(original, bridge_result)
    source_file = args.output.with_suffix('.sources.json')
    manifest = {'adapter_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
                'upstream_file_sha256': hashes, 'added_semantic_annotations': 0,
                'projection_method': 'automatic structural copy of saved metadata; no prose interpretation'}
    result['bridge_adapter']['source_manifest'] = str(source_file)
    text = json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n'
    source_text = json.dumps(manifest, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n'
    if max(len(text), len(source_text)) >= 10000:
        raise ValueError('Split bridge bundle before exporting diagnostic documents of 10000 characters or more')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(source_text, encoding='utf-8')
    args.output.write_text(text, encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'added_semantic_annotations': 0,
                      'semantic_quality_status': result['semantic_quality_status']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
