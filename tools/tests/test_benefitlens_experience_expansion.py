import json
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_experience_expansion import (
    Expansion, admitted, classify, collect, definition, outreachy_children,
    report_links, ror_children,
)
from continue_benefitlens_experiences import next_action
from benefitlens_source_storage import store_source_bytes
from scale_benefitlens_experiences import ExperienceFrontier


def test_drained_queue_adds_work_or_waits_without_false_completion():
    assert next_action(pending_at=None, added=12, continuous=True, now=0) == ('collect', 0)
    assert next_action(pending_at=None, added=0, continuous=True, now=0) == ('waiting_for_source_discovery', 30)
    assert next_action(pending_at=1000, added=0, continuous=True, now=0) == ('waiting_for_provider', 30)
    assert next_action(pending_at=None, added=0, continuous=False, now=0) == ('checkpoint', 0)


def test_long_publisher_delay_is_durable_retry_without_sleep_or_network(tmp_path, monkeypatch):
    import time
    import pytest
    from urllib.robotparser import RobotFileParser
    import benefitlens_experience_transport as transport
    from scale_benefitlens_experiences import failure
    monkeypatch.setattr(transport, 'CACHE', tmp_path)
    monkeypatch.setattr(transport.SharedEvidence, 'fetch', lambda *_a, **_k: pytest.fail('Network must wait'))
    policy = RobotFileParser()
    policy.parse(['User-agent: *', 'Crawl-delay: 600'])
    at = time.time()
    for i in range(2):
        ledger = transport.ExperienceEvidence(tmp_path, 'run-' + str(i), 10)
        ledger.defer_long_waits = True
        ledger.policies['uni.example'] = policy
        if i == 0:
            with ledger.db() as c:
                c.execute('INSERT INTO network_call(at,url) VALUES(?,?)', (at, 'https://uni.example/robots.txt'))
        with pytest.raises(transport.PublisherPacing) as error:
            ledger.fetch('https://uni.example/report.pdf')
        result = failure(error.value, {})
        assert result['status'] == 'retry' and result['next_at'] == at + 600
        assert ledger.network_requests == 0


def test_publisher_delay_allows_verified_same_day_cache_reuse(tmp_path, monkeypatch):
    import time
    from hashlib import sha256
    from urllib.robotparser import RobotFileParser
    import benefitlens_experience_transport as transport
    monkeypatch.setattr(transport, 'CACHE', tmp_path)
    expected = (b'cached original', {'status': 200})
    monkeypatch.setattr(transport.SharedEvidence, 'fetch', lambda *_a, **_k: expected)
    ledger = transport.ExperienceEvidence(tmp_path, 'cached-run', 10)
    ledger.defer_long_waits = True
    policy = RobotFileParser()
    policy.parse(['User-agent: *', 'Crawl-delay: 600'])
    ledger.policies['uni.example'] = policy
    url = 'https://uni.example/report.pdf'
    key = sha256(transport.encoded(['GET', url, None, None]).encode()).hexdigest()
    with ledger.db() as c:
        c.execute('INSERT INTO network_call(at,url) VALUES(?,?)', (time.time(), url))
        c.execute('INSERT INTO captures(day,key,meta) VALUES(?,?,?)', (ledger.day, key, '{}'))
    assert ledger.fetch(url) == expected


def test_discovery_stream_restart_does_not_recount_old_urls(tmp_path):
    q = ExperienceFrontier(tmp_path)
    source = definition('https://uni.example/reports', official=True)
    rows = [(source, {'url': f'https://uni.example/{i}.pdf', 'kind': 'detail'}) for i in range(4)]
    a = Expansion(q, {}, {'sources': []})
    assert a.consume('reports', rows, 2) == 2
    b = Expansion(ExperienceFrontier(tmp_path), {}, {'sources': []})
    assert b.consume('reports', rows, 2) == 2
    assert b.consume('reports', rows, 2) == 0
    with q.connect() as c:
        assert c.execute('SELECT count(*) FROM experience_task').fetchone()[0] == 4


