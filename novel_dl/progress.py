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

    # ---------- 账本读写 ----------
    def _load(self) -> dict:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, encoding="utf-8") as f:
                    state = json.load(f)
                if isinstance(state, dict):
                    state.setdefault("failed", [])
                    state.setdefault("fail_log", {})
                    return state
            except Exception as e:
                log.warning("账本损坏，将重建（缓存章节仍在）：%s", e)
        return {"version": STATE_VERSION, "title": None, "book_url": None,
                "toc": [], "done": [], "failed": [], "fail_log": {},
                "last_chapter_no": None, "updated_at": None}

    def save(self):
        self.state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.state_path)   # 原子替换，防止写一半损坏

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
        return idx in self.state["done"] and os.path.exists(self._path(idx))

    def mark_done(self, idx: int):
        if idx not in self.state["done"]:
            self.state["done"].append(idx)
        if idx in self.state["failed"]:
            self.state["failed"].remove(idx)
        self.state.setdefault("fail_log", {}).pop(str(idx), None)
        self.state["last_chapter_no"] = idx + 1   # 1-based，人类可读
        self.save()

    def mark_failed(self, idx: int, reason: str):
        if idx not in self.state["failed"]:
            self.state["failed"].append(idx)
        self.state.setdefault("fail_log", {})[str(idx)] = str(reason)[:300]
        self.save()

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
        return "，".join(parts)
