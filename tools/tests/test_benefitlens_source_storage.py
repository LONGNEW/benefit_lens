import gzip
from hashlib import sha256
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_source_storage import read_source_bytes, read_source_text, store_source_bytes, source_fetched_at
from build_benefitlens_live_batch import file_bytes, locator, tables_for
from benefitlens_batch_db import validate_sources,load_rows
from test_benefitlens_handoff import bundle


def test_bundle_members_use_recorded_transport_time():
    at = '2026-11-19T15:17:35.405943+09:00'
    assert source_fetched_at({'archive_receipt': {'at_kst': at}}) == at
    assert source_fetched_at({'api_receipt': {'at': at}}) == at
    assert source_fetched_at({'at': at, 'archive_receipt': {'at_kst': 'older'}}) == at


def test_missing_fetch_time_is_not_replaced_with_import_time():
    with pytest.raises(ValueError, match='timestamp is absent'):
        source_fetched_at({'archive_receipt': {'published_at': '2020-01-01'}})


def test_original_bytes_and_lf_quotes_survive_compression(tmp_path):
    original = ('첫 줄\n\nApplication deadline: 30 November 2026\r\n' * 100).encode()
    ref = store_source_bytes(original, tmp_path, suffix='.txt')
    assert ref['storage']['codec'] == 'gzip'
    assert ref['sha256'] == sha256(original).hexdigest()
    assert read_source_bytes(ref['path'], storage=ref['storage']) == original
    assert read_source_text(ref['path']).split('\n')[2].endswith('2026\r')
    assert file_bytes(ref['path'], ref['sha256'], {}, ref['storage']) == original
    start = original.decode().index('Application')
    assert locator({'char_start': start, 'char_end': start + 1}, ref['path']) == 'L3-L3'
    doc = {'document_id':'d', 'capture_mode':'full_document',
           **{f'{k}_{field}':value for k in ('raw','text')
              for field,value in [('path',ref['path']),('sha256',ref['sha256']),('storage',ref['storage'])]}}
    result = validate_sources({'source_document':[doc], 'evidence':[{
        'evidence_id':'e', 'document_id':'d', 'locator':'L3',
        'quote':'Application deadline: 30 November 2026'}]}, Path('/'))
    assert result['spans'] == 1


def test_hash_deduplication_and_determinism(tmp_path):
    content = b'unchanged official source\n' * 1000
    first = store_source_bytes(content, tmp_path, suffix='.html')
    assert first == store_source_bytes(content, tmp_path, suffix='.html')
    assert first == store_source_bytes(content, tmp_path, suffix='.html',compression_level=9)
    assert len(list(tmp_path.iterdir())) == 1
    damaged = dict(first['storage'], sha256='0'*64)
    with pytest.raises(ValueError, match='storage hash'):
        read_source_bytes(first['path'], storage=damaged)
    with pytest.raises(ValueError, match='Hash mismatch'):
        file_bytes(first['path'], '0'*64, {})


def test_bad_gzip_limits_and_cheaper_plain_codec(tmp_path):
    small = store_source_bytes(b'hi', tmp_path)
    assert small['storage']['codec'] == 'identity'
    bomb = tmp_path/'over-limit.txt.gz'
    bomb.write_bytes(gzip.compress(b'a'*1000))
    with pytest.raises(ValueError, match='decoded byte limit'):
        read_source_bytes(bomb, max_decoded_bytes=100)
    bomb.write_bytes(b'broken')
    with pytest.raises(ValueError, match='Invalid or truncated'):
        read_source_bytes(bomb)
    with pytest.raises(ValueError, match='codec mismatch'):
        read_source_bytes(small['path'], storage=dict(small['storage'],codec='gzip'))