def test_new_config_seed_before_old_cursor_is_still_discovered(tmp_path, monkeypatch):
    import benefitlens_experience_expansion as module
    monkeypatch.setattr(module, 'CACHE', tmp_path)
    source = definition('https://uni.example/reports', official=True,
                        seeds=[{'url': 'https://uni.example/old', 'kind': 'index'}])
    q = ExperienceFrontier(tmp_path)
    expansion = Expansion(q, {}, {'sources': [source]})
    assert expansion.seed(100) == 1
    source['seeds'].insert(0, {'url': 'https://uni.example/new', 'kind': 'index'})
    assert expansion.seed(100) == 1
    assert expansion.seed(100) == 0
    with q.connect() as c:
        assert c.execute('SELECT count(*) FROM experience_task').fetchone()[0] == 2


def test_new_staged_collection_before_old_material_cursor_is_not_skipped(tmp_path, monkeypatch):
    import benefitlens_experience_expansion as module
    monkeypatch.setattr(module, 'CACHE', tmp_path)
    q = ExperienceFrontier(tmp_path)
    source = {'id': 'staged-gsoc-reports', 'adapter': 'cached_material'}
    def row(name, stream):
        return source, {'url': 'https://blog.example/' + name,
                        'kind': 'cached_material', 'original': {'sha256': name},
                        'text_ref': {'sha256': name}, 'material_stream': stream}
    old = Expansion(q, {}, {'sources': []})
    monkeypatch.setattr(old, 'stage_rows', lambda: [row('old', 'z-publisher')])
    assert old.add_materials(10) == 1
    new = Expansion(q, {}, {'sources': []})
    monkeypatch.setattr(new, 'stage_rows', lambda: [row('new', 'a-publisher'), row('old', 'z-publisher')])
    assert new.add_materials(10) == 1
    assert new.add_materials(10) == 0
    with q.connect() as c:
        assert c.execute('SELECT count(*) FROM experience_task').fetchone()[0] == 2


def test_official_report_branch_accepts_observed_backend_not_external_sites():
    source = definition('https://uni.example/reports', official=True)
    raw = b'<main><a href="https://backend.uni.example/document/a/download">A</a><a href="https://other.example/a.pdf">B</a><a href="/reports/japan">Japan experience report</a></main>'
    found = report_links(raw, {'url': 'https://uni.example/reports', 'report_scope': True}, source)
    assert {x['url'] for x in found} == {'https://backend.uni.example/document/a/download', 'https://uni.example/reports/japan'}
    assert {x['kind'] for x in found} == {'detail', 'index'}


def test_homepage_navigation_can_discover_international_office():
    source = definition('https://uni.example', official=True)
    raw = b'<nav><a href="/international">International</a><a href="/shop">Shop</a></nav><main>No links</main>'
    found = report_links(raw, {'url': 'https://uni.example', 'report_scope': False}, source)
    assert [x['url'] for x in found] == ['https://uni.example/international']


def test_published_year_does_not_create_multiple_outreachy_experiences():
    text = 'I submitted my Outreachy application after a month of contributions.\n' + 'I learned how to test my changes. ' * 30
    author = {'key': 'blog.example:alice', 'name': 'Alice'}
    a = classify(text, {'url': 'https://blog.example/one', 'published_at': '2024-01-01'}, {}, author)
    b = classify(text + ' More details.', {'url': 'https://blog.example/two', 'published_at': '2026-01-01'}, {}, author)
    assert a['status'] == b['status'] == 'accepted'
    assert a['case_key'] == b['case_key'] and a['cycle'] is None
    assert a['outcome'] == 'unknown' and a['semantic_review'] == 'unreviewed'
    for evidence in a['evidence']:
        assert text[evidence['char_start']:evidence['char_end']] == evidence['exact_quote']


def test_german_report_is_preserved_but_instructions_are_not_an_episode():
    task = {'url': 'https://uni.example/report.pdf'}
    report = 'Ich studiere Biochemie und habe ein Auslandssemester mit Erasmus gemacht.\n' + 'Die Kurswahl war wichtig. ' * 40
    assert classify(report, task, {})['status'] == 'accepted'
    guide = 'Erasmus guidelines: You should submit the forms and check all deadlines. ' * 40
    assert classify(guide, task, {})['status'] == 'reference_material'
    mixed = report + ' I also compared GSoC applications.'
    r = classify(mixed, task, {})
    assert r['status'] == 'reference_material' and len(r['program_candidates']) == 2


def test_generic_code_repository_stays_material_even_with_first_person():
    text = 'I worked on a GSoC project and learned a lot. ' * 30
    assert classify(text, {'url': 'https://github.com/alice/code'}, {})['status'] == 'reference_material'


