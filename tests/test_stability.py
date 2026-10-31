"""稳定性专项测试：并发 / 中断 / 暂停 / 账本一致性。"""
import io
import os
import shutil
import sys
import tempfile
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
    p = os.path.join(tempfile.gettempdir(), "novel-dl-tests",
                     f"{kind}_{uuid.uuid4().hex[:8]}")
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
    StabFetcher.delay = 0.04
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
        e = Engine(BOOK, driver="stab", out_dir=out, workers=1)
        e.refresh_toc()
        p = e.prog
        for i in range(30):
            text, t, _preview = e._fetch_chapter(i, p.state["toc"])
            p.write_chapter(i, text)
            p.mark_done(i)          # 故意不 flush —— 模拟断电

        files_before = len([f for f in os.listdir(p.cache_dir) if f.endswith(".txt")])
        check("章节文件已落盘（正文每章即写）", files_before == 30, str(files_before))

        StabFetcher.calls = []
        Engine(BOOK, driver="stab", out_dir=out, workers=4).run()
        ch_calls = [u for u in StabFetcher.calls if u != BOOK]
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
    """第三方 fetcher 只实现 fetch(url) 时必须仍能工作。"""
    print("\n[7] 向后兼容：老式 fetch(url) 签名仍可用")
    out = tmpdir("legacy")

    class LegacyFetcher(fetchers.BaseFetcher):
        name = "legacy"
        MIN_DELAY = MAX_DELAY = 0

        def fetch(self, url):
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


def test_toc_reorder():
    """站点在目录中间插章 / 互换两章时，缓存正文必须跟着章节走。"""
    print("\n[8] 目录重排：正文跟着章节走，不串章")
    out = tmpdir("reorder")
    book = "https://reorder.example/book/7/"
    base = [(f"第{i}章", f"https://reorder.example/book/7/{i}.html")
            for i in range(1, 4)]
    try:
        p = Progress(out, book)
        p.set_book(book, "重排测试", base)
        for i in range(3):
            p.write_chapter(i, f"CONTENT-{i}")
            p.mark_done(i)
        p.mark_preview(1, "付费章节，只拿到 100 字预览")
        p.save(force=True)

        # 最前面插入一章（最典型的场景）
        p2 = Progress(out, book)
        p2.set_book(book, "重排测试",
                    [("序章", "https://reorder.example/book/7/0.html")] + base)
        check("前插一章后第 1 章仍是原第 1 章正文",
              (p2.read_chapter(1) or "") == "CONTENT-0",
              repr(p2.read_chapter(1)))
        check("前插一章后第 3 章仍是原第 3 章正文",
              (p2.read_chapter(3) or "") == "CONTENT-2",
              repr(p2.read_chapter(3)))
        check("新增的序章没有正文", p2.read_chapter(0) is None,
              repr(p2.read_chapter(0)))
        check("账本没有把新章记成已完成", 0 not in p2.state["done"],
              str(p2.state["done"]))
        check("预览标记跟着章节搬到了下标 2",
              (p2.state["preview"] or {}).get("2", "").startswith("付费章节"),
              str(p2.state["preview"]))

        # 两章互换
        p3 = Progress(out, book)
        p3.set_book(book, "重排测试", [base[1], base[0]])
        check("互换后下标 0 是原第 2 章", (p3.read_chapter(0) or "") == "CONTENT-1",
              repr(p3.read_chapter(0)))
        check("互换后下标 1 是原第 1 章", (p3.read_chapter(1) or "") == "CONTENT-0",
              repr(p3.read_chapter(1)))
        check("互换没有产生丢章", os.path.exists(p3._path(0)) and
              os.path.exists(p3._path(1)))
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_failure_budget():
    """站点整体不可达时，并发模式也必须尽早停手，不能把每章都重试满。"""
    print("\n[9] 并发失败预算：熔断要真的生效")
    out = tmpdir("budget")

    class DeadFetcher(fetchers.BaseFetcher):
        name = "dead"
        MIN_DELAY = MAX_DELAY = 0
        calls = []

        def fetch(self, url, quick=False):
            DeadFetcher.calls.append(url)
            if url == BOOK:
                return toc_html(40)
            raise ConnectionResetError(10054, "远程主机强迫关闭了一个现有的连接")

    fetchers.FETCHERS["dead"] = DeadFetcher
    try:
        Engine(BOOK, driver="dead", out_dir=out, workers=4).run()
        ch_calls = [u for u in DeadFetcher.calls if u != BOOK]
        check("并发模式没有把 40 章全重试满", len(ch_calls) <= 24,
              f"实际请求 {len(ch_calls)} 章")
        p = Progress(out, BOOK)
        check("熔断没有把整本都记成失败",
              len(p.state["failed"]) < 40, str(len(p.state["failed"])))
        check("熔断时没有误记已完成", not p.state["done"], str(p.state["done"]))
    finally:
        fetchers.FETCHERS.pop("dead", None)
        shutil.rmtree(out, ignore_errors=True)


def test_ledger_vs_disk():
    """缓存文件在、账本却记着失败 → 以磁盘为准。"""
    print("\n[10] 账本与磁盘不一致时以磁盘为准")
    out = tmpdir("ledger")
    try:
        p = Progress(out, BOOK)
        p.set_book(BOOK, "一致性", [(f"第{i}章", f"http://x/{i}.html") for i in range(3)])
        p.write_chapter(1, "第1章\n\n" + "正文" * 100)
        p.mark_failed(1, "之前网络失败")
        p.mark_failed(2, "还是失败")
        p.save(force=True)

        p2 = Progress(out, BOOK)
        recovered = p2.recover_from_disk()
        check("磁盘上的章被认回", 1 in p2.state["done"], str(p2.state["done"]))
        check("对应的失败记录被清掉", 1 not in p2.state["failed"], str(p2.state["failed"]))
        check("失败原因一并清掉", "1" not in (p2.state.get("fail_log") or {}),
              str(p2.state.get("fail_log")))
        check("真的没缓存的那章还在失败里", 2 in p2.state["failed"],
              str(p2.state["failed"]))
        check("补回的就是那一章", recovered == 1, str(recovered))
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_cache_naming():
    """缓存文件名只认两种形态：5 位序号（旧）和 16 位摘要（新）。"""
    print("\n[10] 缓存文件名识别：纯数字摘要不当成旧序号")
    from novel_dl.progress import legacy_index

    check("旧式 00002.txt 认成下标 2", legacy_index("00002.txt") == 2,
          str(legacy_index("00002.txt")))
    check("摘要恰好全是数字时不当成旧序号",
          legacy_index("0256661888404515.txt") is None,
          str(legacy_index("0256661888404515.txt")))
    check("非 txt 不算", legacy_index("00002.json") is None)


def main():
    test_concurrent_completeness()
    test_no_duplicate_requests()
    test_stop_is_responsive()
    test_crash_recovery()
    test_state_atomicity()
    test_failure_isolation()
    test_legacy_fetcher_signature()
    test_toc_reorder()
    test_failure_budget()
    test_cache_naming()
    test_ledger_vs_disk()
    print(f"\n{'='*46}\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for n in FAIL:
            print("  ❌ " + n)
        return 1
    print("✅ 稳定性全部通过")
    return 0

if __name__ == "__main__":
    sys.exit(main())
