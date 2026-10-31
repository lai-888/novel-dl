"""novel-dl Web 控制台 —— 双击 bat 自动打开，在网页上操作下载。"""
import argparse
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from novel_dl import __version__, log as nlog, rules
from novel_dl import library
from novel_dl.engine import Engine

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "books")

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
STATE_LOCK = threading.Lock()
STATE = {
    "running": False,
    "paused": False,
    "book_url": None,
    "backup_url": None,
    "message": "待命中",
    "lines": [],          # 控制台输出（最近 400 行）
    "done": 0, "total": 0,
    "failed": [],
    "result_txt": None,
    "started_at": None,
    "finished_at": None,
    "version": __version__,
}


def _push_line(text: str):
    with STATE_LOCK:
        STATE["lines"].append(f"[{time.strftime('%H:%M:%S')}] {text}")
        STATE["lines"] = STATE["lines"][-400:]


class _StateHandler(logging.Handler):
    """把库里的 logging 记录喂给网页控制台（不再劫持 sys.stdout）。"""

    def emit(self, record):
        try:
            _push_line(_humanize(record.getMessage()))
        except Exception:
            pass

_TRANSLATE = [
    ("阵亡名单", "没下载成功的章节"),
    ("阵亡", "失败"),
    ("断点续传中，已完成章节不会重复下载", "已有进度，只下剩下的"),
    ("发现历史进度", "发现之前的进度"),
    ("检测到更新：", "网站更新了，"),
    ("本轮新下载", "本次下载"),
    ("已按照站点规则纠正网址", "已自动修正网址"),
    ("检测到章节页网址，已改用目录页", "这是章节页，已自动换成目录页"),
    ("已合并", "已保存"),
    ("⚡ 并发抓取", "同时下载"),
    ("🔁", "重新尝试"),
    ("📖", ""), ("📚", ""), ("📦", ""), ("🔧", ""), ("↪", "→"),
    ("☠", "×"), ("✓", "√"), ("⛔", "!"), ("⚠", "!"), ("⏸", ""),
    ("▶", ""), ("⏹", ""), ("ℹ", ""), ("🆕", ""),
]


def _humanize(msg: str) -> str:
    for a, b in _TRANSLATE:
        msg = msg.replace(a, b)
    return " ".join(msg.split()) if len(msg) < 120 else msg


def log(msg):
    _push_line(msg)


def resolve_out_dir(path: str | None) -> str:
    """把用户填的保存位置变成绝对路径。"""
    if not path or not str(path).strip():
        return OUT_DIR
    p = os.path.expanduser(str(path).strip().strip('"').strip("'"))
    if not os.path.isabs(p):
        p = os.path.abspath(p)
    return p


