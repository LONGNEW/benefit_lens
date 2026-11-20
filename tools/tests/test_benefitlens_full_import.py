from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from import_benefitlens_full_inventory import capture_ref, extract_clauses, project_body, public_url, read_ref


def test_http_failure_is_not_an_original_even_when_a_file_exists():
    assert capture_ref({'status':403,'path':'/x','sha256':'a'}) is None
    assert capture_ref({'status':200,'path':'/x','sha256':'a'})['path']=='/x'


def test_authentication_or_insecure_location_is_not_a_capture_target():
    assert not public_url('http://example.org/notice/3')
    assert not public_url('https://example.org/oauth/authorize')
    assert not public_url('https://user:password@example.org/notice')
    assert not public_url('https://127.0.0.1/admin')
    assert not public_url('https://169.254.169.254/latest')
    assert not public_url('https://localhost/admin')
    assert public_url('https://example.org/notice/3')


def test_projection_of_board_response_is_bound_to_one_job():
    raw=b'{"jobs":[{"id":1,"title":"First","content":"<p>First description</p>"},{"id":2,"title":"Second","content":"<p>Second description</p>"}]}'
    _,text=project_body(raw,Path('/tmp'),'2')
    assert 'Second description' in text and 'First description' not in text


def test_workday_description_is_extracted_as_full_text():
    raw=b'{"jobPostingInfo":{"title":"Research Intern","jobDescription":"<p>Currently enrolled in a PhD.</p><p>Salary is $50 per hour.</p>"}}'
    _,text=project_body(raw,Path('/tmp'))
    assert 'Currently enrolled in a PhD.' in text and 'Salary is $50 per hour.' in text
    clauses=extract_clauses(text)
    assert clauses['eligibility'] and clauses['benefits']
    assert all('quote' in x and 'line' in x for v in clauses.values() for x in v)
    assert 'verified' not in clauses


def test_corrupted_original_cannot_be_accepted(tmp_path):
    import pytest
    path=tmp_path/'bad';path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='hash mismatch'):
        read_ref({'path':str(path),'sha256':'0'*64})


def test_offline_pass_does_not_start_a_network_request():
    from import_benefitlens_full_inventory import Resolver
    resolver=Resolver.__new__(Resolver)
    resolver.originals=lambda group:iter(())
    result=resolver.process({'members':[]},reuse_only=True)
    assert result['state']=='original_missing'
    assert result['current_open_verified'] is False
