"""抓取层 —— 插件化设计。"""
import atexit
import inspect
import os
import random
import threading
import time

from urllib.parse import urlsplit

import requests

from . import diag, log
from .encoding import decode_body

FETCHERS = {}

_PROXY: str | None = None
_COOKIE: str = ""


_UA = ""


def set_proxy(url: str | None):
    """设置所有 requests 请求走的代理；传空表示「不显式指定」。"""
    global _PROXY
    u = (url or "").strip()
    _PROXY = u or None


DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def set_ua(raw: str | None):
    """站点把人机验证的通行证绑在 UA 上，那就用你浏览器的那一条。"""
    global _UA
    _UA = (raw or "").strip()


def get_ua() -> str:
    """命令行 > 环境变量 NOVEL_DL_UA > 默认 Chrome。"""
    return _UA or os.environ.get("NOVEL_DL_UA", "").strip() or DEFAULT_UA


def set_cookie(raw: str | None):
    """人机验证过不去时，借用浏览器里已经通过验证的那份 cookie。"""
    global _COOKIE
    _COOKIE = (raw or "").strip()


def get_cookie() -> str:
    """命令行 > 环境变量 NOVEL_DL_COOKIE。"""
    return _COOKIE or (os.environ.get("NOVEL_DL_COOKIE", "").strip())


def parse_cookie(raw: str) -> dict:
    """把 "a=1; b=2" 拆成字典，容忍 "Cookie:" 前缀和多余空白。"""
    raw = (raw or "").strip()
    if raw.lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1]
    out = {}
    for part in raw.replace("\n", ";").split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k:
            out[k] = v
    return out


def get_proxy() -> str | None:
    """当前生效的代理：命令行 > 环境变量 NOVEL_DL_PROXY_URL。"""
    return _PROXY or (os.environ.get("NOVEL_DL_PROXY_URL", "").strip() or None)


def register(name):
    def deco(cls):
        FETCHERS[name] = cls
        return cls
    return deco


def get_fetcher(name: str, **kw):
    if name not in FETCHERS:
        raise SystemExit(f"未知的抓取驱动: {name}，可用: {list(FETCHERS)}")
    cls = FETCHERS[name]
    if kw:
        # 只把驱动认得的参数递进去，免得一个可选参数把别的驱动带崩
        import inspect
        params = inspect.signature(cls.__init__).parameters
        if not any(p.kind == p.VAR_KEYWORD for p in params.values()):
            kw = {k: v for k, v in kw.items() if k in params}
    return cls(**kw)


def list_fetchers() -> list[str]:
    return sorted(FETCHERS)


class Breaker:
    """连续被挡就冷却一段时间，冷却期只发一个请求，缓过来再放开。"""

    STREAK = 3          # 连续几次硬失败算「被挡了」
    BASE = 5.0          # 第一次冷却几秒
    CAP = 60.0
    RECOVER = 3         # 连续几次成功算缓过来
    SLOW_GAP = 0.5      # 降速后两次请求至少隔这么久
    CEIL = 8            # 慢慢恢复时最多放到这么大

    def __init__(self, gate):
        self._gate = gate                 # 走 fetcher.gate_wait，能响应暂停/停止
        self._lock = threading.Lock()
        self._streak = 0
        self._ok = 0
        self._until = 0.0
        self._next_at = 0.0
        self._limit = 1
        self._round = 0
        self.slow = False
        self.cooldowns = 0

    def failed(self, err=None):
        if err is not None and not hard_failure(err):
            return
        with self._lock:
            self._streak += 1
            if self._streak < self.STREAK:
                return
            self._streak = 0
            self._round += 1
            wait = min(self.BASE * (2 ** (self._round - 1)), self.CAP)
            self._until = time.monotonic() + wait
            self.slow, self._ok, self._limit = True, 0, 1
            self.cooldowns += 1
            err_text = f"（{str(err)[:60]}）" if err else ""
            diag.emit("breaker.open", wait=round(wait, 3), round=self._round,
                      streak=self.STREAK, err=err_text)
        log.warning("⏸ 连续 %d 次失败%s，停 %.1f 秒再试，期间只发一个请求",
                    self.STREAK, err_text, wait)

    def ok(self):
        with self._lock:
            self._streak = 0
            if not self.slow:
                return
            self._ok += 1
            if self._ok < self.RECOVER:
                return
            self._ok = 0
            if self._limit >= self.CEIL:
                self.slow, self._round = False, 0
                recovered = True
            else:
                self._limit = min(self._limit * 2, self.CEIL)
                recovered = False
            limit = self._limit
        diag.emit("breaker.rise", limit=limit, slow=self.slow)
        log.info("▶ 连着成功，放开到 %d 个请求%s", limit,
                 "，恢复正常" if recovered else "（慢慢来）")

    def window(self, limit: int) -> int:
        """在途请求数上限：降速期按阶梯放，恢复正常后听引擎的。"""
        with self._lock:
            return min(self._limit, limit) if self.slow else limit

    def wait(self):
        """冷却没过去、或者还在降速，就在这里等着（可被暂停/停止打断）。"""
        while True:
            with self._lock:
                now = time.monotonic()
                target = max(self._until, self._next_at) if self.slow else self._until
                remain = target - now
                if remain <= 0:
                    if self.slow:
                        self._next_at = now + self.SLOW_GAP
                    return
            self._gate(min(remain, 0.5))


