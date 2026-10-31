"""断点续传状态管理。

设计哲学：最优雅的进度保存 = 「每章即下即落盘」+ 一个轻量 JSON 账本。
- 章节正文逐章写入 cache/ 目录（一章一个 .txt），原子写入，不怕中途断电。
- state.json 是账本：书名、目录快照、已完成章节、失败章节、上次中断位置。
- 恢复 = 读账本跳过已完成章节。零重复请求。

这是本项目最值得看的部分：不依赖任何数据库，靠「一文件一章 + 账本对比」
就能同时获得断点续传、增量更新、目录改版后的进度迁移。
"""
import json
import os
import re
import threading
import time

from . import log

STATE_VERSION = 1


def _slug(s: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", s).strip("_")
    return s[:60] or "book"


class Progress:
    def __init__(self, root: str, book_url: str):
        self.root = os.path.join(root, _slug(book_url))
        self.cache_dir = os.path.join(self.root, "cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.state_path = os.path.join(self.root, "state.json")
        self.state = self._load()
        self._dirty = False     # 账本有未落盘的改动
        self._pending = 0       # 距上次落盘又过了多少章
        self._lock = threading.RLock()   # 并发落盘必须互斥，否则 tmp 文件会撞车

    # ---------- 账本读写 ----------
    def _load(self) -> dict:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, encoding="utf-8") as f:
                    state = json.load(f)
                if isinstance(state, dict):
                    state.setdefault("failed", [])
                    state.setdefault("fail_log", {})
                    state.setdefault("preview", {})
                    return state
            except Exception as e:
                log.warning("账本损坏，将重建（缓存章节仍在）：%s", e)
        return {"version": STATE_VERSION, "title": None, "book_url": None,
                "toc": [], "done": [], "failed": [], "fail_log": {},
                "preview": {}, "last_chapter_no": None, "updated_at": None}

    def set_book(self, book_url, title, toc):
        """记录目录快照，返回「新增章节索引」列表（增量下载的依据）。

        目录结构变化时（解析规则升级 / 网站改版），按章节 URL 重映射
        已有进度并迁移缓存文件，避免索引错位把旧进度安到错误章节。
        """
        old_toc = self.state.get("toc") or []
        old_urls = [u for _, u in old_toc]

        if old_toc and old_toc != toc:
            url2new = {}
            for i, (_, u) in enumerate(toc):
                url2new.setdefault(u, i)   # 同 URL 保留首次出现

            def remap(idx_list):
                out = []
                for i in idx_list:
                    if isinstance(i, int) and 0 <= i < len(old_urls) \
                            and old_urls[i] in url2new:
                        j = url2new[old_urls[i]]
                        if j not in out:
                            out.append(j)
                return sorted(out)

            for old_i in list(self.state.get("done", [])) + list(self.state.get("failed", [])):
                if isinstance(old_i, int) and 0 <= old_i < len(old_urls) \
                        and old_urls[old_i] in url2new:
                    j = url2new[old_urls[old_i]]
                    if j != old_i and os.path.exists(self._path(old_i)):
                        os.replace(self._path(old_i), self._path(j))

            self.state["done"] = remap(self.state.get("done", []))
            self.state["failed"] = remap(self.state.get("failed", []))
            fail_log = {}
            for i, reason in (self.state.get("fail_log") or {}).items():
                try:
                    i = int(i)
                except (TypeError, ValueError):
                    continue
                if 0 <= i < len(old_urls) and old_urls[i] in url2new:
                    fail_log[str(url2new[old_urls[i]])] = reason
            self.state["fail_log"] = fail_log
            done = self.state["done"]
            self.state["last_chapter_no"] = max(done) + 1 if done else None

        self.state.update({"version": STATE_VERSION, "book_url": book_url,
                           "title": title, "toc": toc})
        old_set = set(old_urls)
        new_idx = [i for i, (_, u) in enumerate(toc) if u not in old_set]
        self.save()
        return new_idx

    def is_done(self, idx: int) -> bool:
        """这一章是否已下载。

        双条件：账本记载已完成 **且** 章节文件确实存在。
        账本是批量落盘的，断电后可能落后于磁盘；反过来说，用户也可能手删了
        缓存文件。两个都满足才算完成，否则重下 —— 宁可多一次请求，不留空章。
        """
        return idx in self.state["done"] and os.path.exists(self._path(idx))

    def recover_from_disk(self) -> int:
        """扫描 cache/ 目录，把「文件在、账本没记」的章节补回账本。

        用于账本批量落盘导致的滞后：断电后重跑时，避免把已经下好的章节
        再请求一遍。返回补回的章数。
        """
        cache_existed = set()
        try:
            for name in os.listdir(self.cache_dir):
                if not name.endswith(".txt"):
                    continue
                try:
                    idx = int(name[:-4])
                except ValueError:
                    continue
                p = os.path.join(self.cache_dir, name)
                # 空文件或只有标题的残缺文件不算完成
                if os.path.getsize(p) > 64:
                    cache_existed.add(idx)
        except OSError:
            return 0

        known = set(self.state["done"])
        missing = cache_existed - known
        if missing:
            self.state["done"] = sorted(known | cache_existed)
            self._dirty = True
            self.save(force=True)
            log.info("🔧 从磁盘恢复了 %d 章已完成记录（账本此前未落盘）", len(missing))
        return len(missing)

    # ---------- 批量落盘 ----------
    # 每章都 save() 会让整本书的总开销变成 O(n²)：每次都要把整个 TOC 重写一遍。
    # 2000 章的书光这一项就浪费近 40 秒。改成「脏标记 + 批量刷盘」：
    # 章节正文仍然每章立刻落盘（断电安全），账本延迟到批末/退出时写。
    def mark_done(self, idx: int, flush: bool = False):
        if idx not in self.state["done"]:
            self.state["done"].append(idx)
        if idx in self.state["failed"]:
            self.state["failed"].remove(idx)
        self.state.setdefault("fail_log", {}).pop(str(idx), None)
        self.state["last_chapter_no"] = idx + 1   # 1-based，人类可读
        self._dirty = True
        if flush:
            self.save(force=True)

    def mark_preview(self, idx: int, reason: str):
        """记下「这一章只拿到免费预览」。

        仍然算已完成 —— 否则每次重跑都会再请求一次，而公开部分永远只有这一段。
        想把这类章重下一遍（比如订阅之后）：删掉 cache/NNNNN.txt 再跑同一条命令。
        """
        self.state.setdefault("preview", {})[str(idx)] = str(reason)[:200]
        self._dirty = True

    def mark_failed(self, idx: int, reason: str, flush: bool = False):
        if idx not in self.state["failed"]:
            self.state["failed"].append(idx)
        self.state.setdefault("fail_log", {})[str(idx)] = str(reason)[:300]
        self._dirty = True
        if flush:
            self.save(force=True)

    def save(self, force: bool = False, chunks: int = 20):
        """写账本。force=False 时受 chunks 节流，避免每章重写整个 TOC。

        chunks：每累计多少章落盘一次。默认 20 —— 最坏情况丢 19 章的「已完成」
        记录；由于 is_done() 会同时检查章节文件是否存在，重跑时这些章会被
        重新核对而不是重新下载，所以不会产生重复网络请求。

        并发安全：整段写入持锁。tmp 文件名带线程标识，避免多线程同时
        写同一个 .tmp 导致 Windows 上 os.replace 报「另一个程序正在使用此文件」。
        """
        with self._lock:
            self._pending += 1
            if not force and self._pending < chunks:
                return
            self._pending = 0
            self._dirty = False
            self.state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            tmp = f"{self.state_path}.{os.getpid()}.{threading.get_ident()}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, ensure_ascii=False, separators=(",", ":"))
                os.replace(tmp, self.state_path)   # 原子替换
            except OSError as e:
                # 落盘失败不该让整本书失败：账本还在内存里，下次会重试
                log.warning("账本落盘失败（%s），稍后重试", e)
                self._pending = chunks   # 让下次调用立刻重试
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except OSError:
                    pass

    def flush(self):
        """退出前强制落盘（Ctrl+C、异常、正常结束都要调）。"""
        if self._dirty or self._pending:
            self.save(force=True)

    # ---------- 章节缓存 ----------
    def _path(self, idx: int) -> str:
        return os.path.join(self.cache_dir, f"{idx:05d}.txt")

    def read_chapter(self, idx: int) -> str | None:
        p = self._path(idx)
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            log.warning("章节缓存读取失败（%s）：%s", p, e)
            return None

    def write_chapter(self, idx: int, text: str):
        tmp = self._path(idx) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, self._path(idx))

    # ---------- 汇总 ----------
    def summary(self) -> str:
        parts = [f"已下载 {len(self.state['done'])} 章"]
        last = self.state.get("last_chapter_no")
        if last:
            parts.append(f"上次下到第 {last} 章")
        if self.state["failed"]:
            parts.append(f"阵亡 {len(self.state['failed'])} 章")
        preview = len(self.state.get("preview") or {})
        if preview:
            parts.append(f"其中 {preview} 章只有付费预览")
        return "，".join(parts)
