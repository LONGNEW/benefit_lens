import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import sample_data


def test_public_sample_is_ten_distinct_opportunities_and_connected_knowledge():
    data = sample_data.read_sample()
    assert len(data['opportunities']) == 10
    assert len(data['program_knowledge']) == 3
    assert sum(len(p['experience_cases']) for p in data['program_knowledge']) == 9
    for item in data['opportunities']:
        assert item['source_refs']
        assert item['application']['url'].startswith('https://')
        assert item['extractor'] == 'public_demo_fixture_v1'
    serialized = json.dumps(data,ensure_ascii=False)
    assert '/home/' not in serialized
    assert '1997' not in serialized
    assert 'person_id' not in serialized


def test_bad_sample_hash_is_rejected(tmp_path):
    (tmp_path/'manifest.json').write_text((sample_data.SAMPLE/'manifest.json').read_text())
    (tmp_path/'programs.json').write_bytes(b'{}')
    with pytest.raises(ValueError,match='checksum'):
        sample_data.read_sample(tmp_path)


def test_sample_refuses_non_sample_data_before_any_write():
    class Connection:
        def execute(self,sql):
            assert sql.startswith('SELECT')
            return self
        def fetchall(self): return [('a-live-record','source_native_fields_v1')]
    with pytest.raises(ValueError,match='non-sample'):
        sample_data.require_sample_target(Connection(), sample_data.read_sample())
