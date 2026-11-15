"""Build a GitHub Pages library from explicitly selected public paper metadata."""
import argparse
import hashlib
import json
import os
import re
import shutil
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from paperclaw.catalog import CATALOG
from paperclaw.core import EXPORT_FIELDS, atomic_json, read_json, safe_url
from paperclaw.instance import InstanceLock
from paperclaw.server import Application
from paperclaw.titles import prepare_title

ROOT = Path(__file__).resolve().parent
PUBLIC_FIELDS = set(EXPORT_FIELDS) - {"ai_summary", "title_text"}
PUBLIC_FIELDS.update(("id", "title_official", "official_url"))


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def public_paper(paper):
    if not isinstance(paper, dict):
        raise ValueError("每篇论文必须是 JSON 对象。")
    conference, year = paper.get("conference"), paper.get("year")
    if conference not in CATALOG or type(year) is not int or not 1980 <= year <= 2100:
        raise ValueError("论文需要有效的会议名称和整数年份。")
    if not isinstance(paper.get("title"), str) or not paper["title"].strip():
        raise ValueError("论文缺少标题。")
    result = {field:value for field,value in paper.items()
              if field in PUBLIC_FIELDS and isinstance(value, (str, int)) and type(value) is not bool}
    for field in ("authors", "keywords", "categories"):
        values = paper.get(field, [])
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError(f"{field} 必须是字符串数组。")
        result[field] = list(dict.fromkeys(values))
    sources = paper.get("category_sources", {})
    if not isinstance(sources, dict):
        raise ValueError("分类来源格式错误。")
    result["category_sources"] = {k:safe_url(v) for k,v in sources.items()
                                  if isinstance(k,str) and isinstance(v,str) and safe_url(v)}
    if not isinstance(result.get("id"), str) or not result["id"]:
        identity = paper.get("doi") or f"{conference}/{year}/{paper['title']}"
        result["id"] = hashlib.sha256(str(identity).encode()).hexdigest()[:24]
    for field in ("abstract", "doi", "url", "publisher_url", "pdf_url", "dblp_url", "abstract_source", "abstract_url",
                  "keyword_source"):
        result.setdefault(field, "")
    result.setdefault("source", "imported")
    result.setdefault("track", "main")
    result["abstract_status"] = "available" if result["abstract"] else result.get("abstract_status", "not_checked")
    prepare_title(result)
    return result


def group_key(conference, year):
    return re.sub(r"[^a-z0-9]+", "-", conference.lower()).strip("-") + "-" + str(year)


def load_input(path):
    data = read_json(path, None)
    papers = data.get("papers") if isinstance(data, dict) else data
    if not isinstance(papers, list) or not papers:
        raise ValueError(f"{path} 需要是非空论文 JSON 数组或包含 papers 的结果文件。")
    return [public_paper(p) for p in papers]


def retain_metadata(paper, old):
    if not paper.get("abstract") and old.get("abstract"):
        for field in ("abstract", "abstract_source", "abstract_url", "abstract_status", "keywords", "keyword_source"):
            paper[field] = old.get(field, paper.get(field))
    paper["category_sources"] = {**old.get("category_sources", {}), **paper.get("category_sources", {})}
    paper["categories"] = sorted(set(old.get("categories", [])) | set(paper.get("categories", [])))
    return paper


def save_collections(papers, data_dir, *, replace=False):
    groups = defaultdict(dict)
    for paper in papers:
        groups[(paper["conference"],paper["year"])][paper["id"]] = public_paper(paper)
    for (conference,year), incoming in groups.items():
        path = data_dir / (group_key(conference,year) + ".json")
        previous = {p["id"]:p for p in load_input(path)} if path.exists() else {}
        for key,paper in incoming.items():
            if key in previous:
                retain_metadata(paper, previous[key])
        merged = incoming if replace else {**previous, **incoming}
        ordered = sorted(merged.values(), key=lambda p:p["title_text"].casefold())
        atomic_json(path, {"schema_version":1, "conference":conference, "year":year,
                           "updated_at":timestamp(), "papers":ordered})
        print(f"{conference} {year}: {len(ordered)} papers", flush=True)


