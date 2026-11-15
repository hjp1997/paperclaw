"""Per-paper distinctions from conference programs. Never infer awards from paper prose."""
import json
import csv
import io
import re
import unicodedata
from collections import Counter, defaultdict
from urllib.parse import urljoin
from xml.etree import ElementTree
from .catalog import Document, plain, parse_usenix
from .titles import prepare_title

VIRTUAL = {"NeurIPS":"neurips.cc", "ICML":"icml.cc", "ICLR":"iclr.cc",
           "CVPR":"cvpr.thecvf.com", "ICCV":"iccv.thecvf.com"}
ACL_SCHEDULE = "https://docs.google.com/spreadsheets/d/16ZuzEVhY1BAeZGK9LJskLcvqacy-7YuoAaK1yXoRcFQ"
AWARD_INDEX = "https://jeffhuang.com/best_paper_awards/"
INDEX_CONFERENCES = {"AAAI","ACL","CVPR","FOCS","ICCV","ICML","INFOCOM","SIGKDD","MobiCom","NSDI",
                     "NeurIPS","SIGCOMM","SIGIR","SIGMOD","SODA","STOC","VLDB","WWW"}
INDEX_LABEL = "获奖论文（Best Paper Awards 汇编）"
ICDE_2025_AWARDS = "https://ieee-icde.org/2025/2025/05/13/awards/"
# DOI matches for published award notices whose websites restrict automated access.
PUBLISHED_AWARDS = {
    "10.1109/infocom55648.2025.11044678": ("Best Paper Award", "https://networks.imdea.org/imdea-networks-work-on-distributed-inference-in-mobile-networks-wins-best-paper-award-at-ieee-infocom-2025/"),
    "10.1109/infocom55648.2025.11044744": ("Best Paper Award", "https://mime.dei.unipd.it/mime-professors-receive-the-best-paper-award-at-ieee-infocom-2025/"),
    **{"10.1109/icde65448.2025." + suffix: (label, ICDE_2025_AWARDS) for suffix, label in (
        ("00035", "Best Paper Award"), ("00191", "Best Paper Runner Up Award"), ("00167", "Best Student Paper Award"),
        ("00325", "Best Industry and Application Paper Award"), ("00312", "Best Industry and Application Paper Runner Up"),
        ("00352", "Best Demonstration Award"), ("00348", "Best Demonstration Award"))}
}


def program_url(conference, year, part="program"):
    if part == "schedule":
        if conference == "CVPR" and year == 2025:
            return f"https://cvpr.thecvf.com/Conferences/{year}/AcceptedPapers"
        return ACL_SCHEDULE + "/gviz/tq?tqx=out:csv" if (conference, year) == ("ACL", 2026) else ""
    if (conference, year) == ("RTSS", 2025):
        return "https://2025.rtss.org/index.html?p=420"
    if (conference, year) == ("CAV", 2025):
        return "https://conferences.i-cav.org/2025/program/"
    if conference == "LICS" and year >= 1986:
        return f"https://lics.siglog.org/archive/{year}/index.html"
    if (conference, year) == ("ICDE", 2026):
        return "https://icde2026.github.io/awards.html"
    if conference == "MobiCom" and year in (2025, 2026):
        return f"https://www.sigmobile.org/mobicom/{year}/awards.html"
    if conference == "SIGCOMM" and year == 2025:
        return "https://conferences.sigcomm.org/sigcomm/2025/program/papers-info/"
    if conference == "NSDI" and year >= 2004:
        return f"https://www.usenix.org/conference/nsdi{year % 100:02d}/technical-sessions"
    if conference == "ACL" and year >= 2020:
        return f"https://raw.githubusercontent.com/acl-org/acl-anthology/master/data/xml/{year}.acl.xml"
    if conference in VIRTUAL:
        return f"https://{VIRTUAL[conference]}/static/virtual/data/{conference.lower()}-{year}-orals-posters.json"
    return ""


def has_classifications(conference, year):
    return bool(program_url(conference, year) or (conference in INDEX_CONFERENCES and year >= 1996))


def parse_award_index(raw):
    by_edition = {}
    aliases = {"MOBICOM":"MobiCom", "KDD":"SIGKDD", "NIPS":"NeurIPS"}
    for table in Document(raw.decode()).root.find("table"):
        year = table.attrs.get("id", "")
        if not re.fullmatch(r"[0-9]{4}", year):
            continue
        name = ""
        for row in table.find("tr"):
            headings = row.find("th", cls="category-name")
            if headings:
                anchors = headings[0].find("a")
                value = plain(anchors[0]) if anchors else ""
                name = aliases.get(value, value)
            if name not in INDEX_CONFERENCES:
                continue
            cells = row.find("td")
            links = cells[0].find("a") if cells else []
            if links:
                by_edition.setdefault(f"{name}:{year}", []).append(plain(links[0]))
    if not by_edition:
        raise ValueError("Unknown award index layout")
    return {"editions": by_edition}


