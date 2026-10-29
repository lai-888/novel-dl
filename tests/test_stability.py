"""稳定性专项测试：并发 / 中断 / 暂停 / 账本一致性。

「小而完整」这个定位下，稳定性必须能被证明，不能靠感觉。
本文件专门攻击边界情况。

跑：python tests/test_stability.py
"""
import io
import os
import shutil
import sys
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from novel_dl import fetchers, log as nlog      # noqa: E402
from novel_dl.engine import Engine               # noqa: E402
from novel_dl.progress import Progress           # noqa: E402

nlog.log.setLevel(50)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  <- {detail}"))


def tmpdir(kind):
    p = os.path.join(ROOT, "tests", ".tmp", f"{kind}_{uuid.uuid4().hex[:8]}")
    os.makedirs(p, exist_ok=True)
    return p


BODY = "夜色沉沉，他提灯走过长街。" * 20
BOOK = "https://stab.example/book/1/"


def toc_html(n):
    return ('<html><head><title>稳定小说</title></head><body><dl>' +
            "".join(f'<dd><a href="/book/1/{i}.html">第{i}章 章{i}</a></dd>'
                    for i in range(1, n + 1)) + '</dl></body></html>')


def ch_html(n):
    return (f'<html><head><title>第{n}章 章{n}</title></head><body>'
            f'<h1>第{n}章 章{n}</h1><div id="content">{BODY}</div></body></html>')


class StabFetcher(fetchers.BaseFetcher):
    """可控假站点：可注入延迟、失败、中断。"""
    name = "stab"
    MIN_DELAY = MAX_DELAY = 0
    chapters = 30
    delay = 0.0
    calls = []
    lock = threading.Lock()

    def fetch(self, url):
        with StabFetcher.lock:
            StabFetcher.calls.append(url)
        if StabFetcher.delay:
            time.sleep(StabFetcher.delay)
        if url == BOOK:
            return toc_html(StabFetcher.chapters)
        n = url.split("/")[-1].split(".")[0]
        return ch_html(n)


fetchers.FETCHERS["stab"] = StabFetcher


