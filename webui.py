"""novel-dl Web 控制台 —— 双击 bat 自动打开，在网页上操作下载。

用法：
    python webui.py              # 启动并自动打开浏览器
    python webui.py --no-open    # 只启动不打开浏览器
    python webui.py --port 8766

零额外依赖：标准库 http.server + 原生 fetch。
下载任务在后台线程执行，页面轮询 /api/status 实时显示进度；
关掉网页不影响下载，关掉本进程则停止。

安全边界：只监听 127.0.0.1，没有鉴权，也没有上传/执行任意代码的接口。
"""
import argparse
import json
import logging
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from novel_dl import __version__, log as nlog, rules
from novel_dl.engine import Engine

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "books")

# ---------------------------------------------------------------------------
# 全局单任务状态（自用工具，一次跑一本书足够；状态即日志）
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
    "failed": [],         # [[章节号, 标题, 原因]]
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
            _push_line(record.getMessage())
        except Exception:
            pass


def log(msg):
    _push_line(msg)


def run_task(url, from_ch, to_ch, driver, delay, backup_url=None):
    stop_event = threading.Event()
    pause_event = threading.Event()
    STATE["stop_event"] = stop_event
    STATE["pause_event"] = pause_event

    def worker():
        with STATE_LOCK:
            STATE.update(running=True, paused=False, book_url=url,
                         backup_url=backup_url, failed=[],
                         result_txt=None, started_at=time.time(),
                         finished_at=None, done=0, total=0,
                         message="正在解析目录…")
        try:
            eng = Engine(url, driver=driver, out_dir=OUT_DIR,
                         from_ch=from_ch, to_ch=to_ch, min_delay=delay,
                         backup_url=backup_url,
                         stop_event=stop_event, pause_event=pause_event)

            prog = eng.prog
            orig_done, orig_fail = prog.mark_done, prog.mark_failed

            def md(idx):
                orig_done(idx)
                with STATE_LOCK:
                    STATE["done"] = len(prog.state["done"])
                    STATE["total"] = max(STATE["total"], len(prog.state["toc"]))

            def mf(idx, reason):
                orig_fail(idx, reason)
                with STATE_LOCK:
                    toc = prog.state.get("toc", [])
                    title = toc[idx][0] if idx < len(toc) else "?"
                    STATE["failed"].append([idx + 1, title, str(reason)[:120]])

            prog.mark_done, prog.mark_failed = md, mf

            with STATE_LOCK:
                STATE["total"] = len(prog.state.get("toc", [])) or STATE["total"]
                STATE["done"] = len(prog.state["done"])

            txt = eng.run()
            with STATE_LOCK:
                STATE["result_txt"] = txt
                STATE["message"] = "完成 ✔"
        except (SystemExit, KeyboardInterrupt) as e:
            log(f"任务已停止：{e}")
            with STATE_LOCK:
                STATE["message"] = "已停止（进度已保存，可重跑续传）"
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
                STATE["message"] = f"失败：{e}"
        finally:
            with STATE_LOCK:
                STATE["running"] = False
                STATE["paused"] = False
                STATE["finished_at"] = time.time()

    threading.Thread(target=worker, daemon=True).start()


PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>novel-dl 控制台</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{--bg:#0f1420;--card:#1a2233;--fg:#e8ecf4;--dim:#8a94a8;--acc:#4f8cff;--ok:#3fb96f;--bad:#e06060}
  *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
    font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif;padding:24px}
  .wrap{max-width:860px;margin:0 auto}
  h1{font-size:20px;margin:0 0 4px}h1 small{color:var(--dim);font-weight:400;font-size:13px}
  .card{background:var(--card);border-radius:12px;padding:18px 20px;margin:14px 0}
  label{font-size:13px;color:var(--dim)}
  input,select{width:100%;padding:10px 12px;margin:4px 0 12px;border-radius:8px;
    border:1px solid #2c3752;background:#0f1420;color:var(--fg);font-size:14px}
  .row{display:flex;gap:12px}.row>div{flex:1}
  button{background:var(--acc);border:0;color:#fff;padding:11px 26px;border-radius:8px;
    font-size:15px;cursor:pointer;width:100%}
  button:disabled{opacity:.45;cursor:default}
  .bar{height:10px;background:#0f1420;border-radius:6px;overflow:hidden;margin:10px 0 6px}
  .bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--acc),#7fb0ff);
    width:0;transition:width .4s}
  .stat{display:flex;justify-content:space-between;color:var(--dim);font-size:13px}
  .stat b{color:var(--fg)}
  #console{background:#0b0f18;border-radius:8px;padding:12px;height:260px;overflow-y:auto;
    font:12px/1.7 Consolas,monospace;white-space:pre-wrap;color:#9fb3d1;margin-top:10px}
  .failed{margin-top:8px;font-size:13px;color:var(--bad)}
  .ok{color:var(--ok)}a.dl{color:var(--ok);text-decoration:none;font-weight:600}
  .tip{font-size:12.5px;color:var(--dim);margin:8px 0 0}
  #rules{font-size:12.5px;color:var(--dim);margin-top:6px;word-break:break-all}
</style></head><body><div class="wrap">
<h1>📚 novel-dl 控制台 <small id="ver">断点续传 · 增量更新 · 战术跳过</small></h1>

<div class="card">
  <label>小说目录页网址</label>
  <input id="url" placeholder="https://example.com/book/123/" value="">
  <label>备用网址（可选，镜像站）—— 主站某章抓取失败时自动改用它</label>
  <input id="backup" placeholder="留空则不使用" value="">
  <div class="row">
    <div><label>起始章（留空=从头）</label><input id="f" type="number" min="1" placeholder="如 100"></div>
    <div><label>结束章（留空=到最后）</label><input id="t" type="number" min="1" placeholder="如 200"></div>
    <div><label>抓取驱动</label><select id="d">
      <option value="requests">requests（静态站）</option>
      <option value="selenium">selenium（动态站）</option></select></div>
    <div><label>每章间隔秒</label><input id="delay" type="number" min="0" step="0.5" value="0.5"></div>
  </div>
  <button id="go">开始下载</button>
  <div style="display:flex;gap:10px;margin-top:10px">
    <button id="pause" style="background:#c8971f">⏸ 暂停</button>
    <button id="stop" style="background:#c04040">⏹ 停止</button>
  </div>
  <p class="tip">💡 中途断了？重新填同一网址再点一次就会自动续传，已下载章节不会重复请求。
     「暂停」在当前章结束后停下，不丢进度；「停止」同样保留进度。</p>
  <div id="rules"></div>
</div>

<div class="card">
  <div class="stat"><span>状态：<b id="msg">加载中…</b></span>
    <span><b id="prog">0 / 0</b> 章</span></div>
  <div class="bar"><i id="fill"></i></div>
  <div class="stat"><span id="failinfo"></span><span id="txtinfo"></span></div>
  <div id="console"></div>
</div>
</div>

<script>
const $=id=>document.getElementById(id);
let busy=false;
async function tick(){
  try{
    const s=await(await fetch('/api/status')).json();
    $('ver').textContent='v'+s.version+' · 断点续传 · 增量更新 · 战术跳过';
    $('msg').textContent=s.message;
    $('prog').textContent=`${s.done} / ${s.total}`;
    $('fill').style.width=(s.total?100*s.done/s.total:0)+'%';
    $('failinfo').textContent=s.failed.length?`☠ 阵亡 ${s.failed.length} 章：`+
      s.failed.slice(0,5).map(f=>'第'+f[0]+'章 '+f[1]).join('；')+
      (s.failed.length>5?' …':''):'';
    $('txtinfo').innerHTML=s.result_txt?`<a class="dl" href="/api/txt">📦 下载 TXT</a>`:'';
    const c=$('console');
    if(c.textContent!==s.lines.join('\\n')){c.textContent=s.lines.join('\\n');c.scrollTop=1e9;}
    busy=s.running;
    $('go').disabled=busy;
    $('go').textContent=busy?'下载中…（关页面不中断）':'开始下载';
    $('pause').disabled=!busy;
    $('pause').textContent=s.paused?'▶ 继续':'⏸ 暂停';
    $('stop').disabled=!busy;
    if(!$('rules').dataset.done){
      $('rules').dataset.done='1';
      const r=await(await fetch('/api/rules')).json();
      $('rules').textContent = r.count
        ? `📑 已加载 ${r.count} 条站点规则：` + r.sources.map(s=>s[0]+' ('+s[1]+')').join('，')
        : '📑 未加载任何站点规则 —— 将使用通用启发式解析。规则写法见 docs/RULES.md';
    }
  }catch(e){$('msg').textContent='无法连接本地服务（进程可能已退出）';}
}
setInterval(tick,1200);tick();

