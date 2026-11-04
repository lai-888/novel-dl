"""通用算法的边界测试：不联网、不碰具体站点。

这些用例都是「算法本身该成立的性质」，换任何一个站都适用：
章号解析、目录排序、分页与下一章的区分、正文容器、清洗、书名、编码、
熔断窗口、目录名、乱码判定。
"""
import io
import os
import shutil
import sys
import tempfile
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from bs4 import BeautifulSoup                      # noqa: E402

from novel_dl.encoding import decode_body, looks_garbled          # noqa: E402
from novel_dl.engine import CHAPTER_HEADING, Engine, _same_chapter  # noqa: E402
from novel_dl.fetchers import Breaker                             # noqa: E402
from novel_dl.guard import looks_truncated, preview_reason        # noqa: E402
from novel_dl.parser import (GenericParser, _doc_title,           # noqa: E402
                             chapter_key_of, chapter_title_from,
                             find_full_catalog_url, find_toc_url)
from novel_dl import fetchers, library                             # noqa: E402
from novel_dl.progress import Progress, _slug                     # noqa: E402
from novel_dl.textutil import body_from, safe_filename            # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"   {detail}"))


def test_chapter_numbers():
    print("\n[1] 章号解析")
    check("第十章", chapter_key_of("第十章 起点") == (None, 10), chapter_key_of("第十章 起点"))
    check("中文章号", chapter_key_of("第一百二十三章 风起") == (None, 123),
          chapter_key_of("第一百二十三章 风起"))
    check("两千零一章", chapter_key_of("第两千零一章 重逢") == (None, 2001),
          chapter_key_of("第两千零一章 重逢"))
    check("第3节", chapter_key_of("第3节 碎语") == (None, 3), chapter_key_of("第3节 碎语"))
    check("第十二话", chapter_key_of("第十二话 余音") == (None, 12),
          chapter_key_of("第十二话 余音"))
    check("英文 Chapter 12", chapter_key_of("Chapter 12 The End") == (None, 12),
          chapter_key_of("Chapter 12 The End"))
    check("区间不当章号", chapter_key_of("第1-2章 合集") is None, chapter_key_of("第1-2章 合集"))
    check("序章没号", chapter_key_of("序章") is None, chapter_key_of("序章"))


def test_reorder():
    print("\n[2] 目录重排")
    toc = [("第3章 三", "u3"), ("第1章 一", "u1"), ("第2章 二", "u2")]
    check("乱序排好", [t for t, _ in GenericParser._reorder(toc)],
          ["第1章 一", "第2章 二", "第3章 三"])
    mixed = [("第2章 二", "u2"), ("第1章 一", "u1"), ("番外 后记", "u9")]
    check("无号条目放末尾", [t for t, _ in GenericParser._reorder(mixed)],
          ["第1章 一", "第2章 二", "番外 后记"])
    same = [("第一章 上", "a"), ("第一章 下", "b")]
    check("同号不动", [t for t, _ in GenericParser._reorder(same)], ["第一章 上", "第一章 下"])
    ordered = [("第1章 一", "u1"), ("第2章 二", "u2")]
    check("已有序不动", [t for t, _ in GenericParser._reorder(ordered)], ["第1章 一", "第2章 二"])


def test_pager():
    print("\n[3] 分页 与 下一章")
    cur = "http://x.example/ch/9.html"
    check("_2 是分页", Engine._is_next_page(cur, "http://x.example/ch/9_2.html"))
    check("-2 是分页", Engine._is_next_page("http://x.example/ch/9-2.html",
                                            "http://x.example/ch/9-3.html"))
    check("跨章不算", not Engine._is_next_page(cur, "http://x.example/ch/10.html"))
    check("查询串分页", Engine._is_next_page("http://x.example/ch/9.html?page=1",
                                             "http://x.example/ch/9.html?page=2"))
    page = ('<body><div id="content">正文。</div>'
            '<a href="/book_read_77_1003.html">下一章</a></body>')
    check("「下一章」不当分页",
          Engine._next_page_link(page, "http://x.example/book_read_77_1002.html") is None)
    check("「下页」当成目录下一页",
          Engine._next_toc_page('<a href="/l2.html">下页</a>', "http://x.example/l1.html")
          == "http://x.example/l2.html")
    check("带符号的下一页",
          Engine._next_toc_page('<a href="/l2.html">下一页 &gt;</a>', "http://x.example/l1.html")
          == "http://x.example/l2.html")
    opts = Engine._page_options('<select><option value="1">1</option>'
                                '<option value="2">2</option></select>')
    check("纯数字页码", opts == ["1", "2"], opts)
    opts2 = Engine._page_options('<select><option value="index_2.html">2</option>'
                                 '<option value="index_3.html">3</option></select>')
    check("index_2.html 当页码", len(opts2) >= 2, opts2)
    check("排序选项不算页码",
          Engine._page_options('<select><option value="articlename">排序</option></select>') == [])


