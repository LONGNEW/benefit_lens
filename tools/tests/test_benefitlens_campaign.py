import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_benefitlens_campaign as runner
import benefitlens_campaign_adapters as adapters
from benefitlens_source_storage import store_source_bytes, read_source_bytes


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'CACHE', tmp_path)
    config = {'version': runner.VERSION, 'rate_seconds': 0,
              'sources': [{'id': 'one', 'adapter': 'academicworks', 'issuer': 'One',
                           'url': 'https://one.academicworks.com/',
                           'allowed_hosts': ['one.academicworks.com']}]}
    c = runner.Campaign(tmp_path/'run', config)
    yield c
    c.close()


def capture(c, url, body):
    raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    rr = store_source_bytes(raw, c.root/'blobs', suffix='.raw')
    text = adapters.plain(body) if isinstance(body, str) else json.dumps(body)
    tr = store_source_bytes((text+'\n').encode(), c.root/'blobs', suffix='.txt')
    return {'url': url, 'status': 200, 'fetched_at': datetime.now(timezone.utc).isoformat(),
            'raw_path': rr['path'], 'raw_sha256': rr['sha256'], 'raw_storage': rr['storage'],
            'text_path': tr['path'], 'text_sha256': tr['sha256'], 'text_storage': tr['storage']}


def test_restart_replays_committed_frontier_without_losing_next_page(campaign):
    c = campaign; c.seed(runner.today())
    first = capture(c, 'https://one.academicworks.com/', '<table><th><a href="/opportunities/17">Student award</a></th></table><a href="/?page=2">Next</a>')
    c.import_capture(first); c.db.commit()
    assert c.step()
    assert c.db.execute("SELECT COUNT(*) FROM jobs WHERE state='pending'").fetchone()[0] == 2
    c.db.execute("UPDATE jobs SET state='running' WHERE stage='detail'"); c.db.commit()
    root, config = c.root, c.config
    other = runner.Campaign(root, config)
    try:
        other.recover()
        assert other.db.execute("SELECT COUNT(*) FROM jobs WHERE state='pending'").fetchone()[0] == 2
        other.seed(runner.today())
        assert other.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 3
    finally: other.close()


def test_capture_cache_roundtrip_and_tamper_rejection(campaign):
    c = campaign; cap = capture(c, 'https://one.academicworks.com/', '<h1>Original</h1>')
    c.import_capture(cap); c.db.commit(); c.seed(runner.today())
    def no_network(*a, **kw): raise AssertionError('Unexpected network')
    c.session.request = no_network
    assert c.step()
    cap['raw_sha256'] = '0'*64
    with pytest.raises(ValueError, match='hash'): c.import_capture(cap)


def test_auth_and_off_origin_urls_rejected_before_request(campaign):
    c = campaign
    for url in ['http://one.academicworks.com/', 'https://other.org/',
                'https://one.academicworks.com/users/sign_in', 'https://x:y@one.academicworks.com/']:
        with pytest.raises(ValueError): c.enqueue('one', runner.today(), 'detail', {'url': url})


class Response:
    def __init__(self, status, body=b'', headers=None):
        self.status_code = status; self.body = body; self.headers = headers or {}
        self.url = 'https://one.academicworks.com/'; self.closed = False
    def iter_content(self, size): yield self.body
    def close(self): self.closed = True


@pytest.mark.parametrize('status,state,reason', [(403, 'held', 'HTTP403'), (202, 'held', 'unusable'), (401, 'held', 'auth_boundary'), (429, 'pending', 'HTTP429')])
def test_http_boundaries_persist_and_do_not_loop(campaign, status, state, reason):
    c = campaign; c.seed(runner.today())
    response = Response(status, headers={'Retry-After': '120'})
    c.session.request = lambda *a, **kw: response
    assert c.step()
    j = c.db.execute('SELECT * FROM jobs').fetchone()
    assert j['state'] == state and reason in j['reason']
    assert response.closed and not c.step()
    assert c.db.execute('SELECT blocked_until FROM hosts').fetchone()[0] > c.clock()


def test_transient_retry_has_persistent_backoff(campaign):
    c = campaign; c.seed(runner.today())
    def fail(*a, **kw): raise requests.Timeout('test timeout')
    c.session.request = fail
    assert c.step() and not c.step()
    j = c.db.execute('SELECT * FROM jobs').fetchone()
    assert j['attempts'] == 1 and j['state'] == 'pending' and j['due'] > c.clock()


