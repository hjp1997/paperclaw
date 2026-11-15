"""Conference catalogs and verifiable paper landing pages."""
import re
import json
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode
from xml.etree import ElementTree

from .catalog import Document, plain
from .program import VIRTUAL, apply_program, title_key

PAGE_HOSTS = {"arxiv.org", "www.arxiv.org", "aclanthology.org", "eccc.weizmann.ac.il",
              "proceedings.mlr.press", "proceedings.neurips.cc", "dl.acm.org", "ieeexplore.ieee.org",
              "iclr.cc", "icml.cc", "neurips.cc", "cvpr.thecvf.com", "iccv.thecvf.com", "www.usenix.org",
              "doi.org", "link.springer.com", "link.springernature.com", "epubs.siam.org", "www.vldb.org",
              "sc26.conference-program.com", "openaccess.thecvf.com", "ojs.aaai.org"}


def allowed_page(url):
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and parsed.hostname in PAGE_HOSTS and not parsed.username
            and not parsed.password and parsed.port in (None, 443) and not parsed.fragment)


def parse_page(raw, url):
    from .core import clean
    root = Document(raw.decode("utf-8")).root
    metadata = {}
    for tag in root.find("meta"):
        name = (tag.attrs.get("name") or tag.attrs.get("property") or "").lower()
        if name and tag.attrs.get("content"):
            metadata[name] = tag.attrs["content"]
    headings = root.find(cls="presentation-title") or root.find("h1")
    title = metadata.get("citation_title") or metadata.get("dc.title") or (plain(headings[0]) if headings else "") or metadata.get("og:title")
    abstracts = root.find(cls="abstract") or root.find(cls="abstractSection") or root.find(cls="field-name-field-paper-description-long")
    # Springer has several section bodies; only the Abstract section is relevant.
    if urlsplit(url).hostname in ("link.springer.com","link.springernature.com"):
        sections = [n for n in root.find("section") if n.attrs.get("data-title", "").casefold() == "abstract" or n.attrs.get("id") == "Abs1"]
        abstracts = sections
    abstracts += [node for node in root.find() if node.attrs.get("id") in ("abstract", "abstract_details", "abstractText")]
    body = [plain(node) for node in abstracts if plain(node)]
    abstract = max(body, key=len) if body else (metadata.get("citation_abstract") or metadata.get("dc.description") or "")
    for script in root.find("script"):
        text = "".join(c for c in script.children if isinstance(c,str))
        payloads = []
        if script.attrs.get("type") == "application/ld+json":
            try:
                data = json.loads(text)
                payloads = data if isinstance(data,list) else [data]
                payloads += [n for d in list(payloads) if isinstance(d,dict) for n in d.get("@graph",[]) if isinstance(n,dict)]
            except (ValueError,TypeError): pass
        elif "xplGlobal.document.metadata" in text:
            match = re.search(r"xplGlobal\.document\.metadata\s*=\s*",text)
            if match:
                try: payloads = [json.JSONDecoder().raw_decode(text[match.end():])[0]]
                except ValueError: pass
        for data in payloads:
            if not isinstance(data,dict): continue
            kind = data.get("@type", [])
            kinds = [kind] if isinstance(kind,str) else kind if isinstance(kind,list) else []
            if script.attrs.get("type") == "application/ld+json" and not any(k in ("ScholarlyArticle","Article") for k in kinds): continue
            if isinstance(data.get("abstract"),str) and data["abstract"].strip():
                # A complete structured abstract beats short description metadata.
                abstract = plain(Document(str(data["abstract"])).root)
                title = data.get("headline") or data.get("title") or data.get("name") or title
                metadata["citation_doi"] = data.get("doi") or metadata.get("citation_doi", "")
    abstract = re.sub(r"^\s*Abstract\s*[:：]?\s*", "", abstract, flags=re.I)
    title = re.sub(r"^\s*Title\s*[:：]?\s*", "", title, flags=re.I)
    if not title or not abstract:
        raise ValueError("Paper title and abstract not found")
    return {"title":clean(title), "abstract":clean(abstract), "url":url,
            "doi":metadata.get("citation_doi", ""), "authors":[plain(n) for n in root.find(cls="authors")]}


def page_candidates(paper):
    candidates = [paper.get("official_url"),paper.get("publisher_url"),paper.get("url")]
    doi = paper.get("doi", "")
    if doi:
        host = ("https://dl.acm.org/doi/" if doi.lower().startswith("10.1145/") else
                "https://link.springer.com/chapter/" if doi.lower().startswith("10.1007/") else
                "https://epubs.siam.org/doi/" if doi.lower().startswith("10.1137/") else "https://doi.org/")
        candidates.append(host+doi)
    result = []
    for candidate in candidates:
        if not candidate: continue
        parsed = urlsplit(candidate)
        if parsed.hostname == "sc26.conference-program.com" and parsed.path == "/":
            values = parse_qs(parsed.query)
            query = urlencode({key:values[key][0] for key in ("id","sess") if key in values})
            candidate = urlunsplit(("https",parsed.netloc,"/presentation/",query,""))
        if allowed_page(candidate) and not urlsplit(candidate).path.lower().endswith(".pdf") and candidate not in result:
            result.append(candidate)
    return result


def parse_arxiv(raw):
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as error:
        raise ValueError("Invalid arXiv response") from error
    ns = {"a":"http://www.w3.org/2005/Atom", "arxiv":"http://arxiv.org/schemas/atom"}
    return {"papers":[{"title":entry.findtext("a:title", "", ns),
                       "abstract":entry.findtext("a:summary", "", ns),
                       "doi":entry.findtext("arxiv:doi", "", ns),
                       "url":entry.findtext("a:id", "", ns).replace("http://", "https://"),
                       "authors":[a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)]}
                      for entry in root.findall("a:entry", ns)]}


