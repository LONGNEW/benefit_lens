import json

import pytest

from data_platform.broad_collection import Store, funding_hints, make_record, normalize_url, plain, postgres_safe
from data_platform.broad_sources import as_fields, jasso, kosaf_catalog, wordpress, youth_catalog
from xml.etree import ElementTree as ET


def sample(**values):
    options=dict(url="https://new-foundation.example/scholarship?id=1",title="새 재단 생활비 장학",family="new_source",record_type="support_catalog_entry",capture_status="listing_metadata",listing_url="https://directory.example/list")
    options.update(values)
    return make_record(**options)


def test_unregistered_issuer_and_missing_eligibility_do_not_drop_a_name():
    value=sample()
    assert value and value["issuer_kind"]=="unknown"
    assert value["eligibility_status"]=="not_evaluated"
    assert value["rights_status"]=="review_required"


def test_one_catalog_url_can_have_distinct_observed_record_ids():
    a=sample(url="https://catalog.example/",identity="catalog:1",source_id="1")
    b=sample(url="https://catalog.example/",identity="catalog:2",source_id="2")
    assert a["source_url"]==b["source_url"]
    assert a["candidate_key"]!=b["candidate_key"]


def test_url_tracking_dedup_does_not_collapse_rounds():
    a=sample(url="https://new-foundation.example/scholarship?id=1&utm_source=x")
    b=sample(url="https://new-foundation.example/scholarship?id=1")
    c=sample(url="https://new-foundation.example/scholarship?id=2")
    assert a["candidate_key"]==b["candidate_key"]!=c["candidate_key"]


def test_register_in_public_article_slug_does_not_drop_free_training_name():
    url="https://catalog.example/2026/10/register-for-free-learning/"
    assert normalize_url(url)==url
    assert normalize_url("https://catalog.example/register") is None


@pytest.mark.parametrize("url",["http://public.example/a","https://u:p@public.example/a","https://public.example/login","https://public.example/a.exe","javascript:alert(1)"])
def test_unsafe_or_authenticated_targets_are_not_download_candidates(url):
    assert normalize_url(url) is None


def test_same_record_can_keep_multiple_discovery_provenances(tmp_path):
    store=Store(tmp_path)
    store.put(sample())
    store.put(sample(family="another_source",listing_url="https://another.example/list"))
    store.put(sample())
    result=store.export()
    assert result["counts"]["unique_source_records"]==1
    assert result["counts"]["observations"]==2
    assert not result["issuer_allowlist_required"]
    assert not result["personal_eligibility_applied"]


def test_longer_detail_wins_over_a_later_url_only_lead(tmp_path):
    store=Store(tmp_path)
    store.put(sample(text="지원 자격과 혜택을 확보했습니다",capture_status="publication_full_text"))
    store.put(sample(text="",capture_status="listing_metadata"))
    assert store.records()[0]["capture_status"]=="publication_full_text"


def test_html_cleanup_keeps_readable_text_without_scripts():
    assert plain('<p>Tuition &amp; living</p><script>secret()</script>')=="Tuition & living"
    assert funding_hints("생활비 등록금 장학금") == ["scholarship","tuition_learning","livelihood_housing"]


def test_repeated_official_xml_fields_are_not_overwritten():
    node=ET.fromstring('<x><EligibleApplicants>21</EligibleApplicants><EligibleApplicants>20</EligibleApplicants><OpportunityID>1</OpportunityID></x>')
    assert as_fields(node)=={"EligibleApplicants":["21","20"],"OpportunityID":"1"}


def test_postgres_nul_cleanup_retains_original_snapshot():
    original={"title":"Word\x00Document","nested":["x\x00"]}
    assert postgres_safe(original)=={"title":"Word\ufffdDocument","nested":["x\ufffd"]}
    assert original["title"]=="Word\x00Document"


class SourceFetcher:
    def __init__(self, handler):
        self.handler=handler
        self.calls=[]
    def fetch(self,url,**kwargs):
        self.calls.append((url,kwargs))
        body,final=self.handler(url,kwargs)
        if isinstance(body,str):body=body.encode()
        return body,{"final_url":final,"sha256":"fixture"}


def test_jasso_pagination_uses_real_redirected_form_action(tmp_path):
    def handler(url,options):
        if 'offset=25' in url:
            assert 'tuition-reduction_search.php' in url
            return '<a class="school-result-item" href="detail.php?mid=2"><p class="scholarship-name">Fee reduction B</p></a>',url
        return '<a class="school-result-item" href="detail.php?mid=1"><p class="scholarship-name">Scholarship A</p></a><a onclick="page(25); return false;">2</a>','https://www.studyinjapan.go.jp/en/search-for-scholarships/tuition-reduction_search.php?lang=en'
    store=Store(tmp_path)
    jasso(store,SourceFetcher(handler),max_pages=5)
    assert len(store.records())==2


def test_catalog_keeps_all_rows_even_with_one_landing_url(tmp_path):
    def handler(url,options):
        if 'fileData' in url:
            return '<script type="application/ld+json">{"distribution":[{"encodingFormat":"CSV","contentUrl":"https://www.data.go.kr/file.csv"}]}</script>',url
        return '번호,운영기관명,상품명,운영기관구분\n1,새 재단,생활비,재단\n2,새 학교,수업료 감면,대학\n',url
    store=Store(tmp_path)
    kosaf_catalog(store,SourceFetcher(handler))
    assert len(store.records())==2
    assert {r["issuer_kind"] for r in store.records()}=={"foundation","university"}
    assert len({r["source_url"] for r in store.records()})==1


def test_youth_repeated_pages_are_detected_without_inventing_total(tmp_path):
    def handler(url,options):
        if 'pubot' not in url:return '<title>Policy search</title>',url
        rows=[{"DOCID":str(i),"PLCY_NM":"정책 "+str(i)} for i in range(100)]
        return json.dumps({"totalCount":200,"searchResult":{"result":rows}}),url
    store=Store(tmp_path)
    with pytest.raises(ValueError,match="repeated policy page"):
        youth_catalog(store,SourceFetcher(handler))
    assert len(store.records())==100


def test_wordpress_keeps_non_scholarship_named_support_and_external_link(tmp_path):
    def handler(url,options):
        if 'wp-json' not in url:return '<link rel="https://api.w.org/" href="https://catalog.example/wp-json/">',url
        rows=[{"id":1,"link":"https://catalog.example/course","title":{"rendered":"Living support training"},"content":{"rendered":"<p>Benefits</p><a href=\"https://new-issuer.example/apply\">Apply</a>"}}]
        return json.dumps(rows),url
    store=Store(tmp_path)
    wordpress(store,SourceFetcher(handler),'https://catalog.example/','catalog')
    record=store.records()[0]
    assert record["capture_status"]=="publication_full_text"
    assert record["source_payload"]["external_links"]==[{"url":"https://new-issuer.example/apply","label":"Apply"}]
    assert record["issuer_kind"]=="unknown"
