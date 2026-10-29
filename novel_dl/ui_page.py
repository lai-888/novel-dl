"""网页界面：书架页 + 新建下载页 + 章节预览。

三块内容：
  1. 书架（首页）—— 已下载的书，每本书可展开成章节列表
  2. 新建（第二页）—— 粘贴网址、设置参数、开始下载
  3. 预览 —— 点任意章节，右侧（手机上是全屏）滑出正文

每章带质检标记：正常 / 需留意 / 有问题。用户不用打开 TXT 就知道哪章坏了。
"""
from .ui_theme import THEME_CSS

PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>小说下载</title>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b0a09">
<style>
__THEME__
</style>
<style>
/* ================= 布局 ================= */
.app{position:relative;z-index:1;max-width:980px;margin:0 auto;
  padding:clamp(18px,4vw,40px) clamp(16px,4vw,28px) 80px}

/* ---- 顶栏 ---- */
.top{display:flex;align-items:center;gap:16px;
  padding-bottom:16px;margin-bottom:clamp(24px,5vw,38px)}
.brand{display:flex;align-items:baseline;gap:10px;margin-right:auto;min-width:0}
.brand h1{font-size:15px;font-weight:600;letter-spacing:.2em;
  margin:0;color:var(--text-0);text-transform:uppercase;white-space:nowrap}
.brand .ver{font-size:10.5px;color:var(--text-2);letter-spacing:.08em}

.tabs{display:flex;gap:2px}
.tab{
  padding:9px 18px;min-height:40px;display:inline-flex;align-items:center;
  background:none;border:1px solid transparent;border-radius:var(--r-0);
  color:var(--text-2);font-size:13.5px;letter-spacing:.06em;cursor:pointer;
  transition:color .18s,border-color .18s,background .18s;
}
.tab:hover{color:var(--text-1)}
.tab.on{color:var(--gold);border-color:var(--line-lit);background:var(--gold-glow)}
.tab .badge{
  display:inline-block;min-width:17px;height:17px;line-height:17px;
  margin-left:7px;padding:0 5px;border-radius:9px;
  background:var(--bad);color:#fff;font-size:10.5px;text-align:center;
}

/* ---- 页面切换 ---- */
.page{display:none}
.page.on{display:block;animation:pageIn .28s ease}
@keyframes pageIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}

/* ================= 书架 ================= */
.shelf-empty{
  text-align:center;padding:clamp(50px,12vw,90px) 20px;
  color:var(--text-2);font-size:14px;line-height:2;
}
.shelf-empty .big{font-size:15px;color:var(--text-1);margin-bottom:8px}

.book{
  border:1px solid var(--line);background:var(--ink-1);
  margin-bottom:10px;position:relative;
  transition:border-color .18s;
}
.book:hover{border-color:var(--line-lit)}
.book.open{border-color:var(--line-lit);background:var(--ink-2)}

