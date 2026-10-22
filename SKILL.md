---
name: cnki-search-download
description: 在知网批量检索文献并下载 PDF。当用户要检索 CNKI/知网、按被引或时间排序取前 N 条、限定 CSSCI/SCI/北大核心、抓摘要与卷期页码、或按题录批量下载全文时使用。
---

# 知网检索与下载

检索走 HTTP（快、不用开浏览器）；下载走浏览器（要过拼图）。脚本是唯一入口。

## 依赖

```bash
pip install -r ~/.agents/skills/cnki-search-download/requirements.txt   # playwright>=1.40
```

| 依赖 | 必需 | 用途 |
|---|---|---|
| Python 3.9+ | ✅ | 脚本运行 |
| `playwright`（pip） | ✅ | 连接已开调试端口的 Chrome（**无需** `playwright install` 下载浏览器） |
| Google Chrome | ✅ | 承载会话与下载 |
| `notify-send`（libnotify-bin） | 可选 | 遇到验证码时弹桌面通知 |
| `pdftotext` / `pdfinfo`（poppler-utils） | 可选 | 校验 PDF 文本层与页数 |

## 静默运行（默认）

- **检索 / 摘要 / 题录**：走 HTTP，**根本不开窗口**
- **下载**：需要一个有头浏览器，但默认**最小化静默**（`--launch` 时窗口最小化，不打断你）
- **遇到验证码**：自动把窗口还原、前置，并发桌面通知；你拖完拼图脚本自己继续
- `--no-silent` 可让窗口正常显示；`--headless` 仅用于调试（**下载会被站点拦，实测不可用**）

```bash
# 全程静默：无浏览器也能跑，窗口最小化，验证码才出现
python3 $S --launch download --in rec.json --dir PDF
```

## 前置

```bash
google-chrome --user-data-dir=~/.cache/cnki-chrome-profile --remote-debugging-port=9222
```

需要：有知网权限的浏览器会话（机构 IP 或已登录）。
首次检索会自动从该浏览器取 Cookie 缓存到 `~/.cache/cnki-cookies.json`，之后检索不再需要浏览器。

## 检索

```bash
S=~/.agents/skills/cnki-search-download/scripts/cnki.py

python3 $S search --query 供应链韧性 --category P0209 --sort PT --pages 2 --size 50 --out rec.json
python3 $S metadata --in rec.json     # 补卷/期/页/年/DOI/ISSN
python3 $S abstract --in rec.json     # 摘要、关键词、基金
```

参数：

| 参数 | 取值 |
|---|---|
| `--field` | `SU` 主题（默认）、`TI` 篇名、`TKA` 篇关摘、`KY` 关键词、`AU` 作者、`AF` 单位、`FU` 基金、`LY` 刊名、`DOI` 等 16 个 |
| `--category` | `P0209` CSSCI、`P0201` SCI、`P01` 北大核心、`P0210` CSCD、`P0202` EI、`P13` AMI、`P12` WJCI；逗号可多选（OR） |
| `--sort` | `PT` 时间、`CF` 被引、`FFD` 相关度、`DFR` 下载、`ZH` 综合 |
| `--pages` / `--size` | 页数；每页只能 10/20/50 |
| `--journal-only` | 期刊单库（计数与总库不同，报告要注明） |
| `--foreign` | 外文库（只有题录/摘要，无 PDF） |

主题检索用 `TOPRANK` 运算符（脚本默认），`DOI` 字段只认它。

## 下载

```bash
python3 $S download --in rec.json --dir PDF --gap 10 --on-verify wait --notify
python3 $S verify --dir PDF --in rec.json
```

- 路径：详情页 `a[name='pdfDown']` → 带 `Referer` 导航下载（唯一能拿到 PDF 的方式）
- 直接访问 `bar.cnki.net/.../order?id=` 会失败；搜索结果行的同款链接默认给的是 CAJ
- 风控按**累计下载量**触发：10 秒/篇可连下约 29 篇，第 30 篇左右弹拼图
- **遇到验证码会暂停并提醒**：默认 `--on-verify wait`，脚本自动前置验证页、弹桌面通知、
  打印操作说明，然后轮询等待；你完成拖动/点选后它自动继续。超时（默认 600 s）则停在当前篇，
  台账已保存，重跑不会重复
- 想完全无人值守时用 `--on-verify stop`：一遇验证码就退出（退出码 2），你自己过完再重跑
- 关闭通知用 `--no-notify`
- 台账 `PDF/download_state.json` 记录每篇状态，重跑自动跳过已完成
- 不要尝试自动破解滑块

## 翻页规则（脚本已封装，手写接口时才用）

```
第 1 页：boolSearch=true , CurPage=1
第 N 页：boolSearch=false, CurPage=N, turnpage=<上一页响应的 hidTurnPage>
```

`boolSearch=true` 时站点强制 `CurPage=1`，带令牌也回验证码；令牌逐页轮换，跳页会静默返空。

## 失败判定（四种结局都是 HTTP 200）

| 现象 | 含义 | 处置 |
|---|---|---|
| `请输入验证码` | 令牌失效或风控 | 停批，人工过滑块后重跑 |
| `字段【…】校验失败` | pageSize 越界 | 用 10/20/50 |
| 无 `<em>` | 空结果（语言或运算符错） | 检查参数 |
| 有 `<em>` | 正常 | 解析 |

退出码：`0` 正常｜`2` 需人工（滑块/连不上浏览器）｜`3` 参数错｜`4` 其他错误。

## 交付要求

报告写清：命中数与口径（字段+运算符+库+来源类别）、排序、抓取时间、实取条数、去重规则、
缺失项与原因。网络首发稿没有卷期页码，属事实缺失。

## 限制

1. 中文库有 SCI（`P0201`）但**没有 SSCI**；外文库不支持来源类别筛选。
2. 外文库没有 PDF，只有题录/摘要/DOI。
3. 下载受站点风控约束，不能承诺无限量、全自动。

## 代码结构

```
scripts/
├── cnki.py      命令行入口（argparse + 分发）
├── utils.py     常量、plain/norm、验证码识别、桌面通知
├── session.py   浏览器连接、自启/关闭、Cookie 缓存、HTTP 客户端
├── search.py    检索式构造、结果解析、cmd_search
├── detail.py    摘要/关键词/基金、RefWorks 题录、cmd_abstract/cmd_metadata
└── download.py  下载（导航 order 链接）、验证码暂停、台账、cmd_download/cmd_verify
```

改代码时的约定：`utils` 不依赖其它模块；`session` 只依赖 `utils`；
`search/detail/download` 依赖前两者；`cnki.py` 只做参数解析与分发。