def test_container():
    print("\n[4] 正文容器")
    inner = "他推开门，风灌进来。" * 40
    page = (f'<html><body><div class="content"><div class="content"><p>{inner}</p></div>'
            f'<div class="content">推荐：某书 某书 某书</div></div></body></html>')
    _t, body = GenericParser().parse_chapter(page)
    check("嵌套命中取最内层", "推荐" not in body, body[-30:])
    check("正文还在", "他推开门" in body, body[:20])

    page2 = ('<html><body><table><tr><td id="content">'
             + "夜色沉沉，他提灯走过长街。" * 20 +
             '</td></tr></table><div class="ad">广告位出租</div></body></html>')
    _t2, body2 = GenericParser().parse_chapter(page2)
    check("table 排版能取", "夜色沉沉" in body2 and "广告位" not in body2, body2[:30])

    short = "他推开门，风灌进来。" * 12
    page3 = (f'<html><body><div id="content">{short}</div>'
             f'<div class="sidebar">推荐阅读：' + "某书名" * 30 + '</div></body></html>')
    _t3, body3 = GenericParser().parse_chapter(page3)
    check("短章节不被侧栏挤掉", "他推开门" in body3, body3[:30])

    links = ('<html><head><title>某书_第1章 开端</title></head><body><div id="content">'
             '<a href="/b/1">公主大人等等我</a><a href="/b/2">伊塔之柱</a>'
             '他推开门，风灌进来，吹得桌上的稿纸翻了一页。'
             '他站着没动，听着走廊尽头的脚步声慢慢远去，直到彻底听不见。'
             '窗外的雨越下越大，玻璃上很快蒙了一层水汽，看不清对面的楼。'
             '</div></body></html>')
    _t4, body4 = GenericParser().parse_chapter(links)
    check("推荐短链接不进正文", "公主大人" not in body4 and "伊塔之柱" not in body4, body4[:40])


def test_cleaning():
    print("\n[5] 正文清洗")
    check("水印贴句内", body_from(["一秒记住【站】x.com，更新快！正文开始。"]) == "正文开始。")
    check("导航整行删", body_from(["上一章", "下一章", "正文。"]) == "正文。")
    check("重复标题+页码删", body_from(["第3章 x(1/2)", "正文。"]) == "正文。")
    check("空行不留", body_from(["", "  ", "正文。"]) == "正文。")
    block = body_from(["猜你喜欢：", "书名甲", "书名乙", "正文第一句。"])
    check("带冒号的推荐块删", "书名甲" not in block and "正文第一句" in block, block)
    body = body_from(["第7章 雨夜(第1/2页)", "雨点砸在铁皮雨棚上。"], drop_title="第7章 雨夜")
    check("标题页码行抠掉", "第1/2页" not in body and "雨点砸" in body, body)