.book-head{
  display:flex;align-items:center;gap:14px;
  padding:16px 18px;cursor:pointer;user-select:none;
  min-height:60px;
}
.book-title{flex:1;min-width:0}
.book-title b{
  display:block;font-size:15.5px;font-weight:600;line-height:1.4;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.book-title span{display:block;font-size:12px;color:var(--text-2);margin-top:3px}

.book-meta{display:flex;align-items:center;gap:14px;flex:0 0 auto}
.stat{font-size:12px;color:var(--text-2);text-align:right;line-height:1.5}
.stat b{color:var(--text-1);font-variant-numeric:tabular-nums}
.health{display:flex;align-items:center;gap:5px;font-size:12px;white-space:nowrap}
.chev{
  width:7px;height:7px;flex:0 0 auto;
  border-right:1.5px solid var(--text-2);border-bottom:1.5px solid var(--text-2);
  transform:rotate(-45deg);transition:transform .2s;margin-left:4px;
}
.book.open .chev{transform:rotate(45deg)}

/* ---- 章节列表 ---- */
.chapters{display:none;border-top:1px solid var(--line)}
.book.open .chapters{display:block}
.chap{
  display:flex;align-items:center;gap:12px;
  padding:11px 18px;min-height:44px;cursor:pointer;
  border-bottom:1px solid rgba(43,38,32,.5);
  transition:background .14s;
}
.chap:last-child{border-bottom:none}
.chap:hover{background:var(--ink-3)}
.chap .no{
  flex:0 0 46px;font-size:11.5px;color:var(--text-2);
  font-variant-numeric:tabular-nums;letter-spacing:.04em;
}
.chap .name{
  flex:1;min-width:0;font-size:13.5px;color:var(--text-1);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.chap .flag{
  flex:0 0 auto;font-size:11px;padding:2px 8px;border-radius:2px;
  border:1px solid currentColor;opacity:.85;
}
.chap.bad .name{color:var(--bad)}
.chap.warn .name{color:var(--warn)}
.chap.bad:hover{background:rgba(184,84,80,.08)}
.chap.warn:hover{background:rgba(201,150,60,.07)}

.book-actions{
  display:flex;gap:8px;padding:14px 18px;border-top:1px solid var(--line);
  background:var(--ink-1);flex-wrap:wrap;
}
.book-actions .btn{min-height:38px;padding:0 16px;font-size:13px}

/* ================= 新建页 ================= */
.form-wrap{max-width:560px}
.url-row{display:flex;gap:8px}
.url-row .input{flex:1;min-width:0}
.url-row .btn{flex:0 0 auto;padding:0 16px}

details.adv{margin-top:26px}
details.adv>summary{
  list-style:none;cursor:pointer;user-select:none;
  font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--text-2);font-weight:600;padding:13px 0;
  border-top:1px solid var(--line);display:flex;align-items:center;gap:8px;
  transition:color .18s;
}
details.adv>summary:hover{color:var(--text-1)}
details.adv>summary::-webkit-details-marker{display:none}
details.adv>summary::before{
  content:"";width:5px;height:5px;border-right:1.5px solid currentColor;
  border-bottom:1.5px solid currentColor;transform:rotate(-45deg);
  transition:transform .2s;
}
details.adv[open]>summary::before{transform:rotate(45deg)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px 18px;padding-top:8px}
.grid2 .full{grid-column:1/-1}

/* ---- 下载进度 ---- */
.progress{display:none;margin-top:clamp(26px,5vw,36px)}
.progress.on{display:block}
.p-head{display:flex;align-items:baseline;gap:10px;margin-bottom:10px}
.p-num{font-size:clamp(30px,7vw,40px);font-weight:600;line-height:1;
  letter-spacing:-.02em;font-variant-numeric:tabular-nums;color:var(--text-0)}
.p-tot{font-size:12.5px;color:var(--text-2)}
.p-state{font-size:13px;color:var(--text-1);margin-bottom:14px;min-height:20px}
.track{height:2px;background:var(--line);position:relative;overflow:hidden}
.track i{position:absolute;inset:0 auto 0 0;width:0;
  background:linear-gradient(90deg,var(--gold-dim),var(--gold));
  transition:width .4s cubic-bezier(.4,0,.2,1)}
.p-sub{display:none;gap:8px;margin-top:14px}
.p-sub.on{display:flex}

.notice{display:none;margin-top:16px;font-size:13px;line-height:1.7;
  padding:12px 14px;border-left:2px solid currentColor;background:var(--ink-1)}
.notice.on{display:block}
.notice.bad{color:var(--bad)}
.notice.warn{color:var(--warn)}

.logwrap{margin-top:24px}
.logwrap>summary{
  list-style:none;cursor:pointer;font-size:11px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--text-2);padding:11px 0;
  border-top:1px solid var(--line);
}
.logwrap>summary::-webkit-details-marker{display:none}
#logbox{max-height:210px;overflow-y:auto;margin-top:10px;
  font:11px/1.9 ui-monospace,"SF Mono",Consolas,monospace;
  white-space:pre-wrap;color:var(--text-2);word-break:break-word}

