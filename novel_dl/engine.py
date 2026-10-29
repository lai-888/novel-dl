"""下载引擎 —— 编排一切：目录解析 → 增量对比 → 逐章抓取 → 战术跳过 → 合并 TXT。

引擎只负责流程，不负责「怎么抓」和「怎么解析」：
    抓取策略  -> fetchers.py 的插件
    解析策略  -> parser.py 的插件 / 站点规则
    进度账本  -> progress.py
所有输出都走 log，唯一例外是章节完成的进度提示（webui 会把它当状态源）。
"""
import os
import re
import time

from . import log
from .fetchers import get_fetcher
from .parser import get_parser
from .progress import Progress
from .rules import apply_url_fixes, find_rule

LINE = "=" * 40

# 「/book/123/456.html」这类章节页 → 猜回目录页 /book/123/。
# 只认这几个常见路径词，宁可猜不到也不要把目录页改坏。
CHAPTER_LIKE = re.compile(
    r"^(https?://[^/]+/(?:book|books|novel|xiaoshuo)/\d+)/\d+\.html?$")


class Engine:
    def __init__(self, book_url: str, driver="requests", out_dir="books",
                 from_ch=None, to_ch=None, min_delay=None,
                 backup_url=None, stop_event=None, pause_event=None,
                 max_toc_pages=200, workers=1):
        book_url = (book_url or "").strip()
        if not book_url:
            raise SystemExit("没有提供小说目录页网址")

        self.rule = find_rule(book_url)
        book_url = apply_url_fixes(book_url, self.rule)
        book_url = self._coerce_toc_url(book_url)

        self.book_url = book_url
        self.fetcher = get_fetcher(driver)
        if min_delay is not None:
            self.fetcher.MIN_DELAY = min_delay
            self.fetcher.MAX_DELAY = max(min_delay * 3, min_delay)
        self.parser = get_parser(book_url)
        self.prog = Progress(out_dir, book_url)
        self.from_ch, self.to_ch = from_ch, to_ch
        self.max_toc_pages = max_toc_pages
        self.workers = max(1, int(workers or 1))
        self.backup_url = (backup_url or "").strip() or None
        self.backup_toc = None
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.fetcher.stop_event = stop_event
        self.fetcher.pause_event = pause_event

        log.info("抓取驱动：%s ｜ 解析器：%s", self.fetcher.name, self.parser.name)

    # ---------- 网址纠错 ----------
    @staticmethod
    def _coerce_toc_url(url: str) -> str:
        """用户常直接贴某一章的网址，这里猜回目录页。

        只对 /book/123/456.html 这类「纯数字书 ID」形态生效，
        避免误伤 /read-1.html 这种本身就是目录的站点。
        """
        m = CHAPTER_LIKE.match(url)
        if not m:
            return url
        fixed = m.group(1) + "/"
        log.info("检测到章节页网址，已改用目录页：%s", fixed)
        return fixed

    # ---------- 暂停 / 停止 ----------
    def _gate(self):
        """暂停时阻塞（不消耗请求），停止时抛 SystemExit。"""
        if self.stop_event is not None and self.stop_event.is_set():
            raise SystemExit("用户停止")
        if self.pause_event is not None:
            warned = False
            while self.pause_event.is_set():
                if not warned:
                    log.info("⏸ 已暂停（点「继续」恢复）")
                    warned = True
                if self.stop_event is not None and self.stop_event.is_set():
                    raise SystemExit("用户停止")
                time.sleep(0.3)
            if warned:
                log.info("▶ 已继续")

    # ---------- 目录 ----------
    def refresh_toc(self) -> tuple[str, list]:
        try:
            html = self.fetcher.fetch(self.book_url)
        except Exception as e:
            if not self.backup_url:
                raise
            log.warning("主站目录抓取失败（%s），改用备用网址：%s", e, self.backup_url)
            self.book_url = self.backup_url
            self.backup_url = None
            html = self.fetcher.fetch(self.book_url)

        title, toc = self.parser.parse_toc(html, self.book_url)
        toc = self._extend_toc_pages(toc, html)

        old_n = len(self.prog.state.get("toc", []))
        new_idx = self.prog.set_book(self.book_url, title, toc)
        if new_idx and old_n:
            log.info("🆕 检测到更新：新增 %d 章（第 %d 章起）",
                     len(new_idx), new_idx[0] + 1)

        if self.backup_url:
            self._load_backup_toc(toc)
        return title, toc

    def _extend_toc_pages(self, toc: list, html: str) -> list:
        """目录分页：优先用 <option value> 拿全部页，否则顺着「下一页」跟。

        上限 max_toc_pages，防止站点给出无穷页码把程序拖死。
        """
        seen = {u for _, u in toc}
        base_m = re.match(r"(https?://[^/]+)/", self.book_url)
        options = [u for u in re.findall(r'<option value="([^"]+)"', html) if u != "/"]

        pages, page_no = [], 2
        if options and base_m:
            pages = [base_m.group(1) + u for u in options]
            pages = [u for u in pages if u.rstrip("/") != self.book_url.rstrip("/")]
            if pages:
                log.info("目录共约 %d 页，开始逐页抓取…", len(pages) + 1)
        else:
            nxt = self._next_toc_page(html)
            while nxt and nxt not in seen and len(pages) < self.max_toc_pages:
                pages.append(nxt)
                try:
                    html = self.fetcher.fetch(nxt)
                except Exception as e:
                    log.warning("目录翻页失败（%s），使用已获取部分", e)
                    break
                nxt = self._next_toc_page(html)

        for nxt in pages[: self.max_toc_pages]:
            try:
                html2 = self.fetcher.fetch(nxt)
                _, more = self.parser.parse_toc(html2, nxt)
            except Exception as e:
                log.warning("目录第 %d 页抓取失败（%s），使用已获取部分", page_no, e)
                break
            fresh = [(t, u) for t, u in more if u not in seen]
            if not fresh:
                log.warning("目录第 %d 页无新章节，停止翻页", page_no)
                break
            toc.extend(fresh)
            seen.update(u for _, u in fresh)
            if page_no % 10 == 0:
                log.info("已抓目录 %d 页，累计 %d 章", page_no, len(toc))
            page_no += 1
        return toc

    @staticmethod
    def _next_toc_page(html: str):
        """从目录页提取「下一页」链接（同站相对地址会拼成绝对地址）。"""
        m = re.search(r'<a[^>]+href="([^"]+)"[^>]*>\s*下一页\s*</a>', html)
        if not m:
            return None
        href = m.group(1)
        if href.startswith("http"):
            return href
        bm = re.search(r"(https?://[^/]+/)", html)
        if not bm:
            return None
        from urllib.parse import urljoin
        return urljoin(bm.group(1), href)

    def _load_backup_toc(self, main_toc: list):
        """备用网址（镜像站）：尽力而为，失败不影响主流程。"""
        try:
            html = self.fetcher.fetch(self.backup_url)
            _, btoc = self.parser.parse_toc(html, self.backup_url)
        except Exception as e:
            log.warning("备用站点目录加载失败（%s），仅使用主站", e)
            return
        if len(btoc) >= max(10, int(len(main_toc) * 0.5)):
            self.backup_toc = btoc
            log.info("🔁 已加载备用站点目录：%d 章（主站某章失败时自动改用）", len(btoc))
        else:
            log.warning("备用站点目录章数过少（%d），忽略该网址", len(btoc))

    def _backup_url_for(self, idx: int, title: str):
        """按序号或标题匹配，找到备用站的同一章。"""
        if not self.backup_toc:
            return None
        if len(self.backup_toc) == len(self.prog.state.get("toc", [])):
            if idx < len(self.backup_toc):
                return self.backup_toc[idx][1]

        def norm(s: str) -> str:
            s = re.sub(r"^第[0-9零一二三四五六七八九十百千万两]+[章节回幕话卷]", "", s)
            return re.sub(r"[\s\-_（()）。，,.、！!？?：:]", "", s)

        want = norm(title)
        if want:
            for t, u in self.backup_toc:
                if norm(t) == want:
                    return u
        return self.backup_toc[idx][1] if idx < len(self.backup_toc) else None

    # ---------- 章节内分页 ----------
    @staticmethod
    def _is_next_page(cur: str, nxt: str) -> bool:
        """合法的下一页 = 当前 URL 的分页序号 +1。

        支持 /ID-2 与 /ID_2 两种写法，忽略 .html 后缀；
        跳到下一章、外部站、广告页都判为 False。
        """
        def norm(u: str) -> str:
            u = u.split("?")[0].split("#")[0].rstrip("/")
            return re.sub(r"\.html?$", "", u)

        cn, nn = norm(cur), norm(nxt)
        m = re.search(r"[-_](\d+)$", cn)
        base = re.sub(r"[-_]\d+$", "", cn)
        n = int(m.group(1)) if m else 1
        return nn in (f"{base}_{n + 1}", f"{base}-{n + 1}")

    @staticmethod
    def _next_page_link(html: str, base_url: str = ""):
        """提取 (下一页URL, 链接文字)。

        有的站点同一页上有多个 id=next_url 的链接（一个是「上一页」），
        所以收集全部候选后，优先返回真正构成「当前页 +1」的那个。
        """
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin

        soup = BeautifulSoup(html, "lxml")
        cands = []
        for a in soup.find_all("a", href=True):
            if a["href"].startswith("javascript"):
                continue
            aid = a.get("id") or ""
            txt = a.get_text(strip=True)
            if aid in ("next_url", "next_page", "pager_next") or "下一页" in txt:
                cands.append((urljoin(base_url, a["href"]), txt))
        if not cands:
            return None
        if base_url:
            for u, t in cands:
                if Engine._is_next_page(base_url, u):
                    return u, t
        return cands[-1]

    # ---------- 主流程 ----------
    def run(self) -> str:
        p = self.prog
        resuming = bool(p.state["done"])
        if resuming:
            log.info("📖 发现历史进度 —— %s", p.summary())

        title, toc = self.refresh_toc()
        if resuming:
            last = p.state.get("last_chapter_no")
            if last and last >= len(toc):
                hint = "全书已追平；网站更新后重跑同命令即可增量下载"
            elif last:
                hint = f"本次从第 {last + 1} 章继续"
            else:
                hint = "本次继续"
            log.info("   断点续传中，已完成章节不会重复下载；%s。", hint)
        else:
            log.info("📚 《%s》共 %d 章", title, len(toc))

        lo = (self.from_ch - 1) if self.from_ch else 0
        hi = min(self.to_ch, len(toc)) if self.to_ch else len(toc)

        # 账本是批量落盘的，断电后可能落后于磁盘：先按缓存文件补齐记录，
        # 否则会把已经下好的章节再请求一遍。
        p.recover_from_disk()
        targets = [i for i in range(lo, hi) if not p.is_done(i)]

        try:
            done_now, dead = self._download_loop(toc, targets)
        except KeyboardInterrupt:
            log.info("\n⏸ 收到 Ctrl+C —— 已停止（进度已保存，重跑同命令可续传）")
            done_now, dead = 0, []
        except SystemExit as e:
            log.info("\n⏹ 已停止：%s（进度已保存，重跑同命令可续传）", e)
            done_now, dead = 0, []
        finally:
            p.flush()   # 批量落盘模式下的兜底：异常退出也不丢账本

        log.info("\n本轮新下载 %d 章。%s", done_now, p.summary())
        if dead:
            log.info("☠ 本轮阵亡名单：")
            for no, t, why in dead:
                log.info("   第%d章 %s: %s", no, t, why)
        return self.export(title, toc)

    def _fetch_chapter(self, i, toc, quick=False):
        """抓取并解析单章。纯函数式：不改任何共享状态，供并发调用。

        quick=True 时只试一次（不重试）：并发模式下由引擎负责安排重试，
        否则一个失败章会占住一个线程 20 多秒，其它章节干等。

        返回 (正文文本, 章节标题)。失败抛异常，由调用方决定怎么记账。
        """
        chap_title, url = toc[i]
        try:
            html = self.fetcher.fetch_compat(url, quick=quick)
        except Exception as e_primary:
            b_url = self._backup_url_for(i, chap_title)
            if not b_url:
                raise
            log.info("    ↪ 主站失败（%s），改用备站：%s",
                     str(e_primary)[:60], b_url)
            html = self.fetcher.fetch_compat(b_url, quick=quick)
            url = b_url

        c_title, body = self.parser.parse_chapter(html)

        # 章节内分页跟随（/ID、/ID-2、/ID_2 …），上限 30 页
        page_url, pages = url, 1
        while pages < 30:
            nxt = self._next_page_link(html, page_url)
            if not nxt or not self._is_next_page(page_url, nxt[0]):
                break
            pages += 1
            page_url = nxt[0]
            html = self.fetcher.fetch(page_url)
            _, body2 = self.parser.parse_chapter(html)
            body += "\n" + body2
        return f"{c_title}\n\n{body}", c_title

    def _download_loop(self, toc, targets):
        """按 workers 并发抓取。

        并发安全的关键：抓取/解析是纯的（不碰共享状态），落盘和记账回到
        主线程串行做。章节正文每章立刻写盘，账本按批次落盘。
        """
        if self.workers <= 1:
            return self._download_serial(toc, targets)
        return self._download_parallel(toc, targets)

    def _record_success(self, i, text, c_title, toc, stats):
        self.prog.write_chapter(i, text)
        self.prog.mark_done(i)
        stats["done_now"] += 1
        stats["conn_fail_streak"] = 0
        log.info("  ✓ [%d/%d] %s", i + 1, len(toc), c_title)

    def _record_failure(self, i, toc, err, dead, stats):
        self.prog.mark_failed(i, str(err))
        dead.append((i + 1, toc[i][0], str(err)))
        log.warning("  ☠ [%d] %s —— 战术跳过（%s），已记录在案",
                    i + 1, toc[i][0], str(err)[:80])
        if any(k in str(err) for k in ("Connection", "timed out", "Reset",
                                       "Max retries", "SSL", "10054", "10061")):
            stats["conn_fail_streak"] += 1
        else:
            stats["conn_fail_streak"] = 0

    def _download_serial(self, toc, targets):
        p = self.prog
        dead, stats = [], {"done_now": 0, "conn_fail_streak": 0}
        last_target = targets[-1] if targets else None

        for i in targets:
            self._gate()
            try:
                text, c_title = self._fetch_chapter(i, toc)
                self._record_success(i, text, c_title, toc, stats)
            except Exception as e:
                self._record_failure(i, toc, e, dead, stats)
                if stats["conn_fail_streak"] >= 5:
                    log.error("\n⛔ 连续 5 章连接失败，判定站点/线路暂时不可达，自动停止。")
                    log.error("   进度已保存：网络恢复后重跑同一命令即可续传。")
                    break
            if (i + 1) % 10 == 0 or i == last_target:
                log.info("    （进度 %d/%d，可暂停/停止）",
                         len(p.state["done"]), len(toc))
                p.flush()
        return stats["done_now"], dead

    def _download_parallel(self, toc, targets):
        """并发抓取，串行落盘。

        两阶段设计（这是并发下不被「坏章拖死」的关键）：
          阶段 1 —— 每题只试一次（quick），失败立即跳过并记入 retry 列表。
                    这样线程池不会被某个卡住的章节占住 20 秒。
          阶段 2 —— 所有章节扫完后，对 retry 列表做少量轮次的重试，
                    轮次之间整体等待，天然带退避。

        用「滑动窗口」而不是一次性提交全部：窗口大小 = workers * 4，
        既能喂饱线程池，又不会因为几千个任务导致内存膨胀，
        也保证停止信号能及时生效。
        """
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

        p = self.prog
        dead, stats = [], {"done_now": 0, "conn_fail_streak": 0}
        window = max(self.workers * 4, self.workers)
        log.info("⚡ 并发抓取：%d 线程，窗口 %d", self.workers, window)

        retry = []

        def run_batch(indices, quick):
            """跑一批。成功的直接落盘，失败的根据 quick 决定进 retry 还是记死。"""
            queue = list(indices)
            finished = 0
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                inflight = {}
                while queue and len(inflight) < window:
                    i = queue.pop(0)
                    inflight[pool.submit(self._fetch_chapter, i, toc, quick)] = i

                while inflight:
                    done_set, _ = wait(inflight, return_when=FIRST_COMPLETED)
                    for fut in done_set:
                        i = inflight.pop(fut)
                        try:
                            text, c_title = fut.result()
                            self._record_success(i, text, c_title, toc, stats)
                        except Exception as e:
                            if quick:
                                # 阶段 1：先记账但不判死，留给阶段 2 重试
                                retry.append(i)
                            else:
                                self._record_failure(i, toc, e, dead, stats)
                        finished += 1

                        stopping = (self.stop_event is not None
                                    and self.stop_event.is_set())
                        if queue and not stopping and stats["conn_fail_streak"] < 5:
                            j = queue.pop(0)
                            inflight[pool.submit(self._fetch_chapter, j, toc, quick)] = j

                        if finished % 10 == 0:
                            log.info("    （进度 %d/%d）", finished, len(indices))
                            p.flush()

                    if stats["conn_fail_streak"] >= 5:
                        for f in inflight:
                            f.cancel()
                        return "circuit_break"

                    try:
                        self._gate()
                    except SystemExit:
                        for f in inflight:
                            f.cancel()
                        raise
            return "ok"

        status = run_batch(targets, quick=True)

        # 阶段 2：统一重试。
        # 全程保持 quick —— 退避由引擎在「轮次之间」统一控制，而不是在
        # 单个线程里 sleep，这样即使只有 1 个失败章，也不会拖住整批的收尾。
        if retry and status == "ok":
            for rnd, wait_s in enumerate((0.8, 2.0, 4.0), start=1):
                if not retry:
                    break
                log.info("🔁 第 %d 轮重试：%d 章待补", rnd, len(retry))
                p.flush()
                self.fetcher.gate_wait(wait_s)
                batch, retry[:] = list(retry), []
                if run_batch(batch, quick=True) != "ok":
                    break
            # 还没成功的，如实记账
            for i in retry:
                self._record_failure(i, toc, "重试后仍失败", dead, stats)

        if status == "circuit_break":
            log.error("\n⛔ 连续 5 章连接失败，判定站点/线路暂时不可达，自动停止。")
            log.error("   进度已保存：网络恢复后重跑同一命令即可续传。")

        p.flush()
        return stats["done_now"], dead

    # ---------- 合并 TXT ----------
    def export(self, title, toc) -> str:
        p = self.prog
        safe = (title or "未命名").replace("/", "_").replace("\\", "_")
        out = os.path.join(os.path.dirname(p.state_path), f"{safe}.txt")
        written = 0
        with open(out, "w", encoding="utf-8") as f:
            f.write(f"《{title}》\n来源: {self.book_url}\n"
                    f"导出时间: {time.strftime('%Y-%m-%d %H:%M')}\n\n")
            for i, (_, _u) in enumerate(toc):
                if not p.is_done(i):
                    continue
                body = p.read_chapter(i)
                if not body:
                    continue
                head, _, rest = body.partition("\n\n")
                if re.match(r"^第[0-9零一二三四五六七八九十百千]+[章节回卷]", head):
                    f.write(f"=== {head} ===\n\n")
                else:
                    f.write(f"=== 第{i + 1}章 {head} ===\n\n")
                f.write(rest.strip() + "\n\n\n")
                written += 1
        log.info("📦 已合并 %d 章到 TXT：%s", written, out)
        if p.state["failed"]:
            log.warning("   ⚠ 仍有 %d 章阵亡，下次运行会自动重试它们。",
                        len(p.state["failed"]))
        return out
