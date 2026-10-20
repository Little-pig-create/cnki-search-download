"""检索：构造 QueryJson、解析结果行。"""
import datetime
import json
import pathlib
import re
import time

from utils import CATS, FIELDS, KUAKU, OP_DEFAULT, OP_TOP, NOISE_RE, SIZES, fail, plain
from session import Browser, Http, harvest_cookies

def _item(field, value, op=OP_DEFAULT):
    return {"Field": field, "Value": value, "Operator": op, "Logic": 0, "Vector": "", "Title": ""}


def _cat_item(code, title=""):
    return {"Key": code, "Title": title, "Logic": 1, "Field": "LYBSM", "Operator": "DEFAULT",
            "Value": code, "Value2": "", "options": {}, "Name": "LYBSM", "ExtendType": 0}


def build_query(q, field, op, categories, rlang, journal_only=False):
    """categories: list[str]；多值时按站点自身的 MutiGroup（注意站点拼写）组合，语义为 OR。"""
    groups = [{"Key": "Subject", "Title": "", "Logic": 0,
               "Items": [_item(field, q, op)], "ChildItems": []}]
    if categories:
        groups.append({"Key": "MutiGroup", "Title": "", "Logic": 0, "Items": [],
                       "ChildItems": [{"Key": "LYBSM", "Title": "", "Logic": 0,
                                       "Items": [_cat_item(c) for c in categories],
                                       "ChildItems": []}]})
    base = {"Platform": "", "Resource": "CROSSDB", "Classid": "WD0FTY92", "Products": "",
            "QNode": {"QGroup": groups}, "ExScope": 1, "SimpTrad": "0", "SearchType": 2,
            "Rlang": rlang, "KuaKuCode": KUAKU, "Expands": {},
            "View": "changeDBCh", "SearchFrom": 1}
    if journal_only:
        base.update({"Platform": "NZKPT", "Resource": "JOURNAL", "Classid": "YSTT4HG0",
                     "Products": "CJFQ,CAPJ,ZHYX,CJTL", "KuaKuCode": "", "SearchFrom": 11})
    return base


def classify(text):
    if "请输入验证码" in text:
        return "captcha"
    if "校验失败" in text:
        return "bad_param"
    if "<em>" not in text:
        return "empty"
    return "ok"


def total_of(text):
    m = re.search(r"<em>([\d,]+)</em>", text)
    return int(m.group(1).replace(",", "")) if m else None


def token_of(text):
    m = re.search(r'id="hidTurnPage"[^>]*value="([^"]+)"', text)
    return m.group(1) if m else None


def cell(tr, name):
    m = re.search(r"<td[^>]*class=['\"]" + name + r"['\"][^>]*>(.*?)</td>", tr, re.S)
    return m.group(1) if m else ""


def plain(s):
    import html as H
    return H.unescape(re.sub(r"<[^>]+>", " ", s)).strip()


def parse_rows(text):
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S)[1:]:
        a = re.search(r'<a class="fz14"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', tr, re.S)
        au = cell(tr, "author")
        names = [plain(x) for x in re.findall(r">([^<>]+)</a>", au)] or \
                [x.strip() for x in plain(au).split(";") if x.strip()]
        dl = re.search(r"href=['\"](https://bar\.cnki\.net/bar/download/order\?id=[^'\"]+)['\"]", tr)
        out.append({
            "title": plain(a.group(2)) if a else plain(cell(tr, "name")),
            "authors": [n for n in names if n],
            "source": plain(cell(tr, "source")),
            "date": plain(cell(tr, "date")),
            "type": plain(cell(tr, "data")),
            "cited": plain(cell(tr, "quote")),
            "detail_url": a.group(1) if a else None,
            "download_hint": dl.group(1) if dl else None,
        })
    return out


