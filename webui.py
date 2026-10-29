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


def resolve_out_dir(path: str | None) -> str:
    """把用户填的保存位置变成绝对路径。

    规则：
      · 留空 → 默认（项目下的 books/）
      · 相对路径 → 相对「桌面」解释会反直觉，所以一律相对当前工作目录
      · 支持 ~ 展开；末尾斜杠无所谓
    不校验是否存在 —— 不存在会自动创建，这是用户想要的方便。
    """
    if not path or not str(path).strip():
        return OUT_DIR
    p = os.path.expanduser(str(path).strip().strip('"').strip("'"))
    if not os.path.isabs(p):
        p = os.path.abspath(p)
    return p


def run_task(url, from_ch, to_ch, driver, delay, backup_url=None, workers=1,
             out_dir=None):
    stop_event = threading.Event()
    pause_event = threading.Event()
    STATE["stop_event"] = stop_event
    STATE["pause_event"] = pause_event
    target_dir = resolve_out_dir(out_dir)

    def worker():
        with STATE_LOCK:
            STATE.update(running=True, paused=False, book_url=url,
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

        try:
            eng = Engine(url, driver=driver, out_dir=target_dir,
                         from_ch=from_ch, to_ch=to_ch, min_delay=delay,
                         backup_url=backup_url,
                         stop_event=stop_event, pause_event=pause_event,
                         workers=workers, on_toc=on_toc)

            prog = eng.prog
            orig_done, orig_fail = prog.mark_done, prog.mark_failed

            def md(idx):
                orig_done(idx)
                with STATE_LOCK:
                    # total 由 on_toc 定下，这里只推进 done，避免两者互相覆盖
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
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#12100e">
<style>
  :root{
    --paper:#12100e; --raise:#1c1916; --ink:#f2ede6; --mute:#8f8579;
    --line:#2a2521; --amber:#d9a441; --amber-dim:#a8802f;
    --ok:#5aa06a; --warn:#c98a3c; --bad:#b85450;
    --r:3px;
  }
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  html,body{margin:0;background:var(--paper);color:var(--ink)}
  body{
    font:16px/1.55 "Inter Tight","PingFang SC","Microsoft YaHei",system-ui,sans-serif;
    padding:clamp(20px,5vw,56px) clamp(16px,4vw,32px) 64px;
    -webkit-font-smoothing:antialiased;
  }
  .app{max-width:680px;margin:0 auto}

  /* ── 头部：小而安静的标记，不用大标题 ── */
  .head{display:flex;align-items:baseline;gap:10px;margin-bottom:clamp(28px,6vw,48px)}
  .mark{font-size:13px;letter-spacing:.14em;text-transform:uppercase;
    color:var(--mute);font-weight:600}
  .head em{font-style:normal;color:var(--amber);font-size:13px;letter-spacing:.06em}

  /* ── 输入区 ── */
  .field{margin-bottom:14px}
  .lbl{display:block;font-size:12px;letter-spacing:.1em;text-transform:uppercase;
    color:var(--mute);margin-bottom:9px;font-weight:600}
  .bare{
    width:100%;padding:15px 0;background:none;border:none;
    border-bottom:1px solid var(--line);
    color:var(--ink);font:16px/1.4 inherit;font-family:inherit;
    transition:border-color .18s;
  }
  .bare::placeholder{color:#574f46}
  .bare:focus{outline:none;border-bottom-color:var(--amber)}
  .with-btn{display:flex;align-items:stretch;gap:0;border-bottom:1px solid var(--line);
    transition:border-color .18s}
  .with-btn:focus-within{border-bottom-color:var(--amber)}
  .with-btn .bare{flex:1;min-width:0;border-bottom:none;padding:15px 0}
  .mini{
    flex:0 0 auto;align-self:center;
    background:none;border:1px solid var(--line);color:var(--mute);
    padding:7px 14px;border-radius:var(--r);font:12.5px inherit;
    font-family:inherit;letter-spacing:.04em;cursor:pointer;
    transition:color .15s,border-color .15s,background .15s;
  }
  .mini:hover{color:var(--ink);border-color:#443c34}
  .mini:active{transform:translateY(1px)}

  /* ── 主按钮：实心单一强调色，足够大，移动端友好 ── */
  .go{
    display:block;width:100%;margin-top:clamp(22px,5vw,32px);
    padding:17px;border:none;border-radius:var(--r);
    background:var(--amber);color:#1a1408;
    font:600 16px/1 inherit;font-family:inherit;letter-spacing:.02em;
    cursor:pointer;transition:background .15s,transform .1s;
    min-height:52px;                       /* 触控目标 */
  }
  .go:hover:not(:disabled){background:#e5b354}
  .go:active:not(:disabled){transform:translateY(1px)}
  .go:disabled{background:#3a332a;color:#6b6157;cursor:default}

  /* ── 次要操作 ── */
  .sub{display:none;gap:10px;margin-top:10px}
  .sub.on{display:flex}
  .sub button{
    flex:1;min-height:46px;background:none;color:var(--mute);
    border:1px solid var(--line);border-radius:var(--r);
    font:13.5px inherit;font-family:inherit;cursor:pointer;
    transition:color .15s,border-color .15s;
  }
  .sub button:hover:not(:disabled){color:var(--ink);border-color:#443c34}
  .sub button:disabled{opacity:.35;cursor:default}

  /* ── 保存位置：低调，紧贴输入区下沿 ── */
  .save{margin-top:clamp(20px,4vw,26px)}
  .save .bare{padding:11px 0;font-size:14px}
  .save .lbl{margin-bottom:6px;font-size:11.5px}

  /* ── 高级：折叠，默认不打扰 ── */
  details.adv{margin-top:clamp(24px,5vw,34px)}
  details.adv>summary{
    list-style:none;cursor:pointer;user-select:none;
    font-size:12px;letter-spacing:.1em;text-transform:uppercase;
    color:var(--mute);font-weight:600;
    padding:10px 0;border-top:1px solid var(--line);
    display:flex;align-items:center;gap:8px;transition:color .15s;
  }
  details.adv>summary:hover{color:var(--ink)}
  details.adv>summary::-webkit-details-marker{display:none}
  details.adv>summary::before{
    content:"";width:6px;height:6px;border-right:1.5px solid currentColor;
    border-bottom:1.5px solid currentColor;transform:rotate(-45deg);
    transition:transform .2s;margin-right:2px;
  }
  details.adv[open]>summary::before{transform:rotate(45deg)}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:18px 20px;
    padding:18px 0 4px}
  .grid .full{grid-column:1/-1}
  .grid .lbl{font-size:11.5px;margin-bottom:6px}
  .grid .bare{padding:10px 0;font-size:14.5px}
  select.bare{appearance:none;background-image:none;cursor:pointer}

  /* ── 进度：数字是主角，不是进度条 ── */
  .stage{margin-top:clamp(30px,6vw,44px);display:none}
  .stage.on{display:block}
  .status{font-size:14.5px;color:var(--mute);line-height:1.6;margin-bottom:18px}
  .count{display:flex;align-items:baseline;gap:6px;margin-bottom:12px}
  .count .big{
    font-size:clamp(38px,10vw,54px);font-weight:600;line-height:.95;
    letter-spacing:-.03em;font-variant-numeric:tabular-nums;
    color:var(--ink);
  }
  .count .rest{font-size:13px;color:var(--mute);letter-spacing:.02em}
  .track{height:2px;background:var(--line);position:relative;overflow:hidden}
  .track i{position:absolute;inset:0 auto 0 0;width:0;background:var(--amber);
    transition:width .45s cubic-bezier(.4,0,.2,1)}
  .warn{display:none;margin-top:16px;font-size:13.5px;line-height:1.6;
    color:var(--warn);padding-left:12px;border-left:2px solid var(--warn)}
  .warn.on{display:block}
  .done{display:none;margin-top:22px}
  .done.on{display:block}
  .done a{
    display:inline-flex;align-items:center;gap:9px;
    background:none;border:1px solid var(--ok);color:var(--ok);
    text-decoration:none;padding:13px 24px;border-radius:var(--r);
    font-size:14.5px;font-weight:600;min-height:48px;
    transition:background .15s,color .15s;
  }
  .done a:hover{background:var(--ok);color:#0e1a11}

  /* ── 日志 ── */
  details.log{margin-top:26px}
  details.log>summary{
    list-style:none;cursor:pointer;font-size:11.5px;letter-spacing:.1em;
    text-transform:uppercase;color:#6b6157;padding:10px 0;
    border-top:1px solid var(--line);
  }
  details.log>summary::-webkit-details-marker{display:none}
  details.log>summary:hover{color:var(--mute)}
  #logbox{
    max-height:230px;overflow-y:auto;margin-top:12px;
    font:11.5px/1.85 ui-monospace,"SF Mono",Consolas,monospace;
    white-space:pre-wrap;color:#6f665c;word-break:break-word;
  }

  /* ── 手机：单列、加大触控、去掉悬停态 ── */
  @media (max-width:560px){
    .grid{grid-template-columns:1fr;gap:16px}
    .count .big{font-size:44px}
    .head{margin-bottom:26px}
    .done a{width:100%;justify-content:center}
  }
  @media (prefers-reduced-motion:reduce){
    *{transition:none!important;animation:none!important}
  }
</style></head><body><div class="app">

<div class="head">
  <span class="mark">小说下载</span>
  <em id="ver"></em>
</div>

<div class="field">
  <label class="lbl" for="url">目录页网址</label>
  <div class="with-btn">
    <input class="bare" id="url" placeholder="粘贴小说目录页的网址"
           autocomplete="off" spellcheck="false"
           inputmode="url" autocapitalize="off" autocorrect="off">
    <button class="mini" id="paste" type="button">粘贴</button>
  </div>
</div>

<div class="save">
  <label class="lbl" for="outdir">保存位置</label>
  <div class="with-btn">
    <input class="bare" id="outdir" placeholder="默认：程序目录下的 books"
           autocomplete="off" spellcheck="false">
    <button class="mini" id="opendir" type="button">打开</button>
  </div>
</div>

<button class="go" id="go">开始下载</button>
<div class="sub" id="ctrl">
  <button id="pause">暂停</button>
  <button id="stop">停止</button>
</div>

<details class="adv">
  <summary>高级选项</summary>
  <div class="grid">
    <div class="full">
      <label class="lbl" for="backup">备用网址</label>
      <input class="bare" id="backup" placeholder="主网址打不开时用"
             autocomplete="off">
    </div>
    <div>
      <label class="lbl" for="f">起始章</label>
      <input class="bare" id="f" type="number" min="1" inputmode="numeric"
             placeholder="从头">
    </div>
    <div>
      <label class="lbl" for="t">结束章</label>
      <input class="bare" id="t" type="number" min="1" inputmode="numeric"
             placeholder="到最后">
    </div>
    <div>
      <label class="lbl" for="workers">同时下载</label>
      <input class="bare" id="workers" type="number" min="1" max="16"
             step="1" value="2" inputmode="numeric">
    </div>
    <div>
      <label class="lbl" for="delay">每章间隔（秒）</label>
      <input class="bare" id="delay" type="number" min="0" step="0.5"
             value="1" inputmode="decimal">
    </div>
    <div class="full">
      <label class="lbl" for="d">网站类型</label>
      <select class="bare" id="d">
        <option value="requests">普通网站</option>
        <option value="selenium">需要浏览器渲染</option>
      </select>
    </div>
  </div>
  <p class="status" id="rulehint" style="margin:10px 0 0;font-size:12px"></p>
</details>

<div class="stage" id="stage">
  <div class="status" id="status">准备中</div>
  <div class="count">
    <span class="big" id="prog">0</span>
    <span class="rest">/ <span id="total">0</span> 章 · <span id="pct">0%</span></span>
  </div>
  <div class="track"><i id="fill"></i></div>
  <div class="warn" id="warn"></div>
  <div class="done" id="done"><a id="dl" href="/api/txt">下载 TXT 文件</a></div>
</div>

<details class="log">
  <summary>运行日志</summary>
  <div id="logbox"></div>
</details>

</div>

<script>
const $=id=>document.getElementById(id);
let busy=false, hadRun=false;

async function tick(){
  try{
    const s=await(await fetch('/api/status')).json();
    if(s.running) hadRun=true;
    if($('status').textContent!==s.message) $('status').textContent=s.message;
    $('prog').textContent=s.done;
    $('total').textContent=s.total;
    const p=s.total?Math.round(100*s.done/s.total):0;
    $('pct').textContent=p+'%';
    $('fill').style.width=p+'%';

    $('stage').classList.toggle('on', hadRun||s.running||s.total>0||!!s.result_txt);

    const w=$('warn');
    if(s.failed.length){
      w.classList.add('on');
      w.textContent='有 '+s.failed.length+' 章没成功。再点一次「开始下载」只补这些。';
    }else w.classList.remove('on');

    $('done').classList.toggle('on', !!s.result_txt);

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
    if(s.version) $('ver').textContent='v'+s.version;
  }catch(e){
    $('status').textContent='连接不上本地服务，确认那个窗口还开着';
    $('stage').classList.add('on');
  }
}
setInterval(tick,1000);tick();

(async()=>{
  try{
    const r=await(await fetch('/api/rules')).json();
    $('rulehint').textContent = r.count
      ? '已收录 '+r.count+' 个网站；其它网站会自动分析网页结构'
      : '未收录网站，全部靠自动分析网页结构';
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
    workers:$('workers').value?parseInt($('workers').value):2,
    outdir:$('outdir').value.trim()
  };
  $('stage').classList.add('on');
  const r=await(await fetch('/api/start',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  if(!r.ok) alert(r.error);
  tick();
};

$('paste').onclick=async()=>{
  const inp=$('url');
  const put=v=>{ inp.value=(v||'').trim(); inp.focus();
                 inp.dispatchEvent(new Event('input')); };
  try{
    const t=await navigator.clipboard.readText();
    if(t){ put(t); return; }
  }catch(e){}
  try{ inp.focus(); if(document.execCommand('paste')) return; }catch(e){}
  inp.focus(); inp.select();
  alert('浏览器不允许网页直接读剪贴板。\\n已选中输入框，按 Ctrl+V 粘贴（手机上长按输入框选粘贴）。');
};

$('opendir').onclick=async()=>{
  const r=await(await fetch('/api/opendir',{method:'POST'})).json();
  if(!r.ok) alert(r.error||'打不开文件夹');
};

$('url').addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!busy) $('go').click();
});

$('pause').onclick=async()=>{
  const resume=$('pause').textContent==='继续';
  await fetch(resume?'/api/resume':'/api/pause',{method:'POST'});
  tick();
};

$('stop').onclick=async()=>{
  if(!confirm('停止？已下好的章节会保留，下次接着下。')) return;
  await fetch('/api/stop',{method:'POST'});
  tick();
};
</script></body></html>"""



class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 本地服务，页面和接口都不该被缓存：
        # 否则改完代码后浏览器还拿着旧页面，看起来像「改动没生效」。
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
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

        if self.path == "/api/opendir":
            # 在资源管理器里打开当前保存目录
            d = STATE.get("out_dir") or OUT_DIR
            try:
                os.makedirs(d, exist_ok=True)
                if sys.platform.startswith("win"):
                    os.startfile(d)            # noqa: S606 打开本地文件夹
                elif sys.platform == "darwin":
                    import subprocess
                    subprocess.Popen(["open", d])
                else:
                    import subprocess
                    subprocess.Popen(["xdg-open", d])
                return self._json(200, {"ok": True, "path": d})
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
            if STATE["running"]:
                return self._json(409, {"ok": False, "error": "已有任务在跑，等它结束再开新的"})
        try:
            outdir = resolve_out_dir(req.get("outdir"))
        except Exception as e:
            return self._json(400, {"ok": False, "error": f"保存位置无效：{e}"})
        run_task(url, req.get("from"), req.get("to"),
                 req.get("driver") or "requests", req.get("delay") or 0,
                 backup_url=(req.get("backup") or "").strip() or None,
                 workers=int(req.get("workers") or 1),
                 out_dir=outdir)
        self._json(200, {"ok": True, "outdir": outdir})

    def log_message(self, *a):   # 静音 HTTP 访问日志
        pass


def lan_addresses() -> list[str]:
    """列出本机在局域网里的 IP，给手机访问用。"""
    import socket
    ips = []
    # 主出口 IP（连一个不存在的外部地址不会真的发包，只为让系统选路由）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        try:
            s.connect(("10.255.255.255", 1))
            ips.append(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    # 兜底：遍历主机名解析出的地址
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


def main():
    ap = argparse.ArgumentParser(description="novel-dl 本地网页控制台")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--lan", action="store_true",
                    help="允许同一 Wi-Fi 下的手机访问（默认只允许本机）")
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

    # --lan 时监听所有网卡，否则只监听本机
    bind = "0.0.0.0" if a.lan else "127.0.0.1"
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
    if a.lan:
        for ip in lan_addresses():
            nlog.info("手机访问：http://%s:%d  （需与电脑同一 Wi-Fi）", ip, port)
    else:
        nlog.info("仅本机可访问。想让手机也能用，加 --lan 参数重启。")

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
