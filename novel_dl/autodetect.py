"""自动推断站点规则。

目标：用户只给一个目录页网址，程序自己分析出「章节链接在哪、正文在哪、
标题在哪」，生成一条规则，**并且当场验证它真的能解析**。验证通过就缓存，
下次同站直接命中，不再分析。

这就是「站点规则通用化」的实际含义：与其给每个站手写选择器（写不完、
站点一改版就失效），不如用一个算法把选择器推出来，让规则从「人工资产」
变成「程序产物」。

局限（必须诚实）：
  · 服务端渲染的页面才行。纯 JS 渲染的站点需配 --driver selenium。
  · 字体加密/图片混淆的站点推断不出来，需要专用解析器。
  · 推断结果会被缓存；站点改版后缓存失效，删掉缓存重跑即可重新推断。
"""
import json
import os
import re
from collections import Counter
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import log

# 章节链接的常见 URL 形态：
#   /book/1/123.html  /read/123_2.html  /chapter-123  /123/
CHAPTER_URL = re.compile(
    r"(?:\d+(?:[_-]\d+)?\.html?$|/chapter[-_]?\d+/?$|/\d+/?$)", re.I)

# 明显的导航/广告词，含这些的链接不算章节
NAV_WORDS = re.compile(
    r"^(首页|目录|书架|排行|顶部|底部|上一[章页]|下一[章页]|返回|登录|注册|"
    r"加入书签|投推荐票|开始阅读|查看更多|more)$")

# 缓存位置：优先用户目录，写不进去就退回当前工作目录。
# 用户目录是标准做法；但某些受限环境（只读家目录、容器）写不了，
# 那时退回 cwd 至少能让「本次运行内的重复推断」免掉。
def _cache_candidates() -> list[str]:
    paths = [
        os.path.join(os.path.expanduser("~"), ".novel-dl", "learned.json"),
        os.path.join(os.getcwd(), ".novel-dl-learned.json"),
    ]
    return paths


CACHE_PATH = _cache_candidates()[0]
_active_cache: str | None = None     # 记住这次实际用成功的那一个


def _writable_cache_path() -> str:
    """找一个真能写的缓存路径（首次调用时探测，之后复用）。"""
    global _active_cache, CACHE_PATH
    if _active_cache:
        return _active_cache
    for p in _cache_candidates():
        try:
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            probe = p + ".probe"
            with open(probe, "w", encoding="utf-8") as f:
                f.write("{}")
            os.remove(probe)
            _active_cache = CACHE_PATH = p
            return p
        except Exception:
            continue
    _active_cache = ""      # 都不可写：本次运行不缓存
    return ""


# ---------------------------------------------------------------------------
# 选择器生成
# ---------------------------------------------------------------------------
def _safe_name(s: str) -> bool:
    """class/id 能不能安全写进 CSS 选择器。"""
    return bool(s) and len(s) < 40 and re.match(r"^[A-Za-z][\w-]*$", s) is not None


def _css_path(node) -> str | None:
    """为节点生成一条能唯一定位到它的 CSS 选择器。

    策略：优先 id（唯一且稳定），否则用 class，最后退化到 nth-of-type。
    遇到 id 就停止向上，避免生成又长又脆的路径。
    """
    if node is None or node.name in ("[document]", None):
        return None
    parts = []
    cur = node
    while cur is not None and cur.name not in ("[document]", None):
        if cur.name in ("html", "body"):
            parts.append(cur.name)
            break
        nid = cur.get("id")
        if nid and _safe_name(nid):
            parts.append(f"#{nid}")
            break                      # id 一般全局唯一，就此打住
        sel = cur.name
        classes = [c for c in (cur.get("class") or []) if _safe_name(c)]
        if classes:
            sel += "." + classes[0]
        else:
            sibs = [s for s in (cur.parent.find_all(cur.name, recursive=False)
                                if cur.parent else [])]
            if len(sibs) > 1:
                sel += f":nth-of-type({sibs.index(cur) + 1})"
        parts.append(sel)
        cur = cur.parent
    return " > ".join(reversed(parts)) if parts else None


def _is_unique(soup, sel: str, node) -> bool:
    try:
        found = soup.select(sel)
    except Exception:
        return False
    return len(found) == 1 and found[0] is node


def _selector_for(soup, node, suffix: str = "") -> str:
    """拿一条「确实能唯一选中」的选择器。

    从节点自身出发逐级加长，直到 select() 只命中它一个。
    找不到唯一路径时，退而求其次返回带 nth-of-type 的索引路径 ——
    哪怕是「第 2 个 div.a > p」这种脆选择器，也比返回一个会选中 5 个元素的
    选择器强（后者会让正文混进别的内容）。
    """
    base = _css_path(node)
    if not base:
        return ""

    # 已经唯一，直接用
    if _is_unique(soup, base, node):
        return base + suffix

    # 不唯一：沿着祖先逐级拼接，直到唯一
    chains, cur = [], node
    while cur is not None and cur.name not in ("[document]",):
        chains.append(cur)
        cand = _css_path(cur)
        if not cand:
            cur = cur.parent
            continue
        if _is_unique(soup, cand, cur):
            # 祖先唯一 → 用「祖先 + 到目标的相对路径」
            rel = _relative_path(cur, node)
            if rel:
                full = f"{cand} {rel}"
                if _is_unique(soup, full, node):
                    return full + suffix
            return cand + suffix
        cur = cur.parent

    # 全部祖先都不唯一：用索引型选择器强制唯一
    idx = _indexed_path(node)
    if idx:
        return idx + suffix
    return base + suffix