def cmd_search(a):
    if a.size not in SIZES:
        fail("--size 只能是 %s" % (SIZES,), 3)
    if a.field not in FIELDS:
        fail("未知字段 %s，可选：%s" % (a.field, " ".join(FIELDS)), 3)
    cats = [c.strip() for c in (a.category or "").split(",") if c.strip()]
    unknown = [c for c in cats if c not in CATS]
    if unknown:
        fail("未知来源类别 %s，可选：%s" % (unknown, " ".join(CATS)), 3)
    if cats and a.foreign:
        fail("外文库不支持来源类别筛选（LYBSM 在该库无效）", 3)
    op = a.operator or (OP_TOP if a.field == "SU" else OP_DEFAULT)
    if a.field == "DOI" and op != OP_TOP:
        fail("DOI 字段只接受 TOPRANK 运算符", 3)
    rlang = "FOREIGN" if a.foreign else "CHINESE"

    http = None if a.browser else Http()
    b = None
    refreshed = False

    def post(body):
        nonlocal http, b, refreshed
        if http is not None:
            try:
                return http.grid(body)
            except Exception:
                if not refreshed:
                    http = Http(harvest_cookies()); refreshed = True
                    return http.grid(body)
                raise
        if b is None:
            b = Browser(a.cdp)
        return b.page.evaluate("""async x => {
            const r = await fetch('https://kns.cnki.net/kns8s/brief/grid', {method:'POST',
              credentials:'include', headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8',
              'X-Requested-With':'XMLHttpRequest'}, body:x});
            return await r.text(); }""", body)

    rows, token, total, status = [], None, None, "ok"
    transport = "browser" if a.browser else "http"
    try:
        import urllib.parse as up
        qj = build_query(a.query, a.field, op, cats, rlang, a.journal_only)
        for pg in range(1, a.pages + 1):
            body = up.urlencode({
                "boolSearch": "true" if pg == 1 else "false",
                "QueryJson": json.dumps(qj, ensure_ascii=False, separators=(",", ":")),
                "pageNum": pg, "pageSize": a.size, "dstyle": "listmode",
                "boolSortSearch": "false", "productStr": "", "aside": "", "searchFrom": "",
                "subject": "", "language": "FOREIGN" if a.foreign else ("CHS" if a.journal_only else ""),
                "uniplatform": "NZKPT" if a.journal_only else "", "CurPage": 1 if pg == 1 else pg,
                "sortField": a.sort, "sortType": a.order,
                **({"turnpage": token} if token else {}),
            })
            res = post(body)
            status = classify(res)
            if status == "captcha" and http is not None and not refreshed:
                print("HTTP 通道被拦，刷新 Cookie 后重试…", flush=True)
                http = Http(harvest_cookies()); refreshed = True
                res = post(body); status = classify(res)
            if status == "captcha" and http is not None:
                print("仍被拦，回落浏览器通道", flush=True)
                http = None; transport = "browser"
                res = post(body); status = classify(res)
            if status != "ok":
                if status == "captcha":
                    fail("第 %d 页触发验证码：需要人工在浏览器里过滑块后重跑" % pg, 2)
                fail("第 %d 页返回 %s（检查 pageSize/字段/运算符）" % (pg, status), 3)
            total = total if total is not None else total_of(res)
            token = token_of(res)
            got = parse_rows(res)
            if getattr(a, "exclude_noise", False):
                got = [r for r in got if not NOISE_RE.search(r.get("source") or "")]
            if not got:
                status = "empty_page"
                break
            rows.extend(got)
            if pg < a.pages:
                time.sleep(a.delay)
    finally:
        if b is not None:
            b.close()
    doc = {"query": {"value": a.query, "field": a.field, "operator": op,
                     "category": ",".join(cats) or None,
                     "platform": "外文" if a.foreign else ("中文·期刊库" if a.journal_only else "中文·总库"),
                     "sort": "%s %s" % (a.sort, a.order)},
           "status": status, "total": total, "fetched": len(rows), "transport": transport,
           "at": datetime.datetime.now().isoformat(timespec="seconds"), "rows": rows}
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print("命中 %s | 取回 %d 条 | %s%s | %s" % (
        f"{total:,}" if total is not None else "?",
        len(rows), FIELDS[a.field], ("/" + "+".join(CATS[c] for c in cats)) if cats else "", transport))
    for r in rows[:5]:
        print("  %-10s %-28s %s" % (r["date"][:10], r["source"][:26], r["title"][:46]))
    if a.out:
        print("已写入", a.out)


