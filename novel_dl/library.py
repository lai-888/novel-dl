"""书架：扫描已下载的书，提供章节列表和单章读取。

给网页端的两个新能力供数据：
  · 已下载的书 → 每一章都能点开预览
  · 每章带质检结论（乱码/空壳/正常），用户一眼看出问题

数据来源就是磁盘上的 cache/ + state.json，不引入数据库。
"""
import json
import os
import re

from . import log
from .quality import analyze, summarize


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def scan(root: str) -> list[dict]:
    """扫描输出目录，返回每本书的概要。

    兼容两种目录结构：
      · 通用站点：<root>/<slug>/cache/00001.txt + state.json
      · GitHub 型：<root>/<书名>/<书名>.txt（只有一个合并文件）
    """
    if not os.path.isdir(root):
        return []

    books = []
    for entry in sorted(os.listdir(root)):
        path = os.path.join(root, entry)
        if not os.path.isdir(path):
            continue

        state_path = os.path.join(path, "state.json")
        cache_dir = os.path.join(path, "cache")

        if os.path.exists(state_path) and os.path.isdir(cache_dir):
            books.append(_scan_standard(path, entry, state_path, cache_dir))
        else:
            merged = [f for f in os.listdir(path) if f.endswith(".txt")]
            if merged:
                books.append(_scan_merged(path, entry, merged[0]))

    # 有问题的排前面，方便用户先看需要处理的
    books.sort(key=lambda b: (-b["stats"]["bad"], -b["stats"]["warn"],
                              b["title"] or b["slug"]))
    return books


def _scan_standard(path, slug, state_path, cache_dir) -> dict:
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:
        log.warning("读取账本失败（%s）：%s", state_path, e)
        state = {}

    toc = state.get("toc") or []
    done = set(state.get("done") or [])
    failed = set(state.get("failed") or [])
    title = state.get("title") or slug

    # 账本是批量落盘的，可能落后于磁盘：以「文件是否存在」为准。
    # 不这样做，已下好的章节会被误报成「尚未下载」。
    on_disk = set()
    try:
        for name in os.listdir(cache_dir):
            if name.endswith(".txt"):
                try:
                    idx = int(name[:-4])
                except ValueError:
                    continue
                if os.path.getsize(os.path.join(cache_dir, name)) > 32:
                    on_disk.add(idx)
    except OSError:
        pass
    done |= on_disk

    reports = []
    for i, (ctitle, _url) in enumerate(toc):
        if i in done:
            body = _read_cache(cache_dir, i)
            rep = analyze(ctitle, body)
            rep["no"] = i + 1
            rep["title"] = ctitle
            rep["status"] = "done"
        elif i in failed:
            rep = {"no": i + 1, "title": ctitle, "status": "failed",
                   "ok": False, "level": "bad", "issues": ["下载失败"],
                   "cjk_ratio": 0, "length": 0}
        else:
            rep = {"no": i + 1, "title": ctitle, "status": "pending",
                   "ok": False, "level": "warn", "issues": ["尚未下载"],
                   "cjk_ratio": 0, "length": 0}
        reports.append(rep)

    return {
        "slug": slug,
        "title": title,
        "path": path,
        "kind": "standard",
        "chapters": reports,
        "stats": summarize(reports),
        "source": state.get("book_url"),
    }


def _read_cache(cache_dir, idx) -> str:
    p = os.path.join(cache_dir, f"{idx:05d}.txt")
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _scan_merged(path, slug, fname) -> dict:
    """GitHub 型站点：只有一个合并 TXT，按 === 标题 === 切分。"""
    p = os.path.join(path, fname)
    try:
        with open(p, encoding="utf-8") as f:
            content = f.read()
    except Exception:
        content = ""

    parts = re.split(r"^=== (.+?) ===$", content, flags=re.M)
    # parts = [前言, 标题1, 正文1, 标题2, 正文2, ...]
    reports = []
    for i in range(1, len(parts), 2):
        ctitle = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        rep = analyze(ctitle, body)
        rep["no"] = (i + 1) // 2
        rep["title"] = ctitle
        rep["status"] = "done"
        reports.append(rep)

    title = slug
    # gh_《书名》 / 《书名》 两种前缀都剥掉，只留书名本身
    if title.startswith("gh_"):
        title = title[3:]
    title = title.strip().strip("《》").strip()
    return {
        "slug": slug,
        "title": title,
        "path": path,
        "kind": "merged",
        "chapters": reports,
        "stats": summarize(reports),
        "source": None,
    }


def read_chapter(book: dict, no: int) -> dict | None:
    """读取某一章的完整内容（供预览面板）。"""
    chapters = book.get("chapters") or []
    if not (1 <= no <= len(chapters)):
        return None

    if book["kind"] == "standard":
        body = _read_cache(os.path.join(book["path"], "cache"), no - 1)
    else:
        fname = [f for f in os.listdir(book["path"]) if f.endswith(".txt")]
        if not fname:
            return None
        with open(os.path.join(book["path"], fname[0]), encoding="utf-8") as f:
            content = f.read()
        parts = re.split(r"^=== (.+?) ===$", content, flags=re.M)
        idx = (no - 1) * 2 + 2
        body = parts[idx] if idx < len(parts) else ""

    rep = chapters[no - 1]
    # 正文里第一行是标题，预览时分开显示
    lines = body.strip().split("\n\n", 1)
    heading = lines[0].strip() if lines else rep["title"]
    text = lines[1] if len(lines) > 1 else ""

    return {
        "no": no,
        "title": rep["title"] or heading,
        "status": rep["status"],
        "quality": {"level": rep["level"], "issues": rep["issues"],
                    "length": rep["length"], "cjk_ratio": rep["cjk_ratio"]},
        "text": text or body.strip(),
    }
