"""Conference registry and parsers for public directory/USENIX HTML."""
import re
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

CATALOG = {r["name"]: r for r in json.loads(Path(__file__).with_name("conferences.json").read_text("utf-8"))}
CATEGORIES = tuple(dict.fromkeys(r["category"] for r in CATALOG.values()))
CROSSREF_VENUES = {"INFOCOM", "ICDE", "SIGCOMM", "MobiCom"}
IEEE_VENUES = {"INFOCOM", "ICDE", "FOCS", "RTSS", "CVPR", "ICCV"}
for names, publisher, portal, prefix in [
    (IEEE_VENUES, "IEEE Xplore", "https://ieeexplore.ieee.org/", "10.1109"),
    ({"SIGCOMM","MobiCom","WWW","STOC","LICS","SIGMOD","SIGKDD","SIGIR"}, "ACM Digital Library", "https://dl.acm.org/", "10.1145"),
    ({"NSDI"}, "USENIX", "https://www.usenix.org/", ""),
    ({"SODA"}, "SIAM", "https://epubs.siam.org/", "10.1137"),
    ({"CAV"}, "Springer", "https://link.springer.com/", "10.1007"),
    ({"AAAI"}, "AAAI", "https://ojs.aaai.org/", "10.1609"),
    ({"NeurIPS"}, "NeurIPS", "https://proceedings.neurips.cc/", ""),
    ({"ACL"}, "ACL Anthology", "https://aclanthology.org/", "10.18653"),
    ({"ICML"}, "PMLR", "https://proceedings.mlr.press/", ""),
    ({"ICLR"}, "OpenReview", "https://openreview.net/", ""),
    ({"VLDB"}, "VLDB Endowment", "https://www.vldb.org/", ""),
]:
    for name in names:
        entry = CATALOG[name]
        provider = "crossref" if name in CROSSREF_VENUES else "dblp_rdf"
        entry.update(provider="usenix" if name == "NSDI" else provider, publisher=publisher,
                     publisher_site=portal, prefix=prefix, label=entry["category"] + " · CCF A")


class Node:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def find(self, tag=None, cls=None):
        found = []
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and (cls is None or cls in child.attrs.get("class", "").split()):
                    found.append(child)
                found.extend(child.find(tag, cls))
        return found

    def text(self, skip=()):
        if self.tag in skip or self.tag in ("script", "style"):
            return ""
        content = "".join(c.text(skip) if isinstance(c, Node) else c for c in self.children)
        if self.tag in ("sup", "sub"):
            return ("^(" if self.tag == "sup" else "_(") + content + ")"
        return content + (" " if self.tag in ("p", "div", "br", "li") else "")


class Document(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def plain(node):
    return re.sub(r"\s+", " ", node.text()).strip()


def parse_catalog(text):
    entries = []
    for row in Document(text).root.find("tr"):
        cells = row.find("td")
        if len(cells) < 5:
            continue
        name, title, rank, kind, category = map(plain, cells[:5])
        if rank != "A" or kind != "会议" or category not in CATEGORIES:
            continue
        links = cells[1].find("a")
        url = urljoin("https://ccf.atom.im/", links[0].attrs.get("href", "")) if links else ""
        parsed = urlsplit(url)
        if parsed.hostname not in ("dblp.org", "dblp.uni-trier.de") or not parsed.path.startswith("/db/conf/"):
            continue
        entries.append({"name": name, "title": title, "rank": rank, "category": category,
                        "dblp_url": "https://dblp.org" + parsed.path, "supported": name in CATALOG})
    if not entries:
        raise ValueError("Conference directory structure changed or verification page")
    return {"entries": entries, "source_url": "https://ccf.atom.im/"}


def parse_usenix(text, year):
    base = f"https://www.usenix.org/conference/nsdi{year % 100:02d}/"
    records, seen = [], set()
    for article in Document(text).root.find(cls="node-paper"):
        headings = article.find("h2") or article.find("h3")
        links = headings[0].find("a") if headings else []
        if not links:
            continue
        url = urljoin(base, links[0].attrs.get("href", ""))
        if not url.startswith(base + "presentation/") or url in seen:
            continue
        title = plain(links[0])
        if not title:
            continue
        seen.add(url)
        descriptions = article.find(cls="field-name-field-paper-description-long")
        people = article.find(cls="field-name-field-paper-people-text")
        awards = sorted({plain(node).rstrip("!.") for field in people for node in field.find("strong")
                         if re.fullmatch(r"(?:Awarded .+ Paper|.{1,65} Award(?: Winner)?)[!.]?", plain(node), re.I)})
        authors_text = plain(people[0]) if people else ""
        names = people[0].text(skip=("em", "i")) if people else ""
        authors = [re.sub(r"\s+", " ", name).strip(" ,;") for name in re.split(r"[,;]|\band\b", names)]
        pdfs = [urljoin(base, a.attrs.get("href", "")) for a in article.find("a") if urlsplit(a.attrs.get("href", "")).path.endswith(".pdf")]
        records.append({"title": title, "publication_title": f"NSDI {year}", "publication_year": year,
                        "authors": {"authors": [{"full_name": n} for n in authors if n]},
                        "authors_text": authors_text, "program_labels": awards,
                        "abstract": plain(descriptions[0]) if descriptions else "",
                        "abstract_url": url, "pdf_url": pdfs[0] if pdfs else "", "key": url})
    if not records:
        raise ValueError("USENIX technical sessions not found")
    return {"total_records": len(records), "articles": records}
