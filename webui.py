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
            _push_line(_humanize(record.getMessage()))
        except Exception:
            pass


# 内部术语 → 用户看得懂的说法。
# 日志本身保留原文（排查问题要用），只在送到界面时翻译一遍。
_TRANSLATE = [
    ("战术跳过", "跳过"),
    ("阵亡名单", "没下载成功的章节"),
    ("阵亡", "失败"),
    ("断点续传中，已完成章节不会重复下载", "已有进度，只下剩下的"),
    ("发现历史进度", "发现之前的进度"),
    ("检测到更新：", "网站更新了，"),
    ("本轮新下载", "本次下载"),
    ("已按照站点规则纠正网址", "已自动修正网址"),
    ("检测到章节页网址，已改用目录页", "这是章节页，已自动换成目录页"),
    ("已合并", "已保存"),
    ("战术性跳过", "跳过"),
    ("⚡ 并发抓取", "同时下载"),
    ("🔁", "重新尝试"),
    ("📖", ""), ("📚", ""), ("📦", ""), ("🔧", ""), ("↪", "→"),
    ("☠", "×"), ("✓", "√"), ("⛔", "!"), ("⚠", "!"), ("⏸", ""),
    ("▶", ""), ("⏹", ""), ("ℹ", ""), ("🆕", ""),
]


def _humanize(msg: str) -> str:
    for a, b in _TRANSLATE:
        msg = msg.replace(a, b)
    # 去掉因替换产生的多余空格
    return " ".join(msg.split()) if len(msg) < 120 else msg


def log(msg):
    _push_line(msg)


def run_task(url, from_ch, to_ch, driver, delay, backup_url=None, workers=1):
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
                         message="正在读取章节列表…")
        try:
            eng = Engine(url, driver=driver, out_dir=OUT_DIR,
                         from_ch=from_ch, to_ch=to_ch, min_delay=delay,
                         backup_url=backup_url,
                         stop_event=stop_event, pause_event=pause_event,
                         workers=workers)

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
                title = eng.prog.state.get("title") or "小说"
                STATE["message"] = f"下载完成，共 {STATE['done']} 章 —— {title}"
        except (SystemExit, KeyboardInterrupt) as e:
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


PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>小说下载</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{--bg:#0f1420;--card:#1a2233;--fg:#e8ecf4;--dim:#8a94a8;--acc:#4f8cff;
        --ok:#3fb96f;--bad:#e06060;--line:#2c3752}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
    font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif;padding:28px 16px}
  .wrap{max-width:640px;margin:0 auto}
  h1{font-size:22px;margin:0 0 20px;font-weight:600}
  .card{background:var(--card);border-radius:14px;padding:22px;margin:0 0 14px}
  label{display:block;font-size:13px;color:var(--dim);margin-bottom:6px}
  input,select{width:100%;padding:12px 14px;border-radius:9px;
    border:1px solid var(--line);background:#0f1420;color:var(--fg);
    font-size:15px;font-family:inherit}
  input:focus,select:focus{outline:none;border-color:var(--acc)}
  #go{background:var(--acc);border:0;color:#fff;padding:14px;border-radius:9px;
    font-size:16px;font-weight:600;cursor:pointer;width:100%;margin-top:16px;
    font-family:inherit}
  #go:disabled{opacity:.45;cursor:default}
  .ctrl{display:none;gap:10px;margin-top:10px}
  .ctrl.on{display:flex}
  .ctrl button{flex:1;border:0;color:#fff;padding:12px;border-radius:9px;
    font-size:15px;cursor:pointer;font-family:inherit}
  .ctrl button:disabled{opacity:.4;cursor:default}
  #pause{background:#c8971f}#stop{background:#c04040}
  details{margin-top:16px}
  summary{cursor:pointer;color:var(--dim);font-size:13.5px;
    list-style:none;user-select:none}
  summary::-webkit-details-marker{display:none}
  summary::before{content:"▸ ";color:var(--dim)}
  details[open] summary::before{content:"▾ "}
  .adv{margin-top:14px;display:grid;gap:12px;
    grid-template-columns:1fr 1fr}
  .adv .full{grid-column:1/-1}
  .adv input,.adv select{padding:10px 12px;font-size:14px}
  .adv label{margin-bottom:4px;font-size:12.5px}
  .hint{font-size:12px;color:var(--dim);margin:4px 0 0}
  /* 手机：高级选项单列，避免挤成两半 */
  @media (max-width:520px){
    body{padding:18px 12px}
    .card{padding:18px}
    .adv{grid-template-columns:1fr}
  }
  /* 进度：没开始时整块不显示，别让空进度条占地方 */
  #progress{display:none}
  #progress.on{display:block}
  #status{font-size:15px;margin-bottom:14px;min-height:24px}
  .bar{height:8px;background:#0b0f18;border-radius:5px;overflow:hidden}
  .bar>i{display:block;height:100%;background:var(--acc);
    width:0;transition:width .35s;border-radius:5px}
  .meta{display:flex;justify-content:space-between;margin-top:10px;
    font-size:13px;color:var(--dim)}
  .meta b{color:var(--fg);font-variant-numeric:tabular-nums}
  #done{display:none;margin-top:18px;text-align:center}
  #done.on{display:block}
  #done a{display:inline-block;background:var(--ok);color:#fff;text-decoration:none;
    padding:12px 28px;border-radius:9px;font-weight:600}
  #warn{display:none;margin-top:12px;font-size:13px;color:#e0a94f;
    background:rgba(224,169,79,.1);padding:10px 12px;border-radius:8px}
  #warn.on{display:block}
  #log{margin-top:16px}
  #logbox{background:#0b0f18;border-radius:8px;padding:12px;height:200px;
    overflow-y:auto;font:12px/1.7 Consolas,monospace;white-space:pre-wrap;
    color:#8fa6c4;margin-top:10px}
