from pathlib import Path
import sys
import threading
from urllib.robotparser import RobotFileParser

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benefitlens_documented_api import DocumentedPublicAPI,public_post_list_access,API_HOST
from run_benefitlens_korea import Held

PUBLIC='https://public-api.wordpress.com/rest/v1.1/sites/example.wordpress.com/posts/'


@pytest.mark.parametrize('url',[
    PUBLIC+'?status=private',PUBLIC+'?status=any',PUBLIC+'?context=edit',
    PUBLIC+'?status=publish&status=draft',PUBLIC+'?number=101',PUBLIC+'?page=0',
    PUBLIC+'?callback=callback',PUBLIC+'?access_token=secret',
    PUBLIC.replace('public-api.wordpress.com','public-api.wordpress.com.example.org'),
    PUBLIC.replace('example.wordpress.com','private.example.org'),
    PUBLIC.replace('/posts/','/users/'),PUBLIC.replace('https://','http://'),
    PUBLIC.replace('/example.wordpress.com/','/example.wordpress.com%2fprivate/'),
])
def test_documented_scope_rejects_private_parameters_and_unrelated_routes(url):
    assert public_post_list_access(url) is None


def test_public_read_scope_is_bound_to_operator_documentation():
    scope=public_post_list_access(PUBLIC+'?number=100&page=1&status=publish&context=display')
    assert scope['scope']=='published_posts_only'
    assert scope['documentation'].startswith('https://developer.wordpress.com/')
    assert public_post_list_access(PUBLIC,method='POST') is None


def ledger():
    obj=DocumentedPublicAPI.__new__(DocumentedPublicAPI)
    obj.lock=threading.Lock();obj.host_locks={}
    policy=RobotFileParser();policy.parse(['User-agent: *','Disallow: /'])
    obj.policies={API_HOST:policy,'example.org':policy}
    return obj


def test_documented_api_records_its_scope_and_original_robots_decision():
    obj=ledger();calls=[]
    def fetch(url,**kwargs):
        calls.append(url);return b'{"posts":[]}',{'status':200}
    obj.fetch=fetch
    _,meta=obj.request(PUBLIC)
    assert calls==[PUBLIC]
    assert meta['robots_crawl_allows'] is False
    assert meta['access_basis']['authentication']=='none'
    assert meta['access_mode']=='documented_api_client'


def test_html_robots_denial_remains_in_force_without_a_fetch():
    obj=ledger()
    obj.fetch=lambda *_a,**_k:pytest.fail('Denied HTML must not be requested')
    with pytest.raises(Held,match='robots disallows'):
        obj.request('https://example.org/story')


def test_server_denial_is_not_retried_or_switched_to_another_identity():
    obj=ledger();calls=[]
    def fetch(url,**kwargs):
        calls.append(url);raise Held('HTTP 403')
    obj.fetch=fetch
    with pytest.raises(Held,match='HTTP 403'):
        obj.request(PUBLIC)
    assert calls==[PUBLIC]


def test_api_host_has_no_general_robots_disable():
    obj=ledger();obj.fetch=lambda *_a,**_k:pytest.fail('Unapproved route')
    with pytest.raises(Held,match='approved published-post'):
        obj.request('https://public-api.wordpress.com/rest/v1.1/me/sites')


def test_private_blog_denial_does_not_ban_unrelated_public_blogs():
    obj=ledger();obj.rate_failures={};obj.rate_holds={}
    denied=b'{"error":"authorization_required","message":"This is a private blog."}'
    assert obj.publisher_retry_at(PUBLIC,403,{},denied,12345)==0
    disabled=b'{"error":"unauthorized","message":"API calls to this endpoint have been disabled.","reason":"unauthenticated"}'
    assert obj.publisher_retry_at(PUBLIC,403,{},disabled,12345)==0
    assert obj.publisher_retry_at(PUBLIC,403,{},b'{"error":"unauthorized","message":"API authorization required"}',12345)==12345
    assert obj.publisher_retry_at(PUBLIC,403,{},b'<html>access denied</html>',12345)==12345
    assert obj.publisher_retry_at(PUBLIC,429,{'Retry-After':'10'},b'rate limit',12345)>0


def test_cached_resource_denial_stays_denied_without_poisoning_other_blogs(tmp_path):
    from datetime import datetime
    from hashlib import sha256
    import json,time
    from benefitlens_source_storage import store_source_bytes
    from run_benefitlens_campaign import KST,encoded
    from run_benefitlens_korea import Evidence
    day=datetime.now(KST).date().isoformat()
    original=Evidence(tmp_path,day)
    body=b'{"error":"unauthorized","message":"API calls to this endpoint have been disabled.","reason":"unauthenticated"}'
    meta={'url':PUBLIC,'status':403,'at':datetime.now(KST).isoformat(),
          'blocked_until':time.time()+3600,'headers':{},
          **store_source_bytes(body,tmp_path/'blobs')}
    key=sha256(encoded(['GET',PUBLIC,None,None]).encode()).hexdigest()
    with original.db() as c:
        c.execute('INSERT INTO captures VALUES(?,?,?)',(day,key,json.dumps(meta)))
    obj=DocumentedPublicAPI(tmp_path,'scope-regression',10)
    assert API_HOST not in obj.blocked
    obj.policies[API_HOST]=ledger().policies[API_HOST]
    with pytest.raises(Held,match='Stored HTTP 403'):
        obj.request(PUBLIC)
    assert obj.network_requests==0
    with obj.db() as c:
        saved=json.loads(c.execute('SELECT meta FROM captures WHERE key=?',(key,)).fetchone()[0])
    assert saved==meta
