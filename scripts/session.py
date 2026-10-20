"""浏览器与 HTTP 会话（Cookie 不落盘到调用方）。"""
import json
import os
import pathlib
import re
import time

from utils import CDP, CHROME, PROFILE, _PROCS, fail

class Browser:
    def __init__(self, cdp=CDP):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            fail("需要 playwright：pip install playwright", 3)
        self._p = sync_playwright().start()
        try:
            self.b = self._p.chromium.connect_over_cdp(cdp)
        except Exception:
            fail("连不上 CDP %s —— 用 --remote-debugging-port 启动已授权 Chrome，或加 --launch 让脚本自启" % cdp, 2)
        ctx = self.b.contexts[0]
        pages = [p for p in ctx.pages if "cnki.net" in p.url]
        self.ctx = ctx
        self.page = pages[0] if pages else ctx.new_page()
        if "cnki.net" not in self.page.url:
            self.page.goto("https://kns.cnki.net/kns8s/defaultresult/index",
                           wait_until="domcontentloaded", timeout=40000)
            self.page.wait_for_timeout(1500)

    def close(self):
        try:
            self._p.stop()
        except Exception:
            pass



# ---------------------------------------------------------------- 自启浏览器
def _port_of(cdp):
    m = re.search(r":(\d+)", cdp or "")
    return int(m.group(1)) if m else 9222


def launch_browser(port=9222, profile=PROFILE, headless=False, timeout=25):
    """由脚本拉起 Chrome 并接管生命周期；退出时关闭它启动的进程。"""
    import subprocess, urllib.request
    url = "http://127.0.0.1:%d/json/version" % port
    try:
        urllib.request.urlopen(url, timeout=1)
        fail("端口 %d 上已有浏览器，去掉 --launch 直接复用即可" % port, 3)
    except Exception:
        pass
    args = [CHROME, "--user-data-dir=" + profile, "--remote-debugging-port=%d" % port,
            "--no-first-run", "--no-default-browser-check", "--disable-background-networking"]
    if headless:
        args.append("--headless=new")
    args.append("about:blank")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    _PROCS[0] = proc
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            print("已启动 Chrome (pid %d, port %d%s)" % (proc.pid, port, ", headless" if headless else ""))
            return "http://127.0.0.1:%d" % port
        except Exception:
            if proc.poll() is not None:
                fail("Chrome 启动即退出（profile 可能被另一个 Chrome 占用）：%s" % profile, 2)
            time.sleep(0.5)
    try:
        os.killpg(proc.pid, 15)
    except Exception:
        pass
    fail("等待 Chrome 调试端口超时", 2)


def shutdown_browser():
    proc = _PROCS[0]
    if not proc or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, 15)
        for _ in range(20):
            if proc.poll() is not None:
                break
            time.sleep(0.25)
        if proc.poll() is None:
            os.killpg(proc.pid, 9)
        print("已关闭本脚本启动的 Chrome")
    except Exception:
        pass



# ---------------------------------------------------------------- HTTP 通道
COOKIE_CACHE = pathlib.Path(os.environ.get("CNKI_COOKIE_CACHE",
                                           str(pathlib.Path.home()/".cache/cnki-cookies.json")))
COOKIE_TTL = int(os.environ.get("CNKI_COOKIE_TTL", "3600"))
UA_DEFAULT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/155.0.0.0 Safari/537.36")


def _load_cookies(max_age=COOKIE_TTL):
    if not COOKIE_CACHE.exists():
        return None
    try:
        d = json.loads(COOKIE_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if time.time() - d.get("saved_at", 0) > max_age:
        return None
    return d


def _save_cookies(cookies, ua):
    COOKIE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_CACHE.write_text(json.dumps({"saved_at": time.time(), "ua": ua, "cookies": cookies},
                                       ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(COOKIE_CACHE, 0o600)
    except Exception:
        pass


def harvest_cookies(profile=PROFILE, headless=True, port=9400):
    """取 Cookie：优先借用已在跑的调试浏览器，没有再短暂启动一个。"""
    try:
        b = Browser(CDP)
        try:
            cookies = b.ctx.cookies("https://kns.cnki.net") + b.ctx.cookies("https://bar.cnki.net")
            ua = b.page.evaluate("() => navigator.userAgent")
        finally:
            b.close()
        _save_cookies(cookies, ua)
        print("已从现有浏览器取 Cookie（%d 条）→ %s" % (len(cookies), COOKIE_CACHE))
        return {"saved_at": time.time(), "ua": ua, "cookies": cookies}
    except SystemExit:
        pass
    cdp = launch_browser(port, profile, headless)
    try:
        b = Browser(cdp)
        try:
            cookies = b.ctx.cookies("https://kns.cnki.net") + b.ctx.cookies("https://bar.cnki.net")
            ua = b.page.evaluate("() => navigator.userAgent")
        finally:
            b.close()
        _save_cookies(cookies, ua)
        print("已刷新 Cookie 缓存（%d 条）→ %s" % (len(cookies), COOKIE_CACHE))
        return {"saved_at": time.time(), "ua": ua, "cookies": cookies}
    finally:
        shutdown_browser()


class Http:
    """只带 Cookie 的轻量客户端：检索/摘要用它，不需要浏览器常驻。"""

    def __init__(self, jar=None):
        jar = jar or _load_cookies() or harvest_cookies()
        self.ua = jar.get("ua") or UA_DEFAULT
        self.cookie = "; ".join("%s=%s" % (c["name"], c["value"]) for c in jar["cookies"])
        self.jar = jar

    def _req(self, url, data=None, referer=None, extra=None):
        import urllib.request
        h = {"User-Agent": self.ua, "Cookie": self.cookie,
             "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
             "Referer": referer or "https://kns.cnki.net/kns8s/defaultresult/index"}
        if data is not None:
            h.update({"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                      "X-Requested-With": "XMLHttpRequest", "Origin": "https://kns.cnki.net"})
        h.update(extra or {})
        req = urllib.request.Request(url, data=data, headers=h,
                                     method="POST" if data is not None else "GET")
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")

    def grid(self, body):
        return self._req("https://kns.cnki.net/kns8s/brief/grid", data=body.encode())[1]

    def page(self, url):
        return self._req(url)[1]


