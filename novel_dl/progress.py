"""断点续传状态管理。"""
import hashlib
import json
import os
import re
import threading
import time

from . import log

STATE_VERSION = 2


def chapter_key(url: str) -> str:
    """章节正文的缓存文件名：由 URL 决定，与目录顺序无关。"""
    return hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:16]


def _slug(s: str) -> str:
    """输出目录名。超长时截断并补 URL 摘要，避免同前缀的两本书撞同一个目录。"""
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "_", s or "").strip("_")
    if not cleaned:
        return "book"
    if len(cleaned) > 56:
        cleaned = cleaned[:47] + "_" + chapter_key(s)[:8]
    return cleaned


def _legacy_slug(s: str) -> str:
    """旧版目录名（截断到 60 字符）。已经下过的书还在用这个名字。"""
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "_", s or "").strip("_")
    return cleaned[:60] or "book"


def legacy_index(name: str):
    """老式缓存文件名是 5 位序号（00007.txt）；新式是 16 位十六进制摘要。"""
    stem = name[:-4] if name.endswith(".txt") else ""
    if len(stem) == 5 and stem.isdigit():
        return int(stem)
    return None


class Progress:
    def __init__(self, root: str, book_url: str):
        new_root = os.path.join(root, _slug(book_url))
        legacy = os.path.join(root, _legacy_slug(book_url))
        # 老版本已经下过的书沿用旧目录名，别让用户重新下一遍
        if new_root != legacy and not os.path.exists(new_root) \
                and os.path.isdir(legacy):
            new_root = legacy
        self.root = new_root
        self.cache_dir = os.path.join(self.root, "cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.state_path = os.path.join(self.root, "state.json")
        self.state = self._load()
        self._dirty = False     # 账本有未落盘的改动
        self._pending = 0       # 距上次落盘又过了多少章
        self._lock = threading.RLock()
        self._migrate_cache()

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

    def _migrate_cache(self):
        """旧版缓存文件名是序号（00001.txt），改成 URL 摘要。"""
        toc = self.state.get("toc") or []
        if not toc:
            return
        try:
            names = os.listdir(self.cache_dir)
        except OSError:
            return
        moved = 0
        for name in names:
            i = legacy_index(name)
            if i is None:
                continue
            if not (0 <= i < len(toc)):
                continue
            dst = self._path(i)
            if os.path.exists(dst):
                continue
            try:
                os.replace(os.path.join(self.cache_dir, name), dst)
                moved += 1
            except OSError:
                pass
        if moved:
            log.info("缓存文件名改用章节地址，已迁移 %d 章", moved)

    def set_book(self, book_url, title, toc):
        """记录目录快照，返回「新增章节索引」列表（增量下载的依据）。"""
        old_toc = self.state.get("toc") or []
        old_urls = [u for _, u in old_toc]

        if old_toc and old_toc != toc:
            url2new = {}
            for i, (_, u) in enumerate(toc):
                url2new.setdefault(u, i)

            def remap(idx_list):
                out = []
                for i in idx_list:
                    if isinstance(i, int) and 0 <= i < len(old_urls) \
                            and old_urls[i] in url2new:
                        j = url2new[old_urls[i]]
                        if j not in out:
                            out.append(j)
                return sorted(out)

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
            preview = {}
            for i, reason in (self.state.get("preview") or {}).items():
                try:
                    i = int(i)
                except (TypeError, ValueError):
                    continue
                if 0 <= i < len(old_urls) and old_urls[i] in url2new:
                    preview[str(url2new[old_urls[i]])] = reason
            self.state["preview"] = preview
            done = self.state["done"]
            self.state["last_chapter_no"] = max(done) + 1 if done else None

        self.state.update({"version": STATE_VERSION, "book_url": book_url,
                           "title": title, "toc": toc})
        old_set = set(old_urls)
        new_idx = [i for i, (_, u) in enumerate(toc) if u not in old_set]
        self.save()
        return new_idx

    def is_done(self, idx: int) -> bool:
        """这一章是否已下载。"""
        return idx in self.state["done"] and os.path.exists(self._path(idx))

    def recover_from_disk(self) -> int:
        """扫描 cache/ 目录，把「文件在、账本没记」的章节补回账本。"""
        key2idx = {}
        for i, (_, url) in enumerate(self.state.get("toc") or []):
            key2idx[chapter_key(url)] = i
        cache_existed = set()
        try:
            for name in os.listdir(self.cache_dir):
                if not name.endswith(".txt"):
                    continue
                stem = name[:-4]
                idx = key2idx.get(stem)
                if idx is None:
                    idx = legacy_index(name)
                if idx is None:
                    continue
                if os.path.getsize(os.path.join(self.cache_dir, name)) > 64:
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
    def mark_done(self, idx: int, flush: bool = False):
        if idx not in self.state["done"]:
            self.state["done"].append(idx)
        if idx in self.state["failed"]:
            self.state["failed"].remove(idx)
        self.state.setdefault("fail_log", {}).pop(str(idx), None)
        self.state["last_chapter_no"] = max(self.state.get("last_chapter_no") or 0,
                                            idx + 1)   # 人类可读：下到第几章
        self._dirty = True
        if flush:
            self.save(force=True)

    def mark_preview(self, idx: int, reason: str):
        """记下「这一章只拿到免费预览」。"""
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
        """写账本。force=False 时受 chunks 节流，避免每章重写整个 TOC。"""
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
    def _url(self, idx: int):
        toc = self.state.get("toc") or []
        if 0 <= idx < len(toc):
            return toc[idx][1]
        return None

    def _path(self, idx: int) -> str:
        url = self._url(idx)
        if url is None:
            return os.path.join(self.cache_dir, f"{idx:05d}.txt")
        return os.path.join(self.cache_dir, chapter_key(url) + ".txt")

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
            parts.append(f"失败 {len(self.state['failed'])} 章")
        preview = len(self.state.get("preview") or {})
        if preview:
            parts.append(f"其中 {preview} 章只有付费预览")
        return "，".join(parts)
