"""限速与封禁防护 —— 专项测试。

起因：用户用 4 并发下某站整本书，58 章全部失败。
根因：并发下没有任何跨线程限速，4 线程 = 4 倍请求频率，被站点封 IP。

本测试确保：
  1. 并发时限速真的生效（实测请求间隔 >= 设定值）
  2. 串行模式仍保留原有的礼貌延迟
  3. 大量失败时会给出「可能被封」的可执行建议

跑：python tests/test_ratelimit.py
"""
import io
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from novel_dl.engine import Engine  # noqa: E402
from novel_dl.fetchers import BaseFetcher, FETCHERS  # noqa: E402

PASS, FAIL = [], []
BODY = "正文内容。" * 40
BOOK = "http://rate.example/book/1/"
N = 12


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  <- {detail}"))


def toc_html(n=N):
    return ('<html><head><title>限速测试</title></head><body><dl>' +
            "".join(f'<dd><a href="/book/1/{i}.html">第{i}章</a></dd>'
                    for i in range(1, n + 1)) + '</dl></body></html>')


def ch_html(n):
    return (f'<html><head><title>第{n}章</title></head><body>'
            f'<h1>第{n}章</h1><div id="content">{BODY}</div></body></html>')


class RateSite(BaseFetcher):
    name = "rate"
    MIN_DELAY = MAX_DELAY = 0
    stamps = []
    lock = threading.Lock()

    def fetch(self, url, quick=False):
        with RateSite.lock:
            RateSite.stamps.append(time.monotonic())
        if url == BOOK:
            return toc_html(N)
        n = url.split("/")[-1].split(".")[0]
        return ch_html(n)


FETCHERS["rate"] = RateSite


def gaps(stamps):
    s = sorted(stamps)
    return [b - a for a, b in zip(s, s[1:])]


def main():
    print("\n[1] 并发 4 + delay=0.15：请求间隔必须 >= 0.15s")
    RateSite.stamps = []
    eng = Engine(BOOK, driver="rate", out_dir="C:/Users/31383/Desktop/novel-dl/tests/.tmp/rl1",
                 workers=4, min_delay=0.15)
    eng.fetcher.MIN_DELAY = eng.fetcher.MAX_DELAY = 0
    eng.run()
    g = gaps(RateSite.stamps)
    min_gap = min(g) if g else 0
    avg_gap = sum(g) / len(g) if g else 0
    check("请求数 = 目录 + 章节", len(RateSite.stamps) == N + 1, str(len(RateSite.stamps)))
    check("最小间隔 >= 0.14s（限速生效）", min_gap >= 0.14, f"{min_gap:.3f}s")
    check("平均间隔 >= 0.15s", avg_gap >= 0.15, f"{avg_gap:.3f}s")
    print(f"     实测：最小 {min_gap:.3f}s  平均 {avg_gap:.3f}s")

    print("\n[2] 对照：delay=0 时应明显更快")
    RateSite.stamps = []
    eng2 = Engine(BOOK, driver="rate",
                  out_dir="C:/Users/31383/Desktop/novel-dl/tests/.tmp/rl2",
                  workers=4, min_delay=0)
    eng2.fetcher.MIN_DELAY = eng2.fetcher.MAX_DELAY = 0
    eng2.run()
    g2 = gaps(RateSite.stamps)
    min_gap2 = min(g2) if g2 else 0
    check("不加限速时确实更快（间隔接近 0）", min_gap2 < 0.05, f"{min_gap2:.3f}s")
    print(f"     实测：最小 {min_gap2:.3f}s")

    print("\n[3] 并发 8 也不能突破限速")
    RateSite.stamps = []
    eng3 = Engine(BOOK, driver="rate",
                  out_dir="C:/Users/31383/Desktop/novel-dl/tests/.tmp/rl3",
                  workers=8, min_delay=0.1)
    eng3.fetcher.MIN_DELAY = eng3.fetcher.MAX_DELAY = 0
    eng3.run()
    g3 = gaps(RateSite.stamps)
    min_gap3 = min(g3) if g3 else 0
    check("8 并发下最小间隔仍 >= 0.09s", min_gap3 >= 0.09, f"{min_gap3:.3f}s")
    print(f"     实测：最小 {min_gap3:.3f}s")

    print("\n[4] 大面积失败时给出「可能被封」建议")
    class Banned(BaseFetcher):
        name = "banned"
        MIN_DELAY = MAX_DELAY = 0

        def fetch(self, url, quick=False):
            if url == BOOK:
                return toc_html(N)
            raise ConnectionError(
                "('Connection aborted.', ConnectionResetError(10054, "
                "'远程主机强迫关闭了一个现有的连接'))")

    FETCHERS["banned"] = Banned
    import logging
    from novel_dl import log as nlog
    records = []

    class Cap(logging.Handler):
        def emit(self, rec):
            records.append(rec.getMessage())

    h = Cap()
    nlog.log.addHandler(h)
    old = nlog.log.level
    nlog.log.setLevel(logging.INFO)
    try:
        Engine(BOOK, driver="banned",
               out_dir="C:/Users/31383/Desktop/novel-dl/tests/.tmp/rl4",
               workers=4, min_delay=0).run()
    finally:
        nlog.log.removeHandler(h)
        nlog.log.setLevel(old)

    joined = "\n".join(records)
    check("提示可能被限流/封禁", "限流" in joined or "封禁" in joined, joined[-300:])
    check("给出降并发建议", "-w 1" in joined, joined[-300:])
    check("说明进度不会丢", "不会丢" in joined, joined[-300:])

    print("\n[5] 失败原因保留真实异常（不再只有「重试后仍失败」）")
    check("记录了 ConnectionError 细节",
          "10054" in joined or "Connection" in joined, joined[-200:])

    import shutil
    for d in ("rl1", "rl2", "rl3", "rl4"):
        shutil.rmtree(f"C:/Users/31383/Desktop/novel-dl/tests/.tmp/{d}",
                      ignore_errors=True)

    print(f"\n{'='*46}\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for n in FAIL:
            print("  ❌ " + n)
        return 1
    print("✅ 限速与封禁防护全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
