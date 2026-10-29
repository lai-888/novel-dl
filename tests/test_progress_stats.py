"""进度统计专项测试。

起因：用户报告「显示共 0 章，但实际下了很多章节」。
根因：total 只在 mark_done 里更新。若目录解析后没有任何新章节需要下载
（全部已下过 / 网络慢时先跳过了统计），total 就永远停在 0。

这类 bug 不会让下载失败，只是界面数字错 —— 所以必须专门断言，
不能靠功能测试顺带覆盖。

跑：python tests/test_progress_stats.py
"""
import io
import os
import shutil
import sys
import uuid

sys.path.insert(0, r"C:\Users\31383\Desktop\novel-dl")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from novel_dl.engine import Engine  # noqa: E402
from novel_dl.fetchers import FETCHERS, BaseFetcher  # noqa: E402
from novel_dl.progress import Progress  # noqa: E402

PASS, FAIL = [], []
BODY = "夜色沉沉，他提灯走过长街，远处传来打更的声音。" * 10
BOOK = "http://stat.example/book/1/"
N = 24


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  <- {detail}"))


def toc_html(n=N):
    return ('<html><head><title>统计测试</title></head><body><dl>' +
            "".join(f'<dd><a href="/book/1/{i}.html">第{i}章 章{i}</a></dd>'
                    for i in range(1, n + 1)) + '</dl></body></html>')


def ch_html(n):
    return (f'<html><head><title>第{n}章 章{n}</title></head><body>'
            f'<h1>第{n}章 章{n}</h1><div id="content">{BODY}</div></body></html>')


class Site(BaseFetcher):
    name = "stat"
    MIN_DELAY = MAX_DELAY = 0
    chapters = N

    def fetch(self, url, quick=False):
        if url == BOOK:
            return toc_html(Site.chapters)
        n = url.split("/")[-1].split(".")[0]
        return ch_html(n)


FETCHERS["stat"] = Site


def tmpdir():
    p = os.path.join(r"C:\Users\31383\Desktop\novel-dl\tests\.tmp",
                     f"stat_{uuid.uuid4().hex[:8]}")
    os.makedirs(p, exist_ok=True)
    return p


def main():
    out = tmpdir()
    calls = []
    try:
        print("\n[1] 首次下载：回调必须报出正确的总数")
        eng = Engine(BOOK, driver="stat", out_dir=out,
                     on_toc=lambda *a: calls.append(a))
        eng.run()
        check("on_toc 被调用", len(calls) == 1, str(len(calls)))
        if calls:
            title, total, already, to_fetch = calls[0]
            check("书名正确", title == "统计测试", title)
            check(f"总数 = {N}", total == N, str(total))
            check("首次没有已完成章节", already == 0, str(already))
            check(f"待下载 = {N}", to_fetch == N, str(to_fetch))

        print("\n[2] 全部已下载：仍要报出总数（这是原 bug 的现场）")
        calls.clear()
        eng2 = Engine(BOOK, driver="stat", out_dir=out,
                      on_toc=lambda *a: calls.append(a))
        eng2.run()
        check("on_toc 仍被调用", len(calls) == 1, str(len(calls)))
        if calls:
            title, total, already, to_fetch = calls[0]
            check(f"总数仍是 {N}（不是 0）", total == N, str(total))
            check(f"已完成 {N} 章", already == N, str(already))
            check("待下载 = 0", to_fetch == 0, str(to_fetch))

        print("\n[3] 增量更新：新增章节时数字要对")
        Site.chapters = N + 6
        calls.clear()
        eng3 = Engine(BOOK, driver="stat", out_dir=out,
                      on_toc=lambda *a: calls.append(a))
        eng3.run()
        if calls:
            title, total, already, to_fetch = calls[0]
            check(f"总数更新为 {N+6}", total == N + 6, str(total))
            check(f"已完成 {N} 章", already == N, str(already))
            check("待下载 6 章", to_fetch == 6, str(to_fetch))
        p = Progress(out, BOOK)
        check("最终全部完成", len(p.state["done"]) == N + 6, str(len(p.state["done"])))

        print("\n[4] 回调抛异常不能拖垮下载")
        def boom(*a):
            raise RuntimeError("模拟回调崩溃")
        Site.chapters = N
        out2 = tmpdir()
        try:
            txt = Engine(BOOK, driver="stat", out_dir=out2, on_toc=boom).run()
            check("回调崩溃后仍能下载完成", os.path.exists(txt))
            p2 = Progress(out2, BOOK)
            check("章节仍然全下完", len(p2.state["done"]) == N, str(len(p2.state["done"])))
        finally:
            shutil.rmtree(out2, ignore_errors=True)

        print("\n[5] 不传回调时行为不变（向后兼容）")
        out3 = tmpdir()
        try:
            txt = Engine(BOOK, driver="stat", out_dir=out3).run()
            check("无回调也能正常下载", os.path.exists(txt))
            p3 = Progress(out3, BOOK)
            check("章节数正确", len(p3.state["done"]) == N, str(len(p3.state["done"])))
        finally:
            shutil.rmtree(out3, ignore_errors=True)
    finally:
        shutil.rmtree(out, ignore_errors=True)
        Site.chapters = N

    print(f"\n{'='*46}\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for n in FAIL:
            print("  ❌ " + n)
        return 1
    print("✅ 进度统计全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
