"""字体加密站拦截 —— 专项测试。

用户诉求：「下载了 178 章，全是缺字的正文，但我不知道」。

这类站点（如番茄小说）把常用汉字替换成私有区码位（U+E4xx），
只有浏览器加载它专用字体才显示正常。程序拿到的是占位符，
解析时还会被清洗掉，于是正文变成「“喜喜，喜！”」——看着通顺，
实则缺一半字，比乱码更危险。

产品行为必须是：**抓第一页就认出来，整本拒绝，一个文件都不落。**

同一族问题还有「付费章节」：站点对未订阅用户只返回开头一小段，
下载照样算成功，账本上写着已完成，用户读的时候才发现只有 130 字。
那里的产品行为是：**照下，但标出来**，别让「下载完成」盖住事实。

跑：python tests/test_guard.py
"""
import io
import logging
import os
import shutil
import sys
import tempfile
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from novel_dl import fetchers, library, log as nlog          # noqa: E402
from novel_dl.engine import Engine                           # noqa: E402
from novel_dl.guard import (EncryptedContent, check_encrypted,  # noqa: E402
                            detect_font_encryption, explain, preview_reason)

nlog.setup()
logging.getLogger("novel_dl").setLevel(logging.CRITICAL)   # 测试输出保持干净

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  <- {detail}"))


# ---------------------------------------------------------------------------
# 样板：真实番茄站正文的形状（私有区占位符 + @font-face）
# ---------------------------------------------------------------------------
FONT_FACE = ('<style>@font-face{font-family:DNMrHsV173Pd4pgy;'
             'src:url(https://lf6-awef.bytetos.com/obj/awesome-font/c/x.woff2)'
             'format("woff2")}</style>')
PUA_LINE = "“喜\ue483喜\ue483，\ue423喜\ue483！”雷火馆\ue44d院，紧闭\ue4f3院"


def enc_page(n=1):
    """加密正文页：私有区字符出现 400 次以上，并声明了专用字体。"""
    return (f'<html><head><title>第{n}章</title>{FONT_FACE}</head><body>'
            f'<h1>第{n}章</h1><div id="content">'
            + PUA_LINE * 100 + '</div></body></html>')


GOOD_TEXT = "夜色沉沉，他提灯走过长街，远处传来打更的声音。风从巷口灌进来，吹得灯焰一阵摇晃。"
GOOD_PAGE = ('<html><head><title>第1章</title></head><body><h1>第1章</h1>'
             '<div id="content">' + GOOD_TEXT * 12 + '</div></body></html>')

# 只用图标字体的普通站点：@font-face 有，但私有区字符只出现在几个图标里
ICON_PAGE = ('<html><head><title>第1章</title><style>@font-face{'
             'font-family:iconfont;src:url(/icon.woff2)}</style></head><body>'
             '<span class="ico">\ue600\ue601\ue602</span><div id="content">'
             + GOOD_TEXT * 12 + '</div></body></html>')


# ---------------------------------------------------------------------------
# 假站点（两种：加密站 / 正常站）
# ---------------------------------------------------------------------------
BOOK = "https://enc.example/book/9/"
PLAIN_BOOK = "https://plain.example/book/9/"


class EncFetcher(fetchers.BaseFetcher):
    name = "fake-enc"
    MIN_DELAY = MAX_DELAY = 0
    calls = []

    def fetch(self, url):
        type(self).calls.append(url)
        if url == BOOK:
            return ('<html><head><title>加密小说_在线阅读</title></head><body>'
                    + "".join(f'<a href="/book/9/{i}.html">第{i}章</a>'
                              for i in range(1, 21)) + '</body></html>')
        n = url.split("/")[-1].split(".")[0]
        self.polite_sleep()
        return enc_page(int(n))


class PlainFetcher(fetchers.BaseFetcher):
    name = "fake-plain"
    MIN_DELAY = MAX_DELAY = 0
    calls = []

    def fetch(self, url):
        type(self).calls.append(url)
        if url == PLAIN_BOOK:
            return ('<html><head><title>正常小说_在线阅读</title></head><body>'
                    + "".join(f'<a href="/book/9/{i}.html">第{i}章</a>'
                              for i in range(1, 4)) + '</body></html>')
        self.polite_sleep()
        return GOOD_PAGE


fetchers.FETCHERS["fake-enc"] = EncFetcher
fetchers.FETCHERS["fake-plain"] = PlainFetcher


# ---------------------------------------------------------------------------
# 付费站：正常章全文，付费章只给开头一小段预览
# ---------------------------------------------------------------------------
PAY_BOOK = "https://pay.example/book/7/"
PAY_SHORT = "陆泽说着，从助理手里拿过一份文件，直接拍在旁边的办公桌上，"
PAY_FLAG = '<script>var meta={"isfree": false}</script>'
PAY_TIP = '<p class="vip">本章为付费章节，订阅后可读全文</p>'


