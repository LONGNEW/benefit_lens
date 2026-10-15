"""Resolve search discoveries to public source documents, with resumable evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import shutil
import threading
import time
import io
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .discovery import clean_text, robots_allowed
from .http import SafeHttpClient, document_kind
from .keyword_search import safe_source_url, scope_hint
from .linked import _inline_notice_body, extract_document

ROOT = Path(__file__).resolve().parents[2]
TOPIC = re.compile(r"장학|장학생|연구장려|연구지원|생활장려|fellowship|scholarship|studentship|bursar|stipend|research.grant|travel.grant|doctoral.funding", re.I)
AUTH = re.compile(r"(?:^|[/\W])(?:login|logout|sign.?in|sso|mypage|password|register|account)(?:[/\W]|$)", re.I)
SOCIAL = {"reddit.com", "youtube.com", "youtu.be", "facebook.com", "instagram.com", "linkedin.com", "quora.com"}
NON_DOCUMENT_ASSET=re.compile(r"\.(?:exe|msi|dmg|apk|deb|rpm|appimage|dll|so|bat|cmd|ps1|sh)$",re.I)
ASSET = re.compile(r"\.pdf|\.hwpx?|\.zip|mode=download|/download(?:\.|/)|attach|양식|신청서|규정|공고문|서식|guideline|official.rules|application.form", re.I)
EXPERIENCE = re.compile(r"인터뷰|수기|후기|선정자|수상자|합격자|winners|recipients|alumni.stories|application.experience|scholars-aiml-2026", re.I)
ELIGIBILITY = re.compile(r"지원자\s*자격|지원\s*(자격|대상)|신청\s*(자격|대상)|선발\s*대상|eligib|applicant.qualification|who.can.apply|requirements.for.applicant", re.I)
BENEFIT = re.compile(r"시상\s*내용|지원\s*(규모|금액|내용)|장학금\s*(규모|지급)|scholarship.reward|financial.support|stipend|benefits|funding|award.amount", re.I)
PERIOD = re.compile(r"접수\s*(기간|마감)|신청\s*(기간|마감)|submission\s+(?:deadline|due|matters)|deadline|application.period|apply.by|applications?.close", re.I)
SELECTORS = (".nxb-view__contents", ".board_view", ".view_cont", ".article-body", ".article_body", "#articleBodyContents", "#VIEW_MCONTENT", "article", "main", "#content", "body")


def now():
    return datetime.now(timezone.utc).isoformat()


def document_url(value: str, parent: str | None = None) -> str | None:
    source = safe_source_url(urljoin(parent, value) if parent else value)
    if not source:
        return None
    p = urlsplit(source)
    if p.scheme != "https":
        return None
    pairs = parse_qsl(p.query, keep_blank_values=True)
    # Only remove display arguments on identifiable detail pages, never on lists.
    if any(k in {"articleNo", "seqNo", "no", "nttId", "uid"} and v for k, v in pairs) and not any(k in {"attachNo", "fileNo", "atchFileId"} for k, _ in pairs):
        pairs = [(k, v) for k, v in pairs if k not in {"GotoPage", "pageNum", "article.offset", "articleLimit", "srSearchVal", "srCategoryId", "cPage", "title"}]
    return urlunsplit(("https", p.netloc, p.path, urlencode(sorted(pairs)), ""))


def provider_for(url: str, config: dict) -> dict | None:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return next((p for p in config["providers"] if host == p["host"] or host.endswith("." + p["host"])), None)


def primary_provider(doc: dict, text: str, config: dict) -> dict | None:
    provider = provider_for(doc["final_url"], config)
    if not provider or doc["role"] == "experience_reference":
        return None
    if provider.get("require_body_pattern") and not re.search(provider["require_body_pattern"],text):
        return None
    if provider["kind"] == "university":
        title = doc["title"]
        external = any(p["name"].casefold() in title.casefold() for p in config["providers"] if p["kind"] != "university")
        if external or re.search(r"[가-힣]{2,25}재단|\[(?!POSTECH)[^\]]+\].*(?:모집|장학)", title, re.I):
            return None
    return provider


def office_text(body: bytes) -> tuple[str, str]:
    """Inspect OOXML members, including Word files mislabeled as .DOC."""
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        entries = archive.infolist()
        if len(entries)>2000 or sum(e.file_size for e in entries)>40_000_000:
            raise ValueError("office expansion limit")
        names = set(archive.namelist())
        if "word/document.xml" in names:
            kind, selected = "docx", ["word/document.xml"]
        else:
            kind = "pptx"
            selected = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml",n)),key=lambda n:int(re.search(r"slide(\d+)",n).group(1)))
        if not selected:
            return "", "zip"
        texts = []
        for name in selected:
            entry=archive.getinfo(name)
            if entry.file_size>8_000_000 or entry.file_size>max(1,entry.compress_size)*200:
                raise ValueError("office member expansion limit")
            blob=archive.read(name)
            if b"<!DOCTYPE" in blob.upper() or b"<!ENTITY" in blob.upper():
                raise ValueError("unsafe office XML")
            root=ET.fromstring(blob)
            paragraphs=[]
            for paragraph in root.iter():
                if paragraph.tag.rsplit("}",1)[-1]=="p":
                    line="".join(node.text or "" for node in paragraph.iter() if node.tag.rsplit("}",1)[-1]=="t")
                    if line.strip(): paragraphs.append(line.strip())
            texts.append("\n".join(paragraphs))
        return "\n\n".join(texts),kind


def archive_text(body: bytes) -> tuple[str, list[dict]]:
    texts, members = [], []
    direct,office_kind=office_text(body)
    if direct:
        return direct,[{"member":"OOXML document","kind":office_kind,"sha256":hashlib.sha256(body).hexdigest(),"text_length":len(direct)}]
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        entries = [e for e in archive.infolist() if not e.is_dir()]
        if len(entries) > 80 or sum(e.file_size for e in entries) > 40_000_000:
            raise ValueError("archive expansion limit")
        for entry in entries:
            if entry.file_size > 8_000_000 or entry.file_size > max(1, entry.compress_size) * 200:
                raise ValueError("archive member expansion limit")
            blob = archive.read(entry)
            kind = document_kind(blob, None, entry.filename)
            item = {"member": entry.filename, "kind": kind, "sha256": hashlib.sha256(blob).hexdigest()}
            if blob.startswith(b"PK\x03\x04") and kind != "hwpx":
                try:
                    text, item["kind"] = office_text(blob)
                    if text: texts.append("[" + entry.filename + "]\n" + text)
                    item["text_length"]=len(text)
                except Exception as error:
                    item["error"]=type(error).__name__
            elif kind in {"pdf", "hwp", "hwpx"}:
                try:
                    text = extract_document(blob, kind)
                    texts.append("[" + entry.filename + "]\n" + text)
                    item["text_length"] = len(text)
                except Exception as error:
                    item["error"] = type(error).__name__
            members.append(item)
    return "\n\n".join(texts), members


def excluded_seed(candidate: dict) -> str | None:
    p = urlsplit(candidate["source_url"])
    host = (p.hostname or "").removeprefix("www.")
    if AUTH.search(candidate["source_url"]):
        return "authentication_not_attempted"
    if any(host == h or host.endswith("." + h) for h in SOCIAL):
        return "experience_or_social_metadata_only"
    if re.search(r"대학별 등록금|학과별 등록금|등록금통계|평균등록금|학자금 대출|tuition02|tuition04_09", candidate["title"] + " " + candidate["source_url"]):
        return "non_opportunity_statistics_or_loan"
    if not document_url(candidate["source_url"]):
        return "non_https_or_unsafe_url"
    return None


def html_document(body: bytes, url: str, hint: str) -> tuple[str, str, list[dict]]:
    soup = BeautifulSoup(body, "html.parser")
    raw_title = soup.find("title")
    title = clean_text(raw_title.get_text(" ", strip=True)) if raw_title else ""
    content = next((soup.select_one(s) for s in SELECTORS if soup.select_one(s)), soup)
    if "kosaf.go.kr" in (urlsplit(url).hostname or "") and soup.select_one(".contents"):
        content=soup.select_one(".contents")
    for node in content.select("nav,header,footer,.global-nav,.global-footer,.feds-header,.feds-footer"):
        node.decompose()
    heading = content.find(["h1", "h2", "h3"])
    if heading and TOPIC.search(heading.get_text(" ", strip=True)):
        title = clean_text(heading.get_text(" ", strip=True))
    if "kosaf.go.kr" in (urlsplit(url).hostname or ""):
        specific = soup.select_one("h2.h-tit")
        if specific and TOPIC.search(specific.get_text(" ", strip=True)):
            title = clean_text(specific.get_text(" ", strip=True))
    embedded = _inline_notice_body(soup, title or hint)
    links = []
    for a in content.select("a[href]"):
        if a.find_parent(["nav", "header", "footer"]):
            continue
        target = document_url(str(a.get("href")), url)
        label = clean_text(a.get_text(" ", strip=True))
        if target and target != url and (ASSET.search(target + " " + label) or TOPIC.search(label)):
            links.append({"url": target, "label": label or target, "relation": "document_link" if ASSET.search(target + " " + label) else "related_notice"})
    # Public ko.board.js realFileDown builds this endpoint from visible arguments.
    # Basis: https://static.kosaf.go.kr/www/js/n_skin/common/ko.board.js
    if "kosaf.go.kr" in (urlsplit(url).hostname or ""):
        for node in soup.select("[onclick]"):
            match=re.search(r"realFileDown\('([A-Z.]+)',\s*'(\d+)',\s*'(\d+)'\)",str(node.get("onclick")),re.I)
            if match:
                target=document_url("https://www.kosaf.go.kr/ko/download.do?"+urlencode(dict(zip(("pPath","pSeq_No","pFile_No"),match.groups()))))
                links.append({"url":target,"label":clean_text(node.get_text(" ",strip=True)) or str(node.get("title")) or "공식 첨부","relation":"document_link"})
    # Plain official web addresses in a notice can identify the issuer's source.
    for value in re.findall(r"https?://[^\s<>\"')]+|www\.[A-Za-z0-9.-]+(?:/[^\s<>\"')]+)?", content.get_text(" ", strip=True)):
        target = document_url("https://" + value if value.startswith("www.") else value)
        if target and target != url:
            links.append({"url": target, "label": value, "relation": "body_reference"})
    for img in content.select("img[src]"):
        label = clean_text(str(img.get("alt") or ""))
        target = document_url(str(img.get("src")), url)
        if target and (re.search(r"포스터|공고|장학생|안내문|poster|notice", label, re.I) or re.search(r"attach|upload|editor|board", target, re.I)):
            links.append({"url": target, "label": label or "본문 이미지", "relation": "poster_image"})
    for node in content.select("script,style,nav,header,footer,svg,noscript"):
        node.decompose()
    text = "\n".join(clean_text(line) for line in content.get_text("\n", strip=True).splitlines() if clean_text(line))
    if embedded:
        text += "\n" + "\n".join(embedded)
    if "daad.de" in (urlsplit(url).hostname or ""):
        match=re.search(r"« Search results\s*\n([^\n]+)\s*\n•\s*DAAD",text)
        if match: title=match.group(1)+" | DAAD"
    if not TOPIC.search(title):
        programme=next((line for line in text.splitlines() if TOPIC.search(line) and re.search(r"call for applications|모집 공고|장학생 모집",line,re.I)),None)
        if programme: title=programme
    links=list({item["url"]:item for item in links if not NON_DOCUMENT_ASSET.search(urlsplit(item["url"]).path)}.values())
    links.sort(key=lambda item:item["relation"]!="document_link")
    return title[:300] or hint[:300], text, links


def role_for_document(url: str, title: str, text: str, kind: str) -> str:
    heading = title + " " + url
    if EXPERIENCE.search(heading):
        return "experience_reference"
    if not TOPIC.search(heading) or not TOPIC.search(text):
        return "reference_or_unclassified"
    if kind in {"pdf", "hwp", "hwpx", "image", "zip"}:
        return "guideline_or_form"
    signals = sum(bool(pattern.search(text)) for pattern in (ELIGIBILITY, BENEFIT, PERIOD))
    if signals >= 2 and len(text) >= 150:
        return "notice_or_policy"
    return "listing_or_reference"


class OriginalCollector:
    def __init__(self, config: dict, output: Path, cached_only: bool=False, reextract_cache: bool=True):
        self.config, self.output = config, output
        self.lock = threading.RLock()
        self.host_locks, self.last, self.host_counts, self.robots, self.documents = {}, {}, Counter(), {}, {}
        self.pending = {}
        self.requests = []
        output.mkdir(parents=True, exist_ok=True)
        (output / "raw").mkdir(exist_ok=True)
        (output / "text").mkdir(exist_ok=True)
        self.database = output / "originals.sqlite"
        previous={}
        if cached_only and (output/"collection.json").exists():
            previous={d["url"]:d for d in json.loads((output/"collection.json").read_text(encoding="utf-8"))["documents"]}
        with sqlite3.connect(self.database) as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS source_document(url TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS candidate_resolution(candidate_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS origin_edge(candidate_key TEXT, url TEXT, parent_url TEXT, relation TEXT, status TEXT, PRIMARY KEY(candidate_key,url,parent_url));
            """)
            for url, payload in c.execute("SELECT url,payload_json FROM source_document"):
                doc = json.loads(payload)
                cached=previous.get(url)
                if cached and cached.get("sha256")==doc.get("sha256"):
                    doc=cached
                text = (output / doc["text_path"]).read_text(encoding="utf-8") if doc.get("text_path") else ""
                if reextract_cache and doc["kind"] == "html" and doc.get("raw_path") and (not cached_only or provider_for(doc["final_url"],config)):
                    title,text,links = html_document((output/doc["raw_path"]).read_bytes(),doc["final_url"],doc["title"])
                    doc.update(title=title,links=links,role=role_for_document(doc["final_url"],title,text,"html"),text_length=len(text))
                    if doc.get("text_path"):
                        (output/doc["text_path"]).write_text(text,encoding="utf-8")
                elif reextract_cache and doc["kind"]=="zip" and doc.get("raw_path"):
                    try:
                        text,members=archive_text((output/doc["raw_path"]).read_bytes())
                        if text:
                            path=output/"text"/(doc["sha256"]+".txt")
                            path.write_text(text,encoding="utf-8")
                            doc.update(text_path=str(path.relative_to(output)),text_length=len(text),archive_members=members,extraction_error=None)
                            doc["role"]=role_for_document(doc["final_url"],doc["title"],text,"zip")
                    except Exception as error:
                        doc["extraction_error"]=type(error).__name__
                doc["primary_provider"] = primary_provider(doc, text, config)
                self.documents[url] = doc

    def persist_document(self, doc):
        with self.lock, sqlite3.connect(self.database) as c:
            self.documents[doc["url"]] = doc
            c.execute("INSERT OR REPLACE INTO source_document VALUES (?,?)", (doc["url"], json.dumps(doc, ensure_ascii=False)))

    def fetch(self, url: str, hint: str) -> dict:
        url = document_url(url)
        if not url:
            raise ValueError("unsafe_or_non_https")
        with self.lock:
            if url in self.documents:
                return self.documents[url]
            future = self.pending.get(url)
            if future is None:
                future = self.pending[url] = threading.Event()
                owner = True
            else:
                owner = False
        if not owner:
            future.wait(120)
            with self.lock:
                if url in self.documents:
                    return self.documents[url]
            raise RuntimeError("shared_request_failed")
        try:
            return self._fetch(url, hint)
        finally:
            future.set()

    def _fetch(self, url, hint):
        limits = self.config["limits"]
        access = self.config.get("access_policy", {})
        robots_mode = access.get("robots_mode", "respect")
        if robots_mode not in {"respect","observe"}:
            raise ValueError("invalid robots mode")
        host = urlsplit(url).hostname
        client = SafeHttpClient(allowed_hosts={host, "www." + host.removeprefix("www."), host.removeprefix("www.")}, user_agent=self.config["user_agent"], timeout_seconds=limits["timeout_seconds"], max_body_bytes=limits["max_body_bytes"], allow_public_redirects=access.get("follow_public_redirects",False))
        observations = []
        def guard(target):
            if NON_DOCUMENT_ASSET.search(urlsplit(target).path):
                raise RuntimeError("non_document_asset")
            if AUTH.search(target):
                raise RuntimeError("authentication_not_attempted")
            target_host = urlsplit(target).hostname
            origin = "https://" + urlsplit(target).netloc
            with self.lock:
                target_lock=self.host_locks.setdefault(target_host,threading.Lock())
            with target_lock:
                if origin not in self.robots:
                    robots_client=SafeHttpClient(allowed_hosts={target_host,"www."+target_host.removeprefix("www."),target_host.removeprefix("www.")},user_agent=self.config["user_agent"],timeout_seconds=access.get("robots_timeout_seconds",10),max_body_bytes=1_000_000,allow_public_redirects=access.get("follow_public_redirects",False))
                    policy={"url":origin+"/robots.txt","status":"unavailable","text":"","checked_at":now(),"mode":robots_mode,"attempts":[]}
                    for attempt in range(access.get("robots_attempts",1)):
                        try:
                            response=robots_client.get(origin+"/robots.txt")
                            policy.update(status="checked",text=response.body.decode("utf-8","replace"),final_url=response.final_url)
                            policy["attempts"].append({"status":response.status_code,"at":now()})
                            break
                        except Exception as error:
                            detail=type(error).__name__+": "+str(error)[:500]
                            policy["attempts"].append({"error":detail,"at":now()})
                            policy["error"]=detail
                            if "HTTP 404 " in str(error):
                                policy["status"]="absent_404"
                                break
                            if "HTTP 403 " in str(error) or "HTTP 401 " in str(error): break
                    with self.lock:
                        self.robots[origin]=policy
                policy=self.robots[origin]
            if policy["status"] == "unavailable" and robots_mode=="respect":
                raise RuntimeError("robots_unavailable: "+policy.get("error","unknown"))
            allowed, delay = robots_allowed(policy["text"], target, self.config["user_agent"])
            observations.append({"url":target,"robots_url":policy["url"],"lookup_status":policy["status"],"rule_allowed":allowed if policy["status"]!="unavailable" else None,"mode":robots_mode,"enforced":robots_mode=="respect"})
            if not allowed and robots_mode=="respect":
                raise RuntimeError("robots_disallowed")
            with target_lock:
                if self.host_counts[target_host] >= limits["max_requests_per_host"]:
                    raise RuntimeError("host_request_limit")
                reserved=max(time.monotonic(), self.last.get(target_host,0)+max(delay,limits["delay_seconds"]))
                wait=reserved-time.monotonic()
                self.last[target_host]=reserved
                self.host_counts[target_host]+=1
            if wait > 0:
                time.sleep(wait)
        fetched = client.get(url, request_guard=guard, response_observer=lambda target,status,location: self.requests.append({"url":target,"status":status,"redirect_to":location,"at":now()}))
        final = document_url(fetched.final_url) or url
        if fetched.body.startswith((b"MZ",b"\x7fELF")):
            raise RuntimeError("non_document_asset")
        kind = document_kind(fetched.body, fetched.content_type, final, fetched.headers)
        title, text, links = hint, "", []
        error = None
        members = []
        if kind == "html":
            title, text, links = html_document(fetched.body, final, hint)
        elif kind in {"pdf", "hwp", "hwpx", "image"}:
            try:
                text = extract_document(fetched.body, kind)
                title = next((clean_text(line) for line in text.splitlines() if TOPIC.search(line)), hint)[:300]
            except Exception as exc:
                error = type(exc).__name__ + ": " + str(exc)[:140]
        elif fetched.body.startswith(b"PK\x03\x04"):
            kind = "zip"
            try:
                text, members = archive_text(fetched.body)
            except Exception as exc:
                error = type(exc).__name__ + ": " + str(exc)[:140]
        role = role_for_document(final, title, text, kind)
        provider = provider_for(final, self.config)
        # Experiences retain only references until reuse scope is reviewed.
        metadata_only = role == "experience_reference"
        raw_path = None
        text_path = None
        if not metadata_only:
            raw = self.output / "raw" / (fetched.sha256 + "." + (kind if kind in {"html", "pdf", "hwp", "hwpx", "image"} else "bin"))
            if not raw.exists():
                raw.write_bytes(fetched.body)
            raw_path = str(raw.relative_to(self.output))
            if text:
                path = self.output / "text" / (fetched.sha256 + ".txt")
                path.write_text(text, encoding="utf-8")
                text_path = str(path.relative_to(self.output))
        doc = {"url": url, "final_url": final, "title": clean_text(title), "kind": kind, "role": role, "provider": provider,
            "capture_mode": "metadata_only" if metadata_only else "internal_research", "sha256": None if metadata_only else fetched.sha256,
            "raw_path": raw_path, "text_path": text_path, "text_length": len(text) if not metadata_only else 0,
            "bytes": len(fetched.body), "content_type": fetched.content_type, "collected_at": now(), "extraction_error": error,
            "links": links, "archive_members": members, "status": "collected", "rights_status": "review_required", "redirect_chain":[{"from":a,"to":b} for a,b in fetched.redirects],"robots_observations":observations}
        if re.search(r"just a moment|verify you are human|access denied|captcha required", title + " " + text[:500], re.I):
            doc["status"], doc["role"] = "access_challenge", "blocked_placeholder"
        doc["primary_provider"] = primary_provider(doc, text, self.config)
        self.persist_document(doc)
        return doc

    def resolve(self, candidate: dict) -> dict:
        key = candidate["url_key"]
        excluded = excluded_seed(candidate)
        result = {"candidate_key": key, "search_url": candidate["source_url"], "title": candidate["title"], "query_ids": candidate["query_ids"], "status": excluded or "pending", "documents": [], "origins": [], "edges": []}
        if not excluded:
            queue = [(document_url(candidate["source_url"]), None, candidate["title"], 0, "search_result")]
            for hint in self.config.get("resolution_hints", []):
                if re.search(hint["pattern"], candidate["title"], re.I):
                    queue.append((hint["url"], hint["basis_url"], hint["label"], 0, hint["method"]))
            visited = set()
            while queue and len(visited) < self.config["limits"]["max_nodes_per_candidate"]:
                url, parent, label, depth, relation = queue.pop(0)
                if not url or url in visited:
                    continue
                visited.add(url)
                edge = {"url": url, "parent_url": parent, "relation": relation, "depth": depth}
                result["edges"].append(edge)
                try:
                    doc = self.fetch(url, label)
                    result["documents"].append(doc["url"])
                    edge["status"] = "collected"
                    if doc.get("primary_provider") and doc["role"] in {"notice_or_policy", "guideline_or_form"} and doc["text_length"] >= 100 and doc["status"] == "collected":
                        result["origins"].append(doc["url"])
                    if depth < self.config["limits"]["max_depth"]:
                        links = doc["links"]
                        # An external official issuer is preferred over more relay boards.
                        links = sorted(links, key=lambda link: (not bool(provider_for(link["url"], self.config)), link["relation"] != "document_link", link["url"]))
                        for link in links[:self.config["limits"]["max_links_per_page"]]:
                            if not AUTH.search(link["url"]) and not any((urlsplit(link["url"]).hostname or "").removeprefix("www.") == h for h in SOCIAL):
                                queue.append((link["url"], doc["final_url"], link["label"], depth + 1, link["relation"]))
                        if len(links) > self.config["limits"]["max_links_per_page"]:
                            edge["unvisited_link_count"] = len(links) - self.config["limits"]["max_links_per_page"]
                except Exception as exc:
                    detail = str(exc)
                    edge["status"] = next((state for state in ("robots_disallowed", "robots_unavailable", "authentication_not_attempted", "host_request_limit", "non_document_asset") if state in detail), "fetch_failed")
                    edge["error"] = type(exc).__name__ + ": " + detail[:160]
            result["unvisited_queue_count"] = len(queue)
            result["documents"] = sorted(set(result["documents"]))
            result["origins"] = sorted(set(result["origins"]))
            result["status"] = "official_origin_collected" if result["origins"] else "collected_without_resolved_official_origin" if result["documents"] else "fetch_or_access_failed"
        result["finished_at"] = now()
        with self.lock, sqlite3.connect(self.database) as c:
            c.execute("INSERT OR REPLACE INTO candidate_resolution VALUES (?,?)", (key,json.dumps(result,ensure_ascii=False)))
            for edge in result["edges"]:
                c.execute("INSERT OR REPLACE INTO origin_edge VALUES (?,?,?,?,?)", (key,edge["url"],edge["parent_url"] or "",edge["relation"],edge["status"]))
        return result

    def run(self, candidates: list[dict], existing_results: list[dict] | None=None) -> dict:
        started = now()
        results = list(existing_results or [])
        initial_count=len(results)
        with ThreadPoolExecutor(max_workers=self.config["limits"]["workers"]) as executor:
            pending = {executor.submit(self.resolve,c): c for c in candidates}
            for future in as_completed(pending):
                results.append(future.result())
                done=len(results)-initial_count
                if done % 25 == 0 or done == len(candidates):
                    print(json.dumps({"processed":done,"total":len(candidates),"ledger_total":len(results),"documents":len(self.documents),"official_origin_candidates":sum(bool(r["origins"]) for r in results)},ensure_ascii=False),flush=True)
                    self.export(results,started,complete=False)
        return self.export(results,started,complete=True)

    def refresh_snapshot(self, expected_candidates: int, fetch_assets: bool=True):
        """Recover completed ledger work and fetch newly understood issuer assets."""
        with sqlite3.connect(self.database) as c:
            results=[json.loads(row[0]) for row in c.execute("SELECT payload_json FROM candidate_resolution")]
        if len(results)!=expected_candidates:
            raise ValueError("candidate ledger incomplete; run normal collection first")
        fetched={} if fetch_assets else dict(self.documents)
        for doc in list(self.documents.values()):
            if not provider_for(doc["final_url"],self.config) or doc["role"] not in {"notice_or_policy","guideline_or_form","listing_or_reference"}:
                continue
            self.persist_document(doc)
            for link in (doc["links"][:self.config["limits"]["max_links_per_page"]] if fetch_assets else []):
                if link["relation"]!="document_link" or not provider_for(link["url"],self.config): continue
                if link["url"] not in fetched:
                    try: fetched[link["url"]]=self.fetch(link["url"],doc["title"]+" / "+link["label"])
                    except Exception as error: fetched[link["url"]]={"error":type(error).__name__+": "+str(error)[:160]}
        for result in results:
            for url in list(result["documents"]):
                doc=self.documents.get(url)
                if not doc: continue
                for link in doc["links"]:
                    asset=fetched.get(link["url"])
                    if not asset: continue
                    if asset.get("error"):
                        result["edges"].append({"url":link["url"],"parent_url":url,"relation":"document_link","depth":1,"status":"fetch_failed","error":asset["error"]})
                    elif link["url"] not in result["documents"]:
                        result["documents"].append(link["url"])
                        result["edges"].append({"url":link["url"],"parent_url":url,"relation":"document_link","depth":1,"status":"collected"})
            result["origins"]=sorted({u for u in result["documents"] if u in self.documents and self.documents[u].get("primary_provider") and self.documents[u]["role"] in {"notice_or_policy","guideline_or_form"} and self.documents[u]["text_length"]>=100 and self.documents[u]["status"]=="collected"})
            if result["origins"]: result["status"]="official_origin_collected"
            result["finished_at"]=now()
            with sqlite3.connect(self.database) as c:
                c.execute("INSERT OR REPLACE INTO candidate_resolution VALUES (?,?)",(result["candidate_key"],json.dumps(result,ensure_ascii=False)))
                for edge in result["edges"]:
                    c.execute("INSERT OR REPLACE INTO origin_edge VALUES (?,?,?,?,?)",(result["candidate_key"],edge["url"],edge["parent_url"] or "",edge["relation"],edge["status"]))
        return self.export(results,now(),complete=True)

    def export(self, results, started, complete):
        with self.lock:
            documents = list(self.documents.values())
            policies = list(self.robots.copy().values())
            requests = list(self.requests)
        summary = {"schema_version":"original-collection-1","started_at":started,"finished_at":now(),"complete":complete,
            "counts":{"processed_candidates":len(results),"statuses":dict(Counter(r["status"] for r in results)),"documents":len(documents),
                "stored_raw_documents":sum(bool(d["raw_path"]) for d in documents),"metadata_only_documents":sum(d["capture_mode"]=="metadata_only" for d in documents),
                "official_documents":sum(bool(d.get("primary_provider")) and d["status"] == "collected" for d in documents),"text_extracted":sum(bool(d["text_path"]) for d in documents),"page_requests":len(self.requests)},
            "results":sorted(results,key=lambda r:r["candidate_key"]),"documents":documents,"requests":requests,
            "policies":[{k:v for k,v in p.items() if k!="text"} for p in policies],"limits":self.config["limits"],"access_policy":self.config.get("access_policy",{})}
        temporary=self.output/"collection.next.json"
        temporary.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
        temporary.replace(self.output/"collection.json")
        return summary


