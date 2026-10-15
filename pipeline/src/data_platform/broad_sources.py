"""Public source adapters. Each keeps every returned record, including old rounds."""
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import math
import re
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from .broad_collection import Store, PublicFetcher, digest, funding_hints, make_record, normalize_url, plain


def as_fields(element):
    fields = {}
    for node in element:
        key = node.tag.rsplit("}", 1)[-1]
        value = node.text or ""
        if key in fields:
            fields[key] = [*fields[key], value] if isinstance(fields[key], list) else [fields[key], value]
        else:
            fields[key] = value
    return fields


def grants_xml(store, fetcher):
    family = "grants_gov_xml"
    landing = "https://www.grants.gov/xml-extract"
    body, _ = fetcher.fetch(landing)
    links = [urljoin(landing, a["href"]) for a in BeautifulSoup(body, "html.parser").select("a[href]") if re.search(r"GrantsDBExtract\d{8}v2\.zip$", a["href"])]
    if not links:
        raise ValueError("official XML extract link missing")
    url = sorted(links)[-1]
    body, meta = fetcher.fetch(url)
    ids, tags = set(), Counter()
    buffer = []
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        xmls = [i for i in archive.infolist() if i.filename.endswith(".xml")]
        if len(xmls) != 1 or xmls[0].file_size > 1_500_000_000:
            raise ValueError("unexpected official extract layout")
        with archive.open(xmls[0]) as handle:
            for _, element in ET.iterparse(handle, events=("end",)):
                tag = element.tag.rsplit("}", 1)[-1]
                if tag not in {"OpportunitySynopsisDetail_1_0", "OpportunityForecastDetail_1_0"}:
                    continue
                fields = as_fields(element)
                source_id = fields.get("OpportunityID")
                title = fields.get("OpportunityTitle")
                if source_id and title:
                    text = "\n".join(f"{key}: {plain(value) if isinstance(value,str) else ', '.join(value)}" for key,value in fields.items())
                    buffer.append(make_record(url=url, title=title, family=family, record_type="general_funding_notice", capture_status="structured_full_record", listing_url=landing, source_id=source_id, identity="grants.gov:"+source_id, issuer_name=fields.get("AgencyName"), issuer_kind="government", country="US", text=text, payload={"record_type":tag,"fields":fields,"title_funding_hints":funding_hints(title),"not_assumed_individual_scholarship":True}, evidence_sha256=meta["sha256"]))
                    ids.add(source_id)
                    tags[tag] += 1
                element.clear()
                if len(buffer) >= 500:
                    store.put_many(buffer)
                    buffer=[]
                    if len(ids) % 5000 == 0:
                        store.status(family,status="running",records=len(ids),url=url)
    store.put_many(buffer)
    store.status(family,status="complete",records=len(ids),source_elements=dict(tags),url=url,bytes=len(body),note="Historical and institutional grants included; not a count of individual scholarships")


def kosaf_catalog(store, fetcher):
    family="kosaf_catalog"
    landing="https://www.data.go.kr/data/15028252/fileData.do"
    body,_=fetcher.fetch(landing)
    soup=BeautifulSoup(body,"html.parser")
    distributions=[]
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            obj=json.loads(script.string or script.get_text())
            distributions.extend(obj.get("distribution",[]))
        except (ValueError,AttributeError):
            continue
    urls=[d["contentUrl"] for d in distributions if str(d.get("encodingFormat","")).upper()=="CSV"]
    if not urls:
        raise ValueError("CSV distribution not declared")
    body,meta=fetcher.fetch(urls[0])
    try:
        text=body.decode("utf-8-sig")
    except UnicodeDecodeError:
        text=body.decode("cp949")
    rows=list(csv.DictReader(io.StringIO(text)))
    records=[]
    kind_map={"기업":"company","재단":"foundation","대학":"university","지자체":"government","공공":"government","정부":"government"}
    for row in rows:
        source_id=row.get("번호","").strip()
        kind=next((v for k,v in kind_map.items() if k in row.get("운영기관구분","")),"unknown")
        detail="\n".join(f"{k}: {v}" for k,v in row.items() if str(v or "").strip())
        records.append(make_record(url=landing,title=row.get("상품명"),family=family,record_type="support_catalog_entry",capture_status="structured_full_record",listing_url=urls[0],source_id=source_id,identity="kosaf-catalog:"+source_id,issuer_name=row.get("운영기관명"),issuer_kind=kind,country="KR",text=detail,payload=row,evidence_sha256=meta["sha256"]))
    store.put_many(records)
    pairs={(r.get("운영기관명","").strip(),r.get("상품명","").strip()) for r in rows}
    store.status(family,status="complete",source_rows=len(rows),issuer_product_pairs=len(pairs),url=urls[0],note="Rows may repeat one program across degree categories; latest catalog is not current application confirmation")