def test_daily_boundary_stales_unfinished_tasks(campaign):
    c = campaign; c.seed('2000-01-01'); c.seed(runner.today())
    assert c.db.execute("SELECT COUNT(*) FROM jobs WHERE state='stale'").fetchone()[0] == 1


def test_single_writer_lock(campaign):
    with runner.single_writer(campaign.root):
        with pytest.raises(RuntimeError, match='lock'):
            with runner.single_writer(campaign.root): pass


@pytest.mark.parametrize('line', ['We support students currently enrolled at university.',
    'Candidates need not be currently enrolled.', 'Applicants must not be currently enrolled.',
    'Currently enrolled students are our customers.', 'Student status is preferred.'])
def test_customer_and_negation_not_student_applicant_proof(line):
    with pytest.raises(ValueError): adapters.student_quote([line])


def test_workday_strict_applicant_and_body_deadline():
    assert adapters.student_quote(['Applicants must currently be a university student.'])
    day = datetime.now().date()
    with pytest.raises(ValueError, match='backend'): adapters.workday_period(['endDate: 2099-01-01'], day)
    with pytest.raises(ValueError, match='Conflicting'):
        adapters.workday_period(['Deadline to Apply:', 'November 30, 2099', 'Application Deadline:', 'December 31, 2099'], day)


def test_workday_pagination_only_observed_external_paths(campaign):
    c = campaign
    cap = capture(c, 'https://one.academicworks.com/jobs', {'jobPostings': [{'externalPath': '/job/place/Student_JR17', 'title': 'Student', 'bulletFields': ['JR17']}], 'total': 2})
    source = {'adapter': 'workday', 'endpoint': 'https://one.academicworks.com', 'tenant': 'one', 'issuer': 'One'}
    candidates, pages = adapters.listing_jobs(source, cap, {'body': {'offset': 0, 'limit': 20}})
    assert candidates[0]['native_id'] == 'JR17' and pages[0]['body']['offset'] == 1


def test_ambiguous_aw_fields_are_held_not_counted(campaign):
    c = campaign; cap = capture(c, 'https://one.academicworks.com/opportunities/7', '<h1>Student award</h1><p>Deadline</p><p>01/02/2099</p>')
    row, held = adapters.parse_detail({'adapter': 'academicworks'}, cap, {}, datetime.now().date(), c.root/'blobs')
    assert row is None and held['extracted_fields']['deadline'] == '01/02/2099'
    assert c.status()['new_machine_verified_variants'] == 0


def test_application_aliases_preserve_native_query_and_remove_tracking():
    assert runner.app_key('https://a.org/apply?job_id=1&utm_source=x') == runner.app_key('https://a.org/apply?job_id=1')
    assert runner.app_key('https://a.org/apply?job_id=1') != runner.app_key('https://a.org/apply?job_id=2')


def test_size_limit_holds_and_closes_response(campaign):
    c = campaign; c.config['max_response_bytes'] = 3; c.seed(runner.today())
    response = Response(200, b'too long'); c.session.request = lambda *a, **kw: response
    c.step(); assert response.closed
    assert 'byte limit' in c.db.execute('SELECT reason FROM jobs').fetchone()[0]


def test_export_is_lossless_and_does_not_export_held(campaign):
    ref = campaign.export()
    assert ref['rows'] == 0 and read_source_bytes(ref['path']) == b''


