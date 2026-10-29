"""本地基准测试：量化「性能」到底慢在哪，以及优化后快了多少。

完全离线：起一个本地 HTTP 服务模拟小说站，可以注入延迟和失败率。

跑：
    python tests/bench.py                 # 基准（逐章串行）
    python tests/bench.py --workers 4     # 并发 4
    python tests/bench.py --delay-ms 50   # 模拟每章 50ms 网络延迟
    python tests/bench.py --chapters 300  # 章节数

输出每一项的耗时分解，方便定位瓶颈。
"""
import argparse
import os
import shutil
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from novel_dl import fetchers, log as nlog          # noqa: E402
from novel_dl.engine import Engine                   # noqa: E402

nlog.log.setLevel(50)   # 静音，只看基准数字

BODY = "夜色沉沉，他提灯走过长街。" * 60


def make_toc(n):
    return ('<html><head><title>基准小说_在线阅读</title></head><body><dl>' +
            "".join(f'<dd><a href="/book/1/{i}.html">第{i}章 测试章节{i}</a></dd>'
                    for i in range(1, n + 1)) +
            '</dl></body></html>')


def make_chapter(n):
    return ('<html><head><title>第{n}章 测试章节{n}</title></head><body>'
            '<h1>第{n}章 测试章节{n}</h1>'
            '<div id="content">' + BODY + '</div></body></html>').format(n=n)


class Handler(BaseHTTPRequestHandler):
    chapters = 100
    delay_ms = 0
    fail_rate = 0.0
    request_count = 0
    _lock = threading.Lock()

    def do_GET(self):
        with Handler._lock:
            Handler.request_count += 1
        if Handler.delay_ms:
            time.sleep(Handler.delay_ms / 1000.0)
        if self.path == '/book/1/':
            body = make_toc(Handler.chapters).encode()
        else:
            n = self.path.rstrip('.html').split('/')[-1]
            if not n.isdigit():
                self.send_response(404); self.end_headers(); return
            # 失败率：用于测重试是否真的生效
            if Handler.fail_rate and (int(n) % max(1, int(1 / Handler.fail_rate)) == 0):
                self.send_response(503); self.end_headers(); return
            body = make_chapter(int(n)).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def run_bench(chapters, delay_ms, fail_rate, workers, use_parser_direct=False):
    Handler.chapters = chapters
    Handler.delay_ms = delay_ms
    Handler.fail_rate = fail_rate
    Handler.request_count = 0

    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    out = os.path.join(ROOT, "tests", ".bench", uuid.uuid4().hex[:8])
    os.makedirs(out, exist_ok=True)
    url = f"http://127.0.0.1:{port}/book/1/"

    t0 = time.perf_counter()
    try:
        eng = Engine(url, driver="requests", out_dir=out,
                     min_delay=0, workers=workers)
        eng.fetcher.MIN_DELAY = 0
        eng.fetcher.MAX_DELAY = 0
        txt = eng.run()
        elapsed = time.perf_counter() - t0

        chapters_ok = 0
        if os.path.exists(txt):
            with open(txt, encoding="utf-8") as f:
                chapters_ok = f.read().count("=== 第")
    finally:
        srv.shutdown()
        shutil.rmtree(out, ignore_errors=True)

    reqs = Handler.request_count
    return {
        "elapsed": elapsed,
        "chapters_ok": chapters_ok,
        "requests": reqs,
        "chapters": chapters,
        "workers": workers,
        "delay_ms": delay_ms,
        "fail_rate": fail_rate,
        "req_per_s": reqs / elapsed if elapsed else 0,
        "chapters_per_s": chapters_ok / elapsed if elapsed else 0,
    }


def fmt(r):
    return (f"  {r['elapsed']:7.2f}s | "
            f"{r['chapters_ok']:4d}/{r['chapters']:<4d} 章 | "
            f"{r['requests']:5d} 请求 | "
            f"{r['req_per_s']:7.1f} req/s | "
            f"workers={r['workers']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapters", type=int, default=100)
    ap.add_argument("--delay-ms", type=int, default=0)
    ap.add_argument("--fail-rate", type=float, default=0.0)
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args()

    print(f"\n基准：{args.chapters} 章，每章模拟网络延迟 {args.delay_ms}ms，"
          f"失败率 {args.fail_rate:.0%}\n")
    print("  耗时     | 完成章数   | 请求数     | 吞吐          | 配置")
    print("  " + "-" * 74)

    rows = []
    for w in ([1, 2, 4, 8] if args.workers == 0 else [args.workers]):
        r = run_bench(args.chapters, args.delay_ms, args.fail_rate, w)
        rows.append(r)
        print(fmt(r))

    if len(rows) > 1:
        base = rows[0]
        print("\n  相对串行（workers=1）的加速比：")
        for r in rows:
            sp = base["elapsed"] / r["elapsed"] if r["elapsed"] else 0
            print(f"    workers={r['workers']:<2d}  {sp:5.2f}x")
    print()


if __name__ == "__main__":
    main()
