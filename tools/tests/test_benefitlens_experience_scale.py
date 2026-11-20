import json
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scale_benefitlens_experiences import ExperienceFrontier, higherin_detail, list_children, failure, dev_detail, github_proposal, reference_episode
from scale_benefitlens_experiences import wordpress_detail, process, archive_members
from scale_benefitlens_experiences import supporting_material_patch
from benefitlens_experience_transport import rate_limited, retry_time
from benefitlens_source_storage import store_source_bytes, read_source_bytes


def review_html(kind='Internship (1 Month+)', answer=None):
    answer=answer or 'I worked with customers and learned how to explain the available options. '*4
    return ('''<script type="application/ld+json">'''+json.dumps({
        '@type':'Review','datePublished':'2025-05-13','author':{'name':'Anonymous'}})+'''</script>
        <main><h1>Intern at Example</h1><p>'''+kind+'''</p><h2>Overall Rating</h2>
        <h4>Please give an overview of your role</h4><div>'''+answer+'''</div>
        <h4>What advice would you give to others applying?</h4><div>Prepare well.</div>
        <aside>Related: Internship (1 Month+)</aside></main>''').encode()


def test_review_preserves_unknown_cycle_outcome_and_funding():
    r=higherin_detail(review_html(),'https://higherin.com/review/123/example/intern')
    assert r['status']=='accepted'
    assert r['cycle'] is None and r['published_at']=='2025-05-13'
    assert r['outcome']=='unknown' and r['funding_status']=='not_confirmed'
    assert r['evidence_roles']==['participation_experience']
    for e in r['evidence']:
        assert r['text'][e['char_start']:e['char_end']]==e['exact_quote']


def test_related_intern_card_cannot_turn_graduate_job_into_internship():
    r=higherin_detail(review_html('Graduate Job'),'https://higherin.com/review/123/example/intern')
    assert r['status']=='rejected_scope'


def test_educational_apprenticeships_are_experiences_without_inferred_pay():
    for kind in ['Degree Apprenticeship','Level 4/5 Apprenticeship','Level 3 Apprenticeship']:
        r=higherin_detail(review_html(kind),'https://higherin.com/review/123/example/apprentice')
        assert r['status']=='accepted' and r['opportunity_type']=='apprenticeship'
        assert r['funding_status']=='not_confirmed'
    r=higherin_detail(review_html('Graduate Scheme'),'https://higherin.com/review/123/example/job')
    assert r['status']=='rejected_scope'


def test_expanded_scope_replay_preserves_old_decision_and_needs_no_download(tmp_path):
    from scale_benefitlens_experiences import VERSION
    q=ExperienceFrontier(tmp_path)
    q.add('higherin',{'url':'https://higherin.com/review/123/example/apprentice','kind':'detail'})
    task=q.lease('old',1)[0]
    q.finish(task,{'status':'rejected_scope','reason':'old internship-only scope',
                  'document':{'document_id':'d','parser_version':'experience-scale-v1.3'}})
    assert q.reparse_expanded_scope()==1
    replay=q.lease('new',1)[0]
    assert replay['reuse_document']['document_id']=='d'
    assert replay['previous_scope_decision']['reason']=='old internship-only scope'
    q.finish(replay,{'status':'rejected_scope','document':{'document_id':'d','parser_version':VERSION}})
    assert q.reparse_expanded_scope()==0


def test_rating_only_review_is_not_an_experience():
    with pytest.raises(ValueError,match='insufficient_body'):
        higherin_detail(review_html(answer='Great!'),'https://higherin.com/review/123/example/intern')


def test_sitemap_has_no_fabricated_paths_and_ignores_external_urls():
    xml=b'''<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://higherin.com/review/10/a/intern</loc></url>
      <url><loc>https://other.example/review/12/a/intern</loc></url></urlset>'''
    source={'adapter':'higherin','allowed_hosts':['higherin.com'],
            'detail_pattern':r'https://higherin\.com/review/\d+/'}
    assert list_children(xml,{'kind':'sitemap','url':'https://higherin.com/sitemap.xml'},source)==[
        {'url':'https://higherin.com/review/10/a/intern','kind':'detail'}]


