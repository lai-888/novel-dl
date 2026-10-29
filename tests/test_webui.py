"""端到端验证网页界面：起假站点 → 起 webui → 走 API 跑一次真实下载。

只验证「界面能正常驱动下载」，不测样式（样式靠肉眼看截图）。

跑：python tests/test_webui.py
"""
import io
import json
import os
import shutil
import sys
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  <- {detail}"))


BODY = "夜色沉沉，他提灯走过长街。" * 20
N = 12


def toc_html():
    return ('<html><head><title>界面测试小说_在线阅读</title></head><body><dl>' +
            "".join(f'<dd><a href="/book/1/{i}.html">第{i}章 测试章节{i}</a></dd>'
                    for i in range(1, N + 1)) + '</dl></body></html>')


def ch_html(n):
    return (f'<html><head><title>第{n}章 测试章节{n}</title></head><body>'
            f'<h1>第{n}章 测试章节{n}</h1><div id="content">{BODY}</div></body></html>')


class FakeSite(BaseHTTPRequestHandler):
    def do_GET(self):
        # 目录页：/book/1/ 或 /book/2/（去掉末尾斜杠后判断）
        if self.path.rstrip('/') in ('/book/1', '/book/2'):
            body = toc_html().encode()
        else:
            n = self.path.rstrip('.html').split('/')[-1]
            if not n.isdigit():
                self.send_response(404); self.end_headers(); return
            body = ch_html(int(n)).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def api(base, path, payload=None):
    url = base + path
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # 400/409 这类业务错误也带 JSON body，照常解析
        return json.loads(e.read().decode())


