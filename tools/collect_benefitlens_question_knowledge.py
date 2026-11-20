"""Resume bounded question-level advice acquisition through documented public reads.

Retains complete question and answer bodies, attribution, licenses and raw receipts.
Retrieval intents are search associations, not semantically approved classifications.
No database promotion, model calls or applicant-outcome counting is performed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from hashlib import sha256
import html
import json
from pathlib import Path
import shutil
import time
from urllib.parse import urlencode, urljoin, urlsplit

import requests

import audit_benefitlens_knowhow_apis as audit
from benefitlens_source_storage import store_source_bytes
from import_benefitlens_full_inventory import SharedEvidence
from run_benefitlens_campaign import CACHE, KST, atomic_json, checked_root, single_writer
from run_benefitlens_korea import Held, soup

ROOT = CACHE / 'question-knowledge-v1'
CONFIG = Path(__file__).resolve().parents[1] / 'pipeline/config/question_knowledge_sources.json'


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def plain(body):
    """Keep paragraph/line context while stripping markup, with LF normalization."""
    node = soup(body.encode('utf-8'))
    for el in node.select('script,style,nav,footer,header,noscript'):
        el.decompose()
    return '\n'.join(line.strip() for line in node.get_text('\n').splitlines()
                     if line.strip()).replace('\r\n', '\n').replace('\r', '\n')


def official_text(page, item):
    node = next((page.select_one(sel) for sel in item['selectors'] if page.select_one(sel)), None)
    if node is None:
        raise Held('Official document content selector absent')
    for el in node.select(','.join(item.get('exclude_selectors') or ['script'])):
        el.decompose()
    text = plain(str(node))
    if len(text) < 300:
        raise Held('Official article body too short')
    return text


def saved_record(root, item, kind, intents, receipt, question=None):
    ident = str(item[kind + '_id'])
    text = plain(item.get('body', ''))
    if not text:
        raise ValueError('API body missing for ' + kind + ':' + ident)
    ref = store_source_bytes(text.encode(), root / 'bodies', suffix='.txt')
    owner = item.get('owner', {})
    row = {
        'document_id': 'stackexchange-' + kind + ':' + ident,
        'source': 'academia-stackexchange', 'native_id': ident,
        'url': item.get('link') or 'https://academia.stackexchange.com/a/' + ident,
        'title': html.unescape(item.get('title') or (question or {}).get('title', '')),
        'text_ref': ref, 'text_sha256': ref['sha256'], 'body_scope': 'full_' + kind,
        'original_body_html': store_source_bytes(item['body'].encode(), root / 'bodies', suffix='.html'),
        'publisher_role': 'community',
        'source_role': 'public_advice' if kind == 'answer' else 'question_context',
        'retrieval_intents': sorted(set(intents)),
        'author': {k: owner.get(k) for k in ('user_id', 'display_name', 'link', 'user_type')},
        'content_license': item.get('content_license'), 'creation_date': item.get('creation_date'),
        'last_activity_date': item.get('last_activity_date'),
        'last_edit_date': item.get('last_edit_date'), 'community_score': item.get('score'),
        'community_answer_accepted': item.get('is_accepted') if kind == 'answer' else None,
        'raw_response': receipt, 'exact_prompt': None,
        'semantic_review': {'state': 'pending'}, 'accepted_experience': False,
        'approved_pattern': False,
        'limitations': ['Retrieval intent needs semantic review.',
                        'Community answer acceptance and score do not identify scholarship selection.'],
    }
    if kind == 'question':
        row['question_id'] = item['question_id']
        row['original_question'] = {'id': ident, 'text': text, 'url': row['url'],
                                    'context': 'Public community question, not an official application prompt.'}
    else:
        if not question or question['native_id'] != str(item['question_id']):
            raise ValueError('Answer missing its original question')
        row['question_id'] = item['question_id']
        row['original_question'] = dict(question['original_question'],
                                        document_id=question['document_id'],
                                        body_ref=question['text_ref'])
    return row


def record_pause(checkpoint, probe, data=None):
    """Persist provider pauses across restarts rather than resuming prematurely."""
    if not probe.paused:
        return
    receipt = probe.receipts[-1] if probe.receipts else {}
    reason = 'provider_pause'
    duration = 24 * 3600
    if data and data.get('backoff'):
        duration, reason = int(data['backoff']), 'provider_backoff'
    elif data and data.get('quota_remaining') == 0:
        reason = 'provider_quota_exhausted'
    elif receipt.get('status') in (401, 403):
        reason = 'provider_http_denial'
    elif receipt.get('status') == 429:
        value = receipt.get('headers', {}).get('Retry-After', '')
        duration = max(60, int(value)) if str(value).isdigit() else 3600
        reason = 'provider_http_429'
    at = receipt.get('at_kst')
    origin = datetime.fromisoformat(at).timestamp() if at else time.time()
    checkpoint['source_pause'] = {'reason': reason, 'resume_after_epoch': origin + duration,
                                  'receipt': receipt}


def collect_stackexchange(root, config, records, checkpoint, *, pagesize, pages_per_query,
                          request_limit, probe_factory=audit.Probe, answers_only=False):
    audit.ROOT = root / 'api'
    audit.ROOT.mkdir(exist_ok=True)
    probe = probe_factory('stackexchange')
    pause = checkpoint.get('source_pause')
    if pause and pause['resume_after_epoch'] > time.time():
        return {'requests': 0, 'stop': pause['reason']}
    checkpoint.pop('source_pause', None)
    searches = checkpoint.setdefault('searches', {})
    answers = checkpoint.setdefault('answer_batches', {})
    stop = None
    latest_quota = None

    def persist():
        atomic_json(root / 'records.json', list(records.values()))
        atomic_json(root / 'checkpoint.json', checkpoint)

    def read(url):
        nonlocal latest_quota
        if len(probe.receipts) >= request_limit:
            raise ValueError('Local bounded request budget reached')
        data, receipt = probe.read(url)
        latest_quota = data.get('quota_remaining')
        checkpoint['quota_remaining_last_response'] = latest_quota
        if data.get('error_id'):
            raise ValueError('API error: ' + str(data.get('error_name')))
        record_pause(checkpoint, probe, data)
        return data, receipt

    def save_item(item, kind, intents, receipt, question=None):
        try:
            return saved_record(root, item, kind, intents, receipt, question)
        except ValueError as error:
            if not str(error).startswith('API body missing for '):
                raise
            identifier = str(item[kind + '_id'])
            checkpoint.setdefault('held_api_records', {})[kind + ':' + identifier] = {
                'reason': 'no_text_after_html_projection', 'native_id': identifier,
                'kind': kind, 'raw_response': receipt,
                'original_body_html': store_source_bytes(item.get('body', '').encode(),
                                                         root/'bodies', suffix='.html'),
                'counted_as_document': False}
            return None

    try:
        for intent, terms in ({} if answers_only else config['question_queries']).items():
            for term in terms:
                key = sha256(json.dumps([intent, term, pagesize]).encode()).hexdigest()
                state = searches.setdefault(key, {'intent': intent, 'query': term,
                                                   'next_page': 1, 'done': False, 'pages': []})
                steps = 0
                while not state['done'] and steps < pages_per_query and not probe.paused:
                    page = state['next_page']
                    data, receipt = read('https://api.stackexchange.com/2.3/search/advanced?' + urlencode({
                        'site': config['site'], 'q': term, 'filter': 'withbody',
                        'pagesize': pagesize, 'page': page, 'sort': 'relevance',
                        'answers': 1}))
                    for item in data.get('items', []):
                        rid = 'stackexchange-question:' + str(item['question_id'])
                        intents = records.get(rid, {}).get('retrieval_intents', []) + [intent]
                        row = save_item(item, 'question', intents, receipt)
                        if row:
                            records[rid] = row
                    state['pages'].append({'page': page, 'returned': len(data.get('items', [])),
                                           'has_more': bool(data.get('has_more')), 'receipt': receipt})
                    state.update(next_page=page + 1, done=not data.get('has_more'))
                    steps += 1
                    persist()
                if probe.paused:
                    break
            if probe.paused:
                break

        # Each batch is durable; resume its next answer page, including >100 answers.
        questions = sorted((r for r in records.values() if r['body_scope'] == 'full_question'),
                           key=lambda r: int(r['native_id']))
        for offset in range(0, len(questions), 100):
            group = questions[offset:offset + 100]
            ids = ';'.join(r['native_id'] for r in group)
            state = answers.setdefault(ids, {'next_page': 1, 'done': False, 'pages': []})
            steps = 0
            while not state['done'] and steps < pages_per_query and not probe.paused:
                page = state['next_page']
                data, receipt = read('https://api.stackexchange.com/2.3/questions/' + ids + '/answers?' +
                                     urlencode({'site': config['site'], 'filter': 'withbody',
                                                'pagesize': 100, 'page': page, 'sort': 'votes', 'order': 'desc'}))
                for item in data.get('items', []):
                    parent = records['stackexchange-question:' + str(item['question_id'])]
                    row = save_item(item, 'answer', parent['retrieval_intents'], receipt, parent)
                    if row:
                        records[row['document_id']] = row
                state['pages'].append({'page': page, 'returned': len(data.get('items', [])),
                                       'has_more': bool(data.get('has_more')), 'receipt': receipt})
                state.update(next_page=page + 1, done=not data.get('has_more'))
                steps += 1
                persist()
            if probe.paused:
                break
    except (ValueError, requests.RequestException) as error:
        record_pause(checkpoint, probe)
        stop = str(error)
        checkpoint.setdefault('errors', []).append({'at_kst': datetime.now(KST).isoformat(), 'reason': stop})
    # A question may receive additional intent links after its answers were cached.
    for row in records.values():
        if row['body_scope'] == 'full_answer':
            row['retrieval_intents'] = records['stackexchange-question:' + str(row['question_id'])]['retrieval_intents']
    persist()
    return {'requests': len(probe.receipts), 'quota_remaining_last_response': latest_quota,
            'stop': stop or (checkpoint.get('source_pause') or {}).get('reason')}


def collect_official(root, sources, records, checkpoint):
    folder = root / 'official'
    folder.mkdir(exist_ok=True)
    ledger = SharedEvidence(folder, datetime.now(KST).date().isoformat(), 30)
    for item in sources:
        rid = 'official:' + item['id']
        signature = sha256(json.dumps([item['selectors'], item.get('exclude_selectors', [])]).encode()).hexdigest()
        held = checkpoint.setdefault('official_holds', {}).get(item['id'])
        # Changed parser selectors may reprocess an already captured public HTML.
        # Access denials and robots holds are never released by parser changes.
        if held and held['reason'] in ('Official document content selector absent',
                                       'Official article body too short') and held.get('selector_signature') != signature:
            checkpoint.setdefault('official_parser_hold_history', []).append(held)
            checkpoint['official_holds'].pop(item['id'])
            held = None
        if (rid in records and records[rid].get('parser_signature') == signature) or held:
            continue
        try:
            url = item['url']
            host = urlsplit(url).hostname
            for _ in range(5):
                raw, receipt = ledger.request(url, redirect_metadata=True)
                if receipt['status'] not in (301, 302, 303, 307, 308):
                    break
                url = urljoin(url, receipt['headers'].get('location', ''))
                if urlsplit(url).hostname != host:
                    raise Held('Redirect left registered official publisher host')
            if receipt['status'] != 200 or 'text/html' not in receipt['headers'].get('content-type', ''):
                raise Held('Expected official HTML document')
            page = soup(raw)
            text = official_text(page, item)
            ref = store_source_bytes(text.encode(), root / 'bodies', suffix='.txt')
            records[rid] = {'document_id': rid, 'source': 'official-policy', 'url': url,
                            'title': page.title.get_text(strip=True) if page.title else item['id'],
                            'text_ref': ref, 'text_sha256': ref['sha256'], 'body_scope': 'full_article',
                            'publisher_role': 'official_program', 'source_role': 'official_policy',
                            'program_id': item['program_id'], 'retrieval_intents': item['intents'],
                            'guidance_edition': item.get('guidance_edition'),
                            'parser_signature': signature,
                            'raw_response': receipt, 'content_license': 'not_declared_in_capture',
                            'author': {'display_name': host}, 'exact_prompt': None,
                            'limitations': [item['limitation']], 'semantic_review': {'state': 'pending'},
                            'accepted_experience': False, 'approved_pattern': False}
        except (ValueError, OSError, requests.RequestException) as error:
            checkpoint['official_holds'][item['id']] = {'url': item['url'], 'reason': str(error),
                                                        'selector_signature': signature,
                                                        'at_kst': datetime.now(KST).isoformat()}
        atomic_json(root / 'records.json', list(records.values()))
        atomic_json(root / 'checkpoint.json', checkpoint)
    return {'network_requests_cumulative': ledger.network_requests}


def status(records, checkpoint, api_result=None, official_result=None):
    values = list(records.values())
    searches = checkpoint.get('searches', {})
    answer_batches = checkpoint.get('answer_batches', {})
    coverage = {}
    for intent in ('research_motivation', 'personal_contribution', 'program_fit', 'execution_plan',
                   'leadership_impact', 'challenge_reflection'):
        subset = [r for r in values if intent in r['retrieval_intents']]
        coverage[intent] = {'questions': sum(r['body_scope'] == 'full_question' for r in subset),
                            'answers': sum(r['body_scope'] == 'full_answer' for r in subset),
                            'official_docs': sum(r['source'] == 'official-policy' for r in subset)}
    return {'version': 'question-knowledge-v1', 'at_kst': datetime.now(KST).isoformat(),
            'unit': 'source question, answer or official guidance document',
            'source_counts': dict(Counter(r['source'] for r in values)),
            'unique_questions': sum(r['body_scope'] == 'full_question' for r in values),
            'unique_answers': sum(r['body_scope'] == 'full_answer' for r in values),
            'unique_official_docs': sum(r['source'] == 'official-policy' for r in values),
            'per_intent_retrieval_coverage': coverage, 'held': checkpoint.get('official_holds', {}),
            'api_progress': {'captured_search_pages': sum(len(s['pages']) for s in searches.values()),
                             'captured_answer_pages': sum(len(s['pages']) for s in answer_batches.values()),
                             'searches_with_more_pages': sum(not s['done'] for s in searches.values()),
                             'answer_batches_with_more_pages': sum(not s['done'] for s in answer_batches.values()),
                             'quota_remaining_last_response': checkpoint.get('quota_remaining_last_response')},
            'quality_flags': {'community_documents_without_declared_license': sum(
                r['source'] == 'academia-stackexchange' and not r.get('content_license') for r in values)},
            'api_pause': checkpoint.get('source_pause'), 'api': api_result, 'official': official_result,
            'held_api_records': len(checkpoint.get('held_api_records', {})),
            'model_called': False, 'approved_patterns': 0, 'accepted_experiences_added': 0,
            'semantic_review': 'pending', 'whole_application_required': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--pagesize', type=int, default=5)
    parser.add_argument('--pages-per-query', type=int, default=1)
    parser.add_argument('--request-limit', type=int, default=24)
    parser.add_argument('--skip-official', action='store_true')
    parser.add_argument('--skip-api', action='store_true')
    parser.add_argument('--answers-only', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.pagesize <= 100 or not 1 <= args.pages_per_query <= 10 or not 1 <= args.request_limit <= 30:
        parser.error('pagesize 1..100, pages-per-query 1..10 and request-limit 1..30 required')
    root = checked_root(args.root)
    if shutil.disk_usage(root).free < 512 * 1024**2:
        raise ValueError('Shared cache reserve')
    config = load(args.config, {})
    with single_writer(root):
        records = {r['document_id']: r for r in load(root / 'records.json', [])}
        checkpoint = load(root / 'checkpoint.json', {})
        api = None if args.skip_api else collect_stackexchange(root, config['stackexchange'], records, checkpoint,
                                    pagesize=args.pagesize, pages_per_query=args.pages_per_query,
                                    request_limit=args.request_limit, answers_only=args.answers_only)
        official = None if args.skip_official else collect_official(root, config['official_documents'], records, checkpoint)
        result = status(records, checkpoint, api, official)
        atomic_json(root / 'status.json', result)
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