def test_observation_dedup_and_baseline_alias_are_not_new_counts(campaign, monkeypatch):
    c = campaign; day = runner.today()
    def fake_parse(source, cap, candidate, as_of, blob_dir):
        return {**cap, 'source_family': 'test', 'source_id': 'provider',
                'native_id': candidate['native_id'], 'title': 'Physics student assistant',
                'issuer': 'University', 'official_url': candidate['url'],
                'application_url': candidate['application']}, None
    monkeypatch.setattr(runner, 'parse_detail', fake_parse)
    for i, app in [(1, 'https://a.org/apply/1'), (2, 'https://a.org/apply/1'), (3, 'https://a.org/apply/3')]:
        url = f'https://one.academicworks.com/opportunities/{i}'
        c.import_capture(capture(c, url, '<h1>Student</h1>'))
        c.enqueue('one', day, 'detail', {'url': url, 'method': 'GET', 'candidate': {'url': url, 'native_id': str(i), 'application': app}})
    c.db.execute('INSERT INTO baseline VALUES(?,?,?)', ('["old","3"]', 'https://a.org/apply/3', 'parent'))
    c.db.commit()
    while c.step(): pass
    states = {r['state']: r['n'] for r in c.db.execute('SELECT state,COUNT(*) n FROM observations GROUP BY state')}
    assert states == {'machine_verified': 1, 'duplicate_application': 1, 'known_baseline': 1}
    assert c.status()['new_machine_verified_variants'] == 1
    assert c.export()['rows'] == 2


def test_dictionary_expansion_requires_exact_source_phrase_and_converges(campaign):
    c = campaign; source = dict(c.sources['one'], adapter='workday', endpoint='https://one.academicworks.com', tenant='one')
    c.sources['one'] = source
    cap = capture(c, 'https://one.academicworks.com/opportunities/1', '<p>Applicants must currently be an undergraduate student for this internship.</p>')
    row = {**cap, 'official_url': cap['url']}
    c.expand_terms('one', runner.today(), row); c.db.commit()
    n = c.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
    assert n == 2
    c.expand_terms('one', runner.today(), row)
    assert c.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == n
    assert c.db.execute('SELECT COUNT(*) FROM terms').fetchone()[0] == 2


def test_rolling_review_is_not_rolling_application_acceptance():
    day = datetime.now().date()
    with pytest.raises(ValueError): adapters.workday_period(['Applications are reviewed on a rolling basis.'], day)
    quote, closing, _ = adapters.workday_period(['Applications will be accepted on an ongoing basis.'], day)
    assert closing is None and quote.startswith('Applications')


def test_one_blocked_host_does_not_starve_another(campaign):
    c = campaign
    c.sources['two'] = {**c.sources['one'], 'url': 'https://two.academicworks.com/', 'allowed_hosts': ['two.academicworks.com']}
    c.seed(runner.today()); c.blocked('one.academicworks.com', 'test boundary', 86400)
    cap = capture(c, 'https://two.academicworks.com/', '<h1>Empty catalogue</h1>')
    c.import_capture(cap); c.db.commit()
    assert c.step()
    assert c.db.execute("SELECT state FROM jobs WHERE source='two'").fetchone()[0] == 'done'


def test_day_rollover_retains_unfinished_page_and_refreshes_detail_parent(campaign):
    c = campaign
    old = '2000-01-01'
    c.enqueue('one', old, 'listing', {'url': 'https://one.academicworks.com/?page=99', 'method': 'GET'})
    c.enqueue('one', old, 'detail', {'url': 'https://one.academicworks.com/opportunities/7',
        'method': 'GET', 'candidate': {'listing': {'url': 'https://one.academicworks.com/?page=98'}}})
    c.db.commit(); c.seed(runner.today())
    jobs = [json.loads(r['payload']) for r in c.db.execute("SELECT payload FROM jobs WHERE day=? AND state='pending'", (runner.today(),))]
    assert {j['url'] for j in jobs} >= {'https://one.academicworks.com/?page=99', 'https://one.academicworks.com/?page=98'}
    assert all('candidate' not in j for j in jobs)
    count = c.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
    c.seed(runner.today()); assert c.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == count


def test_repeated_ids_stop_pagination_even_when_html_and_next_url_change(campaign):
    c = campaign; root = 'https://one.academicworks.com/'
    for page, url in [(1, root), (2, root+'?page=2')]:
        html = f'<h1>Page {page}</h1><a href="/opportunities/17">Student award</a><a href="?page={page+1}">Next</a>'
        c.import_capture(capture(c, url, html))
    c.import_capture(capture(c, root+'opportunities/17', '<h1>Student award</h1>'))
    c.seed(runner.today()); c.db.commit()
    def no_network(*a, **kw): raise AssertionError('Unexpected uncached request')
    c.session.request = no_network
    for _ in range(5):
        if not c.step(): break
    assert c.db.execute("SELECT COUNT(*) FROM jobs WHERE state='pagination_repeat'").fetchone()[0] == 1
    assert c.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 3
    assert not c.db.execute("SELECT 1 FROM jobs WHERE payload LIKE '%page=3%'").fetchone()


