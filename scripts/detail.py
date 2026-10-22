"""详情页解析：摘要 / 关键词 / 基金，以及 RefWorks 题录导出。"""
import datetime
import json
import pathlib
import re
import time

from utils import fail
from session import Browser, Http, harvest_cookies

ABS_JS = """async u => { const r = await fetch(u, {credentials:'include'});
    return {status:r.status, text:await r.text()}; }"""


def fetch_abstract(page, url):
    r = page.evaluate(ABS_JS, url)
    return parse_abstract_html(r["text"])


def parse_abstract_html(html):
    """从详情页 HTML 直接抽字段（不需要浏览器）。"""
    import html as H
    def grab(pat):
        m = re.search(pat, html, re.S)
        return m.group(1) if m else None
    def strip(s):
        return H.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()
    abstract = (strip(grab(r'id="ChDivSummary"[^>]*>(.*?)</span>'))
                or strip(grab(r'id="ChDivSummary"[^>]*>(.*?)</p>')))
    title = (strip(grab(r"<title>(.*?)</title>")) or "").replace(" - 中国知网", "").strip()
    authors = strip(grab(r'id="authorpart"[^>]*>(.*?)</')).replace("\u00a0", " ")
    doi = None
    m = re.search(r"10\.\d{4,9}/[^\s\"'<>，。]+", html)
    if m:
        doi = m.group(0).rstrip(".").lower()
    return {"status": "ok" if abstract else "no_abstract", "title": title,
            "authors": [x for x in re.sub(r"\d", "", authors).split() if x],
            "abstract": abstract or None,
            "keywords": strip(grab(r'class="keywords"[^>]*>(.*?)</p>')) or None,
            "fund": strip(grab(r'class="funds"[^>]*>(.*?)</p>')) or None,
            "doi": doi}


def cmd_abstract(a):
    src = json.loads(pathlib.Path(a.infile).read_text(encoding="utf-8"))
    rows = src["rows"]
    limit = a.limit or len(rows)
    ok, refreshed = 0, False
    http = None
    if not a.browser:
        http = Http()
    b = None
    try:
        for i, r in enumerate(rows[:limit], 1):
            if not r.get("detail_url"):
                r["abs_status"] = "no_url"
                continue
            res = None
            if http is not None:
                try:
                    html = http.page(r["detail_url"])
                    if "验证码" in html or "拼图校验" in html:
                        if not refreshed:
                            print("Cookie 失效/被拦，重新获取…", flush=True)
                            http = Http(harvest_cookies()); refreshed = True
                            html = http.page(r["detail_url"])
                        if "验证码" in html or "拼图校验" in html:
                            res = {"status": "captcha"}
                    if res is None:
                        res = parse_abstract_html(html)
                except Exception as e:
                    res = {"status": "error", "error": type(e).__name__}
            else:
                if b is None:
                    b = Browser(a.cdp)
                try:
                    res = fetch_abstract(b.page, r["detail_url"])
                except Exception as e:
                    res = {"status": "error", "error": type(e).__name__}
            if res.get("status") == "captcha":
                fail("摘要抓取触发验证码（第 %d 条）" % i, 2)
            r.update(res)
            r["abs_status"] = res.get("status")
            ok += 1 if res.get("abstract") else 0
            if i % 10 == 0:
                print("  %d/%d 有摘要 %d" % (i, min(limit, len(rows)), ok), flush=True)
            time.sleep(a.delay)
    finally:
        if b is not None:
            b.close()
    src["abstracts"] = {"requested": min(limit, len(rows)), "with_abstract": ok,
                        "transport": "browser" if a.browser else "http",
                        "at": datetime.datetime.now().isoformat(timespec="seconds")}
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(src, ensure_ascii=False, indent=1), encoding="utf-8")
    print("摘要完成：%d/%d 有内容（%s）%s" % (ok, min(limit, len(rows)),
          "browser" if a.browser else "http", " → " + a.out if a.out else ""))



