"""Run a bounded, source-only semantic abstraction on cached real requests."""
import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import time

from benefitlens_semantic_intent import (
    REPO, VERSION, SCHEMA, POLICY, text_hash, validate_observations, compare_intents,
)
from benefitlens_source_storage import read_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json, checked_root, single_writer


def sample_bundle():
    docs, tasks = {}, []

    def document(identifier, url, ref, role, publisher):
        raw = read_source_bytes(ref['path'], storage=ref.get('storage'))
        if sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError('Cached source hash mismatch')
        text = raw.decode()
        docs[identifier] = {'document_id': identifier, 'url': url, 'text': text,
            'text_sha256': text_hash(text), 'text_ref': ref, 'source_role': role,
            'publisher_role': publisher}

    def task(identifier, docid, question, end_needle, kind, use=None):
        text = docs[docid]['text']
        start = text.index(question)
        end = text.index(end_needle, start+len(question)) if end_needle else len(text)
        tasks.append({'task_id': identifier, 'document_id': docid,
            'question_text': question, 'question_kind': kind,
            'context_start': start, 'context_end': end, 'declared_use': use})

    readings = json.loads((CACHE/'writing-foundations-v1/pattern-analysis-v1/source-readings.json').read_text())
    row = next(x for x in readings if 'LFX - 2023' in x['document_id'])
    document('lfx-public-material', row['url'], row['text_ref'], 'public_application_material', 'personal')
    questions = ['How did you find out about our mentorship program?',
        'Why are you interested in this program?',
        'What experience and knowledge/skills do you have that are\napplicable to this program?',
        'What do you hope to get out of this mentorship experience?']
    for i, question in enumerate(questions):
        task('lfx-'+str(i+1), 'lfx-public-material', question,
             questions[i+1] if i<3 else 'Project Proposal',
             'question_in_public_application_material')

    records = json.loads((CACHE/'question-knowledge-v1/records.json').read_text())
    row = next(x for x in records if x['document_id']=='official:chevening-current-application-criteria')
    document('chevening-guidance', row['url'], row['text_ref'], 'official_guidance', 'official_program')
    questions = ['Do I have good examples of my leadership and influencing skills?',
        'Do I have good examples of building professional relationships to achieve positive outcomes?',
        'How will my chosen course give me the skills to address pressing challenges linked to UK priority areas?',
        'How will my career plan support my ambitions to drive positive change?']
    for i, question in enumerate(questions):
        task('chevening-'+str(i+1), 'chevening-guidance', question,
             str(i+2)+'. '+questions[i+1] if i<3 else 'In this section',
             'official_guidance_question', 'selection')

    row = next(x for x in records if x['document_id']=='official:fulbright-korea-graduate-instructions')
    document('fulbright-instructions', row['url'], row['text_ref'], 'official_guidance', 'official_program')
    task('fulbright-statistics', 'fulbright-instructions',
         'Family Funds 아래 Family Funds와 Your Own Funds 정보를 작성하시기 바랍니다.',
         'Expected Additional Funding', 'administrative_instruction', 'data_collection')

    examples = json.loads((CACHE/'experience-patterns-v1/examples.json').read_text())
    row = examples['sources']['hymans']
    document('higherin-review', row['source_url'], row['text_ref'], 'participation_review', 'personal')
    task('higherin-responsibility', 'higherin-review',
         'Were you given much responsibility during your placement / internship?',
         'Please rate how meaningful', 'experience_review_question', 'experience_review')
    return {'version': VERSION, 'allowed_use': 'source_analysis_only',
            'documents': list(docs.values()), 'tasks': tasks}


def model_payload(bundle):
    docs = {d['document_id']: d for d in bundle['documents']}
    return {'version': VERSION, 'allowed_use': bundle['allowed_use'],
        'open_axis_vocabulary': json.loads(POLICY.read_text())['initial_axes'],
        'tasks': [{**task, 'source_role': docs[task['document_id']]['source_role'],
            'publisher_role': docs[task['document_id']]['publisher_role'],
            'source_url': docs[task['document_id']]['url'],
            'source_context': docs[task['document_id']]['text'][task['context_start']:task['context_end']]}
            for task in bundle['tasks']]}