def main():
    # 1. 假站点
    site = ThreadingHTTPServer(('127.0.0.1', 0), FakeSite)
    site_port = site.server_address[1]
    threading.Thread(target=site.serve_forever, daemon=True).start()
    book_url = f"http://127.0.0.1:{site_port}/book/1/"
    # 第二本书（同站点不同路径），用于测试自定义保存目录
    book_url2 = f"http://127.0.0.1:{site_port}/book/2/"

    # 2. 起 webui（用独立进程内服务，避免 Start-Process 时序问题）
    import webui
    from novel_dl import log as nlog
    nlog.log.handlers.clear()
    nlog.setup()
    nlog.log.addHandler(webui._StateHandler())   # 让日志进 STATE["lines"]

    web_port = None
    for port in range(8930, 8940):
        try:
            from http.server import ThreadingHTTPServer as T
            srv = T(('127.0.0.1', port), webui.Handler)
            web_port = port
            break
        except OSError:
            continue
    if not web_port:
        print("无法绑定端口"); return 1
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{web_port}"

    out = os.path.join(ROOT, "tests", ".tmp", f"web_{uuid.uuid4().hex[:8]}")
    os.makedirs(out, exist_ok=True)
    webui.OUT_DIR = out

    try:
        print("\n[1] 页面可访问")
        with urllib.request.urlopen(base + "/", timeout=10) as r:
            page = r.read().decode()
        check("首页 200", True)
        check("主按钮存在", 'id="go"' in page)
        check("高级选项可折叠", "<details>" in page)
        check("无内部黑话", "战术跳过" not in page and "阵亡" not in page)
        check("有粘贴按钮", 'id="paste"' in page, "缺少粘贴按钮")
        check("有保存位置输入框", 'id="outdir"' in page, "缺少保存位置")
        check("有打开文件夹按钮", 'id="opendir"' in page, "缺少打开按钮")
        check("粘贴按钮有降级方案", "execCommand" in page and "Ctrl+V" in page)

        print("\n[2] 首次运行状态")
        st = api(base, "/api/status")
        check("初始未运行", st["running"] is False)
        check("初始进度为 0", st["done"] == 0 and st["total"] == 0)
        check("初始有提示文案", bool(st["message"]))

        print("\n[3] 启动下载（模拟界面默认 4 并发）")
        r = api(base, "/api/start", {"url": book_url, "from": None, "to": None,
                                     "driver": "requests", "delay": 0,
                                     "workers": 4})
        check("start 返回 ok", r.get("ok") is True, str(r))

        # 轮询到结束
        deadline = time.time() + 40
        last = None
        while time.time() < deadline:
            last = api(base, "/api/status")
            if not last["running"] and last["result_txt"]:
                break
            time.sleep(0.3)

        check("任务已结束", last["running"] is False, str(last["running"]))
        check("总章数正确", last["total"] == N, str(last["total"]))
        check("完成章数正确", last["done"] == N, str(last["done"]))

        print("\n[4] 完成状态对界面友好")
        check("状态文案提到完成", "完成" in last["message"], last["message"])
        check("提供了 TXT 下载", bool(last["result_txt"]))

        print("\n[5] 日志已转译成人话")
        lines = last["lines"]
        check("日志非空", len(lines) > 0, str(len(lines)))
        joined = "\n".join(lines)
        check("日志里没有「战术跳过」", "战术跳过" not in joined)
        check("日志里没有「阵亡」", "阵亡" not in joined)
        check("日志里没有「断点续传中」", "断点续传中" not in joined)

        print("\n[6] TXT 可下载")
        with urllib.request.urlopen(base + "/api/txt", timeout=10) as r:
            txt = r.read().decode()
        check("TXT 含全部章节", txt.count("=== 第") == N, str(txt.count("=== 第")))

        print("\n[7] 重复运行 = 增量（不重复下载）")
        api(base, "/api/start", {"url": book_url, "driver": "requests",
                                 "delay": 0, "workers": 4})
        deadline = time.time() + 30
        while time.time() < deadline:
            last2 = api(base, "/api/status")
            if not last2["running"]:
                break
            time.sleep(0.3)
        check("第二次仍能完成", not last2["running"])
        check("第二次没有新增下载（全部已存在）",
              last2["done"] == N, str(last2["done"]))

        print("\n[8] 错误网址给出人话提示")
        r = api(base, "/api/start", {"url": "ftp://bad", "driver": "requests"})
        check("非 http 网址被拒", r.get("ok") is False, str(r))
        check("错误提示可读", "http" in (r.get("error") or ""), str(r.get("error")))

        print("\n[9] 自定义保存位置")
        custom = os.path.join(out, "我的小说存放处")
        r = api(base, "/api/start", {"url": book_url2, "driver": "requests",
                                     "delay": 0, "workers": 2,
                                     "outdir": custom})
        check("start 接受 outdir", r.get("ok") is True, str(r))
        check("返回了绝对路径", os.path.isabs(r.get("outdir") or ""), str(r.get("outdir")))

        deadline = time.time() + 40
        last3 = None
        while time.time() < deadline:
            last3 = api(base, "/api/status")
            if not last3["running"] and last3["result_txt"]:
                break
            time.sleep(0.3)
        check("文件落在自定义目录", (last3.get("result_txt") or "").startswith(custom),
              str(last3.get("result_txt")))
        check("自定义目录确实有文件",
              any(f.endswith(".txt") for _, _, fs in os.walk(custom) for f in fs),
              "目录里没有 txt")

        print("\n[10] 保存位置：相对路径与留空")
        r = api(base, "/api/start", {"url": book_url, "driver": "requests",
                                     "outdir": ""})
        check("留空回落到默认目录",
              os.path.abspath(r.get("outdir") or "") == os.path.abspath(webui.OUT_DIR),
              str(r.get("outdir")))

        print("\n[11] 打开文件夹接口")
        # 只验证接口存在且返回 JSON，不真的弹资源管理器
        try:
            req = urllib.request.Request(base + "/api/opendir", data=b"",
                                         method="POST")
            with urllib.request.urlopen(req, timeout=5) as rr:
                j = json.loads(rr.read().decode())
            check("opendir 返回 JSON", "ok" in j, str(j))
        except Exception as e:
            check("opendir 返回 JSON", False, repr(e))
    finally:
        srv.shutdown()
        site.shutdown()
        shutil.rmtree(out, ignore_errors=True)

    print(f"\n{'='*46}\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for n in FAIL:
            print("  ❌ " + n)
        return 1
    print("✅ 网页界面全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
