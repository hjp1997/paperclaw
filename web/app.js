"use strict";
const $ = id => document.getElementById(id);
const {el, link, paperTitle} = window.PaperClawUI;
let token = "", papers = [], revision = -1, page = 1, aiBusy = false, running = false;
const PAGE_SIZE = 25;
let view = "current", viewGeneration = 0;
const selected = new Set();
let favorites = [];
let abstractSubmitting = false, abstractTask = false, abstractMessage = "";
let metadataBusy = "";
const openAbstracts = new Set();
const sourceLabels = {openalex:"OpenAlex",openreview:"OpenReview",conference_site:"会议官网",paper_website:"论文官网",arxiv:"arXiv",acl_anthology:"ACL Anthology",acl_program:"ACL 官网",crossref:"Crossref",usenix:"USENIX",ieee:"IEEE",dblp_rdf:"DBLP",dblp:"DBLP"};
let venues = [];
const confirmedPublishers = new Set();
const statusLabels = {idle:"准备就绪",running:"正在采集",completed:"采集完成",partial:"采集中断",stopped:"已停止",interrupted:"已恢复上次结果",pending:"等待中",not_run:"未执行",failed:"失败"};
const keywordLabels = {local_title:"标题提取",local_title_abstract:"标题 / 摘要提取",author_terms:"作者关键词",ieee_terms:"IEEE 关键词"};
function notice(text) { $("notice").textContent = text; $("notice").hidden = !text; }
async function api(path, body, blob = false) {
  const response = await fetch(path, {method:body === undefined ? "GET" : "POST", headers:{"X-PaperClaw-Token":token,...(body === undefined ? {} : {"Content-Type":"application/json"})}, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  if (!response.ok) { const error = await response.json(); throw new Error(error.error || `HTTP ${response.status}`); }
  return blob ? response.blob() : response.json();
}
function options() {
  const from = Number($("yearFrom").value), to = Number($("yearTo").value);
  if (!Number.isInteger(from) || !Number.isInteger(to) || from < 1980 || to > Number($("yearTo").max) || from > to || to-from >= 30) throw new Error("请选择有效的年份范围，最多 30 年。");
  return {source:$("source").value, conferences:[...document.querySelectorAll('input[name="conference"]:checked')].map(x=>x.value), years:Array.from({length:to-from+1},(_,i)=>from+i), include_workshops:$("workshops").checked,fetch_abstracts:$("fetchAbstracts").checked};
}
function filtered() {
  const q=$("search").value.toLowerCase().trim(), c=$("filterConference").value, y=$("filterYear").value;
  const category = $("filterCategory").value;
  return papers.filter(p=>(!c||p.conference===c)&&(!y||String(p.year)===y)&&
    (!category||(category==="__none__"?!p.categories?.length:p.categories?.includes(category)))&&
    (!q||[p.title,p.title_text,...p.authors,...p.keywords,p.abstract,p.doi].join(" ").toLowerCase().includes(q)));
}
function renderSelection(rows) {
  const count = selected.size || rows.length;
  $("selectionInfo").textContent = selected.size ? `已勾选 ${selected.size} 篇，导出所选论文` : `导出当前筛选的 ${rows.length} 篇`;
  $("clearSelection").disabled = !selected.size;
  $("selectFiltered").disabled = !rows.length || rows.every(p=>selected.has(p.id));
  document.querySelectorAll("[data-export]").forEach(b=>b.disabled=!count);
}
function renderAbstractAction(rows=filtered()) {
  const missing=rows.filter(p=>!p.abstract).length, button=$("fillAbstracts");
  button.disabled=running||abstractSubmitting||!missing;
  button.textContent=abstractSubmitting||abstractTask?"正在补全…":!rows.length?"补全摘要":missing?`补全摘要（${missing}）`:"摘要已齐全";
  const hint=!rows.length?"":!missing?`${rows.length}/${rows.length} 篇有摘要`:`缺少 ${missing} 篇`;
  button.title=running?"当前任务进行中":!missing?"当前筛选的论文都有摘要，无需补全":"查询 OpenReview 和 OpenAlex，补全缺失摘要";
  $("abstractHint").textContent=abstractMessage||hint;
}
function render() {
  const rows=filtered(), maxPage=Math.max(1,Math.ceil(rows.length/PAGE_SIZE)); page=Math.min(page,maxPage);
  $("visibleCount").textContent=rows.length; $("count").textContent=`${papers.length} 篇 · ${papers.filter(p=>p.abstract).length} 篇有摘要`;
  renderAbstractAction(rows);
  $("pageInfo").textContent=rows.length?`显示 ${(page-1)*PAGE_SIZE+1}–${Math.min(page*PAGE_SIZE,rows.length)} / ${rows.length}`:"";
  $("pageNumber").textContent=`${page} / ${maxPage}`; $("prev").disabled=page===1; $("next").disabled=page===maxPage;
  renderSelection(rows);
  if(!rows.length){$("papers").replaceChildren(el("div","empty",papers.length?"没有匹配的论文，请调整筛选。":view==="favorites"?"还没有收藏。在最近采集里按会议和年份收藏论文。":"选择会议和年份，然后开始采集。"));return;}
  const fragment=document.createDocumentFragment();
  for(const p of rows.slice((page-1)*PAGE_SIZE,page*PAGE_SIZE)) {
    const card=el("article","paper"), top=el("div","paper-top");
    top.append(el("span","tag",p.conference),el("span","",p.year),el("span","",p.track==="workshop"?"Workshop":"主会论文集"),el("span","tag source",sourceLabels[p.source]||p.source));
    const choose=el("label","paper-select"), box=el("input");
    box.type="checkbox"; box.checked=selected.has(p.id); box.setAttribute("aria-label","选择论文 "+(p.title_text||p.title));
    box.onchange=()=>{if(box.checked)selected.add(p.id);else selected.delete(p.id);renderSelection(rows);};
    choose.append(box,"选择"); top.prepend(choose);
    for(const category of p.categories||[]) {
      const tag=link(category,p.category_sources?.[category]); tag.className="distinction"; top.append(tag);
    }
    card.append(top,paperTitle(p));
    card.append(el("p","authors",p.authors.join(" · ")));
    const kws=el("div","keywords");p.keywords.forEach(k=>kws.append(el("span","keyword",k)));kws.append(el("span","keyword-label",keywordLabels[p.keyword_source]||p.keyword_source));card.append(kws);
    const bottom = el("div", "paper-bottom");
    const venue = venues.find(entry => entry.name === p.conference);
    if (p.publisher_url && venue) bottom.append(link((p.source==="conference_site"?"会议官网":venue.publisher) + " · 论文 ↗", p.publisher_url));
    else if (p.url) bottom.append(link("论文页面 ↗", p.url));
    if (p.pdf_url) bottom.append(link("PDF 链接 ↗", p.pdf_url));
    if (p.dblp_url) bottom.append(link("DBLP ↗", p.dblp_url));
    const update=el("button","source-refresh",metadataBusy===p.id?"更新中…":"更新来源摘要");
    update.disabled=running||Boolean(metadataBusy);
    update.onclick=()=>refreshPaper(p.id);
    bottom.append(update);
    const analyze=el("button","",p.ai_summary?"重新分析摘要":"分析摘要");analyze.disabled=!p.abstract||aiBusy;analyze.title=p.abstract?"发送标题与摘要到你配置的 AI 接口":"暂无摘要，可点击上方补全摘要";analyze.addEventListener("click",()=>analyzePaper(p.id));bottom.append(analyze);card.append(bottom);
    if(p.abstract){
      const detail=el("details","abstract"), summary=el("summary","",`摘要 · ${sourceLabels[p.abstract_source]||p.abstract_source||p.source}`);
      detail.open=openAbstracts.has(p.id);
      detail.addEventListener("toggle",()=>{if(detail.open)openAbstracts.add(p.id);else openAbstracts.delete(p.id);});
      detail.append(summary,el("p","abstract-preview",p.abstract));
      if(p.abstract_url)detail.append(link("摘要来源 ↗",p.abstract_url));card.append(detail);
    }
    else card.append(el("p","help abstract-missing",p.abstract_status==="unavailable"?"摘要：来源未提供。":"摘要：尚未获取，可点击“补全筛选结果的摘要”。"));
    if(p.ai_summary){const detail=el("details");detail.append(el("summary","","AI 摘要分析"),el("p","summary",p.ai_summary));card.append(detail);}
    fragment.append(card);
  }
  $("papers").replaceChildren(fragment);
}
function selectFailedGroup(group, source) {
  for (const input of document.querySelectorAll('input[name="conference"]')) {
    input.checked = input.value === group.conference;
  }
  $("yearFrom").value = $("yearTo").value = group.year;
  syncConferenceTree();
  $("source").value = source;
  $("source").dispatchEvent(new Event("change"));
  const control = source === "ieee" ? $("ieeeKey") : source === "publisher" ? $("publisherFlow") : $("source");
  control.scrollIntoView({block:"center"});
  if (source === "ieee") control.focus();
}

function groupRow(group) {
  const row = el("div", "group");
  row.append(el("span", "label", group.conference + " " + group.year));
  let counts = (view==="favorites"?"已收藏":group.status==="imported"?"已导入":statusLabels[group.status]) + " · " + group.count + " 篇";
  if (group.total != null) counts += " · 来源进度 " + group.fetched + "/" + group.total;
  row.append(el("span", "group-counts", counts));
  if (group.count) {
    const saved=favorites.some(g=>g.conference===group.conference&&g.year===group.year);
    const actions=el("div","group-actions");
    const button=(name, action, args)=>{const b=el("button","",name);b.disabled=running;
      b.onclick=async()=>{b.disabled=true;try{const result=await api(action,{conference:group.conference,year:group.year,view,...args});
        await refresh();notice(result.message||"");}catch(error){notice(error.message);}finally{b.disabled=running;}};
      actions.append(b);};
    if (view==="current") button(saved?"更新收藏":"收藏本组","/api/favorite",{keep:true});
    if(saved) button("取消收藏","/api/favorite",{keep:false});
    if(group.program_available) button("更新论文分类","/api/program",{});
    row.append(actions);
  }
  if(group.classification_note)row.append(el("span","help",group.classification_note));
  if (group.error) row.append(el("span", "error", group.error));
  const issue = group.access_issue;
  if (issue) {
    if (issue.source.startsWith("openreview")) {
      const venue=venues.find(v=>v.name===group.conference);
      if(venue?.openreview_prefix)row.append(link("打开 OpenReview ↗","https://openreview.net/group?"+new URLSearchParams({id:`${venue.openreview_prefix}/${group.year}`})));
      if(venue?.official_supported){
        const official=el("button","","选择会议官网");official.onclick=()=>selectFailedGroup(group,"conference");row.append(official);
      }
      const fallback=el("button","","选择 DBLP");
      fallback.onclick=()=>selectFailedGroup(group,"dblp");row.append(fallback);
      return row;
    }
    if (issue.kind === "api_key" && issue.source === "ieee") {
      const update = el("button", "", "更新 IEEE API Key");
      update.onclick = () => selectFailedGroup(group, "ieee");
      row.append(update);
    }
    const open = el("button", "", "在出版社网页继续");
    open.onclick = () => selectFailedGroup(group, "publisher");
    row.append(open);
  }
  return row;
}

function renderState(state) {
  running=state.status==="running";$("refreshCatalog").disabled=running;
  const wasAbstractTask=abstractTask;
  abstractTask=running&&state.operation==="abstracts";
  if(abstractTask||wasAbstractTask)abstractMessage=state.message;
  if(!running&&wasAbstractTask)notice(state.message);
  $("statusTitle").textContent=statusLabels[state.status]||state.status;$("statusDot").className=`dot ${state.status}`;$("message").textContent=state.message;
  $("start").disabled=state.status==="running";$("stop").disabled=state.status!=="running";$("importButton").disabled=state.status==="running";
  $("importCitation").disabled = running;
  if(state.papers){
    papers=state.papers;revision=state.revision;favorites=state.favorites||[];
    const ids=new Set(papers.map(p=>p.id));for(const id of selected)if(!ids.has(id))selected.delete(id);
    const setOptions=(id,opts)=>{const select=$(id),previous=select.value;select.replaceChildren(...opts);if(opts.some(o=>o.value===previous))select.value=previous;};
    setOptions("filterYear",[new Option("全部年份",""),...[...new Set(papers.map(p=>p.year))].sort((a,b)=>b-a).map(y=>new Option(y,y))]);
    setOptions("filterCategory",[new Option("全部分类",""),new Option("暂无来源标签","__none__"),...[...new Set(papers.flatMap(p=>p.categories||[]))].sort().map(label=>new Option(label,label))]);
    $("groups").replaceChildren(...(view==="current"?state.groups:favorites).map(groupRow));
    $("savedView").textContent=`我的收藏（${favorites.length} 组）`;
    $("resetCurrent").hidden=view!=="current";$("resetCurrent").disabled=running||!(state.groups.length||papers.length);
    render();
  }
  $("groups").querySelectorAll("button").forEach(button=>button.disabled=running);
  renderAbstractAction();
}
async function refresh() {
  const generation=viewGeneration, state=await api(`/api/state?revision=${revision}&view=${view}`);
  if(generation===viewGeneration&&state.revision>=revision)renderState(state);
}
async function showView(next) {
  view=next;viewGeneration++;revision=-1;page=1;selected.clear();papers=[];
  if(!abstractTask)abstractMessage="";
  $("groups").replaceChildren();$("search").value="";$("filterYear").value="";$("filterConference").value="";$("filterCategory").value="";
  $("currentView").setAttribute("aria-pressed",String(view==="current"));$("savedView").setAttribute("aria-pressed",String(view==="favorites"));
  render();await refresh();
}
$("currentView").onclick=()=>showView("current").catch(e=>notice(e.message));
$("savedView").onclick=()=>showView("favorites").catch(e=>notice(e.message));
$("resetCurrent").onclick=async()=>{if(!window.confirm("清空最近一轮采集结果？已收藏的会议仍可在“我的收藏”中查看。"))return;
  try{await api("/api/reset",{});await showView("current");}catch(e){notice(e.message);}};
$("selectFiltered").onclick=()=>{for(const p of filtered())selected.add(p.id);render();};
$("clearSelection").onclick=()=>{selected.clear();render();};
async function poll() {try{await refresh();}catch(error){$("message").textContent=`本地服务连接失败：${error.message}。请保持启动窗口开启。`;}finally{setTimeout(poll,1500);}}
$("crawlForm").addEventListener("submit", async event => {
  event.preventDefault();
  if ($("source").value === "publisher") return;
  notice("");
  try {
    $("start").disabled = true;
    await api("/api/start", {...options(), ieee_key:$("ieeeKey").value, openalex_key:$("openalexKey").value});
    await showView("current");
  } catch (error) {
    notice(error.message);
    $("start").disabled = false;
  }
});
$("stop").addEventListener("click",async()=>{try{await api("/api/stop",{});$("message").textContent="正在停止；若请求已发出，将等待当前请求返回（最多约 40 秒）。";}catch(error){notice(error.message);}});
const sourceInfo = {
  auto:["主会优先核对 OpenReview，遇到接口验证时尝试已适配的会议官网。NSDI 使用 USENIX，IEEE / ACM 会议使用 Crossref，其余使用 DBLP。","https://openreview.net/"],
  openreview:["读取公开的已接收论文、摘要和报告类型，自动发现当届主会各分轨。支持 ICLR、ICML、NeurIPS、ACL、AAAI、CVPR、ICCV、SIGIR、SIGKDD。","https://openreview.net/"],
  conference:["读取 ICLR、ICML、NeurIPS、CVPR、ICCV 官网的论文目录与摘要。","https://iclr.cc/"],
  publisher:["在官网使用个人或机构账号登录，回到这里打开所选会议的查询链接。导出 BibTeX 后可在下方合并。",""],
  usenix:["从 NSDI、FAST 和旧版 ATC 官方技术议程获取题名、作者与摘要。","https://www.usenix.org/conferences"],
  crossref:["通过 Crossref 查询 IEEE / ACM 登记的会议论文集。","https://search.crossref.org/"],
  ieee:["通过 IEEE Metadata API 查询 INFOCOM、ICDE、FOCS、RTSS、CVPR 或 ICCV，请填写 API Key。","https://ieeexplore.ieee.org/"],
  dblp:["查询 DBLP 会议目录，摘要可在采集后按 DOI 补全。","https://dblp.org/"]
};

function searchUrl(venue, year) {
  if (["NSDI","FAST"].includes(venue.name) || venue.name === "ACM SIGOPS ATC" && year <= 2025) {
    const slug = {NSDI:"nsdi",FAST:"fast","ACM SIGOPS ATC":"atc"}[venue.name];
    return "https://www.usenix.org/conference/" + slug + String(year).slice(-2) + "/technical-sessions";
  }
  if (venue.prefix === "10.1145") {
    return "https://dl.acm.org/action/doSearch?" + new URLSearchParams({AllField:venue.name + " " + year});
  }
  if (venue.prefix === "10.1109") return "https://ieeexplore.ieee.org/search/searchresult.jsp?" +
    new URLSearchParams({queryText:venue.name, ranges:year + "_" + year + "_Year"});
  return venue.dblp_url;
}

function ownPublisherLink(venue) {
  const field = el("label", "field", "已有会议或论文链接");
  const input = el("input");
  input.type = "url";
  input.placeholder = venue.publisher_site;
  field.append(input);
  const destination = link("打开所填链接 ↗", venue.publisher_site);
  destination.hidden = true;
  const hint = el("small", "help");
  const host = new URL(venue.publisher_site).hostname.split(".").slice(-2).join(".");
  input.addEventListener("input", () => {
    destination.hidden = true;
    hint.textContent = "";
    if (!input.value.trim()) return;
    try {
      const url = new URL(input.value.trim());
      if (!["https:", "http:"].includes(url.protocol) || url.username || url.password ||
          (url.hostname !== host && !url.hostname.endsWith("." + host))) throw new Error();
      destination.href = url.href;
      destination.hidden = false;
    } catch { hint.textContent = "请输入 " + host + " 的完整网页地址。"; }
  });
  const row = el("div");
  row.append(field, destination, hint);
  return row;
}

function publisherSteps() {
  const container = $("publisherSteps");
  container.replaceChildren();
  if ($("source").value !== "publisher") return;
  let scope;
  try { scope = options(); } catch (error) { container.textContent = error.message; return; }
  if (!scope.conferences.length) { container.textContent = "请先选择会议。"; return; }
  const selected = venues.filter(v => scope.conferences.includes(v.name));
  for (const publisher of new Set(selected.map(v => v.publisher))) {
    const entries = selected.filter(v => v.publisher === publisher);
    const box = el("div", "publisher-step");
    const portal = entries[0].publisher_site;
    const loginUrl = publisher === "USENIX" ? portal + "user/login" : portal;
    box.append(link("打开 " + publisher + " 登录 ↗", loginUrl));
    const confirm = el("button", "", "我已登录，显示会议查询");
    confirm.type = "button";
    confirm.onclick = () => { confirmedPublishers.add(publisher); publisherSteps(); };
    if (!confirmedPublishers.has(publisher)) box.append(confirm);
    else {
      for (const venue of entries) {
        if (searchUrl(venue, scope.years[0]) === venue.dblp_url) {
          box.append(link(venue.name + " · 历届论文集 ↗", venue.dblp_url));
        } else {
          for (const year of scope.years) box.append(link(venue.name + " " + year + " · 官网查询 ↗", searchUrl(venue, year)));
        }
      }
      box.append(ownPublisherLink(entries[0]));
    }
    container.append(box);
  }
}

$("source").addEventListener("change", () => {
  const source = $("source").value;
  const manual = source === "publisher";
  $("publisherFlow").hidden = !manual;
  $("start").hidden = manual;
  $("fetchAbstracts").closest("label").hidden = manual;
  $("ieeeKeyField").hidden = source !== "ieee";
  $("sourceHint").textContent = sourceInfo[source][0];
  $("sourceLink").parentElement.hidden = manual;
  $("sourceLink").href = sourceInfo[source][1];
  publisherSteps();
});
for (const id of ["conferenceChoices", "yearFrom", "yearTo"]) $(id).addEventListener("input", publisherSteps);
$("importCitation").addEventListener("click", async () => {
  try {
    const file = $("citationFile").files[0];
    if (!file) throw new Error("请先选择出版社导出的 BibTeX 文件。");
    if (file.size > 20 * 1024 * 1024) throw new Error("引用文件不能超过 20 MB。");
    const result = await api("/api/import", {...options(), source:"publisher", data:await file.text()});
    notice("文件含 " + result.source_records + " 条会议论文引用，匹配并合并 " + result.imported + " 条。");
    await showView("current");
  } catch (error) { notice(error.message); }
});
for(const id of ["search","filterConference","filterYear","filterCategory"])$(id).addEventListener("input",()=>{page=1;if(!abstractTask)abstractMessage="";render();});
$("prev").addEventListener("click",()=>{page--;render();});$("next").addEventListener("click",()=>{page++;render();});
async function exportFile(format) {
  notice(""); const ids=selected.size?[...selected]:filtered().map(p=>p.id), exportView=view; if(!ids.length)return;
  const name=`conference-papers-${new Date().toISOString().slice(0,10)}-${Date.now()}.${format}`;
  const mime={csv:"text/csv",md:"text/markdown",json:"application/json"}[format];
  try {
    // Acquire picker synchronously within the click gesture, before awaiting fetch.
    const handle=window.showSaveFilePicker?await window.showSaveFilePicker({suggestedName:name,types:[{description:format.toUpperCase(),accept:{[mime]:[`.${format}`]}}]}):null;
    const blob=await api("/api/export",{format,ids,view:exportView},true);
    if(handle){const writer=await handle.createWritable();await writer.write(blob);await writer.close();notice(`已保存 ${ids.length} 篇论文：${handle.name}`);}
    else {const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);notice("已交给浏览器下载；保存位置由浏览器下载设置决定。");}
  } catch(error) {if(error.name!=="AbortError")notice(`导出失败：${error.message}`);}
}
document.querySelectorAll("[data-export]").forEach(b=>b.addEventListener("click",()=>exportFile(b.dataset.export)));
async function analyzePaper(id) {
  notice("");
  if(!$("aiBase").value||!$("aiModel").value){$("aiSettings").open=true;($("aiBase").value?$("aiModel"):$("aiBase")).focus();notice("请先填写摘要分析使用的接口地址和模型。");return;}
  try{aiBusy=true;render();await api("/api/analyze",{id,view,base_url:$("aiBase").value,model:$("aiModel").value,key:$("aiKey").value});await refresh();}
  catch(error){notice(error.message);}finally{aiBusy=false;render();}
}
async function refreshPaper(id) {
  metadataBusy=id;notice("");render();
  try{
    const result=await api("/api/paper/refresh",{id,view});
    openAbstracts.add(id);
    await refresh();notice(result.message);
  }catch(error){notice(error.message);}
  finally{metadataBusy="";render();}
}
$("importButton").addEventListener("click",async()=>{notice("");try{const file=$("importFile").files[0];if(!file)throw new Error("请先选择来源 JSON 文件。");if(file.size>20*1024*1024)throw new Error("文件不能超过 20 MB。");await api("/api/import",{...options(),data:JSON.parse(await file.text())});await showView("current");}catch(error){notice(error.message);}});
$("fillAbstracts").addEventListener("click",async()=>{
  notice("");abstractSubmitting=true;abstractMessage="正在提交补全请求";renderAbstractAction();
  try{
    const result=await api("/api/abstracts",{ids:filtered().map(p=>p.id),view,openalex_key:$("openalexKey").value});
    abstractTask=result.started;abstractMessage=result.message;notice(result.message);await refresh();
  }catch(error){notice(error.message);abstractMessage=error.message;}
  finally{abstractSubmitting=false;renderAbstractAction();}
});
function syncConferenceTree() {
  for (const group of $("conferenceChoices").querySelectorAll(".venue-group")) {
    const leaves = group.querySelectorAll('input[name="conference"]');
    const selected = Array.from(leaves).filter(input => input.checked).length;
    const toggle = group.querySelector(".select-area");
    toggle.checked = selected === leaves.length;
    toggle.indeterminate = selected > 0 && selected < leaves.length;
    group.querySelector(".area-count").textContent = selected + "/" + leaves.length;
  }
}

function conferenceGroup(category, entries) {
  const group = el("section", "venue-group");
  const head = el("div", "area-head");
  const toggle = el("input", "select-area");
  toggle.type = "checkbox";
  toggle.setAttribute("aria-label", "全选" + category);
  const expand = el("button", "area-expand");
  expand.type = "button";
  expand.append(el("span", "area-name", category), el("span", "area-count"));
  const children = el("div", "venue-children");
  children.id = "area-" + entries[0].stream;
  children.setAttribute("role", "group");
  children.setAttribute("aria-label", category);
  expand.setAttribute("aria-controls", children.id);
  children.hidden = category !== "计算机网络";
  expand.setAttribute("aria-expanded", String(!children.hidden));
  expand.onclick = () => {
    children.hidden = !children.hidden;
    expand.setAttribute("aria-expanded", String(!children.hidden));
  };
  for (const venue of entries) {
    const label = el("label", "venue-leaf");
    label.title = venue.title;
    const input = el("input");
    input.type = "checkbox";
    input.name = "conference";
    input.value = venue.name;
    input.checked = ["INFOCOM", "ICDE"].includes(venue.name);
    label.append(input, venue.name);
    children.append(label);
  }
  toggle.onchange = () => {
    for (const input of children.querySelectorAll("input")) input.checked = toggle.checked;
    syncConferenceTree();
    publisherSteps();
  };
  children.addEventListener("change", syncConferenceTree);
  head.append(toggle, expand);
  group.append(head, children);
  return group;
}

function renderCatalog(catalog,initial=false){
  venues = catalog.entries;
  $("conferenceLinks").replaceChildren(...venues.map(c => {
    const row = el("p");
    row.append(link(c.name + " · DBLP ↗", c.dblp_url), " · ", link(c.publisher + " ↗", c.publisher_site));
    return row;
  }));
  if (initial) {
    const areas = [...new Set(venues.map(v => v.category))];
    $("conferenceChoices").replaceChildren(...areas.map(area => conferenceGroup(area, venues.filter(v => v.category === area))));
    $("filterConference").replaceChildren(new Option("全部会议",""), ...venues.map(v => new Option(v.name,v.name)));
    syncConferenceTree();
  }
  $("catalogHint").textContent = "已支持 " + venues.length + " 个 A 类会议。更新链接会读取 ccf.atom.im 的五个领域。";
}
$("refreshCatalog").addEventListener("click",async()=>{notice("");$("refreshCatalog").disabled=true;try{renderCatalog(await api("/api/catalog/refresh",{}));notice("会议目录链接已更新。");}catch(error){notice(`目录更新失败，已有链接已保留：${error.message}`);}finally{$("refreshCatalog").disabled=running;}});
(async()=>{try{const config=await api("/api/config");token=config.token;renderCatalog(config.catalog,true);$("source").dispatchEvent(new Event("change"));$("yearFrom").value=config.year-1;$("yearTo").value=config.year-1;$("yearFrom").max=config.year+1;$("yearTo").max=config.year+1;await poll();}catch(error){notice(`启动失败：${error.message}`);}})();
