"""Extract declarations from a public client-rendered notice, without executing it."""
from dataclasses import replace
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup


def codetree_declarations(script: str) -> dict[str, str]:
    declarations = {}
    for key in ("joined", "paid", "bc3", "closed"):
        match = re.search(r'\b' + key + r':\{title:`([^`]+)`,body:`([^`]+)`\}', script)
        if not match or "${" in match[2]:
            raise ValueError("CodeTree public participation declarations changed: " + key)
        declarations[key] = match[2].replace(r"\n", " ")
    return declarations


def codetree_notice(record, body, url, client, store_raw):
    # General text parsing removes scripts; keep the module declaration here.
    soup = BeautifulSoup(body, "html.parser")
    entry = soup.select_one('script[type="module"][src]')
    if entry is None:
        raise ValueError("CodeTree public module not found")
    # Follow only the route bundle named by the page's own public entry module.
    entry_url = urljoin(url, str(entry["src"]))
    fetched = client.get(entry_url)
    store_raw(fetched)
    route = re.search(r'assets/sheep-sheep-sheep-[A-Za-z0-9_-]+\.js', fetched.body.decode("utf-8"))
    if route is None:
        raise ValueError("CodeTree public route bundle not found")
    fetched = client.get(urljoin(url, "/" + route[0]))
    raw_id = store_raw(fetched)
    declarations = codetree_declarations(fetched.body.decode("utf-8"))
    presentation = {key: list(value) for key, value in record.presentation.items()}
    terms = [declarations[key] for key in ("joined", "paid", "bc3")]
    presentation.setdefault("specialTerms", []).extend(terms)
    presentation.setdefault("eligibility", []).extend(terms)
    sources = [{"url": url, "kind": "public_client_text", "status": "collected", "label": "코드트리 공식 안내 화면의 공개 선언", "text": " ".join(declarations.values()), "bundleUrl": fetched.final_url, "sha256": fetched.sha256, "rawDocumentId": raw_id}]
    return replace(record, eligibility_rules=list(dict.fromkeys(record.eligibility_rules + terms)), presentation=presentation), " ".join(declarations.values()), sources