def test_pagination_fingerprint_separates_searches_and_ignores_offset():
    candidates = [{'native_id': 'JR1', 'url': 'https://a.org/job/1'}]
    make = lambda term, offset: {'url': 'https://a.org/jobs', 'method': 'POST', 'body': {'searchText': term, 'limit': 20, 'offset': offset}}
    assert runner.listing_fingerprint(make('student', 0), candidates) == runner.listing_fingerprint(make('student', 20), candidates)
    assert runner.listing_fingerprint(make('student', 0), candidates) != runner.listing_fingerprint(make('intern', 0), candidates)


def test_listing_backlog_cannot_starve_ready_details(campaign):
    c = campaign; day = runner.today()
    for page in range(20):
        c.enqueue('one', day, 'listing', {'url': f'https://one.academicworks.com/?page={page}', 'method': 'GET'})
    url = 'https://one.academicworks.com/opportunities/17'
    c.enqueue('one', day, 'detail', {'url': url, 'method': 'GET', 'candidate': {}})
    c.import_capture(capture(c, url, '<h1>Student award</h1>')); c.db.commit()
    assert c.step()
    assert c.db.execute("SELECT state FROM jobs WHERE stage='detail'").fetchone()[0] == 'held'
    assert c.db.execute("SELECT COUNT(*) FROM jobs WHERE stage='listing' AND state='pending'").fetchone()[0] == 20


def test_sources_rotate_within_detail_stage(campaign):
    c = campaign; day = runner.today()
    c.sources['two'] = {**c.sources['one'], 'url': 'https://two.academicworks.com/', 'allowed_hosts': ['two.academicworks.com']}
    for sid in ['one', 'two']:
        for native in range(3):
            url = f'https://{sid}.academicworks.com/opportunities/{native}'
            c.enqueue(sid, day, 'detail', {'url': url, 'method': 'GET', 'candidate': {}})
            c.import_capture(capture(c, url, '<h1>Student award</h1>'))
    c.db.commit(); assert c.step(); assert c.step()
    assert {r['source'] for r in c.db.execute("SELECT source FROM jobs WHERE state='held'")} == {'one', 'two'}


@pytest.mark.parametrize('limit', ['daily_request_limit', 'campaign_storage_budget'])
def test_global_limit_leaves_http_queue_untouched_but_processes_cache(campaign, limit):
    c = campaign; day = runner.today()
    if limit == 'daily_request_limit':
        c.config['daily_request_limit'] = 1
        c.event('http')
    else:
        c.config['max_stored_bytes'] = 1
        c.event('stored', stored_bytes=1)
    url = 'https://one.academicworks.com/opportunities/17'
    c.seed(day)
    c.enqueue('one', day, 'detail', {'url': url, 'method': 'GET', 'candidate': {}})
    c.import_capture(capture(c, url, '<h1>Student award</h1>')); c.db.commit()
    def no_network(*a, **kw): raise AssertionError('HTTP request under global limit')
    c.session.request = no_network
    listing_before = dict(c.db.execute("SELECT * FROM jobs WHERE stage='listing'").fetchone())
    assert c.step()
    assert c.db.execute("SELECT state FROM jobs WHERE stage='detail'").fetchone()[0] == 'held'
    assert not c.step() and not c.step()
    assert dict(c.db.execute("SELECT * FROM jobs WHERE stage='listing'").fetchone()) == listing_before
    assert not c.db.execute("SELECT 1 FROM events WHERE kind='resource_pause'").fetchone()
    assert c.status()['http_budget']['block_reason'] == limit


def test_http_budget_resets_at_korea_midnight(campaign):
    c = campaign
    before = datetime.fromisoformat('2026-11-10T23:59:59+09:00').timestamp()
    after = datetime.fromisoformat('2026-11-11T00:00:00+09:00').timestamp()
    c.config['daily_request_limit'] = 1
    c.clock = lambda: before
    c.event('http'); c.db.commit()
    assert c.network_budget('2026-11-10') == {
        'used': 1, 'limit': 1, 'block_reason': 'daily_request_limit',
        'resume_at_kst': '2026-11-11T00:00:00+09:00'}
    c.clock = lambda: after
    assert c.network_budget('2026-11-11')['block_reason'] is None


