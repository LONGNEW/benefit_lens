"""Provider-aware backoff for the experience collector, without authentication."""
from datetime import datetime
import json
import time
from urllib.parse import parse_qs, urlsplit

from benefitlens_source_storage import read_source_bytes
from import_benefitlens_full_inventory import SharedEvidence
from run_benefitlens_campaign import CACHE, KST, encoded, retry_after
from run_benefitlens_korea import Held, MAX_RESPONSE_BYTES, UA


class PublisherPacing(Held):
    def __init__(self, next_at):
        super().__init__('Publisher crawl pacing deferred to the durable queue')
        self.next_at = next_at


def rate_limited(status, headers, raw):
    headers = {k.lower():v for k,v in headers.items()}
    return status == 429 or status == 403 and (
        headers.get('x-ratelimit-remaining') == '0' or b'rate limit' in raw.lower())


def retry_time(headers, now, fallback=300):
    headers = {k.lower():v for k,v in headers.items()}
    times = []
    if headers.get('retry-after'):
        times.append(now+retry_after(headers['retry-after'],now))
    if headers.get('x-ratelimit-remaining') == '0':
        try:
            times.append(float(headers['x-ratelimit-reset'])+1)
        except (KeyError,ValueError):
            pass
    return max([now+1, *times]) if times else now+fallback


class ExperienceEvidence(SharedEvidence):
    host_intervals = {'dev.to':5}

    def __init__(self, root, run_id, limit):
        super().__init__(root,datetime.now(KST).date().isoformat(),limit)
        self.lifetime_requests_at_start=self.network_requests
        self.network_requests=0
        self.run_id=run_id
        self.rate_holds={}
        self.rate_failures={}
        self.quota_observation=None
        self.quota_retry_at=None
        path=CACHE/'experience-api-audit-v1/github-quota.json'
        if path.exists():
            receipt=json.loads(path.read_text())
            meta=receipt['original']
            age=time.time()-datetime.fromisoformat(meta['at']).timestamp()
            if meta['status']==200 and 0<=age<3600:
                from hashlib import sha256
                raw=read_source_bytes(meta['path'],storage=meta['storage'])
                if sha256(raw).hexdigest()!=meta['sha256']:
                    raise ValueError('GitHub quota receipt failed integrity')
                core=json.loads(raw)['resources']['core']
                self.quota_observation=datetime.fromisoformat(meta['at']).timestamp()
                # Use response headers first, as required by GitHub documentation.
                h=meta.get('headers',{})
                remaining=int(h.get('x-ratelimit-remaining',core['remaining']))
                reset=float(h.get('x-ratelimit-reset',core['reset']))
                self.quota_retry_at=reset+1 if remaining==0 else 0
        with self.db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS request_run(run_id TEXT PRIMARY KEY,start_count INTEGER,started_at REAL,request_limit INTEGER)')
            c.execute('INSERT INTO request_run VALUES(?,?,?,?)',
                (run_id,self.lifetime_requests_at_start,time.time(),limit))
            for (item,) in c.execute("""SELECT meta FROM captures WHERE json_extract(meta,'$.status') IN (403,429)
                OR json_extract(meta,'$.headers.\"x-ratelimit-remaining\"')='0'"""):
                meta=json.loads(item)
                raw=read_source_bytes(meta['path'],storage=meta.get('storage'))
                if not rate_limited(meta['status'],meta.get('headers',{}),raw) and not (
                        meta['status']==200 and meta.get('headers',{}).get('x-ratelimit-remaining')=='0'):
                    continue
                host=urlsplit(meta['url']).hostname
                until=meta.get('blocked_until',0)
                if self.quota_applies(meta):
                    until=self.quota_retry_at
                self.rate_holds[host]=max(until,self.rate_holds.get(host,0))
        if 'api.github.com' in self.rate_holds:
            # Replace only our coarse API-rate hold, with a newer public quota receipt.
            self.blocked['api.github.com']=(self.rate_holds['api.github.com'],'provider rate limit')

    def quota_applies(self, meta):
        return self.quota_observation is not None and urlsplit(meta['url']).hostname=='api.github.com' \
            and datetime.fromisoformat(meta['at']).timestamp()<self.quota_observation

    def retry_cached_error(self, meta):
        if meta['status'] not in (403,429):
            return False
        raw=read_source_bytes(meta['path'],storage=meta.get('storage'))
        if not rate_limited(meta['status'],meta.get('headers',{}),raw):
            return False
        until=self.quota_retry_at if self.quota_applies(meta) else meta.get('blocked_until',0)
        return until<=time.time()

    def response_byte_limit(self, url):
        # The observed public repository contains about 170 MB of source files.
        # Keep archive transport bounded below the shared source-store limit.
        u=urlsplit(url)
        if (u.hostname=='codeload.github.com' or
                u.hostname=='api.github.com' and '/tarball/' in u.path):
            return 240*1024**2
        # Observed university reports bundle several students in one public PDF.
        if (u.hostname == 'eduinno.smu.ac.kr' and u.path == '/oia/program/ssap.do'
                and parse_qs(u.query).get('mode') == ['download']):
            return 32*1024**2
        return MAX_RESPONSE_BYTES

    def publisher_retry_at(self, url, status, headers, raw, default):
        host=urlsplit(url).hostname
        if rate_limited(status,headers,raw):
            n=self.rate_failures.get(host,0)+1
            self.rate_failures[host]=n
            until=retry_time(headers,time.time(),min(3600,300*2**min(n-1,4)))
            self.rate_holds[host]=until
            return until
        if status==200:
            h={k.lower():v for k,v in headers.items()}
            if h.get('x-ratelimit-remaining')=='0':
                until=retry_time(h,time.time())
                self.rate_holds[host]=until
                self.blocked[host]=(until,'provider rate limit')
                return until
            else:
                self.rate_failures[host]=0
        return default

    def fetch(self,url,**kwargs):
        host=urlsplit(url).hostname
        if getattr(self,'defer_long_waits',False) and not kwargs.get('robots') and host in self.policies:
            from hashlib import sha256
            method='POST' if kwargs.get('data') is not None or kwargs.get('payload') is not None else 'GET'
            key=sha256(encoded([method,url,kwargs.get('data'),kwargs.get('payload')]).encode()).hexdigest()
            with self.db() as c:
                cached=c.execute('SELECT 1 FROM captures WHERE day=? AND key=?',(self.day,key)).fetchone()
                last=c.execute('SELECT max(at) FROM network_call WHERE url LIKE ?',('https://'+host+'/%',)).fetchone()[0]
            delay=max(2,self.policies[host].crawl_delay(UA) or 0,self.host_intervals.get(host,2))
            if not cached and last and last+delay>time.time()+1:
                raise PublisherPacing(last+delay)
        until,reason=self.blocked.get(urlsplit(url).hostname,(0,''))
        if until>time.time():
            from hashlib import sha256
            method='POST' if kwargs.get('data') is not None or kwargs.get('payload') is not None else 'GET'
            key=sha256(encoded([method,url,kwargs.get('data'),kwargs.get('payload')]).encode()).hexdigest()
            with self.db() as c:
                cached=c.execute('SELECT 1 FROM captures WHERE day=? AND key=?',(self.day,key)).fetchone()
            if not cached:
                raise Held('Publisher paused: '+reason)
        return super().fetch(url,**kwargs)