def test_frontier_restart_and_expired_lease_do_not_lose_discovery(tmp_path):
    q=ExperienceFrontier(tmp_path)
    seed={'url':'https://example.org/index','kind':'index'}
    assert q.add('source',seed)
    assert not q.add('source',seed)
    task=q.lease('first',1)[0]
    with q.connect() as c:
        c.execute('UPDATE experience_task SET lease_until=?',(time.time()-1,))
    q2=ExperienceFrontier(tmp_path)
    replay=q2.lease('second',1)[0]
    assert replay['key']==task['key']
    child={'url':'https://example.org/case','kind':'detail'}
    q2.finish(replay,{'status':'indexed'},children=[child,child])
    pending=q2.lease('second',10)
    assert len(pending)==1 and pending[0]['url']==child['url']
    assert not q2.lease('other',10)


def test_content_duplicate_and_case_identity_have_separate_counters(tmp_path):
    q=ExperienceFrontier(tmp_path)
    for i in range(2):
        q.add('source',{'url':f'https://example.org/{i}','kind':'detail'})
        task=q.lease('r',1)[0]
        q.finish(task,{'status':'accepted','case_key':f'c{i}',
            'text_key':'same substantive answer','document':{'document_id':f'd{i}'},
            'evidence_roles':['participation_experience']})
    snap=q.snapshot()
    assert snap['accepted_cases']==1
    assert snap['sources']['source']['detail']['duplicate']==1


def test_publisher_denial_and_our_budget_are_not_saturation():
    assert failure(ValueError('HTTP 403'),{})['status']=='policy_held'
    assert failure(ValueError('Local task network budget reached'),{})['status']=='pending'
    assert failure(ValueError('HTTP 429'),{})['status']=='retry'
    assert failure(ValueError('Publisher paused: HTTP 429'),{})['status']=='retry'


def test_dev_published_year_is_not_round_and_same_author_is_not_many_cases():
    data={'id':1,'url':'https://dev.to/alice/story','published_at':'2026-11-01',
          'user':{'name':'Alice','username':'alice'},
          'body_html':'<p>I submitted my Outreachy application after preparing with mentors. '+('I learned new skills. '*30)+'</p>'}
    a=dev_detail(json.dumps(data).encode(),'https://dev.to/api/articles/1')
    b=dev_detail(json.dumps(dict(data,id=2)).encode(),'https://dev.to/api/articles/2')
    assert a['status']=='accepted' and a['cycle'] is None
    assert a['outcome']=='unknown' and a['case_key']==b['case_key']


def test_replay_keeps_both_documents_for_one_episode(tmp_path):
    q=ExperienceFrontier(tmp_path)
    for i in range(2):
        q.add('s',{'url':f'https://example.org/{i}','kind':'detail'})
        task=q.lease('r',1)[0]
        q.finish(task,{'status':'accepted','case_key':'one-person-one-round',
            'text_key':f'body-{i}','document':{'document_id':f'd{i}'},
            'evidence':[{'document_id':f'd{i}','char_start':0,'char_end':2}],
            'evidence_roles':['application_process']})
    with q.connect() as c:
        item=json.loads(c.execute('SELECT result FROM experience_result').fetchone()[0])
    assert len(item['documents'])==2 and len(item['evidence'])==2
    assert q.snapshot()['accepted_cases']==1


def test_documented_api_pagination_deduplicates_native_article_urls():
    source={'adapter':'dev_api','max_index_pages':10}
    task={'kind':'api_index','url':'https://dev.to/api/articles?tag=gsoc&per_page=100&page=1'}
    found=list_children(b'[{"id":42,"url":"https://dev.to/alice/one"}]',task,source)
    assert found[0]['url']=='https://dev.to/api/articles/42'
    assert 'page=2' in found[1]['url']
    assert list_children(b'[]',task,source)==[]


