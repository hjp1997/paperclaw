"""Read public accepted submissions and their metadata from OpenReview."""
import hashlib
import re
from collections import Counter
from urllib.parse import quote

PREFIXES = {"ICLR":"ICLR.cc", "ICML":"ICML.cc", "NeurIPS":"NeurIPS.cc",
            "ACL":"aclweb.org/ACL", "AAAI":"AAAI.org", "CVPR":"thecvf.com/CVPR",
            "ICCV":"thecvf.com/ICCV", "SIGIR":"ACM.org/SIGIR", "SIGKDD":"KDD.org"}
PAGE_SIZE = 500


def venue_id(conference, year):
    prefix = PREFIXES.get(conference)
    return f"{prefix}/{year}/Conference" if prefix else ""


def value(content, field, default=""):
    item = content.get(field, default)
    return item.get("value", default) if isinstance(item, dict) else item


def find_venues(client, conference, year, cancel, progress):
    prefix = PREFIXES.get(conference)
    if not prefix:
        return []
    prefix = f"{prefix}/{year}/"
    data = client.get("openreview_groups", {"prefix":prefix, "web":"true", "limit":1000},
                      f"openreview:{prefix}", cancel, progress)
    if int(data.get("count", len(data["groups"]))) != len(data["groups"]):
        from .core import CrawlError
        raise CrawlError("OpenReview 会议目录不完整，未开始论文查询。")
    pattern = (r"Conference|Short_Papers(?:_Track)?|Findings|Industry(?:_Track)?|"
               r"SRW|Demo(?:_Track)?|Research_Track(?:_[A-Za-z0-9_]+)?|ADS_Track(?:_[A-Za-z0-9_]+)?|"
               r"Datasets_and_Benchmarks?_Track(?:_[A-Za-z0-9_]+)?|"
               r"AI4Sciences_Track(?:_[A-Za-z0-9_]+)?|Blue_Sky_Ideas_Track|Position_Paper_Track")
    return sorted((g for g in data["groups"] if g["id"].startswith(prefix) and
                   re.fullmatch(pattern, g["id"][len(prefix):])), key=lambda g:g["id"])


def official_decision(note, venue):
    decisions = []
    for reply in note.get("details", {}).get("directReplies", []):
        invitations = reply.get("invitations") or [reply.get("invitation", "")]
        if reply.get("forum") == note.get("forum", note["id"]) and any(
                invitation.startswith(venue + "/") and invitation.endswith("/Decision") for invitation in invitations):
            decisions.append(reply)
    if not decisions:
        return ""
    latest = max(decisions, key=lambda reply:reply.get("tmdate") or reply.get("mdate") or reply.get("cdate") or 0)
    return str(value(latest.get("content", {}), "decision"))


def public_decision(note, venue, version):
    if version == 2:
        return value(note.get("content", {}), "venueid") == venue
    return bool(re.match(r"^Accept\b", official_decision(note, venue), re.I))


def normalize_note(note, conference, year, venue, version):
    from .core import clean, keywords, safe_url
    content = note.get("content", {})
    original = note.get("details", {}).get("original")
    # API 1 blind submissions may expose authors only in their public original.
    if isinstance(original, dict):
        content = {**original.get("content", {}), **content}
        if value(original.get("content", {}), "authors"):
            content["authors"] = original.get("content", {}).get("authors", [])
    title = clean(value(content, "title"))
    if not title or not public_decision(note, venue, version) or note.get("ddate") or note.get("replyto"):
        return None
    forum = note.get("forum") or note["id"]
    if not re.fullmatch(r"[\w-]+", forum):
        raise ValueError("Invalid OpenReview forum")
    url = "https://openreview.net/forum?id=" + quote(forum)
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", clean(value(content, "doi")), flags=re.I)
    abstract = clean(value(content, "abstract"))
    authors = value(content, "authors", [])
    terms = value(content, "keywords", [])
    if isinstance(terms, str):
        terms = [word.strip() for word in terms.split(",") if word.strip()]
    if not isinstance(authors, list) or not isinstance(terms, list):
        raise ValueError("Invalid OpenReview authors or keywords")
    pdf = value(content, "pdf")
    pdf = "https://openreview.net/pdf?id=" + quote(note["id"]) if pdf else ""
    description = " ".join(str(value(content, field)) for field in ("venue", "decision", "presentation_type", "award"))
    if version == 1:
        description += " " + official_decision(note, venue)
    if re.search(r"\bworkshop\b", description, re.I):
        return None
    labels = [label for label in ("Oral", "Spotlight", "Highlight", "Poster")
              if re.search(r"\b" + label + r"\b", description, re.I)]
    award = clean(value(content, "award"))
    if award and len(award) <= 120 and re.search(r"\bbest\b|\boutstanding\b|\bdistinguished\b", award, re.I):
        labels.append(award)
    return {"id": hashlib.sha256((doi.lower() or "openreview:" + forum).encode()).hexdigest()[:24],
            "conference": conference, "year": year, "title": title,
            "authors": [clean(a) for a in authors if clean(a)], "authors_text": "", "doi": doi,
            "venue": clean(value(content, "venue")) or f"{conference} {year}", "track":"main",
            "pages":"", "abstract":abstract, "abstract_source":"openreview" if abstract else "",
            "abstract_url":url if abstract else "", "abstract_status":"available" if abstract else "not_checked",
            "keywords": [clean(t) for t in terms if clean(t)] or keywords(title, abstract),
            "keyword_source":"author_terms" if terms else ("local_title_abstract" if abstract else "local_title"),
            "url":url, "publisher_url":url, "pdf_url":safe_url(pdf), "dblp_url":"", "source":"openreview",
            "source_record_id":forum, "openreview_venue":venue, "access":"public", "ai_summary":"",
            "category_sources":dict.fromkeys(labels, url), "categories":labels}


