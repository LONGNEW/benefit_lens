import json
from datetime import date
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_benefitlens_batch import assemble, validate_open_cohort
from run_benefitlens_discovery_step import BoundedExpansion


def test_conflicting_round_shards_cannot_replace_each_other(tmp_path):
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps({"format": "benefitlens-public-sample-v1", "opportunities": []}))
    for name, status in (("first", "open"), ("second", "closed")):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "round.json").write_text(json.dumps({"opportunity_round": [
            {"program_id": "p", "round_id": "2026", "opportunity_id": "p-2026", "status": status}
        ]}))
    with pytest.raises(ValueError, match="conflicting input rows"):
        assemble(sample, date(2026, 11, 9), [tmp_path / "first", tmp_path / "second"])


def test_dispatch_cap_does_not_close_pending_wave_and_can_resume(tmp_path):
    config = {"scope": "fixture", "seeds": [{"term": s, "lane": "announcement"} for s in ("bursary", "stipend")],
              "templates": {"announcement": {"en": ['"{term}" funding']}}, "results_per_query": 1,
              "search_batch_size": 8, "fetch_workers": 1, "extract_batch_size": 1,
              "chunk_characters": 16000, "max_attempts": 1}

    class Backend:
        calls = []

        def search(self, queries):
            self.calls.extend(queries)
            return {q: {"results": [{"url": "https://example.org/" + ("bursary" if "bursary" in q else "stipend")}]} for q in queries}, "fixture"

        def extract(self, documents, dictionary):
            return {"documents": [{"chunk_id": d["chunk_id"], "terms": []} for d in documents]}, "fixture"

    backend = Backend()
    def fetch(url, folder):
        return {"text": "A supported public notice.", "text_path": str(folder / "text/fixture.txt")}

    run = BoundedExpansion(tmp_path, config, backend, fetcher=fetch)
    first = run.step(query_attempts=1, waves=1, wall_seconds=30)
    assert first["status"] == "query_attempt_limit"
    assert first["waves"][-1]["status"] == "open"
    assert first["counters"]["query"] == {"executed": 1, "pending": 1}
    assert first["counters"]["chunk"] == {"processed": 1}
    second = BoundedExpansion(tmp_path, config, backend, fetcher=fetch).step(query_attempts=2, waves=1, wall_seconds=30, chunk_attempts=1)
    assert second["status"] == "chunk_attempt_limit"
    assert second["waves"][-1]["status"] == "open"
    assert second["counters"]["chunk"]["pending"] == 1
    third = BoundedExpansion(tmp_path, config, backend, fetcher=fetch).step(query_attempts=2, waves=1, wall_seconds=30, chunk_attempts=2)
    assert third["status"] == "wave_limit"
    assert third["waves"][-1]["status"] == "closed"
    assert len(backend.calls) == len(set(backend.calls)) == 2


@pytest.mark.parametrize("change", [
    {"status": "historical"},
    {"checked_on": "2026-11-08"},
    {"opens_on": "2026-11-10"},
    {"closes_on": "2026-11-08"},
    {"application_url": None},
])
def test_current_open_gate_rejects_unusable_rounds(change):
    row = {"opportunity_id": "fixture", "status": "open", "checked_on": "2026-11-09",
           "opens_on": None, "closes_on": None, "application_url": "https://example.org/apply"}
    with pytest.raises(ValueError):
        validate_open_cohort({"opportunity_round": [row | change]}, {"fixture": "source_backed"}, date(2026, 11, 9))


def test_current_open_mode_excludes_default_sample(tmp_path):
    path = tmp_path / "incoming"
    path.mkdir()
    row = {"program_id": "p", "round_id": "r", "opportunity_id": "fixture",
           "status": "open", "checked_on": "2026-11-09", "opens_on": None, "closes_on": None,
           "application_url": "https://example.org/apply"}
    (path / "round.json").write_text(json.dumps({"opportunity_round": [row]}))
    tables, basis = assemble(None, date(2026, 11, 9), [path])
    assert tables["opportunity_round"] == [row]
    with pytest.raises(ValueError, match="official evidence"):
        validate_open_cohort(tables, basis, date(2026, 11, 9))
    with pytest.raises(ValueError, match="application-period evidence"):
        validate_open_cohort(tables, {"fixture": "source_backed"}, date(2026, 11, 9))


def test_live_importer_requires_evidence_for_published_deadline():
    from build_benefitlens_live_batch import verify_posting
    row = {"title": "Student Intern", "fetched_at": "2026-11-09T02:00:00+00:00", "open_basis": "fixture", "status": "open",
           "official_url": "https://example.org/posting", "application_url": "https://example.org/apply",
           "closes_on": "2026-12-01"}
    with pytest.raises(ValueError, match="deadline evidence"):
        verify_posting(row, date(2026, 11, 9), {})


def test_recapture_uses_newer_observation_and_audits_both_versions():
    from build_benefitlens_live_batch import reconcile_capture
    old = {'source_family': 'official', 'native_id': '1', 'raw_sha256': 'old', 'text_sha256': 'old',
           'fetched_at': '2026-11-09T02:00:00+00:00'}
    new = old | {'raw_sha256': 'new', 'text_sha256': 'new', 'fetched_at': '2026-11-09T03:00:00+00:00'}
    for first, second in ((old, new), (new, old)):
        chosen, audit = reconcile_capture(first, second)
        assert chosen == new
        assert len(audit['captures']) == 2
        assert audit['chosen_raw_sha256'] == 'new'
    with pytest.raises(ValueError, match='identical capture time'):
        reconcile_capture(old, old | {'raw_sha256': 'conflicting'})


