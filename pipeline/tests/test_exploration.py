from data_platform.exploration import case_identity, saturation, Frontier, open_search_stage


def test_open_stage_preserves_demo_fields_and_unknown_measurements():
    batches=[dict(wave=1,queries=[dict(q='scholarship'),dict(q='fellowship')]),dict(wave=2,queries=[dict(q='bursary')])]
    stage=open_search_stage(1,batches,dict(collected=3,pending=5,failed=2),7)
    assert [q['q'] for q in stage['queries']]==['scholarship','fellowship']
    assert stage['executed_queries']==2
    assert stage['accepted_terms']==[]
    assert stage['accepted_terms_status']=='not_reviewed'
    assert stage['new_terms'] is None
    assert stage['new_cases'] is None
    assert stage['rescan_complete'] is False
    assert not saturation([stage,stage])


def test_saturation_requires_complete_explicit_two_zero_waves():
    zero=dict(new_terms=0,new_programs=0,new_cases=0,new_documents=0,pending_queries=0,pending_urls=0,failures=0,inherited_pending=0,rescan_complete=True)
    assert saturation([zero,zero])
    assert not saturation([zero])
    for key in zero:
        bad={**zero,key:1 if key!='rescan_complete' else False}
        assert not saturation([zero,bad])
    assert not saturation([zero,{}])


def test_case_identity_keeps_rounds_and_anonymous_rows():
    a,_=case_identity('nsf','Alice','2026','a',1)
    b,_=case_identity('nsf','ALICE','2026','b',9)
    assert a==b
    assert a!=case_identity('nsf','Alice','2025','a',1)[0]
    assert case_identity('nsf','Alice','2026.0','a',1)[0]==a
    assert case_identity('nsf','Anonymous','2026','a',1)[0]!=case_identity('nsf','Anonymous','2026','a',2)[0]


def test_frontier_deduplicates_and_keeps_evidence_edges(tmp_path):
    f=Frontier(tmp_path)
    assert f.enqueue('https://example.com/a','experience',parent='seed:1')
    assert not f.enqueue('https://example.com/a','experience',parent='seed:2')
    with f.connect() as c:
        assert c.execute('SELECT count(*) FROM edge').fetchone()[0]==2
    assert f.enqueue('https://www.reddit.com/x','mixed')
    assert f.stats()['urls']['excluded_scope']==1
    assert f.enqueue('https://www.reddit.com/r/GRFPApps/comments/1','experience',label='GRFP interview experience')
    assert f.stats()['urls']['pending']==2


def test_drive_viewer_and_public_download_share_frontier_identity(tmp_path):
    f=Frontier(tmp_path)
    assert f.enqueue('https://drive.google.com/file/d/abc/view','experience')
    assert not f.enqueue('https://drive.google.com/open?id=abc','experience')
    assert not f.enqueue('https://drive.google.com/uc?export=download&id=abc','experience')


def test_collector_passes_document_bytes_to_extractor(tmp_path,monkeypatch):
    from data_platform.http import FetchResult
    f=Frontier(tmp_path)
    url='https://example.com/a.pdf'
    f.enqueue(url,'experience')
    monkeypatch.setattr(f,'request',lambda u:FetchResult(u,u,200,'application/pdf',{},b'%PDF-test'))
    def extract(body,kind):
        assert isinstance(body,bytes)
        return 'Example fellowship research statement with enough extracted text for collection.'
    monkeypatch.setattr('data_platform.exploration.extract_document',extract)
    assert f.collect(dict(url=url,lane='experience',depth=0))=='collected'