# ---------------------------------------------------------------- 题录导出（RefWorks）
REFWORKS_MAP = {"T1": "title", "JF": "journal", "JO": "journal_full", "VO": "volume",
                "IS": "issue", "OP": "pages", "SP": "start_page", "EP": "end_page",
                "YR": "year", "PY": "year", "DO": "doi", "SN": "issn", "CN": "cn",
                "LA": "language", "AB": "abstract", "K1": "keywords", "LK": "urlid",
                "UR": "url", "AD": "affiliations", "PB": "publisher"}


def parse_refworks(text):
    """解析 RefWorks Tagged 文本（知网 ShowExport 返回）。"""
    import html as H
    plain = H.unescape(re.sub(r"<[^>]+>", "\n", text))
    out, authors, keywords, last = {}, [], [], None
    for line in plain.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([A-Z][A-Z0-9])\s+(.*)$", line)
        if not m:
            if last == "AB":
                out["abstract"] = (out.get("abstract", "") + " " + line).strip()
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "A1":
            authors.extend([x.strip() for x in val.split(";") if x.strip()])
        elif tag in ("K1", "KW"):
            keywords.extend([x.strip() for x in val.split(";") if x.strip()])
        elif tag in REFWORKS_MAP:
            key = REFWORKS_MAP[tag]
            if key == "abstract":
                out["abstract"] = (out.get("abstract", "") + " " + val).strip()
            else:
                out.setdefault(key, val)
        last = tag
    if authors:
        out["authors"] = authors
    if keywords:
        out["keywords"] = ";".join(keywords)
    return out


def _ids_from_detail(html):
    m = re.search(r'id="paramdbname"[^>]*value="([^"]+)"', html)
    n = re.search(r'id="paramfilename"[^>]*value="([^"]+)"', html)
    if m and n:
        return m.group(1), n.group(1)
    m = re.search(r"dbcode=([A-Z]+)&(?:amp;)?tablename=[A-Z0-9]+&(?:amp;)?filename=([A-Za-z0-9]+)", html)
    if m:
        return m.group(1), m.group(2)
    return None, None


def fetch_refworks(http, dbcode, filename):
    body = ("FileName=%s!%s!1!0&DisplayMode=Refworks&OrderParam=0&OrderType=desc&SelectField="
            "&PageIndex=1&PageSize=20&language=&uniplatform=NZKPT&random=0.%d" % (dbcode, filename, int(time.time() * 1000) % 100000))
    ref = ("https://kns.cnki.net/dm/manage/export.html?filename=%s!%s!1!0&displaymode=NEW&uniplatform=NZKPT"
           % (dbcode, filename))
    txt = http._req("https://kns.cnki.net/dm8/api/ShowExport", data=body.encode(), referer=ref)[1]
    return parse_refworks(txt)


def cmd_metadata(a):
    src = json.loads(pathlib.Path(a.infile).read_text(encoding="utf-8"))
    rows = src["rows"]
    limit = a.limit or len(rows)
    http = Http()
    ok = 0
    for i, r in enumerate(rows[:limit], 1):
        try:
            html = http.page(r["detail_url"])
            db, fn = _ids_from_detail(html)
            if not db:
                r["meta_status"] = "no_ids"
                continue
            meta = fetch_refworks(http, db, fn)
            r.update({k: v for k, v in meta.items() if v})
            r["dbcode"], r["filename"] = db, fn
            r["meta_status"] = "ok"
            ok += 1
        except Exception as e:
            r["meta_status"] = "error:%s" % type(e).__name__
        if i % 10 == 0:
            print("  %d/%d ok=%d" % (i, min(limit, len(rows)), ok), flush=True)
        time.sleep(a.delay)
    src["metadata"] = {"requested": min(limit, len(rows)), "ok": ok,
                       "source": "kns.cnki.net/dm8/api/ShowExport (RefWorks)",
                       "at": datetime.datetime.now().isoformat(timespec="seconds")}
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(src, ensure_ascii=False, indent=1), encoding="utf-8")
    print("题录补全：%d/%d%s" % (ok, min(limit, len(rows)), " → " + a.out if a.out else ""))