def youth_catalog(store, fetcher, max_pages=100):
    family="youthcenter_policies"
    landing="https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch"
    endpoint="https://www.youthcenter.go.kr/pubot/search/portalPolicySearch"
    fetcher.fetch(landing)
    ids=set()
    total=None
    for page in range(1,max_pages+1):
        body,meta=fetcher.fetch(endpoint,payload={"query":"","pageNum":page,"listCount":100,"searchFields":"all","sortFields":""})
        obj=json.loads(body)
        total=obj["totalCount"]
        result=obj["searchResult"]
        # Public search response holds a named collection, discovered from the page.
        rows=[]
        def walk(value):
            if isinstance(value,dict):
                if "DOCID" in value and "PLCY_NM" in value:
                    rows.append(value)
                else:
                    for item in value.values():walk(item)
            elif isinstance(value,list):
                for item in value:walk(item)
        walk(result)
        if not rows:
            break
        page_ids={str(r["DOCID"]) for r in rows}
        if page_ids <= ids:
            raise ValueError("repeated policy page")
        records=[]
        for row in rows:
            source_id=str(row["DOCID"])
            url=landing+"/ythPlcyDetail/"+source_id
            detail="\n".join(f"{k}: {plain(v)}" for k,v in row.items() if isinstance(v,(str,int,float)) and str(v))
            records.append(make_record(url=url,title=plain(row["PLCY_NM"]),family=family,record_type="youth_support_policy",capture_status="structured_catalog_record",listing_url=endpoint,source_id=source_id,issuer_name=row.get("SPRVSN_INST_CD_NM") or row.get("OPER_INST_CD_NM"),issuer_kind="government",country="KR",text=detail,payload=row,evidence_sha256=meta["sha256"]))
        store.put_many(records)
        ids.update(page_ids)
        store.status(family,status="running",page=page,records=len(ids),reported_total=total)
        if len(ids)>=total or len(rows)<100:
            break
    store.status(family,status="complete" if len(ids)==total else "partial",records=len(ids),reported_total=total,note="Public frontend search; no personal filters, login or API credential used")


def wordpress_record(row,base,family,url,meta):
    content=row.get("content",{}).get("rendered","")
    title=plain(row.get("title",{}).get("rendered",""))
    links=[]
    for a in BeautifulSoup(content,"html.parser").select('a[href]'):
        target=normalize_url(a["href"],row["link"])
        if target and urlsplit(target).hostname!=urlsplit(base).hostname:
            links.append({"url":target,"label":plain(a.get_text(" ",strip=True))})
    source_payload={k:v for k,v in row.items() if k!="content"}
    source_payload.update(external_links=links,reference_publisher=base,issuer_verification="not_performed")
    target=normalize_url(row["link"])
    fallback=target is None
    if fallback:source_payload["original_link_unavailable_or_restricted"]=row["link"]
    return make_record(url=target or url,title=title,family=family,record_type="support_catalog_publication",capture_status="publication_full_text",listing_url=url,source_id=row["id"],identity=family+":post:"+str(row["id"]) if fallback else None,text=plain(content),payload=source_payload,evidence_sha256=meta["sha256"])


