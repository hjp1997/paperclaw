"use strict";
const $ = id => document.getElementById(id);
const {el, link, paperTitle} = window.PaperClawUI;
const PAGE_SIZE = 25;
const STORAGE_SCOPE = new URL(".", location.href).pathname;
const STORAGE_KEY = "paperclaw:papers:" + STORAGE_SCOPE;
const LEGACY_STORAGE_KEY = "paperclaw:collections:" + STORAGE_SCOPE;
const selected = new Set(), chosenConferences = new Set(), expanded = new Set();
const cache = new Map(), paperIndex = new Map();
const sources = {openalex:"OpenAlex", openreview:"OpenReview", conference_site:"会议官网", paper_website:"论文官网",
  arxiv:"arXiv", acl_anthology:"ACL Anthology", acl_program:"ACL 官网", ieee:"IEEE", usenix:"USENIX",
  crossref:"Crossref", dblp:"DBLP", dblp_rdf:"DBLP"};
let manifest, papers = [], currentGroups = [], view = "all", page = 1, generation = 0, loading = false;
let bookmarkState = readSaved();
let saved = bookmarkState.papers, legacyGroups = bookmarkState.legacyGroups;
let cloudTerm = null;
const regularLabels = new Set(["poster", "regular", "accept", "accept (regular)", "accept (poster)", "main", "main track", "research track", "full paper"]);
const genericWords = new Set(["paper", "propose", "proposed", "approach", "method", "methods", "model", "models", "learning", "using", "based", "results", "novel", "efficient", "framework", "data", "study", "analysis", "performance", "problem", "large", "neural", "deep", "training", "new", "via", "test", "general", "towards", "networks", "network"]);

function featured(paper) {
  return (paper.categories || []).some(label => label.trim() && !regularLabels.has(label.trim().toLowerCase()));
}

function cloudTerms(paper, mode) {
  if (mode === "topics") return [...new Set(paper.ai_analysis?.topics || [])];
  return [...new Set((paper.ai_analysis?.keyphrases || paper.keywords || []).map(word => word.trim().toLowerCase()).filter(word =>
    word.length > 2 && word.length <= 60 && !genericWords.has(word)))];
}

function renderCloud() {
  const mode = $("cloudSource").value, counts = new Map();
  let covered = 0;
  for (const paper of filtered(true)) {
    const words = cloudTerms(paper, mode);
    if (words.length) covered++;
    for (const word of words) counts.set(word, (counts.get(word) || 0) + 1);
  }
  const terms = [...counts].sort((a,b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0,40);
  const max = terms[0]?.[1] || 1;
  $("wordCloud").replaceChildren(...terms.map(([word,count]) => {
    const label = mode === "topics" ? (manifest.topics?.[word] || word) : word;
    const button = el("button", "cloud-word weight-" + Math.round(4 * Math.log(count + 1) / Math.log(max + 1)));
    button.type = "button"; button.append(label, el("sup", "", count));
    button.setAttribute("aria-label", label + "，" + count + " 篇论文");
    button.setAttribute("aria-pressed", String(cloudTerm?.mode === mode && cloudTerm.id === word));
    button.onclick = () => { cloudTerm = cloudTerm?.mode === mode && cloudTerm.id === word ? null : {mode,id:word,label}; page = 1; render(); };
    return button;
  }));
  if (!terms.length) $("wordCloud").append(el("p", "help", mode === "topics" ? "当前范围尚无 AI Topic。" : "当前范围暂无关键词。"));
  $("cloudInfo").textContent = covered + " 篇覆盖 · " + terms.length + " 个索引";
  $("wordCloud").title = mode === "topics" ? "标签来自预生成 AI Topic" : "优先使用 AI 提取的研究短语，其余使用论文元数据或本地关键词";
  $("clearCloud").hidden = !cloudTerm;
  $("clearCloud").textContent = cloudTerm ? "清除：" + cloudTerm.label : "清除词云筛选";
}

function notice(text) {
  $("notice").textContent = text;
  $("notice").hidden = !text;
}

function readSaved() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw !== null) {
      const value = JSON.parse(raw);
      return {
        papers:new Set(Array.isArray(value.papers) ? value.papers.filter(key => typeof key === "string" && key.includes("|")) : []),
        legacyGroups:new Set(Array.isArray(value.legacy_groups) ? value.legacy_groups.filter(key => typeof key === "string") : [])
      };
    }
    const old = JSON.parse(localStorage.getItem(LEGACY_STORAGE_KEY) || "[]");
    return {papers:new Set(), legacyGroups:new Set(Array.isArray(old) ? old.filter(key => typeof key === "string") : [])};
  } catch {
    return {papers:new Set(), legacyGroups:new Set()};
  }
}