def test_corrupt_github_original_is_not_counted(tmp_path):
    import base64
    raw=json.dumps({'encoding':'base64','content':base64.b64encode(b'%PDF fake').decode(),
                    'sha':'wrong'}).encode()
    with pytest.raises(ValueError,match='Git blob hash'):
        github_proposal(raw,{'source':'github','file_path':'x.pdf'},tmp_path)


def test_reference_requires_own_action_and_program_in_body(monkeypatch,tmp_path):
    import scale_benefitlens_experiences as module
    monkeypatch.setattr(module,'project_original',lambda *_:(None,
        'Erasmus guidelines: Submit the required documents. '*30))
    assert reference_episode(b'x','https://university.example/report.pdf','heidelberg-erasmus',tmp_path)['status']=='needs_review'
    monkeypatch.setattr(module,'project_original',lambda *_:(None,
        'Ich habe im Wintersemester 2023/24 ein Auslandssemester mit Erasmus gemacht.\n'+
        'Die Vorbereitung war sehr hilfreich. '*30))
    r=reference_episode(b'x','https://university.example/report.pdf','heidelberg-erasmus',tmp_path)
    assert r['status']=='accepted' and r['cycle']=='Wintersemester 2023/24'
    assert r['outcome']=='unknown'


def test_restart_recovers_only_the_owned_frontier_tasks(tmp_path):
    q=ExperienceFrontier(tmp_path)
    q.add('s',{'url':'https://example.org/one','kind':'detail'})
    q.lease('crashed',1)
    q.recover_interrupted()
    assert len(q.lease('resumed',1))==1


def test_busy_host_does_not_stop_an_independent_source(tmp_path):
    q=ExperienceFrontier(tmp_path)
    q.add('a',{'url':'https://one.example/a','kind':'detail'})
    q.add('b',{'url':'https://two.example/b','kind':'detail'})
    tasks=q.lease('r',6,busy_hosts=['one.example'])
    assert [t['source'] for t in tasks]==['b']


def test_backoff_defers_network_but_not_already_collected_batch(tmp_path):
    q=ExperienceFrontier(tmp_path)
    q.add('s',{'url':'https://one.example/a','kind':'detail'})
    q.add('s',{'url':'https://one.example/b','kind':'detail','batch_original':{'path':'stored'}})
    q.defer_rate_hosts({'one.example':time.time()+600})
    tasks=q.lease('r',6)
    assert len(tasks)==1 and tasks[0]['url'].endswith('/b')
    assert q.next_ready_at()>time.time()


def test_rate_limit_headers_take_precedence_and_access_denial_stays_distinct():
    assert retry_time({'Retry-After':'90','X-RateLimit-Remaining':'0','X-RateLimit-Reset':'1200'},1000)==1201
    assert retry_time({'Retry-After':'90'},1000)==1090
    assert rate_limited(403,{},b'API rate limit exceeded')
    assert not rate_limited(403,{},b'Access denied')
    assert rate_limited(429,{},b'Wait')


def fulbright_record(record_id=10):
    return {'id':record_id,'link':'https://www.fulbright.or.kr/alice-kim/',
        'title':{'rendered':'Alice Kim'},'date':'2026-10-20',
        'excerpt':{'rendered':'2026 International Education Administrators'},
        'content':{'rendered':'<p>Alice Kim<br>2023 Graduate Student Program</p><p>'+
          '저는 풀브라이트를 통해 연구하고 새로운 경험을 할 수 있었습니다. '*25+'</p>'},
        '_links':{'self':[{'href':f'https://www.fulbright.or.kr/wp-json/wp/v2/posts/{record_id}'}]}}


def test_wordpress_program_and_round_come_from_body_not_mislabelled_excerpt():
    r=wordpress_detail(fulbright_record(),{})
    assert r['status']=='accepted' and r['cycle']=='2023'
    assert r['program_id']=='fulbright-graduate-student-program'
    assert r['outcome']=='unknown'
    for evidence in r['evidence']:
        assert r['text'][evidence['char_start']:evidence['char_end']]==evidence['exact_quote']