/* ================= 预览抽屉 ================= */
.preview{
  position:fixed;z-index:40;inset:0;pointer-events:none;
}
.preview.open{pointer-events:auto}
.preview .scrim{
  position:absolute;inset:0;background:rgba(5,4,3,.6);
  opacity:0;transition:opacity .25s;backdrop-filter:blur(2px);
}
.preview.open .scrim{opacity:1}
.preview .panel{
  position:absolute;top:0;right:0;bottom:0;width:min(620px,100%);
  background:var(--ink-1);border-left:1px solid var(--line-lit);
  display:flex;flex-direction:column;
  transform:translateX(100%);transition:transform .3s cubic-bezier(.4,0,.2,1);
  box-shadow:-24px 0 60px rgba(0,0,0,.5);
}
.preview.open .panel{transform:none}
.p-head-bar{
  display:flex;align-items:center;gap:12px;padding:16px 20px;
  border-bottom:1px solid var(--line);flex:0 0 auto;
}
.p-title{flex:1;min-width:0}
.p-title b{display:block;font-size:14.5px;font-weight:600;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.p-title span{display:block;font-size:11.5px;color:var(--text-2);margin-top:2px}
.p-close{
  flex:0 0 auto;width:36px;height:36px;background:none;
  border:1px solid var(--line);border-radius:var(--r-0);
  color:var(--text-2);font-size:17px;cursor:pointer;line-height:1;
  transition:color .18s,border-color .18s;
}
.p-close:hover{color:var(--text-0);border-color:var(--line-lit)}
.p-warn{
  display:none;margin:14px 20px 0;padding:10px 13px;font-size:12.5px;
  line-height:1.6;border-left:2px solid var(--bad);color:var(--bad);
  background:rgba(184,84,80,.07);
}
.p-warn.on{display:block}
.p-body{
  flex:1;overflow-y:auto;padding:22px 24px 40px;
  font-size:15.5px;line-height:2;color:var(--text-0);
  white-space:pre-wrap;word-break:break-word;
}
.p-body::-webkit-scrollbar{width:8px}
.p-body::-webkit-scrollbar-thumb{background:var(--line-lit);border-radius:4px}
.p-nav{
  display:flex;gap:8px;padding:12px 20px;border-top:1px solid var(--line);
  flex:0 0 auto;
}
.p-nav .btn{flex:1;min-height:40px;font-size:13px}

/* ================= 手机 ================= */
@media (max-width:640px){
  .top{flex-wrap:wrap;gap:12px}
  .brand{margin-right:0;width:100%}
  .tabs{width:100%}
  .tab{flex:1;justify-content:center;padding:9px 10px}
  .grid2{grid-template-columns:1fr}
  .book-head{flex-wrap:wrap;gap:10px}
  .book-meta{width:100%;justify-content:space-between}
  .preview .panel{width:100%;border-left:none}
  .p-body{padding:18px 18px 32px;font-size:16px}
  .chap .no{flex-basis:38px}
}
</style></head><body>
<div class="app">

  <header class="top">
    <div class="brand">
      <h1>小说下载</h1>
      <span class="ver" id="ver"></span>
    </div>
    <nav class="tabs">
      <button class="tab on" data-page="shelf" id="tabShelf">
        书架<span class="badge" id="badge" style="display:none">0</span>
      </button>
      <button class="tab" data-page="new" id="tabNew">新建下载</button>
    </nav>
  </header>

  <hr class="rule">

  <!-- ============ 书架 ============ -->
  <section class="page on" id="page-shelf">
    <div style="margin:22px 0 18px;display:flex;align-items:center;gap:12px">
      <button class="btn" id="refresh">刷新</button>
      <span style="font-size:12px;color:var(--text-2)" id="shelfHint"></span>
    </div>
    <div id="shelf"></div>
  </section>

  <!-- ============ 新建 ============ -->
  <section class="page" id="page-new">
    <div class="form-wrap" style="margin-top:26px">

      <label class="field">
        <span>小说目录页网址</span>
        <div class="url-row">
          <input class="input" id="url" placeholder="粘贴小说目录页的网址"
                 autocomplete="off" spellcheck="false"
                 inputmode="url" autocapitalize="off" autocorrect="off">
          <button class="btn" id="paste" type="button">粘贴</button>
        </div>
      </label>

      <label class="field">
        <span>保存位置</span>
        <div class="url-row">
          <input class="input" id="outdir" placeholder="默认：程序目录下的 books"
                 autocomplete="off" spellcheck="false">
          <button class="btn" id="opendir" type="button">打开</button>
        </div>
      </label>

      <button class="btn btn-primary" id="go" style="width:100%">开始下载</button>
      <div class="p-sub" id="ctrl">
        <button class="btn" id="pause">暂停</button>
        <button class="btn" id="stop">停止</button>
      </div>

      <details class="adv">
        <summary>高级选项</summary>
        <div class="grid2">
          <label class="field full" style="margin:0">
            <span>备用网址</span>
            <input class="input" id="backup" placeholder="主网址打不开时用"
                   autocomplete="off">
          </label>
          <label class="field" style="margin:0">
            <span>起始章</span>
            <input class="input" id="f" type="number" min="1"
                   inputmode="numeric" placeholder="从头">
          </label>
          <label class="field" style="margin:0">
            <span>结束章</span>
            <input class="input" id="t" type="number" min="1"
                   inputmode="numeric" placeholder="到最后">
          </label>
          <label class="field" style="margin:0">
            <span>同时下载</span>
            <input class="input" id="workers" type="number" min="1" max="16"
                   step="1" value="2" inputmode="numeric">
          </label>
          <label class="field" style="margin:0">
            <span>每章间隔（秒）</span>
            <input class="input" id="delay" type="number" min="0" step="0.5"
                   value="1" inputmode="decimal">
          </label>
          <label class="field full" style="margin:0">
            <span>网站类型</span>
            <select class="input" id="d">
              <option value="requests">普通网站</option>
              <option value="selenium">需要浏览器渲染</option>
            </select>
          </label>
        </div>
        <p style="font-size:12px;color:var(--text-2);margin:14px 0 0" id="rulehint"></p>
      </details>

      <div class="progress" id="prog">
        <div class="p-head">
          <span class="p-num" id="progNum">0</span>
          <span class="p-tot">/ <span id="progTot">0</span> 章 · <span id="progPct">0%</span></span>
        </div>
        <div class="p-state" id="progState">准备中</div>
        <div class="track"><i id="progFill"></i></div>
        <div class="notice bad" id="progNotice"></div>
      </div>

      <details class="logwrap">
        <summary>运行日志</summary>
        <div id="logbox"></div>
      </details>
    </div>
  </section>
</div>

<!-- ============ 预览抽屉 ============ -->
<div class="preview" id="preview">
  <div class="scrim" id="scrim"></div>
  <aside class="panel">
    <div class="p-head-bar">
      <div class="p-title">
        <b id="pvTitle">章节</b>
        <span id="pvMeta"></span>
      </div>
      <button class="p-close" id="pvClose" aria-label="关闭">×</button>
    </div>
    <div class="p-warn" id="pvWarn"></div>
    <div class="p-body" id="pvBody"></div>
    <div class="p-nav">
      <button class="btn" id="pvPrev">上一章</button>
      <button class="btn" id="pvNext">下一章</button>
    </div>
  </aside>
</div>

<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

let BOOKS=[], current=null, pvBook=null, pvNo=0, busy=false, hadRun=false;

/* ---------- 页面切换 ---------- */
function showPage(name){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('on'));
  $('page-'+name).classList.add('on');
  document.querySelectorAll('.tab').forEach(t=>
    t.classList.toggle('on', t.dataset.page===name));
  window.scrollTo({top:0,behavior:'smooth'});
}
document.querySelectorAll('.tab').forEach(t=>{
  t.onclick=()=>showPage(t.dataset.page);
});

