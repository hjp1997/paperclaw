# 发布到 GitHub Pages

静态站使用预采集 JSON。访客可以搜索、筛选、查看摘要和分类、勾选导出，以及收藏已勾选的论文。在“我的收藏”中勾选论文后，可以批量取消收藏。收藏使用浏览器本地存储，清除网站数据会移除收藏。

旧版整组收藏会自动转换为该组所有论文的收藏。论文通过会议、年份和来源 ID 识别，网站更新数据后仍可恢复对应论文。

## 首次上线

1. 解压部署包到一个新文件夹，例如 `E:\paperclaw-pages`。
2. 在 GitHub 创建一个名为 `paperclaw` 的空公开仓库。先保持仓库为空，方便上传已有文件。
3. 在解压后的文件夹打开 PowerShell，将下面命令中的 `YOUR_USERNAME` 换成自己的 GitHub 用户名。

```powershell
git init -b main
git add .
git commit -m "Add PaperClaw static library"
git remote add origin https://github.com/YOUR_USERNAME/paperclaw.git
git push -u origin main
```

4. 打开仓库的 **Settings → Pages → Build and deployment → Source**，选择 **GitHub Actions**。
5. 打开 **Actions → Publish PaperClaw → Run workflow**。选择 `main` 后运行；这个 Action 只构建和发布已有 JSON。
6. 等待 `build`、`deploy` 两个任务成功。部署日志和仓库 Pages 设置会给出网站地址，格式为 `https://YOUR_USERNAME.github.io/paperclaw/`。

第一次推送可能早于 Pages 设置完成。完成第 4 步后手动执行第 5 步即可。

## 更新论文数据

### 从本地工具导入

在本地 PaperClaw 中完成采集和摘要更新，然后导出 JSON。将文件放到部署项目之外的目录，在部署项目中执行以下命令。路径请改成自己的导出文件。

```powershell
python pages.py import "E:\paper-exports\iclr-2025.json"
python pages.py build
git add site-data
git commit -m "Update paper data"
git push
```

同一会议和年份默认合并去重。新增记录缺少摘要时会保留已有摘要。可同时导入多个 JSON 文件。

`import` 也支持本地工具的 `data/results.json`。此时只导入最近清单中的论文，公开字段采用固定清单。个人收藏、本地私有 `ai_summary`、API Key 和其他额外字段会被排除。通过下述预生成流程产生的公开 `ai_analysis` 会保留。

### 在 GitHub 中手动采集

