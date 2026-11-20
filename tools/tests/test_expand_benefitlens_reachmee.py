from pathlib import Path
from datetime import date
from hashlib import sha256
import sys,pytest
from bs4 import BeautifulSoup
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from expand_benefitlens_reachmee import parse_record,doctoral_scope


def sample(tmp_path,title='Doctoral student in Natural Science',deadline='2026-11-18'):
 url='https://web103.reachmee.com/ext/I005/1035/main?site=7&rmpage=job&rmjob=41842'
 app='https://web103.reachmee.com/ext/I005/1035/apply?site=7&job_id=41842'
 raw=f'<h1>{title}</h1><p>You can apply to be admitted for education at third-cycle level.</p><p>Applications must be received by:<br>{deadline}</p><a href="{app}">Apply</a>'.encode();text=BeautifulSoup(raw,'lxml').get_text('\n',strip=True).encode()
 rp=tmp_path/'raw.html';rp.write_bytes(raw);tp=tmp_path/'text.txt';tp.write_bytes(text);lr=f'<a href="{url}">Doctoral student</a>'.encode();lp=tmp_path/'list.html';lp.write_bytes(lr)
 cap={'url':url,'status':200,'fetched_at':'2026-11-09T09:00:00+00:00','raw_path':str(rp),'raw_sha256':sha256(raw).hexdigest(),'text_path':str(tp),'text_sha256':sha256(text).hexdigest(),'raw_storage':None,'text_storage':None}
 q={'url':url,'native_id':'41842','issuer':'University of Gothenburg','listing':{'url':'https://www.gu.se/en/work-at-the-university-of-gothenburg/vacancies','raw_path':str(lp),'raw_sha256':sha256(lr).hexdigest(),'fetched_at':cap['fetched_at']}}
 return cap,q


def test_real_application_clause_can_cross_lf(tmp_path):
 cap,q=sample(tmp_path);r=parse_record(cap,q,date(2026,11,9));assert r['closes_on']=='2026-11-18'


def test_today_unknown_time_not_secured(tmp_path):
 cap,q=sample(tmp_path,deadline='2026-11-09')
 with pytest.raises(ValueError,match='same-day'):parse_record(cap,q,date(2026,11,9))


def test_source_id_crossjoin_rejected(tmp_path):
 cap,q=sample(tmp_path);q['native_id']='99999'
 with pytest.raises(ValueError,match='native'):parse_record(cap,q,date(2026,11,9))


def test_unofficial_listing_not_promoted(tmp_path):
 cap,q=sample(tmp_path);q['listing']['url']='https://example.com/list'
 with pytest.raises(ValueError,match='origin'):parse_record(cap,q,date(2026,11,9))


def test_professor_with_student_mentions_rejected():
 with pytest.raises(ValueError):doctoral_scope('Professor of doctoral student supervision','To be eligible for admission to doctoral studies')