def pay_ch_page(n, paid):
    body = PAY_SHORT * 2 if paid else GOOD_TEXT * 10
    extra = (PAY_TIP + PAY_FLAG) if paid else ""
    return (f'<html><head><title>第{n}章</title></head><body>'
            f'<h1>第{n}章</h1><div id="content">{body}</div>{extra}</body></html>')


class PayFetcher(fetchers.BaseFetcher):
    name = "fake-pay"
    MIN_DELAY = MAX_DELAY = 0
    calls = []

    def fetch(self, url):
        type(self).calls.append(url)
        if url == PAY_BOOK:
            return ('<html><head><title>付费小说_在线阅读</title></head><body>'
                    + "".join(f'<a href="/book/7/{i}.html">第{i}章</a>'
                              for i in range(1, 4)) + '</body></html>')
        n = int(url.split("/")[-1].split(".")[0])
        self.polite_sleep()
        return pay_ch_page(n, paid=(n == 3))    # 第 3 章是付费章


fetchers.FETCHERS["fake-pay"] = PayFetcher


def _tmpdir(prefix):
    """临时目录放系统 TEMP：受限环境里工作区对衍生进程只读。"""
    base = os.path.join(tempfile.gettempdir(), "novel-dl-tests")
    path = os.path.join(base, f"{prefix}{uuid.uuid4().hex[:8]}")
    os.makedirs(path, exist_ok=True)
    return path


def _cache_txt(out_dir):
    """返回已落盘的章节文件数（拦下时必须是 0）。"""
    total = 0
    for name in (os.listdir(out_dir) if os.path.isdir(out_dir) else []):
        cache = os.path.join(out_dir, name, "cache")
        if os.path.isdir(cache):
            total += len([f for f in os.listdir(cache) if f.endswith(".txt")])
    return total