/* ---------- 书架 ---------- */
const LV={
  ok:{cls:'lv-ok',label:'正常',dot:'dot-ok'},
  warn:{cls:'lv-warn',label:'需留意',dot:'dot-warn'},
  bad:{cls:'lv-bad',label:'有问题',dot:'dot-bad'},
  pending:{cls:'',label:'未下载',dot:'dot-pending'},
};

async function loadShelf(){
  $('refresh').disabled=true;
  try{
    const r=await(await fetch('/api/books')).json();
    BOOKS=r.books||[];
    renderShelf();
  }catch(e){
    $('shelf').innerHTML='<div class="shelf-empty">读取书架失败</div>';
  }finally{
    $('refresh').disabled=false;
  }
}

function renderShelf(){
  const box=$('shelf'), b=$('badge');
  const badTotal=BOOKS.reduce((n,x)=>n+(x.stats.bad||0),0);
  b.style.display=badTotal?'inline-block':'none';
  b.textContent=badTotal>99?'99+':badTotal;
  $('shelfHint').textContent=BOOKS.length
    ? `${BOOKS.length} 本 · ${BOOKS.reduce((n,x)=>n+x.stats.total,0)} 章`
    : '';

  if(!BOOKS.length){
    box.innerHTML=`<div class="shelf-empty">
      <div class="big">书架还是空的</div>
      去「新建下载」粘贴一个小说目录页的网址开始
    </div>`;
    return;
  }

  box.innerHTML=BOOKS.map((bk,i)=>{
    const s=bk.stats;
    let health, hcls;
    if(s.bad){ health=s.bad+' 章有问题'; hcls='lv-bad'; }
    else if(s.warn){ health=s.warn+' 章需留意'; hcls='lv-warn'; }
    else { health='内容正常'; hcls='lv-ok'; }

    const chaps=bk.chapters.map(c=>{
      const lv=c.status==='pending'?LV.pending:LV[c.level]||LV.ok;
      const flag=(c.level==='bad'||c.level==='warn')
        ? `<span class="flag ${lv.cls}">${lv.label}</span>` : '';
      return `<div class="chap ${c.level}" data-book="${i}" data-no="${c.no}">
        <span class="no">${String(c.no).padStart(3,'0')}</span>
        <span class="name">${esc(c.title)}</span>
        ${flag}
      </div>`;
    }).join('');

    return `<div class="book" data-idx="${i}">
      <div class="book-head">
        <span class="chev"></span>
        <div class="book-title">
          <b>${esc(bk.title)}</b>
          <span>${s.total} 章</span>
        </div>
        <div class="book-meta">
          <span class="health ${hcls}">
            <i class="dot ${s.bad?'dot-bad':(s.warn?'dot-warn':'dot-ok')}"></i>${health}
          </span>
        </div>
      </div>
      <div class="chapters">${chaps}
        <div class="book-actions">
          <button class="btn act-txt" data-idx="${i}">下载 TXT</button>
          <button class="btn act-dir" data-idx="${i}">打开目录</button>
        </div>
      </div>
    </div>`;
  }).join('');

  box.querySelectorAll('.book-head').forEach(h=>{
    h.onclick=()=>h.parentElement.classList.toggle('open');
  });
  box.querySelectorAll('.chap').forEach(el=>{
    el.onclick=()=>openPreview(+el.dataset.book, +el.dataset.no);
  });
  box.querySelectorAll('.act-txt').forEach(btn=>{
    btn.onclick=e=>{
      e.stopPropagation();
      const bk=BOOKS[+btn.dataset.idx];
      location.href='/api/book-file?slug='+encodeURIComponent(bk.slug);
    };
  });
  box.querySelectorAll('.act-dir').forEach(btn=>{
    btn.onclick=e=>{
      e.stopPropagation();
      const bk=BOOKS[+btn.dataset.idx];
      fetch('/api/opendir',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({path:bk.path})});
    };
  });
}