def test_titles():
    print("\n[6] 书名与章节名")

    def doc(raw):
        return _doc_title(BeautifulSoup(f"<html><head><title>{raw}</title></head></html>", "lxml"))

    check("书名含逗号", doc("公路求生，但我是无流大佬小说_作者_新笔趣阁_最新章节")
          == "公路求生，但我是无流大佬", doc("公路求生，但我是无流大佬小说_作者_新笔趣阁_最新章节"))
    check("书名含书名号", doc("《三体》全文阅读_刘慈欣_笔趣阁") == "《三体》",
          doc("《三体》全文阅读_刘慈欣_笔趣阁"))
    check("纯书名不动", doc("调试测试小说") == "调试测试小说", doc("调试测试小说"))
    check("SEO 尾巴削掉", doc("残玺完整版在线免费阅读-番茄小说") == "残玺",
          doc("残玺完整版在线免费阅读-番茄小说"))

    def chap(raw, h1):
        return chapter_title_from(BeautifulSoup(
            f"<html><head><title>{raw}</title></head><body>{h1}</body></html>", "lxml"))

    check("h1 是章名就用 h1", chap("某书_第3章 雨夜-某站", "<h1>第3章 雨夜</h1>") == "第3章 雨夜",
          chap("某书_第3章 雨夜-某站", "<h1>第3章 雨夜</h1>"))
    check("h1 是通知时用 title", chap("某书_第7章 长夜-某站", "<h1>书籍下架通知</h1>") == "第7章 长夜",
          chap("某书_第7章 长夜-某站", "<h1>书籍下架通知</h1>"))


def test_catalog_links():
    print("\n[7] 目录入口")
    page = ('<body><a href="/cat/1">分类目录</a><a href="/top">排行榜目录</a>'
            '<a href="/all/9/">全部章节</a></body>')
    check("不跳分类页", find_full_catalog_url(page, "http://x.example/book/9/")
          == "http://x.example/all/9/", find_full_catalog_url(page, "http://x.example/book/9/"))
    check("只有分类目录就不跳",
          find_full_catalog_url('<body><a href="/cat/1">分类目录</a></body>',
                                "http://x.example/book/9/") is None)
    check("目录入口不跳分类",
          find_toc_url('<body><a href="/cat/1">分类目录</a><a href="/list">章节目录</a></body>',
                       "http://x.example/read/1") == "http://x.example/list")
    check("跳过 javascript",
          find_toc_url('<body><a href="javascript:x">目录</a>'
                       '<a href="/l">章节目录</a></body>', "http://x.example/r/1")
          == "http://x.example/l")


def test_encoding_and_guard():
    print("\n[8] 编码与判定")
    check("UTF-16LE+BOM",
          decode_body("\ufeff第一章 风起".encode("utf-16-le"), "")[0].strip("\ufeff") == "第一章 风起")
    check("GB18030", decode_body("第一章 风起".encode("gb18030"), "gbk")[0] == "第一章 风起")
    check("坏字节不崩", isinstance(decode_body(b"\xff\xfe\x00\x41", "")[0], str))
    check("正常中文不算乱码", not looks_garbled("他推开门，风灌进来，夜色很深。" * 20))
    check("私有区字符算乱码",
          looks_garbled("".join(chr(0xE000 + i % 50) for i in range(300))))
    check("少量符号不算乱码",
          not looks_garbled("他推开门" + chr(0xE001) * 3 + "风灌进来。" * 50))
    check("句中截断要认", looks_truncated("他推开门，风灌进来，然后他"))
    check("句号收尾不认", not looks_truncated("他推开门，风灌进来。" * 30))
    check("正常短章不算预览", preview_reason("", "他推开门，风灌进来。" * 30) is None)


def test_breaker_and_naming():
    print("\n[9] 熔断窗口与目录名")
    b = Breaker(gate=lambda *a, **k: None)
    check("初始窗口 1..8", 1 <= b.window(8) <= 8, b.window(8))
    for _ in range(3):
        b.failed("429 Too Many Requests")
    check("被挡后收敛到 1", b.window(8) == 1, b.window(8))
    for _ in range(9):
        b.ok()
    check("缓过来会放开", b.window(8) >= 2, b.window(8))

    check("同址同目录名", _slug("http://x.example/book/1") == _slug("http://x.example/book/1"))
    check("不同书不同目录名", _slug("http://x.example/book/1") != _slug("http://x.example/book/2"))
    check("超长 URL 不撞名", _slug("http://x.example/" + "a" * 200)
          != _slug("http://x.example/" + "b" * 200))
    check("路径符号被替换", safe_filename('a/b:c*d?e"f<g>h|i') == "a_b_c_d_e_f_g_h_i",
          safe_filename('a/b:c*d?e"f<g>h|i'))
    check("Windows 保留名", safe_filename("CON") == "_CON", safe_filename("CON"))
    check("结尾的点去掉", safe_filename("书名...") == "书名", safe_filename("书名..."))


