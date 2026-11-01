"""内核状态快照与运行轨迹：调试用，不影响下载逻辑。"""
import json
import os
import platform
import sys
import threading
import time

TRACE_EVENTS = 5000


def mask_proxy(url: str) -> str:
    """代理地址里的账号密码不能打出来。"""
    text = (url or "").strip()
    if not text:
        return ""
    if "@" in text and "://" in text:
        head, _, tail = text.rpartition("@")
        scheme = head.split("://")[0]
        return f"{scheme}://***@{tail}"
    return text


def cookie_count(raw) -> int:
    """只报条数，别把 cookie 值写进日志。"""
    if not raw:
        return 0
    text = str(raw)
    n = len([p for p in text.replace("\n", ";").split(";") if p.strip()])
    return n


class Trace:
    """线程安全的环形事件记录。--trace 时同时追加写 JSONL。"""

    def __init__(self, path=None, cap=TRACE_EVENTS):
        self.cap = max(1, int(cap))
        self._lock = threading.Lock()
        self._events = []
        self._fp = None
        self.dropped = 0
        if path:
            self._fp = open(path, "a", encoding="utf-8")
        self.t0 = time.monotonic()

    def emit(self, kind: str, **fields):
        rec = {"t": round(time.monotonic() - self.t0, 4), "kind": kind}
        for k, v in fields.items():
            if isinstance(v, (int, float, bool)) or v is None:
                rec[k] = v
            else:
                rec[k] = str(v)[:200]
        with self._lock:
            if len(self._events) >= self.cap:
                self._events.pop(0)
                self.dropped += 1
            self._events.append(rec)
            fp = self._fp
        if fp is not None:
            try:
                fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception:
                pass

    def events(self, kind: str = None) -> list:
        with self._lock:
            rows = list(self._events)
        return [r for r in rows if kind is None or r["kind"] == kind]

    def close(self):
        with self._lock:
            fp, self._fp = self._fp, None
        if fp is not None:
            try:
                fp.close()
            except Exception:
                pass


CURRENT = None


def set_trace(trace) -> None:
    global CURRENT
    CURRENT = trace


def get_trace():
    return CURRENT


def emit(kind: str, **fields) -> None:
    trace = CURRENT
    if trace is not None:
        trace.emit(kind, **fields)


def _rule_count(rules) -> int:
    try:
        from . import config
        files = config.load().get("rules_files") or []
        return len(rules.load_rules()) if files or True else len(files)
    except Exception:
        return 0


def _learned_count() -> int:
    try:
        from .autodetect import _load_cache
        return len(_load_cache() or {})
    except Exception:
        return 0


def _dep(name: str, attr: str = "__version__") -> str:
    try:
        mod = __import__(name)
    except Exception:
        return "没装"
    return str(getattr(mod, attr, "有"))


def collect(engine=None, url="", out_dir="") -> dict:
    """把能看到的内部状态收成一个 dict（不含任何密钥）。"""
    from . import __version__, config
    from . import fetchers, rules

    cfg = config.load()
    data = {
        "runtime": {
            "版本": __version__,
            "Python": sys.version.split()[0],
            "系统": f"{platform.system()} {platform.release()}",
            "当前目录": os.getcwd(),
            "配置来源": config.find_config_path() or "（默认值）",
        },
        "deps": {
            "requests": _dep("requests"),
            "beautifulsoup4": _dep("bs4"),
            "lxml": _dep("lxml.etree", "LXML_VERSION"),
            "selenium": _dep("selenium"),
            "charset_normalizer": _dep("charset_normalizer"),
            "fontTools": _dep("fontTools"),
            "brotli": _dep("brotli"),
        },
        "config": {
            "输出目录": cfg.get("output_dir"),
            "驱动": cfg.get("driver"),
            "并发": cfg.get("workers"),
            "间隔": cfg.get("delay"),
            "目录页上限": cfg.get("max_pages"),
            "超时": cfg.get("timeout"),
            "代理": mask_proxy(cfg.get("proxy") or os.environ.get("NOVEL_DL_PROXY_URL") or "") or "没设",
            "Cookie 条数": cookie_count(os.environ.get("NOVEL_DL_COOKIE")),
            "规则条数": _rule_count(rules),
            "已学规则数": _learned_count(),
        },
    }

    from .fetchers import get_cookie
    data["config"]["Cookie 条数"] = cookie_count(get_cookie())
    data["config"]["可用驱动"] = list(fetchers.list_fetchers())

    if engine is not None:
        data["引擎"] = engine_state(engine)
        data["进度"] = progress_state(engine, out_dir or "")
    elif url:
        data["站点"] = {"网址": url}
    return data