def parse_program(raw, conference, year, part="program"):
    if part == "schedule" and conference == "CVPR":
        rows = []
        saw_papers = False
        for row in Document(raw.decode()).root.find("tr"):
            cells = row.find("td")
            titles = cells[0].find("strong") if cells else []
            if not titles:
                continue
            saw_papers = True
            badges = [img.attrs.get("title", "") for img in cells[0].find("img")]
            labels = [label for label in badges if re.fullmatch(r"Highlight|Oral|Best (?:Student )?Paper(?: Award)?", label, re.I)]
            if labels:
                rows.append({"title": plain(titles[0]), "labels": labels, "url": program_url(conference, year, part)})
        if not saw_papers:
            raise ValueError("Unknown CVPR accepted papers layout")
    elif part == "schedule":
        rows = acl_schedule(raw)
    elif conference == "ACL":
        try:
            rows = acl_program(raw, year)
        except ElementTree.ParseError as error:
            raise ValueError("Invalid ACL collection") from error
    elif conference == "NSDI":
        rows = [{"title": p["title"], "labels": p["program_labels"], "url": p["key"]}
                for p in parse_usenix(raw.decode(), year)["articles"] if p["program_labels"]]
    elif conference in ("RTSS", "CAV", "LICS"):
        root = Document(raw.decode()).root
        url = program_url(conference, year)
        rows = []
        if conference == "CAV":
            for node in root.find(cls="title"):
                match = re.match(r"^\((Distinguished Paper|Best Paper)\)\s*(.+)$", plain(node), re.I)
                if match:
                    rows.append({"title": match[2], "labels": [match[1]], "url": url})
        elif conference == "RTSS":
            for node in root.find("li"):
                labels = node.find("strong")
                match = re.search(r'paper\s+[“"](.+?)[”"]', plain(node))
                if match and labels and plain(labels[0]).startswith("Best "):
                    rows.append({"title": match[1], "labels": [plain(labels[0])], "url": url})
        else:
            for node in root.find("li"):
                terms = node.find("b")
                if len(terms) < 2:
                    continue
                labels = [plain(term)[1:-1] for term in terms[1:] if re.fullmatch(r"\[(Distinguished Paper|Kleene Award|Best Paper)\]", plain(term))]
                links = node.find("a", cls="infolink")
                if labels:
                    rows.append({"title": plain(terms[0]), "labels": labels,
                                 "url": urljoin(url, links[0].attrs["href"]) if links else url})
        if not rows and (conference != "LICS" or not root.find("a", cls="infolink")):
            raise ValueError("Unknown program layout")
    elif conference in ("MobiCom", "ICDE"):
        rows = []
        award = ""
        for node in Document(raw.decode()).root.find():
            if conference == "MobiCom":
                if node.tag == "h2":
                    text = plain(node)
                    award = text if text.startswith("Best ") and "Reviewer" not in text else ""
                elif award and node.tag == ("b" if year == 2025 else "h3"):
                    rows.append({"title": plain(node), "labels": [award], "url": program_url(conference, year)})
            elif node.tag in ("h2", "h3", "h4"):
                award = plain(node) if node.tag == "h4" and "Best" in plain(node) else ""
            elif award and node.tag == "li":
                strong = node.find("strong") or node.find("b")
                if strong:
                    rows.append({"title": plain(strong[0]), "labels": [award], "url": program_url(conference, year)})
        if not rows:
            raise ValueError("Unknown awards layout")
    elif conference == "SIGCOMM":
        root = Document(raw.decode()).root
        rows = []
        award = ""
        for node in root.find():
            if node.tag in ("h2", "h3"):
                match = re.search(r"\b(Best [^|]+?Award(?: \(Honorable Mention\))?)$", plain(node), re.I)
                award = match[1] if match else ""
            elif award and node.tag == "p" and "Technical Session:" in plain(node):
                rows.append({"title": plain(node).split("|")[0].strip(), "labels": [award],
                             "url": program_url(conference, year)})
                award = ""
        if not rows:
            raise ValueError("Unknown SIGCOMM program layout")
    else:
        rows = virtual_program(raw, conference, year)
    return {"annotations": rows}


def title_key(title):
    if title and ("\\" in title or "$" in title):
        parsed = {"title": title}
        prepare_title(parsed)
        title = parsed["title_text"]
    normalized = unicodedata.normalize("NFKC", title or "").casefold()
    return re.sub(r"[\W_]+", "", normalized)


def acl_schedule(raw):
    reader = csv.reader(io.StringIO(raw.decode("utf-8-sig")))
    header = next((r for r in reader if "Title" in r and "Underline/Whova Session Name" in r), None)
    if header is None:
        raise ValueError("Unknown ACL schedule")
    title, session = header.index("Title"), header.index("Underline/Whova Session Name")
    abstract = header.index("Abstract") if "Abstract" in header else None
    rows = []
    for row in reader:
        if len(row) <= max(title, session) or not row[title].strip():
            continue
        match = re.match(r"^(Orals?|Poster|Demo) Session\b", row[session].strip(), re.I)
        labels = [match[1].capitalize().removesuffix("s")] if match else []
        summary = row[abstract].strip() if abstract is not None and abstract < len(row) else ""
        if summary or labels:
            rows.append({"title": row[title], "labels": labels, "abstract": summary, "url": ACL_SCHEDULE + "/edit"})
    return rows