HARD_FAILS = ("429", "403", "503", "Too Many Requests", "Connection", "timed out",
              "Timeout", "Reset", "reset", "Max retries", "ProxyError", "10054",
              "10061", "10065")


def hard_failure(err) -> bool:
    text = str(err)
    return any(k in text for k in HARD_FAILS)


class BaseFetcher:
    name = "base"

    MIN_DELAY = 0.5
    MAX_DELAY = 1.5

    stop_event = None
    pause_event = None

    def __init__(self):
        self._pace_lock = threading.Lock()
        self._last_request = 0.0
        self.min_interval = 0.0        # 由引擎按 --delay 设置
        self.breaker = Breaker(self.gate_wait)

    def pace(self):
        """把「相邻两次请求的最小间隔」落实到所有线程上。"""
        if getattr(self, "_pace_lock", None) is None:
            self._pace_lock = threading.Lock()
            self._last_request = 0.0
            if not hasattr(self, "min_interval"):
                self.min_interval = 0.0
        if self.min_interval <= 0:
            return
        with self._pace_lock:
            now = time.monotonic()
            wait = self._last_request + self.min_interval - now
            if wait > 0:
                self.gate_wait(wait)
            self._last_request = time.monotonic()

    def gate_wait(self, seconds: float):
        """等待 seconds 秒，期间实时响应暂停/停止。"""
        end = time.time() + seconds
        while True:
            if self.stop_event is not None and self.stop_event.is_set():
                raise SystemExit("用户停止")
            if self.pause_event is not None and self.pause_event.is_set():
                while self.pause_event.is_set():
                    if self.stop_event is not None and self.stop_event.is_set():
                        raise SystemExit("用户停止")
                    time.sleep(0.2)
            remain = end - time.time()
            if remain <= 0:
                return
            time.sleep(min(0.2, remain))

    def polite_sleep(self):
        """礼貌性随机延迟，模拟人类翻页（可被暂停/停止打断）。"""
        self.gate_wait(random.uniform(self.MIN_DELAY, self.MAX_DELAY))

    def fetch(self, url: str, quick: bool = False) -> str:
        """抓取页面。"""
        raise NotImplementedError

    def fetch_compat(self, url: str, quick: bool = False) -> str:
        """按 fetcher 的实际签名选择调用方式（供引擎统一使用）。"""
        fn = self.fetch
        t0 = time.monotonic()
        diag.emit("req.start", url=url[-90:], quick=quick, driver=self.name)
        try:
            if not quick:
                text = fn(url)
            else:
                try:
                    params = inspect.signature(fn).parameters
                except (TypeError, ValueError):
                    text = fn(url)
                else:
                    text = fn(url, quick=True) if "quick" in params else fn(url)
        except Exception as e:
            diag.emit("req.fail", url=url[-90:], driver=self.name,
                      ms=round((time.monotonic() - t0) * 1000), err=str(e)[:120])
            raise
        diag.emit("req.ok", url=url[-90:], driver=self.name,
                  ms=round((time.monotonic() - t0) * 1000), chars=len(text or ""))
        return text


@register("requests")


