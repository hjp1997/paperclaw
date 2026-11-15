# 发布到 GitHub Pages

静态站使用预采集 JSON。访客可以搜索、筛选、查看摘要和分类、勾选导出，以及按会议和年份收藏。收藏使用浏览器本地存储，清除网站数据会移除收藏。网站数据更新后，收藏仍指向该届会议的最新发布数据。

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

`import` 也支持本地工具的 `data/results.json`。此时只导入最近清单中的论文，公开字段采用固定清单。个人收藏、AI 分析、API Key 和其他额外字段会被排除。

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

密钥通过采集步骤的环境变量使用。发布产物仅包含静态页面和白名单论文字段。

本地也可以执行相同采集。

```powershell
python pages.py collect --conferences ICLR --years 2025 --source conference
python pages.py build
```

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
