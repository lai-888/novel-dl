"""脱机端到端测试：不联网，用假 HTML 跑完整条链路。

覆盖：
  1. 首轮下载（含一个必失败章节）→ 跳过失败章 + 合并 TXT
  2. 重跑同命令 → 断点续传（不重复请求）+ 重试失败章 + 增量下载新章
  3. 账本 state.json 的完整性
  4. 站点规则：Web 规则解析、@js 后缀退化、坏选择器不炸
  5. URL 纠错：章节页 → 目录页

运行：python tests/test_all.py        （不依赖 pytest）
"""
import io
import json
import os
import shutil
import sys
import tempfile
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
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
# 假站点
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
    """测试用临时目录：放系统 TEMP，跑完即删。

    不放进仓库目录：受限环境（沙箱 / 只读工作区）里，衍生进程往工作区
    建目录会被拒绝（WinError 5），而 %TEMP% 始终可写。
    """
    base = os.path.join(tempfile.gettempdir(), "novel-dl-tests")
    path = os.path.join(base, f"{prefix}{uuid.uuid4().hex[:8]}")
    os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# 1 + 2：续传 / 增量 / 跳过 / 合并
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
# 3：站点规则
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

        # 规则解析器
        p = parser.get_parser("https://rule.example/read/1/")
        check("get_parser 选中规则解析器", p.name.startswith("rule:"), p.name)
        title, toc = p.parse_toc(RULE_HTML_TOC, "https://rule.example/read/1/")
        check("规则目录解析出 2 章", len(toc) == 2, str(toc))
        ctitle, body = p.parse_chapter(RULE_HTML_CH)
        check("章节标题解析", ctitle == "第1章 开端", ctitle)
        check("广告/噪声被过滤", "小主" not in body and "广告" not in body, body[:60])
        check("正文长度合理", len(body) > 50, str(len(body)))

        # 坏规则：非法 CSS + @js 后缀，不得抛异常
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
# 4：URL 纠错
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
def main():
    test_resume_and_increment()
    test_site_rules()
    test_url_fixes()
    print(f"\n{'='*46}\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for name in FAIL:
            print("  ❌ " + name)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
