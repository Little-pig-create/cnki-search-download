"""浏览器与 HTTP 会话（Cookie 不落盘到调用方）。"""
import json
import os
import pathlib
import subprocess
import sys
import re
import time

from utils import CDP, CHROME, PROFILE, _PROCS, fail

class Browser:
    def __init__(self, cdp=CDP, quiet=False):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            fail("需要 playwright：pip install playwright", 3)
        self._p = sync_playwright().start()
        try:
            self.b = self._p.chromium.connect_over_cdp(cdp)
        except Exception:
            try:                     # 连不上时先释放 playwright，避免同进程二次启动冲突
                self._p.stop()
            except Exception:
                pass
            if quiet:
                sys.exit(2)
            fail("连不上 CDP %s —— 用 --remote-debugging-port 启动已授权 Chrome，或加 --launch 让脚本自启" % cdp, 2)
        ctx = self.b.contexts[0]
        pages = [p for p in ctx.pages if "cnki.net" in p.url]
        self.ctx = ctx
        self.page = pages[0] if pages else ctx.new_page()
        # 只对"本脚本启动的"浏览器静默；用户自己的窗口不动
        owned = bool(_PROCS[0] and _PROCS[0].poll() is None)
        if SILENT and owned:
            _set_bounds(self, state="minimized")         # 先最小化，再导航（缩短闪现）
        if "cnki.net" not in self.page.url:
            self.page.goto("https://kns.cnki.net/kns8s/defaultresult/index",
                           wait_until="domcontentloaded", timeout=40000)
            self.page.wait_for_timeout(1500)
            if SILENT and owned:
                _set_bounds(self, state="minimized")     # 导航后再压一次

    def close(self):
        try:
            self._p.stop()
        except Exception:
            pass



# ---------------------------------------------------------------- 自启浏览器
def _port_of(cdp):
    m = re.search(r":(\d+)", cdp or "")
    return int(m.group(1)) if m else 9222


def launch_browser(port=9222, profile=PROFILE, headless=False, timeout=25, silent=False):
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
    if silent and not headless:
        # 静默：窗口放到屏幕外，遇到验证码再移回可见区域
        args += ["--window-position=-32000,-32000", "--window-size=1280,900"]
    args.append("about:blank")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    _PROCS[0] = proc
    if silent and not headless:
        for _ in range(12):          # 尽早隐藏窗口，减少闪现
            if _unmap_window(proc.pid):
                break
            time.sleep(0.25)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            print("已启动 Chrome (pid %d, port %d%s)" % (
                proc.pid, port, ", headless" if headless else (", silent" if silent else "")))
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
SILENT = True   # 默认静默：窗口移出屏幕，遇到验证码再显示
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


def _harvest_worker(port, profile, headless=True):
    """子进程入口：启动浏览器取 Cookie，打印一行 JSON。"""
    cdp = launch_browser(port, profile, headless=headless, silent=True)
    try:
        b = Browser(cdp)
        try:
            cookies = b.ctx.cookies("https://kns.cnki.net") + b.ctx.cookies("https://bar.cnki.net")
            ua = b.page.evaluate("() => navigator.userAgent")
        finally:
            b.close()
        print(json.dumps({"ua": ua, "cookies": cookies}, ensure_ascii=False))
    finally:
        shutdown_browser()


def harvest_cookies(profile=PROFILE, headless=False, port=9400):
    """取 Cookie：优先借用已在跑的调试浏览器；否则用子进程启动一个（避免事件循环冲突）。"""
    try:
        b = Browser(CDP, quiet=True)
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

    # 没有可借用的浏览器：用子进程启动，取完即关
    import subprocess
    cmd = [sys.executable, os.path.abspath(__file__), "--harvest",
           "--port", str(port), "--profile", profile]
    if headless:
        cmd.append("--headless")   # 无头会被站点拦，默认用有头+最小化
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    for line in reversed((r.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            data = json.loads(line)
            _save_cookies(data["cookies"], data["ua"])
            print("已刷新 Cookie 缓存（%d 条）→ %s" % (len(data["cookies"]), COOKIE_CACHE))
            return {"saved_at": time.time(), "ua": data["ua"], "cookies": data["cookies"]}
    fail("取 Cookie 失败：%s" % ((r.stderr or r.stdout or "").strip().splitlines() or ["未知错误"])[-1][:160], 2)


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


def _unmap_window(pid):
    """有 xdotool 时把窗口直接隐藏（零闪现）；没有就跳过。"""
    import shutil
    if not shutil.which("xdotool"):
        return False
    try:
        ids = subprocess.run(["xdotool", "search", "--pid", str(pid)],
                             capture_output=True, text=True, timeout=5).stdout.split()
        for wid in ids:
            subprocess.run(["xdotool", "windowunmap", wid], timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return bool(ids)
    except Exception:
        return False


def _map_window(pid):
    import shutil
    if not shutil.which("xdotool"):
        return False
    try:
        ids = subprocess.run(["xdotool", "search", "--pid", str(pid)],
                             capture_output=True, text=True, timeout=5).stdout.split()
        for wid in ids:
            subprocess.run(["xdotool", "windowmap", wid], timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["xdotool", "windowactivate", wid], timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return bool(ids)
    except Exception:
        return False


def _set_bounds(b, left=0, top=0, width=1280, height=900, state="normal"):
    """设置窗口位置/状态；state='minimized' 即静默。"""
    try:
        sess = b.ctx.new_cdp_session(b.page)
        wid = sess.send("Browser.getWindowForTarget")["windowId"]
        if state != "normal":
            sess.send("Browser.setWindowBounds", {"windowId": wid,
                      "bounds": {"windowState": state}})
        else:
            sess.send("Browser.setWindowBounds", {"windowId": wid,
                      "bounds": {"windowState": "normal"}})
            sess.send("Browser.setWindowBounds", {"windowId": wid,
                      "bounds": {"left": left, "top": top,
                                 "width": width, "height": height}})
        return True
    except Exception:
        return False


def show_window(b):
    """把静默窗口移回可见区域并前置（验证码时用）。"""
    try:
        target = b.page.context.new_cdp_session(b.page)
    except Exception:
        target = None
    if _PROCS[0] and _PROCS[0].poll() is None:
        _map_window(_PROCS[0].pid)
    if _set_bounds(b, 80, 60):
        try:
            b.ctx.new_cdp_session(b.page).send("Page.bringToFront")
        except Exception:
            pass
        return True
    try:
        b.page.bring_to_front()
        return True
    except Exception:
        return False


if __name__ == "__main__" and "--harvest" in sys.argv:
    _ap_port, _ap_profile, _ap_headless = 9400, PROFILE, False
    _argv = sys.argv[sys.argv.index("--harvest") + 1:]
    if "--port" in _argv:
        _ap_port = int(_argv[_argv.index("--port") + 1])
    if "--profile" in _argv:
        _ap_profile = _argv[_argv.index("--profile") + 1]
    _ap_headless = "--headless" in _argv
    _harvest_worker(_ap_port, _ap_profile, _ap_headless)
