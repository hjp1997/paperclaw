"""Read literal fields from publisher-exported BibTeX conference citations."""
import re
from .catalog import CATALOG
from .core import clean, normalize, safe_url


def _value(text, pos):
    opener = text[pos]
    if opener not in '{"':
        match = re.match(r"[^,})\s]+", text[pos:])
        if not match:
            raise ValueError("BibTeX 字段值为空。")
        return match[0], pos + len(match[0])
    start = pos + 1
    pos, depth = start, 0
    while pos < len(text):
        char = text[pos]
        if char == "\\":
            pos += 2
            continue
        if depth == 0 and char == ('}' if opener == '{' else '"'):
            return text[start:pos], pos + 1
        if char == '{':
            depth += 1
        elif char == '}' and depth:
            depth -= 1
        pos += 1
    raise ValueError("BibTeX 字段没有闭合，请重新导出完整引用。")


def conference_citations(text):
    if not isinstance(text, str):
        raise ValueError("请选择出版社导出的 .bib 文件。")
    entry = re.compile(r"(?im)^[ \t]*@(inproceedings|conference)\s*([({])\s*[^,{}()]+,")
    field = re.compile(r"([\w-]+)\s*=\s*")
    pos = 0
    while match := entry.search(text, pos):
        end = '}' if match[2] == '{' else ')'
        pos, fields = match.end(), {}
        while pos < len(text):
            if text[pos].isspace() or text[pos] == ',':
                pos += 1
                continue
            if text[pos] == end:
                pos += 1
                break
            key = field.match(text, pos)
            if not key or key.end() == len(text):
                raise ValueError("无法读取 BibTeX 字段，请使用出版社导出的完整引用。")
            value, pos = _value(text, key.end())
            fields[key[1].lower()] = clean(value)
        else:
            raise ValueError("BibTeX 记录没有闭合。")
        yield fields


def import_citations(text, options):
    batch, count = [], 0
    for fields in conference_citations(text):
        count += 1
        year = fields.get("year", "")
        if not year.isdigit() or int(year) not in options["years"]:
            continue
        def display(key):
            return fields.get(key, "").replace("{", "").replace("}", "").replace(r"\&", "&")
        names = re.split(r"\s+and\s+", display("author"), flags=re.I)
        data = {
            "title": display("title"), "publication_title": display("booktitle"),
            "publication_year": year, "abstract": display("abstract"),
            "authors": {"authors": [{"full_name": name} for name in names if name]},
            "doi": fields.get("doi", ""), "pages": fields.get("pages", ""),
            "abstract_url": safe_url(fields.get("url", "")),
        }
        for conference in options["conferences"]:
            paper = normalize(data, "publisher_bibtex", conference, int(year), options["include_workshops"])
            if paper:
                prefix = CATALOG[conference]["prefix"]
                if prefix and paper["doi"] and not paper["doi"].lower().startswith(prefix + "/"):
                    continue
                batch.append(paper)
                break
    if not count:
        raise ValueError("文件中没有 BibTeX 会议论文记录（@inproceedings 或 @conference）。")
    return batch, count
