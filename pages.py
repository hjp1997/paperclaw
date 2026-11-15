"""Build a GitHub Pages library from explicitly selected public paper metadata."""
import argparse
import hashlib
import json
import os
import re
import shutil
import time
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from paperclaw.catalog import CATALOG
from paperclaw.core import EXPORT_FIELDS, AccessError, PageError, CrawlError, SafeClient, atomic_json, crawl_dblp_rdf, crawl_group, normalize, read_json, safe_url
from paperclaw import dblp_rdf, official, proceedings
from paperclaw.abstracts import enrich_abstracts, enrich_titles, doi_key
from paperclaw.analysis import MODEL, TOPICS, analyze_paper, is_featured, public_analysis
from paperclaw.instance import InstanceLock
from paperclaw.server import Application
from paperclaw.titles import prepare_title
from paperclaw.program import title_key

ROOT = Path(__file__).resolve().parent
BATCH_AREAS = {"计算机体系结构/并行与分布计算/存储系统", "计算机网络", "交叉/综合/新兴", "计算机科学理论", "数据库/数据挖掘/内容检索"}
PUBLIC_FIELDS = set(EXPORT_FIELDS) - {"ai_summary", "title_text"}
PUBLIC_FIELDS.update(("id", "title_official", "official_url"))
PUBLIC_FIELDS.add("abstract_checked_at")


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
    checks = paper.get("abstract_sources_checked", {})
    if isinstance(checks,dict):
        result["abstract_sources_checked"] = {name:date for name,date in checks.items()
            if name in ("openalex","openalex_title","conference_site","paper_page","arxiv","crossref") and isinstance(date,str) and len(date)<=64}
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
    analysis = public_analysis(paper)
    if analysis:
        result["ai_analysis"] = analysis
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
    analysis = public_analysis(paper) or public_analysis({**paper, "ai_analysis": old.get("ai_analysis")})
    if analysis:
        paper["ai_analysis"] = analysis
    else:
        paper.pop("ai_analysis", None)
    return paper


def save_collections(papers, data_dir, *, replace=False):
    groups = defaultdict(dict)
    for paper in papers:
        groups[(paper["conference"],paper["year"])][paper["id"]] = public_paper(paper)
    for (conference,year), incoming in groups.items():
        path = data_dir / (group_key(conference,year) + ".json")
        previous = {p["id"]:p for p in load_input(path)} if path.exists() else {}
        by_title = defaultdict(list)
        for paper in previous.values(): by_title[title_key(paper["title"])].append(paper)
        incoming_titles = Counter(title_key(paper["title"]) for paper in incoming.values())
        remapped = {}
        for key,paper in incoming.items():
            matches = by_title[title_key(paper["title"])]
            if key not in previous and len(matches) == 1 and incoming_titles[title_key(paper["title"])] == 1:
                old = matches[0]
                if not (old.get("doi") and paper.get("doi") and old["doi"].casefold() != paper["doi"].casefold()):
                    key = paper["id"] = old["id"]
            if key in previous: retain_metadata(paper, previous[key])
            remapped[key] = paper
        incoming = remapped
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
                       "analysis_count":sum(bool(p.get("ai_analysis")) for p in rows),
                       "updated_at":metadata.get("updated_at","") if isinstance(metadata,dict) else ""})
    if not groups:
        raise ValueError("site-data 中还没有论文数据，请先运行 import 或 collect。")
    output.mkdir(parents=True,exist_ok=True)
    for filename in ("index.html","site.js","site.css","chat.js"):
        shutil.copyfile(ROOT / "site" / filename, output / filename)
    for filename in ("style.css","ui.js","katex.min.js","katex.LICENSE"):
        shutil.copyfile(ROOT / "web" / filename, output / filename)
    html = (output / "index.html").read_text("utf-8")
    for filename in ("site.js","chat.js","site.css","ui.js","style.css","katex.min.js"):
        digest = hashlib.sha256((output / filename).read_bytes()).hexdigest()[:12]
        html = html.replace(f'"./{filename}"', f'"./{filename}?v={digest}"')
    (output / "index.html").write_text(html, encoding="utf-8")
    (output / ".nojekyll").write_text("",encoding="utf-8")
    atomic_json(output / "manifest.json", {
        "schema_version":1, "updated_at":timestamp(),
        "groups":sorted(groups,key=lambda g:(-g["year"],g["conference"])),
        "catalog":[dict(v) for v in CATALOG.values()],
        "topics":TOPICS,
        "collection_status":collection_status(data_dir),
        "export_fields":[field for field in EXPORT_FIELDS if field != "ai_summary"] +
                        ["topics", "ai_keyphrases", "ai_problem", "ai_method", "ai_results", "ai_model"],
    })
    print(f"Built {len(groups)} collections in {output.resolve()}",flush=True)