def _indexed_path(node) -> str:
    """生成带 nth-of-type 的路径，从 body 一路到节点。"""
    parts, cur = [], node
    while cur is not None and cur.name not in ("[document]",):
        if cur.name == "body":
            parts.append("body")
            break
        sel = cur.name
        classes = [c for c in (cur.get("class") or []) if _safe_name(c)]
        if classes:
            sel += "." + classes[0]
        parent = cur.parent
        if parent is not None:
            sibs = parent.find_all(cur.name, recursive=False)
            if len(sibs) > 1:
                sel += f":nth-of-type({sibs.index(cur) + 1})"
        parts.append(sel)
        cur = cur.parent
    return " > ".join(reversed(parts)) if parts else ""


def _relative_path(ancestor, node) -> str:
    """node 相对 ancestor 的路径（不含 ancestor）。"""
    parts, cur = [], node
    while cur is not None and cur is not ancestor:
        sel = cur.name
        classes = [c for c in (cur.get("class") or []) if _safe_name(c)]
        if classes:
            sel += "." + classes[0]
        nid = cur.get("id")
        if nid and _safe_name(nid):
            parts.append(f"#{nid}")
            break
        parent = cur.parent
        if parent is not None:
            sibs = parent.find_all(cur.name, recursive=False)
            if len(sibs) > 1:
                sel += f":nth-of-type({sibs.index(cur) + 1})"
        parts.append(sel)
        cur = parent
    return " > ".join(reversed(parts))


# ---------------------------------------------------------------------------
# 三大部件推断
# ---------------------------------------------------------------------------
def _looks_like_chapter(a, base_url: str) -> bool:
    txt = a.get_text(strip=True)
    if not txt or len(txt) > 80 or NAV_WORDS.match(txt):
        return False
    url = urljoin(base_url, a.get("href") or "")
    return bool(CHAPTER_URL.search(url))


def pick_toc_container(soup, base_url: str):
    """找「装章节链接最多」的容器 —— 目录主体通常就在这里面。"""
    groups, nodes = Counter(), {}
    for a in soup.find_all("a", href=True):
        if not _looks_like_chapter(a, base_url):
            continue
        for p in a.parents:
            if p.name in ("body", "html", "[document]"):
                break
            groups[id(p)] += 1
            nodes[id(p)] = p
    if not groups:
        return None
    best_id, cnt = groups.most_common(1)[0]
    if cnt < 3:
        return None
    return nodes[best_id]


def _link_density(node) -> float:
    total = len(node.get_text(" ", strip=True))
    if total == 0:
        return 1.0
    link = sum(len(a.get_text(strip=True)) for a in node.find_all("a"))
    return link / total


def pick_content(soup):
    """找正文容器：文本多、链接少、段落结构像正文。

    注意不能用「文本最长」直接选 —— 那会选中包住整个页面的 body/wrapper。
    阈值定在 200 字：短章节（卷首语、尾声）也要能识别出来。
    """
    cands = []
    for node in soup.find_all(["div", "article", "section", "td", "p"]):
        text = node.get_text(" ", strip=True)
        if len(text) < 200:
            continue
        lr = _link_density(node)
        if lr > 0.35:            # 链接占比高，多半是目录/导航
            continue
        score = float(len(text)) * (1.0 - lr)
        # 正文通常有 <p> 或 <br> 分段的痕迹
        if node.find_all(["p", "br"]):
            score *= 1.15
        cands.append((score, len(text), node))

    if not cands:
        return None

    # 优先取「最内层」：如果某候选被另一个候选完整包含，且文本量接近，
    # 说明前者只是包裹层，应该选后者。
    cands.sort(key=lambda x: -x[0])
    for score, tlen, node in cands:
        inner_bigger = any(
            n is not node and _contains(node, n) and ln >= tlen * 0.9
            for _, ln, n in cands)
        if not inner_bigger:
            return node
    return cands[0][2]


def _contains(outer, inner) -> bool:
    """inner 是否是 outer 的后代（且 inner 更小）。"""
    return any(p is outer for p in inner.parents)