def wordpress(store,fetcher,base,family,max_pages=250,start_page=1):
    body,_=fetcher.fetch(base)
    soup=BeautifulSoup(body,"html.parser")
    tag=soup.select_one('link[rel="https://api.w.org/"]')
    if tag is None:
        raise ValueError("WordPress public API not advertised")
    api=urljoin(base,tag["href"])
    endpoint=api.rstrip("/")+"/wp/v2/posts"
    ids=set()
    if start_page>1:
        with store.connect() as conn:
            ids={int(row[0]) for row in conn.execute("SELECT DISTINCT json_extract(payload,'$.source_record_id') FROM observation WHERE json_extract(payload,'$.source_family')=?",(family,)) if row[0] is not None}
    exhausted=False
    for page in range(start_page,max_pages+1):
        url=endpoint+"?"+urlencode({"per_page":100,"page":page,"_fields":"id,date,modified,link,title,content,excerpt,categories,tags"})
        try:
            body,meta=fetcher.fetch(url)
        except Exception as error:
            if "HTTP 400" in str(error) and page>1:
                exhausted=True
                break
            raise
        rows=json.loads(body)
        if not isinstance(rows,list):
            raise ValueError("public posts response is not a list")
        if not rows:
            exhausted=True
            break
        page_ids={r["id"] for r in rows}
        if page_ids<=ids:
            raise ValueError("repeated posts page")
        records=[]
        for row in rows:
            records.append(wordpress_record(row,base,family,url,meta))
        store.put_many(records)
        ids.update(page_ids)
        if page%5==0 or page==1:
            store.status(family,status="running",page=page,records=len(ids))
        if len(rows)<100:
            exhausted=True
            break
    store.status(family,status="complete" if exhausted else "bounded_partial",records=len(ids),last_page=page,note="Public catalog publications include old rounds, lists, news and preparation guides; not all are unique programs")


def dreamspon(store,fetcher,max_pages=500):
    family="dreamspon"
    base="https://www.dreamspon.com"
    ids=set()
    pages=set()
    queue=[base+"/scholarship/list.html",base+"/dreamscholarship/list.html"]
    while queue and len(pages)<max_pages:
        url=queue.pop(0)
        if url in pages:continue
        body,meta=fetcher.fetch(url)
        soup=BeautifulSoup(body,"html.parser")
        pages.add(url)
        for row in soup.select('tr'):
            a=row.select_one('a[href*="/view.html?idx="]')
            if a is None:continue
            target=normalize_url(a["href"],url)
            label=plain(a.get_text(" ",strip=True))
            cells=[plain(td.get_text(" ",strip=True)) for td in row.select('td')]
            records=make_record(url=target,title=label,family=family,record_type="support_catalog_entry",capture_status="listing_metadata",listing_url=url,source_id=dict(parse_qsl(urlsplit(target).query)).get("idx"),issuer_name=cells[1] if len(cells)>1 else None,country="KR",text="\n".join(cells),payload={"listing_cells":cells,"issuer_verification":"not_performed"},evidence_sha256=meta["sha256"])
            store.put(records)
            ids.add(target)
        for a in soup.select('a[href]'):
            target=normalize_url(a["href"],url)
            if target and urlsplit(target).path==urlsplit(url).path and re.search(r"(?:^|&)page=\d+",urlsplit(target).query) and target not in pages:
                queue.append(target)
        if len(pages)%10==0:
            store.status(family,status="running",pages=len(pages),records=len(ids))
    store.status(family,status="complete" if not queue else "bounded_partial",pages=len(pages),records=len(ids),note="Public listing metadata retained; no login-only details requested")


def university_lists(store,fetcher,family,max_pages=100):
    if family=="uq":
        landing="https://scholarships.uq.edu.au/scholarships"
        issuer="The University of Queensland"
    else:
        landing="https://www.scholarships.unsw.edu.au/scholarships/search"
        issuer="UNSW Sydney"
    queue=[landing]
    visited=set()
    ids=set()
    while queue and len(visited)<max_pages:
        url=queue.pop(0)
        if url in visited:continue
        body,meta=fetcher.fetch(url)
        soup=BeautifulSoup(body,"html.parser")
        visited.add(url)
        cards=soup.select('.card--scholarship' if family=="uq" else '.scholarship-card')
        records=[]
        for card in cards:
            a=card.select_one('a[href^="/scholarship/"]' if family=="uq" else 'a[href*="/scholarships/id/"]')
            heading=card.select_one('.card__title' if family=="uq" else '.scholarship-title')
            if not a or not heading:continue
            target=normalize_url(a["href"],url)
            records.append(make_record(url=target,title=heading.get_text(" ",strip=True),family=family,record_type="university_support_program",capture_status="listing_metadata",listing_url=url,issuer_name=issuer,issuer_kind="university",country="AU",text=plain(str(card)),payload={"listing_fields":plain(str(card))},evidence_sha256=meta["sha256"]))
            ids.add(target)
        store.put_many(records)
        for a in soup.select('a[href]'):
            target=normalize_url(a["href"],url)
            if target and urlsplit(target).path==urlsplit(landing).path and re.search(r"(?:^|&)page=\d+",urlsplit(target).query) and target not in visited:
                queue.append(target)
        if len(visited)%10==0:
            store.status(family,status="running",pages=len(visited),records=len(ids))
    store.status(family,status="complete" if not queue else "bounded_partial",pages=len(visited),records=len(ids),note="Named awards with values and criteria from official university listing; no individual eligibility filtering")


