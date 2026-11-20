"""Capture publicly accessible international opportunity pages for evidence review."""
from argparse import ArgumentParser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import unescape
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET
from tempfile import TemporaryDirectory
from urllib.parse import urljoin, urlsplit

import requests

from benefitlens_source_storage import store_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json, single_writer
from run_benefitlens_korea import Evidence, Held, compact, soup


def projection(raw):
    markup = soup(raw)
    for node in markup.select('script,style,noscript,template'):
        node.decompose()
    text = '\n'.join(compact(line) for line in markup.stripped_strings)
    return markup, text


def project_original(raw, root):
    if raw.lstrip().startswith(b'{'):
        data = json.loads(raw)
        jobs = data.get('jobs')
        if isinstance(jobs, list) and all(isinstance(job, dict) for job in jobs):
            lines = []
            for job in jobs:
                # Greenhouse's public board API uses absolute_url/content rather
                # than Ashby's jobUrl/descriptionPlain. Preserve each post ID.
                if job.get('absolute_url') and 'content' in job and job.get('internal_job_id'):
                    _, description = projection(unescape(job['content']).encode())
                    lines.extend(str(job[key]) for key in
                                 ('id', 'internal_job_id', 'title', 'absolute_url', 'updated_at')
                                 if job.get(key))
                    if job.get('location'):
                        lines.append(json.dumps(job['location'], ensure_ascii=False))
                    lines.extend((description, ''))
                    continue
                if job.get('isListed') is False or not (job.get('jobUrl') and job.get('applyUrl')):
                    continue
                lines.extend(str(job[key]) for key in ('id', 'title', 'location', 'employmentType',
                                                      'publishedAt', 'jobUrl', 'applyUrl') if job.get(key))
                lines.extend((job.get('descriptionPlain') or '').splitlines())
                if job.get('compensation'):
                    lines.append(json.dumps(job['compensation'], sort_keys=True, ensure_ascii=False))
                lines.append('')
            return None, '\n'.join(lines)
    if raw.startswith(b'%PDF-'):
        result = subprocess.run(['pdftotext','-layout','-','-'],input=raw,check=True,
                                capture_output=True,timeout=20)
        return None, result.stdout.decode()
    if raw.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        converter = shutil.which('hwp5proc')
        if not converter:
            raise Held('HWP v5 table-aware XML extractor is unavailable')
        with TemporaryDirectory(dir=root) as folder:
            path = Path(folder) / 'notice.hwp'
            path.write_bytes(raw)
            result = subprocess.run([converter, 'xml', str(path)], check=True, capture_output=True, timeout=20)
        if len(result.stdout) > 32 * 1024 * 1024:
            raise Held('HWP XML exceeds projection byte ceiling')
        return None, project_hwp_xml(result.stdout)
    return projection(raw)


def project_hwp_xml(raw):
    """Include table-cell text that hwp5txt replaces with table placeholders."""
    try:
        document = ET.fromstring(raw)
    except ET.ParseError as error:
        raise Held('HWP XML is malformed') from error
    body = next((n for n in document.iter() if n.tag.rsplit('}', 1)[-1] == 'BodyText'), None)
    if body is None:
        raise Held('HWP XML document body absent')
    return '\n'.join(n.text for n in body.iter()
                     if n.tag.rsplit('}', 1)[-1] == 'Text' and n.text)


def capture_entry(entry, ledger):
    name, url = entry
    try:
        raw, metadata = ledger.request(url)
        markup, text = project_original(raw, ledger.root)
        if len(text) < 100:
            raise Held('No readable original text')
        return name, {'url': url, 'original': metadata,
                      'projection_version': 'benefitlens-original-v2-table-text',
                      'text': store_source_bytes(text.encode(), ledger.root / 'blobs', suffix='.txt'),
                      'title': compact(markup.title.get_text(' ', strip=True)) if markup and markup.title else None,
                      'links': [{'url': urljoin(url, a['href']), 'label': compact(a.get_text(' ', strip=True))}
                                for a in (markup.select('a[href]') if markup else [])
                                if urlsplit(urljoin(url, a['href'])).scheme == 'https']}
    except (ValueError, OSError, requests.RequestException, subprocess.SubprocessError) as exc:
        return name, {'url': url, 'held': str(exc)[:240]}


def collect(config, root):
    if not root.resolve().is_relative_to(CACHE.resolve()):
        raise ValueError('Use the shared BenefitLens dataset cache')
    root.mkdir(parents=True, exist_ok=True)
    with single_writer(root):
        day = datetime.now(KST).date().isoformat()
        entries = json.loads(config.read_text())
        ledger = Evidence(root, day)
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = dict(pool.map(lambda item: capture_entry(item, ledger), entries.items()))
        hosts = defaultdict(lambda: {'fetched': 0, 'held': 0})
        for row in results.values():
            hosts[urlsplit(row['url']).hostname]['held' if 'held' in row else 'fetched'] += 1
        folder = root / day
        folder.mkdir(exist_ok=True)
        manifest = folder / 'captures.json'
        atomic_json(manifest, {'at': datetime.now(KST).isoformat(), 'rows': results, 'hosts': dict(hosts)})
        return manifest


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'pipeline/config/crossborder_campaign.json')
    parser.add_argument('--root', type=Path, default=CACHE / 'crossborder-campaign-v1')
    args = parser.parse_args()
    print(collect(args.config, args.root))