def test_published_essay_collection_is_not_a_person_even_with_first_person_examples():
    text = 'I submitted my Outreachy application and learned from the feedback. ' * 30
    result = classify(text, {'url': 'https://uni.example/essay-examples.pdf'},
                      {'material_only': True})
    assert result['status'] == 'reference_material'
    assert 'case_key' not in result and not result['approved_pattern']


def test_korean_attachment_labels_and_observed_offset_pagination():
    source = definition('https://uni.example/reports', official=True)
    raw = ('<main><a href="?mode=download&amp;attachNo=4">지원 준비 후기.pdf</a>'
           '<a href="?mode=download&amp;attachNo=5">참여 보고서.hwp</a>'
           '<a href="?mode=list&amp;article.offset=10">2</a>'
           '<a href="https://other.example/essay.pdf">Other</a></main>').encode()
    rows = report_links(raw, {'url': 'https://uni.example/reports',
                             'report_scope': True}, source)
    assert any(r['kind'] == 'detail' and 'attachNo=4' in r['url'] for r in rows)
    assert any(r['kind'] == 'index' and 'article.offset=10' in r['url'] for r in rows)
    assert any(r['kind'] == 'detail' and 'attachNo=5' in r['url'] for r in rows)
    assert not any('other.example' in r['url'] for r in rows)


def test_registered_korean_program_still_requires_original_program_and_personal_action():
    source = {'program_bindings': [{'program_id': 'university-student-exchange',
                                   'body_pattern': '교환학생'}]}
    task = {'url': 'https://uni.example/report.pdf'}
    text = '저는 교환학생 지원 준비 과정에서 학교별 수업과 비용을 비교했습니다.\n' * 30
    result = classify(text, task, source)
    assert result['status'] == 'accepted'
    assert result['program_id'] == 'university-student-exchange'
    assert result['case_scope'] == 'application_process' and result['outcome'] == 'unknown'
    for evidence in result['evidence']:
        assert text[evidence['char_start']:evidence['char_end']] == evidence['exact_quote']
    assert classify(text.replace('교환학생', '장기 여행'), task, source)['status'] == 'reference_material'
    assert classify('교환학생 지원서 작성 방법을 안내합니다. ' * 40, task, source)['status'] == 'reference_material'


def test_larger_university_pdf_budget_does_not_expand_other_routes():
    from benefitlens_experience_transport import ExperienceEvidence
    ledger = ExperienceEvidence.__new__(ExperienceEvidence)
    assert ledger.response_byte_limit('https://eduinno.smu.ac.kr/oia/program/ssap.do?mode=download&attachNo=1') == 32*1024**2
    for url in ['https://other.example/large.pdf',
                'https://eduinno.smu.ac.kr/other?mode=download',
                'https://eduinno.smu.ac.kr/oia/program/ssap.do?mode=view']:
        assert ledger.response_byte_limit(url) == 8*1024**2


def test_private_hosts_and_explicit_collection_holds_are_not_new_seeds():
    for url in ['https://127.0.0.1/private', 'https://localhost/x', 'https://user:pass@public.example/x',
                'https://www.helsinki.fi/reports', 'https://foo.kcl.ac.uk/x']:
        assert not admitted(url)


def test_repeated_alias_for_one_program_is_not_two_programs():
    text = 'I submitted my GSoC application after testing the project. ' * 30
    source = {'program_bindings': [{'program_id': 'google-summer-of-code', 'body_pattern': 'GSoC'}]}
    result = classify(text, {'url': 'https://blog.example/story'}, source)
    assert result['status'] == 'accepted'
    assert result['program_candidates'] == ['google-summer-of-code']


