"""GitHub Pages 型站点的下载器。"""
import re

import requests

from . import log
from .githubpages import (MultiSourceFetcher, build_toc, detect_github_pages,
                          fetch_tree, md_to_text, strip_meta)


def is_github_pages_site(html: str) -> bool:
    return detect_github_pages(html, "") is not None


def download(book_url: str, out_dir: str, fetcher_headers=None,
             stop_event=None, pause_event=None,
             on_toc=None, progress_cb=None) -> list[str]:
    """下载 GitHub Pages 型站点的全部 Markdown 小说。"""
    import os
    import time

    from .progress import Progress

    headers = dict(fetcher_headers or {})
    headers.setdefault("User-Agent", "novel-dl")
    try:
        sess = requests.Session()
        sess.trust_env = False
        sess.headers.update(headers)
        home = sess.get(book_url, timeout=25).text
    except Exception as e:
        log.error("打不开页面：%s", e)
        return []

    info = detect_github_pages(home, book_url)
    if not info:
        log.error("这个页面不是 GitHub 仓库型站点")
        return []

    log.info("仓库：%s/%s@%s", info["user"], info["repo"], info["branch"])
    try:
        tree = fetch_tree(info, headers={"User-Agent": "novel-dl"})
    except Exception as e:
        log.error("读取仓库文件列表失败：%s", e)
        return []

    books = build_toc(tree, info)
    if not books:
        log.error("仓库里没有找到 .md 小说文件")
        return []

    log.info("找到 %d 本书，共 %d 章", len(books),
             sum(len(v) for v in books.values()))

    src = MultiSourceFetcher(info["user"], info["repo"], info["branch"],
                             headers=headers)

    exported = []
    for book_name, chapters in books.items():
        if stop_event is not None and stop_event.is_set():
            log.info("已停止")
            break
        log.info("")
        display = book_name.strip().strip("《》").strip() or book_name
        log.info("📖 《%s》共 %d 章", display, len(chapters))
        if on_toc is not None:
            try:
                on_toc(book_name, len(chapters), 0, len(chapters))
            except Exception:
                pass

        safe = re.sub(r'[\\/:*?"<>|]', "_", book_name)
        book_dir = os.path.join(out_dir, f"gh_{safe}")
        os.makedirs(book_dir, exist_ok=True)
        out_txt = os.path.join(book_dir, f"{safe}.txt")
        display = book_name.strip().strip("《》").strip() or book_name

        done = 0
        with open(out_txt, "w", encoding="utf-8") as f:
            f.write(f"《{display}》\n来源: {book_url}\n"
                    f"导出时间: {time.strftime('%Y-%m-%d %H:%M')}\n\n")
            for title, path in chapters:
                if stop_event is not None and stop_event.is_set():
                    break
                while pause_event is not None and pause_event.is_set():
                    time.sleep(0.25)

                try:
                    raw = src.fetch_path(path)
                except Exception as e:
                    log.warning("  × %s 抓取失败：%s", title[:30], str(e)[:80])
                    continue

                body, meta = strip_meta(raw)
                text = md_to_text(body)
                heading = meta.get("title") or title
                if len(text) < 20:
                    log.warning("  × %s 内容过短，跳过", heading[:30])
                    continue

                f.write(f"=== {heading} ===\n\n{text}\n\n\n")
                done += 1
                log.info("  ✓ [%d/%d] %s", done, len(chapters), heading[:40])
                if progress_cb is not None:
                    try:
                        progress_cb(done, len(chapters))
                    except Exception:
                        pass

        log.info("📦 已保存：%s（%d 章）", out_txt, done)
        exported.append(out_txt)

    return exported
