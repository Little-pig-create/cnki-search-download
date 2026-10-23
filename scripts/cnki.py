#!/usr/bin/env python3
"""CNKI 检索 / 摘要 / 题录 / 下载 —— 命令行入口。

  cnki.py search    --query 供应链韧性 [--category P0209] [--sort CF] [--pages 2] [--out rec.json]
  cnki.py metadata  --in rec.json [--out rec_meta.json]
  cnki.py abstract  --in rec_meta.json [--out rec_abs.json]
  cnki.py download  --in rec_abs.json --dir PDF [--on-verify wait]
  cnki.py verify    --dir PDF --in rec_abs.json

退出码：0 正常｜2 需人工（验证码/连不上浏览器）｜3 参数或输入错误｜4 其他错误
"""
import argparse
import atexit
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import CDP, PROFILE, SORTS          # noqa: E402
from session import (launch_browser, _port_of,  # noqa: E402
                     shutdown_browser)
import session as _session  # noqa: E402
from search import cmd_search                  # noqa: E402
from detail import cmd_abstract, cmd_metadata  # noqa: E402
from download import cmd_download, cmd_verify  # noqa: E402


def build_parser():
    ap = argparse.ArgumentParser(prog="cnki.py", description="CNKI 检索/摘要/下载")
    ap.add_argument("--cdp", default=CDP, help="已开调试端口的浏览器地址")
    ap.add_argument("--launch", action="store_true",
                    help="由脚本启动 Chrome（用指定 profile），命令结束后自动关闭")
    ap.add_argument("--profile", default=PROFILE, help="--launch 使用的 profile 目录")
    ap.add_argument("--headless", action="store_true", help="--launch 时无头运行（下载会被拦，仅调试用）")
    ap.add_argument("--silent", dest="silent", action="store_true", default=True,
                    help="--launch 时静默：窗口移出屏幕，遇验证码才显示（默认）")
    ap.add_argument("--no-silent", dest="silent", action="store_false", help="正常显示窗口")
    ap.add_argument("--port", type=int, default=None, help="--launch 的调试端口")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="检索并按页取题录")
    s.add_argument("--query", required=True)
    s.add_argument("--field", default="SU")
    s.add_argument("--operator", default=None, help="默认 SU→TOPRANK，其它→DEFAULT")
    s.add_argument("--category", default=None,
                   help="来源类别，逗号可多选：P01/P0209/P0201/P13/P12/P0210/P0202")
    s.add_argument("--journal-only", action="store_true", help="期刊单库模式（计数与总库不同）")
    s.add_argument("--sort", default="PT", choices=list(SORTS))
    s.add_argument("--order", default="DESC", choices=["DESC", "ASC"])
    s.add_argument("--pages", type=int, default=1)
    s.add_argument("--size", type=int, default=50, choices=[10, 20, 50])
    s.add_argument("--delay", type=float, default=1.2)
    s.add_argument("--foreign", action="store_true", help="外文库（无 PDF）")
    s.add_argument("--exclude-noise", action="store_true", help="剔除资讯/商业源（外文库常用）")
    s.add_argument("--out", default=None)
    s.add_argument("--browser", action="store_true", help="强制用浏览器发检索请求")
    s.set_defaults(func=cmd_search)

    md = sub.add_parser("metadata", help="补全卷/期/页/年/DOI/ISSN")
    md.add_argument("--in", dest="infile", required=True)
    md.add_argument("--limit", type=int, default=0)
    md.add_argument("--delay", type=float, default=0.5)
    md.add_argument("--out", default=None)
    md.set_defaults(func=cmd_metadata)

    ab = sub.add_parser("abstract", help="逐篇抓摘要/关键词/基金")
    ab.add_argument("--in", dest="infile", required=True)
    ab.add_argument("--limit", type=int, default=0)
    ab.add_argument("--delay", type=float, default=0.8)
    ab.add_argument("--out", default=None)
    ab.add_argument("--browser", action="store_true", help="强制用浏览器取摘要")
    ab.set_defaults(func=cmd_abstract)

    dl = sub.add_parser("download", help="下载 PDF（遇到验证码会暂停提醒）")
    dl.add_argument("--in", dest="infile", required=True)
    dl.add_argument("--dir", required=True)
    dl.add_argument("--limit", type=int, default=0)
    dl.add_argument("--gap", type=float, default=10.0, help="篇间隔秒；并发时可设 0–2")
    dl.add_argument("--concurrency", type=int, default=1, choices=[1, 2, 3, 4, 5, 6],
                    help="并发下载标签数（默认 1；实测 3 并发约快 2.5 倍）")
    dl.add_argument("--wait", type=int, default=30, help="单篇等文件落盘的秒数")
    dl.add_argument("--state", default=None, help="台账路径（默认在 --dir 下）")
    dl.add_argument("--notify", dest="notify", action="store_true", default=True,
                    help="验证码时发桌面通知（默认开）")
    dl.add_argument("--no-notify", dest="notify", action="store_false", help="关闭通知")
    dl.add_argument("--probe", action="store_true", help="先试 XHR 快通道")
    dl.add_argument("--on-verify", choices=["stop", "wait"], default="wait",
                    help="wait=暂停等人（默认）；stop=立即退出")
    dl.add_argument("--verify-timeout", type=int, default=600,
                    help="等待人工完成验证的最长秒数")
    dl.set_defaults(func=cmd_download)

    vf = sub.add_parser("verify", help="校验 PDF 与题录一致性")
    vf.add_argument("--dir", required=True)
    vf.add_argument("--in", dest="infile", default=None)
    vf.set_defaults(func=cmd_verify)
    return ap


def main():
    a = build_parser().parse_args()
    _session.SILENT = bool(a.silent)
    if a.launch:
        a.cdp = launch_browser(a.port or _port_of(a.cdp), a.profile, a.headless, silent=a.silent)
    atexit.register(shutdown_browser)
    try:
        a.func(a)
    finally:
        shutdown_browser()


if __name__ == "__main__":
    main()