def fetcher_state(fetcher) -> dict:
    if fetcher is None:
        return {}
    br = getattr(fetcher, "breaker", None)
    out = {
        "驱动": getattr(fetcher, "name", "?"),
        "最小间隔": getattr(fetcher, "min_interval", 0),
        "随机延迟": f"{getattr(fetcher, 'MIN_DELAY', 0)}~{getattr(fetcher, 'MAX_DELAY', 0)}",
        "超时": getattr(fetcher, "TIMEOUT", "?"),
        "重试次数": getattr(fetcher, "RETRIES", "?"),
        "放行证书的站点": len(getattr(fetcher, "insecure_hosts", ()) or ()),
        "Cookie 条数": cookie_count(get_cookie_text()),
    }
    if br is not None:
        with br._lock:
            out["熔断"] = {
                "连续失败": br._streak,
                "冷却次数": br.cooldowns,
                "冷却中": br.slow,
                "当前上限": br._limit,
                "冷却剩余": round(max(0.0, br._until - time.monotonic()), 2),
            }
    return out


def get_cookie_text() -> str:
    try:
        from .fetchers import get_cookie
        return get_cookie() or ""
    except Exception:
        return ""


def engine_state(engine) -> dict:
    from .parser import get_parser
    state = {
        "网址": getattr(engine, "book_url", ""),
        "并发": getattr(engine, "workers", 1),
        "目录页上限": getattr(engine, "max_toc_pages", 0),
        "起始章": getattr(engine, "from_ch", None) or "从头",
        "结束章": getattr(engine, "to_ch", None) or "到最后",
        "备用网址": "有" if getattr(engine, "backup_url", None) else "无",
        "本轮被挡停过": bool(getattr(engine, "_circuit_broken", False)),
        "字体加密已检测": bool(getattr(engine, "_encryption_checked", False)),
        "解析器": type(getattr(engine, "parser", None)).__name__ if getattr(engine, "parser", None) else "还没选",
    }
    fet = getattr(engine, "fetcher", None)
    if fet is not None:
        state["抓取器"] = fetcher_state(fet)
    try:
        p = get_parser(engine.book_url, fetcher=fet, auto=False)
        state["不给提示时会用"] = type(p).__name__
    except Exception:
        pass
    return state


def progress_state(engine, out_dir="") -> dict:
    from .progress import Progress
    try:
        p = getattr(engine, "prog", None) or Progress(out_dir or "", getattr(engine, "book_url", ""))
    except Exception:
        return {}
    st = getattr(p, "state", {}) or {}
    done = st.get("done") or []
    failed = st.get("failed") or []
    preview = st.get("preview") or {}
    total = len(st.get("toc") or [])
    cache_dir = os.path.join(os.path.dirname(getattr(p, "state_path", "") or ""), "cache")
    cache_files = 0
    if os.path.isdir(cache_dir):
        cache_files = len([f for f in os.listdir(cache_dir) if f.endswith(".txt")])
    return {
        "书名": st.get("title") or "（还没读到）",
        "目录章数": total,
        "已完成": len(done),
        "失败": len(failed),
        "仅预览": len(preview),
        "缓存文件": cache_files,
        "账本版本": st.get("version", "?"),
        "账本路径": getattr(p, "state_path", ""),
    }


