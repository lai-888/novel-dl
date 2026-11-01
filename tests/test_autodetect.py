"""自动推断站点规则 —— 专项测试。"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

os.environ["NOVEL_DL_CACHE"] = os.path.join(
    tempfile.gettempdir(), "novel-dl-tests", "learned.json")

from bs4 import BeautifulSoup  # noqa: E402

from novel_dl import autodetect  # noqa: E402
from novel_dl.fetchers import BaseFetcher  # noqa: E402
from novel_dl.parser import SiteRuleParser  # noqa: E402

PASS, FAIL = [], []
BODY = "夜色沉沉，他提灯走过长街，远处传来打更的声音。" * 12


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  <- {detail}"))


class Fake(BaseFetcher):
    """按 base_url + 相对路径查表。"""

    MIN_DELAY = MAX_DELAY = 0

    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url, quick=False):
        if url not in self.pages:
            raise KeyError(f"未准备的 URL: {url}")
        return self.pages[url]


def _pages(base, toc_html, ch_html, first_href):
    """把「目录页 + 第一章」组装成 {绝对URL: html}。"""
    from urllib.parse import urljoin
    return {base: toc_html, urljoin(base, first_href): ch_html}

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


def site_dl(base):
    """老式 dl/dd 列表。"""
    toc = ("<html><head><title>甲站_在线阅读</title></head><body>"
           '<div id="maininfo"><h1>甲站小说</h1></div>'
           '<div id="list"><dl>' +
           "".join(f'<dd><a href="/book/1/{i}.html">第{i}章 标题{i}</a></dd>'
                   for i in range(1, 21)) +
           '</dl></div></body></html>')
    ch = (f'<html><head><title>第1章</title></head><body>'
          f'<div class="bookname"><h1>第1章 标题1</h1></div>'
          f'<div id="content">{BODY}</div>'
          f'<div class="bottem"><a href="/book/1/2.html">下一章</a></div></body></html>')
    return _pages(base, toc, ch, "/book/1/1.html")


def site_ul(base):
    """ul/li，只有 class 没有 id。"""
    toc = ("<html><head><title>乙站</title></head><body>"
           '<nav class="top"><a href="/">首页</a><a href="/list">排行</a></nav>'
           '<ul class="chapter-list">' +
           "".join(f'<li><a href="/read/9/{i}.html">第{i}章 乙{i}</a></li>'
                   for i in range(1, 16)) +
           '</ul></body></html>')
    ch = (f'<html><head><title>第1章 乙1</title></head><body>'
          f'<h2>第1章 乙1</h2>'
          f'<article class="read-content">{BODY}</article></body></html>')
    return _pages(base, toc, ch, "/read/9/1.html")


def site_table(base):
    """table 布局（很老的站）。"""
    rows = "".join(
        f'<tr><td><a href="/txt/5/{i}.html">第{i}章 丙{i}</a></td></tr>'
        for i in range(1, 13))
    toc = ("<html><head><title>丙站</title></head><body>"
           f'<table id="chaptertable">{rows}</table></body></html>')
    ch = (f'<html><head><title>第1章 丙1</title></head><body>'
          f'<table><tr><td><h1>第1章 丙1</h1></td>'
          f'<td id="txt">{BODY}</td></tr></table></body></html>')
    return _pages(base, toc, ch, "/txt/5/1.html")


def site_nested(base):
    """div 层层嵌套，正文在很深的地方。"""
    toc = ("<html><head><title>丁站</title></head><body>"
           '<div class="wrap"><div class="inner"><div class="box">'
           '<div class="catalog">' +
           "".join(f'<p><a href="/n/7/{i}.html">第{i}章 丁{i}</a></p>'
                   for i in range(1, 18)) +
           '</div></div></div></div></body></html>')
    ch = (f'<html><head><title>第1章 丁1</title></head><body>'
          f'<div class="wrap"><div class="inner"><div class="article">'
          f'<h1>第1章 丁1</h1><div class="txt"><p>{BODY}</p><p>{BODY}</p></div>'
          f'</div></div></div></body></html>')
    return _pages(base, toc, ch, "/n/7/1.html")


def run_site(name, builder, expect_toc_min=10):
    print(f"\n{'─'*50}\n[{name}]")
    base = "http://x.example/book/1/"
    pages = builder(base)
    f = Fake(pages)

    rule = autodetect.detect(base, f)
    if not rule:
        check("推断出规则", False, "detect 返回 None")
        return
    check("推断出规则", True)
    check("有 toc_item", bool(rule.get("toc_item")), str(rule))

    p = SiteRuleParser(rule)
    title, toc = p.parse_toc(pages[base], base)
    check(f"目录解析出 ≥{expect_toc_min} 章", len(toc) >= expect_toc_min, str(len(toc)))

    first_key = [k for k in pages if k != base][0]
    ctitle, body = p.parse_chapter(pages[first_key])
    check("正文长度合理", len(body) > 200, str(len(body)))
    check("正文没混进导航词", "首页" not in body and "排行" not in body, body[:60])
    check("标题解析出来", bool(ctitle) and len(ctitle) < 80, ctitle)


def test_dom_index():
    print("\n[8] DOM 索引：查桶必须和 soupsieve 给一样的结果")
    page = ('<html><body><div id="wrap"><div class="hd"><a href="/">首页</a></div>'
            '<div class="main"><div class="main"><dl>'
            + "".join(f'<dd class="c"><a href="/b/{i}.html">第{i}章 甲{i}</a></dd>'
                      for i in range(1, 6))
            + '</dl></div></div><div class="side"><a href="/x.html">热门</a></div>'
              '</div><p class="note">说明</p></body></html>')
    soup = BeautifulSoup(page, "lxml")
    idx = autodetect.DomIndex(soup)

    same = True
    for sel in ["a", "dd", "#wrap", ".main", "div.main", "dd.c a",
                "#wrap > div.main a", "div.main dl a", "#wrap .side a"]:
        fast = idx.select(sel)
        slow = soup.select(sel)
        if fast is None or [id(x) for x in fast] != [id(x) for x in slow]:
            same = False
            check(f"索引结果与 soupsieve 一致（{sel}）", False,
                  f"快 {0 if fast is None else len(fast)} / 慢 {len(slow)}")
    check("索引结果与 soupsieve 一致", same)

    check("带伪类的选择器交回 soupsieve", idx.select("dd:first-child") is None)
    check("组合逗号的选择器交回 soupsieve", idx.select("a, dd") is None)
    check("ID 桶只收真正那个节点",
          [x.get("id") for x in idx.candidates("#wrap")] == ["wrap"])

    node = soup.select_one(".note")
    check("_is_unique 用索引后仍要求同一对象",
          autodetect._is_unique(soup, ".note", node, idx) is True)
    other = soup.select_one(".hd")
    check("_is_unique 认得出首节点不是它",
          autodetect._is_unique(soup, "a", other, idx) is False)
    check("同名 class 有两个时不认唯一",
          autodetect._is_unique(soup, ".main", node, idx) is False)


def main():
    print("=" * 50)
    print("自动推断：四种截然不同的站点结构")
    print("=" * 50)

    run_site("dl/dd 老式列表", site_dl)
    run_site("ul/li 纯 class", site_ul)
    run_site("table 布局", site_table, expect_toc_min=8)
    run_site("深层 div 嵌套", site_nested, expect_toc_min=10)

    print(f"\n{'─'*50}\n[缓存与失效]")
    host = "cache-test.example"
    autodetect.save_learned(host, {"host": host, "toc_item": "#list dd a"})
    got = autodetect.load_learned(host)
    check("推断结果能缓存", got and got.get("toc_item") == "#list dd a", str(got))
    autodetect.forget(host)
    check("缓存能清掉", autodetect.load_learned(host) is None)

    print(f"\n{'─'*50}\n[推断失败要优雅返回]")
    f_bad = Fake({"http://bad.example/book/1/":
                  "<html><body><p>没有章节链接的页面</p></body></html>"})
    r = autodetect.detect("http://bad.example/book/1/", f_bad)
    check("无章节链接时返回 None 而不是崩溃", r is None, str(r))

    print(f"\n{'─'*50}\n[选择器唯一性]")
    html = ('<html><body><div class="a"><p>1</p></div>'
            '<div class="a"><p>2</p></div></body></html>')
    soup = BeautifulSoup(html, "lxml")
    node = soup.find_all("p")[1]
    sel = autodetect._selector_for(soup, node)
    found = soup.select(sel)
    check("生成的选择器能唯一定位", len(found) == 1, f"{sel} -> {len(found)}")
    check("唯一选中的就是目标节点", found[0].get_text() == "2", sel)

    print(f"\n{'─'*50}\n[--no-auto 必须关掉推断]")
    from novel_dl import engine as eng_mod
    from novel_dl.parser import get_parser

    calls = []
    orig_detect = autodetect.detect

    def spy(*a, **kw):
        calls.append(a[0] if a else "?")
        return orig_detect(*a, **kw)

    autodetect.detect = spy
    try:
        get_parser("http://noauto.example/book/1/", fetcher=Fake({}), auto=False)
        check("关掉自动推断时不调用 detect", not calls, str(calls))
        get_parser("http://noauto2.example/book/1/", fetcher=Fake({}), auto=True,
                   lazy_auto=True)
        check("开启自动推断时交给惰性推断（首屏不猜）", not calls, str(calls))
    finally:
        autodetect.detect = orig_detect

    test_dom_index()

    print(f"\n{'='*46}\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for n in FAIL:
            print("  ❌ " + n)
        return 1
    print("✅ 自动推断全部通过")
    return 0

if __name__ == "__main__":
    sys.exit(main())
