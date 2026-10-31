"""下载引擎：目录解析 → 增量对比 → 逐章抓取 → 失败记账 → 合并 TXT。"""
import os
import re
import time

from . import log
from .fetchers import get_fetcher
from .guard import EncryptedContent
from .parser import get_parser
from .progress import Progress
from .rules import apply_url_fixes, find_rule

LINE = "=" * 40

CHAPTER_LIKE = re.compile(
    r"^(https?://[^/]+/(?:book|books|novel|xiaoshuo)/\d+)/\d+\.html?$")


class Engine:
    def __init__(self, book_url: str, driver="requests", out_dir="books",
                 from_ch=None, to_ch=None, min_delay=None,
                 backup_url=None, stop_event=None, pause_event=None,
                 max_toc_pages=200, workers=1, auto_detect=True,
                 proxy=None, on_toc=None):
        book_url = (book_url or "").strip()
        if not book_url:
            raise SystemExit("没有提供小说目录页网址")

        self.rule = find_rule(book_url)
        book_url = apply_url_fixes(book_url, self.rule)
        book_url = self._coerce_toc_url(book_url)

        self.book_url = book_url
        if proxy:
            from .fetchers import set_proxy
            set_proxy(proxy)
        self.fetcher = get_fetcher(driver)
        if min_delay is not None:
            self.fetcher.MIN_DELAY = min_delay
            self.fetcher.MAX_DELAY = max(min_delay * 3, min_delay)
        self.fetcher.min_interval = max(0.0, float(min_delay or 0))
        self.parser = get_parser(book_url, fetcher=self.fetcher,
                                 lazy_auto=auto_detect)
        self.prog = Progress(out_dir, book_url)
        self.from_ch, self.to_ch = from_ch, to_ch
        self.max_toc_pages = max_toc_pages
        self.workers = max(1, int(workers or 1))
        self.on_toc = on_toc
        self._encryption_checked = False   # 字体加密只检测一次（首章）
        self.blocked = None
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
        """用户常直接贴某一章的网址，这里猜回目录页。"""
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
    def _try_github_pages(self):
        """如果是 GitHub 仓库型站点，就地下载并返回结果；否则返回 None。"""
        m = re.match(r"https?://([^/:]+)", self.book_url or "")
        host = m.group(1).lower() if m else ""
        is_pages_host = host.endswith(".github.io") or host == "github.io"
        if not is_pages_host:
            return None

        try:
            html = self._get(self.book_url)
        except Exception:
            return None

        from .githubpages import detect_github_pages
        info = detect_github_pages(html, self.book_url)
        if not info:
            return None

        log.info("检测到 GitHub 仓库型站点，改用专用流程")
        from .ghpages_dl import download as gh_download

        def on_toc_cb(book, total, already, to_fetch):
            if self.on_toc is not None:
                try:
                    self.on_toc(book, total, already, to_fetch)
                except Exception:
                    pass

        paths = gh_download(
            self.book_url,
            out_dir=os.path.dirname(os.path.dirname(self.prog.state_path)),
            fetcher_headers=self.fetcher.session.headers
            if hasattr(self.fetcher, "session") else None,
            stop_event=self.stop_event,
            pause_event=self.pause_event,
            on_toc=on_toc_cb,
        )
        return paths[0] if paths else ""

    def refresh_toc(self) -> tuple[str, list]:
        try:
            html = self._get(self.book_url)
        except Exception as e:
            if not self.backup_url:
                raise
            log.warning("主站目录抓取失败（%s），改用备用网址：%s", e, self.backup_url)
            self.book_url = self.backup_url
            self.backup_url = None
            html = self._get(self.book_url)

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
        """目录分页：优先用 <option value> 拿全部页，否则顺着「下一页」跟。"""
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
                    html = self._get(nxt)
                except Exception as e:
                    log.warning("目录翻页失败（%s），使用已获取部分", e)
                    break
                nxt = self._next_toc_page(html)

        for nxt in pages[: self.max_toc_pages]:
            try:
                html2 = self._get(nxt)
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
            html = self._get(self.backup_url)
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
        """合法的下一页 = 当前 URL 的分页序号 +1。"""
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
        """提取 (下一页URL, 链接文字)。"""
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

        gh = self._try_github_pages()
        if gh is not None:
            return gh

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

        p.recover_from_disk()
        targets = [i for i in range(lo, hi) if not p.is_done(i)]

        if self.on_toc is not None:
            try:
                self.on_toc(title, len(toc), len(p.state.get("done", [])),
                            len(targets))
            except Exception as e:
                log.warning("进度回调失败（不影响下载）：%s", e)

        try:
            done_now, dead = self._download_loop(toc, targets)
        except KeyboardInterrupt:
            log.info("\n⏸ 收到 Ctrl+C —— 已停止（进度已保存，重跑同命令可续传）")
            done_now, dead = 0, []
        except SystemExit as e:
            log.info("\n⏹ 已停止：%s（进度已保存，重跑同命令可续传）", e)
            done_now, dead = 0, []
        except EncryptedContent as e:
            from .guard import explain
            self.blocked = e
            log.error("")
            log.error("=" * 52)
            log.error("这个网站无法下载")
            log.error("=" * 52)
            for line in explain(e).splitlines():
                log.error("%s", line)
            log.error("")
            log.error("已下载成功的章节会保留（本次新增 0 章）。")
            p.flush()
            raise SystemExit(2)
        finally:
            p.flush()   # 批量落盘模式下的兜底：异常退出也不丢账本

        log.info("\n本轮新下载 %d 章。%s", done_now, p.summary())
        if dead:
            log.info("☠ 本轮阵亡名单：")
            for no, t, why in dead[:20]:
                log.info("   第%d章 %s: %s", no, t, why)
            if len(dead) > 20:
                log.info("   … 其余 %d 章见 state.json", len(dead) - 20)
            self._explain_failures(dead, len(toc))
        prev_n = len(p.state.get("preview") or {})
        if prev_n:
            log.warning("")
            log.warning("⚠ 有 %d 章只拿到「免费预览」，不是完整正文。", prev_n)
            log.warning("   这些是站点的付费章节：没订阅就只能取到开头一小段。")
            log.warning("   书架里会给它们标成「付费预览」，免得你以为下全了。")
            log.warning("   订阅之后想重下这几章：删掉 book 目录 cache/ 里对应的 "
                        "NNNNN.txt 再跑同一条命令。")
        return self.export(title, toc)

    def _explain_failures(self, dead, total):
        """失败时给出可执行的原因判断，而不是只说「失败」。"""
        if not dead or total <= 0:
            return
        ratio = len(dead) / total
        reasons = " ".join(str(d[2]) for d in dead[:10]).lower()
        banned_hint = any(k in reasons for k in
                          ("reset", "10054", "connection", "timed out",
                           "403", "429", "forbidden", "too many"))
        if ratio >= 0.5 and banned_hint:
            log.error("")
            log.error("⚠ 大部分章节都失败了，很可能被该网站限流或临时封禁。")
            log.error("   建议：把并发降到 1、把每章间隔调大，并等十几分钟再试。")
            log.error("   命令示例：novel-dl <同样的网址> -w 1 --delay 3")
            log.error("   已下载的章节不会丢，恢复后重跑只补缺的部分。")
        elif ratio >= 0.5:
            log.error("")
            log.error("⚠ 多个章节解析失败。可能是网站改版或需要专用解析器。")
            log.error("   可以试试：novel-dl <网址> --driver selenium")

    def _get(self, url: str, quick: bool = False) -> str:
        """统一抓取入口：先限速，再抓。"""
        self.fetcher.pace()
        return self.fetcher.fetch_compat(url, quick=quick)

    def _fetch_chapter(self, i, toc, quick=False):
        """抓取并解析单章。纯函数式：不改任何共享状态，供并发调用。"""
        chap_title, url = toc[i]
        try:
            html = self._get(url, quick=quick)
        except Exception as e_primary:
            b_url = self._backup_url_for(i, chap_title)
            if not b_url:
                raise
            log.info("    ↪ 主站失败（%s），改用备站：%s",
                     str(e_primary)[:60], b_url)
            html = self._get(b_url, quick=quick)
            url = b_url

        first_html = html
        if not self._encryption_checked:
            from .guard import check_encrypted
            check_encrypted(html, "", url)
            self._encryption_checked = True

        try:
            c_title, body = self.parser.parse_chapter(html)
        except Exception as e_short:
            if "过短" not in str(e_short):
                raise
            log.info("    ↺ 解析出的正文过短，再请求一次")
            html = self._get(url)
            first_html = html
            c_title, body = self.parser.parse_chapter(html)

        page_url, pages = url, 1
        while pages < 30:
            nxt = self._next_page_link(html, page_url)
            if not nxt or not self._is_next_page(page_url, nxt[0]):
                break
            pages += 1
            page_url = nxt[0]
            html = self._get(page_url)
            _, body2 = self.parser.parse_chapter(html)
            body += "\n" + body2

        text = f"{c_title}\n\n{body}"

        from .guard import FillerPage, filler_reason, looks_truncated, preview_reason

        fr = filler_reason(body)
        if fr:
            raise FillerPage(fr)

        if pages == 1 and looks_truncated(body):
            log.info("    ↺ 正文只有 %d 字且未收尾，再请求一次", len(body))
            try:
                html2 = self._get(url)
                c2, body2 = self.parser.parse_chapter(html2)
                if len(body2) > len(body):
                    c_title = c2 or c_title
                    body = body2
                    text = f"{c_title}\n\n{body}"
                    first_html = html2
            except Exception as e:
                log.debug("补抓失败（保留第一次的结果）：%s", e)

        return text, c_title, preview_reason(first_html, text)

    def _download_loop(self, toc, targets):
        """按 workers 并发抓取。"""
        if self.workers <= 1:
            return self._download_serial(toc, targets)
        return self._download_parallel(toc, targets)

    def _record_success(self, i, text, c_title, toc, stats, preview=None):
        self.prog.write_chapter(i, text)
        self.prog.mark_done(i)
        if preview:
            self.prog.mark_preview(i, preview)
            stats["preview"] = stats.get("preview", 0) + 1
        stats["done_now"] += 1
        stats["conn_fail_streak"] = 0
        if preview:
            log.info("  ✓ [%d/%d] %s  ⚠ 仅预览", i + 1, len(toc), c_title)
        else:
            log.info("  ✓ [%d/%d] %s", i + 1, len(toc), c_title)

    def _record_failure(self, i, toc, err, dead, stats):
        self.prog.mark_failed(i, str(err))
        dead.append((i + 1, toc[i][0], str(err)))
        log.warning("  ☠ [%d] %s —— 跳过（%s），已记录在案",
                    i + 1, toc[i][0], str(err)[:80])
        if any(k in str(err) for k in ("Connection", "timed out", "Reset",
                                       "Max retries", "SSL", "10054", "10061")):
            stats["conn_fail_streak"] += 1
        else:
            stats["conn_fail_streak"] = 0

    def _download_serial(self, toc, targets):
        p = self.prog
        dead, stats = [], {"done_now": 0, "conn_fail_streak": 0, "preview": 0}
        last_target = targets[-1] if targets else None

        for i in targets:
            self._gate()
            try:
                text, c_title, preview = self._fetch_chapter(i, toc)
                self._record_success(i, text, c_title, toc, stats, preview)
            except EncryptedContent:
                raise
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
        """并发抓取，串行落盘。"""
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

        p = self.prog
        dead, stats = [], {"done_now": 0, "conn_fail_streak": 0, "preview": 0}
        window = max(self.workers * 4, self.workers)
        log.info("⚡ 并发抓取：%d 线程，窗口 %d", self.workers, window)

        retry = []
        last_error = {}          # 每章最后一次的真实异常，用于最终记账

        if targets and not self._encryption_checked:
            i0 = targets[0]
            try:
                self._gate()
                text, c_title, preview = self._fetch_chapter(i0, toc)
                self._record_success(i0, text, c_title, toc, stats, preview)
                targets = targets[1:]
            except EncryptedContent:
                raise
            except Exception:
                pass    # 网络抖动之类：留给下面的并发批次去重试

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
                            text, c_title, preview = fut.result()
                            self._record_success(i, text, c_title, toc, stats, preview)
                        except EncryptedContent:
                            for f in inflight:
                                f.cancel()
                            raise
                        except Exception as e:
                            last_error[i] = e
                            if quick:
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
            for i in retry:
                reason = last_error.get(i) or "重试后仍失败"
                self._record_failure(i, toc, reason, dead, stats)

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