def run_task(url, from_ch, to_ch, driver, delay, backup_url=None, workers=1,
             out_dir=None, cookie=None):
    target_dir = resolve_out_dir(out_dir)

    with STATE_LOCK:                      # 占位和检查在同一把锁里，连点两次也只起一个
        if STATE["running"]:
            return False
        STATE["running"] = True

    stop_event = threading.Event()
    pause_event = threading.Event()
    STATE["stop_event"] = stop_event
    STATE["pause_event"] = pause_event

    def worker():
        with STATE_LOCK:
            STATE.update(paused=False, book_url=url,
                         backup_url=backup_url, failed=[],
                         result_txt=None, started_at=time.time(),
                         finished_at=None, done=0, total=0,
                         title=None, skipped=0, out_dir=target_dir,
                         message="正在读取章节列表…")

        def on_toc(title, total, already_done, to_fetch):
            """目录确定后立刻上报总数 —— 别等 mark_done，否则可能永远是 0。"""
            with STATE_LOCK:
                STATE["title"] = title
                STATE["total"] = total
                STATE["done"] = already_done
                STATE["skipped"] = already_done
                if to_fetch == 0:
                    STATE["message"] = f"《{title}》共 {total} 章，已全部下载过，无需再下"
                elif already_done:
                    STATE["message"] = (f"《{title}》共 {total} 章，"
                                        f"已有 {already_done} 章，本次补 {to_fetch} 章")
                else:
                    STATE["message"] = f"《{title}》共 {total} 章，开始下载"

        eng = None
        try:
            try:
                eng = Engine(url, driver=driver, out_dir=target_dir,
                             from_ch=from_ch, to_ch=to_ch, min_delay=delay,
                             backup_url=backup_url,
                             stop_event=stop_event, pause_event=pause_event,
                             workers=workers, cookie=cookie, on_toc=on_toc)
            except SystemExit as e:      # 驱动没装、驱动名写错之类
                log(f"启动失败：{e}")
                with STATE_LOCK:
                    STATE["message"] = f"启动失败：{str(e)[:120]}"
                return

            prog = eng.prog
            orig_done, orig_fail = prog.mark_done, prog.mark_failed

            def md(idx):
                orig_done(idx)
                with STATE_LOCK:
                    STATE["done"] = len(prog.state["done"])

            def mf(idx, reason):
                orig_fail(idx, reason)
                with STATE_LOCK:
                    toc = prog.state.get("toc", [])
                    title = toc[idx][0] if idx < len(toc) else "?"
                    STATE["failed"].append([idx + 1, title, str(reason)[:120]])

            prog.mark_done, prog.mark_failed = md, mf

            txt = eng.run()
            with STATE_LOCK:
                STATE["result_txt"] = txt
                title = STATE.get("title") or eng.prog.state.get("title") or "小说"
                total = STATE["total"] or len(eng.prog.state.get("toc", []))
                done = len(eng.prog.state["done"])
                STATE["done"] = done
                STATE["total"] = total
                if total:
                    STATE["message"] = f"下载完成：《{title}》共 {total} 章"
                else:
                    STATE["message"] = f"下载完成：《{title}》"
                prev_n = len(eng.prog.state.get("preview") or {})
                if prev_n:
                    STATE["message"] += f"（其中 {prev_n} 章只有付费预览）"
        except (SystemExit, KeyboardInterrupt) as e:
            blocked = getattr(eng, "blocked", None) if eng is not None else None
            if blocked is not None:
                from novel_dl.guard import explain
                for line in explain(blocked).splitlines():
                    log(line)
                with STATE_LOCK:
                    STATE["message"] = "这个网站下载不了：正文被字体加密（网页能看，程序取不到文字）"
                    STATE["result_txt"] = None
            else:
                log(f"任务已停止：{e}")
                with STATE_LOCK:
                    STATE["message"] = "已停止，进度已保存。再点一次「开始下载」会接着下。"
                try:
                    txt = eng.export(eng.prog.state.get("title") or "未命名",
                                     eng.prog.state.get("toc") or [])
                    with STATE_LOCK:
                        STATE["result_txt"] = txt
                except Exception:
                    pass
        except Exception as e:
            log(f"任务失败：{e}")
            with STATE_LOCK:
                msg = str(e)
                if "目录页解析不到" in msg:
                    STATE["message"] = "这个网址不是小节列表页。请打开小说的「目录」页面，复制那一页的网址。"
                elif "http" in msg.lower() or "connection" in msg.lower():
                    STATE["message"] = "连不上这个网站。检查网址是否正确、网络是否正常。"
                else:
                    STATE["message"] = f"出错了：{msg[:80]}"
        finally:
            with STATE_LOCK:
                STATE["running"] = False
                STATE["paused"] = False
                STATE["finished_at"] = time.time()

    threading.Thread(target=worker, daemon=True).start()
    return True

from novel_dl.ui_page import PAGE