def test_registered_stages_preserve_source_scopes_and_do_not_recount(tmp_path, monkeypatch):
    import benefitlens_experience_expansion as module
    from benefitlens_source_storage import store_source_bytes
    monkeypatch.setattr(module, 'CACHE', tmp_path)
    text = b'I submitted my GSoC application after preparing the project. ' * 30
    raw = store_source_bytes(b'<main>' + text + b'</main>', tmp_path/'blobs')
    raw.update(url='https://uni.example/a', at='2026-11-19T00:00:00+09:00', status=200)
    ref = store_source_bytes(text, tmp_path/'blobs', suffix='.txt')
    path = tmp_path/'new-library/index.json'
    path.parent.mkdir()
    rows = [{'state': 'body_staged', 'url': 'https://uni.example/' + name,
             'kind': 'html', 'original': raw, 'text': ref,
             'source_definition': {'id': name, 'program_bindings': [
                 {'program_id': name, 'body_pattern': name + ' scholarship'}]},
             'reuse_license': 'CC-BY-4.0', 'material_only': name == 'sample'}
            for name in ['report', 'sample']]
    path.write_text(json.dumps(rows))
    q = ExperienceFrontier(tmp_path/'frontier')
    config = {'sources': [], 'staged_collections': [{'id': 'new-library',
              'index': 'new-library/index.json', 'material_only': False}]}
    expansion = Expansion(q, {}, config)
    assert expansion.add_materials(100) == 2
    assert expansion.add_materials(100) == 0
    assert expansion.sources['new-library:report']['program_bindings'][0]['program_id'] == 'report'
    tasks = q.lease('r', 10)
    by_url = {task['url']: task for task in tasks}
    task = by_url['https://uni.example/sample']
    result, children = module.collect(task, expansion.sources[task['source']], SimpleNamespace())
    assert result['status'] == 'reference_material' and not children
    assert result['document']['reuse_license'] == 'CC-BY-4.0'
    assert result['document']['kind'] == 'html'
    assert 'case_key' not in result


def test_registered_stage_still_rejects_changed_original_bytes(tmp_path, monkeypatch):
    import benefitlens_experience_expansion as module
    from benefitlens_source_storage import store_source_bytes
    raw = store_source_bytes(b'original', tmp_path)
    ref = store_source_bytes(b'I submitted my GSoC application. ' * 30, tmp_path, suffix='.txt')
    raw['sha256'] = '0' * 64
    task = {'url': 'https://blog.example/story', 'kind': 'cached_material',
            'original': raw, 'text_ref': ref}
    result, _ = module.collect(task, {'id': 'new-stage', 'adapter': 'cached_material'}, SimpleNamespace(rate_holds={}))
    assert result['status'] != 'accepted' and 'document' not in result
    assert 'integrity' in result['reason'].lower()


def test_staged_registry_cannot_read_outside_dataset_cache(tmp_path, monkeypatch):
    import pytest
    import benefitlens_experience_expansion as module
    monkeypatch.setattr(module, 'CACHE', tmp_path)
    config = {'sources': [], 'staged_collections': [{'id': 'bad', 'index': '../outside.json'}]}
    expansion = Expansion(ExperienceFrontier(tmp_path/'frontier'), {}, config)
    with pytest.raises(ValueError, match='shared dataset cache'):
        expansion.staged_collections()


def test_corrected_stage_receipt_requeues_only_failed_identical_bytes(tmp_path):
    q = ExperienceFrontier(tmp_path)
    expansion = Expansion(q, {}, {'sources': []})
    source = {'id': 'stage', 'adapter': 'cached_material'}
    task = {'url': 'https://example.org/report', 'kind': 'cached_material',
            'original': {'sha256': 'same'}, 'text_ref': {'sha256': 'same-text'}}
    assert expansion.consume('first', [(source, task)], 10) == 1
    leased = q.lease('r', 1)[0]
    q.finish(leased, {'status': 'needs_review', 'reason': 'Missing retrieval time'})
    assert expansion.consume('unchanged', [(source, task)], 10) == 0
    task['original']['at'] = '2026-11-19T00:00:00+09:00'
    assert expansion.consume('corrected', [(source, task)], 10) == 1
    repaired = q.lease('r', 1)[0]
    assert repaired['staging_previous_failure']['error'] == 'Missing retrieval time'
    q.finish(repaired, {'status': 'reference_material'})
    task['original']['at'] = '2026-11-19T01:00:00+09:00'
    assert expansion.consume('later', [(source, task)], 10) == 1
    leased = q.lease('r', 1)[0]
    q.finish(leased, {'status': 'policy_held', 'reason': 'Publisher denial'})
    task['original']['at'] = '2026-11-19T02:00:00+09:00'
    assert expansion.consume('denial', [(source, task)], 10) == 0


