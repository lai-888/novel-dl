"""GitHub Pages + Markdown 型小说站。

这类站点和传统小说网站完全不同：
  · 页面上没有章节链接，HTML 里也抓不到正文
  · 内容是一堆 Markdown 文件，放在某个 GitHub 仓库里
  · 前端通过 GitHub API 拿文件树，再用 jsDelivr CDN 取文件

典型代表：youreln.github.io/novel/

所以不能用「解析目录 HTML」的思路。正确做法是识别出这类站点后，
直接走 GitHub API + CDN：

    GET api.github.com/repos/{user}/{repo}/git/trees/{branch}?recursive=1
    → 过滤 novel/ 下的 .md
    → 从 CDN 拉每章内容

章节顺序不能信 API 返回的顺序（GitHub 的 tree 顺序不可靠，
实测是乱的），必须按文件名里的中文数字重排。
"""
import json
import re

import requests

from . import log

# youreln.github.io/novel/ 这类页面的特征
GHPAGES_HINT = re.compile(
    r'GH_USER\s*:\s*["\']([^"\']+)["\']\s*,\s*'
    r'GH_REPO\s*:\s*["\']([^"\']+)["\']', re.S)
GHPAGES_BRANCH = re.compile(r'GH_BRANCH\s*:\s*["\']([^"\']+)["\']')
GHPAGES_CDN = re.compile(r'CDN_BASE\s*:\s*["\']([^"\']+)["\']')

# 章节文件名里的中文数字
CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "两": 2}
CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}

# 章节前的元数据注释：<!-- ZW_META:{"title":"...","author":"...","book":"..."} -->
META_COMMENT = re.compile(r"<!--\s*ZW_META:\s*(\{.*?\})\s*-->", re.S)


def cn_to_int(text: str) -> int | None:
    """把「第一百二十三」这类中文数字转成整数。"""
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total, section, num = 0, 0, 0
    for ch in text:
        if ch in CN_DIGITS:
            num = CN_DIGITS[ch]
        elif ch in CN_UNITS:
            unit = CN_UNITS[ch]
            if unit >= 10000:
                total = (total + section + num) * unit
                section = num = 0
            else:
                section += (num or 1) * unit
                num = 0
        else:
            return None
    return total + section + num


def chapter_order_key(filename: str) -> tuple:
    """从文件名推断排序键。

    文件名形如「第二章 课桌藏心事.md」「彩蛋.md」「第三章 ...」。
    用（类别, 序号）排序：正文在前，番外/彩蛋在后。
    """
    stem = filename.rsplit("/", 1)[-1].rsplit(".md", 1)[0]
    m = re.match(r"^第([零〇一二三四五六七八九十百千万两\d]+)[章回节话]", stem)
    if m:
        n = cn_to_int(m.group(1))
        return (0, n if n is not None else 9999, stem)
    if any(k in stem for k in ("彩蛋", "番外", "后记", "尾声")):
        return (2, 0, stem)
    if "序" in stem or "楔子" in stem:
        return (0, -1, stem)
    return (1, 0, stem)


class GitHubPagesFetcher:
    """给 autodetect / engine 用的轻量 fetcher 适配层。"""

    def __init__(self, headers=None, timeout=30):
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(headers or {})
        self.timeout = timeout

    def fetch(self, url, quick=False):
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.text


def detect_github_pages(html: str, page_url: str) -> dict | None:
    """判断这个页面是不是「GitHub 仓库 + Markdown」型站点。

    命中返回 {user, repo, branch, cdn, prefix}，否则返回 None。
    """
    m = GHPAGES_HINT.search(html)
    if not m:
        return None
    user, repo = m.group(1), m.group(2)
    mb = GHPAGES_BRANCH.search(html)
    branch = mb.group(1) if mb else "main"
    mc = GHPAGES_CDN.search(html)
    if mc:
        cdn = mc.group(1)
    else:
        cdn = f"https://cdn.jsdelivr.net/gh/{user}/{repo}@{branch}/"
    # 站点自己用的是 novel/ 前缀；从页面里推断，兜底用 novel/
    prefix = "novel/"
    mp = re.search(r'["\'](novel/)["\']', html)
    if mp:
        prefix = mp.group(1)
    log.info("识别为 GitHub 仓库型站点：%s/%s@%s", user, repo, branch)
    return {"user": user, "repo": repo, "branch": branch,
            "cdn": cdn, "prefix": prefix}


def source_urls(user: str, repo: str, branch: str, path: str) -> list[str]:
    """同一份文件的多个可取地址，按实测速度排序。

    实测（2026-10，国内网络）：
        gh-proxy.com        1.5s  ✅
        gcore.jsdelivr      3.2s  ✅
        cdn.jsdelivr        3.8s  ✅
        raw.githubusercontent  13.7s ⚠️ 慢但可用
        fastly.jsdelivr / ghproxy.net / moeyy  → 超时或拒绝

    jsDelivr 与 raw 在国内经常被重置，只用一个源会让整本书下不下来。
    """
    quoted = requests.utils.quote(path)
    raw_path = requests.utils.quote(path, safe="/")
    raw_url = f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{raw_path}"
    return [
        f"https://gh-proxy.com/{raw_url}",
        f"https://gcore.jsdelivr.net/gh/{user}/{repo}@{branch}/{quoted}",
        f"https://cdn.jsdelivr.net/gh/{user}/{repo}@{branch}/{quoted}",
        raw_url,
        f"https://fastly.jsdelivr.net/gh/{user}/{repo}@{branch}/{quoted}",
        f"https://ghproxy.net/{raw_url}",
        f"https://github.moeyy.xyz/{raw_url}",
    ]