def anthology_text(node):
    if node is None:
        return ""
    content = node.text or ""
    for child in node:
        value = anthology_text(child)
        if child.tag == "tex-math":
            value = "$" + value + "$"
        elif child.tag in ("sup", "sub"):
            value = ("^" if child.tag == "sup" else "_") + "{" + value + "}"
        content += value + (child.tail or "")
    return content.strip() if node.tag in ("title", "abstract") else content


def acl_program(raw, year):
    root = ElementTree.fromstring(raw)
    if root.tag != "collection" or not root.findall("volume"):
        raise ValueError("Unknown ACL collection")
    rows = []
    for vol in root.findall("volume"):
        for paper in vol.findall("paper"):
            labels = [e.get("name") for e in paper.findall("award") if e.get("name")]
            key = f"{year}.acl-{vol.attrib['id']}.{paper.attrib['id']}"
            rows.append({"doi":paper.findtext("doi") or "10.18653/v1/"+key,
                         "title":anthology_text(paper.find("title")), "abstract":anthology_text(paper.find("abstract")),
                         "labels":labels, "url":"https://aclanthology.org/"+key+"/"})
    return rows


def virtual_program(raw, conference, year):
    data = json.loads(raw)
    items = data["results"]
    if not isinstance(items, list) or data.get("next") or int(data.get("count", len(items))) != len(items):
        raise ValueError("Incomplete program")
    rows = []
    for item in items:
        fields = " ".join(str(item.get(k) or "") for k in ("eventtype", "event_type", "decision", "session"))
        labels = [label for label in ("Oral","Spotlight","Highlight","Poster")
                  if re.search(r"\b" + label + r"s?\b", fields, re.I)]
        if labels and item.get("name"):
            rows.append({"title":item["name"], "labels":labels,
                         "abstract":item.get("abstract") or "", "abstract_source":"conference_site",
                         "authors":[author.get("fullname", "") for author in item.get("authors", []) if isinstance(author, dict)],
                         "keywords":item.get("keywords") or [], "pdf_url":item.get("paper_pdf_url") or "",
                         "url":urljoin("https://"+VIRTUAL[conference], item.get("virtualsite_url") or program_url(conference,year))})
    return rows


def add_labels(paper, labels, url):
    if not url or not labels:
        return
    sources = paper.setdefault("category_sources", {})
    for label in labels:
        sources[label] = url
    paper["categories"] = sorted(sources)


def apply_program(papers, rows):
    by_doi, by_title = defaultdict(list), defaultdict(list)
    for row in rows:
        if row.get("doi"):
            by_doi[row["doi"].casefold()].append(row)
        elif row.get("title"):
            by_title[title_key(row["title"])].append(row)
    counts = Counter(title_key(p.get("title_text") or p["title"]) for p in papers)
    for paper in papers:
        key = title_key(paper.get("title_text") or paper["title"])
        matches = by_doi.get(paper.get("doi", "").casefold(), [])
        if counts[key] == 1:
            matches = [*matches, *by_title.get(key, [])]
        for row in matches:
            if row.get("indexed") and any(re.search("best|award|outstanding|distinguished", label, re.I)
                                          for label in paper.get("categories", [])):
                continue
            add_labels(paper,row["labels"],row["url"])
            if row.get("doi") and row.get("title") and "$" in row["title"]:
                paper["title_official"] = row["title"]
                prepare_title(paper)
            if row.get("abstract") and (not paper.get("abstract") or paper.get("abstract_source") == "openalex"):
                paper.update(abstract=row["abstract"], abstract_url=row["url"], abstract_status="available",
                             abstract_source=row.get("abstract_source") or ("acl_anthology" if row.get("doi") else "acl_program"))


def venue_labels(paper):
    # Only use explicit session names in the venue field (not the paper title).
    venue = paper.get("venue", "")
    award = PUBLISHED_AWARDS.get(paper.get("doi", "").lower())
    if award:
        add_labels(paper, [award[0]], award[1])
    if paper.get("category_sources"):
        paper["categories"] = sorted(paper["category_sources"])
    for label in ("Oral", "Highlight", "Spotlight", "Poster", "Demo", "Findings"):
        if re.search(r"\b" + label + r"s?\b", venue, re.I):
            # A joint Posters and Demos volume does not identify individual categories.
            if label in ("Poster", "Demo") and re.search(r"posters?\s*(?:and|&|/)\s*demos?", venue, re.I):
                continue
            add_labels(paper,[label],paper.get("dblp_url") or paper.get("url"))
