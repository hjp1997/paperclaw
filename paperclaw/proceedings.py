"""Official accepted-paper lists for editions not yet indexed by DBLP."""
import hashlib
import re
from urllib.parse import urljoin

from .catalog import Document, plain

URLS = {
    ("ACM SIGOPS ATC",2026): "https://atc.sigops.org/2026/accepted.html",
    ("MICRO",2026): "https://www.microarch.org/micro59/program/",
    ("MobiCom",2026): "https://www.sigmobile.org/mobicom/2026/accepted.html",
    ("FOCS",2026): "https://focs.computer.org/2026/accepted-papers/",
    ("RTSS",2026): "https://2026.rtss.org/program/program/",
    ("SC",2026): "https://sc26.conference-program.com/",
}


def url(conference,year,page=""):
    base = URLS[(conference,year)]
    if not page: return base
    if conference != "SC" or year != 2026 or not re.fullmatch(r"/wp-content/linklings_snippets/wp_program_view_all_2026-11-(?:1[4-9]|2[01])\.txt(?:\?v=\d+)?",page):
        raise ValueError("Unexpected official program page address")
    return urljoin(base,page)


def author_names(text):
    text = re.sub(r"^Authors?:\s*", "", text, flags=re.I)
    names, depth = [], 0
    for char in text:
        if char == "(": depth += 1
        elif char == ")": depth = max(0,depth-1)
        elif not depth: names.append(char)
    return [name.strip() for name in re.split(r",|;|\band\b", "".join(names)) if name.strip()]


def parse(raw, conference, year, page=""):
    source_url = URLS[(conference,year)]
    root = Document(raw.decode("utf-8")).root
    if conference == "SC":
        if not page:
            pages = list(dict.fromkeys(n.attrs["source"] for n in root.find() if "source" in n.attrs))
            if not pages: raise ValueError("SC program day index not found")
            for path in pages: url(conference,year,path)
            return {"total_records":0,"articles":[],"pages":pages}
        articles = {}
        for item in root.find("tr"):
            identity = item.attrs.get("ssid", "")
            if not re.fullmatch(r"pap\d+",identity): continue
            titles = [a for a in item.find("a") if a.attrs.get("data-link-type", "").endswith(".presentation")]
            if not titles: continue
            landing = urljoin(source_url,titles[0].attrs["href"])
            articles[identity] = {"title":plain(titles[0]),"publication_title":conference,"publication_year":year,
                "authors":{"authors":[{"full_name":plain(n)} for n in item.find(cls="presenter-name")]},
                "key":source_url+"?id="+identity,"landing_url":landing,"program_labels":[]}
        return {"total_records":len(articles),"articles":list(articles.values())}
    entries = []
    if conference == "ACM SIGOPS ATC":
        for section in root.find("ul",cls="paperlist"):
            for item in section.find("li"):
                titles, people = item.find("strong"), item.find("em")
                if titles and people: entries.append((plain(titles[0]),plain(people[0]),[]))
        expected = re.search(r"following\s+(\d+)\s+papers",plain(root),re.I)
        if expected and len(entries) != int(expected[1]): raise ValueError("Incomplete ATC accepted-paper list")
    elif conference == "MobiCom":
        for section in root.find(cls="acceptedpapers"):
            for item in section.find("li"):
                titles, people = item.find("b") or item.find("strong"), item.find(cls="pauthors")
                if titles and people: entries.append((plain(titles[0]),plain(people[0]),[]))
    elif conference == "MICRO":
        for panel in root.find(cls="panel"):
            headings = panel.find("h4")
            heading = plain(headings[0]) if headings else ""
            if not re.match(r"Session\s+[1-9]",heading) or re.search(r"test.of.time|keynote",heading,re.I): continue
            labels = ["Distinguished Paper"] if "Distinguished Papers" in heading else []
            for item in panel.find(cls="paper"):
                titles, people = item.find(cls="paper-title"), item.find(cls="paper-authors")
                if titles and people: entries.append((plain(titles[0]),plain(people[0]),labels))
    elif conference == "FOCS":
        for item in root.find("tr"):
            titles, people = item.find(cls="paper-title"), item.find(cls="paper-authors")
            if titles and people: entries.append((plain(titles[0]),plain(people[0]),[]))
    elif conference == "RTSS":
        for item in root.find("li",cls="mb-2"):
            people = item.find("small",cls="fw-light")
            if not people: continue
            title = re.sub(r"\s+", " ",item.text(skip=("small","span"))).strip(" ⭐🏅")
            labels = [plain(tag) for tag in item.find("small") if plain(tag) == "Outstanding Paper"]
            if title: entries.append((title,plain(people[0]),labels))
    articles = {}
    for title, people, labels in entries:
        identity = hashlib.sha256(title.encode()).hexdigest()[:24]
        articles[identity] = {"title":title,"publication_title":conference,"publication_year":year,
                              "authors":{"authors":[{"full_name":name} for name in author_names(people)]},
                              "key":source_url+"#paper-"+identity,"landing_url":source_url,"program_labels":labels}
    if not articles: raise ValueError("Official accepted-paper list is empty or its structure changed")
    return {"total_records":len(articles),"articles":list(articles.values())}
