"""下载与校验：导航 order 链接、台账、验证码暂停。"""
import datetime
import json
import pathlib
import re
import time

from utils import PDF_LABEL, captcha_kind, fail, norm
from utils import notify as _notify
from session import Browser, show_window

def _probe_download(b, row, outdir):
    """用共享 Cookie 的 APIRequestContext 直接取下载响应：
    返回 (status, file|None, note)。PDF 直接落盘；遇到拼图页返回 verify。"""
    hint = row.get("download_hint")
    if not hint:
        return None, None, "no_hint"
    ref = row.get("detail_url") or "https://kns.cnki.net/"
    try:
        r = b.ctx.request.get(hint, headers={"Referer": ref, "Accept": "*/*"}, timeout=60000)
    except Exception as e:
        return None, None, "request_fail:%s" % type(e).__name__
    body = r.body()
    ctype = r.headers.get("content-type", "")
    if body[:4] == b"%PDF":
        name = re.sub(r'[\\/:*?"<>|]', "_", (row.get("title") or "download")[:80])
        fname = "%s_%s.pdf" % (name, (row.get("authors") or ["未知"])[0])
        (outdir / fname).write_bytes(body)
        return r.status, fname, "pdf"
    text = body.decode("utf-8", "replace")
    kind = captcha_kind(text)
    if kind or "验证成功，请稍等" in text:
        return r.status, None, "captcha:%s" % (kind or "unknown")
    if "来源应用不正确" in text:
        return r.status, None, "bad_referer"
    return r.status, None, "html_%d_%s" % (len(body), ctype.split(";")[0])



def _pause_for_human(b, a, kind):
    """暂停下载，提醒用户，并在其完成验证后返回 True。"""
    label = "滑块拼图" if kind in (None, "slider") else "点选验证码"
    verify_pages = [p.url for p in b.ctx.pages if "bar/verify" in p.url]
    print("\n" + "=" * 62, flush=True)
    print("  下载已暂停：需要人工完成「%s」" % label, flush=True)
    print("  1) 切到浏览器里弹出的验证页（已自动前置）", flush=True)
    print("  2) 按提示拖动/点选，完成后脚本会自动继续", flush=True)
    print("  3) 最长等待 %d 秒；超时会停在当前篇，台账已保存" % a.verify_timeout, flush=True)
    if verify_pages:
        print("  验证页：%s" % verify_pages[0][:96], flush=True)
    print("=" * 62 + "\n", flush=True)
    # 静默模式下窗口在屏幕外：先移回可见区域，再前置
    show_window(b)
    for p in b.ctx.pages:
        if "bar/verify" in p.url:
            try:
                b.ctx.new_cdp_session(p).send("Page.bringToFront")
                break
            except Exception:
                pass
    if a.notify:
        _notify("下载已暂停：请在浏览器里完成「%s」，完成后自动继续" % label)
    return _wait_human_verify(b, a.verify_timeout)