/* ---------- 预览 ---------- */
async function openPreview(bookIdx, no){
  pvBook=BOOKS[bookIdx];
  if(!pvBook) return;
  pvNo=no;
  $('preview').classList.add('open');
  document.body.style.overflow='hidden';
  $('pvBody').textContent='加载中…';
  $('pvWarn').classList.remove('on');

  try{
    const r=await(await fetch(
      `/api/chapter?slug=${encodeURIComponent(pvBook.slug)}&no=${no}`)).json();
    if(r.error){ $('pvBody').textContent=r.error; return; }

    $('pvTitle').textContent=r.title||'';
    const q=r.quality||{};
    const lv=LV[q.level]||LV.ok;
    $('pvMeta').innerHTML=
      `<span class="${lv.cls}">${lv.label}</span>`
      + ` · ${q.length||0} 字`
      + (q.cjk_ratio!=null?` · 中文 ${Math.round(q.cjk_ratio*100)}%`:'');

    if(q.issues&&q.issues.length){
      $('pvWarn').textContent='⚠ '+q.issues.join('；');
      $('pvWarn').classList.add('on');
    }
    $('pvBody').textContent=r.text||'（本章没有正文）';
    $('pvBody').scrollTop=0;

    const total=pvBook.chapters.length;
    $('pvPrev').disabled=no<=1;
    $('pvNext').disabled=no>=total;
  }catch(e){
    $('pvBody').textContent='读取失败：'+e;
  }
}

