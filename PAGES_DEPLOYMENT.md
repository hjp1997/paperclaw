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
5. 打开 **Actions → Publish PaperClaw → Run workflow**。选择 `main`，首次发布保持“发布前重新采集指定会议”关闭，再运行。
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

打开 **Actions → Publish PaperClaw → Run workflow**。

- 勾选“发布前重新采集指定会议”。
- 会议填 `ICLR`，年份填 `2025`，来源选择 `conference`，即可取得一个含摘要的官网目录。
- 多个会议或年份使用英文逗号，例如 `ICLR,ICML` 和 `2025,2026`。
- 包含 INFOCOM、ICDE、NSDI 等会议时选择 `auto`。`conference` 支持 ICLR、ICML、NeurIPS、CVPR、ICCV。

流程将采集结果保存为 `site-data` 中的 JSON，提交到当前分支，并在同一轮任务中构建发布。公开页面只读取预采集数据。

目录采集失败时，流程终止并保留原有 JSON 和线上版本。摘要覆盖以来源为准。部分年份官网目录只提供标题与链接，摘要需要先在本地逐篇更新后再导入。

需要密钥时，在仓库 **Settings → Secrets and variables → Actions** 添加以下 Repository secrets。

| 名称 | 用途 |
|---|---|
| `OPENALEX_API_KEY` | 批量补全 DOI 对应摘要 |
| `IEEE_API_KEY` | 使用 `ieee` 来源 |
| `DEEPSEEK_API_KEY` | 预生成 AI Topic 与中文摘要总结 |

密钥通过采集步骤的环境变量使用。发布产物仅包含静态页面和白名单论文字段。

本地也可以执行相同采集。

```powershell
python pages.py collect --conferences ICLR --years 2025 --source conference
python pages.py build
```

## 预生成 Topic 和摘要级总结

已接入 DeepSeek 官方 Chat Completions 接口，默认模型为 `deepseek-flash`，关闭思考并要求 JSON 输出。每篇只发送标题和摘要，生成 1–5 个 Topic 及中文研究问题、方法、结果三项总结。摘要没有报告具体结果时会要求模型明确写出这一点。总结按摘要级内容标记，不等同于全文评阅。

网站支持 Topic 筛选、点击 Topic 标签筛选、搜索总结内容，以及随 CSV / Markdown / JSON 导出。AI Topic 独立于 Oral、Highlight、Best Paper 等会议官方分类。每个 Topic 必须附带可在标题或摘要中找到的原文依据，鼠标悬停标签可查看；模型虚构或改写的引文不会通过校验。

首次启用：

1. 在仓库 [Settings → Secrets and variables → Actions](https://github.com/hjp1997/paperclaw/settings/secrets/actions) 添加 Repository secret `DEEPSEEK_API_KEY`，值填 DeepSeek Key。不要把密钥放入代码、论文字段或 workflow 输入。
2. 打开 [Actions → Publish PaperClaw](https://github.com/hjp1997/paperclaw/actions/workflows/pages.yml)，点击 **Run workflow**。
3. 若已有论文数据，保持“发布前重新采集指定会议”关闭。填会议、年份，例如 `ICLR` / `2025`。
4. 勾选“生成 AI Topic 和中文摘要总结”，首次将本轮篇数设为 `5`，核对质量后可改为 `50` 或其他 1–1000 的值。
5. 运行结束后，结果自动保存到 `site-data`、提交并发布。普通推送和不勾选 AI 的发布均不会调用模型；访客阅读预生成结果不需要 API Key。

没有摘要的论文自动跳过；标题、摘要、词表版本和模型未变化的已完成记录也会跳过。再次运行可接着处理剩余论文。每完成一篇就保存检查点；接口错误或格式错误会立即停止，不自动重试，也不添加重试暂停。工作流在失败时仍提交已完成的检查点，失败轮次不会替换线上版本。下一轮成功后继续发布。

重新采集时，匹配同一 ID 且标题、摘要未变化的分析会保留；输入变化后旧总结会失效，下一次 AI 运行重新生成。若需在输入不变时重新生成，可在本地使用 `--force`；更换模型可使用 `--model`。

本地运行（事先在环境变量中设置 `DEEPSEEK_API_KEY`）：

```powershell
# 仅查看数量，不访问 AI、不产生费用
python pages.py analyze --conferences ICLR --years 2025 --dry-run
# 实际调用，最多生成 5 篇
python pages.py analyze --conferences ICLR --years 2025 --limit 5
python pages.py build
```

Topic 词表位于 `paperclaw/analysis.py` 的 `TOPICS`，覆盖网络、系统、AI、数据库、理论和交叉应用。修改标签含义或提示词规则时应同时递增 `VERSION`，使旧结果失效。提示词与结构校验不能保证每项总结都准确；可以先导出小批结果检查质量。

接口依据：[DeepSeek 首次调用](https://api-docs.deepseek.com/)、[JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)、[Chat Completions 参数](https://api-docs.deepseek.com/api/create-chat-completion/)。

### 访客使用自己的 API Key

可以实现，登录并非必要条件，调用费用归各自的服务商账号。预生成结果与访客临时分析可以并存。

纯静态页面可以让访客输入 Key 后直连允许跨域访问的服务商；Key 只放在当前页面内存中，刷新即清除，不写入收藏、浏览器长期存储或导出文件。该方式依赖服务商 CORS 支持，浏览器扩展和页面脚本仍可能接触密钥。

面向公开使用更推荐增加一个小型后端：访客输入自己的 Key，页面经 HTTPS 将本次请求交给后端转发到固定服务商域名；后端不保存 Key、不记录请求头，限制请求大小与频率。GitHub Pages 继续承载前端，后端另行部署。当前版本实现预生成发布，没有开放浏览器输入密钥或即时分析入口。

## 本地预览

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
| `.github/workflows/pages.yml` | 提交触发发布及手动采集发布 |
| `_site/` | 构建产物，由 Actions 上传到 Pages |

## 常见问题

- **首次构建提示 Pages 未启用**。完成 Settings → Pages → Source → GitHub Actions 后重新运行工作流。
- **手动采集在保存 JSON 时返回权限错误**。检查 Actions 工作流写入权限和分支保护设置。也可使用本地导入后提交的方式。
- **采集来源返回 403 或验证挑战**。在本地工具选择适配的会议官网，或从可访问的来源导入论文 JSON，再提交 `site-data`。GitHub Actions 使用自己的网络和会话。
- **收藏无法跨设备同步**。收藏保存在各设备的浏览器中。

GitHub 官方的 [Pages 工作流说明](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)介绍了发布权限、部署环境和构建产物的要求。