def pick_title(soup):
    """标题：优先 h1，其次 h2，取文字最短且不像站点名的。"""
    cands = []
    for h in soup.find_all(["h1", "h2"]):
        t = h.get_text(strip=True)
        if t and 0 < len(t) <= 80:
            cands.append((len(t), h))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    return cands[0][1]


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
def _load_cache() -> dict:
    p = _writable_cache_path()
    if not p:
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(cache: dict):
    p = _writable_cache_path()
    if not p:
        return                      # 没有可写位置，静默跳过
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    except Exception as e:
        log.warning("推断结果缓存失败（不影响本次使用）：%s", e)


def host_of(url: str) -> str:
    m = re.match(r"https?://([^/:]+)", url or "")
    return m.group(1).lower() if m else ""


def load_learned(host: str) -> dict | None:
    return _load_cache().get(host)


def save_learned(host: str, rule: dict):
    cache = _load_cache()
    cache[host] = rule
    _save_cache(cache)


def forget(host: str):
    cache = _load_cache()
    if cache.pop(host, None) is not None:
        _save_cache(cache)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def detect(book_url: str, fetcher) -> dict | None:
    """分析一个站点，返回可用的规则 dict；推断失败返回 None。

    推断完会**实际验证**：用生成的规则跑一遍目录解析 + 章节解析，
    条数和正文长度都过关才算成功。这是自动规则最容易翻车的地方，
    不验证就等于给用户一个随时会崩的配置。
    """
    from .parser import SiteRuleParser

    host = host_of(book_url)
    if not host:
        return None

    try:
        toc_html = fetcher.fetch(book_url)
    except Exception as e:
        log.warning("推断失败：目录页抓不下来（%s）", e)
        return None

    soup = BeautifulSoup(toc_html, "lxml")
    toc_node = pick_toc_container(soup, book_url)
    if toc_node is None:
        log.info("推断：目录页找不到成组的章节链接")
        return None

    toc_container_sel = _selector_for(soup, toc_node)
    if not toc_container_sel:
        return None
    toc_item = f"{toc_container_sel} a"

    # 先确认这条选择器能捞出足量章节
    try:
        candidates = [a for a in soup.select(toc_item)
                      if _looks_like_chapter(a, book_url)]
    except Exception:
        candidates = []
    if len(candidates) < 3:
        log.info("推断：目录选择器只命中 %d 条，放弃", len(candidates))
        return None

    rule = {
        "host": host,
        "name": host,
        "toc_item": toc_item,
    }

    # 用第一条章节页推断正文与标题
    first_url = urljoin(book_url, candidates[0].get("href"))
    try:
        ch_html = fetcher.fetch(first_url)
    except Exception as e:
        log.warning("推断失败：章节页抓不下来（%s）", e)
        return None

    ch_soup = BeautifulSoup(ch_html, "lxml")
    content = pick_content(ch_soup)
    if content is not None:
        sel = _selector_for(ch_soup, content)
        if sel:
            rule["ch_content"] = sel

    title = pick_title(ch_soup)
    if title is not None:
        sel = _selector_for(ch_soup, title)
        if sel:
            rule["ch_title"] = sel

    # 常见噪声：脚本、样式、导航块
    rule["filter_tag"] = "script, style, ins, iframe"

    # —— 验证 ——
    # 关键取舍：即使 ch_content 没推断出来，只要目录规则可用 + 通用解析器
    # 能从章节页拿到正文，这条规则依然有价值（目录是通用解析最容易失败的部分）。
    # 所以分两档验证：完整通过 → 直接采用；部分通过 → 只保留目录规则。
    status = _verify(rule, toc_html, book_url, ch_html, SiteRuleParser)
    if status == "ok":
        log.info("✅ 推断成功：%s → toc=%s content=%s",
                 host, rule["toc_item"], rule.get("ch_content", "(通用)"))
        return rule
    if status == "toc_only":
        rule.pop("ch_content", None)
        log.info("✅ 推断部分成功：%s → 目录规则可用，正文走通用解析", host)
        return rule

    log.info("推断：生成的规则未通过验证，放弃")
    return None


def _verify(rule, toc_html, book_url, ch_html, parser_cls) -> str:
    """用规则实际解析一次。

    返回 "ok"（目录+正文都可用）/ "toc_only"（只有目录可用）/ "fail"。
    """
    try:
        p = parser_cls(rule)
    except Exception:
        return "fail"

    try:
        title, toc = p.parse_toc(toc_html, book_url)
    except Exception:
        return "fail"
    if not toc or len(toc) < 3:
        return "fail"

    try:
        ctitle, body = p.parse_chapter(ch_html)
    except Exception:
        return "toc_only"

    if not body or len(body) < 200:
        return "toc_only"
    # 正文里混进大量链接说明选错了容器
    if body.count("http") > 5:
        return "toc_only"
    return "ok"


def ensure_rule(book_url: str, fetcher) -> dict | None:
    """取规则：先看缓存，没有就现场推断并缓存。"""
    host = host_of(book_url)
    if not host:
        return None
    cached = load_learned(host)
    if cached:
        log.info("使用之前推断过的规则（%s）", host)
        return cached
    rule = detect(book_url, fetcher)
    if rule:
        save_learned(host, rule)
    return rule
