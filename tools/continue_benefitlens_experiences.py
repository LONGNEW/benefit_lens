"""Continue experience discovery and collection after a bounded queue drains.

Uses the existing experience root and its exclusive lock. Persistent source
streams, originals and import receipts make SIGTERM and restart recoverable.
"""
import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import threading
import time
import uuid
from urllib.parse import urlsplit

import project
from benefitlens_experience_expansion import Expansion, collect, failure, VERSION
from benefitlens_experience_coverage import database_coverage
from benefitlens_experience_transport import ExperienceEvidence
from run_benefitlens_campaign import CACHE, KST, atomic_json, checked_root, single_writer
from scale_benefitlens_experiences import ExperienceFrontier, import_originals, import_pending


def next_action(*, pending_at, added, continuous, now):
    if added:
        return 'collect', 0
    if pending_at is not None:
        return 'waiting_for_provider', min(30, max(1, pending_at - now))
    return ('waiting_for_source_discovery', 30) if continuous else ('checkpoint', 0)


def run(args):
    root = checked_root(args.root)
    if args.max_requests < 1 or args.workers < 1 or args.seed_batch < 1 or args.import_batch < 1:
        raise ValueError('Positive budgets, workers and seed batches required')
    with single_writer(root):
        base = json.loads((project.ROOT/'pipeline/config/experience_scale_sources.json').read_text())
        config = json.loads(Path(args.config).read_text())
        sources = {s['id']: s for s in base['sources'] if s.get('enabled', True)}
        frontier = ExperienceFrontier(root)
        frontier.recover_interrupted()
        expansion = Expansion(frontier, sources, config)
        seeded = expansion.seed(args.seed_batch) + expansion.add_materials(args.seed_batch)
        run_id = str(uuid.uuid4())
        ledger = ExperienceEvidence(root, run_id, args.max_requests)
        ledger.defer_long_waits = True
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        started = time.time()
        state = {'run_id': run_id, 'pid': os.getpid(), 'started_at': datetime.now(KST).isoformat(),
                 'runtime': 'Python only; no collection agent or model calls', 'parser_version': VERSION,
                 'target_cases': args.target, 'request_budget_per_run': args.max_requests,
                 'model_called': False, 'approved_patterns_added': 0,
                 'continuous_discovery': args.continuous, 'new_tasks_enqueued_this_run': seeded}
        processed = 0
        last_checkpoint = last_coverage = 0
        config_mtime = Path(args.config).stat().st_mtime_ns
        reason = None

        def checkpoint(mode='running', force_coverage=False):
            nonlocal last_coverage
            db_error = None
            if not args.no_postgres:
                try:
                    import_originals(frontier, limit=args.import_batch)
                    import_pending(frontier, limit=args.import_batch)
                except Exception as error:
                    db_error = type(error).__name__ + ': ' + str(error)[:300]
                if force_coverage or time.time() - last_coverage > 120:
                    try:
                        coverage = database_coverage(floor=args.target)
                        state['experience_coverage'] = coverage
                        state['target_cases'] = coverage['target_experience_episodes']
                        atomic_json(root/'coverage.json', coverage)
                        last_coverage = time.time()
                    except Exception as error:
                        db_error = type(error).__name__ + ': ' + str(error)[:300]
            with frontier.connect() as c:
                material_counts = dict(c.execute("SELECT state,count(*) FROM experience_task WHERE state IN ('reference_material','needs_review','policy_held') GROUP BY state"))
                queued = c.execute("SELECT count(*) FROM experience_task WHERE state IN ('pending','retry','processing')").fetchone()[0]
                cursors = dict(c.execute('SELECT stream,position FROM expansion_cursor'))
            state.update(at_kst=datetime.now(KST).isoformat(), state=mode,
                processed_this_run=processed, network_requests_this_run=ledger.network_requests,
                network_requests_before_run=ledger.lifetime_requests_at_start,
                postgres_error=db_error, queued_tasks=queued, material_task_counts=material_counts,
                discovery_cursors=cursors, registered_source_count=len(sources),
                elapsed_seconds=round(time.time()-started, 1), **frontier.snapshot())
            state['provider_backoff_until'] = {h: datetime.fromtimestamp(t, KST).isoformat()
                for h, t in ledger.rate_holds.items() if t > time.time()}
            atomic_json(root/'status.json', state)
            atomic_json(root/'continuous-status.json', state)
            atomic_json(root/'continuous-runtime.json', {'pid': os.getpid(), 'root': str(root),
                'run_id': run_id, 'config': str(Path(args.config).resolve()), 'at_kst': state['at_kst']})
            print(json.dumps({k: state.get(k) for k in ('at_kst', 'state', 'processed_this_run',
                'accepted_cases', 'queued_tasks', 'registered_source_count', 'network_requests_this_run',
                'new_tasks_enqueued_this_run', 'postgres_error')}, ensure_ascii=False), flush=True)

        checkpoint(force_coverage=True)
        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {}
                while True:
                    if not reason:
                        if stop.is_set(): reason = 'requested_checkpoint'
                        elif ledger.network_requests >= args.max_requests: reason = 'request_budget'
                        elif args.max_items and processed >= args.max_items: reason = 'batch_budget'
                        elif args.max_seconds and time.time() - started >= args.max_seconds: reason = 'time_budget'
                        elif state.get('experience_coverage', {}).get('counted_experience_episodes', 0) >= state['target_cases']:
                            reason = 'global_target_reached_linkage_review_pending'
                    if not reason:
                        frontier.defer_rate_hosts(ledger.rate_holds)
                        busy = [urlsplit(t['url']).hostname for t in futures.values()
                                if t['kind'] != 'cached_material']
                        capacity = args.workers - len(futures)
                        if args.max_items:
                            capacity = min(capacity, max(0, args.max_items-processed-len(futures)))
                        tasks = frontier.lease(run_id, capacity, busy_hosts=busy) if capacity > 0 else []
                        for task in tasks:
                            source = sources.get(task['source'])
                            if source is None:
                                frontier.finish(task, {'status': 'needs_review', 'reason': 'Source definition missing'})
                                continue
                            futures[pool.submit(collect, task, source, ledger)] = task
                    if not futures and reason:
                        break
                    if not futures:
                        # More streams are consulted even while one publisher is delayed.
                        if Path(args.config).stat().st_mtime_ns != config_mtime:
                            expansion.config = json.loads(Path(args.config).read_text())
                            config_mtime = Path(args.config).stat().st_mtime_ns
                        today = datetime.now(KST).date().isoformat()
                        if ledger.day != today:
                            ledger.day = today
                            ledger.policies.clear()
                        refreshed = expansion.refresh_indices(today)
                        added = expansion.seed(args.seed_batch) + expansion.add_materials(args.seed_batch)
                        added += refreshed
                        state['new_tasks_enqueued_this_run'] += added
                        action, delay = next_action(pending_at=frontier.next_ready_at(), added=added,
                            continuous=args.continuous, now=time.time())
                        if action == 'checkpoint':
                            reason = 'bounded_streams_checkpoint'; break
                        checkpoint(action)
                        if delay: stop.wait(delay)
                        continue
                    done, _ = wait(futures, timeout=1, return_when=FIRST_COMPLETED)
                    for future in done:
                        task = futures.pop(future)
                        try:
                            result, children = future.result()
                        except Exception as error:
                            result, children = failure(error, task), []
                        expansion.finish(task, result, children)
                        processed += 1
                    if time.time() - last_checkpoint >= 10:
                        if Path(args.config).stat().st_mtime_ns != config_mtime:
                            expansion.config = json.loads(Path(args.config).read_text())
                            config_mtime = Path(args.config).stat().st_mtime_ns
                        if len(futures) < args.workers:
                            added = expansion.seed(args.seed_batch) + expansion.add_materials(args.seed_batch)
                            state['new_tasks_enqueued_this_run'] += added
                        checkpoint()
                        last_checkpoint = time.time()
            checkpoint('checkpoint', force_coverage=True)
        finally:
            state.update(at_kst=datetime.now(KST).isoformat(), state='checkpoint',
                         stop_reason=reason or 'failed; checkpoint retained')
            atomic_json(root/'status.json', state)
            atomic_json(root/'continuous-status.json', state)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=CACHE/'experience-scale-v1')
    p.add_argument('--config', type=Path, default=project.ROOT/'pipeline/config/experience_expansion_sources_v1.json')
    p.add_argument('--continuous', action='store_true')
    p.add_argument('--target', type=int, default=50000)
    p.add_argument('--max-requests', type=int, default=100000)
    p.add_argument('--max-items', type=int, default=0)
    p.add_argument('--max-seconds', type=int, default=0)
    p.add_argument('--workers', type=int, default=8)
    p.add_argument('--seed-batch', type=int, default=100)
    p.add_argument('--import-batch', type=int, default=500)
    p.add_argument('--no-postgres', action='store_true')
    run(p.parse_args())
