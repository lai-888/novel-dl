"""抓取层 —— 插件化设计。

所有抓取器实现同一个接口：
    fetch(url) -> str  (返回页面 HTML 文本)

通过注册表按名字切换：
    python -m novel_dl <url> --driver selenium

新增抓取策略 = 新增一个类 + 一行注册，不动任何其他代码。
"""
import inspect
import random
import time

import requests

from . import log

FETCHERS = {}


def register(name):
    def deco(cls):
        FETCHERS[name] = cls  # 只存类，懒实例化：未装 selenium 也不影响导入
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

    # —— 礼貌性延迟（反爬的最小尊重，也可被 --delay 覆盖）——
    MIN_DELAY = 0.5
    MAX_DELAY = 1.5

    # —— 外部控制（网页暂停/停止按钮注入）——
    stop_event = None
    pause_event = None

    def gate_wait(self, seconds: float):
        """等待 seconds 秒，期间实时响应暂停/停止。

        停止 → 立即 SystemExit；暂停 → 阻塞到恢复（恢复后继续本次等待）。
        """
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
        """抓取页面。

        quick=True：只请求一次，不重试。用于并发模式——单个线程不该为了
        一章卡 20 秒，重试交给引擎统一安排（见 engine._download_parallel）。

        兼容性说明：第三方 fetcher 只要实现 fetch(url) 即可。本方法会探测
        它是否支持 quick 参数，不支持就退回调用 fetch(url)，不让新增参数
        变成破坏性变更。
        """
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
        # 系统代理（Clash 等）常把国内小站的连接掐掉；默认直连更稳。
        # 需要走代理时设置环境变量 NOVEL_DL_PROXY=1。
        import os
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
                r.encoding = r.apparent_encoding or r.encoding
                if not quick:
                    self.polite_sleep()
                return r.text
            except Exception as e:
                last_err = e
                # 证书链不完整（Windows 信任但 Python CA 库缺中间证书）：
                # 对该会话关闭校验后重试，不消耗重试次数
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
            # 并发模式：不在单个线程里死磕，立刻抛给引擎，腾出线程
            raise ConnectionError(f"请求失败: {url} ({last_err})")

        # 最后手段：直连被重置时改走系统代理再试 1 次
        alt = requests.Session()
        alt.headers.update(self.session.headers)
        alt.trust_env = True
        try:
            log.warning("    ⚠ 直连反复失败，尝试经系统代理访问…")
            r = alt.get(url, timeout=self.TIMEOUT)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or r.encoding
            self.polite_sleep()
            return r.text
        except Exception as e:
            last_err = e
        raise ConnectionError(f"重试 {self.RETRIES} 次仍失败: {url} ({last_err})")

    @classmethod
    def _backoff(cls, attempt: int) -> float:
        """指数退避 + 抖动。

        抖动（jitter）很关键：并发抓取时，多个线程会同时收到限流响应，
        如果退避时间完全相同，它们会同时重试 —— 这在站点看来就是攻击，
        反而延长被封时间。加 0~50% 随机量把重试打散。

        上限也从 10s 降到 6s：章节失败后由引擎记账、下次运行重试，
        没必要在单次运行里死磕。
        """
        base = min(2 ** attempt, 6)
        return base * (0.75 + random.random() * 0.5)


@register("selenium")
class SeleniumFetcher(BaseFetcher):
    """插件驱动：处理 JS 动态加载 / 需要等待渲染的网站。

    依赖: pip install "novel-dl[selenium]"
    """

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