打开 [Actions → Collect PaperClaw → Run workflow](https://github.com/hjp1997/paperclaw/actions/workflows/collect.yml)。

- 会议留空时，覆盖体系结构/并行与分布计算/存储、网络、交叉/综合/新兴、理论、数据库/数据挖掘/内容检索五个领域的全部 CCF A 会议，共 27 个；默认年份为 `2025,2026`。
- 也可指定会议，例如 `PPoPP,FAST,ASPLOS`。会议与年份均使用英文逗号分隔。
- 默认保留已有组，仅补充缺失会议年份；需要更新已有组时勾选 `refresh_existing`。
- NSDI、FAST 与 2025 年及以前的 ATC 使用 USENIX 技术目录；其余优先使用 DBLP 结构化目录。已适配的独立官网目录可在 DBLP 尚未收录时补充。
- 自定义选择 ICLR、ICML、NeurIPS、CVPR、ICCV 时使用已适配的会议官网目录与摘要。

采集流程只访问论文元数据来源，不读取 `DEEPSEEK_API_KEY`，不调用 AI。每个会议年份独立保存，结果及状态报告提交到 `main`。成功结束后，通过 `workflow_run` 自动触发独立的 **Publish PaperClaw** 发布，避免 `GITHUB_TOKEN` 推送不能触发普通 push 工作流的问题。

个别组失败时保留该组原有 JSON，继续采集其他组；尚未收录和失败原因保存在 `site-data/status/collections.json` 并展示于网页。不会创建空论文集冒充成功。报告中每个组有独立时间与状态。摘要覆盖以来源为准，DBLP 目录通常只有元数据；USENIX 目录可直接提供摘要。

需要密钥时，在仓库 **Settings → Secrets and variables → Actions** 添加以下 Repository secrets。

| 名称 | 用途 |
|---|---|
| `OPENALEX_API_KEY` | 批量补全 DOI 对应摘要 |
| `IEEE_API_KEY` | 使用 `ieee` 来源 |
| `DEEPSEEK_API_KEY` | 预生成 AI Topic 与中文摘要总结 |

批量采集 Action 不需要上述 Key；本地补摘要可按需使用 OpenAlex / IEEE Key，只有独立 AI 分析 Action 使用 `DEEPSEEK_API_KEY`。发布产物仅包含静态页面和白名单论文字段。

本地也可以执行相同采集。

```powershell
python pages.py collect --conferences ICLR --years 2025 --source conference
# 批量补齐五个领域的 2025–2026 会议目录，无 AI 调用
python pages.py collect-batch
# 仅更新指定会议
python pages.py collect-batch --conferences PPoPP,FAST --years 2025,2026 --refresh-existing
python pages.py build
```

## 单独补全原文摘要

DBLP 提供论文目录，不提供摘要。打开 [Actions → Enrich PaperClaw](https://github.com/hjp1997/paperclaw/actions/workflows/enrich.yml)，选择会议、年份、来源和篇数上限后运行；此操作不调用 AI。

- `auto`：先读取已适配的会议官网摘要索引，再按 DOI 查询 OpenAlex；没有 DOI 时要求完整标题、作者及相邻年份唯一匹配，最后访问仍缺失的论文落地页。
- `openalex`：只查询公开索引，适合大批量补齐。排除已识别的软件/数据工件、Zenodo/Figshare 工件仓储记录和不足 80 字符的片段。来源索引仍可能只有摘要首段，来源链接始终保留，不能据此保证全文摘要完整。
- `pages`：只读取论文落地页，支持 IEEE 页面元数据、ACM 摘要区域、Springer Abstract 章节、SC 议程 Description、PMLR 等已适配页面。只接收标题一致、DOI 不冲突的摘要，不读取付费全文。
- 已有摘要和论文 ID 保留；成功批次即时保存。成功核验但无摘要的记录 24 小时内跳过同一来源，网络错误或登录拦截不视为“无摘要”。每次重跑继续缺失项。保持正常请求间隔，不添加重试暂停、不自动重试。
- `limit=0` 表示处理全部缺失记录，运行仍受统一的 24 小时 300 次请求限制。每个 DOI 批次最多 100 篇；单篇官网通常每篇至少一次请求，可分多轮运行。

如来源中断，工作流保存并发布已完成批次，运行摘要和 `site-data/status/abstracts.json` 显示覆盖率及中断原因。受限出版社记录示例链接与原因，并在该轮跳过该网站，避免逐篇重复触发验证。可选 Repository secret `OPENALEX_API_KEY` 只传给 OpenAlex，不会发布到网站。

本地执行同一补全流程：

```powershell
python pages.py enrich --limit 0 --source openalex
python pages.py enrich --conferences SC --years 2026 --source pages --limit 100
```

校园网 IP 有时直接提供访问权限；机构登录则由你在浏览器完成。GitHub Actions 位于远程服务器，无法使用你本机的校园网或浏览器登录状态。本地 Python 请求也不会自动继承浏览器 Cookie；不会将校园网密码、Cookie 或完整登录网页写入仓库。需要登录的来源可在同一浏览器查看原文、导出含摘要的引用或整理为当前 JSON 格式，再通过 `pages.py import` 合并；登录成功不等于远程 Action 获得了权限。

## 预生成 Topic 和摘要级总结

已接入 DeepSeek 官方 Chat Completions 接口，默认模型为 `deepseek-flash`，关闭思考并要求 JSON 输出。每篇只发送标题和摘要，生成 1–5 个 Topic、研究关键词短语及中文研究问题、方法、结果三项总结。研究短语必须逐字出现在标题或摘要中。摘要没有报告具体结果时会要求模型明确写出这一点。总结按摘要级内容标记，不等同于全文评阅。

网站支持 Topic 筛选、点击 Topic 标签筛选、搜索总结内容，以及随 CSV / Markdown / JSON 导出。AI Topic 独立于 Oral、Highlight、Best Paper 等会议官方分类。每个 Topic 必须附带可在标题或摘要中找到的原文依据，鼠标悬停标签可查看；模型虚构或改写的引文不会通过校验。

首次启用：

1. 在仓库 [Settings → Secrets and variables → Actions](https://github.com/hjp1997/paperclaw/settings/secrets/actions) 添加 Repository secret `DEEPSEEK_API_KEY`，值填 DeepSeek Key。不要把密钥放入代码、论文字段或 workflow 输入。
2. 打开 [Actions → Analyze PaperClaw](https://github.com/hjp1997/paperclaw/actions/workflows/analyze.yml)，点击 **Run workflow**。该 Action 只能手动运行。
3. 填写已采集的会议、年份，例如 `ICML` / `2026`；这个 Action 不采集会议目录。
4. 默认 `scope=featured`，只处理带特别分类的论文，排除仅有 Poster、Regular 或无标签的记录。Oral、Spotlight、Highlight、获奖等分类会纳入。选择 `all` 可处理所有有摘要论文。首次将 `limit` 设为 `5`，核对质量后可改为 `50` 或其他 1–1000 的值。
5. 结果保存到 `site-data` 并提交，成功结束后自动触发独立发布 Action。采集、普通推送和手动发布均不会调用模型；访客阅读预生成结果不需要 API Key。

没有摘要的论文自动跳过；标题、摘要、词表版本和模型未变化的已完成记录也会跳过。再次运行可接着处理剩余论文。每完成一篇就保存检查点；接口错误或格式错误会立即停止，不自动重试，也不添加重试暂停。工作流在失败时仍提交已完成的检查点，失败轮次不会替换线上版本。下一轮成功后继续发布。

重新采集时，匹配同一 ID 且标题、摘要未变化的分析会保留；输入变化后旧总结会失效，下一次 AI 运行重新生成。若需在输入不变时重新生成，可在本地使用 `--force`；更换模型可使用 `--model`。

本地运行（事先在环境变量中设置 `DEEPSEEK_API_KEY`）：

```powershell
# 仅查看数量，不访问 AI、不产生费用
python pages.py analyze --conferences ICLR --years 2025 --dry-run
# 实际调用，最多生成 5 篇
python pages.py analyze --conferences ICLR --years 2025 --limit 5
# 包含常规 Poster 在内的全部有摘要论文
python pages.py analyze --conferences ICLR --years 2025 --scope all --limit 5
python pages.py build
```

Topic 词表位于 `paperclaw/analysis.py` 的 `TOPICS`，覆盖网络、系统、AI、数据库、理论和交叉应用。修改核心标签含义或总结规则时应同时递增 `VERSION`，使旧结果失效。研究关键词是兼容旧 v2 结果的可选扩展，旧分析不需要付费重生成；如需为旧分析补充关键词，可使用 `--force`。提示词与结构校验不能保证每项总结都准确；可以先导出小批结果检查质量。

接口依据：[DeepSeek 首次调用](https://api-docs.deepseek.com/)、[JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)、[Chat Completions 参数](https://api-docs.deepseek.com/api/create-chat-completion/)。

### 访客使用自己的 API Key

已实现。右上角点击“连接我的 AI”，填写自己的 Key；支持 DeepSeek（默认 `deepseek-flash`）及其他兼容 Chat Completions 的 HTTPS 接口。点击某篇论文的“与 AI 讨论”，即可围绕该篇标题与摘要进行多轮对话，无需登录。

Key 只放在当前页面内存中，刷新即清除，不写入收藏、浏览器长期存储或导出文件；每篇论文的对话独立，也仅保存在本页。切换服务地址时须重新输入 Key，不会把原服务密钥自动发往另一个地址。请求不跟随跳转、不附带浏览器登录 Cookie。调用费用归访客自己的服务商账号，不使用仓库的批量生成 Secret。

纯静态直连依赖服务商 CORS 支持，浏览器扩展和页面脚本仍可能接触密钥。DeepSeek 跨域预检已通过；其他服务若拒绝跨域，页面会提示原因，需要改用允许跨域的接口或自行部署受控后端代理。后端应限制服务域名、请求大小和频率，避免记录 Key。GitHub Pages 可继续承载前端。

聊天提供“问题与贡献、拆解方法、证据与待核实项、研究关键词”四个提问入口。提示词借鉴科研阅读与证据核验的结构：区分作者主张、已报告结果、推断和未知；不会仅凭摘要给接收评分、断言创新性或把摘要未报告的信息判为论文缺陷。没有自动加载全文或检索外部文献。

## 词云索引与摘要来源

“主题索引”支持研究关键词与 AI Topic 两种视图。每个词按关联论文数显示大小，数字按论文去重计数；点击即可筛选相关论文，再次点击或清除可恢复。词云会跟随会议、年份、搜索、分类和收藏范围变化，展示频次最高的 40 项。研究关键词优先使用 AI 从原文提取的短语，其余使用已有元数据或本地标题/摘要关键词；AI Topic 仅覆盖已生成分析的论文。

ICML 等官网可能把摘要与目录拆开发布。例如 ICML 2026 目录为 `icml-2026-orals-posters.json`，摘要在单独的 `icml-2026-abstracts.json`，按官方事件 ID 对应。采集器现会在目录缺摘要时读取这一索引；不能读取时保留目录和已有摘要。静态站不会在访客打开页面时重新爬取来源，需要更新 `site-data` 后发布。

## 本地预览

开发验证使用 `python -m unittest discover -s tests -q`。浏览器回归脚本为 `tests/browser_ai.cjs`：先用 `python tests/browser_fixture.py` 构建测试数据，通过端口 18766 提供 `test-results/pages-browser` 静态服务，再使用已安装的 Playwright / Chromium 运行该脚本（`PAPER_BROWSER` 指向浏览器）。测试仅模拟 AI 接口，不使用真实 Key 或产生调用费用。

Python 3.10+ 即可，使用仓库中的脚本构建后启动静态服务。

```powershell
python pages.py build
python -m http.server 8766 --bind 127.0.0.1 --directory _site
```

打开 `http://127.0.0.1:8766/`。通过 HTTP 服务预览可以正常读取 JSON 和浏览器收藏。

## 文件对应关系

| 文件 | 作用 |
|---|---|
| `site-data/*.json` | 按会议和年份保存的公开论文数据 |
| `site/` | 静态页面、筛选、导出和浏览器收藏 |
| `pages.py` | 导入、预采集和构建 |
| `.github/workflows/collect.yml` | 手动批量采集，无 AI 调用 |
| `.github/workflows/analyze.yml` | 手动分析已采集论文，独立控制范围和费用 |
| `.github/workflows/pages.yml` | 推送、手动操作或数据流程成功后构建发布 |
| `_site/` | 构建产物，由 Actions 上传到 Pages |

## 常见问题

- **首次构建提示 Pages 未启用**。完成 Settings → Pages → Source → GitHub Actions 后重新运行工作流。
- **手动采集在保存 JSON 时返回权限错误**。检查 Actions 工作流写入权限和分支保护设置。也可使用本地导入后提交的方式。
- **采集来源返回 403 或验证挑战**。在本地工具选择适配的会议官网，或从可访问的来源导入论文 JSON，再提交 `site-data`。GitHub Actions 使用自己的网络和会话。
- **收藏无法跨设备同步**。收藏保存在各设备的浏览器中。

GitHub 官方的 [Pages 工作流说明](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)介绍了发布权限、部署环境和构建产物的要求。