def test_same_chapter_edges():
    print("\n[10] 同一章的判定细节")
    check("副标题差异算同章", _same_chapter("第3章", "第3章 雨夜") is True,
          _same_chapter("第3章", "第3章 雨夜"))
    check("页码差异算同章", _same_chapter("第3章 雨夜(1/2)", "第3章 雨夜(2/2)") is True)
    check("上下篇不算同章", _same_chapter("第3章 上", "第3章 下") is False,
          _same_chapter("第3章 上", "第3章 下"))
    check("不同章不算", _same_chapter("第3章 雨夜", "第4章 清晨") is False)
    check("空标题判不了", _same_chapter("", "第4章") is None)


def test_toc_clustering():
    print("\n[11] 目录聚类：别家书的章节不能混进来")
    html = ('<body><div class="list">'
            + "".join(f'<a href="/book/11/{i}.html">第{i}章 本书</a>' for i in range(1, 9))
            + "".join(f'<a href="/book/22/{i}.html">第{i}章 别家</a>' for i in range(1, 5))
            + '</div></body>')
    _t, toc = GenericParser().parse_toc(html, "http://x.example/book/11/")
    check("只收同一本书", all("/book/11/" in u for _t, u in toc), [u for _t, u in toc][:3])
    check("别家章节被排除", len(toc) == 8, len(toc))


def test_export_heading():
    print("\n[12] 导出时的章标题判定")
    check("第3话 认", bool(CHAPTER_HEADING.match("第3话 残响")))
    check("Chapter 3 认", bool(CHAPTER_HEADING.match("Chapter 3 The End")))
    check("第3节 认", bool(CHAPTER_HEADING.match("第3节 碎语")))
    check("第12章 认", bool(CHAPTER_HEADING.match("第12章 风起")))
    check("序章不算带号", not CHAPTER_HEADING.match("序章"))


def test_toc_shift():
    print("\n[12b] 目录插入新章后，完成记录要跟着移位")
    root = os.path.join(tempfile.gettempdir(), "novel-dl-tests",
                        "shift" + uuid.uuid4().hex[:6])
    url = "http://shift.example/book/1/"
    try:
        p = Progress(root, url)
        toc_a = [("第1章 一", "http://shift.example/book/1/1.html"),
                 ("第2章 二", "http://shift.example/book/1/2.html"),
                 ("第3章 三", "http://shift.example/book/1/3.html")]
        p.set_book(url, "移位测试", toc_a)
        for i in range(3):
            p.write_chapter(i, f"第{i + 1}章 正文")
            p.mark_done(i, flush=True)
        check("先记 3 章完成", sorted(p.state["done"]) == [0, 1, 2], p.state["done"])

        toc_b = [("番外 楔子", "http://shift.example/book/1/0.html")] + toc_a
        p.set_book(url, "移位测试", toc_b)
        check("完成记录跟着后移", sorted(p.state["done"]) == [1, 2, 3], p.state["done"])
        check("缓存仍对新下标", "第1章 正文" in (p.read_chapter(1) or ""),
              (p.read_chapter(1) or "")[:20])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_multi_volume():
    print("\n[13] 多卷目录：章号重复时不能按号硬排")
    vol = [("第一卷 第一章 上", "u1"), ("第一卷 第二章 中", "u2"),
           ("第二卷 第一章 下", "u3"), ("第二卷 第二章 终", "u4")]
    check("多卷重号保持原序",
          [t for t, _ in GenericParser._reorder(vol)] == [t for t, _ in vol])
    dup = [("第一章 一", "a"), ("第二章 二", "b"), ("第一章 又一", "c")]
    check("跨卷重号不打乱",
          [t for t, _ in GenericParser._reorder(dup)] == ["第一章 一", "第二章 二", "第一章 又一"])
    uniq = [("第3章 三", "u3"), ("第1章 一", "u1"), ("第2章 二", "u2")]
    check("无重复时照常重排",
          [t for t, _ in GenericParser._reorder(uniq)] == ["第1章 一", "第2章 二", "第3章 三"])


