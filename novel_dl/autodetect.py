"""自动推断站点规则。"""
import json
import os
import re
import tempfile
from collections import Counter
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import log

CHAPTER_URL = re.compile(
    r"(?:\d+(?:[_-]\d+)?\.html?$|/chapter[-_]?\d+/?$|/\d+/?$)", re.I)

NAV_WORDS = re.compile(
    r"^(首页|目录|书架|排行|顶部|底部|上一[章页]|下一[章页]|返回|登录|注册|"
    r"加入书签|投推荐票|开始阅读|查看更多|more)$")


def _cache_candidates() -> list[str]:
    paths = []
    env = os.environ.get("NOVEL_DL_CACHE", "").strip()
    if env:
        paths.append(env)
    paths += [
        os.path.join(os.path.expanduser("~"), ".novel-dl", "learned.json"),
        os.path.join(os.getcwd(), ".novel-dl-learned.json"),
        os.path.join(tempfile.gettempdir(), "novel-dl-learned.json"),
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
# ---------------------------------------------------------------------------


def _safe_name(s: str) -> bool:
    """class/id 能不能安全写进 CSS 选择器。"""
    return bool(s) and len(s) < 40 and re.match(r"^[A-Za-z][\w-]*$", s) is not None


def _css_path(node) -> str | None:
    """为节点生成一条能唯一定位到它的 CSS 选择器。"""
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


_SIMPLE_SEL = re.compile(
    r"^(?P<tag>[A-Za-z][\w-]*)?(?P<ids>(?:#[\w-]+)*)(?P<classes>(?:\.[\w-]+)*)$")


class DomIndex:
    """一次遍历建好 id / class / tag 三张桶，省掉反复全树 select。"""

    def __init__(self, soup):
        self.soup = soup
        self.by_id = {}
        self.by_class = {}
        self.by_tag = {}
        for el in soup.find_all(True):
            nid = el.get("id")
            if nid:
                self.by_id.setdefault(nid, []).append(el)
            for c in (el.get("class") or ()):
                self.by_class.setdefault(c, []).append(el)
            self.by_tag.setdefault(el.name, []).append(el)

    def select(self, sel: str):
        """只处理「简单选择器 + 空格/大于号」组成的链；别的返回 None 交给 soupsieve。"""
        if not sel or any(ch in sel for ch in ",+~:[]*()"):
            return None
        parts = [p.strip() for p in re.split(r"\s*>\s*|\s+", sel.strip()) if p.strip()]
        if len(parts) < 1 or len(parts) > 8:
            return None
        combs = re.findall(r"\s*>\s*|\s+", sel.strip())
        chain = []
        for i, part in enumerate(parts):
            chain.append((part, ">" if i and combs[i - 1].strip() == ">" else " "))
        cands = self.candidates(chain[-1][0])
        if cands is None:
            return None
        return [el for el in cands if self._match_chain(el, chain)]

    def _match_chain(self, el, chain) -> bool:
        return self._match_from(el, chain, len(chain) - 1)

    def _match_from(self, el, chain, i) -> bool:
        """chain[i] 落在 el 上，且左边的每段都能配上；同名嵌套要能回溯。"""
        if not self._match_compound(el, chain[i][0]):
            return False
        if i == 0:
            return True
        cur = el.parent
        if chain[i][1] == ">":
            return cur is not None and self._match_from(cur, chain, i - 1)
        while cur is not None and cur.name not in (None, "[document]"):
            if self._match_from(cur, chain, i - 1):
                return True
            cur = cur.parent
        return False

    @staticmethod
    def _match_compound(el, compound: str) -> bool:
        if el is None or el.name in (None, "[document]"):
            return False
        m = _SIMPLE_SEL.match(compound)
        if not m:
            return False
        tag = m.group("tag")
        if tag and el.name != tag:
            return False
        ids = [p for p in m.group("ids").split("#") if p]
        if ids and el.get("id") != ids[0]:
            return False
        have = set(el.get("class") or ())
        for c in [p for p in m.group("classes").split(".") if p]:
            if c not in have:
                return False
        return True

    def candidates(self, sel: str):
        """能查桶就返回候选列表（可能为空），否则返回 None 表示「查不了」。"""
        sel = (sel or "").strip()
        if not sel or any(ch in sel for ch in " >+~,:[]*("):
            return None
        m = _SIMPLE_SEL.match(sel)
        if not m:
            return None
        tag = m.group("tag")
        ids = [p for p in m.group("ids").split("#") if p]
        classes = [p for p in m.group("classes").split(".") if p]
        if len(ids) > 1:
            return None
        pool = None
        if ids:
            pool = self.by_id.get(ids[0], [])
        for c in classes:
            bucket = self.by_class.get(c, [])
            if pool is None:
                pool = bucket
            else:
                keep = {id(el) for el in bucket}
                pool = [el for el in pool if id(el) in keep]
        if pool is None:
            pool = self.by_tag.get(tag, [])
        elif tag:
            pool = [el for el in pool if el.name == tag]
        return pool


def _is_unique(soup, sel: str, node, index=None) -> bool:
    if index is not None:
        found = index.candidates(sel)
        if found is not None:
            return len(found) == 1 and found[0] is node
    try:
        found = soup.select(sel)
    except Exception:
        return False
    return len(found) == 1 and found[0] is node


def _selector_for(soup, node, suffix: str = "", index=None) -> str:
    """拿一条「确实能唯一选中」的选择器。"""
    base = _css_path(node)
    if not base:
        return ""

    if _is_unique(soup, base, node, index):
        return base + suffix

    chains, cur = [], node
    while cur is not None and cur.name not in ("[document]",):
        chains.append(cur)
        cand = _css_path(cur)
        if not cand:
            cur = cur.parent
            continue
        if _is_unique(soup, cand, cur, index):
            rel = _relative_path(cur, node)
            if rel:
                full = f"{cand} {rel}"
                if _is_unique(soup, full, node, index):
                    return full + suffix
            return cand + suffix
        cur = cur.parent

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
    """找正文容器：文本多、链接少、段落结构像正文。"""
    cands = []
    for node in soup.find_all(["div", "article", "section", "td", "p"]):
        text = node.get_text(" ", strip=True)
        if len(text) < 200:
            continue
        lr = _link_density(node)
        if lr > 0.35:            # 链接占比高，多半是目录/导航
            continue
        score = float(len(text)) * (1.0 - lr)
        if node.find_all(["p", "br"]):
            score *= 1.15
        cands.append((score, len(text), node))

    if not cands:
        return None

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
# ---------------------------------------------------------------------------


def detect(book_url: str, fetcher) -> dict | None:
    """分析一个站点，返回可用的规则 dict；推断失败返回 None。"""
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
    idx = DomIndex(soup)
    toc_node = pick_toc_container(soup, book_url)
    if toc_node is None:
        log.info("推断：目录页找不到成组的章节链接")
        return None

    toc_container_sel = _selector_for(soup, toc_node, index=idx)
    if not toc_container_sel:
        return None
    toc_item = f"{toc_container_sel} a"

    # 容器已经唯一命中，直接取它里面的链接，省一次全树 select
    if _is_unique(soup, toc_container_sel, toc_node, idx):
        candidates = [a for a in toc_node.find_all("a", href=True)
                      if _looks_like_chapter(a, book_url)]
    else:
        try:
            candidates = [a for a in soup.select(toc_item)
                          if _looks_like_chapter(a, book_url)]
        except Exception:
            candidates = []
    if len(candidates) < 3:
        log.info("推断：目录选择器只命中 %d 条，放弃", len(candidates))
        return None

    # 只留「同一个网址形状」的那一批：导航、分类、别的书都不算章节
    from .parser import GenericParser as _GP

    def _tpl(u: str) -> str:
        return re.sub(r"\d+", "N", u.split("?")[0].split("#")[0])

    groups = {}
    for a in candidates:
        groups.setdefault(_tpl(urljoin(book_url, a.get("href"))), []).append(a)
    main_tpl, main_group = max(groups.items(), key=lambda kv: len(kv[1]))
    trimmed = [a for a in main_group
               if a.get_text(strip=True) not in _GP.NAV_TEXTS]
    if len(trimmed) < 3:
        log.info("推断：收窄到同一网址形状后只剩 %d 条，放弃", len(trimmed))
        return None

    rule = {
        "host": host,
        "name": host,
        "toc_item": toc_item,
    }
    if len(groups) > 1 or len(main_group) > len(trimmed):
        # 选择器会连带命中别的链接，用 toc_filter 收到这一批
        flt = re.escape(main_tpl).replace("N", "\\d+")
        rule["toc_filter"] = flt
        log.debug("推断：目录收窄到 %s（%d 章）", flt, len(trimmed))
    candidates = trimmed

    first_url = urljoin(book_url, candidates[0].get("href"))
    try:
        ch_html = fetcher.fetch(first_url)
    except Exception as e:
        log.warning("推断失败：章节页抓不下来（%s）", e)
        return None

    ch_soup = BeautifulSoup(ch_html, "lxml")
    ch_idx = DomIndex(ch_soup)
    content = pick_content(ch_soup)
    if content is not None:
        sel = _selector_for(ch_soup, content, index=ch_idx)
        if sel:
            rule["ch_content"] = sel

    title = pick_title(ch_soup)
    if title is not None:
        sel = _selector_for(ch_soup, title, index=ch_idx)
        if sel:
            rule["ch_title"] = sel

    rule["filter_tag"] = "script, style, ins, iframe"

    status = _verify(rule, soup, book_url, ch_soup, SiteRuleParser, idx)
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


def _verify(rule, toc_soup, book_url, ch_soup, parser_cls, index=None) -> str:
    """用规则实际解析一次（页面已经解析好，直接复用）。"""
    try:
        p = parser_cls(rule)
    except Exception:
        return "fail"

    try:
        title, toc = p.toc_from_soup(toc_soup, book_url, index=index)
    except Exception:
        return "fail"
    if not toc or len(toc) < 3:
        return "fail"

    try:
        ctitle, body = p.parse_chapter(str(ch_soup))
    except Exception:
        return "toc_only"

    if not body or len(body) < 200:
        return "toc_only"
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
