"""Fill missing abstracts using exact DOI matches; never synthesize text."""
import re
import unicodedata
from urllib.parse import urlsplit
from datetime import datetime, timezone
from .core import CrawlError, Cancelled, clean, keywords, safe_url, validate_payload
from .program import title_key


def doi_key(value):
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", clean(value), flags=re.I).lower()


def reconstruct(index):
    if not isinstance(index, dict) or not index:
        return ""
    words = {}
    for word, positions in index.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            return ""
        for position in positions:
            if type(position) is not int or not 0 <= position < 50000 or position in words:
                return ""
            words[position] = word
    if not words or len(words) != max(words) + 1:
        return ""
    return clean(" ".join(words[i] for i in range(len(words))))


def paper_abstract(record):
    """Reject repository artifacts and tiny snippets, without claiming completeness."""
    if record.get("type") in ("dataset","software","paratext","supplementary-material"):
        return ""
    location = record.get("primary_location") or {}
    landing = location.get("landing_page_url") or ""
    doi = doi_key(record.get("doi", ""))
    if doi.startswith(("10.5281/","10.6084/")) or urlsplit(landing).hostname in ("zenodo.org","www.zenodo.org","figshare.com"):
        return ""
    abstract = reconstruct(record.get("abstract_inverted_index"))
    return abstract if len(abstract) >= 80 else ""


def author_key(name):
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    words = re.findall(r"[^\W\d_]+", name.casefold(), re.UNICODE)
    return (words[0][0], words[-1]) if words else None


def enrich_titles(client, papers, cancel, on_batch, progress, api_key=""):
    """Search short title prefixes, then require a unique full title/author match."""
    pending = [dict(p) for p in papers if not p.get("abstract") and not p.get("doi")]
    for offset in range(0, len(pending), 20):
        if cancel.is_set():
            raise Cancelled("摘要补全已停止；已有摘要已保留。")
        batch = pending[offset:offset+20]
        phrases = ['"' + " ".join(re.findall(r"\w+", p.get("title_text") or p["title"], re.UNICODE)[:8])[:70].strip() + '"' for p in batch]
        params = {"search.title":" OR ".join(dict.fromkeys(phrases)), "per_page":100,
                  "select":"id,doi,title,type,publication_year,authorships,abstract_inverted_index,primary_location"}
        if api_key: params["api_key"] = api_key
        progress(f"按标题和作者核对摘要：{offset}/{len(pending)}")
        data = client.get("openalex", params, "openalex:titles", cancel, progress)
        total, records = validate_payload("openalex", data)
        if total > len(records):
            # A broad prefix is not evidence that a paper has no abstract.
            progress("标题检索候选过多，本批保留未核验状态；可用官网链接补全。")
            continue
        for paper in batch:
            authors = {author_key(a) for a in paper.get("authors", [])} - {None}
            matches = [r for r in records if title_key(r.get("title", "")) == title_key(paper.get("title_text") or paper["title"])
                and authors & {author_key(a.get("author",{}).get("display_name", "")) for a in r.get("authorships", [])}
                and isinstance(r.get("publication_year"),int) and abs(r["publication_year"]-paper["year"]) <= 1]
            matches = {r["id"]:r for r in matches if paper_abstract(r)}
            paper["abstract_sources_checked"] = {**paper.get("abstract_sources_checked", {}), "openalex_title":datetime.now(timezone.utc).isoformat()}
            if len(matches) != 1: continue
            record = next(iter(matches.values()))
            abstract = paper_abstract(record)
            if not abstract: continue
            paper.update(abstract=abstract, abstract_source="openalex", abstract_url=safe_url(record["id"]), abstract_status="available")
            # Preserve the existing paper ID so bookmarks survive DOI discovery.
            doi = doi_key(record.get("doi", ""))
            if re.fullmatch(r"10\.\d{4,9}/\S+",doi): paper["doi"] = doi
            if paper.get("keyword_source", "").startswith("local_"):
                paper.update(keywords=keywords(paper["title"],abstract), keyword_source="local_title_abstract")
        on_batch(batch)


def enrich_abstracts(client, papers, cancel, on_batch, progress, api_key=""):
    missing = [dict(p) for p in papers if not p.get("abstract")]
    no_doi = [dict(p, abstract_status="unavailable") for p in missing if not p.get("doi")]
    if no_doi:
        on_batch(no_doi)
    pending = [p for p in missing if p.get("doi")]
    for offset in range(0, len(pending), 100):
        if cancel.is_set():
            raise Cancelled("摘要补全已停止；已有摘要已保留。")
        batch = pending[offset:offset + 100]
        progress(f"补全真实摘要：{offset}/{len(pending)}，按 DOI 查询 OpenAlex")
        params = {"filter": "doi:" + "|".join("https://doi.org/" + doi_key(p["doi"]) for p in batch),
                  "per_page": 100, "select": "id,doi,title,type,abstract_inverted_index,primary_location"}
        if api_key:
            params["api_key"] = api_key
        data = client.get("openalex", params, "openalex:abstracts", cancel, progress)
        total, records = validate_payload("openalex", data)
        if total > len(records):
            raise CrawlError("OpenAlex 摘要响应不完整；本批未标记为摘要缺失。")
        found = {}
        for record in records:
            abstract = paper_abstract(record)
            if abstract:
                found[doi_key(record.get("doi"))] = (abstract, safe_url(record.get("id")))
        for paper in batch:
            paper["abstract_checked_at"] = datetime.now(timezone.utc).isoformat()
            paper["abstract_sources_checked"] = {**paper.get("abstract_sources_checked", {}), "openalex":paper["abstract_checked_at"]}
            match = found.get(doi_key(paper["doi"]))
            if match:
                paper.update(abstract=match[0], abstract_source="openalex", abstract_url=match[1], abstract_status="available")
                if paper.get("keyword_source", "").startswith("local_"):
                    paper.update(keywords=keywords(paper["title"], match[0]), keyword_source="local_title_abstract")
            else:
                paper["abstract_status"] = "unavailable"
        on_batch(batch)
        progress(f"补全真实摘要：{min(offset + 100, len(pending))}/{len(pending)}")