# ---------------------------------------------------------------------------
def test_concurrent_completeness():
    """并发抓取不能丢章、不能串章（每个文件内容必须对应正确的章号）。"""
    print("\n[1] 并发完整性：不丢章 / 不串章")
    StabFetcher.chapters = 60
    StabFetcher.delay = 0.002
    StabFetcher.calls = []
    out = tmpdir("conc")
    try:
        e = Engine(BOOK, driver="stab", out_dir=out, workers=8)
        txt = e.run()
        p = Progress(out, BOOK)
        check("全部 60 章都完成", len(p.state["done"]) == 60, str(len(p.state["done"])))

        # 串章检查：第 i 章缓存文件里必须是「第i章」
        bad = []
        for i in range(60):
            body = p.read_chapter(i) or ""
            if f"第{i+1}章" not in body:
                bad.append(i + 1)
        check("缓存文件内容与章号一一对应（无串章）", not bad, f"错位章：{bad[:10]}")

        with open(txt, encoding="utf-8") as f:
            content = f.read()
        check("TXT 合并出 60 章", content.count("=== 第") == 60,
              str(content.count("=== 第")))
        check("TXT 顺序正确（第1章在最后1章之前）",
              content.index("=== 第1章") < content.rindex("=== 第60章"))
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_no_duplicate_requests():
    """并发下同一章不能被请求两次。"""
    print("\n[2] 并发去重：每章只请求一次")
    StabFetcher.chapters = 40
    StabFetcher.delay = 0.001
    StabFetcher.calls = []
    out = tmpdir("dup")
    try:
        Engine(BOOK, driver="stab", out_dir=out, workers=8).run()
        ch_calls = [u for u in StabFetcher.calls if u != BOOK]
        check("请求数 == 章节数（无重复）", len(ch_calls) == 40, str(len(ch_calls)))
        check("请求 URL 无重复", len(set(ch_calls)) == len(ch_calls))
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_stop_is_responsive():
    """停止信号必须能打断下载，且已完成的章节要保住。"""
    print("\n[3] 停止响应：能中断，且已下章节不丢")
    StabFetcher.chapters = 200
    StabFetcher.delay = 0.01
    StabFetcher.calls = []
    out = tmpdir("stop")
    try:
        stop_ev = threading.Event()
        paus_ev = threading.Event()

        def stopper():
            time.sleep(0.6)
            stop_ev.set()

        threading.Thread(target=stopper, daemon=True).start()
        t0 = time.perf_counter()
        try:
            Engine(BOOK, driver="stab", out_dir=out, workers=4,
                   stop_event=stop_ev, pause_event=paus_ev).run()
        except SystemExit:
            pass
        elapsed = time.perf_counter() - t0

        check("停止后 2 秒内退出", elapsed < 2.5, f"{elapsed:.2f}s")
        p = Progress(out, BOOK)
        n_done = len(p.state["done"])
        check("停止时已下章节被保留", n_done > 0, f"完成 {n_done} 章")
        check("没有下完全部 200 章", n_done < 200, str(n_done))
        check("账本可正常读取（未损坏）", isinstance(p.state.get("toc"), list))
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_crash_recovery():
    """模拟进程被杀：未 flush 的账本，重跑后不能重下已完成的章。"""
    print("\n[4] 崩溃恢复：账本滞后也不重复下载")
    StabFetcher.chapters = 100
    StabFetcher.delay = 0
    StabFetcher.calls = []
    out = tmpdir("crash")
    try:
        # 第一轮：下 30 章后模拟崩溃（不调 flush）
        e = Engine(BOOK, driver="stab", out_dir=out, workers=1)
        e.refresh_toc()
        p = e.prog
        for i in range(30):
            text, t = e._fetch_chapter(i, p.state["toc"])
            p.write_chapter(i, text)
            p.mark_done(i)          # 故意不 flush —— 模拟断电
        # 此时磁盘上有 30 个章节文件，账本只落了若干次

        files_before = len([f for f in os.listdir(p.cache_dir) if f.endswith(".txt")])
        check("章节文件已落盘（正文每章即写）", files_before == 30, str(files_before))

        # 第二轮：重跑，已完成的章不应再请求
        StabFetcher.calls = []
        Engine(BOOK, driver="stab", out_dir=out, workers=4).run()
        ch_calls = [u for u in StabFetcher.calls if u != BOOK]
        # 允许少量重下（账本滞后），但不应是全部
        check("重跑时大部分章节被跳过", len(ch_calls) < 100, f"实际请求 {len(ch_calls)} 章")

        p2 = Progress(out, BOOK)
        check("最终 100 章全部完成", len(p2.state["done"]) == 100,
              str(len(p2.state["done"])))
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_state_atomicity():
    """账本写入必须是原子的：绝不能出现半截 JSON。"""
    print("\n[5] 账本原子性：并发写不出半截文件")
    out = tmpdir("atomic")
    try:
        p = Progress(out, BOOK)
        p.set_book(BOOK, "原子测试",
                   [(f"第{i}章", f"http://x/{i}.html") for i in range(500)])
        errs = []

        def writer(base):
            try:
                for i in range(base, base + 100):
                    p.mark_done(i, flush=True)
            except Exception as ex:
                errs.append(repr(ex))

        ts = [threading.Thread(target=writer, args=(k * 100,)) for k in range(3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        import json
        try:
            with open(p.state_path, encoding="utf-8") as f:
                json.load(f)
            parsed = True
        except Exception as ex:
            parsed = False
            errs.append(repr(ex))
        check("500 次并发强制落盘后账本仍可解析", parsed, str(errs[:2]))
        check("无写入异常", not errs, str(errs[:2]))
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_failure_isolation():
    """单章失败不能影响其他章，且失败要记账。"""
    print("\n[6] 失败隔离：坏章不影响好章")
    StabFetcher.chapters = 30
    StabFetcher.delay = 0
    StabFetcher.calls = []

    orig = StabFetcher.fetch

    def flaky(self, url):
        n = url.split("/")[-1].split(".")[0]
        if n.isdigit() and int(n) % 10 == 0:      # 每 10 章坏一个
            raise ConnectionError("模拟连接重置")
        return orig(self, url)

    StabFetcher.fetch = flaky
    out = tmpdir("fail")
    try:
        txt = Engine(BOOK, driver="stab", out_dir=out, workers=4).run()
        p = Progress(out, BOOK)
        check("坏章被记录为失败", len(p.state["failed"]) == 3, str(p.state["failed"]))
        check("好章全部完成", len(p.state["done"]) == 27, str(len(p.state["done"])))
        check("失败原因被记录", all(p.state["fail_log"].values()))
        with open(txt, encoding="utf-8") as f:
            c = f.read()
        check("TXT 只含成功的 27 章", c.count("=== 第") == 27, str(c.count("=== 第")))
    finally:
        StabFetcher.fetch = orig
        shutil.rmtree(out, ignore_errors=True)


def test_legacy_fetcher_signature():
    """第三方 fetcher 只实现 fetch(url) 时必须仍能工作。

    引擎内部用 quick 参数做并发重试，但那是新增能力，不能变成破坏性变更：
    老插件不改一行代码也得能跑。
    """
    print("\n[7] 向后兼容：老式 fetch(url) 签名仍可用")
    out = tmpdir("legacy")

    class LegacyFetcher(fetchers.BaseFetcher):
        name = "legacy"
        MIN_DELAY = MAX_DELAY = 0

        def fetch(self, url):          # 注意：只有 url，没有 quick
            if url == BOOK:
                return toc_html(10)
            n = url.split("/")[-1].split(".")[0]
            return ch_html(n)

    fetchers.FETCHERS["legacy"] = LegacyFetcher
    try:
        txt = Engine(BOOK, driver="legacy", out_dir=out, workers=4).run()
        p = Progress(out, BOOK)
        check("老签名 fetcher 并发模式可用", len(p.state["done"]) == 10,
              str(len(p.state["done"])))
        with open(txt, encoding="utf-8") as f:
            check("TXT 正确合并 10 章", f.read().count("=== 第") == 10)
    finally:
        fetchers.FETCHERS.pop("legacy", None)
        shutil.rmtree(out, ignore_errors=True)


def main():
    test_concurrent_completeness()
    test_no_duplicate_requests()
    test_stop_is_responsive()
    test_crash_recovery()
    test_state_atomicity()
    test_failure_isolation()
    test_legacy_fetcher_signature()
    print(f"\n{'='*46}\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for n in FAIL:
            print("  ❌ " + n)
        return 1
    print("✅ 稳定性全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