def test_sql_loader_accepts_only_declared_storage_extensions(tmp_path):
    import json
    directory = tmp_path/'data';directory.mkdir()
    payload = {'source_document':[{'document_id':'d','raw_storage':{'codec':'gzip'}}],
               'opportunity_round':[{'program_id':'p','round_id':'r'}]}
    path = directory/'sources.json';path.write_text(json.dumps(payload))
    columns = {'source_document':['document_id'],'opportunity_round':['program_id','round_id']}
    loaded,_ = load_rows(directory,columns,columns)
    assert loaded['source_document'][0]['raw_storage']['codec'] == 'gzip'
    payload['source_document'][0]['arbitrary_column'] = 'wrong'
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError,match='unexpected column'):
        load_rows(directory,columns,columns)


def test_compressed_json_manifest_still_pins_physical_container_hash(tmp_path):
    import json
    from run_benefitlens_expansion import file_ref,pinned_json
    payload = {'records':['official source']*200}
    ref = store_source_bytes(json.dumps(payload).encode(),tmp_path,suffix='.json')
    assert pinned_json(file_ref(ref['path'])) == payload
    # Blob content hashes and physical manifest pins have different meanings.
    with pytest.raises(ValueError,match='Pinned input hash mismatch'):
        pinned_json({'path':ref['path'],'sha256':ref['sha256']})


def test_compressed_uf_body_can_forbid_the_generic_portal_apply_button(tmp_path):
    from datetime import date
    from build_benefitlens_live_batch import verify_candidate_posting
    body = b'<main>DO NOT APPLY TO THIS POSTING, APPLY DIRECTLY TO EMAIL.</main>' * 20
    ref = store_source_bytes(body,tmp_path,suffix='.html')
    row = {'title':'Student Assistant','source_family':'UniversityOfFlorida/PageUp',
           'native_id':'522146','fetched_at':'2026-11-09T04:00:00+00:00',
           'status':'open','open_basis':'public listing',
           'official_url':'https://explore.jobs.ufl.edu/en-us/job/522146/',
           'application_url':'https://explore.jobs.ufl.edu/apply/522146',
           **{f'{k}_{field}':value for k in ('raw','text')
              for field,value in [('path',ref['path']),('sha256',ref['sha256']),('storage',ref['storage'])]}}
    with pytest.raises(ValueError,match='forbids its standard Apply route'):
        verify_candidate_posting(row,date(2026,11,9),{})


def test_grouping_cli_accepts_gzip_jsonl_and_pins_stored_hash(tmp_path,monkeypatch):
    import json
    from benefitlens_opportunity_groups import main
    rows = [{'source_family':'official','native_id':str(i),'issuer':'CVS Health',
             'country':country,'title':'Pharmacy Intern'} for i,country in enumerate(['US','CA'])]
    ref = store_source_bytes(''.join(json.dumps(r)+'\n' for r in rows).encode(),tmp_path,suffix='.json')
    output = tmp_path/'groups'
    monkeypatch.setattr(sys,'argv',['group','--input',ref['path'],'--output-dir',str(output)])
    main()
    manifest = json.loads(next(output.glob('manifest-*.json')).read_text())
    assert manifest['input']['sha256'] == sha256(Path(ref['path']).read_bytes()).hexdigest()
    assert manifest['all_variant_count'] == 2 and manifest['parent_groups'] == 1


def test_handoff_validates_compressed_artifacts_and_storage_hash(bundle, tmp_path):
    from benefitlens_handoff import validate_handoff
    # Use a real minimal handoff fixture rather than testing decompression alone.
    for doc in bundle['source_documents']:
        for kind in ('raw','text'):
            if not doc.get(kind+'_path'): continue
            path = Path(doc[kind+'_path'])
            content = path.read_bytes() if path.is_absolute() else (tmp_path/path).read_bytes()
            ref = store_source_bytes(content, tmp_path/'packed', suffix='.txt' if kind=='text' else '.html')
            doc[kind+'_path'] = ref['path']
            doc[kind+'_storage'] = ref['storage']
    result = validate_handoff(bundle, artifact_root=tmp_path)
    assert result['valid'], result
    bundle['source_documents'][0]['text_storage']['sha256'] = '0'*64
    assert not validate_handoff(bundle, artifact_root=tmp_path)['valid']