function closePreview(){
  $('preview').classList.remove('open');
  document.body.style.overflow='';
}
$('pvClose').onclick=closePreview;
$('scrim').onclick=closePreview;
$('pvPrev').onclick=()=>{ if(pvNo>1) openPreview(BOOKS.indexOf(pvBook), pvNo-1); };
$('pvNext').onclick=()=>{
  if(pvNo<pvBook.chapters.length) openPreview(BOOKS.indexOf(pvBook), pvNo+1);
};
document.addEventListener('keydown',e=>{
  if(!$('preview').classList.contains('open')) return;
  if(e.key==='Escape') closePreview();
  if(e.key==='ArrowLeft'&&!$('pvPrev').disabled) $('pvPrev').click();
  if(e.key==='ArrowRight'&&!$('pvNext').disabled) $('pvNext').click();
});

$('refresh').onclick=loadShelf;

/* ---------- 下载进度 ---------- */
async function tick(){
  try{
    const s=await(await fetch('/api/status')).json();
    if(s.running) hadRun=true;
    if(s.version) $('ver').textContent='v'+s.version;

    const show=hadRun||s.running||s.total>0;
    $('prog').classList.toggle('on',show);
    if(show){
      $('progNum').textContent=s.done;
      $('progTot').textContent=s.total;
      const p=s.total?Math.round(100*s.done/s.total):0;
      $('progPct').textContent=p+'%';
      $('progFill').style.width=p+'%';
      $('progState').textContent=s.message||'';
      const n=$('progNotice');
      if(s.failed&&s.failed.length){
        n.classList.add('on');
        n.textContent='有 '+s.failed.length+' 章没成功。再点一次「开始下载」只补这些。';
      }else n.classList.remove('on');
    }

    const box=$('logbox');
    const txt=(s.lines||[]).join('\\n');
    if(box.textContent!==txt){ box.textContent=txt; box.scrollTop=1e9; }

    busy=s.running;
    $('go').disabled=busy;
    $('go').textContent=busy?'正在下载…':'开始下载';
    $('ctrl').classList.toggle('on',busy);
    $('pause').disabled=!busy;
    $('pause').textContent=s.paused?'继续':'暂停';
    $('stop').disabled=!busy;

    // 下载刚结束：自动刷新书架，让新书立刻出现
    if(hadRun&&!busy&&s.result_txt&&$('lastResult')!==s.result_txt){
      $('lastResult')=s.result_txt;
      loadShelf();
    }
  }catch(e){
    $('progState').textContent='连接不上本地服务，确认那个窗口还开着';
  }
}
setInterval(tick,1000);
tick();

(async()=>{
  try{
    const r=await(await fetch('/api/rules')).json();
    $('rulehint').textContent=r.count
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
  const r=await(await fetch('/api/start',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  if(!r.ok) alert(r.error);
  tick();
};

$('url').addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!busy) $('go').click();
});

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
  alert('浏览器不允许网页直接读剪贴板。\\n已选中输入框，按 Ctrl+V 粘贴（手机长按选粘贴）。');
};

$('opendir').onclick=async()=>{
  const r=await(await fetch('/api/opendir',{method:'POST'})).json();
  if(!r.ok) alert(r.error||'打不开文件夹');
};

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

loadShelf();
</script>
</body></html>"""

PAGE = PAGE.replace("__THEME__", THEME_CSS)