def test_one_day_budget_increase_keeps_prior_requests_and_expires(campaign):
    c = campaign; day = runner.today()
    c.config.update(daily_request_limit=1, daily_request_limits_kst={day: 3})
    c.event('http'); c.db.commit()
    assert c.network_budget(day)['used'] == 1
    assert c.network_budget(day)['limit'] == 3
    assert c.network_budget(day)['block_reason'] is None
    assert c.network_budget('2000-01-01')['limit'] == 1


def test_campaign_recognizes_baseline_varbi_route_alias_without_rewriting_url(campaign):
    c = campaign
    old = 'https://lu.varbi.com/en/what:job/jobID:981772/type:job/where:39/apply:1'
    current = 'https://lu.varbi.com/en/what:login/jobID:981772/type:job/where:4/apply:1'
    c.db.execute('INSERT INTO baseline VALUES (?,?,?)', ('euraxess/478781', old, 'prior-parent'))
    assert c.baseline_match('varbi/lu/981772', current) == 'euraxess/478781'
    assert c.baseline_match('other', current.replace('lu.varbi', 'ki.varbi')) is None
    assert c.db.execute('SELECT application FROM baseline').fetchone()[0] == old


def test_global_cap_requires_all_supporting_captures(campaign):
    c = campaign; day = runner.today(); root = 'https://one.academicworks.com/'
    main, info = root+'opportunities/17', root+'data/17'
    c.config['daily_request_limit'] = 1
    c.event('http')
    c.enqueue('one', day, 'detail', {'url': main, 'method': 'GET', 'candidate': {
        'support_requests': {'info': {'url': info, 'method': 'GET'}}}})
    c.import_capture(capture(c, main, '<h1>Student award</h1>')); c.db.commit()
    def no_network(*a, **kw): raise AssertionError('HTTP request under global limit')
    c.session.request = no_network
    assert not c.step()
    assert c.db.execute('SELECT attempts FROM jobs').fetchone()[0] == 0
    c.import_capture(capture(c, info, '<p>Verified support document</p>')); c.db.commit()
    assert c.step()
    assert c.db.execute("SELECT COUNT(*) FROM events WHERE kind='http'").fetchone()[0] == 1


def test_support_fetch_keeps_host_pacing_and_reuses_existing_capture(campaign):
    c = campaign; day = runner.today(); root = 'https://one.academicworks.com/'
    request = {'url': root+'data/17', 'method': 'GET'}
    c.enqueue('one', day, 'detail', {'url': root+'opportunities/17', 'method': 'GET', 'candidate': {
        'support_requests': {'info': request}}})
    c.db.execute('INSERT INTO hosts(host,next_at) VALUES(?,?)', ('one.academicworks.com', c.clock()+60)); c.db.commit()
    job = c.db.execute('SELECT * FROM jobs').fetchone(); payload = json.loads(job['payload'])
    with pytest.raises(runner.ResourcePause, match='support_host_pacing'):
        c.fetch_support(job, payload, c.sources['one'])
    c.import_capture(capture(c, request['url'], '<p>Cached support</p>')); c.db.commit()
    assert c.fetch_support(job, payload, c.sources['one'])['info']['url'] == request['url']


def test_jobbnorge_application_fragment_preserves_native_identity(campaign):
    old = 'https://www.jobbnorge.no/en/available-jobs/job/311174/phd-candidate'
    spa = 'https://www.jobbnorge.no/jobseeker/#/application/apply/311174'
    c = campaign
    c.db.execute('INSERT INTO baseline VALUES (?,?,?)', ('euraxess/native', old, 'prior-parent'))
    assert runner.app_key(spa) == 'https://jobseeker.jobbnorge.no/client/apply/311174'
    assert runner.app_key(spa) != runner.app_key(spa.replace('311174', '311175'))
    assert c.baseline_match('jobbnorge/native', runner.app_key(spa)) == 'euraxess/native'
    assert c.baseline_match('jobbnorge/native', runner.app_key(spa.replace('311174', '311175'))) is None


