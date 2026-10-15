import json
import pytest
from data_platform.saturation_search import SearchLoop,normalized,metric_delta
from data_platform.broad_collection import digest


def test_normalization_and_real_delta():
    assert normalized('  Graduate  Fellowships ')==normalized('graduate fellowship')
    assert normalized('Bursaries')=='bursary'
    assert metric_delta(dict(terms=['a']),dict(terms=['a','b'],documents=['d']))==dict(terms=1,programs=0,cases=0,documents=1)


def add_document(loop,tmp_path):
    text_path=tmp_path/'sample.txt';text_path.write_text('International travel bursaries fund doctoral research. Personal statement examples.')
    doc=dict(canonical_url='https://example.com/grant',title='Travel Bursaries',text_path=str(text_path),content_sha256='f'*64,lane='announcement',document_id='d')
    with loop.frontier.connect() as c:c.execute('INSERT INTO document VALUES(?,?)',('d',json.dumps(doc)))
    return doc


def test_term_requires_evidence_and_deduplicates(tmp_path):
    loop=SearchLoop(tmp_path);doc=add_document(loop,tmp_path)
    assert loop.add_term('travel bursary','announcement',doc['canonical_url'])==1
    assert loop.add_term('travel bursaries','announcement',doc['canonical_url'])==0
    with pytest.raises(ValueError):loop.add_term('invented scholarship','announcement',doc['canonical_url'])


def test_query_queue_is_durable_and_receipts_are_required(tmp_path):
    loop=SearchLoop(tmp_path);doc=add_document(loop,tmp_path);loop.stage()
    loop.add_term('travel bursaries','announcement',doc['canonical_url'])
    first=loop.plan();assert first['new_queued_queries']==2
    assert loop.plan()['new_queued_queries']==0
    items=loop.next(1);assert len(items)==1
    batch=dict(wave=items[0]['wave'],lane='announcement',queries=[dict(q=items[0]['q'])],query_keys=[items[0]['query_key']],response='No results returned',status='executed')
    (tmp_path/'search-001.json').write_text(json.dumps(batch))
    assert loop.reconcile()['new_search_receipts']==1
    assert loop.reconcile()['new_search_receipts']==0
    assert len(loop.next())==1
    with pytest.raises(ValueError):loop.close()


def test_two_complete_zero_stages_and_failed_url_guard(tmp_path):
    loop=SearchLoop(tmp_path)
    for n in [1,2]:
        stage=loop.stage();assert stage['wave']==n
        q=dict(q='same-scope rescan',status='executed')
        with loop.frontier.connect() as c:c.execute('INSERT INTO saturation_query VALUES(?,?,?,?,?)',(str(n),n,'announcement-world','executed',json.dumps(q)))
        assert loop.close()['saturated']==(n==2)
    assert loop.status()['saturated'] is True


def test_failed_source_cannot_close(tmp_path):
    loop=SearchLoop(tmp_path);stage=loop.stage()
    with loop.frontier.connect() as c:
        c.execute('INSERT INTO saturation_query VALUES(?,?,?,?,?)',('q',stage['wave'],'s','executed',json.dumps(dict(q='q'))))
        c.execute('INSERT INTO url VALUES(?,?,?,?)',('https://example.com/a','announcement','failed',json.dumps(dict(url='https://example.com/a'))))
    assert loop.status()['failures']==1
    with pytest.raises(ValueError):loop.close()
