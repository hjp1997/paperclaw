"use strict";
const $ = id => document.getElementById(id);
const {el, link, paperTitle} = window.PaperClawUI;
const PAGE_SIZE = 25;
const STORAGE_KEY = "paperclaw:collections:" + new URL(".", location.href).pathname;
const selected = new Set(), chosenConferences = new Set(), expanded = new Set();
const cache = new Map(), paperIndex = new Map();
const sources = {openalex:"OpenAlex", openreview:"OpenReview", conference_site:"会议官网", paper_website:"论文官网",
  arxiv:"arXiv", acl_anthology:"ACL Anthology", acl_program:"ACL 官网", ieee:"IEEE", usenix:"USENIX",
  crossref:"Crossref", dblp:"DBLP", dblp_rdf:"DBLP"};
let manifest, papers = [], currentGroups = [], view = "all", page = 1, generation = 0, loading = false;
let saved = readSaved();

function notice(text) {
  $("notice").textContent = text;
  $("notice").hidden = !text;
}

function readSaved() {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return new Set(Array.isArray(value) ? value.filter(key => typeof key === "string") : []);
  } catch {
    return new Set();
  }
}

function toggleSaved(key) {
  const next = new Set(saved);
  if (next.has(key)) next.delete(key); else next.add(key);
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...next]));
    saved = next;
    notice("");
    if (view === "saved") loadPapers(); else renderGroups();
    updateSavedCount();
  } catch {
    notice("浏览器未能保存收藏，请检查是否允许此网站保存本地数据。");
  }
}

function updateSavedCount() {
  $("savedView").textContent = "我的收藏（" + saved.size + " 组）";
}

async function jsonFile(path) {
  const url = new URL(path, new URL(".", location.href));
  if (url.origin !== location.origin) throw new Error("论文数据地址超出当前网站。");
  const response = await fetch(url, path === "manifest.json" ? {cache:"no-cache"} : {});
  if (!response.ok) throw new Error("论文数据读取失败（HTTP " + response.status + "）");
  return response.json();
}

function groupsInScope() {
  const year = $("filterYear").value;
  return manifest.groups.filter(group => (!year || String(group.year) === year) &&
    (view === "saved" ? saved.has(group.key) : chosenConferences.has(group.conference)));
}

async function collection(group) {
  if (cache.has(group.file)) return cache.get(group.file);
  const rows = await jsonFile(group.file);
  if (!Array.isArray(rows) || rows.length !== group.count) throw new Error("论文数据与目录不一致，请刷新页面。");
  const result = rows.map(paper => ({...paper, _key:group.key + "|" + paper.id}));
  cache.set(group.file, result);
  for (const paper of result) paperIndex.set(paper._key, paper);
  return result;
}

async function loadPapers() {
  notice("");
  const version = ++generation;
  currentGroups = groupsInScope();
  papers = [];
  page = 1;
  loading = true;
  renderGroups();
  render();
  try {
    const results = await Promise.all(currentGroups.map(collection));
    if (version !== generation) return;
    papers = results.flat();
    const category = $("filterCategory").value;
    const labels = [...new Set(papers.flatMap(p => p.categories || []))].sort();
    $("filterCategory").replaceChildren(new Option("全部分类", ""), new Option("暂无分类标签", "__none__"),
      ...labels.map(label => new Option(label, label)));
    if ([...$("filterCategory").options].some(option => option.value === category)) $("filterCategory").value = category;
  } catch (error) {
    if (version === generation) notice(error.message);
  } finally {
    if (version === generation) { loading = false; render(); }
  }
}

function filtered() {
  const text = $("search").value.trim().toLowerCase(), category = $("filterCategory").value;
  return papers.filter(p => (!category || (category === "__none__" ? !p.categories?.length : p.categories?.includes(category))) &&
    (!text || [p.title, p.title_text, p.abstract, p.doi, ...p.authors, ...p.keywords].join(" ").toLowerCase().includes(text)));
}

function renderGroups() {
  $("groups").replaceChildren(...currentGroups.map(group => {
    const row = el("div", "collection-row");
    row.append(el("span", "collection-name", group.conference + " " + group.year),
      el("span", "collection-count", group.count + " 篇 · " + group.abstract_count + " 篇有摘要"));
    const keep = el("button", "", saved.has(group.key) ? "取消收藏" : "收藏本组");
    keep.setAttribute("aria-pressed", String(saved.has(group.key)));
    keep.onclick = () => toggleSaved(group.key);
    row.append(keep);
    return row;
  }));
}

