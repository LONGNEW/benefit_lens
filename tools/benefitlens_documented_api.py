"""Provider-documented public API reading, separately from website crawling.

WordPress.com documents unauthenticated public post lists even though its API
host's robots.txt disallows indexing. This class permits that exact GET route,
preserves honest identification, host pacing, HTTP denial and rate limits, and
records the operator's documentation as the access basis. Other web URLs retain
the existing crawler's robots checks. There is no general ignore-robots flag.
"""
import re
import threading
import json
import time
from urllib.parse import parse_qs, urlsplit

from benefitlens_experience_transport import ExperienceEvidence,rate_limited
from run_benefitlens_korea import Held,UA

API_HOST='public-api.wordpress.com'
DOCUMENTATION='https://developer.wordpress.com/docs/api/getting-started/'
REFERENCE='https://developer.wordpress.com/docs/api/1/get/sites/$site/posts/'
API_TERMS='https://developer.wordpress.com/docs/api/guidelines-for-responsible-use-of-automattics-apis/'


def public_post_list_access(url, *, method='GET'):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname!=API_HOST or p.username or p.password or p.port not in (None,443):
        return None
    if method!='GET' or p.fragment:
        return None
    m=re.fullmatch(r'/rest/v1\.1/sites/([a-z0-9][a-z0-9-]*\.wordpress\.com)/posts/?',p.path)
    if not m:
        return None
    q=parse_qs(p.query,keep_blank_values=True)
    if set(q)-{'number','page','fields','status','context','search','after','before','order','order_by'}:
        return None
    if any(len(values)!=1 for values in q.values()):
        return None
    if q.get('status',['publish'])!=['publish'] or q.get('context',['display'])!=['display']:
        return None
    try:
        if not 1<=int(q.get('number',['20'])[0])<=100 or int(q.get('page',['1'])[0])<1:
            return None
    except ValueError:
        return None
    return {'kind':'provider_documented_public_api','provider':'WordPress.com',
            'route':'GET /rest/v1.1/sites/{public_blog}/posts/',
            'site':m[1],'authentication':'none','scope':'published_posts_only',
            'documentation':DOCUMENTATION,'endpoint_reference':REFERENCE,'api_terms':API_TERMS,
            'content_reuse_rights':'not_inferred_from_read_access'}


def denied_api_resource(url,status,raw):
    if not public_post_list_access(url) or status not in (401,403):
        return False
    try:
        error=json.loads(raw)
    except (ValueError,UnicodeDecodeError):
        return False
    if not isinstance(error,dict) or not error.get('error'):
        return False
    message=str(error.get('message',''))
    return bool(re.search(r'private (?:blog|site)|(?:blog|site) is private',message,re.I) or
                error.get('error')=='unauthorized' and error.get('reason')=='unauthenticated' and
                message=='API calls to this endpoint have been disabled.')


class DocumentedPublicAPI(ExperienceEvidence):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        # Older receipts used the generic collector's whole-host 12-hour hold
        # for a single disabled/private blog. Reconstruct only our local hold
        # scope; denied target URLs keep their cached denial and are not retried.
        from benefitlens_source_storage import read_source_bytes
        unknown_holds=[]
        with self.db() as c:
            rows=c.execute("SELECT meta FROM captures WHERE json_extract(meta,'$.status') IN (401,403,429)").fetchall()
        for (value,) in rows:
            meta=json.loads(value)
            if urlsplit(meta['url']).hostname!=API_HOST or meta.get('blocked_until',0)<=time.time():
                continue
            raw=read_source_bytes(meta['path'],storage=meta.get('storage'))
            if not denied_api_resource(meta['url'],meta['status'],raw):
                unknown_holds.append((meta['blocked_until'],'HTTP '+str(meta['status'])))
        if unknown_holds:
            self.blocked[API_HOST]=max(unknown_holds)
        else:
            self.blocked.pop(API_HOST,None)

    def publisher_retry_at(self,url,status,headers,raw,default):
        # A private blog is one denied resource on this shared API host. Do not
        # retry it, and do not turn that resource denial into a host-wide ban.
        # HTML challenges, unknown denials and provider rate limits stay holds.
        if denied_api_resource(url,status,raw) and not rate_limited(status,headers,raw):
            return 0
        return super().publisher_retry_at(url,status,headers,raw,default)

    def request(self,url,*,data=None,payload=None,redirect_metadata=False):
        method='POST' if data is not None or payload is not None else 'GET'
        scope=public_post_list_access(url,method=method)
        if not scope:
            if urlsplit(url).hostname==API_HOST:
                raise Held('Not an approved published-post list API request')
            return super().request(url,data=data,payload=payload,redirect_metadata=redirect_metadata)
        host=API_HOST
        with self.lock:
            lock=self.host_locks.setdefault(host,threading.Lock())
        with lock:
            if host not in self.policies:
                self.check_robots(host)
            robots_allows=self.policies[host].can_fetch(UA,url)
            raw,meta=self.fetch(url,redirect_metadata=redirect_metadata)
            return raw,dict(meta,access_basis=scope,robots_crawl_allows=robots_allows,
                            access_mode='documented_api_client')