def main():
    parser=argparse.ArgumentParser(description="검색 URL을 공식 원문과 첨부로 연결해 수집")
    parser.add_argument("--input",type=Path,required=True)
    parser.add_argument("--config",type=Path,default=ROOT/"config/original_sources.json")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--limit",type=int)
    parser.add_argument("--refresh-snapshot",action="store_true")
    parser.add_argument("--offline-refresh",action="store_true")
    parser.add_argument("--retry-status",action="append",default=[])
    parser.add_argument("--checkpoint-name")
    args=parser.parse_args()
    summary=json.loads(args.input.read_text(encoding="utf-8")); config=json.loads(args.config.read_text(encoding="utf-8"))
    candidates=summary["candidates"]
    if args.limit:
        candidates=candidates[:args.limit]
    existing=[]
    previous=None
    if args.retry_status:
        previous=json.loads((args.output/"collection.json").read_text(encoding="utf-8"))
        retry_keys={r["candidate_key"] for r in previous["results"] if r["status"] in args.retry_status}&{c["url_key"] for c in candidates}
        existing=[r for r in previous["results"] if r["candidate_key"] not in retry_keys]
        candidates=[c for c in candidates if c["url_key"] in retry_keys]
    if args.checkpoint_name:
        if not re.fullmatch(r"[A-Za-z0-9_-]+",args.checkpoint_name): raise ValueError("invalid checkpoint name")
        checkpoint=args.output/(args.checkpoint_name+".json")
        if checkpoint.exists() or (args.output/(args.checkpoint_name+".sqlite")).exists(): raise ValueError("checkpoint already exists")
        shutil.copyfile(args.output/"collection.json",checkpoint)
        with sqlite3.connect(args.output/"originals.sqlite") as source, sqlite3.connect(args.output/(args.checkpoint_name+".sqlite")) as target:
            source.backup(target)
    collector=OriginalCollector(config,args.output,cached_only=bool(args.retry_status) or args.refresh_snapshot or args.offline_refresh,reextract_cache=not args.retry_status)
    result=collector.refresh_snapshot(len(candidates),fetch_assets=not args.offline_refresh) if args.refresh_snapshot or args.offline_refresh else collector.run(candidates,existing_results=existing)
    if previous:
        current={r["candidate_key"]:r for r in result["results"]}
        changes=[{"candidate_key":r["candidate_key"],"url":r["search_url"],"before":r["status"],"after":current[r["candidate_key"]]["status"]} for r in previous["results"] if r["candidate_key"] in retry_keys]
        audit={"started_at":result["started_at"],"finished_at":result["finished_at"],"access_policy":result["access_policy"],"retried_candidates":len(changes),"transitions":dict(Counter(c["before"]+" -> "+c["after"] for c in changes)),"changes":changes,"new_document_urls":sorted(set(collector.documents)-{d["url"] for d in previous["documents"]})}
        (args.output/"access-retry.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result["counts"],ensure_ascii=False),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
