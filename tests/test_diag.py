"""调试工具的回归：状态快照、只读查看、事件轨迹。

运行：python tests/test_diag.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 假站点别把学到的规则写进用户真实缓存
os.environ.setdefault("NOVEL_DL_CACHE",
                      os.path.join(tempfile.gettempdir(), "novel-dl-tests",
                                   "learned.json"))

from novel_dl import diag, fetchers, log as nlog  # noqa: E402
from novel_dl.engine import Engine  # noqa: E402
from novel_dl.__main__ import main as cli_main  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  <- {detail}"))


BODY = "夜色沉沉，他提灯走过长街，远处传来打更的声音。" * 12
N = 12


def toc_html():
    return ('<html><head><title>调试测试小说_在线阅读</title></head><body><dl>'
            + "".join(f'<dd><a href="/book/1/{i}.html">第{i}章 调试{i}</a></dd>'
                      for i in range(1, N + 1))
            + "</dl></body></html>")


def ch_html(n):
    return (f'<html><head><title>第{n}章 调试{n}</title></head><body>'
            f'<h1>第{n}章 调试{n}</h1><div id="content">{BODY}</div></body></html>')


class Site(BaseHTTPRequestHandler):
    """故意用 gbk 发送、声明写成 gb2312，轨迹里能看到编码判断。"""

    def do_GET(self):
        if self.path.rstrip("/").endswith("book/1"):
            body = toc_html()
        else:
            last = self.path.rsplit("/", 1)[-1]
            num = last.split(".")[0]
            if not num.isdigit():
                self.send_response(404)
                self.end_headers()
                return
            body = ch_html(int(num))
        raw = body.encode("gbk")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=gb2312")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


def start_site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/book/1/"


def tmpdir(tag):
    d = os.path.join(tempfile.gettempdir(), "novel-dl-tests", "diag" + tag + uuid.uuid4().hex[:6])
    os.makedirs(d, exist_ok=True)
    return d


def main():
    print("\n[1] 状态快照：分区齐全，且不泄露敏感信息")
    data = diag.collect()
    check("有运行环境", "runtime" in data and data["runtime"].get("版本"), data.keys())
    check("有依赖清单", "deps" in data and "requests" in data["deps"])
    check("有配置", "config" in data and "输出目录" in data["config"])
    text = diag.report(data)
    for head in ("运行环境", "依赖", "配置"):
        check(f"报告里有「{head}」", head in text)
    check("报告不带 cookie 值", "sessionid=" not in text)

    print("\n[2] 敏感字段只报形状")
    check("代理账号密码被隐去", diag.mask_proxy("http://u:p@127.0.0.1:7890") == "http://***@127.0.0.1:7890",
          diag.mask_proxy("http://u:p@127.0.0.1:7890"))
    check("没密码的代理原样保留", diag.mask_proxy("http://127.0.0.1:7890") == "http://127.0.0.1:7890")
    check("空代理不算错", diag.mask_proxy("") == "")
    check("cookie 只数条数", diag.cookie_count("a=1; b=2; c=3") == 3, diag.cookie_count("a=1; b=2; c=3"))

    print("\n[3] 轨迹是环形缓冲，不会吃爆内存")
    t = diag.Trace(cap=5)
    for i in range(20):
        t.emit("tick", i=i)
    check("只留最近 cap 条", len(t.events()) == 5, len(t.events()))
    check("丢了 15 条", t.dropped == 15, t.dropped)
    check("留下的是最新的", t.events()[-1]["i"] == 19, t.events()[-1])
    long_msg = "字" * 500
    t.emit("long", msg=long_msg)
    check("超长字段被截断", len(t.events("long")[-1]["msg"]) == 200,
          len(t.events("long")[-1]["msg"]))

    print("\n[4] 轨迹能落盘成 JSONL")
    path = os.path.join(tmpdir("trace"), "run.jsonl")
    t2 = diag.Trace(path)
    t2.emit("req.ok", url="http://x/1.html", ms=12, enc="gb18030")
    t2.emit("ch.ok", no=1, chars=100)
    t2.close()
    with open(path, encoding="utf-8") as f:
        lines = [json.loads(x) for x in f if x.strip()]
    check("写了两行", len(lines) == 2, len(lines))
    check("每行有时间和类型", all("t" in r and "kind" in r for r in lines))
    check("字段原样保留", lines[0]["enc"] == "gb18030" and lines[0]["ms"] == 12, lines[0])

    print("\n[5] 只读查看站点：抓目录页但不写任何文件")
    srv, url = start_site()
    try:
        out_dir = tmpdir("peek")
        info = diag.peek(url)
        check("认出解析器", info.get("解析器") in ("SiteRuleParser", "GenericParser"), info.get("解析器"))
        check("读到书名", info.get("书名") == "调试测试小说", info.get("书名"))
        check("数出章数", info.get("目录章数") == N, info.get("目录章数"))
        check("带了抓取耗时", isinstance(info.get("抓取毫秒"), int), info.get("抓取毫秒"))
        check("查看不会生成书目录", not os.listdir(out_dir), os.listdir(out_dir))

        bad = diag.peek(url + "nope/")
        check("抓不到时不抛异常，只报原因",
              "抓取失败" in bad or "目录解析失败" in bad, list(bad))

        print("\n[6] --inspect 只打印，不下载")
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            cli_main([url, "--inspect", "-o", out_dir])
        finally:
            sys.stdout = old
        printed = buf.getvalue()
        check("打印了站点段", "◆ 站点" in printed, printed[-200:])
        check("打印了目录章数", "目录章数" in printed and str(N) in printed)
        check("没有开始下载", "✓ [" not in printed)
        check("没往输出目录写东西", not os.listdir(out_dir), os.listdir(out_dir))
    finally:
        srv.shutdown()

    print("\n[7] 跑一本书：轨迹里能看到内核在干什么")
    srv, url = start_site()
    try:
        out = tmpdir("run")
        trace = diag.Trace(os.path.join(out, "trace.jsonl"))
        diag.set_trace(trace)
        try:
            Engine(url, driver="requests", out_dir=out, workers=3,
                   min_delay=0).run()
        finally:
            diag.set_trace(None)
        kinds = [r["kind"] for r in trace.events()]
        check("记了目录解析", "toc" in kinds, kinds[:10])
        check("记了每次请求", kinds.count("req.start") >= N, kinds.count("req.start"))
        check("记了每章完成", kinds.count("ch.ok") == N, kinds.count("ch.ok"))
        check("记了并发窗口", "win" in kinds, kinds)
        oks = trace.events("req.ok")
        check("请求事件带耗时和驱动",
              all("ms" in r and r.get("driver") == "requests" for r in oks), oks[:1])
        encs = {r["enc"] for r in trace.events("enc") if r.get("enc")}
        check("记了解码结果", "gb18030" in encs, encs)
        declared = {r["declared"] for r in trace.events("enc")}
        check("记了站点声明的编码", "gb2312" in declared, declared)
        check("没有失败事件", not trace.events("ch.fail"), trace.events("ch.fail")[:2])
        trace.close()
        check("轨迹文件非空", os.path.getsize(os.path.join(out, "trace.jsonl")) > 0)
    finally:
        srv.shutdown()

    print("\n[8] 被限流时轨迹里有熔断记录")
    BOOK = "https://diag.example/book/1/"

    class Throttle(fetchers.BaseFetcher):
        name = "diag-throttle"
        MIN_DELAY = MAX_DELAY = 0
        window, limit = 0.2, 2
        recent = []
        lock = threading.Lock()

        def fetch(self, url, quick=False):
            import time
            now = time.monotonic()
            with Throttle.lock:
                Throttle.recent = [t for t in Throttle.recent if now - t < Throttle.window]
                Throttle.recent.append(now)
                too_fast = len(Throttle.recent) > Throttle.limit
            if url == BOOK:
                return toc_html()
            if too_fast:
                raise ConnectionError("429 Too Many Requests")
            return ch_html(int(url.rsplit("/", 1)[-1].split(".")[0]))

    fetchers.FETCHERS["diag-throttle"] = Throttle
    old_base, old_gap = fetchers.Breaker.BASE, fetchers.Breaker.SLOW_GAP
    fetchers.Breaker.BASE, fetchers.Breaker.SLOW_GAP = 0.05, 0.1
    out = tmpdir("thr")
    trace = diag.Trace()
    diag.set_trace(trace)
    h = nlog.log.handlers[:]
    nlog.log.setLevel(50)
    try:
        Engine(BOOK, driver="diag-throttle", out_dir=out, workers=6, min_delay=0).run()
    finally:
        nlog.log.setLevel(20)
        for _ in h:
            pass
        diag.set_trace(None)
        fetchers.Breaker.BASE, fetchers.Breaker.SLOW_GAP = old_base, old_gap
        fetchers.FETCHERS.pop("diag-throttle", None)
    opens = trace.events("breaker.open")
    check("记下了冷却", len(opens) > 0, len(opens))
    check("冷却事件带原因", any("429" in str(r.get("err", "")) for r in opens), opens[:1])
    rises = trace.events("breaker.rise")
    check("记下了恢复", len(rises) > 0, len(rises))
    check("失败也留痕", len(trace.events("req.fail")) > 0, len(trace.events("req.fail")))

    print("\n[9] 网页端也能取到内核状态")
    import urllib.request
    import webui

    srv, url = start_site()
    web_srv = None
    try:
        for port in range(8960, 8975):
            try:
                web_srv = ThreadingHTTPServer(("127.0.0.1", port), webui.Handler)
                break
            except OSError:
                continue
        if web_srv is None:
            check("网页端能起来", False, "端口都被占了")
        else:
            threading.Thread(target=web_srv.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{web_srv.server_address[1]}"
            raw = urllib.request.urlopen(base + "/api/diag", timeout=10).read()
            got = json.loads(raw.decode("utf-8"))
            check("接口返回 200 且有运行环境", "runtime" in got, list(got))
            check("带依赖清单", "deps" in got and got["deps"].get("requests"))
            check("带配置", "config" in got)
            check("网页端不泄露 cookie", "sessionid" not in json.dumps(got, ensure_ascii=False))
    finally:
        srv.shutdown()
        if web_srv is not None:
            web_srv.shutdown()

    print("\n[10] 进度扫描只读不写")
    out = tmpdir("disk")
    srv, url = start_site()
    try:
        Engine(url, driver="requests", out_dir=out, workers=1, min_delay=0).run()
    finally:
        srv.shutdown()
    books = [d for d in os.listdir(out) if os.path.isdir(os.path.join(out, d))]
    before = {}
    for d in books:
        for root, _dirs, files in os.walk(os.path.join(out, d)):
            for f in files:
                p = os.path.join(root, f)
                before[p] = os.path.getmtime(p)
    info = diag.disk_progress(out, url)
    check("读到书名", info.get("书名") == "调试测试小说", info)
    check("读到完成章数", info.get("已完成") == N, info.get("已完成"))
    check("读到账本版本", info.get("账本版本") == 2, info.get("账本版本"))
    after = {}
    for d in books:
        for root, _dirs, files in os.walk(os.path.join(out, d)):
            for f in files:
                p = os.path.join(root, f)
                after[p] = os.path.getmtime(p)
    check("没有新建或改动文件", before == after, set(after) - set(before))
    empty = diag.disk_progress(os.path.join(out, "nope"))
    check("空目录只给说明", "说明" in empty and not os.path.exists(os.path.join(out, "nope")), empty)
    shutil.rmtree(out, ignore_errors=True)

    print(f"\n{'='*46}\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for n in FAIL:
            print("  ❌ " + n)
        return 1
    print("✅ 调试工具全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())