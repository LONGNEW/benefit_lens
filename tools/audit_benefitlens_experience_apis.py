"""Inspect documented APIs and publisher-advertised discovery links, read-only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import sys
from urllib.parse import urljoin, urlsplit

import requests

import project
from benefitlens_source_storage import store_source_bytes
from import_benefitlens_full_inventory import SharedEvidence
from run_benefitlens_campaign import CACHE, KST, atomic_json
from run_benefitlens_korea import UA, soup

ROOT = CACHE/'experience-api-audit-v1'
PAGES = {
    'higherin': 'https://higherin.com/',
    'daad-rise': 'https://www.daad.de/rise/en/rise-germany/about-the-program/reports/',
    'heidelberg': 'https://www.mathinf.uni-heidelberg.de/en/outreach/international-affairs/erasmus-student-reports',
    'chevening': 'https://www.chevening.org/news/',
    'gates-cambridge': 'https://www.gatescambridge.org/',
    'fulbright-korea': 'https://www.fulbright.or.kr/my-fulbright-story/',
}


def inspect_page(item):
    name, url = item
    (ROOT/name).mkdir(parents=True,exist_ok=True)
    ledger = SharedEvidence(ROOT/name, datetime.now(KST).date().isoformat(), 30)
    result = {'source': name, 'page': url, 'authentication': 'none'}
    try:
        raw, meta = ledger.request(url, redirect_metadata=True)
        result['page_original'] = meta
        if meta['status'] != 200:
            result.update(state='redirect_or_hold', status=meta['status'])
            return result
        doc = soup(raw)
        links = [{'rel': a.get('rel'), 'url': urljoin(url, a['href']), 'type': a.get('type')}
                 for a in doc.select('link[href]')
                 if any(x in str(a) for x in ['api.w.org', 'application/rss', 'application/atom', 'oembed'])]
        result['advertised_links'] = links
        api = next((x['url'] for x in links if '/wp-json/' in x['url']), None)
        if not api:
            result['state'] = 'no_public_api_advertised_on_observed_page'
            return result
        base = api.split('/wp-json/')[0]+'/wp-json/'
        if urlsplit(base).hostname != urlsplit(url).hostname:
            result['state'] = 'cross_host_api_needs_review'
            return result
        result['api_base'] = base
        api_raw, api_meta = ledger.request(base)
        data = json.loads(api_raw)
        result['api_original'] = api_meta
        result['namespaces'] = data.get('namespaces', [])
        routes = data.get('routes', {})
        result['readable_content_routes'] = [r for r in routes if r in
            ('/wp/v2/posts', '/wp/v2/pages', '/wp/v2/media')]
        result['state'] = 'public_wordpress_api_observed'
        if '/wp/v2/posts' in routes:
            posts, receipt = ledger.request(base+'wp/v2/posts?per_page=1')
            records = json.loads(posts)
            result['sample_original'] = receipt
            result['reported_total_posts'] = receipt['headers'].get('x-wp-total')
            result['sample_has_complete_content_field'] = bool(records and records[0].get('content',{}).get('rendered'))
            result['sample_public_link'] = records[0].get('link') if records else None
    except (ValueError, OSError, requests.RequestException) as error:
        result.update(state='not_usable_observed', reason=str(error)[:400])
    return result


def github_quota():
    """GitHub explicitly exempts GET /rate_limit from the primary quota.

    This is one status check, not a content request or a secondary-limit bypass.
    https://docs.github.com/en/rest/rate-limit/rate-limit
    """
    url = 'https://api.github.com/rate_limit'
    with requests.get(url, headers={'User-Agent': UA, 'Accept': 'application/vnd.github+json'},
                      timeout=(10,20), allow_redirects=False, stream=True) as response:
        raw = response.raw.read(1024*1024+1, decode_content=True)
        if len(raw)>1024*1024:
            raise ValueError('Quota status exceeds byte ceiling')
        meta = dict(url=url,status=response.status_code,at=datetime.now(KST).isoformat(),
                    headers={k.lower():v for k,v in response.headers.items() if
                             k.lower().startswith('x-ratelimit-') or k.lower() in ('retry-after','date','content-type')},
                    **store_source_bytes(raw,ROOT/'blobs',suffix='.json'))
        data = json.loads(raw) if response.status_code==200 else {}
        result = dict(original=meta,core=data.get('resources',{}).get('core'),
                      documentation='https://docs.github.com/en/rest/rate-limit/rate-limit')
        atomic_json(ROOT/'github-quota.json',result)
        return result


def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    import shutil
    if shutil.disk_usage(ROOT).free < 2*1024**3:
        raise ValueError('Shared cache storage reserve reached')
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(inspect_page,PAGES.items()))
    quota = github_quota()
    report = dict(at=datetime.now(KST).isoformat(),pages=results,github_quota=quota,
                  public_api_absence_not_proven=True)
    atomic_json(ROOT/'audit.json',report)
    for result in results:
        print(json.dumps({k:v for k,v in result.items() if k not in
              ('page_original','api_original','sample_original')},ensure_ascii=False),flush=True)
    print(json.dumps({'github_quota':quota.get('core'),'receipt':str(ROOT/'github-quota.json')}),flush=True)


if __name__=='__main__':
    main()
