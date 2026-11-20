from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_campaign_identity import varbi_application_identity


@pytest.mark.parametrize('job_id', ['981772', '986999'])
def test_euraxess_application_link_and_varbi_login_share_posting_identity(job_id):
    indexed = f'https://lu.varbi.com/en/what:job/jobID:{job_id}/type:job/where:39/apply:1'
    login = f'https://lu.varbi.com/en/what:login/jobID:{job_id}/type:job/where:4/apply:1/'
    assert varbi_application_identity(indexed) == varbi_application_identity(login) == ('varbi/lu', job_id)


def test_distinct_tenants_and_ids_do_not_merge():
    urls = [f'https://{tenant}.varbi.com/en/what:login/jobID:{job}/type:job/where:4/apply:1/'
            for tenant, job in [('lu', '981772'), ('lu', '986999'), ('aau', '981772')]]
    assert len({varbi_application_identity(url) for url in urls}) == len(urls)


def test_attested_kth_quick_route_and_encoded_colons():
    assert varbi_application_identity('https://kth.varbi.com/en/apply/positionquick/981104/?where=4') == ('varbi/kth', '981104')
    assert varbi_application_identity('https://lu.varbi.com/en/what%3Alogin/jobID%3a981772/type:job/where:4/apply:1/') == ('varbi/lu', '981772')


@pytest.mark.parametrize('url', [
    'https://lu.varbi.com.example.org/en/what:login/jobID:981772/type:job/where:4/apply:1/',
    'https://guest@lu.varbi.com/en/what:login/jobID:981772/type:job/where:4/apply:1/',
    'https://lu.varbi.com:8443/en/what:login/jobID:981772/type:job/where:4/apply:1/',
    'http://lu.varbi.com/en/what:login/jobID:981772/type:job/where:4/apply:1/',
    'https://lu.varbi.com/en/other/what:login/jobID:981772/type:job/where:4/apply:1/',
    'https://lu.varbi.com/en/what:login/jobID:981772/type:job/where:4/apply:0/',
    'https://lu.varbi.com/en/what:login/jobID:981772/type:job/where:4/apply:1/?jobID=986999',
    'https://lu.varbi.com/en/what:login/jobID:981772%2Ftype:job/where:4/apply:1/',
    'https://lu.varbi.com/en/what:login/jobID:981772/type:job/where:4/apply:1/\n',
    'https://lu.varbi.com/en/apply/positionquick/981104/?where=4',
    'https://kth.varbi.com/en/apply/positionquick/981104/?where=4&jobID=986999',
])
def test_unattested_routes_do_not_produce_duplicate_keys(url):
    assert varbi_application_identity(url) is None