def test_wordpress_batch_body_is_used_without_a_detail_http_request(tmp_path):
    from types import SimpleNamespace
    data=[fulbright_record()]
    raw=json.dumps(data,ensure_ascii=False).encode()
    meta=dict(url='https://www.fulbright.or.kr/wp-json/wp/v2/posts?per_page=100&page=1',
              status=200,at='2026-11-15T18:00:00+09:00',headers={'x-wp-totalpages':'2'},
              **store_source_bytes(raw,tmp_path/'blobs'))
    source={'adapter':'wordpress_api','allowed_hosts':['www.fulbright.or.kr']}
    children=list_children(raw,{'kind':'wordpress_index','url':meta['url']},source,meta)
    task=dict(children[0],source='fulbright-korea-api')
    ledger=SimpleNamespace(root=tmp_path)
    result,_=process(task,source,ledger)
    assert result['status']=='accepted'
    doc=result['document']
    assert doc['raw']['sha256']==meta['sha256'] and doc['record_pointer']=='/0'
    assert read_source_bytes(doc['raw']['path'],storage=doc['raw']['storage'])==raw
    assert 'page=2' in children[1]['url']


def test_batch_discovery_upgrades_waiting_task_but_keeps_finished_case(tmp_path):
    q=ExperienceFrontier(tmp_path)
    q.add('s',{'url':'https://one.example/index','kind':'index'})
    index=q.lease('r',1)[0]
    q.add('s',{'url':'https://one.example/detail','kind':'detail'})
    with q.connect() as c:
        c.execute("UPDATE experience_task SET state='waiting_bulk' WHERE kind='detail'")
    q.finish(index,{'status':'indexed'},children=[{'url':'https://one.example/detail','kind':'detail',
             'archive_original':{'path':'stored'}}])
    task=q.lease('r',1)[0]
    assert task['archive_original']['path']=='stored'


def test_archive_never_extracts_path_traversal_to_filesystem(tmp_path):
    import io,tarfile
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w:gz') as tar:
        member=tarfile.TarInfo('repo/../../escape.pdf');member.size=4
        tar.addfile(member,io.BytesIO(b'%PDF'))
    receipt=store_source_bytes(stream.getvalue(),tmp_path/'blobs')
    with pytest.raises(ValueError,match='Unsafe archive'):
        archive_members(receipt['path'],receipt['sha256'])
    assert not (tmp_path/'escape.pdf').exists()


def test_expired_temporary_error_is_retried_and_old_receipt_is_preserved(tmp_path,monkeypatch):
    from urllib.robotparser import RobotFileParser
    import run_benefitlens_korea as module
    from run_benefitlens_campaign import encoded
    from hashlib import sha256
    ledger=module.Evidence(tmp_path,'2026-11-15')
    ledger.retry_cached_error=lambda m:m['status']==429 and m['blocked_until']<=time.time()
    url='https://one.example/api'
    key=sha256(encoded(['GET',url,None,None]).encode()).hexdigest()
    old=dict(url=url,status=429,blocked_until=time.time()-10,
             **store_source_bytes(b'rate limited',tmp_path/'blobs'))
    with ledger.db() as c:c.execute('INSERT INTO captures VALUES(?,?,?)',(ledger.day,key,encoded(old)))
    parser=RobotFileParser();parser.parse(['User-agent: *','Allow: /'])
    ledger.policies['one.example']=parser
    class Response:
        status_code=200
        headers={'content-type':'application/json','X-RateLimit-Remaining':'3'}
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def iter_content(self,_):yield b'{"ok":true}'
    class Session:
        def request(self,*_,**__):return Response()
    ledger.local.session=Session()
    raw,meta=ledger.fetch(url)
    assert raw==b'{"ok":true}' and meta['headers']['x-ratelimit-remaining']=='3'
    with ledger.db() as c:
        assert json.loads(c.execute('SELECT meta FROM capture_attempt').fetchone()[0])['status']==429
        assert json.loads(c.execute('SELECT meta FROM captures').fetchone()[0])['status']==200