def records(client, conference, year, cancel, progress):
    rows = client.get("program", {"conference":conference, "year":year, "part":"program", "content_version":3},
                      f"conference:{conference}:{year}", cancel, progress)["annotations"]
    if conference in VIRTUAL and any(not row.get("abstract") for row in rows):
        from .core import CrawlError, Cancelled
        try:
            progress(f"{conference} {year}：读取独立的官方摘要索引")
            abstracts = client.get("program", {"conference":conference, "year":year, "part":"abstracts", "content_version":1},
                                   f"conference:{conference}:{year}", cancel, progress)["annotations"]
            by_id = {item["event_id"]:item["abstract"] for item in abstracts}
            rows = [{**row, "abstract":row.get("abstract") or by_id.get(row.get("event_id", ""), "")} for row in rows]
        except Cancelled:
            raise
        except CrawlError:
            progress(f"{conference} {year}：独立摘要索引暂不可用，保留论文目录和已有摘要。")
    return rows


def crawl(client, conference, year, cancel, on_page, progress):
    from .core import CrawlError, normalize
    if conference not in VIRTUAL:
        raise CrawlError("会议官网来源支持 ICLR、ICML、NeurIPS、CVPR、ICCV。")
    progress(f"{conference} {year}：读取会议官网目录和摘要")
    rows = records(client, conference, year, cancel, progress)
    by_paper = {}
    for row in rows:
        key = (title_key(row["title"]), tuple(sorted(a.casefold() for a in row.get("authors", []))))
        existing = by_paper.get(key)
        if existing:
            existing["labels"] = sorted(set(existing["labels"]) | set(row["labels"]))
            if not existing.get("abstract") and row.get("abstract"):
                existing["abstract"] = row["abstract"]
        else:
            by_paper[key] = dict(row)
    batch = []
    for row in by_paper.values():
        paper = normalize({"title":row["title"], "publication_title":conference, "publication_year":year,
                           "key":row["url"], "authors":{"authors":[{"full_name":a} for a in row.get("authors",[])]},
                           "abstract":row.get("abstract",""), "abstract_url":row["url"], "pdf_url":row.get("pdf_url","")},
                          "ieee", conference, year)
        if paper:
            paper.update(source="conference_site", publisher_url=row["url"], official_url=row["url"],
                         abstract_source="conference_site" if paper["abstract"] else "",
                         categories=row["labels"], category_sources=dict.fromkeys(row["labels"], row["url"]))
            batch.append(paper)
    if not batch:
        raise CrawlError("会议官网尚未返回本届论文目录，或目录格式已变化。")
    on_page(batch, len(batch), len(batch))


def enrich(client, papers, cancel, on_batch, progress):
    editions = {(p["conference"], p["year"]) for p in papers if p["conference"] in VIRTUAL and
                (not p.get("abstract") or p.get("abstract_source") == "openalex")}
    for conference, year in sorted(editions):
        target = [p for p in papers if (p["conference"],p["year"]) == (conference,year)]
        apply_program(target, records(client, conference, year, cancel, progress))
        on_batch(target)


def update_paper(client, paper, cancel, progress, url=""):
    from .core import CrawlError, clean, keywords
    if paper["conference"] in VIRTUAL and not url:
        rows = records(client, paper["conference"], paper["year"], cancel, progress)
        exact = [r for r in rows if title_key(r["title"]) == title_key(paper.get("title_text") or paper["title"])]
        if exact:
            best = max(exact, key=lambda r:len(r.get("abstract", "")))
            if best.get("abstract"):
                paper.update(abstract=clean(best["abstract"]), abstract_source="conference_site",
                             abstract_url=best["url"], abstract_status="available", title_official=best["title"])
                return paper
            url = best["url"]
    candidates = [url] if url else [paper.get("official_url"), paper.get("publisher_url"), paper.get("url")]
    last_error = ""
    for candidate in dict.fromkeys(u for u in candidates if u):
        if not allowed_page(candidate):
            continue
        try:
            result = client.get("paper_page", {"url":candidate}, "paper:metadata", cancel, progress)
            if title_key(result["title"]) != title_key(paper.get("title_text") or paper["title"]):
                raise CrawlError("来源页面标题与所选论文不一致。")
            break
        except CrawlError as error:
            from .core import Cancelled
            if isinstance(error, Cancelled):
                raise
            last_error = str(error)
    else:
        if url:
            raise CrawlError(last_error or "该链接不在支持的论文来源中。")
        progress("正在查询 arXiv 论文原始摘要")
        data = client.get("arxiv", {"search_query":'ti:"' + (paper.get("title_text") or paper["title"]).replace('"', '') + '"',
                                   "max_results":5}, "arxiv:metadata", cancel, progress)
        matches = [p for p in data["papers"] if title_key(p["title"]) == title_key(paper.get("title_text") or paper["title"])]
        if len(matches) != 1:
            raise CrawlError("未找到可唯一匹配的原始摘要。" + last_error)
        result = matches[0]
    doi = result.get("doi", "").lower().removeprefix("https://doi.org/")
    if doi and paper.get("doi") and doi != paper["doi"].lower():
        raise CrawlError("来源页面 DOI 与所选论文不一致。")
    host = urlsplit(result["url"]).hostname
    source = "conference_site" if host in VIRTUAL.values() else "arxiv" if host == "arxiv.org" else "paper_website"
    paper.update(abstract=clean(result["abstract"]), abstract_source=source,
                 abstract_url=result["url"], abstract_status="available", official_url=result["url"], title_official=clean(result["title"]))
    if paper.get("keyword_source", "").startswith("local_"):
        paper.update(keywords=keywords(paper["title"], paper["abstract"]), keyword_source="local_title_abstract")
    return paper
