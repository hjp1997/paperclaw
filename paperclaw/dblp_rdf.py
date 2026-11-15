"""Read DBLP's public SPARQL index by stream and conference edition."""
import json
import re
from .catalog import CATALOG

PREFIX = "PREFIX d: <https://dblp.org/rdf/schema#>\nPREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
PAGE_SIZE = 250

# Venue labels within a stream also include co-located meetings.
def main_proceedings(venue, conference):
    aliases = {
        "SIGKDD": ("KDD", "SIGKDD"), "NeurIPS": ("NIPS", "NeurIPS"),
        "ACL": ("ACL", "ACL/IJCNLP", "ACL-IJCNLP", "ACL/COLING", "COLING/ACL"),
        "AAAI": ("AAAI", "AAAI/IAAI", "AAAI/IAAI/EAAI"),
        "VLDB": ("VLDB", "Proc. VLDB Endow."),
        "SIGMOD": ("SIGMOD", "SIGMOD Conference", "SIGMOD Conference Companion",
                   "SIGMOD ARI Reports", "SIGMOD/PODS", "Proc. ACM Manag. Data"),
        "WWW": ("WWW", "The Web Conference"),
        "ACM SIGOPS ATC": ("USENIX ATC", "USENIX Annual Technical Conference", "ACM SIGOPS ATC", "ATC"),
    }
    names = aliases.get(conference, (conference,))
    return any(re.fullmatch(re.escape(name) + r"(?:\s*\([^()]*\))*", venue, re.I) for name in names)

# Journal issues assigned to each edition in DBLP's SIGMOD series directory.
PACMMOD = {
    2023: [(1, 1), (1, 2)],
    2024: [(1, 3), (1, 4), (2, 1), (2, 3)],
    2025: [(2, 4), (2, 6), (3, 1), (3, 3)],
    2026: [(3, 4), (3, 6), (4, 1), (4, 3)],
}


def edition(conference, year):
    stream = CATALOG[conference]["stream"]
    year = int(year)
    common = f"?p d:publishedInStream <https://dblp.org/streams/conf/{stream}> ."
    date = f'"{year}"^^xsd:gYear'
    regular = f"""?p a d:Inproceedings .
      {{ ?p d:yearOfEvent {date} }} UNION
      {{ ?p d:yearOfPublication {date} FILTER NOT EXISTS {{ ?p d:yearOfEvent ?other }} }}"""
    journal = ""
    if conference == "VLDB" and year >= 2008:
        journal = f'?p a d:Article ; d:publishedIn "Proc. VLDB Endow." ; d:publishedInJournalVolume "{year - 2007}" .'
    elif conference == "SIGMOD" and year in PACMMOD:
        pairs = " ".join(f'("{vol}" "{issue}")' for vol, issue in PACMMOD[year])
        journal = f"""?p a d:Article ; d:publishedIn "Proc. ACM Manag. Data" ;
          d:publishedInJournalVolume ?vol ; d:publishedInJournalVolumeIssue ?issue .
          VALUES (?vol ?issue) {{ {pairs} }}"""
    if journal:
        return f"{common} {{ {regular} }} UNION {{ {journal} }}"
    return f"{common} {regular}"


def page_query(scope, offset, size):
    # Page publication IDs before joining authors, so no author list is split across pages.
    return PREFIX + f"""SELECT ?p ?title ?venue ?year ?page ?doi ?url ?name ?position WHERE {{
      {{ SELECT DISTINCT ?p WHERE {{ {scope} }} ORDER BY ?p LIMIT {int(size)} OFFSET {int(offset)} }}
      ?p d:title ?title ; d:publishedIn ?venue ; d:yearOfPublication ?year .
      OPTIONAL {{ ?p d:primaryDocumentPage ?url }}
      OPTIONAL {{ ?p d:doi ?doi }}
      OPTIONAL {{ ?p d:pagination ?page }}
      OPTIONAL {{ ?p d:hasSignature ?s . ?s a d:AuthorSignature ;
          d:signatureDblpName ?name ; d:signatureOrdinal ?position }}
    }}"""


def counts_query(editions):
    branches = ["{ { " + edition(conference, year) + " } BIND(" + json.dumps(conference) +
                " AS ?conf) BIND(" + str(int(year)) + " AS ?year) }" for conference,year in editions]
    return PREFIX + "SELECT ?conf ?year (COUNT(DISTINCT ?p) AS ?n) WHERE { " + " UNION ".join(branches) + " } GROUP BY ?conf ?year"


def page_records(bindings):
    rows, authors = {}, {}
    for binding in bindings:
        values = {k: v["value"] for k, v in binding.items()}
        url = values["p"]
        if not url.startswith("https://dblp.org/rec/"):
            raise ValueError("Invalid DBLP record identifier")
        if url not in rows:
            rows[url] = {"key": url.split("/rec/", 1)[1], "url": url, "title": values["title"],
                         "venue": values["venue"], "year": values["year"], "ee": values.get("url", ""),
                         "doi": values.get("doi", ""), "pages": values.get("page", ""),
                         "type": "Conference and Workshop Papers"}
            authors[url] = {}
        if "name" in values:
            authors[url][int(values["position"])] = values["name"]
    for url, row in rows.items():
        row["authors"] = {"author": [name for _, name in sorted(authors[url].items())]}
    return rows.values()
