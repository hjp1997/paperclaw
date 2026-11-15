"""Fill missing abstracts using exact DOI matches; never synthesize text."""
import re
from datetime import datetime, timezone
from .core import CrawlError, Cancelled, clean, keywords, safe_url, validate_payload


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


def enrich_abstracts(client, papers, cancel, on_batch, progress, api_key=""):
    missing = [dict(p) for p in papers if not p.get("abstract")]
    no_doi = [dict(p, abstract_status="unavailable") for p in missing if not p.get("doi")]
    if no_doi:
        on_batch(no_doi)
    pending = [p for p in missing if p.get("doi")]
    for offset in range(0, len(pending), 50):
        if cancel.is_set():
            raise Cancelled("摘要补全已停止；已有摘要已保留。")
        batch = pending[offset:offset + 50]
        progress(f"补全真实摘要：{offset}/{len(pending)}，按 DOI 查询 OpenAlex")
        params = {"filter": "doi:" + "|".join("https://doi.org/" + doi_key(p["doi"]) for p in batch),
                  "per-page": 200, "select": "id,doi,title,abstract_inverted_index"}
        if api_key:
            params["api_key"] = api_key
        data = client.get("openalex", params, "openalex:abstracts", cancel, progress)
        total, records = validate_payload("openalex", data)
        if total > len(records):
            raise CrawlError("OpenAlex 摘要响应不完整；本批未标记为摘要缺失。")
        found = {}
        for record in records:
            abstract = reconstruct(record.get("abstract_inverted_index"))
            if abstract:
                found[doi_key(record.get("doi"))] = (abstract, safe_url(record.get("id")))
        for paper in batch:
            paper["abstract_checked_at"] = datetime.now(timezone.utc).isoformat()
            match = found.get(doi_key(paper["doi"]))
            if match:
                paper.update(abstract=match[0], abstract_source="openalex", abstract_url=match[1], abstract_status="available")
                if paper.get("keyword_source", "").startswith("local_"):
                    paper.update(keywords=keywords(paper["title"], match[0]), keyword_source="local_title_abstract")
            else:
                paper["abstract_status"] = "unavailable"
        on_batch(batch)
        progress(f"补全真实摘要：{min(offset + 50, len(pending))}/{len(pending)}")
