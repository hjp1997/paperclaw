"""Providers, persistent request policy, normalization, and portable exports.

Only public metadata endpoints are contacted. No cookies or login sessions.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from .catalog import CATALOG, Document, plain, parse_catalog, parse_usenix
from . import dblp_rdf, openreview, official, proceedings
from .program import AWARD_INDEX, parse_award_index, program_url, parse_program
from .titles import prepare_title

CONFERENCES = {name: entry["stream"] for name, entry in CATALOG.items()}
ENDPOINTS = {
    "crossref": "https://api.crossref.org/works",
    "dblp": "https://dblp.org/search/publ/api",
    "dblp_rdf": "https://sparql.dblp.org/sparql",
    "ieee": "https://ieeexploreapi.ieee.org/api/v1/search/articles",
    "openalex": "https://api.openalex.org/works",
    "ccf": "https://ccf.atom.im/",
    "usenix": "https://www.usenix.org/conference/",
    "award_index": AWARD_INDEX,
    "openreview_groups": "https://api2.openreview.net/groups",
    "openreview": "https://api2.openreview.net/notes",
    "openreview_v1": "https://api.openreview.net/notes",
    "arxiv": "https://export.arxiv.org/api/query",
}
DAY = 86400
MAX_RESPONSE = 20 * 1024 * 1024


class CrawlError(Exception):
    """A sanitized error suitable for the local UI."""


class Cancelled(CrawlError):
    pass


class AccessError(CrawlError):
    def __init__(self, message, source, kind):
        super().__init__(message)
        self.source = source
        self.kind = kind


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a key to a redirected host.


def request_json(url, *, body=None, headers=None, max_response=MAX_RESPONSE):
    request = urllib.request.Request(
        url, data=body,
        headers={"User-Agent": "PaperClaw/1.0 (local conference metadata collector)",
                 "Accept": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=40) as response:
            return response.status, dict(response.headers), response.read(max_response + 1)
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read(8192)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CrawlError("网络连接失败或超时；本轮已停止，没有自动重试。请检查网络后手动重试。") from None


def header(headers, name):
    return next((v for k, v in headers.items() if k.lower() == name.lower()), "")


class SafeClient:
    """One serialized request lane, 10 s spacing, 300 requests / rolling 24 h.

    Policy/cache writes happen under this lock. Restart preserves request quota.
    Failures stop the current run without adding a manual-retry cooldown.
    """

    def __init__(self, directory, *, transport=request_json, clock=time.time, interval=10):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.policy_file = self.directory / "policy.json"
        self.lock = threading.Lock()
        self.transport, self.clock, self.interval = transport, clock, interval

    def query_cache_file(self, source, params):
        key = hashlib.sha256(json.dumps([source, params], sort_keys=True).encode()).hexdigest()
        return self.directory / "cache" / ("complete-query-" + key + ".json")

    def get_cached_query(self, source, params):
        with self.lock:
            try:
                cached = read_json(self.query_cache_file(source, params), {})
                if cached and 0 <= self.clock() - cached["time"] < DAY:
                    data = cached["data"]
                    if isinstance(data["items"], list) and len(data["items"]) == data["total"]:
                        return data
            except (ValueError, KeyError, TypeError, OSError):
                pass
        return None

    def cache_query(self, source, params, data):
        with self.lock:
            atomic_json(self.query_cache_file(source, params), {"time": self.clock(), "data": data})

    def get(self, source, params, scope, cancel, progress=lambda message: None, *, cache=True):
        public_params = {k: v for k, v in params.items() if k not in ("apikey", "api_key")}
        cache_key = hashlib.sha256(json.dumps([source, public_params], sort_keys=True).encode()).hexdigest()
        cache_file = self.directory / "cache" / (cache_key + ".json")
        with self.lock:
            if cancel.is_set():
                raise Cancelled("已停止，已采集结果仍可导出。")
            now = self.clock()
            try:
                cached = read_json(cache_file, {}) if cache else {}
            except (ValueError, OSError):
                cached = {}
            ttl = 3600 if source.startswith("openreview") else DAY
            if cached and 0 <= now - cached.get("time", 0) < ttl:
                progress("读取 1 小时内的 OpenReview 缓存" if source.startswith("openreview") else "读取 24 小时内的缓存")
                return cached["data"]
            try:
                policy = read_json(self.policy_file, {"requests": []})
                requests = [t for t in policy["requests"] if now - t < DAY]
            except (ValueError, KeyError, TypeError):
                raise CrawlError("限速状态文件损坏，已停止请求。请保留 policy.json 并检查文件。") from None
            if len(requests) >= 300:
                raise CrawlError("已达到 24 小时 300 次请求上限，请稍后再试。缓存仍可使用。")
            delay = max(0, (requests[-1] + self.interval - now) if requests else 0)
            if delay:
                progress(f"限速等待 {delay:.0f} 秒")
                if cancel.wait(delay):
                    raise Cancelled("已停止，已采集结果仍可导出。")
            if cancel.is_set():
                raise Cancelled("已停止，已采集结果仍可导出。")
            now = self.clock()
            # Ignore and retire cooldown fields from earlier versions.
            policy = {"requests": requests + [now]}
            atomic_json(self.policy_file, policy)  # Count failed attempts too.
            limit = 96 * 1024 * 1024 if source in ("program","proceedings") else MAX_RESPONSE
            if source == "paper_page":
                url = params["url"]
                if not official.allowed_page(url):
                    raise CrawlError("该论文链接不在支持的来源中。")
            elif source == "proceedings":
                url = proceedings.url(params["conference"],params["year"],params.get("page", ""))
            elif source == "program":
                url = program_url(params["conference"], params["year"], params.get("part", "program"))
            else:
                url = ENDPOINTS[source] + "?" + urllib.parse.urlencode(params)
            if source == "usenix":
                slug = {"NSDI":"nsdi", "FAST":"fast", "ACM SIGOPS ATC":"atc"}[params.get("conference","NSDI")]
                url = ENDPOINTS[source] + f"{slug}{int(params['year']) % 100:02d}/technical-sessions"
            elif source in ("ccf", "award_index"):
                url = ENDPOINTS[source]
            elif source == "dblp_rdf":
                # SPARQL JSON is negotiated via the explicit format parameter.
                url = ENDPOINTS[source] + "?" + urllib.parse.urlencode({"query": params["query"], "format": "json"})
            if source in ("program","proceedings") and self.transport is request_json:
                status, headers, raw = self.transport(url, max_response=limit)
            else:
                status, headers, raw = self.transport(url)
            text = raw[:8192].decode("utf-8", errors="replace").lower()
            looks_json = raw.lstrip().startswith((b"{", b"["))
            api_error = {}
            if status in (401, 403) and looks_json:
                try:
                    api_error = json.loads(raw)
                except ValueError:
                    pass
            if source == "openreview_groups" and status == 404 and looks_json:
                try:
                    error = json.loads(raw)
                except ValueError:
                    error = {}
                if error.get("name") == "NotFoundError":
                    return {"groups": []}
            challenge = api_error.get("name") == "ChallengeRequiredError" or status == 202 or (not looks_json and any(x in text for x in (
                "captcha", "verify you are human", "checking your browser", "challenge-platform",
                "anubis", "challenge.js", "just a moment", "cf-chl-",
            )))
            location = header(headers, "Location")
            login = (status in (301, 302, 303, 307, 308) and
                     re.search(r"/(?:log-?in|sign-?in|authenticate)(?:[/#?.]|$)", location, re.I))
            login = login or (not looks_json and re.search(
                r"<title[^>]*>\s*(?:sign\s*in|log\s*in|user login|登录)(?:\s*[|:–-]|\s*</title>)", text))
            if not looks_json and source not in ("usenix", "ccf"):
                login = login or re.search(r"<input\b[^>]*\btype\s*=\s*['\"]password['\"]", text)
            denied_page = not looks_json and re.search(r"<(?:title|h1)[^>]*>\s*(?:access denied|403 forbidden)\b", text)
            if status in (401, 403, 429) or challenge or login or denied_page:
                if status == 429:
                    reason = "服务端限流（429）"
                    retry_after = header(headers, "Retry-After")
                    if retry_after:
                        reason += f"，服务端建议 Retry-After: {retry_after}（仅提示）"
                elif challenge:
                    reason = "网站验证挑战"
                elif login:
                    reason = "来源要求登录"
                elif denied_page:
                    reason = "来源页面提示访问被拒绝"
                else:
                    reason = f"访问被拒绝（{status}）"
                if source.startswith("openreview"):
                    reason += "；OpenReview 公开接口暂不可用。可打开会议页面核对，或手动选择 DBLP"
                if source == "openalex" and status in (401, 403, 429):
                    reason += "；请在页面填写有效的 OpenAlex Key 后继续补全摘要（已有摘要保留）"
                if status == 429:
                    raise CrawlError(reason + "。本轮已停止，可手动重试。")
                kind = "denied"
                if challenge:
                    kind = "challenge"
                elif source in ("ieee", "openalex"):
                    kind = "api_key"
                    reason += "。请检查 API Key 及接口权限"
                elif login or status == 401:
                    kind = "login"
                raise AccessError(reason + "。本轮已停止，可手动继续。", source, kind)
            if status != 200:
                raise CrawlError(f"来源返回 HTTP {status}；本轮已停止，没有自动重试。")
            if len(raw) > limit:
                raise CrawlError("来源响应过大，本轮已停止。")
            try:
                data = (official.parse_page(raw, url) if source == "paper_page" else
                        proceedings.parse(raw, params["conference"],int(params["year"]),params.get("page", "")) if source == "proceedings" else
                        official.parse_arxiv(raw) if source == "arxiv" else
                        parse_award_index(raw) if source == "award_index" else
                        parse_program(raw, params["conference"], params["year"], params.get("part", "program")) if source == "program" else
                        parse_usenix(raw.decode("utf-8"), int(params["year"]), params.get("conference","NSDI")) if source == "usenix" else
                        parse_catalog(raw.decode("utf-8")) if source == "ccf" else json.loads(raw))
                validate_payload(source, data)
            except (ValueError, TypeError, KeyError):
                raise CrawlError("来源未返回有效的论文数据（可能是验证页面、页面结构变化或 API Key 错误）；没有将失败记为 0 篇。") from None
            # Persist only bibliographic responses; never the URL or API key.
            if cache:
                atomic_json(cache_file, {"time": self.clock(), "data": data})
            return data


def as_list(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


def clean(value):
    if isinstance(value, dict):
        value = value.get("text", "")
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def safe_url(value):
    value = clean(value)
    parsed = urllib.parse.urlsplit(value)
    return value if parsed.scheme in ("http", "https") and parsed.hostname else ""


def publisher_link(conference, urls, doi):
    """Use the record's publisher landing page, or resolve its registered DOI."""
    entry = CATALOG.get(conference)
    if not entry:
        return ""
    host = urllib.parse.urlsplit(entry["publisher_site"]).hostname
    for url in urls:
        if not url:
            continue
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme in ("https", "http") and parsed.hostname in (host, host.removeprefix("www.")) and not parsed.username and not parsed.password:
            return urllib.parse.urlunsplit(("https", host, parsed.path, parsed.query, parsed.fragment))
    prefix = entry["prefix"]
    if prefix and doi.lower().startswith(prefix + "/"):
        suffix = urllib.parse.quote(doi, safe="/():")
        if prefix == "10.1145":
            return "https://dl.acm.org/doi/" + suffix
        return "https://doi.org/" + suffix
    return ""