def test_new_source_robots_policy_blocks_detail_without_fetch(campaign):
    c = campaign; day = runner.today(); root = 'https://one.academicworks.com/'
    c.sources['one']['robots_preflight'] = True
    c.import_capture(capture(c, root+'robots.txt', 'User-agent: *\nDisallow: /opportunities/\n'))
    c.enqueue('one', day, 'detail', {'url': root+'opportunities/17', 'method': 'GET', 'candidate': {}})
    c.db.commit()
    def no_network(*a, **kw): raise AssertionError('Disallowed path fetched')
    c.session.request = no_network
    assert c.step()
    state = c.db.execute('SELECT state,reason FROM jobs').fetchone()
    assert tuple(state) == ('held', 'Hold: robots_policy_disallows_source_path')
    assert not c.db.execute("SELECT 1 FROM events WHERE kind='http'").fetchone()


def test_robots_406_is_recorded_once_per_host_with_text_accept(campaign):
    c = campaign; day = runner.today(); root = 'https://one.academicworks.com/'
    c.sources['one']['robots_preflight'] = True
    for i in (1, 2):
        c.enqueue('one', day, 'detail', {'url': root+f'opportunities/{i}', 'method': 'GET', 'candidate': {}})
    c.db.commit()
    requests_seen = []
    def fetch(method, url, **kwargs):
        requests_seen.append((url, kwargs['headers']))
        response = Response(406, b'Policy not available'); response.url = url
        return response
    c.session.request = fetch
    assert c.step() and c.step() and not c.step()
    assert requests_seen == [(root+'robots.txt', {'Accept': 'text/plain,*/*;q=0.1'})]
    assert c.db.execute("SELECT COUNT(*) FROM jobs WHERE state='held'").fetchone()[0] == 2


def test_focus_prioritizes_unprocessed_detail_without_dropping_reobservation(campaign, monkeypatch):
    c = campaign; day = runner.today(); root = 'https://one.academicworks.com/'
    old, fresh = root+'opportunities/1', root+'opportunities/2'
    c.config['focus_new_validatable_details'] = True
    c.sources['one']['adapter'] = 'workday'
    monkeypatch.setattr(runner, 'parse_detail', lambda *a, **kw: (None, {'reason': 'fixture'}))
    c.enqueue('one', '2000-01-01', 'detail', {'url': old, 'method': 'GET', 'candidate': {}})
    c.db.execute("UPDATE jobs SET state='held'")
    for url in (old, fresh):
        c.enqueue('one', day, 'detail', {'url': url, 'method': 'GET', 'candidate': {}})
        c.import_capture(capture(c, url, '<h1>Student award</h1>'))
    c.db.commit(); assert c.step()
    assert c.db.execute("SELECT state FROM jobs WHERE day=? AND key=?", (day, runner.request_key({'url': fresh, 'method': 'GET'}))).fetchone()[0] == 'held'
    assert c.db.execute("SELECT COUNT(*) FROM jobs WHERE day=? AND state='pending'", (day,)).fetchone()[0] == 1
    assert c.step()


def test_jobbnorge_only_follows_same_posting_canonical_redirect(campaign):
    from benefitlens_campaign_jobbnorge import canonical_notice_redirect
    c = campaign
    root = 'https://www.jobbnorge.no/ledige-stillinger/stilling/'
    start, final = root+'310921', root+'310921/phd-fellow'
    source = c.sources['one'] | {'adapter':'jobbnorge', 'allowed_hosts':['www.jobbnorge.no']}
    assert canonical_notice_redirect(start, final.replace('310921','310922')) is None
    assert canonical_notice_redirect(start, final.replace('www.jobbnorge.no','other.example')) is None
    visited = []
    def fake(method, url, **kwargs):
        visited.append(url)
        response = Response(302, headers={'Location': '/ledige-stillinger/stilling/310921/phd-fellow'}) if url == start else Response(200, b'<p>Official notice</p>')
        response.url = url
        return response
    c.session.request = fake
    payload = {'url': start, 'method':'GET'}
    cap = c.fetch({'id': 1, 'day': runner.today(), 'key': runner.request_key(payload)}, payload, source)
    assert visited == [start, final]
    assert cap['request_url'] == start and cap['final_url'] == cap['canonical_notice_redirect'] == final
    assert c.network_budget(runner.today())['used'] == 2