# ---------------------------------------------------------------------------
def main():
    print("\n[1] 检测函数：加密正文必须认出来")
    try:
        check_encrypted(enc_page(), "", BOOK)
        check("加密页 → 抛出 EncryptedContent", False, "没有抛出")
    except EncryptedContent as e:
        check("加密页 → 抛出 EncryptedContent", True)
        check("说明里给出可读原因", "字体" in explain(e), explain(e)[:60])

    # 加密站页面里通常还预加载了下一章的 JSON（\u003Cp\u003E…），
    # 那里的占位符密度最高，但那是转义代码。样本必须挑像正文的那一段，
    # 否则用户看到的是一串 "\u003Cp\u003E???，????"。
    json_line = ('\\u003Cp\\u003E' + PUA_LINE * 20 + '\\u003C\\u002Fp\\u003E')
    noisy = (f'<html><head><title>t</title>{FONT_FACE}</head><body>'
             f'<script>var c={{"content":"{json_line}"}}</script>'
             f'<div id="content">{PUA_LINE * 100}</div></body></html>')
    try:
        check_encrypted(noisy, "", BOOK)
        check("含预加载 JSON 的页 → 抛出", False, "没有抛出")
    except EncryptedContent as e:
        check("含预加载 JSON 的页 → 抛出", True)
        check("样本取自正文而非 JSON", "\\u003" not in e.sample and "雷火馆" in e.sample,
              e.sample)

    print("\n[2] 健康页面不能误报")
    check("普通正文页 → 不抛", _no_raise(GOOD_PAGE, ""), "误报了")
    check("只有图标字体的页 → 不抛", _no_raise(ICON_PAGE, ""), "误报了")
    check("空页面 → 不抛", _no_raise("", ""), "误报了")
    check("占比为 0", detect_font_encryption(GOOD_PAGE, GOOD_TEXT * 12) == 0.0)

    print("\n[3] 清洗后仍残留占位符的正文")
    # 解析器已剥掉占位符时，正文里没有 PUA —— 所以主判据看原始 HTML。
    # 但若某个解析器没剥干净，次判据要能兜住。
    dirty = PUA_LINE * 20 + GOOD_TEXT * 3
    try:
        check_encrypted("", dirty, BOOK)
        check("正文占比超阈值 → 抛出", False, "没有抛出")
    except EncryptedContent as e:
        check("正文占比超阈值 → 抛出", True)
        check("占比 > 15%", e.ratio > 0.15, str(e.ratio))

    print("\n[4] 串行引擎：整本拒绝，一个文件都不落")
    out = os.path.join(_tmpdir("enc_"), "books")
    try:
        e = Engine(BOOK, driver="fake-enc", out_dir=out)
        e.run()
        check("抛 SystemExit(2)", False, "run() 正常返回了")
    except SystemExit as ex:
        check("抛 SystemExit(2)", ex.code == 2, str(ex.code))
    check("没有落盘任何章节文件", _cache_txt(out) == 0, f"{_cache_txt(out)} 个文件")
    check("没有生成 TXT", not any(f.endswith(".txt") for f in _walk(out)),
          str([os.path.basename(f) for f in _walk(out)]))
    check("请求数 ≤ 3（认出即停）", len(EncFetcher.calls) <= 3,
          f"{len(EncFetcher.calls)} 次：{EncFetcher.calls}")
    shutil.rmtree(os.path.dirname(out), ignore_errors=True)

    print("\n[5] 并发引擎：同样不能先留下半残章节")
    out = os.path.join(_tmpdir("encw_"), "books")
    EncFetcher.calls = []
    try:
        e = Engine(BOOK, driver="fake-enc", out_dir=out, workers=4)
        e.run()
        check("抛 SystemExit(2)", False, "run() 正常返回了")
    except SystemExit as ex:
        check("抛 SystemExit(2)", ex.code == 2, str(ex.code))
    check("并发下仍是 0 个章节文件", _cache_txt(out) == 0, f"{_cache_txt(out)} 个文件")
    check("并发下请求数 ≤ 6", len(EncFetcher.calls) <= 6,
          f"{len(EncFetcher.calls)} 次")
    shutil.rmtree(os.path.dirname(out), ignore_errors=True)

    print("\n[6] 拦下时把原因交给上层（webui 靠它显示说明）")
    out = os.path.join(_tmpdir("encb_"), "books")
    eng = Engine(BOOK, driver="fake-enc", out_dir=out)
    try:
        eng.run()
    except SystemExit:
        pass
    check("engine.blocked 是 EncryptedContent", isinstance(eng.blocked, EncryptedContent),
          str(eng.blocked))
    shutil.rmtree(os.path.dirname(out), ignore_errors=True)

    print("\n[7] 正常站点不受影响")
    out = os.path.join(_tmpdir("plain_"), "books")
    txt = Engine(PLAIN_BOOK, driver="fake-plain", out_dir=out).run()
    body = open(txt, encoding="utf-8").read()
    check("3 章全部下载", body.count("=== 第") == 3, str(body.count("=== 第")))
    check("正文完整", "提灯走过长街" in body)
    shutil.rmtree(os.path.dirname(out), ignore_errors=True)

    print("\n[8] 付费章节：只拿到免费预览，必须认出来")
    check("付费标记 + 短正文 → 判定预览",
          preview_reason(pay_ch_page(9, paid=True), "第9章\n\n" + PAY_SHORT * 2)
          is not None)
    check("付费标记 + 完整正文 → 不判预览（免费章页面也挂充值入口）",
          preview_reason(pay_ch_page(9, paid=True), "第9章\n\n" + GOOD_TEXT * 10)
          is None)
    check("普通页 + 短正文 → 不判预览",
          preview_reason(GOOD_PAGE, "第1章\n\n" + PAY_SHORT) is None,
          "误报")
    check("原因里写明字数",
          "字" in (preview_reason(pay_ch_page(9, paid=True),
                                  "第9章\n\n" + PAY_SHORT * 2) or ""),
          str(preview_reason(pay_ch_page(9, paid=True), "第9章\n\n" + PAY_SHORT * 2)))

    print("\n[9] 引擎：预览章照下但记账，且不会每次重跑都重下")
    out = os.path.join(_tmpdir("pay_"), "books")
    eng = Engine(PAY_BOOK, driver="fake-pay", out_dir=out)
    eng.run()
    st = eng.prog.state
    check("账本标记了第 3 章是预览", "2" in (st.get("preview") or {}),
          str(st.get("preview")))
    check("预览章仍算已完成", 2 in st["done"], str(st["done"]))
    check("三章都落盘了", _cache_txt(out) == 3, str(_cache_txt(out)))
    check("TXT 仍导出（部分内容也有价值）",
          any(f.endswith(".txt") and "cache" not in f for f in _walk(out)),
          str([os.path.basename(f) for f in _walk(out)]))

    PayFetcher.calls = []
    Engine(PAY_BOOK, driver="fake-pay", out_dir=out).run()
    chapters_again = [u for u in PayFetcher.calls if u != PAY_BOOK]
    check("第二次运行不再请求任何章节", not chapters_again, str(chapters_again))

    book = library.scan(out)[0]
    by_no = {c["no"]: c for c in book["chapters"]}
    check("书架把预览章标成 warn", by_no[3]["level"] == "warn", str(by_no[3]))
    check("书架写明了原因", "付费" in " ".join(by_no[3]["issues"]),
          str(by_no[3]["issues"]))
    check("正常章没被误标", by_no[1]["level"] == "ok", str(by_no[1]))
    shutil.rmtree(os.path.dirname(out), ignore_errors=True)

    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for name in FAIL:
            print(f"  ❌ {name}")
        return 1
    return 0


def _no_raise(html, text):
    try:
        check_encrypted(html, text, BOOK)
        return True
    except EncryptedContent:
        return False


def _walk(root):
    found = []
    for dirpath, _dirs, files in os.walk(root):
        found.extend(os.path.join(dirpath, f) for f in files)
    return found


if __name__ == "__main__":
    code = main()
    shutil.rmtree(os.path.join(tempfile.gettempdir(), "novel-dl-tests"),
                  ignore_errors=True)
    sys.exit(code)