def _wait_human_verify(b, timeout_s):
    """轮询浏览器标签，直到出现 verifySuccess 或滑块页消失。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(2)
        urls = [p.url for p in b.ctx.pages if "bar.cnki.net" in p.url]
        if any("verifySuccess" in u for u in urls):
            return True
        if not any("bar/verify" in u for u in urls):
            return True          # 标签已关，通常意味着已通过
    return False


def _retry_one(b, r, outdir, state, state_path, a):
    """滑块通过后重试单篇。"""
    key = r.get("detail_url") or r.get("title")
    before = {f.name for f in outdir.iterdir()}
    p = b.ctx.new_page()
    cdp = b.ctx.new_cdp_session(p)
    cdp.send("Browser.setDownloadBehavior",
             {"behavior": "allow", "downloadPath": str(outdir), "eventsEnabled": True})
    status, note, fname = "unknown", "", None
    try:
        p.goto(r["detail_url"], wait_until="domcontentloaded", timeout=40000)
        p.wait_for_timeout(1500)
        loc = p.locator('a:has-text("%s")' % PDF_LABEL)
        if loc.count():
            loc.first.click()
            for _ in range(a.wait):
                time.sleep(1)
                new = {x for x in {f.name for f in outdir.iterdir()} - before
                       if not x.endswith(".crdownload")}
                if new:
                    fname = sorted(new)[0]
                    fp = outdir / fname
                    status = "ok" if fp.open("rb").read(4) == b"%PDF" else "not_pdf"
                    note = "%dKB" % (fp.stat().st_size // 1024)
                    break
        else:
            status = "no_pdf_link"
    except Exception as e:
        status, note = "error", "%s: %s" % (type(e).__name__, str(e)[:60])
    finally:
        p.close()
    state[key] = {"status": status, "file": fname, "note": note,
                  "title": r["title"], "at": datetime.datetime.now().isoformat(timespec="seconds")}
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    print("  retry %-10s %s %s" % (status, r["title"][:30], note), flush=True)


def cmd_download(a):
    src = json.loads(pathlib.Path(a.infile).read_text(encoding="utf-8"))
    rows = src["rows"]
    outdir = pathlib.Path(a.dir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    state_path = pathlib.Path(a.state) if a.state else outdir / "download_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}

    def done(r):
        key = r.get("detail_url") or r.get("title")
        return state.get(key, {}).get("status") == "ok"

    b = Browser(a.cdp)
    attempts = 0
    try:
        for i, r in enumerate(rows, 1):
            if done(r) or not r.get("detail_url"):
                continue
            if a.limit and attempts >= a.limit:
                break
            attempts += 1
            key = r.get("detail_url") or r.get("title")
            before = {f.name for f in outdir.iterdir()}
            if a.probe:
                st, fname, note = _probe_download(b, r, outdir)
                if fname:
                    status, extra = "ok", "%s|%dKB" % (fname[:38], (outdir / fname).stat().st_size // 1024)
                elif note == "captcha":
                    print("  (XHR 探测遇到拼图，回落点击路径)", flush=True)
                    status, extra = None, "captcha_page"
                else:
                    status, extra = None, note
                if status:
                    state[key] = {"status": status, "file": fname, "note": extra,
                                  "title": r["title"],
                                  "at": datetime.datetime.now().isoformat(timespec="seconds")}
                    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
                    print("%3d %-11s %-40s %s" % (i, status, r["title"][:34], extra), flush=True)
                    if status == "verify":
                        kind = (extra or "").split(":")[1] if ":" in (extra or "") else None
                        if a.on_verify == "stop":
                            _pause_for_human(b, a, kind)
                            fail("已暂停并等待人工完成验证；完成后重跑本命令（台账会跳过已完成）", 2)
                        if not _pause_for_human(b, a, kind):
                            fail("等待人工验证超时；已完成项已入台账，可稍后重跑", 2)
                        st2, f2, n2 = _probe_download(b, r, outdir)
                        if f2:
                            state[key] = {"status": "ok", "file": f2, "note": "after_verify",
                                          "title": r["title"],
                                          "at": datetime.datetime.now().isoformat(timespec="seconds")}
                            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
                            print("  retry ok:", f2[:50], flush=True)
                    time.sleep(a.gap)
                    continue
            p = b.ctx.new_page()
            cdp = b.ctx.new_cdp_session(p)
            cdp.send("Browser.setDownloadBehavior",
                     {"behavior": "allow", "downloadPath": str(outdir), "eventsEnabled": True})
            try:
                cdp.send("Network.enable")
                cdp.send("Network.setExtraHTTPHeaders",
                         {"headers": {"Referer": r.get("detail_url") or "https://kns.cnki.net/"}})
            except Exception:
                pass
            status, note, fname = "unknown", "", None
            try:
                p.goto(r["detail_url"], wait_until="domcontentloaded", timeout=40000)
                p.wait_for_timeout(1500)
                loc = p.locator('a:has-text("%s")' % PDF_LABEL)
                if loc.count() == 0:
                    status = "no_pdf_link"
                    note = "caj=%d" % p.locator('a:has-text("CAJ")').count()
                else:
                    # 优先直接导航 PDF 的 order 链接（实测可下，比点按钮稳）
                    pdf_href = p.evaluate("""() => {
                        const byName = document.querySelector("a[name='pdfDown']");
                        if (byName && byName.href) return byName.href;
                        const a = [...document.querySelectorAll('a')]
                          .find(e => (e.getAttribute('href')||'').includes('/bar/download/order')
                                     && /PDF/.test(e.innerText||''));
                        return a ? a.getAttribute('href') : null; }""")
                    if pdf_href:
                        try:
                            p.goto(pdf_href, wait_until="commit", timeout=30000)
                        except Exception:
                            pass
                    else:
                        loc.first.click()
                    for _ in range(a.wait):
                        time.sleep(1)
                        new = {x for x in {f.name for f in outdir.iterdir()} - before
                               if not x.endswith(".crdownload")}
                        if new:
                            fname = sorted(new)[0]
                            fp = outdir / fname
                            magic = fp.open("rb").read(4)
                            status = "ok" if magic == b"%PDF" else "not_pdf"
                            note = "%dKB" % (fp.stat().st_size // 1024)
                            break
                    else:
                        vts = [q for q in b.ctx.pages
                               if "bar/verify" in q.url and "verifySuccess" not in q.url]
                        status = "verify" if vts else "timeout"
                        note = "verify_tabs=%d" % len(vts)
                        for q in vts:
                            q.close()
            except Exception as e:
                status, note = "error", "%s: %s" % (type(e).__name__, str(e)[:60])
            finally:
                p.close()
            state[key] = {"status": status, "file": fname, "note": note,
                          "title": r["title"], "at": datetime.datetime.now().isoformat(timespec="seconds")}
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
            print("%3d %-11s %-40s %s" % (i, status, r["title"][:34], note), flush=True)
            if status == "verify":
                kind = (extra or "").split(":")[1] if ":" in (extra or "") else None
                if a.on_verify == "stop":
                    _pause_for_human(b, a, kind)
                    fail("已暂停并等待人工完成验证；完成后重跑本命令（台账会跳过已完成）", 2)
                if _pause_for_human(b, a, kind):
                    print("  >> 滑块已通过，重试本篇", flush=True)
                    state.pop(key, None)
                    time.sleep(2)
                    _retry_one(b, r, outdir, state, state_path, a)
                else:
                    fail("等待滑块超时；已完成项已记入台账，可稍后重跑", 2)
            time.sleep(a.gap)
    finally:
        b.close()
    okc = sum(1 for v in state.values() if v["status"] == "ok")
    print("下载结束：成功 %d 篇；台账 %s" % (okc, state_path))


# ---------------------------------------------------------------- verify
def cmd_verify(a):
    d = pathlib.Path(a.dir)
    files = sorted(f for f in d.iterdir() if f.suffix.lower() == ".pdf")
    bad = []
    for f in files:
        head = f.open("rb").read(4)
        size = f.stat().st_size
        if head != b"%PDF" or size < 50_000:
            bad.append((f.name, head.decode("latin1"), size))
    print("PDF %d 个 | %.1f MB | 异常 %d" % (len(files), sum(f.stat().st_size for f in files) / 1048576, len(bad)))
    for name, head, size in bad:
        print("  异常:", name[:60], head, size)
    if a.infile:
        src = json.loads(pathlib.Path(a.infile).read_text(encoding="utf-8"))
        keys = [norm(f.name) for f in files]
        miss = [r["title"] for r in src["rows"] if not any(norm(r["title"])[:13] in k for k in keys)]
        print("题录 %d 条，缺 PDF %d 条" % (len(src["rows"]), len(miss)))
        for t in miss[:10]:
            print("  缺:", t[:60])


