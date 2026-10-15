import pytest
from data_platform.http import SafeHttpClient, document_kind


def test_redirect_is_validated_before_contacting_unapproved_host(monkeypatch):
    client = SafeHttpClient(allowed_hosts={"official.test"}, user_agent="test", timeout_seconds=1, max_body_bytes=1000)
    seen = []
    monkeypatch.setattr("data_platform.http.socket.getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("8.8.8.8", 443))])

    class Redirect:
        status_code = 302
        headers = {"Location": "https://private.test/secret"}
        def close(self): pass

    def get(url, **kwargs):
        seen.append(url)
        assert kwargs["allow_redirects"] is False
        return Redirect()
    monkeypatch.setattr("data_platform.http.requests.get", get)
    with pytest.raises(ValueError, match="not allowlisted"):
        client.get("https://official.test/detail")
    assert seen == ["https://official.test/detail"]


def test_private_dns_is_rejected_before_any_request(monkeypatch):
    client = SafeHttpClient(allowed_hosts={"official.test"}, user_agent="test", timeout_seconds=1, max_body_bytes=1000)
    monkeypatch.setattr("data_platform.http.socket.getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("127.0.0.1", 443))])
    monkeypatch.setattr("data_platform.http.requests.get", lambda *args, **kwargs: pytest.fail("private destination contacted"))
    with pytest.raises(ValueError, match="non-public"):
        client.get("https://official.test/detail")


def test_document_signature_overrides_html_header_and_download_url():
    assert document_kind(b"\x89PNG\r\n\x1a\nposter", "text/html", "https://official.test/download?id=1") == "image"
    assert document_kind(b"%PDF-1.7\n", "application/octet-stream", "https://official.test/file") == "pdf"
    assert document_kind("<html>접근 오류</html>".encode(), "application/pdf", "https://official.test/file.pdf") == "html"


def test_document_mime_and_attachment_filename_are_used_without_signature():
    assert document_kind(b"image bytes", "image/jpeg", "https://official.test/download") == "image"
    assert document_kind(b"pdf bytes", "application/octet-stream", headers={"Content-Disposition": "attachment; filename*=UTF-8''guide.pdf"}) == "pdf"
    assert document_kind(b"\x00binary", "application/octet-stream", "https://official.test/download") == "unsupported"


class PublicResponse:
    def __init__(self,url,body=b"ok",status=200,headers=None):
        self.url,self.body,self.status_code,self.headers=url,body,status,headers or {}
    def __enter__(self): return self
    def __exit__(self,*args): self.close()
    def close(self): pass
    def iter_content(self,chunk_size):
        for offset in range(0,len(self.body),chunk_size): yield self.body[offset:offset+chunk_size]


def test_unlimited_public_download_accepts_more_than_eight_megabytes(monkeypatch):
    monkeypatch.setattr("data_platform.http.socket.getaddrinfo", lambda *args,**kwargs:[(None,None,None,None,("8.8.8.8",443))])
    content=b"%PDF-"+b"x"*8_100_000
    monkeypatch.setattr("data_platform.http.requests.get",lambda url,**kwargs:PublicResponse(url,content))
    client=SafeHttpClient(allowed_hosts={"official.test"},user_agent="test",timeout_seconds=1,max_body_bytes=None)
    assert client.get("https://official.test/manual.pdf").body==content


def test_public_cross_domain_redirect_is_allowed_without_forwarding_credentials(monkeypatch):
    monkeypatch.setattr("data_platform.http.socket.getaddrinfo", lambda *args,**kwargs:[(None,None,None,None,("8.8.8.8",443))])
    seen=[]
    def get(url,**kwargs):
        seen.append((url,kwargs["headers"]))
        return PublicResponse(url,status=302,headers={"Location":"https://cdn.test/form.pdf"}) if len(seen)==1 else PublicResponse(url,b"%PDF-")
    monkeypatch.setattr("data_platform.http.requests.get",get)
    client=SafeHttpClient(allowed_hosts={"official.test"},user_agent="test",timeout_seconds=1,max_body_bytes=None,allow_public_redirects=True)
    result=client.get("https://official.test/download",headers={"Authorization":"private","Cookie":"private","X-Api-Key":"private"})
    assert result.final_url=="https://cdn.test/form.pdf"
    assert result.redirects==(("https://official.test/download","https://cdn.test/form.pdf"),)
    assert not {"authorization","cookie","x-api-key"}&{k.lower() for k in seen[1][1]}


@pytest.mark.parametrize("target",["https://internal.test/private","http://cdn.test/form","https://user:password@cdn.test/form"])
def test_redirect_expansion_keeps_private_tls_and_credential_guards(monkeypatch,target):
    monkeypatch.setattr("data_platform.http.socket.getaddrinfo",lambda host,*args,**kwargs:[(None,None,None,None,("127.0.0.1" if host=="internal.test" else "8.8.8.8",443))])
    seen=[]
    def get(url,**kwargs):
        seen.append(url)
        return PublicResponse(url,status=302,headers={"Location":target})
    monkeypatch.setattr("data_platform.http.requests.get",get)
    client=SafeHttpClient(allowed_hosts={"official.test"},user_agent="test",timeout_seconds=1,max_body_bytes=None,allow_public_redirects=True)
    with pytest.raises(ValueError): client.get("https://official.test/download")
    assert seen==["https://official.test/download"]