def jasso(store,fetcher,max_pages=100):
    family="jasso"
    landing="https://www.studyinjapan.go.jp/en/search-for-scholarships/"
    queue=[landing]
    visited=set()
    ids=set()
    while queue and len(visited)<max_pages:
        url=queue.pop(0)
        if url in visited:continue
        body,meta=fetcher.fetch(url)
        soup=BeautifulSoup(body,"html.parser")
        visited.add(url)
        records=[]
        for a in soup.select('a.school-result-item[href*="detail.php"]'):
            target=normalize_url(a["href"],url)
            name=a.select_one('.scholarship-name')
            issuer=a.select_one('.school-name')
            if not name:continue
            records.append(make_record(url=target,title=name.get_text(" ",strip=True),family=family,record_type="scholarship_tuition_catalog",capture_status="listing_metadata",listing_url=url,source_id=dict(parse_qsl(urlsplit(target).query)).get("mid"),issuer_name=issuer.get_text(" ",strip=True) if issuer else None,country="JP",text=plain(str(a)),payload={"listing_fields":plain(str(a))},evidence_sha256=meta["sha256"]))
            ids.add(target)
        store.put_many(records)
        # The public form declares page(num) -> offset=num, go=ofs, limit=25.
        for a in soup.select('a[onclick]'):
            match=re.search(r"^page\((\d+)\);",a["onclick"])
            if match:
                # Directory URL redirects to the real public form action. Keep it.
                action=meta["final_url"].split("?",1)[0]
                target=action+"?"+urlencode({"lang":"en","go":"ofs","offset":match[1],"limit":25,"narabikae":2})
                if target not in visited:queue.append(target)
        if len(visited)%5==0:
            store.status(family,status="running",pages=len(visited),records=len(ids))
    store.status(family,status="complete" if not queue else "bounded_partial",pages=len(visited),records=len(ids),note="Japanese public scholarship and tuition reduction directory; multiple academic-level entries may refer to one program")


def follow_details(store,fetcher,families,max_records=500):
    records=[]
    for r in store.iter_records():
        if r["source_family"] in families and r["capture_status"]=="listing_metadata":
            records.append(r)
            if len(records)>=max_records:break
    def one(record):
        try:
            body,meta=fetcher.fetch(record["source_url"])
            soup=BeautifulSoup(body,"html.parser")
            content=soup.select_one('main,article,#main-content,#content') or soup
            for tag in content.select('nav,header,footer,script,style,noscript'):tag.decompose()
            text=plain(str(content))
            if re.search(r"just a moment|access denied|verify you are human",text[:600],re.I):
                raise ValueError("access challenge returned")
            value={**record,"capture_status":"detail_collected","detail_text":text,"evidence_sha256":meta["sha256"],"source_payload":{**record["source_payload"],"detail_final_url":meta["final_url"],"detail_artifact":meta["raw_path"]}}
            store.put(value)
            return True
        except Exception:return False
    with ThreadPoolExecutor(max_workers=4) as ex:
        successes=sum(ex.map(one,records))
    store.status("official_detail_enrichment",status="complete",attempted=len(records),received=successes,note="Public detail retrieval; original listing metadata retained on failure")