def collection_status(data_dir):
    data = read_json(data_dir / "status" / "collections.json", {})
    rows = data.get("editions", []) if isinstance(data,dict) else []
    result = []
    for row in rows:
        if (not isinstance(row,dict) or row.get("conference") not in CATALOG or type(row.get("year")) is not int or
                not 1980 <= row["year"] <= 2100 or row.get("status") not in ("available","unavailable","failed","pending")):
            continue
        result.append({"key":group_key(row["conference"],row["year"]), "conference":row["conference"], "year":row["year"],
                       "status":row["status"], "reason":str(row.get("reason", ""))[:500],
                       "checked_at":str(row.get("checked_at", ""))[:64]})
    return result


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


def collect_batch(args):
    conferences = list(dict.fromkeys(c.strip() for c in args.conferences.split(",") if c.strip()))
    conferences = conferences or [name for name,entry in CATALOG.items() if entry["category"] in BATCH_AREAS]
    years = sorted({int(y.strip()) for y in args.years.split(",") if y.strip()})
    if set(conferences) - CATALOG.keys() or not years or any(not 1980 <= year <= 2100 for year in years):
        raise ValueError("请选择有效的会议和年份。")
    report_path = args.data_dir / "status" / "collections.json"
    previous = read_json(report_path, {}).get("editions", [])
    statuses = {item["key"]:item for item in previous}
    pending, counts = [], {}
    def persist():
        atomic_json(report_path, {"schema_version":1, "source_url":"https://ccf.atom.im/", "updated_at":timestamp(),
                                  "editions":sorted(statuses.values(),key=lambda item:(item["conference"],item["year"]))})
    for conference in conferences:
        for year in years:
            key = group_key(conference,year)
            path = args.data_dir / (key + ".json")
            state = {"key":key,"conference":conference,"year":year,"checked_at":timestamp(),"status":"pending","count":0,"reason":""}
            if path.exists() and not args.refresh_existing:
                state.update(status="available",count=len(load_input(path)),reason="保留已有采集结果。")
                print(f"{conference} {year}: 保留已有 {state['count']} 篇",flush=True)
            else:
                pending.append((conference,year))
            statuses[key] = state
    persist()
    cache_dir = ROOT / "data" / "pages-cache"
    with InstanceLock(cache_dir):
        client, cancel = SafeClient(cache_dir), threading.Event()
        rdf_editions = [(c,y) for c,y in pending if not (c in ("NSDI","FAST") or c in official.VIRTUAL or c == "ACM SIGOPS ATC" and y <= 2025)]
        # Batch only the small count queries; paper pages remain bounded and serialized.
        for offset in range(0,len(rdf_editions),8):
            chunk = rdf_editions[offset:offset+8]
            print(f"核对 DBLP 目录计数：{offset+1}–{offset+len(chunk)} / {len(rdf_editions)}",flush=True)
            result = client.get("dblp_rdf", {"query":dblp_rdf.counts_query(chunk)}, "dblp:batch:counts", cancel, print)
            counts.update({edition:0 for edition in chunk})
            for row in result["results"]["bindings"]:
                counts[(row["conf"]["value"],int(row["year"]["value"]))] = int(row["n"]["value"])
        for conference,year in pending:
            state = statuses[group_key(conference,year)]
            official_url = proceedings.URLS.get((conference,year))
            if counts.get((conference,year)) == 0 and not official_url:
                state.update(status="unavailable",reason="DBLP 尚未收录该届会议目录；可能尚未公开或正在更新。",checked_at=timestamp())
                persist(); print(f"{conference} {year}: {state['reason']}",flush=True); continue
            papers = []
            try:
                if counts.get((conference,year)) == 0 and official_url:
                    data = client.get("proceedings", {"conference":conference,"year":year,"parser_version":1}, "conference:accepted",cancel,print)
                    records = {r["key"]:r for r in data["articles"]}
                    for path in data.get("pages", []):
                        part = client.get("proceedings", {"conference":conference,"year":year,"page":path,"parser_version":1}, "conference:accepted",cancel,print)
                        records.update({r["key"]:r for r in part["articles"]})
                    for record in records.values():
                        paper = normalize(record,"ieee",conference,year)
                        if paper:
                            landing = record.get("landing_url") or official_url
                            paper.update(source="conference_site",url=landing,publisher_url=landing,official_url=landing,
                                         categories=record.get("program_labels",[]),category_sources=dict.fromkeys(record.get("program_labels",[]),official_url))
                            papers.append(paper)
                elif (conference,year) in counts:
                    crawl_dblp_rdf(client,conference,year,False,cancel,lambda batch,*_:papers.extend(batch),print,
                                   known_total=counts[(conference,year)],page_size=250)
                else:
                    source = "conference" if conference in official.VIRTUAL else "usenix"
                    crawl_group(client,source,conference,year,False,"",cancel,lambda batch,*_:papers.extend(batch),print)
                if not papers:
                    raise CrawlError("来源未返回可发布的论文。")
                save_collections(papers,args.data_dir,replace=True)
                state.update(status="available",count=len(load_input(args.data_dir/(group_key(conference,year)+".json"))),reason="",checked_at=timestamp())
            except CrawlError as error:
                state.update(status="failed",reason=str(error),checked_at=timestamp())
                print(f"{conference} {year}: {error}",flush=True)
            persist()
    summary = {status:sum(item["status"]==status for item in statuses.values()) for status in ("available","unavailable","failed","pending")}
    print("批量结果：" + json.dumps(summary,ensure_ascii=False),flush=True)