def crawl(client, group, conference, year, cancel, on_page, progress, *, allow_empty=False):
    from .core import Cancelled, CrawlError
    from datetime import datetime, timezone
    venue = group["id"]
    version = 2 if value(group.get("content", {}), "submission_name") else 1
    params = {"content.venueid":venue} if version == 2 else {
        "invitation":venue + "/-/Blind_Submission", "details":"directReplies,original"}
    source = "openreview" if version == 2 else "openreview_v1"
    seen, offset, total, accepted = set(), 0, None, 0
    while total is None or offset < total:
        if cancel.is_set():
            raise Cancelled("OpenReview 采集已停止，已有论文可导出。")
        progress(f"{conference} {year}：读取 OpenReview 第 {offset + 1} 条起的记录")
        data = client.get(source, {**params, "limit":PAGE_SIZE, "offset":offset, "sort":"id:asc"},
                          f"openreview:{venue}", cancel, progress)
        count, notes = int(data["count"]), data["notes"]
        if total is not None and count != total:
            raise CrawlError("OpenReview 收录数量在分页期间发生变化，请重新采集；已有论文已保存。")
        total = count
        if not notes and offset < total:
            raise CrawlError("OpenReview 提前返回空页，已有结果已保存。")
        batch = []
        for note in notes:
            if note["id"] in seen:
                raise CrawlError("OpenReview 分页返回重复论文，已有结果已保存。")
            seen.add(note["id"])
            try:
                paper = normalize_note(note, conference, year, venue, version)
            except (TypeError, ValueError, KeyError):
                raise CrawlError("OpenReview 论文记录格式变化，已保留前面收到的论文。") from None
            if paper:
                paper["fetched_at"] = datetime.now(timezone.utc).isoformat()
                batch.append(paper)
        offset += len(notes)
        if offset > total:
            raise CrawlError("OpenReview 返回数量与来源总数不一致，已有结果已保存。")
        accepted += len(batch)
        on_page(batch, total, offset)
    if not accepted and not allow_empty:
        raise CrawlError(f"{conference} {year} 的 OpenReview 尚未返回可公开读取的已接收论文。可打开会议页面核对，或手动选择 DBLP。")
    return accepted


def crawl_venues(client, groups, conference, year, cancel, on_page, progress):
    from .core import CrawlError
    completed, accepted = 0, 0
    for group in groups:
        track_total = 0
        def page(batch, total, fetched):
            nonlocal track_total
            track_total = total
            on_page(batch, completed + total, completed + fetched)
        accepted += crawl(client, group, conference, year, cancel, page, progress, allow_empty=True)
        completed += track_total
    if not accepted:
        raise CrawlError(f"{conference} {year} 的 OpenReview 尚未返回可公开读取的已接收论文。可打开会议页面核对，或手动选择 DBLP。")


def enrich(client, papers, cancel, on_batch, progress):
    """Only join exact, unambiguous paper titles within one conference edition."""
    from .program import title_key
    editions = {(p["conference"], p["year"]) for p in papers if not p.get("abstract") and venue_id(p["conference"], p["year"])}
    for conference, year in sorted(editions):
        groups = find_venues(client, conference, year, cancel, progress)
        if not groups:
            continue
        by_title = {}
        all_rows = []
        crawl_venues(client, groups, conference, year, cancel, lambda batch,*_:all_rows.extend(batch), progress)
        all_rows = list({paper["id"]:paper for paper in all_rows}.values())
        counts = Counter(title_key(p["title"]) for p in all_rows)
        for paper in all_rows:
            key = title_key(paper["title"])
            if counts[key] == 1:
                by_title[key] = paper
        targets = [p for p in papers if (p["conference"],p["year"]) == (conference,year) and not p.get("abstract")]
        target_counts = Counter(title_key(p.get("title_text") or p["title"]) for p in targets)
        updates = []
        for paper in targets:
            key = title_key(paper.get("title_text") or paper["title"])
            match = by_title.get(key)
            doi_matches = not paper.get("doi") or not match or not match.get("doi") or paper["doi"].lower() == match["doi"].lower()
            if target_counts[key] == 1 and match and match["abstract"] and doi_matches:
                update = dict(paper)
                update.update({field:match[field] for field in ("abstract","abstract_source","abstract_url","abstract_status","keywords","keyword_source")})
                updates.append(update)
                paper.update(update)
        if updates:
            on_batch(updates)
