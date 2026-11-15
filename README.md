# PaperClaw · 会议论文采集

本地 Python + 浏览器工具，按会议与年份获取论文目录、真实摘要和链接。左栏按领域折叠，支持 34 个 CCF A 类会议，包括新增的 11 个体系结构/并行与分布计算/存储系统会议。可按领域全选或跨领域勾选单场会议。Python 3.10+，无需 pip 安装依赖。

GitHub Pages 的目录采集、原文摘要补全、AI 分析和发布分别由 **Collect PaperClaw**、**Enrich PaperClaw**、**Analyze PaperClaw**、**Publish PaperClaw** 四个独立 Action 负责。批量采集默认覆盖五个指定领域的 27 个会议、2025–2026 年，采集和补摘要不读取 AI Key、不调用 AI。详情见 [部署说明](PAGES_DEPLOYMENT.md)。

## 启动

Windows 双击 `start_windows.bat`，或在项目目录执行：

```powershell
python start.py
```

浏览器自动打开 `http://127.0.0.1:8765`。使用期间保持启动窗口开启。端口被占用时运行 `python start.py --port 8766`；同一数据目录只允许一个进程，重复启动时先关闭旧启动器。

1. 选择数据源、会议和年份，点击“开始采集”。默认最近一个完整年份，支持连续 1–30 年。
2. 搜索标题、作者、关键词、摘要，或按会议/年份筛选。
3. 勾选单篇论文或点击“勾选全部筛选结果”。勾选跨页和筛选保留。顶部 CSV、Markdown 和 JSON 按已勾选的清单导出。未勾选时导出全部筛选结果，清单上方显示篇数。
4. 每次点击导出后弹出“另存为”，在其中选择文件夹和文件名；顶部不再单独选择文件夹。Chrome / Edge 支持该接口，不支持的浏览器使用普通下载（保存位置遵循浏览器设置）。
5. 默认勾选“采集后补全缺失摘要”。已有记录可点击“补全摘要（缺失篇数）”。全部有摘要时显示“摘要已齐全”和覆盖篇数。补全期间显示请求进度，结束或失败后显示结果。
6. 刷新会恢复最近一轮采集。要长期保存，请在会议和年份旁点击“收藏本组”。“我的收藏”支持筛选、导出和取消收藏。点击“更新收藏”可用本轮结果替换已收藏的同届会议。

标题中的数学公式在浏览器本地排版。CSV 和 JSON 的 `title` 保留来源原题，`title_text` 提供可读形式。

分类标签可点击查看来源，也可以筛选。ACL Anthology 提供获奖记录，ACL 2026 另按会议日程匹配 Oral、Poster 和 Demo。ICLR、ICML、NeurIPS、CVPR、ICCV 使用公开议程，NSDI 读取技术议程。工具还读取 LICS 档案和部分届次的奖项名单，例如 CAV、RTSS 和 SIGCOMM 2025、MobiCom 和 ICDE 2025/2026。INFOCOM 2025 有按 DOI 核对的获奖记录。18 个会议另接入 Jeff Huang 的 Best Paper Awards 汇编，汇编来源有独立标签。分类字段、公布方式与可访问年份因会议而异，“暂无来源标签”可单独筛选。