STOPWORDS = set("a an the and or for of in on to with by from at as via into over under using based towards toward efficient effective novel new approach method system framework learning data network networks study analysis we our this that is are be can has have it its which paper propose proposed".split())


def keywords(title, abstract="", limit=7):
    """Simple local heuristic. Explicitly labeled, never presented as author terms."""
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", (title + " " + title + " " + abstract).lower())
    counts = Counter(w for w in words if w not in STOPWORDS)
    return [w for w, _ in counts.most_common(limit)]


def is_frontmatter(title):
    title = title.strip(" .[]").lower()
    return bool(re.match(r"^(front\s?matter|back\s?matter|table of contents|author index|copyright( notice)?|organizing committee|technical program committee|reviewers|sponsors|a message from the chairs|message from (the )?.*chairs?|welcome (message|from.*)|preface|foreword)$", title))


def venue_matches(venue, conference):
    patterns = {
        "INFOCOM": r"\bINFOCOM\b",
        "ICDE": r"\bICDEW?\b|international conference on data engineering",
        "MobiCom": r"\bMobiCom\b|(?:international|annual) conference on mobile computing and networking",
        "SIGCOMM": r"\bSIGCOMM\b",
        "NSDI": r"\bNSDI\b|networked systems design and implementation",
    }
    if conference in patterns:
        return bool(re.search(patterns[conference], venue, re.I))
    aliases = {
        "SIGKDD": ("KDD", "Knowledge Discovery and Data Mining"),
        "NeurIPS": ("NIPS", "Neural Information Processing Systems"),
        "WWW": ("The Web Conference", "World Wide Web Conference"),
        "VLDB": ("Proc. VLDB Endow.", "Very Large Data Bases"),
        "SIGMOD": ("Proc. ACM Manag. Data", "Management of Data"),
        "STOC": ("Theory of Computing",),
        "CVPR": ("Computer Vision and Pattern Recognition",),
        "ACM SIGOPS ATC": ("USENIX ATC", "USENIX Annual Technical Conference"),
    }
    names = (conference, CATALOG[conference]["title"], *aliases.get(conference, ()))
    return any(re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", venue, re.I) for n in names)


def is_workshop(venue):
    return bool(re.search(r"wo(?:r)?kshops?|\b(?:ICDEW|ICCVW|CVPRW)\b|\bWKS[HP]?S?\b", venue, re.I))


def validate_payload(source, data):
    if not isinstance(data, dict):
        raise ValueError("Not an object")
    if source == "dblp":
        result = data["result"]
        if str(result.get("status", {}).get("@code", "200")) != "200":
            raise ValueError("Search failed")
        hits = result["hits"]
        total = int(hits["@total"])
        records = as_list(hits.get("hit"))
        for hit in records:
            if not isinstance(hit, dict) or not isinstance(hit.get("info"), dict):
                raise ValueError("Bad hit")
    elif source == "dblp_rdf":
        records = data["results"]["bindings"]
        if not isinstance(records, list):
            raise ValueError("Invalid SPARQL response")
        total = len(records)
    elif source == "crossref":
        if data.get("status") != "ok" or data.get("message-type") != "work-list":
            raise ValueError("Crossref search failed")
        message = data["message"]
        total = int(message["total-results"])
        records = message["items"]
        if not isinstance(records, list) or any(not isinstance(row, dict) or not row.get("DOI") for row in records):
            raise ValueError("Bad Crossref records")
    elif source == "openalex":
        total, records = int(data["meta"]["count"]), data["results"]
        if not isinstance(records, list) or any(not isinstance(r, dict) or not r.get("id") for r in records):
            raise ValueError("Bad OpenAlex records")
    elif source == "openreview_groups":
        records = data["groups"]
        if not isinstance(records, list) or any(not isinstance(g, dict) or not g.get("id") for g in records):
            raise ValueError("Bad OpenReview groups")
        total = len(records)
    elif source in ("openreview", "openreview_v1"):
        records, total = data["notes"], int(data["count"])
        if not isinstance(records, list) or any(not isinstance(n, dict) or not n.get("id") or not isinstance(n.get("content"), dict) for n in records):
            raise ValueError("Bad OpenReview notes")
    elif source == "arxiv":
        records, total = data["papers"], len(data["papers"])
    elif source == "paper_page":
        records, total = [data], 1
        if not data.get("title") or not data.get("abstract"):
            raise ValueError("Incomplete paper page")
    elif source == "award_index":
        records = data["editions"]
        total = len(records)
    elif source == "program":
        records = data["annotations"]
        total = len(records)
    elif source == "ccf":
        records = data["entries"]
        if not isinstance(records, list) or not records:
            raise ValueError("Empty directory")
        total = len(records)
    else:
        total = int(data["total_records"])
        records = as_list(data.get("articles"))
        if any(not isinstance(article, dict) for article in records):
            raise ValueError("Bad article")
    if total < 0:
        raise ValueError("Negative total")
    return total, records


def normalize(info, source, conference, year, include_workshops=False):
    if source == "crossref":
        venue = clean(next(iter(as_list(info.get("container-title"))), ""))
        if info.get("type") != "proceedings-article" or not crossref_venue_matches(venue, conference):
            return None
        doi = clean(info.get("DOI"))
        if not CATALOG[conference]["prefix"] or not doi.lower().startswith(CATALOG[conference]["prefix"] + "/"):
            return None
        mapped = {
            "title": clean(next(iter(as_list(info.get("title"))), "")),
            "publication_title": venue, "publication_year": crossref_year(info),
            "authors": {"authors": [{"full_name": clean(a.get("name")) or clean(f"{a.get('given', '')} {a.get('family', '')}")}
                                    for a in info.get("author", [])]},
            "doi": doi, "pages": clean(info.get("page")),
            "abstract": plain(Document(str(info.get("abstract", ""))).root),
            "abstract_url": info.get("resource", {}).get("primary", {}).get("URL") or info.get("URL"),
        }
        paper = normalize(mapped, "ieee", conference, year, include_workshops)
        if paper:
            paper.update(source="crossref", source_record_id=doi, abstract_source="crossref" if paper["abstract"] else "")
        return paper
    venue = clean(info.get("venue") if source == "dblp" else info.get("publication_title"))
    title = clean(info.get("title"))
    pubyear = clean(info.get("year") if source == "dblp" else info.get("publication_year"))
    if (pubyear != str(year) or not venue_matches(venue, conference) or not title or is_frontmatter(title)):
        return None
    workshop = is_workshop(venue)
    if workshop and not include_workshops:
        return None
    if source == "dblp" and info.get("type") != "Conference and Workshop Papers":
        return None
    if source == "ieee" and info.get("content_type", "Conferences") != "Conferences":
        return None
    author_data = info.get("authors", {}) or {}
    authors = [clean(a.get("full_name", a.get("text", "")) if isinstance(a, dict) else a)
               for a in as_list(author_data.get("author") if source == "dblp" else author_data.get("authors"))]
    doi = clean(info.get("doi"))
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    doi_url = "https://doi.org/" + urllib.parse.quote(doi, safe="/():") if doi else ""
    article_number = clean(info.get("article_number"))
    abstract = clean(info.get("abstract"))
    pdf_url = safe_url(info.get("pdf_url"))
    landing = safe_url(info.get("abstract_url"))
    external = [safe_url(x) for x in as_list(info.get("ee"))]
    landing = landing or (f"https://ieeexplore.ieee.org/document/{article_number}" if article_number.isdigit() else "")
    if not landing:
        landing = publisher_link(conference, external, doi) or next((u for u in external if u), "") or doi_url
    if conference == "NSDI":
        landing = publisher_link(conference, [landing], doi) or landing
    # A DOI is a landing link, never invent a PDF address from it.
    terms, term_source = [], ""
    for name in ("author_terms", "ieee_terms"):
        section = (info.get("index_terms") or {}).get(name, {})
        values = as_list(section.get("terms")) if isinstance(section, dict) else []
        if values:
            terms, term_source = list(dict.fromkeys(clean(x) for x in values if clean(x))), name
            break
    if not terms:
        terms, term_source = keywords(title, abstract), "local_title_abstract" if abstract else "local_title"
    identity = doi.lower() or (landing if conference == "NSDI" and landing else "") or clean(info.get("key")) or article_number or f"{conference}:{year}:{title.lower()}"
    return {
        "id": hashlib.sha256(identity.encode()).hexdigest()[:24], "conference": conference,
        "year": int(year), "title": title, "authors": [a for a in authors if a], "venue": venue,
        "track": "workshop" if workshop else "main", "doi": doi,
        "pages": clean(info.get("pages")) or "-".join(filter(None, [clean(info.get("start_page")), clean(info.get("end_page"))])),
        "abstract": abstract, "keywords": terms, "keyword_source": term_source,
        "abstract_source": source if abstract else "", "abstract_url": landing if abstract else "",
        "abstract_status": "available" if abstract else "not_checked", "authors_text": clean(info.get("authors_text")),
        "url": landing or safe_url(info.get("url")), "pdf_url": pdf_url,
        "publisher_url": publisher_link(conference, [landing, *external], doi),
        "dblp_url": safe_url(info.get("url")) if source == "dblp" else "",
        "source": source, "source_record_id": clean(info.get("key")) or article_number,
        "access": clean(info.get("access")) or clean(info.get("accessType")),
        **({"category_sources": dict.fromkeys(info["program_labels"], landing)}
           if source == "usenix" and info.get("program_labels") else {}),
        "fetched_at": datetime.now(timezone.utc).isoformat(), "ai_summary": "",
    }


def crossref_year(record):
    dates = record.get("published", {}).get("date-parts", [[]])
    return dates[0][0] if dates and dates[0] else ""


def crossref_venue_matches(title, conference):
    if conference == "INFOCOM":
        # ICRITO also contains the word "Infocom", but is a different conference.
        return bool(re.search(r"\bIEEE\s+INFOCOM\b|\bINFOCOM\s+\d{4}\b|\bConference on Computer Communications\b", title, re.I))
    return venue_matches(title, conference)


def crossref_pages(client, params, scope, cancel, progress):
    if cancel.is_set():
        raise Cancelled("已停止，已采集结果仍可导出。")
    cached = client.get_cached_query("crossref", params)
    if cached is not None:
        progress("读取 24 小时内的 Crossref 完整查询缓存")
        yield cached["items"], cached["total"], cached["total"]
        return
    fetched, seen, collected, cursor = 0, set(), [], "*"
    while True:
        if cancel.is_set():
            raise Cancelled("已停止，已采集结果仍可导出。")
        # Crossref cursors can expire or remain unchanged while advancing server-side.
        # Cache only complete queries, never individual cursor responses.
        data = client.get("crossref", {**params, "cursor": cursor}, scope, cancel, progress, cache=False)
        total, records = validate_payload("crossref", data)
        if not records and fetched < total:
            raise CrawlError("Crossref 提前返回空页，当前结果不完整；已保留前面页的数据。")
        for record in records:
            doi = clean(record["DOI"]).lower()
            if doi in seen:
                raise CrawlError("Crossref 分页返回重复 DOI，当前结果不完整；请稍后手动重新采集。")
            seen.add(doi)
        collected.extend(records)
        fetched += len(records)
        yield records, total, fetched
        if fetched >= total:
            client.cache_query("crossref", params, {"items": collected, "total": total})
            return
        cursor = data["message"].get("next-cursor")
        if not isinstance(cursor, str) or not cursor:
            raise CrawlError("Crossref 未提供后续分页游标，当前结果不完整；已保留前面页的数据。")


def crawl_crossref(client, conference, year, include_workshops, cancel, on_page, progress):
    scope = f"crossref:{conference}:{year}"
    dates = f"from-pub-date:{year}-01-01,until-pub-date:{year}-12-31"
    prefix = CATALOG[conference]["prefix"]
    queries = ["Mobile Computing Networking" if conference == "MobiCom" else conference] + (["ICDEW"] if conference == "ICDE" and include_workshops else [])
    progress(f"{conference} {year}：查找 Crossref 登记的会议论文集")
    venues = []
    for query in queries:
        # Broad phrases such as "International Conference" match thousands of venues.
        params = {"filter": f"prefix:{prefix},type:proceedings,{dates}", "query.title": query,
                  "rows": 100, "select": "DOI,title,type,published"}
        for records, _, _ in crossref_pages(client, params, scope, cancel, progress):
            for record in records:
                if (record.get("type") != "proceedings" or crossref_year(record) != year
                        or not clean(record.get("DOI")).lower().startswith(prefix + "/")):
                    continue
                title = clean(next(iter(as_list(record.get("title"))), ""))
                if crossref_venue_matches(title, conference) and (include_workshops or not is_workshop(title)):
                    if title not in venues:
                        venues.append(title)
    if not venues:
        raise CrawlError(f"Crossref 尚未找到 {conference} {year} 的匹配论文集。可检查年份或选择 DBLP；未将查询失败记为完整采集。")
    finished, total_before = 0, 0
    for venue in venues:
        progress(f"{conference} {year}：按论文集名称精确采集 {venue}")
        # Exact container-title filtering is essential: query.container-title is fuzzy.
        params = {"filter": f"prefix:{prefix},type:proceedings-article,{dates},container-title:{venue}",
                  "rows": 200,
                  "select": "DOI,title,author,container-title,published,type,publisher,resource,abstract,page,URL"}
        for records, total, fetched in crossref_pages(client, params, scope, cancel, progress):
            batch = []
            for record in records:
                # Recheck exact container match locally as well as conference/year.
                if venue not in [clean(v) for v in as_list(record.get("container-title"))]:
                    raise CrawlError("Crossref 返回了不属于所选论文集的记录，当前结果不完整。")
                paper = normalize(record, "crossref", conference, year, include_workshops)
                if paper:
                    batch.append(paper)
            on_page(batch, total_before + total, finished + fetched)
        finished += fetched
        total_before += total


def crawl_dblp_rdf(client, conference, year, include_workshops, cancel, on_page, progress, *, known_total=None, page_size=None):
    if conference == "SIGMOD" and year > max(dblp_rdf.PACMMOD):
        raise CrawlError(f"尚未核对 SIGMOD {year} 的 PACMMOD 卷期，请查看当届会议目录。")
    scope = dblp_rdf.edition(conference, year)
    def query(statement):
        result = client.get("dblp_rdf", {"query": statement}, f"dblp:{conference}:{year}", cancel, progress)
        return result["results"]["bindings"]
    progress(f"{conference} {year}：读取 DBLP 会议目录")
    try:
        if known_total is None:
            counts = query(dblp_rdf.PREFIX + f"SELECT (COUNT(DISTINCT ?p) AS ?n) WHERE {{ {scope} }}")
            total = int(counts[0]["n"]["value"])
        else:
            total = int(known_total)
        if total <= 0:
            raise CrawlError(f"DBLP 未找到 {conference} {year} 的会议论文，请检查举办年份和收录情况。")
        seen = set()
        kept = 0
        size = page_size or dblp_rdf.PAGE_SIZE
        for offset in range(0, total, size):
            progress(f"{conference} {year}：读取 DBLP 第 {offset + 1} 条起的论文")
            records = list(dblp_rdf.page_records(query(dblp_rdf.page_query(scope, offset, size))))
            if len(records) != min(total - offset, size):
                raise CrawlError("DBLP 分页记录数与目录不一致，已保留之前收到的论文。")
            batch = []
            for record in records:
                if record["key"] in seen:
                    raise CrawlError("DBLP 分页出现重复记录，本次目录不完整。")
                seen.add(record["key"])
                # Stream membership is checked by DBLP. Affiliated workshops may
                # have unrelated acronyms, so explicitly constrain main proceedings.
                venue = record["venue"]
                workshop = is_workshop(venue) or "@" in venue
                if not dblp_rdf.main_proceedings(venue, conference) and not (include_workshops and workshop):
                    continue
                published = int(record["year"])
                data = {**record, "year": year, "venue": conference}
                paper = normalize(data, "dblp", conference, year, include_workshops)
                if paper and (include_workshops or not workshop):
                    paper.update(venue=record["venue"], track="workshop" if workshop else "main",
                                 publication_year=published, source="dblp_rdf")
                    batch.append(paper)
            on_page(batch, total, len(seen))
            kept += len(batch)
        if not kept:
            raise CrawlError(f"DBLP 本届目录有 {total} 条记录，当前范围内未找到匹配的论文。")
    except (IndexError, KeyError, ValueError, TypeError):
        raise CrawlError("DBLP 结构化目录响应不完整，已保留之前收到的论文。") from None


def crawl_group(client, source, conference, year, include_workshops, api_key, cancel, on_page, progress):
    if source == "conference":
        if include_workshops:
            raise CrawlError("会议官网来源当前支持主会，Workshop 请使用 DBLP。")
        return official.crawl(client, conference, year, cancel, on_page, progress)
    if source == "openreview" or (source == "auto" and not include_workshops and openreview.venue_id(conference, year)):
        if include_workshops:
            raise CrawlError("OpenReview 当前采集主会已接收论文，包含 Workshop 请使用 DBLP。")
        progress(f"{conference} {year}：核对 OpenReview 会议")
        received = False
        def page(batch, total, fetched):
            nonlocal received
            received = received or bool(batch)
            on_page(batch, total, fetched)
        try:
            groups = openreview.find_venues(client, conference, year, cancel, progress)
            if groups:
                return openreview.crawl_venues(client, groups, conference, year, cancel, page, progress)
        except AccessError:
            if source != "auto" or received or conference not in official.VIRTUAL:
                raise
            progress(f"OpenReview 需要接口验证，正在读取 {conference} {year} 会议官网")
            return official.crawl(client, conference, year, cancel, on_page, progress)
        if source == "openreview":
            raise CrawlError("OpenReview 没有这届主会目录，请使用自动来源或 DBLP。")
        progress(f"{conference} {year}：OpenReview 无此届目录，读取会议目录来源")
    if source == "auto":
        source = "usenix" if conference == "ACM SIGOPS ATC" and year <= 2025 else CATALOG[conference]["provider"]
    elif source == "dblp" and CATALOG[conference]["provider"] == "dblp_rdf":
        source = "dblp_rdf"
    if source == "dblp_rdf":
        return crawl_dblp_rdf(client, conference, year, include_workshops, cancel, on_page, progress)
    if source == "usenix":
        first_year = {"NSDI":2004,"FAST":2002,"ACM SIGOPS ATC":1986}.get(conference)
        if first_year is None or include_workshops or year < first_year or (conference == "ACM SIGOPS ATC" and year > 2025):
            raise CrawlError("USENIX 来源支持 NSDI、FAST 和 2025 年及以前的 ATC 主会技术论文。")
        progress(f"{conference} {year}：读取 USENIX 官方技术论文与摘要")
        data = client.get("usenix", {"year": year, "conference":conference, "parser_version": 3}, f"usenix:{conference}:{year}", cancel, progress)
        total, records = validate_payload("usenix", data)
        batch = [p for r in records if (p := normalize(r, "usenix", conference, year))]
        on_page(batch, total, len(records))
        return
    if source == "crossref":
        return crawl_crossref(client, conference, year, include_workshops, cancel, on_page, progress)
    offset, total, seen = (0 if source == "dblp" else 1), None, set()
    while total is None or offset < total + (1 if source == "ieee" else 0):
        if cancel.is_set():
            raise Cancelled("已停止，已采集结果仍可导出。")
        if source == "dblp":
            # Stream query includes workshops, local venue/type checks enforce scope.
            params = {"q": f"stream:streams/conf/{CONFERENCES[conference]}: year:{year}:",
                      "format": "json", "h": 1000, "f": offset, "c": 0}
        else:
            if not api_key:
                raise CrawlError("IEEE 模式需要填写官方 Metadata API Key。")
            publication_query = "International Conference on Data Engineering" if conference == "ICDE" else conference
            params = {"apikey": api_key, "format": "json", "publication_title": publication_query,
                      "content_type": "Conferences", "start_year": year, "end_year": year,
                      "max_records": 200, "start_record": offset,
                      "sort_field": "article_number", "sort_order": "asc"}
        progress(f"{conference} {year}：请求第 {offset if source == 'ieee' else offset + 1} 条起的记录")
        data = client.get(source, params, f"{source}:{conference}:{year}", cancel, progress)
        total, records = validate_payload(source, data)
        if not records and total > (offset if source == "dblp" else offset - 1):
            raise CrawlError("来源提前返回空页，结果不完整；已保留前面页的数据。")
        batch = []
        added = 0
        for record in records:
            info = record["info"] if source == "dblp" else record
            fingerprint = clean(info.get("key")) or clean(info.get("article_number")) or json.dumps(info, sort_keys=True)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            added += 1
            paper = normalize(info, source, conference, year, include_workshops)
            if paper:
                batch.append(paper)
        if records and added == 0:
            raise CrawlError("来源重复返回同一页，已停止分页；当前结果不完整。")
        offset += len(records)  # Respect actual page length, even if server caps h.
        on_page(batch, total, min(total, offset if source == "dblp" else offset - 1))
        if not records:
            break


EXPORT_FIELDS = ["conference", "year", "publication_year", "track", "title", "authors", "doi", "venue", "pages",
                 "title_text", "categories", "category_sources",
                 "abstract", "abstract_source", "abstract_url", "abstract_status", "authors_text", "keywords", "keyword_source", "url", "publisher_url", "pdf_url", "dblp_url", "source",
                 "source_record_id", "access", "fetched_at", "ai_summary"]


def export_papers(papers, kind):
    papers = [p.copy() for p in papers]
    for paper in papers:
        paper.pop("title_zh", None)
        prepare_title(paper)
    if kind == "json":
        return json.dumps(papers, ensure_ascii=False, indent=2).encode("utf-8"), "application/json"
    if kind == "csv":
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=EXPORT_FIELDS)
        writer.writeheader()
        for paper in papers:
            row = {}
            for field in EXPORT_FIELDS:
                value = paper.get(field, "")
                if isinstance(value, dict):
                    value = json.dumps(value, ensure_ascii=False)
                value = "; ".join(map(str, value)) if isinstance(value, list) else str(value)
                # Spreadsheet formula injection applies to remote titles/authors too.
                row[field] = "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
            writer.writerow(row)
        return stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8"
    if kind != "md":
        raise ValueError("Unsupported export")
    def esc(value):
        return re.sub(r"([\\`*_[\]<>#|])", r"\\\1", str(value))
    parts = [f"# 会议论文\n\n共 {len(papers)} 篇。关键词与摘要来源随每篇列出；链接不代表全文访问权限。"]
    for p in papers:
        distinctions = "".join(f"- {esc(label)}：{esc(url)}\n" for label, url in p.get("category_sources", {}).items())
        parts.append(f"## {esc(p['title_text'])}\n\n{distinctions}"
                     f"- 会议：{esc(p['conference'])} {p['year']} / {esc(p['track'])}\n"
                     f"- 作者：{esc('; '.join(p['authors']))}\n"
                     f"- DOI：{esc(p['doi'])}\n"
                     f"- 关键词：{esc('; '.join(p['keywords']))}（{esc(p['keyword_source'])}）\n"
                     f"- 来源：{esc(p['source'])}\n"
                     f"- 出版社页面：{esc(p.get('publisher_url', ''))}\n"
                     f"- 摘要来源：{esc(p.get('abstract_source', ''))} {esc(p.get('abstract_url', ''))}\n"
                     f"- 论文：{esc(p['url'])}\n- PDF：{esc(p['pdf_url'])}\n\n"
                     f"{esc(p['abstract']) if p['abstract'] else '摘要：来源未提供。'}")
        if p.get("ai_summary"):
            parts.append("### AI 摘要分析\n\n" + esc(p["ai_summary"]))
    return ("\n\n".join(parts) + "\n").encode("utf-8"), "text/markdown; charset=utf-8"