function paperCard(p) {
  const card = el("article", "paper"), top = el("div", "paper-top");
  const choose = el("label", "paper-select"), input = el("input");
  input.type = "checkbox";
  input.checked = selected.has(p._key);
  input.setAttribute("aria-label", "选择论文 " + (p.title_text || p.title));
  input.onchange = () => {
    if (input.checked) selected.add(p._key); else selected.delete(p._key);
    renderSelection(filtered());
  };
  choose.append(input, "选择");
  top.append(choose, el("span", "tag", p.conference), el("span", "", p.year), el("span", "tag source", sources[p.source] || p.source));
  for (const category of p.categories || []) {
    const tag = link(category, p.category_sources?.[category]);
    tag.className = "distinction";
    top.append(tag);
  }
  card.append(top, paperTitle(p), el("p", "authors", p.authors.join(" · ")));
  const keywords = el("div", "keywords");
  for (const word of p.keywords) keywords.append(el("span", "keyword", word));
  card.append(keywords);
  const bottom = el("div", "paper-bottom");
  if (p.publisher_url || p.url) bottom.append(link("论文页面 ↗", p.publisher_url || p.url));
  if (p.pdf_url) bottom.append(link("PDF ↗", p.pdf_url));
  if (p.dblp_url) bottom.append(link("DBLP ↗", p.dblp_url));
  card.append(bottom);
  if (p.abstract) {
    const detail = el("details", "abstract");
    detail.open = expanded.has(p._key);
    detail.addEventListener("toggle", () => {
      if (detail.open) expanded.add(p._key); else expanded.delete(p._key);
    });
    detail.append(el("summary", "", "摘要 · " + (sources[p.abstract_source] || p.abstract_source || p.source)),
      el("p", "abstract-preview", p.abstract));
    if (p.abstract_url) detail.append(link("摘要来源 ↗", p.abstract_url));
    card.append(detail);
  } else card.append(el("p", "help", "来源尚未提供摘要。"));
  return card;
}

function renderSelection(rows) {
  $("selectionInfo").textContent = selected.size ? "已勾选 " + selected.size + " 篇，导出所选论文" : "导出筛选的 " + rows.length + " 篇";
  $("clearSelection").disabled = !selected.size;
  $("selectFiltered").disabled = loading || !rows.length || rows.every(p => selected.has(p._key));
  document.querySelectorAll("[data-export]").forEach(button => button.disabled = !selected.size && !rows.length);
}

function render() {
  const rows = filtered(), pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  page = Math.min(page, pages);
  $("visibleCount").textContent = rows.length;
  $("loading").textContent = loading ? "正在读取论文数据…" : "";
  $("pageInfo").textContent = rows.length ? "显示 " + ((page - 1) * PAGE_SIZE + 1) + "–" + Math.min(page * PAGE_SIZE, rows.length) + " / " + rows.length : "";
  $("pageNumber").textContent = page + " / " + pages;
  $("prev").disabled = page === 1;
  $("next").disabled = page === pages;
  renderSelection(rows);
  const cards = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE).map(paperCard);
  if (!cards.length) {
    let empty = "没有匹配的论文，请调整会议或筛选条件。";
    if (loading) empty = "正在读取论文数据…";
    else if (view === "saved" && !currentGroups.length) empty = saved.size ? "当前发布数据中暂未收录这些收藏。" : "还没有收藏。";
    cards.push(el("div", "empty", empty));
  }
  $("papers").replaceChildren(...cards);
}

function conferenceTree() {
  const available = new Set(manifest.groups.map(g => g.conference));
  const venues = manifest.catalog.filter(venue => available.has(venue.name));
  for (const area of [...new Set(venues.map(v => v.category))]) {
    const group = el("details", "venue-group");
    group.open = true;
    group.append(el("summary", "", area));
    const children = el("div", "venue-children");
    for (const venue of venues.filter(v => v.category === area)) {
      const label = el("label", "venue-leaf"), box = el("input");
      box.type = "checkbox"; box.value = venue.name; box.checked = chosenConferences.has(venue.name);
      box.onchange = () => {
        if (box.checked) chosenConferences.add(venue.name); else chosenConferences.delete(venue.name);
        if (view !== "all") setView("all"); else loadPapers();
      };
      const count = manifest.groups.filter(g => g.conference === venue.name).reduce((sum, g) => sum + g.count, 0);
      label.append(box, venue.name, el("span", "venue-count", count));
      children.append(label);
    }
    group.append(children);
    $("conferenceChoices").append(group);
  }
}

function setView(next) {
  view = next; selected.clear(); $("search").value = ""; $("filterYear").value = ""; $("filterCategory").value = "";
  $("allView").setAttribute("aria-pressed", String(view === "all"));
  $("savedView").setAttribute("aria-pressed", String(view === "saved"));
  loadPapers();
}