def test_deep_catalog_pagination():
    print("\n[14] 详情页 → 完整目录 → 目录自己的第二页")
    book = "http://deep.example/book/5/"

    def catalog(page, a, b, nxt=None):
        items = "".join(f'<dd><a href="list_{page}_{i}.html">第{i}章 标题{i}</a></dd>'
                        for i in range(a, b + 1))
        nav = f'<a href="list_{page}_{nxt}.html">下一页</a>' if nxt else ""
        return f'<html><head><title>深目录</title></head><body><dl>{items}</dl>{nav}</body></html>'

    class Deep(fetchers.BaseFetcher):
        name = "fake-deep"
        MIN_DELAY = MAX_DELAY = 0

        def fetch(self, url):
            if url.endswith("/book/5/"):
                return ('<html><head><title>深目录</title></head><body><dl>'
                        + "".join(f'<dd><a href="ch_{i}.html">第{i}章 最新更新</a></dd>'
                                  for i in range(20, 25))
                        + '</dl><a href="/book/5/all/">全部章节</a></body></html>')
            if url.endswith("/all/"):
                return catalog(1, 1, 5, 2)
            if url.endswith("/all/list_1_2.html"):
                return catalog(2, 6, 10)
            return ('<html><head><title>第1章 标题1</title></head><body>'
                    '<div id="content">' + "正文内容在这里，他推门进来。" * 12 + '</div></body></html>')

    fetchers.FETCHERS["fake-deep"] = Deep
    out = os.path.join(tempfile.gettempdir(), "novel-dl-tests", "deep" + uuid.uuid4().hex[:6])
    try:
        eng = Engine(book, driver="fake-deep", out_dir=out, workers=1, min_delay=0)
        _t, toc = eng.refresh_toc()
        check("完整目录的第二页也翻了", len(toc) == 10, len(toc))
        check("相对链接按完整目录页解析",
              all("/book/5/all/" in u for _t, u in toc), [u for _t, u in toc][:2])
    finally:
        shutil.rmtree(out, ignore_errors=True)
        fetchers.FETCHERS.pop("fake-deep", None)


def test_dedupe_and_damage():
    print("\n[15] 目录去重与账本损坏")
    root = os.path.join(tempfile.gettempdir(), "novel-dl-tests", "rob" + uuid.uuid4().hex[:6])
    url = "http://r.example/book/1/"
    try:
        p = Progress(root, url)
        p.set_book(url, "健壮性", [("第1章 一", "http://r.example/1.html"),
                                   ("第1章 一", "http://r.example/1.html"),
                                   ("第2章 二", "http://r.example/2.html")])
        check("重复地址只留一条", len(p.state["toc"]) == 2, len(p.state["toc"]))

        p.save(force=True)
        import json as _json
        io.open(os.path.join(p.root, "state.json"), "w", encoding="utf-8").write("{ 坏 JSON")
        p2 = Progress(root, url)
        check("坏账本读成空账本", isinstance(p2.state, dict) and p2.state.get("toc") == [],
              p2.state.get("toc"))

        shutil.rmtree(p2.cache_dir, ignore_errors=True)
        p3 = Progress(root, url)
        check("缓存目录自动重建", os.path.isdir(p3.cache_dir), p3.cache_dir)
        check("越界读返回空", p3.read_chapter(999) is None)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    test_chapter_numbers()
    test_reorder()
    test_pager()
    test_container()
    test_cleaning()
    test_titles()
    test_catalog_links()
    test_encoding_and_guard()
    test_breaker_and_naming()
    test_same_chapter_edges()
    test_toc_clustering()
    test_export_heading()
    test_toc_shift()
    test_multi_volume()
    test_deep_catalog_pagination()
    test_dedupe_and_damage()
    print(f"\n{'='*46}\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for name in FAIL:
            print("  ❌ " + name)
        return 1
    print("✅ 通用算法全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