class RequestsFetcher(BaseFetcher):
    """默认驱动：requests + 会话复用 + 指数退避重试。"""

    name = "requests"
    RETRIES = 4
    TIMEOUT = 45

    def __init__(self):
        super().__init__()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": get_ua(),
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        proxy = get_proxy()
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
            self.session.trust_env = False
        else:
            self.session.trust_env = os.environ.get("NOVEL_DL_PROXY") == "1"
        jar = parse_cookie(get_cookie())
        if jar:
            self.session.cookies.update(jar)
            log.info("🍪 带上 %d 个 cookie", len(jar))
        self.insecure_hosts = set()    # 证书链不完整的站点，逐个放行

    def fetch(self, url: str, quick: bool = False) -> str:
        retries = 1 if quick else self.RETRIES
        last_err = None
        attempt = 0
        host = urlsplit(url).netloc
        while attempt < retries:
            attempt += 1
            try:
                r = self.session.get(
                    url, timeout=self.TIMEOUT,
                    verify=False if host in self.insecure_hosts else None)
                r.raise_for_status()
                if not quick:
                    self.polite_sleep()
                text, used = decode_body(r.content, r.encoding or "")
                log.debug("编码 %s -> %s", r.encoding, used)
                diag.emit("enc", url=url[-90:], bytes=len(r.content), enc=used,
                          declared=r.encoding or "", status=r.status_code)
                return text
            except Exception as e:
                last_err = e
                diag.emit("retry", url=url[-90:], try_no=attempt, err=str(e)[:120])
                if "CERTIFICATE_VERIFY_FAILED" in str(e) and host not in self.insecure_hosts:
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                    self.insecure_hosts.add(host)
                    log.warning("    ⚠ %s 的证书链不完整，只对该站点放行", host)
                    attempt -= 1
                    continue
                if attempt >= retries:
                    break
                wait = self._backoff(attempt)
                log.warning("    ⚠ 请求失败(%s)，%.1fs 后第 %d/%d 次重试",
                            e, wait, attempt, retries)
                self.gate_wait(wait)

        if quick:
            raise ConnectionError(f"请求失败: {url} ({last_err})")

        alt = requests.Session()
        alt.headers.update(self.session.headers)
        if self.session.proxies:
            raise ConnectionError(
                f"重试 {self.RETRIES} 次仍失败（代理 {list(self.session.proxies.values())[0]}）"
                f": {url} ({last_err})")
        alt.trust_env = True
        try:
            log.warning("    ⚠ 直连反复失败，尝试经系统代理访问…")
            r = alt.get(url, timeout=self.TIMEOUT)
            r.raise_for_status()
            self.polite_sleep()
            text, _ = decode_body(r.content, r.encoding or "")
            return text
        except Exception as e:
            last_err = e
        raise ConnectionError(f"重试 {self.RETRIES} 次仍失败: {url} ({last_err})")

    @classmethod
    def _backoff(cls, attempt: int) -> float:
        """指数退避 + 抖动。"""
        base = min(2 ** attempt, 6)
        return base * (0.75 + random.random() * 0.5)


CHALLENGE_MARKS = ("cf_chl_opt", "challenge-platform", "cf-challenge",
                   "Just a moment", "captcha.qq.com", "geetest",
                   "人机验证", "拖动滑块")


def _looks_challenged(html: str) -> bool:
    """这一页是不是反爬验证页（而不是真正的正文）。"""
    if not html:
        return False
    if len(html) > 200_000:          # 这么大的页面不可能是验证页
        return False
    low = html.lower()
    return any(m.lower() in low for m in CHALLENGE_MARKS)


