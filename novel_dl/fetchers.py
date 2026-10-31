"""抓取层 —— 插件化设计。"""
import inspect
import os
import random
import threading
import time

import requests

from . import log
from .encoding import decode_body

FETCHERS = {}

_PROXY: str | None = None


def set_proxy(url: str | None):
    """设置所有 requests 请求走的代理；传空表示「不显式指定」。"""
    global _PROXY
    u = (url or "").strip()
    _PROXY = u or None


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
    return FETCHERS[name](**kw)


def list_fetchers() -> list[str]:
    return sorted(FETCHERS)


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
        if not quick:
            return fn(url)
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return fn(url)
        if "quick" in params:
            return fn(url, quick=True)
        return fn(url)


@register("requests")


class RequestsFetcher(BaseFetcher):
    """默认驱动：requests + 会话复用 + 指数退避重试。"""

    name = "requests"
    RETRIES = 4
    TIMEOUT = 45

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        proxy = get_proxy()
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
            self.session.trust_env = False
        else:
            self.session.trust_env = os.environ.get("NOVEL_DL_PROXY") == "1"

    def fetch(self, url: str, quick: bool = False) -> str:
        retries = 1 if quick else self.RETRIES
        last_err = None
        attempt = 0
        while attempt < retries:
            attempt += 1
            try:
                r = self.session.get(url, timeout=self.TIMEOUT)
                r.raise_for_status()
                if not quick:
                    self.polite_sleep()
                text, used = decode_body(r.content, r.encoding or "")
                log.debug("编码 %s -> %s", r.encoding, used)
                return text
            except Exception as e:
                last_err = e
                if "CERTIFICATE_VERIFY_FAILED" in str(e) and self.session.verify is not False:
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                    self.session.verify = False
                    log.warning("    ⚠ 证书验证失败，已对该站点禁用证书校验后重试")
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


@register("selenium")


class SeleniumFetcher(BaseFetcher):
    """插件驱动：处理 JS 动态加载 / 需要等待渲染的网站。"""

    name = "selenium"
    WAIT_SELECTOR = None   # 可按站点覆盖：等到某元素出现
    WAIT_SECONDS = 3

    def __init__(self):
        try:
            import selenium  # noqa: F401
        except ImportError:
            raise SystemExit(
                'selenium 模式需要先安装：pip install "novel-dl[selenium]"')

    def _build(self):
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36")
        return webdriver.Chrome(options=opts)

    def fetch(self, url: str) -> str:
        driver = self._build()
        try:
            driver.get(url)
            if self.WAIT_SELECTOR:
                from selenium.webdriver.common.by import By
                from selenium.webdriver.support.ui import WebDriverWait
                WebDriverWait(driver, 15).until(
                    lambda d: d.find_elements(By.CSS_SELECTOR, self.WAIT_SELECTOR))
            else:
                time.sleep(self.WAIT_SECONDS)
            html = driver.page_source
            self.polite_sleep()
            return html
        finally:
            driver.quit()