function saveBookmarks(next, remainingGroups = legacyGroups) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({version:1, papers:[...next], legacy_groups:[...remainingGroups]}));
    saved = next;
    legacyGroups = remainingGroups;
    updateSavedCount();
    return true;
  } catch {
    notice("浏览器未能保存收藏，请检查是否允许此网站保存本地数据。");
    return false;
  }
}

async function restoreLegacyBookmarks() {
  const groups = manifest.groups.filter(group => legacyGroups.has(group.key));
  if (!groups.length) return true;
  const results = await Promise.allSettled(groups.map(collection));
  const next = new Set(saved), pending = new Set(legacyGroups);
  results.forEach((result,index) => {
    if (result.status !== "fulfilled") return;
    for (const paper of result.value) next.add(paper._key);
    pending.delete(groups[index].key);
  });
  return saveBookmarks(next,pending) && results.every(result => result.status === "fulfilled");
}

function saveSelectedPapers() {
  const next = new Set(saved);
  for (const key of selected) next.add(key);
  if (saveBookmarks(next)) { notice("已收藏所选论文。"); render(); }
}

function removeSelectedPapers() {
  const next = new Set(saved);
  for (const key of selected) next.delete(key);
  if (!saveBookmarks(next)) return;
  for (const key of [...selected]) if (!next.has(key)) selected.delete(key);
  if (view === "saved") loadPapers(); else render();
}

function updateSavedCount() {
  $("savedView").textContent = "我的收藏（" + saved.size + " 篇）";
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
  const savedGroups = new Set([...saved].map(key => key.split("|")[0]));
  return manifest.groups.filter(group => (!year || String(group.year) === year) &&
    (view === "saved" ? savedGroups.has(group.key) : chosenConferences.has(group.conference)));
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
    papers = results.flat().filter(p => view !== "saved" || saved.has(p._key));
    renderGroups();
    const category = $("filterCategory").value;
    const labels = [...new Set(papers.flatMap(p => p.categories || []))].sort();
    $("filterCategory").replaceChildren(new Option("全部分类", ""), new Option("特别分类", "__featured__"), new Option("暂无分类标签", "__none__"),
      ...labels.map(label => new Option(label, label)));
    if ([...$("filterCategory").options].some(option => option.value === category)) $("filterCategory").value = category;
    const topic = $("filterTopic").value;
    const topics = [...new Set(papers.flatMap(p => p.ai_analysis?.topics || []))].sort();
    $("filterTopic").replaceChildren(new Option("全部 Topic", ""), new Option("尚未分析", "__none__"),
      ...topics.map(id => new Option(manifest.topics?.[id] || id, id)));
    if ([...$("filterTopic").options].some(option => option.value === topic)) $("filterTopic").value = topic;
  } catch (error) {
    if (version === generation) notice(error.message);
  } finally {
    if (version === generation) { loading = false; render(); }
  }
}

function filtered(ignoreCloud = false) {
  const text = $("search").value.trim().toLowerCase(), category = $("filterCategory").value, topic = $("filterTopic").value;
  return papers.filter(p => (!category || (category === "__featured__" ? featured(p) : category === "__none__" ? !p.categories?.length : p.categories?.includes(category))) &&
    (!topic || (topic === "__none__" ? !p.ai_analysis : p.ai_analysis?.topics.includes(topic))) &&
    (ignoreCloud || !cloudTerm || cloudTerms(p,cloudTerm.mode).includes(cloudTerm.id)) &&
    (!text || [p.title, p.title_text, p.abstract, p.doi, ...p.authors, ...p.keywords,
      ...(p.ai_analysis?.topics || []).map(id => manifest.topics?.[id] || id),
      ...(p.ai_analysis?.keyphrases || []),
      ...["problem", "method", "results"].map(field => p.ai_analysis?.[field] || "")].join(" ").toLowerCase().includes(text)));
}