def test_document_metadata_refresh_reopens_only_the_import_receipt(tmp_path):
    q = ExperienceFrontier(tmp_path)
    q.add('source', {'url': 'https://example.org/report', 'kind': 'cached_material'})
    task = q.lease('r', 1)[0]
    first = {'document_id': 'd1', 'material_role_hint': 'application_candidate'}
    q.finish(task, {'status': 'reference_material', 'document': first})
    with q.connect() as c:
        c.execute('INSERT INTO experience_document_import VALUES(?,?)', ('d1', 'pg-d1'))
    q.finish(task, {'status': 'reference_material', 'document': first})
    with q.connect() as c:
        assert c.execute('SELECT count(*) FROM experience_document_import').fetchone()[0] == 1
    corrected = dict(first, material_role_hint='collection_directory', author={'key': 'public-author'})
    q.finish(task, {'status': 'reference_material', 'document': corrected})
    with q.connect() as c:
        assert c.execute('SELECT count(*) FROM experience_document_import').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM document').fetchone()[0] == 1


def test_registry_selects_only_active_education_and_observed_https_websites():
    data = {'number_of_results': 21, 'items': [
        {'id': 'https://ror.org/001', 'status': 'active', 'types': ['education'], 'links': [{'type': 'website', 'value': 'https://uni.example'}]},
        {'id': 'https://ror.org/002', 'status': 'active', 'types': ['company'], 'links': [{'type': 'website', 'value': 'https://company.example'}]},
        {'id': 'https://ror.org/003', 'status': 'active', 'types': ['education'], 'links': [{'type': 'website', 'value': 'http://old.example'}]},
    ]}
    children, sources = ror_children(json.dumps(data).encode(), {'url': 'https://api.ror.org/v2/organizations?query=university&page=1'})
    assert len(sources) == 1
    assert [x['url'] for x in children if 'source_id' in x] == ['https://uni.example']
    assert children[-1]['kind'] == 'ror_index' and 'page=2' in children[-1]['url']


def test_outreachy_blog_icons_are_followed_but_social_profiles_are_not():
    raw = b'<a href="/alums/2024-05/">Older</a><a href="https://alice.wordpress.com/"><img alt="Blog"></a><a href="https://github.com/alice"><img alt="GitHub"></a>'
    children, sources = outreachy_children(raw, {'url': 'https://www.outreachy.org/alums/2025-12/'})
    assert {x['url'] for x in children} == {'https://www.outreachy.org/alums/2024-05/', 'https://alice.wordpress.com/'}
    assert len(sources) == 1


def test_cached_post_cannot_be_bound_to_another_api_item(tmp_path):
    original = {'url': 'https://public-api.wordpress.com/posts', 'at': '2026-11-18T12:00:00+09:00', 'status': 200,
                **store_source_bytes(json.dumps({'posts': [{'URL': 'https://blog.example/real', 'content': '<p>Real</p>'}]}).encode(), tmp_path/'blobs')}
    task = {'url': 'https://blog.example/fake', 'kind': 'cached_material', 'original': original,
            'text_ref': store_source_bytes(b'Fake text', tmp_path/'blobs', suffix='.txt'), 'record_pointer': '/posts/0'}
    result, children = collect(task, {'adapter': 'cached_material'}, SimpleNamespace(rate_holds={}))
    assert result['status'] == 'needs_review' and 'pointer' in result['reason']
    assert 'document' not in result and not children


def test_completed_indices_refresh_daily_but_access_denials_stay_held(tmp_path):
    q = ExperienceFrontier(tmp_path)
    for i in range(2):
        q.add('s', {'url': f'https://uni.example/{i}', 'kind': 'index'})
        task = q.lease('run', 1)[0]
        q.finish(task, {'status': 'indexed' if i == 0 else 'policy_held',
                        'original': {'at': '2026-11-17T12:00:00+09:00'}})
    expansion = Expansion(q, {}, {'sources': []})
    assert expansion.refresh_indices('2026-11-18') == 1
    with q.connect() as c:
        assert sorted(c.execute('SELECT state FROM experience_task').fetchall()) == [('pending',), ('policy_held',)]


def test_stored_materials_are_not_blocked_by_a_remote_host_pause(tmp_path):
    import time
    q = ExperienceFrontier(tmp_path)
    for i in range(2):
        q.add('cached', {'url': f'https://uni.example/{i}', 'kind': 'cached_material'})
    q.defer_rate_hosts({'uni.example': time.time() + 600})
    assert len(q.lease('run', 2, busy_hosts=['uni.example'])) == 2