$('go').onclick=async()=>{
  const url=$('url').value.trim();
  if(!url){$('url').focus();return;}
  const body={url,
    backup:$('backup').value.trim(),
    from:$('f').value?parseInt($('f').value):null,
    to:$('t').value?parseInt($('t').value):null,
    driver:$('d').value,
    delay:$('delay').value?parseFloat($('delay').value):0};
  const r=await(await fetch('/api/start',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  if(!r.ok)alert(r.error);
  tick();
};
document.addEventListener('keydown',e=>{if(e.key==='Enter'&&!busy)$('go').click();});
$('pause').onclick=async()=>{
  await fetch(!busy?'/api/resume':($('pause').textContent.includes('继续')?'/api/resume':'/api/pause'),{method:'POST'});
  tick();
};
$('stop').onclick=async()=>{
  if(!confirm('确定停止下载？已下载的章节会保留，之后重跑可续传。'))return;
  await fetch('/api/stop',{method:'POST'});
  tick();
};
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        if self.path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/status":
            with STATE_LOCK:
                snap = {k: v for k, v in STATE.items()
                        if not isinstance(v, threading.Event)}
            self._json(200, snap)
        elif self.path == "/api/rules":
            rs = rules.load_rules()
            self._json(200, {"count": len(rs), "sources": rules.rule_sources()})
        elif self.path == "/api/txt":
            p = STATE.get("result_txt")
            if p and os.path.exists(p):
                with open(p, "rb") as f:
                    self._send(200, f.read(), "text/plain; charset=utf-8")
            else:
                self._send(404, "TXT 未生成".encode(), "text/plain; charset=utf-8")
        else:
            self._send(404, b"404", "text/plain")

    def do_POST(self):
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
            if STATE["running"]:
                return self._json(409, {"ok": False, "error": "已有任务在跑，等它结束再开新的"})
        run_task(url, req.get("from"), req.get("to"),
                 req.get("driver") or "requests", req.get("delay") or 0,
                 backup_url=(req.get("backup") or "").strip() or None)
        self._json(200, {"ok": True})

    def log_message(self, *a):   # 静音 HTTP 访问日志
        pass


def main():
    ap = argparse.ArgumentParser(description="novel-dl 本地网页控制台")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    # 日志双写：控制台 + logs/webui.log（闪退也有据可查）
    nlog.setup(logging.INFO)
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    fh = logging.FileHandler(os.path.join(log_dir, "webui.log"), encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    nlog.log.addHandler(fh)
    nlog.log.addHandler(_StateHandler())

    # 端口被占用时自动换下一个（之前闪退的元凶就是端口冲突）
    srv = None
    for port in range(a.port, a.port + 10):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            nlog.warning("端口 %d 已被占用，尝试下一个…", port)
    if srv is None:
        nlog.error("%d~%d 端口全部被占用，退出", a.port, a.port + 9)
        input("按回车键关闭...")
        sys.exit(1)

    url = f"http://127.0.0.1:{srv.server_address[1]}"
    nlog.info("novel-dl 控制台运行中: %s  (关掉本窗口即停止服务)", url)
    n_rules = len(rules.load_rules())
    nlog.info("站点规则：%d 条%s", n_rules,
              "" if n_rules else "（未加载规则，将使用通用启发式解析）")
    if not a.no_open:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        nlog.info("已退出（下载中断；缓存章节保留，下次继续）")


if __name__ == "__main__":
    main()