</style></head><body><div class="wrap">
<h1>小说下载</h1>

<div class="card">
  <label for="url">小说目录页网址</label>
  <input id="url" placeholder="把网址粘贴到这里" autocomplete="off" spellcheck="false">
  <button id="go">开始下载</button>
  <div class="ctrl" id="ctrl">
    <button id="pause">暂停</button>
    <button id="stop">停止</button>
  </div>

  <details>
    <summary>高级选项</summary>
    <div class="adv">
      <div class="full">
        <label for="backup">备用网址（可选）</label>
        <input id="backup" placeholder="主网址打不开时可以填镜像站" autocomplete="off">
      </div>
      <div>
        <label for="f">从第几章开始</label>
        <input id="f" type="number" min="1" placeholder="从头">
      </div>
      <div>
        <label for="t">到第几章结束</label>
        <input id="t" type="number" min="1" placeholder="到最后">
      </div>
      <div>
        <label for="workers">同时下载几章</label>
        <input id="workers" type="number" min="1" max="16" step="1" value="4">
      </div>
      <div>
        <label for="delay">每章间隔（秒）</label>
        <input id="delay" type="number" min="0" step="0.5" value="0.5">
      </div>
      <div class="full">
        <label for="d">网站类型</label>
        <select id="d">
          <option value="requests">普通网站（推荐）</option>
          <option value="selenium">需要浏览器渲染的网站</option>
        </select>
      </div>
    </div>
    <p class="hint" id="rulehint"></p>
  </details>
</div>

<div class="card">
  <div id="status">等待开始</div>
  <div id="progress">
    <div class="bar"><i id="fill"></i></div>
    <div class="meta">
      <span><b id="prog">0</b> / <b id="total">0</b> 章</span>
      <span id="pct">0%</span>
    </div>
    <div id="warn"></div>
  </div>
  <div id="done"><a id="dl" href="/api/txt">下载 TXT 文件</a></div>
  <details id="log">
    <summary>运行日志</summary>
    <div id="logbox"></div>
  </details>
</div>
</div>

<script>
const $=id=>document.getElementById(id);
let busy=false, hadResult=false;

function setStatus(s){ if($('status').textContent!==s) $('status').textContent=s; }

async function tick(){
  try{
    const s=await(await fetch('/api/status')).json();
    setStatus(s.message);
    $('prog').textContent=s.done;
    $('total').textContent=s.total;
    const p=s.total?Math.round(100*s.done/s.total):0;
    $('pct').textContent=p+'%';
    $('fill').style.width=p+'%';
    // 有进度或有结果时才显示进度区，避免空进度条占地方
    $('progress').classList.toggle('on', s.running||s.total>0);

    // 失败章节：用日常说法，只在真有时出现
    const w=$('warn');
    if(s.failed.length){
      w.classList.add('on');
      w.textContent='有 '+s.failed.length+' 章没下载成功。可以再点一次「开始下载」，'
        +'只补这些章节。';
    }else{
      w.classList.remove('on');
    }

    // 完成后的下载按钮
    const d=$('done');
    if(s.result_txt){ d.classList.add('on'); }
    else if(!s.running){ d.classList.remove('on'); }

    const c=$('logbox');
    const txt=s.lines.join('\\n');
    if(c.textContent!==txt){ c.textContent=txt; c.scrollTop=1e9; }

    busy=s.running;
    $('go').disabled=busy;
    $('go').textContent=busy?'正在下载…':'开始下载';
    $('ctrl').classList.toggle('on',busy);
    $('pause').disabled=!busy;
    $('pause').textContent=s.paused?'继续':'暂停';
    $('stop').disabled=!busy;
  }catch(e){
    setStatus('连接不上本地服务，请确认那个黑色窗口还开着');
  }
}
setInterval(tick,1000);tick();

// 规则提示：只在高级选项展开时才有意义，说人话
(async()=>{
  try{
    const r=await(await fetch('/api/rules')).json();
    $('rulehint').textContent = r.count
      ? '已加载 '+r.count+' 条网站解析规则。'
      : '没有加载网站规则，程序会自动识别网页结构。';
  }catch(e){}
})();

$('go').onclick=async()=>{
  const url=$('url').value.trim();
  if(!url){ $('url').focus(); return; }
  if(!/^https?:\\/\\//i.test(url)){
    alert('网址要以 http:// 或 https:// 开头');
    return;
  }
  $('done').classList.remove('on');
  const body={
    url,
    backup:$('backup').value.trim(),
    from:$('f').value?parseInt($('f').value):null,
    to:$('t').value?parseInt($('t').value):null,
    driver:$('d').value,
    delay:$('delay').value?parseFloat($('delay').value):0,
    workers:$('workers').value?parseInt($('workers').value):1
  };
  const r=await(await fetch('/api/start',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  if(!r.ok) alert(r.error);
  tick();
};

$('url').addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!busy) $('go').click();
});

$('pause').onclick=async()=>{
  const toResume=$('pause').textContent==='继续';
  await fetch(toResume?'/api/resume':'/api/pause',{method:'POST'});
  tick();
};

$('stop').onclick=async()=>{
  if(!confirm('停止下载？已经下好的章节会保留，下次点开始会接着下。')) return;
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
                 backup_url=(req.get("backup") or "").strip() or None,
                 workers=int(req.get("workers") or 1))
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
