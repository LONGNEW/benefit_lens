from __future__ import annotations

import hashlib
import io
import ipaddress
import re
import socket
import tempfile
import time
import zipfile
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit, urljoin

import requests


@dataclass(frozen=True)
class FetchResult:
    source_url: str
    final_url: str
    status_code: int
    content_type: str | None
    headers: dict[str, str]
    body: bytes
    redirects: tuple[tuple[str, str], ...] = ()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


def document_kind(body: bytes, content_type: str | None = None, url: str = "", headers: Mapping[str, str] | None = None) -> str:
    """Prefer actual signatures over labels on download links and headers."""
    if body.startswith(b"%PDF-"):
        return "pdf"
    if (body.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"II*\x00", b"MM\x00*", b"BM"))
            or (body.startswith(b"RIFF") and body[8:12] == b"WEBP")):
        return "image"
    if body.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "hwp"
    if body.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                if any(re.fullmatch(r"Contents/section\d+\.xml", name, re.I) for name in archive.namelist()):
                    return "hwpx"
        except zipfile.BadZipFile:
            pass
        return "unsupported"
    prefix = body.lstrip()[:100].lower()
    if any(prefix.startswith(tag) for tag in (b"<!doctype html", b"<html", b"<head", b"<body", b"<main", b"<article", b"<div")):
        return "html"
    mime = str(content_type or "").split(";", 1)[0].strip().lower()
    if mime.startswith("image/"):
        return "image"
    if mime == "application/pdf":
        return "pdf"
    if mime in {"application/hwp", "application/x-hwp", "application/vnd.hancom.hwp"}:
        return "hwp"
    if mime in {"application/hwpx", "application/vnd.hancom.hwpx", "application/vnd.hancom.hwp+zip"}:
        return "hwpx"
    if mime in {"text/html", "application/xhtml+xml"}:
        return "html"
    disposition = next((str(value) for key, value in (headers or {}).items() if key.lower() == "content-disposition"), "")
    filename = unquote(disposition) + " " + unquote(urlsplit(url).path)
    extension = re.search(r"\.(pdf|hwpx|hwp|png|jpe?g|gif|webp|tiff?)(?:[\s\"';]|$)", filename, re.I)
    if extension:
        return extension[1].lower() if extension[1].lower() in {"pdf", "hwpx", "hwp"} else "image"
    if mime and mime not in {"text/plain", "application/octet-stream"}:
        return "unsupported"
    if b"\x00" in body[:1000]:
        return "unsupported"
    return "html"


class SafeHttpClient:
    def __init__(
        self,
        *,
        allowed_hosts: set[str],
        user_agent: str,
        timeout_seconds: int,
        max_body_bytes: int | None,
        allow_public_redirects: bool = False,
        max_download_seconds: float = 180,
    ) -> None:
        self.allowed_hosts = {item.lower() for item in allowed_hosts}
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.max_body_bytes = max_body_bytes
        self.allow_public_redirects = allow_public_redirects
        self.max_download_seconds = max_download_seconds

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        secret_values: Sequence[str] = (),
        request_guard: Callable[[str], None] | None = None,
        response_observer: Callable[[str, int, str | None], None] | None = None,
    ) -> FetchResult:
        self._validate_url(url)
        current = url
        redirects = []
        started = time.monotonic()
        for hop in range(7):
            self._validate_url(current, allow_unlisted=hop > 0 and self.allow_public_redirects)
            if request_guard is not None:
                request_guard(current)
            request_headers = {
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.5",
                **dict(headers or {}),
            }
            if urlsplit(current).netloc != urlsplit(url).netloc:
                request_headers = {k:v for k,v in request_headers.items() if k.lower() not in {"authorization","proxy-authorization","cookie","x-api-key","x-auth-token","referer"}}
            response = requests.get(current, params=dict(params or {}) if hop == 0 else None, headers=request_headers, timeout=self.timeout_seconds, stream=True, allow_redirects=False)
            if response_observer is not None:
                response_observer(current, response.status_code, response.headers.get("Location"))
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            target = urljoin(current, response.headers.get("Location", ""))
            response.close()
            self._validate_url(target, allow_unlisted=self.allow_public_redirects)
            redirects.append((_redact_url(current), _redact_url(target)))
            current = target
        else:
            raise RuntimeError("too many source redirects")
        with response:
            self._validate_url(response.url, allow_unlisted=self.allow_public_redirects)
            # Large public attachments spill to a temporary file, not a list of
            # chunks. None explicitly removes the byte limit for this caller.
            with tempfile.SpooledTemporaryFile(max_size=2_000_000) as stream:
                for chunk in response.iter_content(chunk_size=65536):
                    if time.monotonic() - started > self.max_download_seconds:
                        raise TimeoutError("download total time budget exceeded")
                    if not chunk:
                        continue
                    stream.write(chunk)
                    if self.max_body_bytes is not None and stream.tell() > self.max_body_bytes:
                        raise ValueError(f"response exceeds max_body_bytes={self.max_body_bytes}")
                stream.seek(0)
                body = stream.read()
            if response.status_code >= 400:
                preview = _redact_text(
                    body.decode("utf-8", errors="replace")[:500], secret_values
                )
                raise RuntimeError(
                    f"HTTP {response.status_code} from {_redact_url(response.url)}: {preview}"
                )
            return FetchResult(
                source_url=_redact_url(url),
                final_url=_redact_url(response.url),
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                headers={str(k): str(v) for k, v in response.headers.items()},
                body=body,
                redirects=tuple(redirects),
            )

    def _validate_url(self, url: str, *, allow_unlisted: bool=False) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise ValueError("only https sources are allowed")
        if parts.username or parts.password:
            raise ValueError("credentials in source URL are not allowed")
        if parts.port not in {None,443}:
            raise ValueError("non-HTTPS service port is not allowed")
        host = (parts.hostname or "").lower()
        if host not in self.allowed_hosts and not allow_unlisted:
            raise ValueError(f"host is not allowlisted: {host}")
        for result in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
            address = ipaddress.ip_address(result[4][0])
            if (
                not address.is_global
                or address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_reserved
            ):
                raise ValueError(f"host resolves to a non-public address: {host}")


def _redact_text(value: str, secret_values: Sequence[str]) -> str:
    redacted = value
    for secret in secret_values:
        if secret:
            redacted = redacted.replace(secret, "REDACTED")
    return redacted


def _redact_url(url: str) -> str:
    parts = urlsplit(url)
    sensitive = {
        "servicekey",
        "apikey",
        "apikeynm",
        "openapivlak",
        "authentication",
    }
    query = [
        (key, "REDACTED" if key.lower() in sensitive else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