def run(root, execute, timeout):
    root = checked_root(root)
    with single_writer(root):
        bundle = sample_bundle()
        payload = model_payload(bundle)
        prompt = (REPO/'pipeline/config/semantic_intent_prompt_v1.txt').read_text()
        request = prompt+'\nSOURCE_INPUT_JSON\n'+json.dumps(payload, ensure_ascii=False)
        key = text_hash(request + SCHEMA.read_text())
        folder = root/key[:24]
        folder.mkdir(exist_ok=True)
        atomic_json(folder/'source-bundle.json', bundle)
        atomic_json(folder/'model-input.json', payload)
        (folder/'analysis-prompt.txt').write_text(prompt)
        (folder/'analysis-schema.json').write_text(SCHEMA.read_text())
        state = {'version': VERSION, 'input_sha256': key, 'tasks': len(bundle['tasks']),
                 'model_called': False, 'semantic_review': 'unreviewed',
                 'state': 'input_prepared', 'at_kst': datetime.now(KST).isoformat()}
        atomic_json(root/'status.json', state)
        if not execute:
            print(json.dumps(state, ensure_ascii=False)); return
        result_path = folder/'observations.json'
        if result_path.exists():
            result = json.loads(result_path.read_text())
            receipt = json.loads((folder/'receipt.json').read_text())
            if receipt['input_sha256'] != key or receipt['result_sha256'] != sha256(result_path.read_bytes()).hexdigest():
                raise ValueError('Cached observation receipt failed integrity')
            properties = json.loads(SCHEMA.read_text())['properties']['observations']['items']['properties']
            raw = {'version': VERSION, 'observations': [{k: row[k] for k in properties} | {
                'source_evidence': [{'document_id': e['document_id'], 'quote': e['quote']}
                                    for e in row['source_evidence']]}
                for row in result['observations']]}
            validate_observations(raw, bundle)
            state.update(state='reused_validated_observation', model_called=False,
                         result_path=str(result_path), prior_run_receipt=str(folder/'receipt.json'))
            atomic_json(root/'status.json', state)
            print(json.dumps(state, ensure_ascii=False)); return
        # The installed CLI validator uses the default JSON Schema dialect.
        # Our schema uses common keywords; keep its 2020-12 declaration local.
        model_schema = json.loads(SCHEMA.read_text())
        model_schema.pop('$schema', None)
        attempts = folder/'attempts'
        attempts.mkdir(exist_ok=True)
        attempt = attempts/(str(len(list(attempts.iterdir()))+1).zfill(3))
        attempt.mkdir()
        args = ['/home/longnew/.local/bin/claude', '--safe-mode', '--print', '--tools', '',
                '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                '--permission-mode', 'plan', '--no-chrome', '--no-session-persistence',
                '--output-format', 'json', '--json-schema', json.dumps(model_schema)]
        started = time.time()
        state.update(state='model_running', model_call_attempted=True, model_called=None)
        atomic_json(root/'status.json', state)
        try:
            response = subprocess.run(args, input=request, text=True, capture_output=True, timeout=timeout)
            (attempt/'model-response.json').write_text(response.stdout)
            (attempt/'model-stderr.txt').write_text(response.stderr)
            if response.returncode:
                state['model_called'] = False if '--json-schema is not a valid' in response.stderr else None
                raise ValueError('Model invocation failed: '+response.stderr[:350])
            envelope = json.loads(response.stdout)
            state['model_called'] = bool(envelope.get('modelUsage'))
            if response.returncode or envelope.get('is_error'):
                raise ValueError('Model invocation failed; raw receipt retained')
            raw = envelope.get('structured_output')
            if raw is None:
                raw = json.loads(envelope['result'])
            checked = validate_observations(raw, bundle)
            comparisons = [compare_intents(a, b) for i, a in enumerate(checked['observations'])
                           for b in checked['observations'][i+1:]]
            atomic_json(result_path, checked)
            atomic_json(folder/'comparisons.json', comparisons)
            state.update(state='source_validated_semantic_review_pending', result_path=str(result_path),
                         comparison_path=str(folder/'comparisons.json'),
                         models=list((envelope.get('modelUsage') or {}).keys()),
                         result_sha256=sha256(result_path.read_bytes()).hexdigest(),
                         elapsed_seconds=round(time.time()-started, 1),
                         approved_patterns_added=0, source_tasks=len(bundle['tasks']))
            state['finished_at_kst'] = datetime.now(KST).isoformat()
            atomic_json(folder/'receipt.json', state)
            atomic_json(attempt/'receipt.json', state)
        except Exception as error:
            state.update(state='failed_receipt_retained', error=type(error).__name__+': '+str(error)[:400],
                         elapsed_seconds=round(time.time()-started, 1))
            atomic_json(root/'status.json', state)
            atomic_json(attempt/'receipt.json', state)
            raise
        atomic_json(root/'status.json', state)
        print(json.dumps(state, ensure_ascii=False))


if __name__=='__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=CACHE/'semantic-intent-v1')
    p.add_argument('--execute', action='store_true')
    p.add_argument('--timeout', type=int, default=240)
    a = p.parse_args()
    run(a.root, a.execute, a.timeout)