class MultiSourceFetcher:
    """对同一份文件按多个镜像依次尝试，任一成功即返回。

    记住上次成功的源并优先使用，避免每章都从头试一遍。
    """

    def __init__(self, user, repo, branch, headers=None, timeout=25):
        self.user, self.repo, self.branch = user, repo, branch
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(headers or {})
        self.session.headers.setdefault("User-Agent", "novel-dl")
        self.timeout = timeout
        self._preferred = None

    def fetch_path(self, path: str) -> str:
        urls = source_urls(self.user, self.repo, self.branch, path)
        if self._preferred is not None:
            urls.sort(key=lambda u: 0 if self._preferred in u else 1)

        last = None
        for u in urls:
            try:
                r = self.session.get(u, timeout=self.timeout)
                if r.status_code == 200 and r.content:
                    self._preferred = next(
                        (k for k in ("jsdelivr", "fastly", "gcore",
                                     "raw.githubusercontent", "ghproxy",
                                     "gh-proxy", "moeyy") if k in u), None)
                    return r.text
                last = f"HTTP {r.status_code}"
            except Exception as e:
                last = f"{type(e).__name__}"
                continue
        raise ConnectionError(f"所有源都失败（最后一个：{last}）：{path}")


def fetch_tree(info: dict, headers=None) -> list[dict]:
    """调 GitHub API 拿文件树。"""
    url = (f"https://api.github.com/repos/{info['user']}/{info['repo']}"
           f"/git/trees/{info['branch']}?recursive=1")
    h = {"Accept": "application/vnd.github+json", "User-Agent": "novel-dl"}
    h.update(headers or {})
    r = requests.get(url, timeout=30, headers=h)
    r.raise_for_status()
    data = r.json()
    if data.get("truncated"):
        log.warning("仓库文件过多，GitHub 返回的树被截断，可能缺章节")
    return data.get("tree", [])


def build_toc(tree: list[dict], info: dict) -> dict[str, list[tuple[str, str]]]:
    """把文件树整理成 {书名: [(章节名, 文件路径), ...]}，已按章号排好序。

    第二项是**仓库内路径**而不是完整 URL —— 具体从哪个镜像取，
    由 MultiSourceFetcher 逐个尝试后决定。
    """
    prefix = info.get("prefix", "novel/")
    books: dict[str, list[tuple[str, str]]] = {}

    for item in tree:
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        if not path.startswith(prefix) or not path.endswith(".md"):
            continue
        rel = path[len(prefix):]
        parts = rel.split("/")
        if len(parts) < 2:
            book, fname = "未分类", rel
        else:
            book, fname = parts[0], parts[-1]
        if fname.lower() == "sort.json":
            continue
        if "sort" in fname.lower() and fname.lower().endswith(".json"):
            continue
        chap = fname[:-3]          # 去掉 .md
        books.setdefault(book, []).append((chap, path))

    for book in books:
        books[book].sort(key=lambda cu: chapter_order_key(cu[0] + ".md"))
    return books


def strip_meta(md: str) -> tuple[str, dict]:
    """剥离章节开头的 ZW_META 注释，返回 (正文, 元信息)。"""
    meta = {}
    m = META_COMMENT.search(md)
    if m:
        try:
            meta = json.loads(m.group(1))
        except Exception:
            meta = {}
        md = META_COMMENT.sub("", md, count=1)
    return md.lstrip("\n"), meta


def md_to_text(md: str) -> str:
    """把 Markdown 转成朴素正文。

    不做完整的 Markdown 渲染 —— 小说正文基本只有段落和少量强调，
    过度渲染反而会把文字改坏。只处理确定安全的几种：
      · 标题井号
      · 粗体/斜体/行内代码标记
      · 图片（丢弃，保留 alt 文本）
      · 链接（保留文字）
      · 引用符 >
    """
    lines = []
    in_fence = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            lines.append(line)
            continue
        # 图片 ![alt](url) -> alt；HTML 的 <img> 直接去掉（保留 alt 会插进正文中间）
        line = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"<img\b[^>]*>", "", line, flags=re.I)
        # 其余常见内联 HTML 标签也去掉，避免污染正文
        line = re.sub(r"</?(?:br|hr|p|div|span|center|b|i|u|strong|em)\b[^>]*>",
                      "", line, flags=re.I)
        # 链接 [text](url) -> text
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
        # 标题
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        # 引用
        line = re.sub(r"^\s{0,3}>\s?", "", line)
        # 粗体斜体删除线行内代码。顺序很重要：
        # 先处理三个星号，再两个，最后单个 —— 否则 **x** 会被单星号规则拆坏。
        line = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"__(.+?)__", r"\1", line)
        line = re.sub(r"~~(.+?)~~", r"\1", line)
        line = re.sub(r"`([^`]*)`", r"\1", line)
        # 单个星号斜体：不要求词边界（中文没有空格分隔，要求 \w 边界会漏掉）
        line = re.sub(r"\*([^*\n]+?)\*", r"\1", line)
        # 分隔线
        if re.fullmatch(r"\s*([-*_])\1{2,}\s*", line):
            lines.append("")
            continue
        # 全角空格占位（原文里常见）→ 空行
        if line.strip() in ("", "\u3000"):
            lines.append("")
            continue
        lines.append(line)

    text = "\n".join(lines)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