def additional_official_lists(store,fetcher,family,max_pages=500):
    if family=="ubc":
        landing="https://www.grad.ubc.ca/scholarships-awards-funding/award-opportunities"
        seed=landing
        country="CA"
    else:
        landing="https://www.ukri.org/opportunity/"
        seed=landing+"?"+urlencode([("filter_status[]","open"),("filter_status[]","closed"),("filter_status[]","upcoming")])
        country="GB"
    queue=[seed]
    visited=set()
    ids=set()
    failed_pages=[]
    while queue and len(visited)<max_pages:
        url=queue.pop(0)
        if url in visited:continue
        try:
            body,meta=fetcher.fetch(url)
        except Exception as error:
            visited.add(url)
            failed_pages.append({"url":url,"error":type(error).__name__+": "+str(error)[:160]})
            store.status(family,status="running",pages=len(visited),records=len(ids),failed_pages=failed_pages)
            continue
        soup=BeautifulSoup(body,"html.parser")
        visited.add(url)
        elements=soup.select('table tbody tr') if family=="ubc" else soup.select('h3')
        records=[]
        for element in elements:
            a=element.select_one('a[href^="/awards/"]') if family=="ubc" else element.select_one('a[href*="/opportunity/"]')
            if a is None:continue
            target=normalize_url(a["href"],url)
            if not target:continue
            card=element if family=="ubc" else element.find_parent(['article','li']) or element.parent
            records.append(make_record(url=target,title=a.get_text(" ",strip=True),family=family,record_type="university_support_program" if family=="ubc" else "general_funding_notice",capture_status="listing_metadata",listing_url=url,issuer_name=None if family=="ubc" else "UK Research and Innovation",issuer_kind="unknown" if family=="ubc" else "government",country=country,text=plain(str(card)),payload={"listing_fields":plain(str(card)),"catalog_operator":"University of British Columbia" if family=="ubc" else "UKRI"},evidence_sha256=meta["sha256"]))
            ids.add(target)
        store.put_many(records)
        for a in soup.select('a[href]'):
            target=normalize_url(a["href"],url)
            if not target:continue
            if family=="ubc":
                page_link=urlsplit(target).path==urlsplit(landing).path and re.search(r"(?:^|&)page=\d+",urlsplit(target).query)
                if page_link:
                    number=dict(parse_qsl(urlsplit(target).query))["page"]
                    target=landing+"?"+urlencode({"page":number})
            else:
                page_link=re.fullmatch(r"/opportunity/page/\d+/",urlsplit(target).path)
                if page_link:
                    # Exclude sort variants from the visit identity, not records.
                    target=urlsplit(landing).scheme+"://"+urlsplit(landing).netloc+urlsplit(target).path+"?"+urlencode([("filter_status[]","open"),("filter_status[]","closed"),("filter_status[]","upcoming")])
            if page_link and target not in visited:queue.append(target)
        if family=="ukri" and len(visited)==1:
            # The first page advertises the real last page and a fixed page path.
            numbers=[int(m.group(1)) for m in (re.search(r"/opportunity/page/(\d+)/",u) for u in queue) if m]
            if numbers:
                last=max(numbers)
                for number in range(2,last+1):
                    target=urlsplit(landing).scheme+"://"+urlsplit(landing).netloc+f"/opportunity/page/{number}/?"+urlencode([("filter_status[]","open"),("filter_status[]","closed"),("filter_status[]","upcoming")])
                    if target not in visited:queue.append(target)
        if len(visited)%10==0:
            store.status(family,status="running",pages=len(visited),records=len(ids))
    store.status(family,status="partial_with_page_failures" if failed_pages else "complete" if not queue else "bounded_partial",pages=len(visited),records=len(ids),failed_pages=failed_pages,note="Official list; all returned dates and applicant categories retained")


def run_source(name,store,fetcher,max_pages):
    if name=="grants":return grants_xml(store,fetcher)
    if name=="kosaf":return kosaf_catalog(store,fetcher)
    if name=="youth":return youth_catalog(store,fetcher,max_pages)
    if name=="dreamspon":return dreamspon(store,fetcher,max_pages)
    if name=="scholars4dev":return wordpress(store,fetcher,"https://www.scholars4dev.com/",name,max_pages)
    if name=="opportunitydesk":return wordpress(store,fetcher,"https://opportunitydesk.org/",name,max_pages)
    if name in {"uq","unsw"}:return university_lists(store,fetcher,name,max_pages)
    if name=="jasso":return jasso(store,fetcher,max_pages)
    if name in {"ubc","ukri"}:return additional_official_lists(store,fetcher,name,max_pages)
    raise ValueError(name)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--sources",nargs="+",required=True)
    parser.add_argument("--max-pages",type=int,default=250)
    parser.add_argument("--workers",type=int,default=4)
    args=parser.parse_args()
    store=Store(args.output)
    fetcher=PublicFetcher(store)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures={executor.submit(run_source,name,store,fetcher,args.max_pages):name for name in args.sources}
        for future in as_completed(futures):
            name=futures[future]
            try:future.result()
            except Exception as error:store.status(name,status="failed",error=type(error).__name__+": "+str(error)[:300])
    print(json.dumps(store.export()["counts"],ensure_ascii=False),flush=True)


if __name__=="__main__":main()
