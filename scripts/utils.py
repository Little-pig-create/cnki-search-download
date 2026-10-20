"""共享常量与工具。"""
import html as H
import os
import pathlib
import re
import shutil
import subprocess
import sys

CDP = os.environ.get("CNKI_CDP", "http://127.0.0.1:9222")
CHROME = os.environ.get("CNKI_CHROME", "google-chrome")
PROFILE = os.environ.get("CNKI_PROFILE", str(pathlib.Path.home()/".cache/cnki-chrome-profile"))
_PROCS = ["" * 0]
KUAKU = ("YSTT4HG0,LSTPFY1C,EMRPGLPA,JUP3MUPD,MPMFIG1A,"
         "WQ0UVIAA,BLZOG7CK,PWFIRAGL,NN3FJMUV,NLBO1Z6R")
SIZES = (10, 20, 50)
CATS = {"P01": "北大核心", "P0209": "CSSCI", "P13": "AMI", "P12": "WJCI",
        "P0210": "CSCD", "P0202": "EI", "P0201": "SCI"}
SORTS = {"FFD": "相关度", "PT": "发表时间", "CF": "被引", "DFR": "下载", "ZH": "综合"}
FIELDS = {"SU": "主题", "TKA": "篇关摘", "KY": "关键词", "TI": "篇名", "FT": "全文",
          "AB": "摘要", "CO": "小标题", "RF": "参考文献", "AU": "作者", "FI": "第一作者",
          "RP": "通讯作者", "AF": "作者单位", "FU": "基金", "CLC": "分类号",
          "LY": "文献来源", "DOI": "DOI"}
# 只有这几个字段在实测中需要/接受特定运算符
OP_DEFAULT, OP_TOP = "DEFAULT", "TOPRANK"
# 外文库里混入的资讯/商业源，--exclude-noise 时剔除
NOISE_RE = re.compile(r"(Close\s*-\s*Up|Wireless News|Research and Markets|PR ?Newswire|"
                      r"Business Wire|GlobeNewswire|Market ?Watch|Press Release|Weekly|"
                      r"Magazine|Trade|Report)\b", re.I)


def fail(msg, code=4):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(code)



PDF_LABEL = "\u0050\u0044\u0046\u4e0b\u8f7d"
PUNCT = re.compile(r"[\s\-—_\uff1a:，,、（）()《》\"'\u2019·]")


def norm(s):
    return PUNCT.sub("", s or "")





def plain(s):
    import html as H
    return H.unescape(re.sub(r"<[^>]+>", " ", s)).strip()



CAPTCHA_SLIDER = ("拖动", "拼图", "blockPuzzle", "滑动验证")
CAPTCHA_CLICK = ("依次点击", "点选", "点击验证", "clickWord", "文字点选")


def captcha_kind(text):
    """判断是人机验证页还是普通页面，并区分滑块/点选。"""
    if not text:
        return None
    if any(k in text for k in CAPTCHA_CLICK):
        return "click"
    if any(k in text for k in CAPTCHA_SLIDER):
        return "slider"
    return None


def _notify(msg):
    """需要人工介入时提醒（有 notify-send 才用，失败静默）。"""
    import shutil, subprocess
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.Popen(["notify-send", "-u", "critical", "CNKI 需要你操作", msg],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

# 别名：外部统一用 notify
def notify(msg, *a, **k):
    return _notify(msg, *a, **k)