def analyze(args):
    conferences = {c.strip() for c in args.conferences.split(",") if c.strip()}
    years = {int(y.strip()) for y in args.years.split(",") if y.strip()}
    if not conferences or conferences - CATALOG.keys() or not years or any(not 1980 <= y <= 2100 for y in years):
        raise ValueError("请选择有效的会议和年份。")
    if not 1 <= args.limit <= 1000:
        raise ValueError("每轮分析篇数必须在 1–1000 之间。")
    scope = getattr(args, "scope", "featured")
    if scope not in ("featured", "all"):
        raise ValueError("分析范围必须为 featured 或 all。")
    batches, total, ready, missing, excluded = [], 0, 0, 0, 0
    for path in sorted(args.data_dir.glob("*.json")):
        rows = load_input(path)
        pending = []
        for paper in rows:
            if paper["conference"] not in conferences or paper["year"] not in years:
                continue
            total += 1
            if scope == "featured" and not is_featured(paper):
                excluded += 1
            elif not paper["abstract"].strip():
                missing += 1
            elif not args.force and paper.get("ai_analysis", {}).get("model") == args.model:
                ready += 1
            else:
                pending.append(paper)
        if pending:
            batches.append((path, rows, pending))
    if not total:
        raise ValueError("指定会议和年份尚无已采集数据，请先采集或导入。")
    pending_count = sum(len(items) for _, _, items in batches)
    print(f"范围 {total} 篇；分类筛选跳过 {excluded}；已完成 {ready}；缺摘要 {missing}；待分析 {pending_count}；本轮上限 {args.limit}。", flush=True)
    if args.dry_run or not pending_count:
        return
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise ValueError("请在 GitHub Secrets 或本地环境变量中配置 DEEPSEEK_API_KEY。")
    done = 0
    for path, rows, pending in batches:
        metadata = read_json(path, {})
        for paper in pending:
            if done >= args.limit:
                break
            paper["ai_analysis"] = analyze_paper(paper, key, args.model)
            atomic_json(path, {"schema_version":1, "conference":rows[0]["conference"], "year":rows[0]["year"],
                               "updated_at":metadata.get("updated_at", ""), "analysis_updated_at":timestamp(), "papers":rows})
            done += 1
            print(f"已生成 {done}/{min(args.limit,pending_count)} · {paper['conference']} {paper['year']}", flush=True)
        if done >= args.limit:
            break
    print(f"本轮生成 {done} 篇；剩余 {pending_count-done} 篇可再次运行继续。", flush=True)