离线体验：选择 DBLP、INFOCOM、2025，导入 `examples/dblp_sample.json`。它是从 [DBLP 公开响应](https://dblp.org/search/publ/api?format=json&h=1000&q=toc%3Adb%2Fconf%2Finfocom%2Finfocom2025.bht%3A) 摘取的 **2 条真实记录**（核对日期 2026-11-13），仅供检验筛选与导出，不是完整会议数据。

## 数据源

自动来源先核对 ICLR、ICML、NeurIPS、ACL、AAAI、CVPR、ICCV、SIGIR、SIGKDD 对应年份的 OpenReview 主会和公开分轨，包括 Research、ADS、Datasets and Benchmarks、Short Papers 等。目录存在时读取公开的已接收论文，直接获取摘要、作者、关键词和报告类型。API 2 按精确 `venueid` 过滤，旧 API 1 按官方 Decision 过滤。拒稿、撤稿和审稿中记录会被排除，各届收录范围以来源公开分轨为准。

OpenReview 确认没有该届主会时，自动来源继续使用原有会议目录。访问问题会显示原因，也可以手动选择 DBLP。OpenReview 响应缓存 1 小时。补全已有论文摘要时，适用会议会查询官网和 OpenReview，再对剩余 DOI 查询 OpenAlex。包含 Workshop 时自动来源使用原有会议目录。

OpenReview 返回 `ChallengeRequiredError` 时，需要 API 请求本身完成验证。网页登录和 Python 采集请求使用各自的会话。自动来源现可在尚未取得该组论文时转用 ICLR、ICML、NeurIPS、CVPR、ICCV 公开官网目录，也可直接选择“会议官网”。已有部分 OpenReview 论文时会保存当前进度并显示访问问题。

会议官网目录提供摘要时会直接读取，有些年份的目录只提供题名和单篇链接。“更新来源摘要”会查询官网数据及论文页面，再尝试按唯一标题匹配 arXiv。新摘要必须匹配所选论文标题，来源提供 DOI 时同时核对 DOI。OpenAlex 原始索引可能只有首段，即使句子和词序完整，也不足以证明它包含论文的全部摘要。

选择“出版社网页 · 登录后查询”，按会议打开 IEEE Xplore、ACM Digital Library 或 USENIX。使用自己的账号或机构入口完成登录，回到本工具点击“我已登录，显示会议查询”，在同一浏览器打开所选会议和年份。也可以填入出版社的会议或论文链接。官网导出的 BibTeX 引用可按当前会议和年份合并到论文清单。

自动采集遇到登录页或访问拒绝时，失败会议旁会显示“在出版社网页继续”。IEEE Metadata API 的授权错误另有“更新 IEEE API Key”入口。登录会话由官网使用，PaperClaw 通过导入的引用接收查询结果。

| 模式 | 凭据 | 获取内容 |
|---|---|---|
| 自动选择来源（页面默认） | 不需要 | 适用会议优先 OpenReview，INFOCOM、ICDE、SIGCOMM、MobiCom 使用 Crossref，NSDI 使用 USENIX，其余使用 DBLP |
| OpenReview | 读取公开记录 | 已接收论文的标题、作者、真实摘要、关键词、论文页、PDF 和公开的报告类型 |
| Crossref 公开目录 | 不需要 | IEEE / ACM 登记的标题、作者、会议、年份、页码、DOI、论文链接；保留来源提供的摘要 |
| DBLP 公开目录 | 不需要，可能遇到网站验证 | 标题、作者、会议、年份、页码、DOI、论文落地页、DBLP 记录链接 |
| IEEE Metadata API | [申请官方 API Key](https://developer.ieee.org/) | 支持 INFOCOM、ICDE、CVPR、ICCV、FOCS、RTSS |
| USENIX 官方页面 | 不需要 | NSDI 主会技术议程中的题名、作者、摘要与论文页面；不依赖 DOI |
| OpenAlex 摘要补全 | 少量可匿名；批量建议 [API Key](https://openalex.org/settings/api) | 按 DOI 精确匹配，重建 abstract_inverted_index，保留来源链接 |

Crossref 覆盖出版商登记的跨学科 DOI 记录；DBLP 专注计算机领域会议与期刊目录，也收录没有 DOI 的论文。两者不存在对所有会议都成立的“更全”：找计算机会议目录优先参考 DBLP / 官方目录，补 DOI 元数据用 Crossref。DBLP 明确不提供摘要，Crossref 的摘要依赖出版商是否提交。

摘要使用来源原文，不由 AI 生成。已有摘要优先保留，缺失项每批最多 100 个 DOI 向 OpenAlex 查询，只接受 DOI 精确匹配。成功查询但没有摘要标记为 `unavailable`，请求失败保留原状态，不会把限流误报为“摘要不存在”。每批保存，停止或限流后可以继续。NSDI 直接读取官方摘要，上下标保留为 `^(...)` / `_(...)`，防止 10 的 6 次方误写成 106。CSV / Markdown / JSON 都包含摘要、摘要来源及来源链接。

**OpenAlex 有访问额度，批量补全可能返回 429。** 在页面的 OpenAlex Key 栏填写自己的 Key 后重新点击补全；也可以等待来源额度恢复。Key 只用于当前操作，不保存。独立补摘要 Action 对没有 DOI 的记录采用标题检索，并要求完整标题、作者及相邻年份唯一匹配；候选不唯一时不写入。没有摘要时关键词为本地标题提取，有摘要时用标题和摘要提取；IEEE 返回作者关键词时优先保留。

默认排除 Workshop、论文集整本记录、常见序言与目录。主会论文集可能包含 Research、Short、Demo 等类别；接口没有可靠细分类时不推断。没有 Oral / Poster / Best Paper 等数据时不生成这些标签。年份使用来源的出版年份；最新年份可能尚未收录完整。DBLP 的来源总条数包含被过滤的记录，因此可能大于最终论文数。

IEEE 使用 `publication_title`、`content_type=Conferences` 与 `start_year/end_year` 查询，再校验返回记录。DBLP 使用会议 stream 与出版年份查询，再校验 venue/type。两者按返回总数分页，按**实际返回长度**推进，检测空页与重复页，避免服务器限制单页大小时漏抓。

Crossref 先查找对应年份的论文集，再使用 `container-title` 精确筛选，通过游标获取至来源总数，校验会议、年份、出版商 DOI 前缀（IEEE `10.1109`、ACM `10.1145`）及类型。MobiCom 使用全名查询；INFOCOM 排除 ICRITO。缺少论文集时明确提示不可用。SIGCOMM 的独立 Posters/Demos 论文集会保留，不属于 Workshop；导出中的 `venue` 可区分论文集。

Crossref 仅缓存完整查询结果，不缓存会过期或动态推进的游标响应。中断后保留已有论文，手动重试从新游标开始并去重；完成的论文集查询仍可复用 24 小时缓存。

若 DBLP 提示“网站验证挑战”，选择 **Crossref 公开目录**再点击开始采集。该选择是独立的公开元数据服务，不需要完成 DBLP 验证；程序不会在遇到拦截时自动切换来源。

## CCF 会议目录

“会议目录与链接 → 从 CCF 目录更新链接”读取计算机网络、交叉/综合/新兴、人工智能、计算机科学理论、数据库/数据挖掘/内容检索五个领域的 A 类会议链接。[目录来源](https://ccf.atom.im/)

新增会议按 DBLP 的会议系列与举办年份查询；没有举办年份的记录按出版年份查询。先按论文 ID 分页，再读取作者署名。SIGMOD 从 2023 年开始额外按官方目录对应的 PACMMOD 卷期收录研究论文，目前已核对到 2026 年。VLDB 从 2008 年开始纳入当届 PVLDB 卷。记录的 year 对应选择的届次，publication_year 保留 DBLP 的出版年份。自动来源沿用 OpenAlex 按 DOI 补摘要的机制。[DBLP 数据说明](https://dblp.org/rdf/docu/) · [SIGMOD 卷期](https://dblp.org/db/conf/sigmod/index.html)

这些链接是会议系列总目录，不是每年的官网或论文摘要接口。程序只更新已支持会议的链接，新会议仍需适配来源。网站返回验证页面、403、202、429 或页面结构变化时明确报错，保留内置/已有目录，不自动解验证码或循环重试。目录响应同样缓存 24 小时。NSDI 已实测 2025 年，其他年份的 USENIX 页面若结构不同会提示失败，不将空解析当作 0 篇成功。

## 访问限制与恢复

- 不读取或发送网站登录 Cookie、账号密码。仅采集元数据和链接，不批量下载 PDF；PDF 链接不代表有全文权限。
- 全局串行请求，间隔至少 10 秒，滚动 24 小时最多 300 次。成功响应缓存 24 小时。
- 普通 401/403、网站验证（包括 HTTP 202）与 429 均不设置额外重试冷却；可手动再次采集，仍遵守正常的 10 秒请求间隔和每日额度。
- 429 的 `Retry-After` 仅显示为服务端建议，不在本地强制等待。
- 目录采集遇到上游失败立即停止，后面的组标为“未执行”。摘要补全失败保留全部目录和已获取摘要；不自动重试，也不自动切换数据源。
- 缓存与请求计数保存在 `data/`。每页保存本轮结果，新一轮会替换最近清单。清空最近采集后，“我的收藏”仍可单独查看。首次迁移会将旧版完整清单备份至 `data/results.pre-favorites.json`，再按最近一轮的会议和年份恢复当前结果。
- 点击“停止”可中断限速等待；正在进行的网络请求会等待返回或超时（约 40 秒）。

“打开来源网站”用于检查网站访问情况。工具不共享浏览器 Cookie，打开网页不保证采集器通过验证。来源拒绝访问时显示具体错误，不显示为“成功，0 篇”。移除本地重试冷却不会改变来源网站的访问限制。

也可以从浏览器获取来源 API 的 JSON，在“导入来源 JSON”中导入。按当前来源/会议/年份筛选，多页文件可依次合并；页面明确显示该文件条数与来源总数，导入不表示全量抓取完成。

## 可选 AI

填写兼容 Chat Completions 的 Base URL（通常以 `/v1` 结尾）、模型、Key。每次点击单篇“分析摘要”才发送该篇的标题与摘要，远程接口要求 HTTPS（本地模型可用 loopback HTTP）。无摘要的论文不启用分析。

IEEE / OpenAlex / AI Key 只在当前操作内使用，不写入缓存、结果、日志、浏览器存储或导出文件。AI 输出保存在本地结果文件，导出可携带；不是阅读全文后的结论。接口费用由你选择的服务决定。

## 公网部署

GitHub Pages 静态版本已经提供，完整步骤见 [PAGES_DEPLOYMENT.md](PAGES_DEPLOYMENT.md)。

```powershell
python pages.py import data/results.json
python pages.py build
python -m http.server 8766 --bind 127.0.0.1 --directory _site
```

GitHub Pages 可以发布 HTML、CSS、JavaScript 和预先采集的 JSON。纯静态版本可通过 GitHub Actions 更新论文数据，在浏览器中完成搜索、筛选、导出和本机收藏。部署项目站点时需要适配仓库子目录中的资源路径。

实时采集版本需要一个支持 Python 的独立服务。前端可以继续放在 GitHub Pages，采集和摘要更新通过 HTTPS API 调用后端。后端应使用任务队列、共享来源缓存和数据库，收藏按用户保存。当前本地版本使用单实例任务、统一结果文件和本机请求验证，公开服务需要相应改造。

GitHub Pages 的静态托管范围和服务端语言限制可参阅 [GitHub 官方说明](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)。

## 本地文件

```text
start.py / start_windows.bat   启动器
paperclaw/core.py              数据源、分页、过滤、缓存、限速、导出
paperclaw/abstracts.py         OpenAlex 批量匹配与摘要重建
paperclaw/openreview.py        OpenReview 分轨发现、接收状态过滤与摘要匹配
paperclaw/official.py          官网目录、论文页面和 arXiv 摘要更新
paperclaw/catalog.py           会议注册表、CCF / USENIX HTML 解析
paperclaw/server.py            本地 API、任务状态、AI 与结果持久化
paperclaw/instance.py          数据目录单实例锁
web/                          中文界面
tests/                        无外部依赖的测试
data/results.json             本地论文结果（首次采集/导入后创建）
data/policy.json               请求计数
data/cache/                   API 成功响应
```

默认仅监听 `127.0.0.1`，校验 Host、Origin 与页面令牌；不会向局域网开放服务。不跟随远程重定向以避免转发 Key。CSV 对公式起始字符做转义，中文使用 UTF-8 BOM。

## 验证

```powershell
python -m unittest discover -s tests -v
node --check web/app.js
```

Node 只用于开发时的 JavaScript 语法检查，运行工具不需要安装 Node。测试覆盖分页、过滤、导出、缓存、失败后手动重试、旧版暂停迁移、请求额度、失败保留、中断恢复、AI 请求范围与本地 HTTP 接口。

实际网络检查（2026-11-13）：DBLP 曾返回 HTTP 202 验证页面。Crossref 已采集 INFOCOM 2025 的 272 篇和 ICDE 2025 的 401 篇（405 条来源记录排除 4 条非论文记录）。本次保留原有 1,649 篇（含用户后来采集的 2026 年记录），新增 NSDI 2025 的 83 篇、MobiCom 2025 的 157 篇、SIGCOMM 2025 的 154 篇，共 2,043 篇。NSDI 83 篇均含官方摘要，OpenAlex 首批新增 48 篇，随后匿名额度返回 429；当前 131 篇有摘要、2 篇已查询但无摘要、1,910 篇尚未查询。该数字不代表 OpenAlex 的最终摘要覆盖率。51 项 Python 自动化测试通过。IEEE / AI 账号接口使用模拟响应验证，未进行真实账号联调。

接口依据：

- [Crossref 精确筛选](https://www.crossref.org/documentation/retrieve-metadata/rest-api/rest-api-filters/)
- [Crossref REST API](https://github.com/CrossRef/rest-api-doc)
- [Crossref 元数据完整度](https://www.crossref.org/documentation/reports/participation-reports/)
- [DBLP 为什么不提供摘要](https://dblp.org/faq/Why+are+there+no+abstracts+in+dblp.html)
- [OpenAlex API 认证与额度](https://help.openalex.org/api/authentication/)
- [NSDI 2025 官方技术议程](https://www.usenix.org/conference/nsdi25/technical-sessions)
- [DBLP Search API](https://dblp.org/faq/How%2Bto%2Buse%2Bthe%2Bdblp%2Bsearch%2BAPI.html)
- [INFOCOM 2025 目录](https://dblp.org/db/conf/infocom/infocom2025.html)
- [ICDE 2025 目录](https://dblp.org/db/conf/icde/icde2025.html)
- [IEEE API Query Basics](https://developer.ieee.org/docs/read/Searching_the_IEEE_Xplore_Metadata_API)
- [IEEE Filtering Parameters](https://developer.ieee.org/docs/read/metadata_api_details/Filtering_Parameters)
- [IEEE Paging Parameters](https://developer.ieee.org/docs/read/metadata_api_details/Sorting_and_Paging_Parameters)
- [IEEE Data Fields](https://developer.ieee.org/docs/read/Metadata_API_responses)
