"""脱机端到端测试：不联网，用假 HTML 跑完整条链路。"""
import io
import json
import os
import shutil
import sys
import tempfile
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 假站点别把学到的规则写进用户真实缓存
os.environ.setdefault("NOVEL_DL_CACHE",
                      os.path.join(tempfile.gettempdir(), "novel-dl-tests",
                                   "learned.json"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from novel_dl import fetchers, log as nlog, parser, rules      # noqa: E402
from novel_dl.engine import Engine                              # noqa: E402
from novel_dl.progress import Progress                          # noqa: E402

nlog.setup()

BOOK_URL = "https://fake.example/book/1/"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  <- {detail}"))

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
CH_HTML = ('<html><head><title>第{n}章 测试章节{n}</title></head><body>'
           '<h1>第{n}章 测试章节{n}</h1>'
           '<div id="content">' + '这是第{n}章的正文内容。' * 12 + '</div>'
           '<a id="next_url" href="/book/1/{n}.html">下一章</a></body></html>')
BAD_CH_HTML = '<html><body><div id="content">短</div></body></html>'


class FakeFetcher(fetchers.BaseFetcher):
    name = "fake"
    MIN_DELAY = MAX_DELAY = 0
    fail_once = {"3"}
    toc_round = 0
    calls = []

    def fetch(self, url):
        type(self).calls.append(url)
        if url == BOOK_URL:
            type(self).toc_round += 1
            n = 6 if self.toc_round >= 2 else 5
            return ('<html><head><title>测试小说_在线阅读</title></head><body>' +
                    "".join(f'<a href="/book/1/{i}.html">第{i}章 测试章节{i}</a>'
                            for i in range(1, n + 1)) + '</body></html>')
        n = url.split("/")[-1].split(".")[0]
        self.polite_sleep()
        return BAD_CH_HTML if n in self.fail_once else CH_HTML.format(n=n)

fetchers.FETCHERS["fake"] = FakeFetcher


def _tmpdir(prefix):
    """测试用临时目录：放系统 TEMP，跑完即删。"""
    base = os.path.join(tempfile.gettempdir(), "novel-dl-tests")
    path = os.path.join(base, f"{prefix}{uuid.uuid4().hex[:8]}")
    os.makedirs(path, exist_ok=True)
    return path

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


def test_resume_and_increment():
    print("\n[1] 首轮下载 + 断点续传 + 增量更新")
    out = os.path.join(_tmpdir("out_"), "books")
    try:
        e = Engine(BOOK_URL, driver="fake", out_dir=out)
        txt1 = e.run()
        c1 = open(txt1, encoding="utf-8").read()
        check("首轮合并 4 章（第3章阵亡被跳过）", c1.count("=== 第") == 4,
              f"实际 {c1.count('=== 第')}")
        check("坏章节未混进 TXT", "=== 第3章" not in c1)

        e2 = Engine(BOOK_URL, driver="fake", out_dir=out)
        txt2 = e2.run()
        c2 = open(txt2, encoding="utf-8").read()
        check("续传+增量后合并 5 章", c2.count("=== 第") == 5,
              f"实际 {c2.count('=== 第')}")
        check("新增的第6章被增量下载", "=== 第6章" in c2)

        first_two = FakeFetcher.calls[:5]
        check("已完成章节不重复请求", "/book/1/1.html" not in FakeFetcher.calls[5:],
              str(first_two))

        p = Progress(out, BOOK_URL)
        check("账本记录 5 章完成", len(p.state["done"]) == 5, str(p.state["done"]))
        check("始终失败的章节仍留在失败名单里（供下次重试）",
              p.state["failed"] == [2], str(p.state["failed"]))
    finally:
        shutil.rmtree(out, ignore_errors=True)

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
RULE_HTML_TOC = ('<html><head><title>规则小说_全本</title></head><body>'
                 '<dl class="list">'
                 '<dd><a href="/read/1/1.html">第1章 开端</a></dd>'
                 '<dd><a href="/read/1/2.html">第2章 发展</a></dd>'
                 '</dl></body></html>')
RULE_HTML_CH = ('<html><body><h1 class="title">第1章 开端</h1>'
                '<div id="content"><p>' + '正文第一句。' * 20 + '</p>'
                '<p>广告：小主，这个章节后面还有哦！</p></div></body></html>')


def test_site_rules():
    print("\n[2] 站点规则：加载 / 匹配 / 解析 / 容错")
    tmp = _tmpdir("rules_")
    rule_file = os.path.join(tmp, "sites.json")
    good = {
        "host": "rule.example", "name": "规则站",
        "toc_item": "dl.list dd a",
        "ch_title": ".title",
        "ch_content": "#content",
        "filter_txt": "小主，这个章节后面还有哦！",
        "filter_tag": "p:last-child",
    }
    broken = {
        "host": "broken.example", "name": "坏规则站",
        "toc_item": "div[[[bad",
        "ch_title": "",
        "ch_content": "div[[[bad@js:r=r.replace(/x/g,'y')",
    }
    with open(rule_file, "w", encoding="utf-8") as f:
        json.dump([good, broken], f, ensure_ascii=False)

    old = os.environ.get("NOVEL_DL_RULES")
    os.environ["NOVEL_DL_RULES"] = rule_file
    rules.load_rules(force=True)
    try:
        loaded = rules.load_rules()
        check("规则文件被加载", len(loaded) >= 2, str(len(loaded)))
        check("按 host 命中规则",
              (rules.find_rule("https://rule.example/read/1/") or {}).get("name") == "规则站")

        p = parser.get_parser("https://rule.example/read/1/")
        check("get_parser 选中规则解析器", p.name.startswith("rule:"), p.name)
        title, toc = p.parse_toc(RULE_HTML_TOC, "https://rule.example/read/1/")
        check("规则目录解析出 2 章", len(toc) == 2, str(toc))
        ctitle, body = p.parse_chapter(RULE_HTML_CH)
        check("章节标题解析", ctitle == "第1章 开端", ctitle)
        check("广告/噪声被过滤", "小主" not in body and "广告" not in body, body[:60])
        check("正文长度合理", len(body) > 50, str(len(body)))

        pb = parser.get_parser("https://broken.example/book/1/")
        try:
            t2, toc2 = pb.parse_toc(RULE_HTML_TOC, "https://broken.example/book/1/")
            ok = len(toc2) == 2
        except Exception as ex:
            ok = False
            toc2 = repr(ex)
        check("坏选择器自动退回通用解析（不崩溃）", ok, str(toc2))

        js_sel = "div#content@js:r=r.replace(/x/g,'y')"
        check("@js 后缀被剥离", rules.plain_selector(js_sel) == "div#content",
              rules.plain_selector(js_sel))
        try:
            t3, b3 = pb.parse_chapter(RULE_HTML_CH)
            ok2 = len(b3) > 50
        except Exception as ex:
            ok2, b3 = False, repr(ex)
        check("含 @js 的正文规则不炸并能取到正文", ok2, str(b3)[:80])
    finally:
        if old is None:
            os.environ.pop("NOVEL_DL_RULES", None)
        else:
            os.environ["NOVEL_DL_RULES"] = old
        rules.load_rules(force=True)
        shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


def test_url_fixes():
    print("\n[3] 网址纠错")
    fixed = Engine._coerce_toc_url("https://x.example/book/6334/833155.html")
    check("章节页 → 目录页", fixed == "https://x.example/book/6334/", fixed)
    same = Engine._coerce_toc_url("https://x.example/book/6334/")
    check("目录页保持不变", same == "https://x.example/book/6334/", same)
    weird = Engine._coerce_toc_url("https://x.example/read-1.html")
    check("非数字书 ID 形态不误伤", weird == "https://x.example/read-1.html", weird)

    rule = {"url_fix": [[r"^https://m\.ex\.com/book/(\d+).*$", r"https://www.ex.com/book/\1/"]]}
    out = rules.apply_url_fixes("https://m.ex.com/book/88/9.html", rule)
    check("规则 url_fix 生效", out == "https://www.ex.com/book/88/", out)

# ---------------------------------------------------------------------------


def test_chapter_boilerplate():
    """章节页的工具栏、站点域名、推荐位、重复标题都不能混进正文。"""
    print("\n[4] 正文清洗：广告与页面杂项要拦掉")
    from novel_dl.parser import GenericParser

    body_text = "".join(f"她推开窗，风灌进来，吹动手边的稿纸{i}。\n" for i in range(8))
    html = f"""<html><head><title>第9章 替罪羊_西瓜书屋</title></head><body>
    <div class="C0NTENT">
      <div class="toolbar"><a>首页</a><a>书架</a><a>阅读记录</a><a>搜索小说</a>
        <a>封面</a><a>繁体</a><a>〔加入书签〕</a></div>
      <div id="C0NTENT" class="RBGsectionThree-content">
        <p>第9章 替罪羊(1/2)</p>
        <p>本站新（短）域名：xiguashuwu.com</p>
        <p>第9章 替罪羊</p>
        {''.join(f'<p>{line}</p>' for line in body_text.splitlines())}
      </div>
      <div class="recommend"><h3>热门小说推荐</h3>
        <a>老师别哭请摆好</a><a>长日光阴</a><a>清和</a>
        <h3>最近更新小说</h3><a>小满（年上）</a><a>天妒（修真1v1 H）</a>
      </div>
    </div></body></html>"""

    title, body = GenericParser().parse_chapter(html)
    check("标题去掉了页码标记", title == "第9章 替罪羊", title)
    for word in ("封面", "繁体", "加入书签", "阅读记录", "搜索小说",
                 "域名", "热门小说推荐", "最近更新小说", "老师别哭", "长日光阴"):
        check(f"正文里没有「{word}」", word not in body, body[:120])
    check("正文保留了真正的段落", "她推开窗" in body, body[:120])
    check("重复的 (1/2) 标题行被删掉", "(1/2)" not in body, body[:120])


Q_BASE = "https://qs.example/index.php"
Q_INDEX = f"{Q_BASE}?ca=Chapter.Index&bid=88&cid=1"
Q_READ = f"{Q_BASE}?ca=Chapter.Read&bid=88&cid=2"


class QuerySiteFetcher(fetchers.BaseFetcher):
    """网址里没有 .html，全靠查询串；正文页上只有「目录」链接能回去。"""

    name = "fake-query"
    MIN_DELAY = MAX_DELAY = 0
    calls = []

    def fetch(self, url):
        type(self).calls.append(url)
        if "Chapter.Index" in url:
            items = "".join(
                f'<a href="{Q_BASE}?ca=Chapter.Read&bid=88&cid={i}">{t}</a>'
                for i, t in enumerate(
                    ["版权信息", "献词", "引子", "1 飓风恩护者", "2 荣誉已死"], 1))
            return (f'<html><head><title>《曙光志》</title></head><body>'
                    f'<div class="cat">{items}</div></body></html>')
        cid = url.rsplit("cid=", 1)[-1]
        return ('<html><head><title>《曙光志》</title></head><body>'
                f'<a href="{Q_INDEX}">目录</a>'
                f'<div id="content">{"这是本节的内容。" * 20}</div></body></html>')

fetchers.FETCHERS["fake-query"] = QuerySiteFetcher


def test_query_url_sites():
    """没有目录地址的站：给阅读页地址也要能下。"""
    print("\n[5] 查询串网址：给阅读页地址也能找到目录")
    html = ('<html><head><title>《曙光志（全三卷）》</title></head><body>'
            '<a href="/index.php?ca=Chapter.Read&bid=88&cid=1">开始阅读</a>'
            '<div class="catalog">'
            + "".join(f'<a href="/index.php?ca=Chapter.Read&bid=88&cid={i}">{t}</a>'
                      for i, t in enumerate(
                          ["版权信息", "献词", "引子", "1 飓风恩护者"], 1))
            + '</div></body></html>')
    from novel_dl.parser import GenericParser
    _title, toc = GenericParser().parse_toc(html, Q_INDEX)
    check("查询串网址也能认出章节", len(toc) == 4, str(len(toc)))
    check("章节标题取链接文字", toc[0][0] == "版权信息", str(toc[0]))
    check("顺序就是页面顺序", [t for t, _ in toc][1:3] == ["献词", "引子"],
          str([t for t, _ in toc]))
    check("地址保留查询串", "cid=4" in toc[3][1], toc[3][1])

    out = os.path.join(_tmpdir("query"), "books")
    try:
        txt = Engine(Q_INDEX, driver="fake-query", out_dir=out).run()
        text = open(txt, encoding="utf-8").read()
        check("直接给目录地址能下完", text.count("=== ") == 5, str(text.count("=== ")))
    finally:
        shutil.rmtree(os.path.dirname(out), ignore_errors=True)

    out = os.path.join(_tmpdir("query2"), "books")
    try:
        QuerySiteFetcher.calls = []
        txt = Engine(Q_READ, driver="fake-query", out_dir=out).run()
        text = open(txt, encoding="utf-8").read()
        check("给正文页地址也能下完", text.count("=== ") == 5, str(text.count("=== ")))
        check("自己顺着「目录」链接找了回来",
              any("Chapter.Index" in u for u in QuerySiteFetcher.calls),
              str(QuerySiteFetcher.calls[:3]))
    finally:
        shutil.rmtree(os.path.dirname(out), ignore_errors=True)


def test_wide_forms():
    """站点把页码、导航写成全角，判定要认得，输出要原样。"""
    print("\n[6] 全角写法：认得出，但不改原文")
    from novel_dl.textutil import body_from, is_noise, strip_page_mark

    check("全角页码被当成页码标记",
          strip_page_mark("第3章 夜色（１／３）") == "第3章 夜色",
          strip_page_mark("第3章 夜色（１／３）"))
    check("半角页码照旧",
          strip_page_mark("第3章 夜色(1/3)") == "第3章 夜色",
          strip_page_mark("第3章 夜色(1/3)"))
    weird = "第1章 ㈱与㌔"
    check("回复字符原样保留（不写进 TXT 的改动一律不做）",
          strip_page_mark(weird) == weird, strip_page_mark(weird))

    check("全角括号里的导航算噪声", is_noise("（本章完）"))
    check("全角页码行算噪声", is_noise("（２／７）"))
    check("正常句子不算噪声", not is_noise("她推开窗，风灌进来。"))

    body = body_from(["第9章 甲（１／２）", "（本章完）", "风灌进来，吹动稿纸。",
                      "（１／３）"], drop_title="第9章 甲")
    check("全角重复标题被删", "甲" not in body, body)
    check("全角噪声行被删", "本章完" not in body and "１／３" not in body, body)
    check("正文段落留着", "风灌进来" in body, body)


PAGE_ONE = "".join(
    f"<p>第一页第{i}句，风从窗缝里钻进来，吹得稿纸响了一下。</p>" for i in range(1, 13))
PAGE_TWO = "".join(
    f"<p>第二页第{i}句，她把灯往前挪了挪，影子跟着动了一下。</p>" for i in range(1, 13))
PAGE_OTHER = "".join(
    f"<p>另一章第{i}句，屋檐滴水，滴在青石板上。</p>" for i in range(1, 13))


class PagingFetcher(fetchers.BaseFetcher):
    """长章拆成两页；第二章的第二页只有一句「未完」，不该被接上去。"""

    name = "fake-paging"
    MIN_DELAY = MAX_DELAY = 0

    def fetch(self, url):
        if url.endswith("toc.html"):
            return ('<html><head><title>分页测试</title></head><body><dl>'
                    '<dd><a href="/b/p1.html">第1章 甲</a></dd>'
                    '<dd><a href="/b/p2.html">第2章 乙</a></dd>'
                    '</dl></body></html>')
        if url.endswith("p1.html"):
            return ('<html><head><title>第1章 甲</title></head><body>'
                    '<h1>第1章 甲</h1><div id="content">' + PAGE_ONE +
                    '</div><a href="/b/p1_2.html">下一页</a></body></html>')
        if url.endswith("p1_2.html"):
            return ('<html><head><title>第1章 甲(2/2)</title></head><body>'
                    '<h1>第1章 甲(2/2)</h1><div id="content">' + PAGE_TWO +
                    '</div></body></html>')
        if url.endswith("p2.html"):
            return ('<html><head><title>第2章 乙</title></head><body>'
                    '<h1>第2章 乙</h1><div id="content">' + PAGE_OTHER +
                    '</div><a href="/b/p2_2.html">下一页</a></body></html>')
        if url.endswith("p2_2.html"):
            return ('<html><head><title>第2章 乙(2/2)</title></head><body>'
                    '<h1>第2章 乙(2/2)</h1><div id="content">未完</div></body></html>')
        return '<html><body>没有这一页</body></html>'


fetchers.FETCHERS["fake-paging"] = PagingFetcher
PAGING_BOOK = "http://paging.example/b/toc.html"


def test_image_chars_and_paging():
    """两件事：站点把字换成图片、把一个长章拆成几页。"""
    print("\n[7] 图片字与分页：缺字的正文不要存，空的页不要接")
    from novel_dl.guard import image_text_reason
    from bs4 import BeautifulSoup

    para = ('涎<img class="hz" src="/wzbodyimg/a.png"/>接'
            '<img class="hz" src="/wzbodyimg/b.png"/>到'
            '<img class="hz" src="/wzbodyimg/c.png"/>面'
            '<img class="hz" src="/wzbodyimg/d.png"/>的'
            '<img class="hz" src="/wzbodyimg/e.png"/>瞬'
            '<img class="hz" src="/wzbodyimg/f.png"/>间，'
            '她本能地后仰却退无可退，只能看着他靠近，'
            '喉间发紧，连呼吸都放轻了，'
            '像怕惊动什么似的。')
    why = image_text_reason(BeautifulSoup(f'<div id="c"><p>{para * 3}</p></div>', "lxml").div)
    check("成片的字图会被认出来", why and "图片" in why, why)

    icons = ('<div id="c"><p>' + "正文内容。" * 100 + '</p>'
             '<img src="/static/sep.png"/><img src="/static/sep.png"/></div>')
    check("普通装饰图不误判",
          image_text_reason(BeautifulSoup(icons, "lxml").div) is None)

    short = '<div><p>短<img class="hz" src="/wzbodyimg/x.png"/>句</p></div>'
    check("太短的块不判（可能是别的组件）",
          image_text_reason(BeautifulSoup(short, "lxml").div) is None)

    out = os.path.join(tempfile.gettempdir(), "novel-dl-tests",
                       "paging" + uuid.uuid4().hex[:6])
    os.makedirs(out, exist_ok=True)
    Engine(PAGING_BOOK, driver="fake-paging", out_dir=out, workers=1,
           min_delay=0).run()
    p = Progress(out, PAGING_BOOK)
    first = p.read_chapter(0) or ""
    second = p.read_chapter(1) or ""
    check("第一页在", "第一页第1句" in first, first[:40])
    check("第二页也接上了", "第二页第1句" in first, first[-40:])
    check("空的分页页不接（不留个「未完」尾巴）", "未完" not in second, second[-40:])
    check("两章都算完成", len(p.state["done"]) == 2, p.state["done"])
    shutil.rmtree(out, ignore_errors=True)


def test_user_agent():
    """验证通行证和 UA 绑在一起，所以 UA 必须能换。"""
    print("\n[9] User-Agent：验证过的 Cookie 认它")
    from novel_dl import fetchers

    fetchers.set_ua(None)
    old = os.environ.pop("NOVEL_DL_UA", None)
    try:
        check("没指定时用默认 Chrome",
              "Chrome/" in fetchers.get_ua(), fetchers.get_ua()[:40])
        fetchers.set_ua("Mozilla/5.0 (Macintosh) TestUA/1.0")
        check("命令行指定的优先", fetchers.get_ua().endswith("TestUA/1.0"),
              fetchers.get_ua())
        fetchers.set_ua(None)
        os.environ["NOVEL_DL_UA"] = "EnvUA/2.0"
        check("环境变量次之", fetchers.get_ua() == "EnvUA/2.0", fetchers.get_ua())
    finally:
        fetchers.set_ua(None)
        os.environ.pop("NOVEL_DL_UA", None)
        if old:
            os.environ["NOVEL_DL_UA"] = old

    fetchers.set_ua("SessionUA/3.0")
    try:
        f = fetchers.get_fetcher("requests")
        check("requests 会话真的用它",
              f.session.headers.get("User-Agent") == "SessionUA/3.0",
              f.session.headers.get("User-Agent"))
    finally:
        fetchers.set_ua(None)


def test_challenge_detect():
    """Cloudflare 那种「请稍候…」页面要认得出来，别当成正文去解析。"""
    print("\n[8] 验证页：认得出来，才谈得上等人去点")
    from novel_dl.fetchers import _looks_challenged, get_fetcher

    check("Cloudflare 挑战页认得",
          _looks_challenged('<html><title>Just a moment...</title>'
                            '<script>window._cf_chl_opt={}</script></html>'))
    check("腾讯验证页认得",
          _looks_challenged('<iframe src="https://captcha.qq.com/TCaptcha.js"></iframe>'))
    check("正常正文页不误判",
          not _looks_challenged('<html><body><div id="content">'
                                + "正文内容。" * 200 + '</div></body></html>'))
    check("空页面不判", not _looks_challenged(""))
    check("超大页面不判（不可能是验证页）",
          not _looks_challenged("<html>" + "正" * 250_000 + "</html>"))

    try:
        from novel_dl.fetchers import SeleniumFetcher
        f = SeleniumFetcher(show=True)
        check("selenium 能记住「要看得见」", f.show is True, str(f.show))
        f2 = SeleniumFetcher()
        check("默认还是无头", f2.show is False, str(f2.show))
    except (ImportError, SystemExit):
        print("  （没装 selenium，跳过可见浏览器的两项）")


def main():
    test_resume_and_increment()
    test_site_rules()
    test_url_fixes()
    test_chapter_boilerplate()
    test_query_url_sites()
    test_wide_forms()
    test_image_chars_and_paging()
    test_challenge_detect()
    test_user_agent()
    print(f"\n{'='*46}\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for name in FAIL:
            print("  ❌ " + name)
        return 1
    print("✅ 全部通过")
    return 0

if __name__ == "__main__":
    sys.exit(main())
