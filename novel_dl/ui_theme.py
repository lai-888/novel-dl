"""页面结构与主题。"""

THEME_CSS = r"""
/* ============ 设计令牌 ============ */
:root{
  --ink-0:#0b0a09;          /* 最深：页面底 */
  --ink-1:#121110;          /* 面板 */
  --ink-2:#181614;          /* 抬升面 */
  --ink-3:#211d19;          /* 悬停 */
  --line:#2b2620;           /* 常规分割线 */
  --line-lit:#3d3428;       /* 强调分割线 */

  --gold:#c9a227;           /* 主强调：金 */
  --gold-dim:#8a6f1c;
  --gold-glow:rgba(201,162,39,.14);

  --text-0:#efe9dd;         /* 主文字 */
  --text-1:#a89e8d;         /* 次要 */
  --text-2:#6d655a;         /* 弱化 */

  --good:#6ba368;
  --warn:#c9963c;
  --bad:#b85450;

  --r-0:2px;
  --r-1:4px;

  --pattern-opacity:.5;
}

*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}

html,body{
  margin:0;padding:0;
  background:var(--ink-0);
  color:var(--text-0);
}

body{
  font:15px/1.65 "Inter Tight","PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased;
  min-height:100dvh;
  position:relative;
}

/* ============ 底纹：颗粒尘埃 ============
   参考图里那种「金色细尘」的感觉，用极小的 radial-gradient 点阵模拟。
   只在页面上层做一层，pointer-events:none 且不参与合成层，性能影响很小。 */
body::before{
  content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
  opacity:var(--pattern-opacity);
  background-image:
    radial-gradient(circle at 18% 22%, rgba(201,162,39,.055) 0 1px, transparent 1px),
    radial-gradient(circle at 72% 38%, rgba(201,162,39,.045) 0 1px, transparent 1px),
    radial-gradient(circle at 41% 71%, rgba(201,162,39,.05)  0 1px, transparent 1px),
    radial-gradient(circle at 88% 84%, rgba(201,162,39,.04) 0 1px, transparent 1px);
  background-size:340px 340px, 420px 420px, 380px 380px, 460px 460px;
  background-repeat:repeat;
}
/* 顶部一层极淡的暖光，让纯黑不死板 */
body::after{
  content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
  background:
    radial-gradient(120% 60% at 50% -10%, rgba(201,162,39,.055), transparent 62%),
    radial-gradient(80% 50% at 50% 110%, rgba(120,90,40,.03), transparent 70%);
}

/* ============ 金线分隔 ============
   两端渐隐的 1px 线，模仿参考图的细金线。 */
.rule{
  height:1px;border:0;margin:0;
  background:linear-gradient(90deg,
    transparent, var(--line-lit) 12%, var(--gold-dim) 50%,
    var(--line-lit) 88%, transparent);
  opacity:.55;
}

/* ============ 带角标的框 ============
   参考图里的方框有内嵌的角部装饰。这里用两条伪元素各画一对角。 */
.frame{position:relative}
.frame::before,.frame::after{
  content:"";position:absolute;width:11px;height:11px;pointer-events:none;
  border-color:var(--gold-dim);border-style:solid;opacity:.55;
}
.frame::before{top:-1px;left:-1px;border-width:1px 0 0 1px}
.frame::after{bottom:-1px;right:-1px;border-width:0 1px 1px 0}
.frame>.corner::before,.frame>.corner::after{
  content:"";position:absolute;width:11px;height:11px;pointer-events:none;
  border-color:var(--gold-dim);border-style:solid;opacity:.55;
}
.frame>.corner::before{top:-1px;right:-1px;border-width:1px 1px 0 0}
.frame>.corner::after{bottom:-1px;left:-1px;border-width:0 0 1px 1px}

/* ============ 基础控件 ============ */
button{font-family:inherit}

.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:8px;
  min-height:44px;padding:0 20px;
  background:transparent;color:var(--text-0);
  border:1px solid var(--line-lit);border-radius:var(--r-0);
  font-size:14px;letter-spacing:.02em;cursor:pointer;
  transition:border-color .18s, background .18s, color .18s;
}
.btn:hover:not(:disabled){border-color:var(--gold-dim);background:var(--gold-glow)}
.btn:active:not(:disabled){transform:translateY(1px)}
.btn:disabled{opacity:.35;cursor:default}

.btn-primary{
  background:var(--gold);color:#1a1408;border-color:var(--gold);
  font-weight:600;
}
.btn-primary:hover:not(:disabled){background:#dcb42f;border-color:#dcb42f}
.btn-primary:disabled{background:var(--ink-3);color:var(--text-2);border-color:var(--line)}

.field{display:block;margin-bottom:20px}
.field>span{
  display:block;font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--text-2);margin-bottom:9px;font-weight:600;
}
.input{
  width:100%;padding:13px 14px;
  background:var(--ink-1);color:var(--text-0);
  border:1px solid var(--line);border-radius:var(--r-0);
  font:15px/1.4 inherit;font-family:inherit;
  transition:border-color .18s, background .18s;
}
.input::placeholder{color:#4a433a}
.input:focus{outline:none;border-color:var(--gold-dim);background:var(--ink-2)}

/* ============ 状态色 ============ */
.lv-ok{color:var(--good)}
.lv-warn{color:var(--warn)}
.lv-bad{color:var(--bad)}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;vertical-align:middle}
.dot-ok{background:var(--good)}
.dot-warn{background:var(--warn)}
.dot-bad{background:var(--bad)}
.dot-pending{background:var(--text-2)}


@media (prefers-reduced-motion:reduce){
  *{transition:none!important;animation:none!important}
}
"""