def enrich(args):
    conferences = {c.strip() for c in args.conferences.split(",") if c.strip()}
    years = {int(y.strip()) for y in args.years.split(",") if y.strip()}
    if conferences - CATALOG.keys() or not years or any(not 1980 <= year <= 2100 for year in years) or args.limit < 0:
        raise ValueError("请选择有效会议、年份和非负篇数上限；0 表示全部。")
    collections, index = {}, {}
    for path in sorted(args.data_dir.glob("*.json")):
        rows = load_input(path)
        if rows[0]["year"] not in years or conferences and rows[0]["conference"] not in conferences: continue
        collections[path] = (read_json(path,{}),rows)
        for paper in rows: index[(paper["conference"],paper["year"],paper["id"])] = (path,paper)
    now = datetime.now(timezone.utc)
    def eligible(paper, source):
        checked = paper.get("abstract_sources_checked",{}).get(source, "")
        try: recent = checked and 0 <= (now-datetime.fromisoformat(checked)).total_seconds() < 86400
        except (ValueError,TypeError): recent = False
        return not paper["abstract"] and (args.force or not recent)
    pending = [p for _,p in index.values() if not p["abstract"]]
    if args.limit: pending = pending[:args.limit]
    if not collections: raise ValueError("尚无所选会议年份的数据，请先采集。")
    report_path = args.data_dir/"status"/"abstracts.json"
    blocked = {}
    initial = sum(bool(p["abstract"]) for _,p in index.values())
    def report(status, message=""):
        rows = [p for _,items in collections.values() for p in items]
        atomic_json(report_path, {"updated_at":timestamp(),"status":status,"message":message,
            "total":len(rows),"with_abstract":sum(bool(p["abstract"]) for p in rows),
            "remaining":sum(not bool(p["abstract"]) for p in rows),"added":sum(bool(p["abstract"]) for p in rows)-initial,
            "blocked_sources":list({v["host"]:v for v in blocked.values()}.values())})
    def update(batch):
        changed = set()
        for paper in batch:
            path,original = index[(paper["conference"],paper["year"],paper["id"])]
            original.update(paper); changed.add(path)
        for path in changed:
            metadata, rows = collections[path]
            metadata.update(papers=rows,updated_at=timestamp())
            atomic_json(path,metadata)
        report("running")
    cache_dir = ROOT/"data"/"pages-cache"
    print(f"本轮补全 {len(pending)} 篇缺失的原文摘要，不调用 AI。",flush=True)
    report("running")
    with InstanceLock(cache_dir):
        client, cancel = SafeClient(cache_dir), threading.Event()
        try:
            if args.source == "auto":
                official.enrich(client,pending,cancel,update,print)
            if args.source in ("auto","openalex"):
                enrich_abstracts(client,[p for p in pending if p.get("doi") and eligible(p,"openalex")],cancel,update,print,os.getenv("OPENALEX_API_KEY", ""))
                enrich_titles(client,[p for p in pending if eligible(p,"openalex_title")],cancel,update,print,os.getenv("OPENALEX_API_KEY", ""))
            if args.source in ("auto","pages"):
                for paper in pending:
                    if not eligible(paper,"paper_page"): continue
                    for url in official.page_candidates(paper):
                        if urlsplit(url).hostname in blocked: continue
                        try:
                            result = client.get("paper_page",{"url":url},"paper:metadata",cancel,print)
                            if title_key(result["title"]) != title_key(paper.get("title_text") or paper["title"]):
                                raise PageError("官网标题与论文记录不一致。")
                            if result.get("doi") and paper.get("doi") and doi_key(result["doi"]) != doi_key(paper["doi"]):
                                raise PageError("官网 DOI 与论文记录不一致。")
                            paper.update(abstract=result["abstract"],abstract_source="paper_website",abstract_url=result["url"],abstract_status="available")
                            if paper.get("keyword_source", "").startswith("local_"):
                                from paperclaw.core import keywords
                                paper.update(keywords=keywords(paper["title"],paper["abstract"]),keyword_source="local_title_abstract")
                        except AccessError as error:
                            host = urlsplit(error.url or url).hostname
                            blocked[host] = {"host":host,"url":error.url or url,"reason":str(error),"kind":error.kind}
                            # A DOI resolver may lead to a blocked publisher.
                            blocked[urlsplit(url).hostname] = blocked[host]
                            report("running"); continue
                        except PageError:
                            continue
                        else:
                            paper["abstract_sources_checked"] = {**paper.get("abstract_sources_checked",{}),"paper_page":timestamp()}
                            update([paper]); break
                    else:
                        # Access restrictions are not evidence of a missing abstract.
                        if official.page_candidates(paper) and not any(urlsplit(u).hostname in blocked for u in official.page_candidates(paper)):
                            paper["abstract_sources_checked"] = {**paper.get("abstract_sources_checked",{}),"paper_page":timestamp()}
                            update([paper])
        except CrawlError as error:
            report("partial",str(error)); raise
    report("completed")


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
    batch = sub.add_parser("collect-batch",help="Collect the five requested CCF A areas independently, without AI.")
    batch.add_argument("--conferences",default="",help="Empty selects all CCF A venues in the five batch areas.")
    batch.add_argument("--years",default="2025,2026")
    batch.add_argument("--refresh-existing",action="store_true")
    analyzer = sub.add_parser("analyze",help="Precompute public topics and abstract summaries using DeepSeek.")
    analyzer.add_argument("--conferences",required=True)
    analyzer.add_argument("--years",required=True)
    analyzer.add_argument("--model",default=MODEL)
    analyzer.add_argument("--limit",type=int,default=50)
    analyzer.add_argument("--scope",choices=("featured","all"),default="featured",help="Featured classifications (excluding regular posters), or all papers with abstracts.")
    analyzer.add_argument("--dry-run",action="store_true",help="Show counts without contacting the AI service.")
    analyzer.add_argument("--force",action="store_true",help="Regenerate existing analyses within the limit.")
    enricher = sub.add_parser("enrich",help="Fill missing source abstracts without invoking AI.")
    enricher.add_argument("--conferences",default="")
    enricher.add_argument("--years",default="2025,2026")
    enricher.add_argument("--limit",type=int,default=500)
    enricher.add_argument("--source",choices=("auto","openalex","pages"),default="auto")
    enricher.add_argument("--force",action="store_true")
    for item in (importer,builder,collector,batch,analyzer,enricher):
        item.add_argument("--data-dir",type=Path,default=ROOT / "site-data")
    args = parser.parse_args()
    if args.command == "import":
        papers = [p for path in args.files for p in load_input(path)]
        save_collections(papers,args.data_dir)
    elif args.command == "collect":
        collect(args)
    elif args.command == "collect-batch":
        collect_batch(args)
    elif args.command == "analyze":
        analyze(args)
    elif args.command == "enrich":
        enrich(args)
    else:
        if args.output.resolve() in (ROOT,ROOT/"web",ROOT/"site",args.data_dir.resolve()):
            raise ValueError("请将构建输出放在独立目录，例如 _site。")
        build(args.data_dir,args.output)


if __name__ == "__main__":
    try:
        main()
    except (ValueError,KeyError,TypeError,OSError,CrawlError) as error:
        raise SystemExit(str(error)) from None
