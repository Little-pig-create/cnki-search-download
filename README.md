# cnki-search-download

CNKI（中国知网）批量检索与 PDF 下载的命令行工具，也可以作为 agent skill 使用。

- **检索**：主题/篇名/关键词等 16 个字段，CSSCI、SCI、北大核心等来源类别，按被引/时间/相关度排序
- **题录**：RefWorks 导出补全卷、期、页码、DOI、ISSN
- **摘要**：逐篇抓摘要、关键词、基金
- **下载**：按题录批量下载 PDF，带台账可续跑；遇到人机验证会暂停并提醒

## 安装

```bash
pip install -r requirements.txt
```

需要一个有知网访问权限的 Chrome（机构 IP 或已登录账号）：

```bash
google-chrome --user-data-dir=~/.cache/cnki-chrome-profile --remote-debugging-port=9222
```

## 用法

```bash
S=./scripts/cnki.py

# 检索：CSSCI + 时间倒序，取 2 页 100 条
python3 $S search --query 供应链韧性 --category P0209 --sort PT --pages 2 --size 50 --out rec.json

# 补全卷期页码 / DOI / ISSN
python3 $S metadata --in rec.json --out rec_meta.json

# 摘要、关键词、基金
python3 $S abstract --in rec_meta.json --out rec_abs.json

# 下载 PDF（遇到验证码暂停提醒）
python3 $S download --in rec_abs.json --dir PDF

# 校验已下载文件
python3 $S verify --dir PDF --in rec_abs.json
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--field` | `SU` 主题、`TI` 篇名、`TKA` 篇关摘、`KY` 关键词、`AU` 作者、`AF` 单位、`FU` 基金、`LY` 刊名、`DOI` 等 |
| `--category` | `P0209` CSSCI、`P0201` SCI、`P01` 北大核心、`P0210` CSCD、`P0202` EI、`P13` AMI、`P12` WJCI；逗号可多选 |
| `--sort` | `PT` 时间、`CF` 被引、`FFD` 相关度、`DFR` 下载、`ZH` 综合 |
| `--journal-only` | 期刊单库模式（计数与总库不同） |
| `--foreign` | 外文库（只有题录/摘要，无 PDF） |
| `--on-verify wait\|stop` | 遇验证码时暂停等待（默认）或立即退出 |

退出码：`0` 正常｜`2` 需人工（验证码/连不上浏览器）｜`3` 参数错误｜`4` 其他错误。

## 静默运行

- 检索、摘要、题录走 HTTP，**不打开任何窗口**
- 下载默认**静默**运行：装了 `xdotool` 时窗口启动后立即隐藏（零闪现），否则最小化；遇到人机验证会自动还原窗口并通知
- `--no-silent` 显示窗口；`--headless` 仅调试用（下载会被拦）

## 说明与边界

- 检索走 HTTP（Cookie 缓存在 `~/.cache/cnki-cookies.json`，权限 0600），下载走浏览器。
- 下载会受站点风控约束：约每 30 篇触发一次人机验证，需要人工完成，**不提供自动绕过**。
- 中文库有 SCI 筛选，**没有 SSCI**；外文库不支持来源类别筛选，也没有 PDF。
- 仅用于个人研究用途。请遵守所在机构与知网的使用条款。

## 安全

- 仓库内**不含任何账号、Cookie、令牌**；`.gitignore` 已排除 `*.json`、`PDF/`、缓存目录。
- 运行时的 Cookie 只保存在本机 `~/.cache/cnki-cookies.json`（权限 0600），不会写进仓库，也不会上传。
- 代码只打印 Cookie 的**条数与路径**，不打印值。

## 作为 skill 使用

把本仓库放进 agent 的 skills 目录（例如 `~/.agents/skills/cnki-search-download/`），
`SKILL.md` 里写明了触发条件、参数表与失败判定。