def disk_progress(out_dir: str, book_url: str = "") -> dict:
    """只看不写：扫输出目录里的账本，报一本书（或全部书）的进度。"""
    out_dir = out_dir or "books"
    rows, books = [], 0
    if not os.path.isdir(out_dir):
        return {"输出目录": out_dir, "说明": "还没有下过书"}
    target = (book_url or "").strip()
    for name in sorted(os.listdir(out_dir)):
        state_path = os.path.join(out_dir, name, "state.json")
        if not os.path.isfile(state_path):
            continue
        try:
            with open(state_path, encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            continue
        books += 1
        done = st.get("done") or []
        failed = st.get("failed") or []
        preview = st.get("preview") or {}
        toc = st.get("toc") or []
        cache_dir = os.path.join(out_dir, name, "cache")
        cache_files = 0
        if os.path.isdir(cache_dir):
            cache_files = len([x for x in os.listdir(cache_dir) if x.endswith(".txt")])
        row = {
            "书名": st.get("title") or name,
            "目录章数": len(toc),
            "已完成": len(done),
            "失败": len(failed),
            "仅预览": len(preview),
            "缓存文件": cache_files,
            "账本版本": st.get("version", "?"),
        }
        if target and st.get("book_url") != target:
            continue
        rows.append(row)
    if target and rows:
        return rows[0]
    return {"输出目录": out_dir, "书数": books} if not rows else {
        "说明": "输出目录里有这些书", "书数": books, "其中一本": rows[0]}


def peek(url: str, driver="requests", proxy=None, cookie=None,
         show=False, attach=None) -> dict:
    """只读地看一眼站点：抓目录页、选解析器、数章节，不写任何文件。"""
    from .fetchers import get_fetcher, set_cookie, set_proxy
    from .parser import find_toc_url, get_parser

    out = {"网址": url}
    if proxy:
        set_proxy(proxy)
    if cookie:
        set_cookie(cookie)
    try:
        fet = get_fetcher(driver, show=show, attach=attach)
    except Exception as e:
        out["抓取器"] = f"起不来（{e}）"
        return out
    out.update(fetcher_state(fet))
    try:
        t0 = time.monotonic()
        html = fet.fetch(url)
        out["抓取毫秒"] = round((time.monotonic() - t0) * 1000)
        out["页面字节"] = len(html.encode("utf-8", "ignore"))
    except Exception as e:
        out["抓取失败"] = str(e)[:160]
        return out

    from .guard import captcha_reason, probe_reason
    for label, fn in (("反爬校验页", probe_reason), ("需要人机验证", captcha_reason)):
        why = fn(html)
        if why:
            out[label] = why
            return out

    parser = get_parser(url, fetcher=fet, auto=True, lazy_auto=False)
    out["解析器"] = type(parser).__name__
    try:
        title, toc = parser.parse_toc(html, url)
    except SystemExit as e:
        alt = find_toc_url(html, url)
        out["目录解析失败"] = f"{e}（页面里有目录链接：{alt or '没有'}）"
        return out
    except Exception as e:
        out["目录解析失败"] = str(e)[:160]
        return out
    out["书名"] = title
    out["目录章数"] = len(toc)
    if toc:
        out["第一章"] = toc[0][0]
        out["最后一章"] = toc[-1][0]
    return out


def report(data: dict) -> str:
    """收好的状态 → 人能读的多行文本。"""
    lines = []
    for section in ("runtime", "deps", "config", "站点", "引擎", "进度"):
        block = data.get(section)
        if not block:
            continue
        head = {"runtime": "运行环境", "deps": "依赖", "config": "配置",
                "站点": "站点", "引擎": "引擎", "进度": "进度"}[section]
        lines.append(f"◆ {head}")
        lines.extend(_rows(block, "  "))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _rows(block, indent: str) -> list:
    out = []
    if not isinstance(block, dict):
        return [indent + str(block)]
    width = max([len(str(k)) for k in block] or [0])
    for k, v in block.items():
        if isinstance(v, dict):
            out.append(f"{indent}{k}")
            out.extend(_rows(v, indent + "  "))
        else:
            out.append(f"{indent}{str(k).ljust(width)}  {v}")
    return out