function renderGroups() {
  $("groups").replaceChildren(...currentGroups.map(group => {
    const row = el("div", "collection-row");
    row.append(el("span", "collection-name", group.conference + " " + group.year),
      el("span", "collection-count", group.count + " 篇 · " + group.abstract_count + " 篇有摘要" +
        (group.analysis_count ? " · " + group.analysis_count + " 篇已分析" : "")));
    if (view === "saved") {
      const count = papers.filter(p => p._key.startsWith(group.key + "|")).length;
      row.replaceChildren(el("span", "collection-name", group.conference + " " + group.year),
        el("span", "collection-count", count + " 篇收藏"));
    }
    return row;
  }));
  if (view === "all") {
    const year = $("filterYear").value;
    const published = new Set(manifest.groups.map(group => group.key));
    for (const state of manifest.collection_status || []) {
      if (!chosenConferences.has(state.conference) || (year && String(state.year) !== year) || published.has(state.key)) continue;
      const labels = {unavailable:"尚未收录",failed:"暂未采集成功",pending:"待采集",available:"数据待发布"};
      const row = el("div", "collection-row collection-pending");
      row.append(el("span", "collection-name", state.conference + " " + state.year), el("span", "collection-count", labels[state.status] || "待采集"));
      if (state.reason) row.append(el("span", "collection-count", state.reason));
      $("groups").append(row);
    }
  }
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
  if (saved.has(p._key)) top.append(el("span","saved-mark","已收藏"));
  for (const category of p.categories || []) {
    const tag = link(category, p.category_sources?.[category]);
    tag.className = "distinction";
    top.append(tag);
  }
  card.append(top, paperTitle(p), el("p", "authors", p.authors.join(" · ")));
  const keywords = el("div", "keywords");
  for (const word of p.ai_analysis?.keyphrases || p.keywords) {
    const chip = el(p.ai_analysis?.keyphrases ? "button" : "span", "keyword", word);
    if (p.ai_analysis?.keyphrases) {
      chip.type = "button"; chip.title = "AI 提取的原文研究短语 · 点击筛选论文";
      chip.onclick = () => { $("cloudSource").value = "keywords"; cloudTerm = {mode:"keywords",id:word.toLowerCase(),label:word}; page = 1; render(); };
    }
    keywords.append(chip);
  }
  card.append(keywords);
  if (p.ai_analysis) {
    const topics = el("div", "ai-topics");
    topics.setAttribute("aria-label", "AI Topic");
    for (const id of p.ai_analysis.topics) {
      const chip = el("button", "ai-topic", manifest.topics?.[id] || id);
      chip.type = "button";
      chip.title = "按此 AI Topic 筛选" + (p.ai_analysis.topic_evidence?.[id] ? " · " + p.ai_analysis.topic_evidence[id] : "");
      chip.onclick = () => { $("filterTopic").value = id; page = 1; render(); };
      topics.append(chip);
    }
    card.append(topics);
  }
  const bottom = el("div", "paper-bottom");
  if (p.publisher_url || p.url) bottom.append(link("论文页面 ↗", p.publisher_url || p.url));
  if (p.pdf_url) bottom.append(link("PDF ↗", p.pdf_url));
  if (p.dblp_url) bottom.append(link("DBLP ↗", p.dblp_url));
  const chat = el("button", "paper-chat", "与 AI 讨论");
  chat.type = "button"; chat.onclick = () => window.PaperClawChat.open(p); bottom.append(chat);
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
  if (p.ai_analysis) {
    const analysis = el("details", "ai-analysis"), key = p._key + "|analysis";
    analysis.open = expanded.has(key);
    analysis.addEventListener("toggle", () => {
      if (analysis.open) expanded.add(key); else expanded.delete(key);
    });
    analysis.append(el("summary", "", "AI 总结 · 摘要级"));
    const content = el("dl");
    for (const [field, label] of [["problem", "研究问题"], ["method", "方法"], ["results", "结果"]]) {
      content.append(el("dt", "", label), el("dd", "", p.ai_analysis[field]));
    }
    analysis.append(content, el("p", "help", p.ai_analysis.provider + " · " + p.ai_analysis.model + " · " +
      new Date(p.ai_analysis.generated_at).toLocaleDateString("zh-CN")));
    card.append(analysis);
  }
  return card;
}

function renderSelection(rows) {
  $("selectionInfo").textContent = selected.size ? "已勾选 " + selected.size + " 篇，导出所选论文" : "导出筛选的 " + rows.length + " 篇";
  $("clearSelection").disabled = !selected.size;
  const allSaved = selected.size > 0 && [...selected].every(key => saved.has(key));
  $("saveSelected").disabled = !selected.size || allSaved;
  $("saveSelected").textContent = allSaved ? "所选论文已收藏" : "收藏已勾选的论文";
  $("removeSelected").hidden = view !== "saved";
  $("removeSelected").disabled = ![...selected].some(key => saved.has(key));
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
  if (manifest) renderCloud();
  const cards = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE).map(paperCard);
  if (!cards.length) {
    let empty = "没有匹配的论文，请调整会议或筛选条件。";
    if (loading) empty = "正在读取论文数据…";
    else if (view === "saved" && !rows.length) empty = saved.size || legacyGroups.size ? "当前筛选或发布数据中暂未收录这些收藏。" : "还没有收藏论文。";
    cards.push(el("div", "empty", empty));
  }
  $("papers").replaceChildren(...cards);
}