@register("selenium")
class SeleniumFetcher(BaseFetcher):
    """插件驱动：处理 JS 动态加载 / 需要等待渲染的网站。"""

    name = "selenium"
    WAIT_SELECTOR = None   # 可按站点覆盖：等到某元素出现
    WAIT_SECONDS = 3

    def __init__(self, proxy: str | None = None, show: bool = False):
        super().__init__()
        try:
            import selenium  # noqa: F401
        except ImportError:
            raise SystemExit(
                'selenium 模式需要先安装：pip install "novel-dl[selenium]"')
        self.proxy = (proxy or get_proxy() or "").strip()
        self.show = bool(show)          # 看得见的浏览器：人机验证由人来点
        self._local = threading.local()
        atexit.register(self.close)

    def _build(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        opts = Options()
        if not self.show:
            opts.add_argument("--headless=new")
        else:
            # 明确摆到主屏中间，别开在屏幕外或被别的窗口盖住
            opts.add_argument("--window-position=120,80")
            opts.add_argument("--window-size=1280,900")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        opts.add_argument(f"user-agent={get_ua()}")
        # 让浏览器看起来就是普通 Chrome：自动化标记会让人机验证一直转圈、永远不出勾选框
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        if self.proxy:
            opts.add_argument(f"--proxy-server={self.proxy}")
        if self.show:
            log.warning("🌐 正在启动 Chrome，第一次可能要等一分钟，看到窗口再操作…")
        d = webdriver.Chrome(options=opts)
        try:
            d.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator,'webdriver',"
                           "{get:()=>undefined});"})
        except Exception as e:
            log.debug("  藏 automation 标记失败（不影响抓取）：%s", e)
        if self.show:
            try:
                d.switch_to.window(d.current_window_handle)   # 尽量把窗口拎到前面
                d.set_window_rect(x=120, y=80, width=1280, height=900)
            except Exception as e:
                log.debug("  摆窗口失败（不影响抓取）：%s", e)
            log.warning("✅ 浏览器窗口开好了。如果没看到，看一下任务栏的 Chrome。")
        return d

    def _driver(self):
        """每个线程复用一个浏览器实例，别每章都起一次 Chrome。"""
        d = getattr(self._local, "driver", None)
        if d is None:
            d = self._build()
            self._local.driver = d
        return d

    def close(self):
        d = getattr(self._local, "driver", None)
        if d is not None:
            try:
                d.quit()
            except Exception:
                pass
            self._local.driver = None

    def _cookie_once(self, driver, url: str):
        """cookie 要先进到域名下才能种；每个 host 只种一次。"""
        jar = parse_cookie(get_cookie())
        if not jar:
            return
        seen = getattr(self._local, "cookied", None)
        if seen is None:
            seen = set()
            self._local.cookied = seen
        parts = urlsplit(url)
        host = parts.netloc
        if host in seen:
            return
        driver.get(f"{parts.scheme}://{host}/")
        ok = 0
        for k, v in jar.items():
            try:
                driver.add_cookie({"name": k, "value": v})
                ok += 1
            except Exception as e:
                log.debug("   cookie %s 没种上：%s", k, e)
        seen.add(host)
        log.info("🍪 浏览器里种了 %d/%d 个 cookie", ok, len(jar))

    def fetch(self, url: str, quick: bool = False) -> str:
        driver = self._driver()
        self._cookie_once(driver, url)
        driver.get(url)
        if self.WAIT_SELECTOR:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            WebDriverWait(driver, 15).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, self.WAIT_SELECTOR))
        else:
            time.sleep(self.WAIT_SECONDS)
        html = driver.page_source
        if self.show and _looks_challenged(html):
            html = self._wait_human(driver)
        if not quick:
            self.polite_sleep()
        return html

    def _wait_human(self, driver, limit: float = 300.0) -> str:
        """浏览器可见时，等人在窗口里把验证点过去。程序不碰验证。"""
        mins = max(1, int(limit // 60))
        log.warning("\n🧑 这个站要人机验证，浏览器窗口已经打开。")
        log.warning("   请在窗口里点一下「Verify you are human」之类的勾选框；"
                    "页面一直转圈就再等几秒，最多等 %d 分钟。", mins)
        t0 = time.time()
        last_tip = t0
        while time.time() - t0 < limit:
            time.sleep(3)
            try:
                html = driver.page_source
            except Exception:
                break
            if not _looks_challenged(html):
                log.warning("✅ 验证过了，接着下（等了 %d 秒）。", int(time.time() - t0))
                return html
            now = time.time()
            if now - last_tip >= 20:
                last_tip = now
                left = int(limit - (now - t0))
                log.warning("   ⏳ 还在等验证：已等 %d 秒，还剩 %d 秒。"
                            "窗口别关，出现勾选框就点一下。",
                            int(now - t0), max(0, left))
        log.warning("⌛ 等满了还没通过验证。可以重跑一次，或者用 --cookie 贴浏览器的 Cookie。")
        try:
            return driver.page_source
        except Exception:
            return ""