class Handler(BaseHTTPRequestHandler):
    def _host_ok(self) -> bool:
        """只接受本机地址：挡住 DNS 重绑定和任意网页发来的跨站请求。"""
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return False
        name = host.split(":")[0].strip("[]")
        return name in ("127.0.0.1", "localhost", "::1")

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _query(self):
        """把 query string 解析成 dict。"""
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(self.path).query)
        return {k: v[0] for k, v in q.items() if v}

    def do_GET(self):
        from urllib.parse import urlparse
        route = urlparse(self.path).path
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")

        if route == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")

        elif route == "/api/status":
            with STATE_LOCK:
                snap = {k: v for k, v in STATE.items()
                        if not isinstance(v, threading.Event)}
            self._json(200, snap)

        elif route == "/api/rules":
            rs = rules.load_rules()
            self._json(200, {"count": len(rs), "sources": rules.rule_sources()})

        elif route == "/api/books":
            with STATE_LOCK:
                root = STATE.get("out_dir") or OUT_DIR
            try:
                books = library.scan(root)
            except Exception as e:
                return self._json(500, {"books": [], "error": str(e)})
            self._json(200, {"books": books, "root": root})

        elif route == "/api/chapter":
            q = self._query()
            slug = q.get("slug", "")
            try:
                no = int(q.get("no", "0"))
            except ValueError:
                no = 0
            with STATE_LOCK:
                root = STATE.get("out_dir") or OUT_DIR
            try:
                books = library.scan(root)
            except Exception as e:
                return self._json(500, {"error": str(e)})
            book = next((b for b in books if b["slug"] == slug), None)
            if not book:
                return self._json(404, {"error": "找不到这本书"})
            data = library.read_chapter(book, no)
            if not data:
                return self._json(404, {"error": "找不到这一章"})
            self._json(200, data)

        elif route == "/api/book-file":
            q = self._query()
            slug = q.get("slug", "")
            with STATE_LOCK:
                root = STATE.get("out_dir") or OUT_DIR
            try:
                books = library.scan(root)
            except Exception:
                books = []
            book = next((b for b in books if b["slug"] == slug), None)
            if not book:
                self._send(404, b"404", "text/plain")
                return
            txt = None
            for f in os.listdir(book["path"]):
                if f.endswith(".txt"):
                    txt = os.path.join(book["path"], f)
                    break
            if not txt or not os.path.exists(txt):
                self._send(404, "TXT 不存在".encode(), "text/plain; charset=utf-8")
                return
            with open(txt, "rb") as f:
                data = f.read()
            self._send(200, data, "text/plain; charset=utf-8")

        elif route == "/api/txt":
            p = STATE.get("result_txt")
            if p and os.path.exists(p):
                with open(p, "rb") as f:
                    self._send(200, f.read(), "text/plain; charset=utf-8")
            else:
                self._send(404, "TXT 未生成".encode(), "text/plain; charset=utf-8")
        else:
            self._send(404, b"404", "text/plain")

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        if self.path in ("/api/pause", "/api/resume", "/api/stop"):
            with STATE_LOCK:
                pe = STATE.get("pause_event")
                se = STATE.get("stop_event")
                if self.path == "/api/pause":
                    if pe is not None:
                        pe.set()
                    STATE["paused"] = True
                    STATE["message"] = "已暂停（当前章结束后停下）"
                elif self.path == "/api/resume":
                    if pe is not None:
                        pe.clear()
                    STATE["paused"] = False
                    STATE["message"] = "已继续"
                else:
                    if pe is not None:
                        pe.clear()
                    if se is not None:
                        se.set()
                    STATE["paused"] = False
                    STATE["message"] = "正在停止…"
            return self._json(200, {"ok": True})

        if self.path == "/api/opendir":
            req = {}
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length:
                    req = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                req = {}
            d = (req.get("path") or "").strip() if isinstance(req, dict) else ""
            if not d:
                with STATE_LOCK:
                    d = STATE.get("out_dir") or OUT_DIR
            with STATE_LOCK:
                root = os.path.abspath(STATE.get("out_dir") or OUT_DIR)
            target = os.path.abspath(d)
            if not (target == root or target.startswith(root + os.sep)):
                return self._json(200, {"ok": False, "error": "只能打开保存目录内的文件夹"})
            try:
                os.makedirs(target, exist_ok=True)
                if sys.platform.startswith("win"):
                    os.startfile(target)          # noqa: S606 打开本地文件夹
                elif sys.platform == "darwin":
                    import subprocess
                    subprocess.Popen(["open", target])
                else:
                    import subprocess
                    subprocess.Popen(["xdg-open", target])
                return self._json(200, {"ok": True, "path": target})
            except Exception as e:
                return self._json(200, {"ok": False, "error": f"打不开：{e}"})

        if self.path != "/api/start":
            return self._send(404, b"404", "text/plain")
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._json(400, {"ok": False, "error": "请求体不是合法 JSON"})

        url = (req.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return self._json(400, {"ok": False, "error": "网址需要以 http(s):// 开头"})
        with STATE_LOCK:
            busy = STATE["running"]
        if busy:
            return self._json(409, {"ok": False, "error": "已有任务在跑，等它结束再开新的"})
        try:
            outdir = resolve_out_dir(req.get("outdir"))
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"保存位置无效：{e}"})
        started = run_task(url, req.get("from"), req.get("to"),
                           req.get("driver") or "requests", req.get("delay") or 0,
                           backup_url=(req.get("backup") or "").strip() or None,
                           workers=int(req.get("workers") or 1),
                           out_dir=outdir,
                           cookie=(req.get("cookie") or "").strip() or None)
        if not started:
            return self._json(409, {"ok": False, "error": "已有任务在跑，等它结束再开新的"})
        self._json(200, {"ok": True, "outdir": outdir})

    def log_message(self, *a):   # 静音 HTTP 访问日志
        pass


def main():
    ap = argparse.ArgumentParser(description="novel-dl 本地网页控制台")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    nlog.setup(logging.INFO)
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "webui.log"), maxBytes=4 * 1024 * 1024,
        backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    nlog.log.addHandler(fh)
    nlog.log.addHandler(_StateHandler())

    bind = "127.0.0.1"
    srv = None
    for port in range(a.port, a.port + 10):
        try:
            srv = ThreadingHTTPServer((bind, port), Handler)
            break
        except OSError:
            nlog.warning("端口 %d 已被占用，尝试下一个…", port)
    if srv is None:
        nlog.error("%d~%d 端口全部被占用，退出", a.port, a.port + 9)
        input("按回车键关闭...")
        sys.exit(1)

    port = srv.server_address[1]
    self_url = f"http://127.0.0.1:{port}"
    nlog.info("已启动：%s", self_url)

    n_rules = len(rules.load_rules())
    nlog.info("站点规则：%d 条；未收录的站会自动分析网页结构", n_rules)

    if not a.no_open:
        threading.Timer(0.6, webbrowser.open, args=(self_url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        nlog.info("已退出（下载中断；缓存章节保留，下次继续）")

if __name__ == "__main__":
    main()