def build(data_dir, output):
    groups = []
    for path in sorted(data_dir.glob("*.json")):
        rows = load_input(path)
        scope = {(p["conference"],p["year"]) for p in rows}
        if len(scope) != 1:
            raise ValueError(f"{path} 必须只包含一个会议和年份。")
        conference,year = next(iter(scope))
        content = json.dumps(rows,ensure_ascii=False,separators=(",",":")).encode()
        digest = hashlib.sha256(content).hexdigest()[:16]
        key = group_key(conference,year)
        filename = f"papers/{key}-{digest}.json"
        destination = output / filename
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(content)
        metadata = read_json(path,{})
        groups.append({"key":key, "conference":conference, "year":year, "file":filename,
                       "count":len(rows), "abstract_count":sum(bool(p["abstract"]) for p in rows),
                       "updated_at":metadata.get("updated_at","") if isinstance(metadata,dict) else ""})
    if not groups:
        raise ValueError("site-data 中还没有论文数据，请先运行 import 或 collect。")
    output.mkdir(parents=True,exist_ok=True)
    for filename in ("index.html","site.js","site.css"):
        shutil.copyfile(ROOT / "site" / filename, output / filename)
    for filename in ("style.css","ui.js","katex.min.js","katex.LICENSE"):
        shutil.copyfile(ROOT / "web" / filename, output / filename)
    (output / ".nojekyll").write_text("",encoding="utf-8")
    atomic_json(output / "manifest.json", {
        "schema_version":1, "updated_at":timestamp(),
        "groups":sorted(groups,key=lambda g:(-g["year"],g["conference"])),
        "catalog":[dict(v) for v in CATALOG.values()],
        "export_fields":[field for field in EXPORT_FIELDS if field != "ai_summary"],
    })
    print(f"Built {len(groups)} collections in {output.resolve()}",flush=True)


def collect(args):
    conferences = [c.strip() for c in args.conferences.split(",") if c.strip()]
    years = [int(y.strip()) for y in args.years.split(",") if y.strip()]
    cache_dir = ROOT / "data" / "pages-cache"
    with InstanceLock(cache_dir):
        app = Application(cache_dir)
        app.start({"source":args.source,"conferences":conferences,"years":years,
                   "fetch_abstracts":not args.skip_abstracts, "ieee_key":os.getenv("IEEE_API_KEY",""),
                   "openalex_key":os.getenv("OPENALEX_API_KEY","")})
        previous_message = ""
        try:
            while True:
                state = app.snapshot(include_papers=False)
                if state["message"] != previous_message:
                    print(state["message"],flush=True)
                    previous_message = state["message"]
                if state["status"] != "running":
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            app.cancel.set()
            raise
        result = app.snapshot()
        if not result["groups"] or any(g["status"] != "completed" for g in result["groups"]):
            raise ValueError("本轮目录未完整采集，已保留原有 site-data，未发布本轮结果。")
        if not result["papers"]:
            raise ValueError("来源没有返回论文，已保留原有 site-data。")
        save_collections(result["papers"],args.data_dir,replace=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command",required=True)
    importer = sub.add_parser("import",help="Import explicitly chosen paper JSON files.")
    importer.add_argument("files",type=Path,nargs="+")
    builder = sub.add_parser("build",help="Build the static website.")
    builder.add_argument("--output",type=Path,default=ROOT / "_site")
    collector = sub.add_parser("collect",help="Collect public metadata before publication.")
    collector.add_argument("--conferences",required=True,help="Comma-separated venue names.")
    collector.add_argument("--years",required=True,help="Comma-separated years.")
    collector.add_argument("--source",choices=["auto","conference","crossref","dblp","openreview","ieee","usenix"],default="conference")
    collector.add_argument("--skip-abstracts",action="store_true")
    for item in (importer,builder,collector):
        item.add_argument("--data-dir",type=Path,default=ROOT / "site-data")
    args = parser.parse_args()
    if args.command == "import":
        papers = [p for path in args.files for p in load_input(path)]
        save_collections(papers,args.data_dir)
    elif args.command == "collect":
        collect(args)
    else:
        if args.output.resolve() in (ROOT,ROOT/"web",ROOT/"site",args.data_dir.resolve()):
            raise ValueError("请将构建输出放在独立目录，例如 _site。")
        build(args.data_dir,args.output)


if __name__ == "__main__":
    try:
        main()
    except (ValueError,KeyError,TypeError,OSError) as error:
        raise SystemExit(str(error)) from None
