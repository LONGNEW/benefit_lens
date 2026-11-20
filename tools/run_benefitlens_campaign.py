"""Durable, agent-free public collection: run, start, stop, status, export.

Only configured public sources are visited. SQLite is the frontier and audit
ledger; source bytes and extracted rows are immutable gzip blobs. Unknown
semantics are held. Nothing is written to the production opportunity database.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
from hashlib import sha256
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from benefitlens_campaign_adapters import attested_search_terms
from benefitlens_campaign_identity import application_identity, jobbnorge_application_identity
from benefitlens_campaign_public_sources import listing_jobs, parse_detail
from benefitlens_opportunity_groups import issuer_key, role_key, category
from benefitlens_source_storage import read_source_bytes, store_source_bytes
from build_benefitlens_live_batch import source_id

CACHE = Path('/home/longnew/.cache/datasets/benefitlens/scale50k-20261109')
DEFAULT_ROOT = CACHE / 'python-campaign-v1'
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / 'pipeline/config/python_campaign.json'
KST = ZoneInfo('Asia/Seoul')
VERSION = 'python-campaign-v1'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def today():
    return datetime.now(KST).date().isoformat()


def request_key(payload):
    return sha256(encoded({k: payload.get(k) for k in ('url', 'method', 'body')}).encode()).hexdigest()


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as f:
        f.write(encoded(value)+'\n'); f.flush(); os.fsync(f.fileno())
    os.replace(temporary, path)


def app_key(url):
    u = urlsplit(url)
    if u.path == '/jobseeker/' and (key := jobbnorge_application_identity(url)):
        return f'https://jobseeker.jobbnorge.no/client/apply/{key[1]}'
    # Remove only known tracking fields; tenant and actual native parameters stay.
    query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in {'source', 'ref'}]
    return urlunsplit((u.scheme, (u.netloc or '').lower(), u.path, urlencode(sorted(query)), ''))


def listing_fingerprint(payload, candidates):
    """Ignore page counters and dynamic HTML; compare the actual native-ID set."""
    u = urlsplit(payload['url'])
    paging = {'page', 'offset', 'limit', 'per_page'}
    query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k not in paging]
    body = {k: v for k, v in (payload.get('body') or {}).items() if k not in paging}
    stream = sha256(encoded({'url': urlunsplit((u.scheme, u.netloc, u.path, urlencode(sorted(query)), '')),
                             'method': payload.get('method', 'GET'), 'body': body}).encode()).hexdigest()
    ids = sorted({str(c.get('native_id') or c['url']) for c in candidates})
    return stream, sha256(encoded(ids).encode()).hexdigest()


def checked_root(root):
    root = Path(root).absolute()
    root.resolve().relative_to(CACHE.resolve())
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < 256*1024*1024:
        raise ValueError('Cache disk has less than 256 MiB reserve')
    return root


def live_pid(root):
    try:
        runtime = json.loads((root/'runtime.json').read_text())
        pid = runtime['pid']
        args = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        cwd = Path(f'/proc/{pid}/cwd').resolve()
        script_found = any(a and not a.startswith(b'-') and (cwd/os.fsdecode(a)).resolve() == Path(__file__).resolve() for a in args)
        root_matches = str(root).encode() in args or (b'--root' not in args and root == DEFAULT_ROOT)
        return pid if script_found and root_matches and b'run' in args else None
    except (FileNotFoundError, ValueError, KeyError, ProcessLookupError):
        return None


@contextmanager
def single_writer(root):
    with (root/'campaign.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise RuntimeError('Another campaign runner holds the lock') from e
        yield


class Campaign:
    def __init__(self, root, config, *, session=None, clock=time.time):
        self.root = checked_root(root)
        self.config = config
        self.sources = {s['id']: s for s in config['sources'] if s.get('enabled', True)}
        BeautifulSoup('', 'lxml')  # Fail startup if the configured HTML parser is unavailable.
        if not self.sources: raise ValueError('No enabled public sources configured')
        if len(self.sources) != len([s for s in config['sources'] if s.get('enabled', True)]):
            raise ValueError('Duplicate source IDs')
        for s in self.sources.values():
            if s['adapter'] not in {'varbi', 'reachmee', 'workday', 'academicworks', 'euraxess', 'fr_pass', 'jobbnorge'}:
                raise ValueError('No pure Python source adapter: '+s['adapter'])
            self.check_url(s, s.get('url') or s['endpoint']+'/jobs')
            if ref := s.get('admission_evidence'):
                path = Path(ref['path'])
                if not path.is_absolute() or sha256(path.read_bytes()).hexdigest() != ref['sha256']:
                    raise ValueError('Source admission evidence changed: '+s['id'])
        self.clock = clock
        self.session = session or requests.Session()
        self.session.headers.update({'User-Agent': 'BenefitLens-Python/1.0 public student-support research',
                                     'Accept': 'application/json,text/html;q=0.9'})
        self.db = sqlite3.connect(self.root/'frontier.sqlite', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA temp_store=MEMORY')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS jobs(
          id INTEGER PRIMARY KEY, source TEXT NOT NULL, day TEXT NOT NULL,
          stage TEXT NOT NULL, key TEXT NOT NULL, host TEXT NOT NULL, payload TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
          due REAL NOT NULL DEFAULT 0, capture TEXT, reason TEXT, updated REAL NOT NULL,
          UNIQUE(source,day,stage,key));
        CREATE INDEX IF NOT EXISTS queue_due ON jobs(state,due,id);
        CREATE INDEX IF NOT EXISTS detail_history ON jobs(source,key,day,state) WHERE stage='detail';
        CREATE TABLE IF NOT EXISTS captures(
          id INTEGER PRIMARY KEY, day TEXT NOT NULL, key TEXT NOT NULL,
          capture TEXT NOT NULL, created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS capture_reuse ON captures(day,key,id);
        CREATE TABLE IF NOT EXISTS hosts(
          host TEXT PRIMARY KEY, next_at REAL NOT NULL, blocked_until REAL NOT NULL DEFAULT 0,
          reason TEXT);
        CREATE TABLE IF NOT EXISTS observations(
          day TEXT NOT NULL, identity TEXT NOT NULL, source TEXT NOT NULL, job INTEGER NOT NULL,
          row_ref TEXT NOT NULL, application TEXT NOT NULL, parent TEXT NOT NULL,
          state TEXT NOT NULL, PRIMARY KEY(day,identity));
        CREATE INDEX IF NOT EXISTS observed_app ON observations(day,application);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at REAL NOT NULL,
          kind TEXT NOT NULL, job INTEGER, data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS events_kind_at ON events(kind,at);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS baseline(identity TEXT PRIMARY KEY,application TEXT,parent TEXT);
        CREATE TABLE IF NOT EXISTS terms(source TEXT NOT NULL,term TEXT NOT NULL,evidence TEXT NOT NULL,
          PRIMARY KEY(source,term));
        CREATE TABLE IF NOT EXISTS listing_pages(day TEXT NOT NULL,source TEXT NOT NULL,
          stream TEXT NOT NULL,signature TEXT NOT NULL,job INTEGER NOT NULL,
          PRIMARY KEY(day,source,stream,signature));
        CREATE TABLE IF NOT EXISTS source_turns(source TEXT PRIMARY KEY,last_step REAL NOT NULL);
        ''')
        self.db.commit()

    def close(self):
        self.db.close()

    def event(self, kind, job=None, **data):
        self.db.execute('INSERT INTO events(at,kind,job,data) VALUES(?,?,?,?)',
                        (self.clock(), kind, job, encoded(data)))

    def check_url(self, source, url):
        u = urlsplit(url)
        hosts = set(source.get('allowed_hosts', []))
        if u.scheme != 'https' or u.hostname not in hosts or u.username or u.password or u.port not in (None, 443):
            raise ValueError('URL outside configured public HTTPS origins: '+url)
        if re_auth_path(u.path):
            raise ValueError('Authenticated/form endpoint is not a capture target')

    def enqueue(self, source, day, stage, payload):
        self.check_url(self.sources[source], payload['url'])
        key = request_key(payload)
        requests_needed = payload.get('candidate', {}).get('support_requests', {})
        if requests_needed:
            deps = {}
            for role, request in requests_needed.items():
                self.check_url(self.sources[source], request['url'])
                if request.get('method') != 'GET' or request.get('body') is not None:
                    raise ValueError('Supporting original must be a public GET')
                deps[role] = {**request, 'key': request_key(request)}
            payload = {**payload, 'capture_dependencies': deps}
        self.db.execute('INSERT OR IGNORE INTO jobs(source,day,stage,key,host,payload,updated) VALUES(?,?,?,?,?,?,?)',
                        (source, day, stage, key, urlsplit(payload['url']).hostname, encoded(payload), self.clock()))

    def seed(self, day):
        seed_key = day+':'+sha256(encoded(list(self.sources.values())).encode()).hexdigest()
        previous = self.db.execute("SELECT value FROM meta WHERE key='seed_key'").fetchone()
        if previous and previous['value'] == seed_key: return
        # Preserve unfinished pagination at midnight. Revisit the observed parent
        # page of every unfinished detail instead of reusing yesterday's listing.
        for old in self.db.execute("SELECT * FROM jobs WHERE day<>? AND state IN ('pending','running') ORDER BY id", (day,)).fetchall():
            if old['source'] not in self.sources: continue
            payload = json.loads(old['payload'])
            if old['stage'] == 'listing':
                self.enqueue(old['source'], day, 'listing', payload)
            else:
                listing = payload['candidate']['listing']
                self.enqueue(old['source'], day, 'listing', {
                    'url': listing.get('request_url') or listing['url'],
                    'method': listing.get('method', 'GET'),
                    'body': listing.get('request_body')})
        # Old detail evidence cannot produce a same-day verified claim.
        self.db.execute("UPDATE jobs SET state='stale',reason='new_KST_capture_day',updated=? WHERE day<>? AND state IN ('pending','running')", (self.clock(), day))
        for sid, s in self.sources.items():
            if s['adapter'] == 'workday':
                for term in s.get('seed_terms', [s.get('search', '')]):
                    self.db.execute('INSERT OR IGNORE INTO terms VALUES(?,?,?)', (sid, term, encoded({'kind': 'configured_seed'})))
                for row in self.db.execute('SELECT term FROM terms WHERE source=?', (sid,)):
                    self.enqueue_term(sid, day, row['term'])
                continue
            for seed_url in s.get('seed_urls', [s['url']]):
                self.enqueue(sid, day, 'listing', {'url': seed_url, 'method': 'GET'})
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('seed_key',?)", (seed_key,))
        self.db.commit()

    def enqueue_term(self, sid, day, term):
        source = self.sources[sid]
        self.enqueue(sid, day, 'listing', {'url': source['endpoint']+'/jobs', 'method': 'POST',
                     'body': {'appliedFacets': {}, 'limit': 20, 'offset': 0, 'searchText': term}})

    def expand_terms(self, sid, day, row):
        if self.sources[sid]['adapter'] != 'workday': return
        for evidence in attested_search_terms(row):
            before = self.db.total_changes
            self.db.execute('INSERT OR IGNORE INTO terms VALUES(?,?,?)', (sid, evidence['term'], encoded(evidence)))
            if self.db.total_changes > before:
                self.enqueue_term(sid, day, evidence['term'])
                self.event('dictionary_expanded', term=evidence['term'], source=sid, evidence=evidence)

    def recover(self):
        # Call only while holding the runner lock.
        self.db.execute("UPDATE jobs SET state='pending',reason='recovered_after_interruption',updated=? WHERE state='running'", (self.clock(),))
        self.db.commit()

    def import_capture(self, cap, method='GET', body=None):
        day = datetime.fromisoformat(cap['fetched_at']).astimezone(KST).date().isoformat()
        if day != today():
            return
        cap = dict(cap)
        cap.setdefault('url', cap.get('request_url'))
        if 'text_path' not in cap:
            content = read_source_bytes(cap['raw_path'], storage=cap.get('raw_storage'))
            text = content if content.lstrip().startswith((b'{', b'[')) else (BeautifulSoup(content, 'lxml').get_text('\n', strip=True)+'\n').encode()
            blob = store_source_bytes(text, self.root/'blobs', suffix='.txt')
            cap.update(text_path=blob['path'], text_sha256=blob['sha256'], text_storage=blob['storage'])
        for name in ('raw', 'text'):
            raw = read_source_bytes(cap[name+'_path'], storage=cap.get(name+'_storage'))
            if sha256(raw).hexdigest() != cap[name+'_sha256']:
                raise ValueError('Imported source hash mismatch')
        payload = {'url': cap.get('request_url') or cap['url'], 'method': method, 'body': body}
        key = request_key(payload)
        if not self.db.execute('SELECT 1 FROM captures WHERE day=? AND key=?', (day, key)).fetchone():
            self.db.execute('INSERT INTO captures(day,key,capture,created) VALUES(?,?,?,?)', (day, key, encoded(cap), self.clock()))

    def blocked(self, host, reason, seconds):
        until = self.clock()+seconds
        self.db.execute('INSERT INTO hosts(host,next_at,blocked_until,reason) VALUES(?,?,?,?) ON CONFLICT(host) DO UPDATE SET blocked_until=excluded.blocked_until,reason=excluded.reason',
                        (host, until, until, reason))

    def network_budget(self, day):
        start = datetime.fromisoformat(day).replace(tzinfo=KST)
        end = start+timedelta(days=1)
        count = self.db.execute("SELECT COUNT(*) FROM events WHERE kind='http' AND at>=? AND at<?",
                                (start.timestamp(), end.timestamp())).fetchone()[0]
        maximum = self.config.get('daily_request_limits_kst', {}).get(day, self.config.get('daily_request_limit', 10000))
        result = {'used': count, 'limit': maximum, 'block_reason': None, 'resume_at_kst': None}
        if count >= maximum:
            result.update(block_reason='daily_request_limit', resume_at_kst=end.isoformat())
        elif self.db.execute("SELECT COALESCE(SUM(CAST(json_extract(data,'$.stored_bytes') AS INTEGER)),0) FROM events WHERE kind='stored'").fetchone()[0] >= self.config.get('max_stored_bytes', 5*1024**3):
            result.update(block_reason='campaign_storage_budget')
        return result

    def latest_capture(self, job):
        cap = self.db.execute('SELECT capture FROM captures WHERE day=? AND key=? ORDER BY id DESC LIMIT 1', (job['day'], job['key'])).fetchone()
        return json.loads(cap['capture']) if cap else None

    def cached_capture(self, job):
        value = self.latest_capture(job)
        if value and value.get('status', value.get('http_status')) == 200:
            read_source_bytes(value['raw_path'], storage=value.get('raw_storage'))
            return value
        return None

    def verify_robots(self, job, source, url):
        host = urlsplit(url).hostname
        accept = 'text/plain,*/*;q=0.1'
        request = {'url': 'https://'+host+'/robots.txt', 'method': 'GET', 'accept': accept}
        child = {**dict(job), 'key': request_key(request)}
        cap = self.latest_capture(child)
        status = cap.get('status', cap.get('http_status')) if cap else None
        if status == 406 and cap.get('request_accept') == accept:
            raise Hold('robots_policy_http406')
        if status not in {200, 404, 410}:
            h = self.db.execute('SELECT next_at,blocked_until,reason FROM hosts WHERE host=?', (host,)).fetchone()
            if h and h['blocked_until'] > self.clock() and h['reason'] != 'HTTP429_backoff':
                raise Hold('Robots host is blocked: '+host)
            if h and (wait := max(h['next_at'], h['blocked_until'])-self.clock()) > 0:
                raise ResourcePause('robots_host_pacing', wait)
            try:
                cap = self.fetch(child, request, {**source, 'robots_preflight': False})
                status = 200
            except Hold as exc:
                if str(exc) not in {'HTTP404', 'HTTP410'}:
                    raise
                status = int(str(exc)[4:])
        if status == 200:
            raw = read_source_bytes(cap['raw_path'], storage=cap.get('raw_storage'))
            if len(raw) > 512*1024 or raw.lstrip().startswith(b'<'):
                raise Hold('robots_policy_unusable')
            parser = RobotFileParser(request['url'])
            parser.parse(raw.decode('utf-8-sig').splitlines())
            if not parser.can_fetch('BenefitLens-Python', url):
                raise Hold('robots_policy_disallows_source_path')
        h = self.db.execute('SELECT next_at FROM hosts WHERE host=?', (host,)).fetchone()
        if h and (wait := h['next_at']-self.clock()) > 0:
            raise ResourcePause('robots_host_pacing', wait)

    def fetch_support(self, job, payload, source):
        captured = {}
        for role, request in payload.get('capture_dependencies', {}).items():
            child = {**dict(job), 'key': request['key']}
            if cap := self.cached_capture(child):
                captured[role] = cap
                continue
            host = urlsplit(request['url']).hostname
            h = self.db.execute('SELECT next_at,blocked_until,reason FROM hosts WHERE host=?', (host,)).fetchone()
            if h and h['blocked_until'] > self.clock() and h['reason'] != 'HTTP429_backoff':
                raise Hold('Supporting source is blocked: '+host)
            if h and (wait := max(h['next_at'], h['blocked_until'])-self.clock()) > 0:
                raise ResourcePause('support_host_pacing', wait)
            captured[role] = self.fetch(child, request, source)
        return captured

    def fetch(self, job, payload, source):
        # Reuse original bytes only on the same KST date, preserving provenance.
        if cap := self.cached_capture(job):
            return cap
        if source.get('robots_preflight'):
            self.verify_robots(job, source, payload['url'])
        limit = self.config.get('max_response_bytes', 8*1024*1024)
        if shutil.disk_usage(self.root).free < 256*1024*1024+limit*2:
            raise OSError('Cache disk reserve reached')
        blocked = self.network_budget(job['day'])['block_reason']
        if blocked:
            raise ResourcePause(blocked, 3600)
        url = payload['url']; host = urlsplit(url).hostname
        response = None
        canonical_redirect = None
        for _ in range(6):
            self.check_url(source, url)
            if response is not None:
                response.close()
                raise ValueError('Redirect held for origin/route review; no automatic redirected fetch')
            self.event('http', job['id'], method=payload.get('method', 'GET'), url=url)
            # Persist pacing BEFORE the request, so interruption cannot erase it.
            self.db.execute('INSERT INTO hosts(host,next_at) VALUES(?,?) ON CONFLICT(host) DO UPDATE SET next_at=excluded.next_at',
                            (host, self.clock()+self.config.get('rate_seconds', 2)))
            self.db.commit()
            response = self.session.request(payload.get('method', 'GET'), url,
                                            json=payload.get('body'), timeout=(10, 25),
                                            allow_redirects=False, stream=True,
                                            headers={'Accept': payload['accept']} if payload.get('accept') else None)
            if 300 <= response.status_code < 400:
                if source['adapter'] == 'jobbnorge' and payload.get('method', 'GET') == 'GET' and canonical_redirect is None:
                    from benefitlens_campaign_jobbnorge import canonical_notice_redirect
                    canonical_redirect = canonical_notice_redirect(url, response.headers.get('Location'))
                    if canonical_redirect:
                        self.event('canonical_notice_redirect', job['id'], url=url, location=canonical_redirect)
                        response.close(); response = None
                        h = self.db.execute('SELECT next_at FROM hosts WHERE host=?', (host,)).fetchone()
                        if h:
                            time.sleep(max(0, h['next_at']-self.clock()))
                        if source.get('robots_preflight'):
                            self.verify_robots(job, source, canonical_redirect)
                        if blocked := self.network_budget(job['day'])['block_reason']:
                            raise ResourcePause(blocked, 3600)
                        url = canonical_redirect
                        continue
                self.event('redirect_held', job['id'], url=url, location=response.headers.get('Location'))
                continue
            break
        assert response is not None
        try:
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > limit:
                    raise ValueError('Response exceeds decoded byte limit')
            raw = bytes(content)
            suffix = '.json' if raw.lstrip().startswith((b'{', b'[')) else ('.xml' if raw.lstrip().startswith((b'<?xml', b'<rss')) else '.html')
            rr = store_source_bytes(raw, self.root/'blobs', suffix=suffix)
            soup = BeautifulSoup(raw, 'lxml') if suffix == '.html' else None
            if soup:
                for node in soup.select('script,style,noscript'): node.decompose()
                text = soup.get_text('\n', strip=True)+'\n'
            else:
                text = raw.decode('utf-8')
            tr = store_source_bytes(text.encode(), self.root/'blobs', suffix='.txt')
            cap = {'url': payload['url'], 'request_url': payload['url'], 'final_url': response.url,
                   'method': payload.get('method', 'GET'), 'request_body': payload.get('body'),
                   'request_accept': payload.get('accept'),
                   'canonical_notice_redirect': canonical_redirect,
                   'status': response.status_code, 'http_status': response.status_code,
                   'fetched_at': datetime.now(timezone.utc).isoformat(),
                   'raw_path': rr['path'], 'raw_sha256': rr['sha256'], 'raw_storage': rr['storage'],
                   'text_path': tr['path'], 'text_sha256': tr['sha256'], 'text_storage': tr['storage']}
            self.db.execute('INSERT INTO captures(day,key,capture,created) VALUES(?,?,?,?)', (job['day'], job['key'], encoded(cap), self.clock()))
            self.event('stored', job['id'], stored_bytes=rr['storage']['stored_bytes']+tr['storage']['stored_bytes'])
            if response.status_code == 403:
                self.blocked(host, 'HTTP403_manual_source_review', 10*365*86400)
                raise Hold('HTTP403_manual_source_review')
            if response.status_code == 429:
                delay = retry_after(response.headers.get('Retry-After'), self.clock())
                self.blocked(host, 'HTTP429_backoff', delay)
                raise ResourcePause('HTTP429_backoff', delay)
            if response.status_code in (202, 401) or (response.status_code == 200 and not raw.strip()):
                self.blocked(host, 'unusable_or_auth_boundary', 86400)
                raise Hold('unusable_or_auth_boundary')
            if response.status_code >= 500:
                raise requests.HTTPError('Transient HTTP '+str(response.status_code))
            if response.status_code != 200:
                raise Hold('HTTP'+str(response.status_code))
            if b'\xe8\xaf\xb7\xe7\x99\xbb\xe5\xbd\x95\xe5\x90\x8e\xe6\x9f\xa5\xe7\x9c\x8b' in raw or b'verify you are human' in raw.lower():
                self.blocked(host, 'login_or_challenge_boundary', 10*365*86400)
                raise Hold('login_or_challenge_boundary')
            return cap
        finally:
            response.close()

    def finish(self, job, state, reason=None, cap=None):
        self.db.execute('UPDATE jobs SET state=?,reason=?,capture=?,updated=? WHERE id=?',
                        (state, reason, encoded(cap) if cap else None, self.clock(), job['id']))
        self.event(state, job['id'], reason=reason)

    def repeated_listing(self, job, payload, candidates):
        if not candidates: return False
        stream, signature = listing_fingerprint(payload, candidates)
        key = (job['day'], job['source'], stream, signature)
        previous = self.db.execute('SELECT job FROM listing_pages WHERE day=? AND source=? AND stream=? AND signature=?', key).fetchone()
        if previous and previous['job'] != job['id']:
            self.event('pagination_repeat_detected', job['id'], original_job=previous['job'],
                       stream=stream, signature=signature, candidate_count=len(candidates))
            return True
        self.db.execute('INSERT OR IGNORE INTO listing_pages VALUES(?,?,?,?,?)', (*key, job['id']))
        return False

    def baseline_match(self, identity, application):
        match = self.db.execute('SELECT identity FROM baseline WHERE identity=? OR application=?', (identity, application)).fetchone()
        if match:
            return match['identity']
        key = application_identity(application)
        if key:
            prefix = 'https://%.jobbnorge.no/%' if key[0] == 'JOBBNORGE' else f'https://{urlsplit(application).hostname}/%'
            return next((r['identity'] for r in self.db.execute('SELECT identity,application FROM baseline WHERE application LIKE ?', (prefix,))
                         if application_identity(r['application']) == key), None)
        return None

    def observed_alias(self, day, identity, application):
        match = self.db.execute('SELECT identity FROM observations WHERE day=? AND application=? AND identity<>?', (day, application, identity)).fetchone()
        if match:
            return match['identity']
        key = application_identity(application)
        if key:
            prefix = 'https://%.jobbnorge.no/%' if key[0] == 'JOBBNORGE' else f'https://{urlsplit(application).hostname}/%'
            return next((r['identity'] for r in self.db.execute('SELECT identity,application FROM observations WHERE day=? AND identity<>? AND application LIKE ?', (day, identity, prefix))
                         if application_identity(r['application']) == key), None)
        return None

    def step(self, day=None):
        day = day or today()
        # A blocked host never prevents work on an independent configured source.
        placeholders = ','.join('?' for _ in self.sources)
        turn_row = self.db.execute("SELECT value FROM meta WHERE key='scheduler_turn'").fetchone()
        turn = int(turn_row['value']) if turn_row else 0
        preferred = 'listing' if turn % 4 == 3 else 'detail'
        offline = self.network_budget(day)['block_reason'] is not None
        # A global limit must not claim, retry or change due times of each HTTP job.
        # A same-day successful capture can still be parsed without a request.
        eligible = """(SELECT COALESCE(json_extract(c.capture,'$.status'),json_extract(c.capture,'$.http_status'))
                         FROM captures c WHERE c.day=j.day AND c.key=j.key ORDER BY c.id DESC LIMIT 1)=200
                      AND NOT EXISTS (SELECT 1 FROM json_each(j.payload,'$.capture_dependencies') d WHERE COALESCE(
                         (SELECT COALESCE(json_extract(c.capture,'$.status'),json_extract(c.capture,'$.http_status'))
                          FROM captures c WHERE c.day=j.day AND c.key=json_extract(d.value,'$.key') ORDER BY c.id DESC LIMIT 1),0)<>200)""" if offline else 'COALESCE(h.next_at,0)<=? AND COALESCE(h.blocked_until,0)<=?'
        pacing = () if offline else (self.clock(), self.clock())
        priority = ''
        priority_params = ()
        # Three of each four rounds favor detail work with a validator and no
        # completed attempt in an earlier cohort. The fourth retains normal rotation.
        if not offline and self.config.get('focus_new_validatable_details') and turn % 16 < 12:
            deferred = tuple(sid for sid, s in self.sources.items() if s['adapter'] == 'academicworks')
            if deferred:
                priority += 'CASE WHEN j.stage=\'detail\' AND j.source IN ('+','.join('?' for _ in deferred)+') THEN 1 ELSE 0 END,'
                priority_params = deferred
            priority += """CASE WHEN j.stage='detail' AND EXISTS (SELECT 1 FROM jobs old
                WHERE old.source=j.source AND old.key=j.key AND old.stage='detail' AND old.day<j.day
                AND old.state IN ('held','machine_verified','known_baseline','duplicate_application')) THEN 1 ELSE 0 END,"""
        job = self.db.execute(f"""SELECT j.* FROM jobs j LEFT JOIN hosts h ON j.host=h.host
            LEFT JOIN source_turns s ON j.source=s.source
            WHERE j.state='pending' AND j.day=? AND j.due<=?
            AND {eligible}
            AND j.source IN ({placeholders})
            ORDER BY CASE WHEN j.stage=? THEN 0 ELSE 1 END,{priority}COALESCE(s.last_step,0),j.id LIMIT 1""",
            (day, self.clock(), *pacing, *self.sources, preferred, *priority_params)).fetchone()
        if job is None:
            return False
        payload = json.loads(job['payload']); source = self.sources[job['source']]
        self.db.execute("UPDATE jobs SET state='running',attempts=attempts+1,updated=? WHERE id=?", (self.clock(), job['id']))
        self.db.execute('INSERT OR REPLACE INTO source_turns VALUES(?,?)', (job['source'], self.clock()))
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('scheduler_turn',?)", (str(turn+1),))
        self.db.commit()
        cap = None
        try:
            if not offline and job['stage']=='detail' and source['adapter']=='jobbnorge' and source.get('robots_preflight'):
                for role in ('page','template'):
                    req = payload['capture_dependencies'][role]
                    if not self.cached_capture({**dict(job), 'key': req['key']}):
                        self.verify_robots(job, source, req['url'])
            cap = self.fetch(job, payload, source)
            if job['stage'] == 'listing':
                candidates, pages = listing_jobs(source, cap, payload)
                if self.repeated_listing(job, payload, candidates):
                    self.finish(job, 'pagination_repeat', 'Repeated native-ID set; next page stopped, catalogue tail not independently proved complete', cap)
                    self.db.commit()
                    return True
                for candidate in candidates:
                    self.enqueue(job['source'], day, 'detail', {'url': candidate['url'], 'method': 'GET', 'candidate': candidate})
                for page in pages:
                    self.enqueue(job['source'], day, 'listing', page)
                self.event('listed', job['id'], candidates=len(candidates), next_pages=len(pages))
                self.finish(job, 'done', cap=cap)
            else:
                if source['adapter'] == 'jobbnorge':
                    from benefitlens_campaign_jobbnorge import parse_detail as parse_jobbnorge
                    support = self.fetch_support(job, payload, source)
                    row, held = parse_jobbnorge(source, cap, payload['candidate'], datetime.fromisoformat(day).date(), self.root/'blobs', support)
                else:
                    row, held = parse_detail(source, cap, payload['candidate'], datetime.fromisoformat(day).date(), self.root/'blobs')
                if held:
                    self.event('parsed_fields_unapproved', job['id'], **held)
                    self.finish(job, 'held', held['reason'], cap)
                else:
                    self.expand_terms(job['source'], day, row)
                    identity = encoded(list(source_id(row))); application = app_key(row['application_url'])
                    role, _ = role_key(row)
                    parent = encoded([issuer_key(row), category(row), role])
                    alias = self.observed_alias(day, identity, application)
                    baseline = self.baseline_match(identity, application)
                    state = 'duplicate_application' if alias else 'known_baseline' if baseline else 'machine_verified'
                    blob = store_source_bytes((encoded(row)+'\n').encode(), self.root/'rows', suffix='.json')
                    self.event('stored', job['id'], stored_bytes=blob['storage']['stored_bytes'])
                    self.db.execute('INSERT INTO observations(day,identity,source,job,row_ref,application,parent,state) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(day,identity) DO UPDATE SET job=excluded.job,row_ref=excluded.row_ref,state=excluded.state',
                                    (day, identity, job['source'], job['id'], encoded(blob), application, parent, state))
                    self.finish(job, state, cap=cap)
        except ResourcePause as e:
            self.db.execute("UPDATE jobs SET state='pending',due=?,reason=?,updated=?,attempts=attempts-? WHERE id=?",
                            (self.clock()+e.seconds, str(e), self.clock(),
                             int(str(e) in {'support_host_pacing', 'robots_host_pacing'}), job['id']))
            self.event('resource_pause', job['id'], reason=str(e), seconds=e.seconds)
        except (requests.RequestException, TimeoutError) as e:
            attempts = job['attempts']+1
            if attempts >= 5:
                self.finish(job, 'failed', type(e).__name__+': '+str(e), cap)
            else:
                self.db.execute("UPDATE jobs SET state='pending',due=?,reason=?,updated=? WHERE id=?", (self.clock()+min(3600, 30*2**attempts), type(e).__name__+': '+str(e), self.clock(), job['id']))
                self.event('retry', job['id'], attempt=attempts, error=str(e))
        except (ValueError, KeyError, TypeError, AttributeError, IndexError, Hold) as e:
            self.finish(job, 'held', type(e).__name__+': '+str(e), cap)
        self.db.commit()
        return True

    def status(self):
        day = today()
        rows = list(self.db.execute('SELECT state,COUNT(*) n FROM jobs WHERE day=? GROUP BY state', (day,)))
        counts = {r['state']: r['n'] for r in rows}
        sources = {}
        for sid in self.sources:
            sources[sid] = {r['state']: r['n'] for r in self.db.execute('SELECT state,COUNT(*) n FROM jobs WHERE day=? AND source=? GROUP BY state', (day, sid))}
        obs = self.db.execute("""SELECT COUNT(*) n,COUNT(DISTINCT CASE WHEN NOT EXISTS
            (SELECT 1 FROM baseline b WHERE b.parent=o.parent) THEN parent END) p
            FROM observations o WHERE day=? AND state='machine_verified'""", (day,)).fetchone()
        current = self.db.execute("SELECT COUNT(*) FROM observations WHERE day=? AND state IN ('machine_verified','known_baseline')", (day,)).fetchone()[0]
        baseline = self.db.execute("SELECT value FROM meta WHERE key='baseline_ref'").fetchone()
        budget = self.network_budget(day)
        try:
            handoff = json.loads((self.root/'verified-handoffs'/day/'summary.json').read_text())
        except (OSError, ValueError):
            handoff = None
        return {'version': VERSION, 'at': datetime.now(timezone.utc).isoformat(), 'capture_day_kst': day,
                'pid': live_pid(self.root), 'root': str(self.root), 'physical_root': str(self.root.resolve()),
                'mode': 'python_only_no_agent_or_LLM_calls', 'jobs': counts, 'sources': sources,
                'enabled_source_count': len(self.sources),
                'jobs_by_stage': [dict(r) for r in self.db.execute('SELECT stage,state,COUNT(*) n FROM jobs WHERE day=? GROUP BY stage,state', (day,))],
                'detail_candidate_urls': self.db.execute("SELECT COUNT(DISTINCT json_extract(payload,'$.url')) FROM jobs WHERE day=? AND stage='detail'", (day,)).fetchone()[0],
                'scheduler_policy': ('three detail turns per listing turn; first-time details with validators prioritized in three of four rounds'
                                     if self.config.get('focus_new_validatable_details') else 'three detail turns per listing turn, sources rotated by last service'),
                'new_machine_verified_variants': obs['n'], 'new_exact_parent_candidates': obs['p'],
                'current_rule_passed_variants_in_this_runner': current,
                'search_dictionary_terms': self.db.execute('SELECT COUNT(*) FROM terms').fetchone()[0],
                'dictionary_policy': 'configured seeds plus finite exact source phrases, no agent',
                'independently_approved_new_opportunities': 0, 'semantic_distinct_opportunities': None,
                'verified_handoff_today': handoff,
                'baseline_ref': json.loads(baseline['value']) if baseline else None,
                'target': self.config.get('target', 50000), 'target_achieved': False,
                'http_budget': budget,
                'frontier': ('cache_only_global_limit' if budget['block_reason'] else 'pending_or_backoff') if counts.get('pending') else 'cycle_exhausted_waiting_next_KST_day',
                'held_reasons': [dict(r) for r in self.db.execute("SELECT reason,COUNT(*) n FROM jobs WHERE day=? AND state IN ('held','failed') GROUP BY reason ORDER BY n DESC LIMIT 15", (day,))],
                'host_controls': [dict(r) for r in self.db.execute('SELECT * FROM hosts WHERE blocked_until>?', (self.clock(),))],
                'latest_event': dict(self.db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 1').fetchone() or {}),
                'production_database_writes': 0}

    def next_delay(self):
        if self.network_budget(today())['block_reason']:
            # Checkpoint health at least once a minute while the HTTP frontier is paused.
            return 30
        placeholders = ','.join('?' for _ in self.sources)
        at = self.db.execute(f"""SELECT MIN(MAX(j.due,COALESCE(h.next_at,0),COALESCE(h.blocked_until,0)))
            FROM jobs j LEFT JOIN hosts h ON j.host=h.host
            WHERE j.state='pending' AND j.day=? AND j.source IN ({placeholders})""",
            (today(), *self.sources)).fetchone()[0]
        return 10 if at is None else min(10, max(.2, at-self.clock()))

    def export(self):
        # Streaming gzip JSONL export, atomically replace only the daily pointer.
        import gzip
        out = self.root/'exports'; out.mkdir(exist_ok=True)
        name = out/(today()+'-current-rules-passed.jsonl.gz'); temp = name.with_suffix('.tmp')
        count = 0
        with temp.open('wb') as f, gzip.GzipFile(fileobj=f, mode='wb', filename='', mtime=0) as g:
            for r in self.db.execute("SELECT row_ref FROM observations WHERE day=? AND state IN ('machine_verified','known_baseline') ORDER BY identity", (today(),)):
                ref = json.loads(r['row_ref']); raw = read_source_bytes(ref['path'], storage=ref['storage'])
                if sha256(raw).hexdigest() != ref['sha256']: raise ValueError('Export source hash mismatch')
                g.write(raw); count += 1
        os.replace(temp, name)
        return {'path': str(name), 'rows': count, 'sha256': sha256(name.read_bytes()).hexdigest(),
                'approval': 'automated_rules_only', 'format': 'jsonl+gzip'}


class Hold(Exception):
    pass


class ResourcePause(Exception):
    def __init__(self, reason, seconds):
        super().__init__(reason); self.seconds = seconds


def re_auth_path(path):
    return any(x in path.lower() for x in ('/apply/', '/users/sign_in', '/login', '/what:login'))


def retry_after(value, now):
    try:
        delay = float(value)
    except (ValueError, TypeError):
        try: delay = parsedate_to_datetime(value).timestamp()-now
        except (ValueError, TypeError): delay = 3600
    return max(60, delay)


def load_config(path):
    value = json.loads(path.read_text())
    if value.get('version') != VERSION: raise ValueError('Unsupported campaign config')
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['run', 'start', 'stop', 'status', 'export'])
    p.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    p.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    p.add_argument('--max-jobs', type=int, default=0, help='0: remain running and refresh next KST day')
    p.add_argument('--max-seconds', type=int, default=0)
    args = p.parse_args(); root = checked_root(args.root)
    if args.command == 'stop':
        pid = live_pid(root)
        if pid: os.kill(pid, signal.SIGTERM)
        print(encoded({'signal_sent': bool(pid), 'pid': pid})); return
    if args.command == 'start':
        pid = live_pid(root)
        if pid: print(encoded({'already_running': True, 'pid': pid})); return
        command = [sys.executable, str(Path(__file__).resolve()), 'run', '--root', str(root), '--config', str(args.config.resolve())]
        if args.max_jobs: command += ['--max-jobs', str(args.max_jobs)]
        if args.max_seconds: command += ['--max-seconds', str(args.max_seconds)]
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', TMPDIR=str(root))
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=open(root/'startup-error.log', 'a'), start_new_session=True,
                                 cwd=str(Path(__file__).resolve().parents[1]), env=env)
        for _ in range(50):
            time.sleep(.1)
            if live_pid(root): print(encoded({'started': True, 'pid': child.pid, 'root': str(root)})); return
            if child.poll() is not None: raise RuntimeError('Runner exited; see startup-error.log')
        raise RuntimeError('Runner startup not confirmed')
    config = load_config(args.config)
    if args.command == 'status':
        campaign = Campaign(root, config)
        try: print(json.dumps(campaign.status(), ensure_ascii=False, indent=2))
        finally: campaign.close()
        return
    with single_writer(root):
        campaign = Campaign(root, config)
        if args.command == 'export':
            try: print(encoded(campaign.export()))
            finally: campaign.close()
            return
        logger = logging.getLogger('benefitlens-campaign'); logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(root/'runner.log', maxBytes=5*1024*1024, backupCount=2)
        logger.addHandler(handler)
        stop = False
        def stopping(signum, frame):
            nonlocal stop
            stop = True
        signal.signal(signal.SIGTERM, stopping); signal.signal(signal.SIGINT, stopping)
        code_hash = sha256(Path(__file__).read_bytes()).hexdigest()
        atomic_json(root/'runtime.json', {'pid': os.getpid(), 'started_at': datetime.now(timezone.utc).isoformat(), 'code_sha256': code_hash, 'config_sha256': sha256(args.config.read_bytes()).hexdigest()})
        started = time.monotonic(); done = 0; last_export = 0
        try:
            campaign.recover()
            while not stop:
                campaign.seed(today())
                progressed = campaign.step()
                if progressed: done += 1
                state = campaign.status()
                state.update(worker_state='running' if progressed else 'waiting_network_budget' if state['http_budget']['block_reason'] else 'idle_waiting', heartbeat_pid=os.getpid())
                atomic_json(root/'status.json', state)
                if done-last_export >= 25 or (not progressed and done != last_export):
                    receipt = campaign.export(); atomic_json(root/'latest_export.json', receipt); last_export = done
                if progressed: logger.info(encoded({'at': state['at'], 'completed_steps': done, 'jobs': state['jobs']}))
                if args.max_jobs and done >= args.max_jobs: break
                if args.max_seconds and time.monotonic()-started >= args.max_seconds: break
                if not progressed:
                    until = time.monotonic()+campaign.next_delay()
                    while time.monotonic() < until:
                        if stop: break
                        time.sleep(min(.5, max(0, until-time.monotonic())))
        except Exception:
            logger.exception('Runner failed, persistent queue preserved')
            raise
        finally:
            receipt = campaign.export(); atomic_json(root/'latest_export.json', receipt)
            state = campaign.status(); state.update(worker_state='stopped', heartbeat_pid=None, pid=None)
            atomic_json(root/'status.json', state)
            campaign.close()
            (root/'runtime.json').unlink(missing_ok=True)


if __name__ == '__main__':
    main()