function conferenceTree() {
  const available = new Set(manifest.groups.map(g => g.conference));
  for (const state of manifest.collection_status || []) available.add(state.conference);
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
      label.append(box, venue.name, el("span", "venue-count", count || "待收录"));
      children.append(label);
    }
    group.append(children);
    $("conferenceChoices").append(group);
  }
}

function setView(next) {
  view = next; selected.clear(); cloudTerm = null; $("search").value = ""; $("filterYear").value = ""; $("filterCategory").value = ""; $("filterTopic").value = "";
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
    const csvRow = row => ({...row, topics:(row.ai_analysis?.topics || []).map(id => manifest.topics?.[id] || id),
      ai_keyphrases:row.ai_analysis?.keyphrases,
      ai_problem:row.ai_analysis?.problem, ai_method:row.ai_analysis?.method,
      ai_results:row.ai_analysis?.results, ai_model:row.ai_analysis?.model});
    return "\uFEFF" + [fields.map(cell).join(","), ...clean.map(csvRow).map(row => fields.map(field => cell(row[field])).join(","))].join("\r\n") + "\r\n";
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
      labels, "", escape(p.abstract) || "来源尚未提供摘要。", "",
      ...(p.ai_analysis ? ["### AI 总结 · 摘要级", "",
        "- Topic · " + p.ai_analysis.topics.map(id => escape(manifest.topics?.[id] || id)).join("; "),
        "- 研究关键词 · " + (p.ai_analysis.keyphrases || []).map(escape).join("; "),
        "- 研究问题 · " + escape(p.ai_analysis.problem), "- 方法 · " + escape(p.ai_analysis.method),
        "- 结果 · " + escape(p.ai_analysis.results),
        "- 模型 · " + escape(p.ai_analysis.provider + " / " + p.ai_analysis.model), ""] : [])
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
$("cloudSource").onchange = () => { cloudTerm = null; page = 1; render(); };
$("clearCloud").onclick = () => { cloudTerm = null; page = 1; render(); };
for (const id of ["search", "filterCategory", "filterTopic"]) $(id).addEventListener("input", () => { page = 1; render(); });
$("selectFiltered").onclick = () => { for (const paper of filtered()) selected.add(paper._key); render(); };
$("clearSelection").onclick = () => { selected.clear(); render(); };
$("saveSelected").onclick = saveSelectedPapers;
$("removeSelected").onclick = removeSelectedPapers;
$("prev").onclick = () => { page--; render(); };
$("next").onclick = () => { page++; render(); };
document.querySelectorAll("[data-export]").forEach(button => button.onclick = () => exportFile(button.dataset.export));
window.addEventListener("storage", async event => {
  if (event.key !== STORAGE_KEY && event.key !== LEGACY_STORAGE_KEY && event.key !== null) return;
  bookmarkState = readSaved(); saved = bookmarkState.papers; legacyGroups = bookmarkState.legacyGroups;
  const restored = await restoreLegacyBookmarks();
  updateSavedCount();
  if (view === "saved") await loadPapers(); else render();
  if (!restored) notice("旧收藏暂未恢复完成，请稍后刷新重试。");
});

(async () => {
  try {
    manifest = await jsonFile("manifest.json");
    if (manifest.schema_version !== 1 || !manifest.groups?.length) throw new Error("论文目录为空或版本不受支持。");
    const initialGroup = manifest.groups.find(group => group.abstract_count > 0) || manifest.groups[0];
    chosenConferences.add(initialGroup.conference);
    conferenceTree(); updateSavedCount();
    const restored = await restoreLegacyBookmarks();
    $("filterYear").replaceChildren(new Option("全部年份", ""),
      ...[...new Set([...manifest.groups, ...(manifest.collection_status || [])].map(g => g.year))].sort((a, b) => b - a).map(year => new Option(year, year)));
    $("updated").textContent = "数据更新于 " + new Date(manifest.updated_at).toLocaleString("zh-CN");
    await loadPapers();
    if (!restored) notice("旧收藏暂未恢复完成，请稍后刷新重试。");
  } catch (error) {
    $("loading").textContent = ""; notice(error.message);
  }
})();