def test_unsw_compound_identity_requires_the_exact_official_call_path():
    from build_benefitlens_live_batch import native_in_source
    row = {'source_family': 'UNSW', 'official_url': 'https://www.scholarships.unsw.edu.au/scholarships/id/1993/7299'}
    raw = b'<a href="/scholarships/id/1993/7299">Call</a>'
    assert native_in_source(row, '1993-7299', raw)
    assert not native_in_source(row, '1993-7300', raw)
    assert not native_in_source(row | {'official_url': 'https://example.org/scholarships/id/1993/7299'}, '1993-7299', raw)
    assert not native_in_source({'source_family': 'official'}, '123', b'{"id":"41234"}')


def test_workday_namespaces_are_derived_from_the_actual_tenant():
    from build_benefitlens_live_batch import source_id
    row = {'source_family': 'workday_public_cxs', 'native_id': 'R1', 'source_id': 'unreliable_alias',
           'official_url': 'https://employer.wd5.myworkdayjobs.com/site/job/example_R1'}
    assert source_id(row) == ('workday/employer', 'R1')
    assert source_id(row | {'source_id': 'other_alias'}) == source_id(row)
    assert source_id(row | {'official_url': 'https://another.wd5.myworkdayjobs.com/site/job/example_R1'}) != source_id(row)
    with pytest.raises(ValueError, match='official public tenant'):
        source_id(row | {'official_url': 'https://unrelated.example/job/R1'})


def test_visible_student_evidence_preserves_inline_words_and_negation():
    from build_benefitlens_live_batch import visible_source, normalize_source_evidence
    raw = json.dumps({'description': '<p>Must be <span>en</span>rolled until 202<span>7</span>.</p><p>Not eligible after graduation.</p>'}).encode()
    text = visible_source(raw, {}, 'fixture')
    assert normalize_source_evidence('Must be enrolled until 2027.') in text
    assert normalize_source_evidence('Not eligible after graduation.') in text
    assert normalize_source_evidence('eligible until 2028') not in text


def test_application_link_on_support_page_uses_its_actual_base_url():
    from build_benefitlens_live_batch import official_application_link
    row = {'source_family': 'official', 'official_url': 'https://uni.example/fund/a',
           'application_url': 'https://uni.example/course/apply'}
    raw = b'<a href="apply">Apply</a>'
    assert official_application_link(row, raw, {}, 'https://uni.example/course/finance')
    assert not official_application_link(row, raw, {})


def test_support_evidence_requires_same_organization_scope_and_current_capture(tmp_path):
    from hashlib import sha256
    from build_benefitlens_live_batch import verify_support_capture
    raw, text = tmp_path / 'rule.html', tmp_path / 'rule.txt'
    raw.write_text('<p>Currently enrolled</p>')
    text.write_text('Currently enrolled\n')
    source = {'url': 'https://www.postgraduate.study.cam.ac.uk/course/example',
              'fetched_at': '2026-11-09T04:00:00+00:00', 'scope': 'named course funding deadline',
              'raw_path': str(raw), 'raw_sha256': sha256(raw.read_bytes()).hexdigest(),
              'text_path': str(text), 'text_sha256': sha256(text.read_bytes()).hexdigest()}
    row = {'official_url': 'https://www.student-funding.cam.ac.uk/fund/example'}
    assert verify_support_capture(row, source, date(2026, 11, 9), {}) == raw.read_bytes()
    for changed in ({'url': 'https://unrelated.example/course'}, {'scope': ''},
                    {'fetched_at': '2026-11-08T04:00:00+00:00'}):
        with pytest.raises(ValueError):
            verify_support_capture(row, source | changed, date(2026, 11, 9), {})


def test_mercedes_publication_end_is_not_an_application_deadline():
    from build_benefitlens_live_batch import application_deadline
    row = {'closes_on': '2026-12-01', 'closes_on_kind': 'source_publication_end'}
    assert application_deadline(row) is None
    assert application_deadline(row | {'closes_on_kind': 'explicit_application_deadline'}) == '2026-12-01'


def test_department_modal_keeps_xml_cdata_document_text():
    from build_benefitlens_live_batch import visible_source, normalize_source_evidence
    raw = b'<?xml version="1.0"?><partial-response><changes><update><![CDATA[<ul><li>Engineering</li><li>Computer Science</li></ul>]]></update></changes></partial-response>'
    assert normalize_source_evidence('Computer Science') in visible_source(raw, {}, 'modal.xml')


def test_walmart_cannot_reenter_from_old_unreviewed_workday_inputs():
    from build_benefitlens_live_batch import verify_posting
    row = {'title': 'Pharmacy Pre-Grad Intern', 'source_family': 'workday_public_cxs', 'native_id': 'R-2569652', 'status': 'open',
           'fetched_at': '2026-11-09T04:00:00+00:00', 'open_basis': 'workday_active',
           'official_url': 'https://walmart.wd504.myworkdayjobs.com/WalmartExternal/job/example_R-2569652',
           'application_url': 'https://walmart.wd504.myworkdayjobs.com/WalmartExternal/job/example_R-2569652'}
    with pytest.raises(ValueError, match='independently verified official frontend'):
        verify_posting(row, date(2026, 11, 9), {})


def test_source_categories_map_to_the_fixed_sql_vocabulary():
    from build_benefitlens_live_batch import classify_type
    assert classify_type({'opportunity_type': 'doctoral_studentship'})[0] == 'admission_funding'
    assert classify_type({'title': '2027 Pharmacy Pre-Grad Intern'})[0] == 'internship'
    assert classify_type({'title': 'Working Student in Software'})[0] == 'other'
    assert classify_type({'title': 'Research Assistant'})[0] == 'other'
    with pytest.raises(ValueError, match='Unmapped opportunity category'):
        classify_type({'opportunity_type': 'unmapped_source_category'})
