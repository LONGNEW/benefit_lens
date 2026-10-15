import io
import zipfile
import pytest
from data_platform.original_collection import OriginalCollector, archive_text, document_url, excluded_seed, html_document, office_text, provider_for, role_for_document
from data_platform.http import FetchResult


def test_detail_display_aliases_preserve_document_and_attachment_identifiers():
    assert document_url("https://school.test/view?no=7&mode=V&GotoPage=4") == "https://school.test/view?mode=V&no=7"
    assert document_url("https://school.test/view?mode=list&GotoPage=4") == "https://school.test/view?GotoPage=4&mode=list"
    assert "attachNo=99" in document_url("https://school.test/view?articleNo=7&attachNo=99&mode=download")
    assert document_url("https://school.test/view?no=8") != document_url("https://school.test/view?no=7")


def test_authentication_social_and_statistics_are_not_originals():
    assert excluded_seed({"source_url":"https://school.test/sso/login", "title":"장학금"}) == "authentication_not_attempted"
    assert excluded_seed({"source_url":"https://www.reddit.com/r/funding", "title":"Scholarship"}) == "experience_or_social_metadata_only"
    assert excluded_seed({"source_url":"https://school.test/table", "title":"대학별 등록금 통계"}) == "non_opportunity_statistics_or_loan"
    assert excluded_seed({"source_url":"https://ai.postech.ac.kr/careers/view/id/1", "title":"Qualcomm fellowship"}) is None


def test_document_classification_requires_body_not_just_search_title():
    assert role_for_document("https://issuer.test/fellowship", "PhD fellowship", "unrelated homepage", "html") == "reference_or_unclassified"
    assert role_for_document("https://issuer.test/fellowship", "PhD fellowship", "PhD fellowship eligibility requirements. Scholarship reward. " * 8, "html") == "notice_or_policy"
    assert role_for_document("https://issuer.test/recipients", "Fellowship recipients", "fellowship eligibility"*20, "html") == "experience_reference"


def test_original_issuer_requires_configured_domain_and_is_not_a_title_guess():
    config={"providers":[{"host":"issuer.test","name":"Issuer","kind":"company"}]}
    assert provider_for("https://issuer.test/program",config)["name"] == "Issuer"
    assert provider_for("https://relay.test/issuer-fellowship",config) is None
    assert provider_for("https://issuer.test.evil.test/program",config) is None


def test_original_links_and_attachment_are_extracted_without_navigation_noise():
    html=b"<html><title>Fellowship</title><nav><a href='/other-fellowship'>Scholarship</a></nav><main><h1>PhD Fellowship</h1><p>Eligibility: PhD students</p><p>See www.issuer.test</p><a href='/forms/application.pdf'>Application form</a></main></html>"
    title,text,links=html_document(html,"https://relay.test/view","Search hint")
    assert title=="PhD Fellowship"
    assert "Eligibility" in text
    assert any(l["url"]=="https://www.issuer.test/" for l in links)
    assert any(l["url"]=="https://relay.test/forms/application.pdf" for l in links)
    assert not any("other-fellowship" in l["url"] for l in links)


def packed(files):
    result=io.BytesIO()
    with zipfile.ZipFile(result,"w") as archive:
        for name,text in files.items(): archive.writestr(name,text)
    return result.getvalue()


def test_office_archive_identifies_doc_contents_and_orders_slides():
    word=packed({"word/document.xml":"<doc><p><t>Fellowship eligibility</t></p></doc>"})
    deck=packed({"ppt/slides/slide2.xml":"<slide><p><t>Second</t></p></slide>","ppt/slides/slide1.xml":"<slide><p><t>First</t></p></slide>"})
    text,members=archive_text(packed({"guide.DOC":word,"manual.pptx":deck}))
    assert [m["kind"] for m in members]==["docx","pptx"]
    assert "Fellowship eligibility" in text
    assert text.index("First")<text.index("Second")


def test_office_xml_entity_and_member_expansion_are_rejected():
    unsafe=packed({"word/document.xml":'<!DOCTYPE doc [<!ENTITY a "unsafe">]><doc><p><t>&a;</t></p></doc>'})
    with pytest.raises(ValueError,match="unsafe office XML"):
        office_text(unsafe)


def test_kosaf_public_download_arguments_connect_actual_attachment():
    html="<body><h1>Navigation</h1><div class='contents'><h2 class='h-tit'>박사우수장학금</h2><p>지원 대상 박사과정, 지원금액 안내</p><button onclick=\"realFileDown('HP.BRD.UPLOAD', '20613', '1'); return false;\">업무처리기준</button></div></body>".encode()
    title,text,links=html_document(html,"https://www.kosaf.go.kr/ko/scholar.do?pg=scholarship05_24_01","")
    assert title=="박사우수장학금"
    assert "Navigation" not in text
    assert "pSeq_No=20613" in links[0]["url"]
    assert links[0]["relation"]=="document_link"


@pytest.mark.parametrize("policy_state",["disallow","unavailable"])
@pytest.mark.parametrize("mode",["respect","observe"])
def test_robot_observation_is_explicit_and_keeps_detailed_errors(monkeypatch,tmp_path,policy_state,mode):
    config={"providers":[],"user_agent":"test","limits":{"timeout_seconds":1,"max_body_bytes":None,"max_requests_per_host":10,"delay_seconds":0},"access_policy":{"robots_mode":mode,"follow_public_redirects":True}}
    seen=[]
    def get(self,url,**kwargs):
        if url.endswith("robots.txt"):
            if policy_state=="unavailable": raise RuntimeError("HTTP 503 from public robots endpoint")
            return FetchResult(url,url,200,"text/plain",{},b"User-agent: *\nDisallow: /\n")
        kwargs["request_guard"](url)
        seen.append(url)
        return FetchResult(url,url,200,"text/html",{},b"<html><main><h1>PhD Fellowship</h1><p>Eligibility: PhD. Scholarship reward: USD 1000.</p></main></html>")
    monkeypatch.setattr("data_platform.original_collection.SafeHttpClient.get",get)
    collector=OriginalCollector(config,tmp_path)
    if mode=="respect":
        with pytest.raises(RuntimeError,match="robots_"): collector.fetch("https://issuer.test/fellowship","Fellowship")
        assert seen==[]
    else:
        document=collector.fetch("https://issuer.test/fellowship","Fellowship")
        assert document["status"]=="collected" and len(seen)==1
        assert document["robots_observations"][0]["enforced"] is False
        if policy_state=="unavailable": assert "HTTP 503" in collector.robots["https://issuer.test"]["error"]


def test_expanded_crawl_does_not_download_executable_links(monkeypatch,tmp_path):
    config={"providers":[],"user_agent":"test","limits":{"timeout_seconds":1,"max_body_bytes":None,"max_requests_per_host":10,"delay_seconds":0},"access_policy":{"robots_mode":"observe","follow_public_redirects":True}}
    def get(self,url,**kwargs):
        kwargs["request_guard"](url)
        pytest.fail("executable request reached transport")
    monkeypatch.setattr("data_platform.original_collection.SafeHttpClient.get",get)
    with pytest.raises(RuntimeError,match="non_document_asset"):
        OriginalCollector(config,tmp_path).fetch("https://issuer.test/tool.exe","download")