def test_legacy_duplicate_keeps_every_original_without_changing_outcome():
    old={'document_ids':['old-doc'],'evidence':[{'document_id':'old-doc','exact_quote':'old'}],
         'outcome':'unknown','approved_pattern':False}
    new={'evidence':[{'document_id':'new-local','exact_quote':'new'}],
         'evidence_roles':['application_process']}
    patch=supporting_material_patch(old,new,{'new-local':'new-pg'},'new-case')
    combined=old|patch
    assert set(combined['document_ids'])=={'old-doc','new-pg'}
    assert len(combined['evidence'])==2 and combined['outcome']=='unknown'
    assert combined['approved_pattern'] is False
    replay=supporting_material_patch(combined,new,{'new-local':'new-pg'},'new-case')
    assert len(replay['evidence'])==2 and len(replay['supporting_collections'])==1


def test_full_rss_is_preserved_as_material_until_named_subject_review(tmp_path):
    from types import SimpleNamespace
    raw=b'''<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item>
      <link>https://www.chevening.org/news/example/</link><guid>story-1</guid>
      <content:encoded><![CDATA[<p>A story about multiple scholars.</p>]]></content:encoded>
      </item></channel></rss>'''
    meta=dict(url='https://www.chevening.org/news/feed/',status=200,at='2026-11-15T18:00:00+09:00',
              **store_source_bytes(raw,tmp_path/'blobs'))
    source={'adapter':'rss_fulltext','allowed_hosts':['www.chevening.org']}
    child=list_children(raw,{'kind':'rss_index','url':meta['url']},source,meta)[0]
    result,_=process(dict(child,source='chevening-rss'),source,SimpleNamespace(root=tmp_path))
    assert result['status']=='reference_material' and 'case_key' not in result
    assert result['document']['kind']=='xml' and result['document']['record_pointer']=='/channel/item/0'


def test_bulk_archive_validates_tree_hashes_and_avoids_per_file_requests(tmp_path,monkeypatch):
    import io,tarfile
    from hashlib import sha1
    from types import SimpleNamespace
    import scale_benefitlens_experiences as module
    name='2026/Example - 2026 - Alice Kim.pdf'
    content=b'%PDF-1.4\nsource-original'
    blob=sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w:gz') as tar:
        member=tarfile.TarInfo('repo-snapshot/'+name);member.size=len(content)
        tar.addfile(member,io.BytesIO(content))
    raw=stream.getvalue()
    archive_url='https://api.github.com/repos/example/repo/tarball'
    tree_url='https://api.github.com/repos/example/repo/git/trees/tree'
    blob_url='https://api.github.com/repos/example/repo/git/blobs/'+blob
    tree=json.dumps({'truncated':False,'tree':[
        {'type':'blob','path':name,'sha':blob,'url':blob_url},
        {'type':'blob','path':'absent.pdf','sha':'wrong','url':'https://api.github.com/repos/example/repo/git/blobs/abcd'}]}).encode()
    calls=[]
    class Ledger:
        root=tmp_path
        def request(self,url,**kwargs):
            calls.append(url)
            data=raw if url==archive_url else tree
            return data,dict(url=url,status=200,at='2026-11-15T18:00:00+09:00',
                **store_source_bytes(data,tmp_path/'blobs'))
    source={'adapter':'github_proposals','tree_url':tree_url,'allowed_hosts':['api.github.com']}
    result,children=process({'kind':'github_archive','url':archive_url,'source':'github'},source,Ledger())
    assert result['observed_children']==1 and result['expected_pdf_count']==2
    assert calls==[archive_url,tree_url]
    monkeypatch.setattr(module,'project_original',lambda *_:(None,'Alice Kim GSoC 2026\n'+
        'I propose to implement this feature. '*30))
    child=dict(children[0],source='github')
    result,_=process(child,source,SimpleNamespace(root=tmp_path))
    assert result['status']=='accepted' and result['document']['git_blob_sha1']==blob
    assert result['document']['raw']['transport']=='verified_archive_member'
    assert calls==[archive_url,tree_url]