function exportRows(rows, format) {
  const clean = rows.map(p => Object.fromEntries(Object.entries(p).filter(([key]) => !key.startsWith("_"))));
  if (format === "json") return JSON.stringify(clean, null, 2);
  if (format === "csv") {
    const cell = value => {
      if (Array.isArray(value)) value = value.join("; ");
      else if (value && typeof value === "object") value = JSON.stringify(value);
      value = String(value ?? "");
      if (/^\s*[=+\-@]/.test(value)) value = "'" + value;
      return '"' + value.replaceAll('"', '""') + '"';
    };
    const fields = manifest.export_fields;
    return "\uFEFF" + [fields.map(cell).join(","), ...clean.map(row => fields.map(field => cell(row[field])).join(","))].join("\r\n") + "\r\n";
  }
  const escape = value => String(value ?? "").replace(/[\\\x60*_[\]<>#|]/g, "\\$&");
  const sections = clean.map(p => {
    const labels = Object.entries(p.category_sources || {}).map(([label, url]) => "- " + escape(label) + " · " + escape(url)).join("\n");
    return [
      "## " + escape(p.title_text || p.title), "",
      "- 会议 · " + escape(p.conference) + " " + p.year,
      "- 作者 · " + escape(p.authors.join("; ")),
      "- DOI · " + escape(p.doi),
      "- 关键词 · " + escape(p.keywords.join("; ")),
      "- 论文 · " + escape(p.url),
      "- 摘要来源 · " + escape(p.abstract_source) + " " + escape(p.abstract_url),
      labels, "", escape(p.abstract) || "来源尚未提供摘要。", ""
    ].join("\n");
  });
  return "# 会议论文\n\n共 " + clean.length + " 篇。\n\n" + sections.join("\n");
}

async function exportFile(format) {
  const rows = selected.size ? [...selected].map(key => paperIndex.get(key)).filter(Boolean) : filtered();
  if (!rows.length) return;
  const mime = {csv:"text/csv", md:"text/markdown", json:"application/json"}[format];
  const name = "paperclaw-" + new Date().toISOString().slice(0, 10) + "." + format;
  try {
    const content = exportRows(rows, format);
    const handle = window.showSaveFilePicker ? await window.showSaveFilePicker({
      suggestedName:name, types:[{description:format.toUpperCase(), accept:{[mime]:["." + format]}}]
    }) : null;
    const blob = new Blob([content], {type:mime + ";charset=utf-8"});
    if (handle) {
      const writer = await handle.createWritable();
      await writer.write(blob); await writer.close();
    } else {
      const url = URL.createObjectURL(blob), anchor = el("a");
      anchor.href = url; anchor.download = name; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    }
    notice("已导出 " + rows.length + " 篇论文。");
  } catch (error) {
    if (error.name !== "AbortError") notice("导出失败：" + error.message);
  }
}

$("allView").onclick = () => setView("all");
$("savedView").onclick = () => setView("saved");
$("filterYear").onchange = () => loadPapers();
for (const id of ["search", "filterCategory"]) $(id).addEventListener("input", () => { page = 1; render(); });
$("selectFiltered").onclick = () => { for (const paper of filtered()) selected.add(paper._key); render(); };
$("clearSelection").onclick = () => { selected.clear(); render(); };
$("prev").onclick = () => { page--; render(); };
$("next").onclick = () => { page++; render(); };
document.querySelectorAll("[data-export]").forEach(button => button.onclick = () => exportFile(button.dataset.export));
window.addEventListener("storage", event => {
  if (event.key !== STORAGE_KEY) return;
  saved = readSaved(); updateSavedCount();
  if (view === "saved") loadPapers(); else renderGroups();
});

(async () => {
  try {
    manifest = await jsonFile("manifest.json");
    if (manifest.schema_version !== 1 || !manifest.groups?.length) throw new Error("论文目录为空或版本不受支持。");
    const initialGroup = manifest.groups.find(group => group.abstract_count > 0) || manifest.groups[0];
    chosenConferences.add(initialGroup.conference);
    conferenceTree(); updateSavedCount();
    $("filterYear").replaceChildren(new Option("全部年份", ""),
      ...[...new Set(manifest.groups.map(g => g.year))].sort((a, b) => b - a).map(year => new Option(year, year)));
    $("updated").textContent = "数据更新于 " + new Date(manifest.updated_at).toLocaleString("zh-CN");
    await loadPapers();
  } catch (error) {
    $("loading").textContent = ""; notice(error.message);
  }
})();
