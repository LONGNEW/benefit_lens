"""Read-only inventory of public text, assets and outbound source links."""
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin
from urllib.request import urlopen

import requests
from bs4 import BeautifulSoup


def inspect(row):
    try:
        response = requests.get(row["sourceUrl"], timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        content = next((soup.select_one(s) for s in (".board_view", "#bo_v_con", ".board-view-content", "#VIEW_MCONTENT", "#jwxe_main_content", "main") if soup.select_one(s)), soup)
        links = [{"text": a.get_text(" ", strip=True)[:90], "url": urljoin(response.url, a["href"])} for a in content.select("a[href]")]
        assets = [{"alt": a.get("alt", ""), "url": urljoin(response.url, a.get("src", ""))} for a in content.select("img[src]")]
        text = content.get_text("\n", strip=True)
        return {"id": row["id"], "url": response.url, "text": text[:5000], "links": links[:25], "assets": assets[:10]}
    except Exception as error:
        return {"id": row["id"], "error": str(error)}


if __name__ == "__main__":
    rows = json.load(urlopen("http://127.0.0.1:5174/api/opportunities?pageSize=100"))["data"]
    rows = [r for r in rows if r.get("operatorGroup") != "school"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for finding in pool.map(inspect, rows):
            print(json.dumps(finding, ensure_ascii=False))
