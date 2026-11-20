"""Comparison keys for application routes already backed by source evidence."""

import re
from urllib.parse import urlsplit


_VARBI_HOST = re.compile(r'([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\.varbi\.com')
_VARBI_JOB = re.compile(r'/en/what:(?:job|login)/jobID:([0-9]+)/type:job/where:[0-9]+/apply:1/?')
_KTH_QUICK = re.compile(r'/en/apply/positionquick/([0-9]+)/?')


def varbi_application_identity(url: str) -> tuple[str, str] | None:
    """Match the attested Varbi routes by tenant and posting ID without rewriting URLs.

    Call only on application links from validated records. A key proves neither
    the authenticity of a newly discovered URL nor whether applications are open.
    """
    if not isinstance(url, str) or re.search(r'[\x00-\x20\x7f]', url):
        return None
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ''
    except ValueError:
        return None
    host = _VARBI_HOST.fullmatch(hostname)
    if not host or parsed.scheme != 'https' or parsed.netloc.lower() != hostname or parsed.fragment:
        return None
    path = re.sub(r'%3[aA]', ':', parsed.path)
    job = _VARBI_JOB.fullmatch(path)
    if job and not parsed.query:
        return f'varbi/{host[1]}', job[1]
    quick = _KTH_QUICK.fullmatch(path) if host[1] == 'kth' else None
    if quick and (not parsed.query or re.fullmatch(r'where=[0-9]+', parsed.query)):
        return 'varbi/kth', quick[1]
    return None


def jobbnorge_application_identity(url: str) -> tuple[str, str] | None:
    """The archived portal routes include an application ID in a SPA fragment."""
    if not isinstance(url, str) or re.search(r'[\x00-\x20\x7f]', url):
        return None
    try:
        u = urlsplit(url)
    except ValueError:
        return None
    if u.scheme != 'https' or u.query:
        return None
    if u.netloc == 'jobseeker.jobbnorge.no' and not u.fragment:
        match = re.fullmatch(r'/client/apply/([0-9]+)/?', u.path)
    elif u.netloc == 'www.jobbnorge.no' and u.path == '/jobseeker/':
        match = re.fullmatch(r'/application/apply/([0-9]+)', u.fragment)
    elif u.netloc == 'www.jobbnorge.no' and not u.fragment:
        match = re.fullmatch(r'/(?:en/available-jobs/job|ledige-stillinger/stilling)/([0-9]+)(?:/[^/]+)?/?', u.path)
    else:
        match = None
    return ('JOBBNORGE', match[1]) if match else None


def application_identity(url):
    return varbi_application_identity(url) or jobbnorge_application_identity(url)
