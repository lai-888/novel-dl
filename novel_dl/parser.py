"""解析层 —— 站点解析器的插件化。"""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from . import log
from .rules import RuleNeedsScript, find_rule, has_script, plain_selector
from .textutil import body_from, clean_text, strip_invisible, strip_page_mark

PARSERS = {}


def register_parser(key):
    def deco(cls):
        PARSERS[key] = cls()
        return cls
    return deco

PUA_PAT = re.compile(r"[\ue000-\uf8ff]")

CHAPTER_URL_PAT = re.compile(r"^(.*?/)\d+\.html?$")


def _looks_cipher(text: str) -> bool:
    """正文几乎不含汉字 —— 多半是 base64 密文而不是小说正文。"""
    sample = (text or "").strip()
    if len(sample) < 100:
        return False
    cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    return cjk / len(sample) < 0.05


class BaseParser:
    name = "base"

    def parse_toc(self, html: str, base_url: str) -> tuple[str, list[tuple[str, str]]]:
        """返回 (书名, [(章节标题, 章节URL), ...])，按阅读顺序。"""
        raise NotImplementedError

    def parse_chapter(self, html: str) -> tuple[str, str]:
        """返回 (章节标题, 正文)。反爬钩子：乱序/加密都在这里解。"""
        raise NotImplementedError

    @staticmethod
    def clean_text(text: str) -> str:
        return clean_text(text)


def _doc_title(soup, noise_pat) -> str:
    raw = soup.title.get_text(strip=True) if soup.title else ""
    if not raw:
        return ""
    return strip_invisible(noise_pat.split(raw)[0].strip())


class SiteRuleParser(BaseParser):
    """按 JSON 规则解析。规则失效时自动退回通用解析器，保证「还能下」。"""

    def __init__(self, rule: dict):
        self.rule = rule
        self.name = f"rule:{rule.get('name') or rule.get('host')}"

    # ---------- 目录 ----------
    def parse_toc(self, html, base_url):
        soup = BeautifulSoup(html, "lxml")
        title = _doc_title(soup, GenericParser.TITLE_NOISE_PAT)

        selector = plain_selector(self.rule.get("toc_item", ""))
        links = []
        if selector:
            try:
                for a in soup.select(selector):
                    if a.has_attr("href") and a.get_text(strip=True):
                        links.append((GenericParser.clean_toc_title(a.get_text(strip=True)),
                                      urljoin(base_url, a["href"])))
            except Exception as e:
                log.warning("toc_item 选择器无效（%s），改用通用解析：%s", selector, e)
                links = []

        if not links:
            log.debug("规则 toc_item 未命中，退回通用解析")
            title2, toc2 = GenericParser().parse_toc(html, base_url)
            return title or title2, toc2

        seen, toc = set(), []
        for t, u in links:
            if u not in seen:
                seen.add(u)
                toc.append((t, u))
        if self.rule.get("toc_desc"):
            toc.reverse()
        return title, toc

    def _apply_filter_tag(self, container, spec: str):
        """按 filter_tag 删噪声。删得太多就跳过 —— 防止把正文段落整段删光。"""
        budget = int(len(container.get_text(strip=True)) * 0.5)
        for tag in spec.split(","):
            tag = tag.strip()
            if not tag:
                continue
            try:
                picked = list(container.select(tag))
            except Exception:
                continue
            for el in picked:
                if el.parent is None:       # 已被前面的选择器删掉
                    continue
                size = len(el.get_text(strip=True))
                if size > budget:
                    log.warning("规则 filter_tag=%s 会删掉大半正文，已跳过这一条", tag)
                    continue
                budget -= size
                el.decompose()

    # ---------- 正文 ----------
    def parse_chapter(self, html):
        rule = self.rule
        soup = BeautifulSoup(html, "lxml")

        selector = plain_selector(rule.get("ch_title", ""))
        node = self._select_one(soup, selector)
        if node:
            title = strip_page_mark(node.get_text(strip=True))
        else:
            h = soup.find(["h1", "h2"])
            title = strip_page_mark(h.get_text(strip=True)) if h else (
                soup.title.get_text(strip=True) if soup.title else "未知章节")

        c_sel = plain_selector(rule.get("ch_content", ""))
        js_encoded = has_script(rule.get("ch_content", ""))
        container = self._select_one(soup, c_sel)
        if container is None:
            log.debug("ch_content 未命中，退回通用正文解析")
            return GenericParser().parse_chapter(html, title_hint=title)

        for t in container.find_all(["script", "style", "ins"]):
            t.decompose()
        self._apply_filter_tag(container, rule.get("filter_tag") or "")

        lines = [ln.strip() for ln in container.get_text("\n").splitlines()]
        body = body_from(lines)

        for pat in (rule.get("filter_txt") or "").split("|"):
            pat = pat.strip()
            if not pat:
                continue
            try:
                body = re.sub(pat, "", body)
            except re.error:
                body = body.replace(pat, "")
        body = clean_text(body)

        if len(body) < 50:
            raise ValueError("正文过短，疑似解析失败或触发了反爬")
        if js_encoded and _looks_cipher(body):
            raise RuleNeedsScript(
                "这一章的正文是脚本加密的（规则里的 @js: 解码跑不了），"
                "取回来是密文，不写进 TXT。")
        return title, body

    @staticmethod
    def _select_one(soup, selector):
        """安全 select_one：选择器非法时返回 None 而不是抛异常。"""
        if not selector:
            return None
        try:
            return soup.select_one(selector)
        except Exception as e:
            log.warning("选择器无效（%s）：%s", selector, e)
            return None


@register_parser("generic")


class GenericParser(BaseParser):
    """通用启发式：适合大多数静态小说站。"""

    name = "generic"

    TOC_LINK_PAT = re.compile(
        r"(第[0-9零一二三四五六七八九十百千]+[章节卷幕话回]|序幕|序章|楔子|番外|"
        r"chapter\s*\d+|interlude)", re.I)

    TITLE_NOISE_PAT = re.compile(
        r"[-_－―]\s*$|全文阅读|最新章节|笔趣阁|顶点小说|无弹窗|txt下载|TXT下载|[,，_|]|"
        r"小说最新章节|全文免费阅读|精校版|章节列表|最新章节列表|免费阅读")

    CONTENT_TAGS = ["#content", "#chaptercontent", "#booktxt", "#txt",
                    ".content", ".read-content", ".chapter-content", ".showtxt",
                    "#htmlContent", ".txtnav", "#BookText", "#cont-body"]

    NAV_TEXTS = {"开始阅读", "点击阅读", "继续阅读", "章节目录", "查看目录",
                 "加入书签", "投推荐票", "上一章", "下一章", "上一页", "下一页",
                 "目录", "首页", "书架", "排行", "顶部", "底部"}

    CONTENT_HINT = re.compile(r"content|chapter|readtxt|read|article|novel|booktxt|text", re.I)

    _LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t"})

    @staticmethod
    def clean_toc_title(text: str) -> str:
        """目录标题净化：剥离图标字体字符，并把因此残缺的「第X章」补回来。"""
        t = PUA_PAT.sub("", text).strip()
        t = re.sub(r"^[章节回幕话卷]\s*[.、]?\s*", "", t).strip()
        t = re.sub(r"^([0-9零一二三四五六七八九十百千万两]+)([章节回幕话卷])", r"第\1\2", t)
        return t

    def _chapter_links_by_url(self, soup, base_url):
        """按 URL 形态聚类识别章节链接（标题被图标字体混淆时的兜底）。"""
        from collections import Counter
        cands = Counter()
        parsed = []
        for a in soup.find_all("a", href=True):
            if a["href"].startswith(("javascript", "#")):
                continue
            url = urljoin(base_url, a["href"])
            m = CHAPTER_URL_PAT.match(url)
            if m:
                cands[m.group(1)] += 1
                parsed.append((a, url, m.group(1)))
        if not cands:
            return [], ""
        prefix = re.sub(r"_\d+/?$", "/", cands.most_common(1)[0][0])
        out = []
        for a, url, pfx in parsed:
            if re.sub(r"_\d+/?$", "/", pfx) != prefix:
                continue
            text = self.clean_toc_title(a.get_text(strip=True))
            if text and text not in self.NAV_TEXTS and len(text) < 60:
                out.append((a, text, url))
        return (out, prefix) if len(out) >= 3 else ([], "")

    def parse_toc(self, html, base_url):
        soup = BeautifulSoup(html, "lxml")
        title = _doc_title(soup, self.TITLE_NOISE_PAT) or "未知书名"

        links = []
        for a in soup.find_all("a", href=True):
            if a["href"].startswith(("javascript", "#")):
                continue
            text = a.get_text(strip=True)
            if text and len(text) < 60 and self.TOC_LINK_PAT.search(text):
                links.append((a, text, urljoin(base_url, a["href"])))

        by_url, cluster_prefix = self._chapter_links_by_url(soup, base_url)
        if by_url and len(by_url) >= max(10, len(links) * 2):
            extras = [(a, t, u) for a, t, u in links
                      if not cluster_prefix or u.startswith(cluster_prefix)]
            seen_u, merged = set(), []
            for a, t, u in by_url + extras:
                if u not in seen_u:
                    seen_u.add(u)
                    merged.append((a, t, u))
            links = merged
        elif not links and by_url:
            links = by_url

        if not links:
            raise SystemExit(
                "目录页解析不到任何章节链接 —— 该站可能需要专用解析器、"
                "Selenium 模式，或你自己写一份站点规则（见 docs/RULES.md）。")

        counter = {}
        for a, _, _ in links:
            for p in a.parents:
                if p.name in ("body", "html", "[document]"):
                    break
                entry = counter.setdefault(id(p), [0, p])
                entry[0] += 1
        if not counter:  # 没有共同父容器（极少见）：直接按页面顺序
            return title, [(t, u) for _, t, u in links]
        _, best_node = max(counter.values(), key=lambda cp: cp[0])

        inside_ids = {id(a) for a, _, _ in links
                      if any(p is best_node for p in a.parents)}

        segments, cur = [], []
        for el in best_node.descendants:
            if el.name in ("dt", "hr"):
                if cur:
                    segments.append(cur)
                    cur = []
            elif el.name == "a" and id(el) in inside_ids:
                cur.append(el)
        if cur:
            segments.append(cur)

        text_of = {id(a): t for a, t, _ in links}
        href_of = {id(a): u for a, _, u in links}
        if segments:
            main = max(segments, key=len)
            main_list = [(text_of[id(a)], href_of[id(a)]) for a in main]
            main_urls = {u for _, u in main_list}
            extras = [(text_of[id(a)], href_of[id(a)]) for seg in segments
                      if seg is not main for a in seg if href_of[id(a)] not in main_urls]
        else:
            main_list = [(text_of[i], href_of[i]) for i in inside_ids]
            extras = []

        outside = [(t, u) for a, t, u in links if id(a) not in inside_ids]

        seen, toc = set(), []
        for t, u in main_list + extras + outside:
            if u not in seen:
                seen.add(u)
                toc.append((t, u))

        return title, self._reorder(toc)

    @staticmethod
    def _reorder(toc: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """目录常是「最新章节(倒序) + 正文列表(正序)」拼接，也可能整体倒序。"""
        nums = []
        for _, u in toc:
            m = re.search(r"(\d+)\.html?$", u) or re.search(r"/(\d+)/?$", u)
            nums.append(int(m.group(1)) if m else None)
        if len(toc) < 5 or any(n is None for n in nums):
            return toc
        if all(a > b for a, b in zip(nums, nums[1:])):
            return list(reversed(toc))       # 整体倒序

        bi = bj = cur = 0                    # 最长严格递增连续段
        for k in range(1, len(nums)):
            if nums[k] > nums[k - 1]:
                if k - cur > bj - bi:
                    bi, bj = cur, k
            else:
                cur = k
        if bj - bi + 1 >= max(5, int(0.6 * len(toc))) and bi > 0:
            return toc[bi:] + toc[:bi]        # 前面是「最新章节」块，挪到末尾
        return toc

    def parse_chapter(self, html, title_hint=None):
        soup = BeautifulSoup(html, "lxml")

        h = soup.find(["h1", "h2"])
        title = h.get_text(strip=True) if h else (
            soup.title.get_text(strip=True) if soup.title else "未知章节")

        parts = [p.strip() for p in re.split(r"[_|］】]", title) if p.strip()]
        noise = re.compile(r"最新章节|免费阅读|无弹窗|笔趣阁|小说网|全文阅读| txt |txt下载", re.I)
        chap_pat = re.compile(r"^(第)?[0-9零一二三四五六七八九十百千]+[.、〕]?")
        chap_parts = [p for p in parts if not noise.search(p)
                      and (chap_pat.match(p) or re.match(r"^[序终]章|楔子|后记|番外", p))]
        page_parts = [p for p in parts if re.match(r"^[（(]\s*\d+\s*/\s*\d+\s*[)）]", p)]
        if chap_parts:
            title = "_".join(chap_parts + page_parts[:1])
        else:
            clean = [p for p in parts if not noise.search(p)]
            if clean:
                title = "_".join(clean + page_parts[:1])
        title = strip_page_mark(title) or (title_hint or "未知章节")

        container = self._pick_container(soup)
        if container is None:
            raise ValueError("正文容器定位失败")

        for t in container.find_all(["script", "style", "ins"]):
            t.decompose()
        lines = [ln.strip() for ln in container.get_text("\n").splitlines()]
        body = body_from(lines, drop_title=title)
        if len(body) < 50:
            raise ValueError("正文过短，疑似解析失败或触发了反爬")
        return title, body

    def _pick_container(self, soup):
        """找正文容器：先认常见选择器，再按 id/class 关键词取「最贴近正文」的那个。"""
        for sel in self.CONTENT_TAGS:
            try:
                cand = soup.select_one(sel)
            except Exception:
                continue
            if cand is not None and len(cand.get_text(strip=True)) > 200:
                return cand

        cands = []
        for el in soup.find_all(["div", "section", "article", "td"]):
            hint = " ".join(filter(None, [el.get("id") or "",
                                          " ".join(el.get("class") or [])]))
            if not hint:
                continue
            if not self.CONTENT_HINT.search(hint.lower().translate(self._LEET)):
                continue
            n = len(el.get_text(strip=True))
            if n > 200:
                cands.append((n, el))
        if cands:
            top = max(n for n, _ in cands)
            # 外层包裹（带工具栏、推荐位）文字更多；取仍有八成正文的最内层
            return min((n, el) for n, el in cands if n >= top * 0.8)[1]
        return self._longest_block(soup)

    @staticmethod
    def _longest_block(soup):
        """兜底：找「自身直接文本最多」的 div/article（扣掉子容器，避免选中外层包裹）。"""
        best, best_len = None, 0
        for div in soup.find_all(["div", "article"]):
            n = len(div.get_text(strip=True))
            for sub in div.find_all(["div", "article"]):
                n -= len(sub.get_text(strip=True))
            if n > best_len:
                best, best_len = div, n
        return best


def get_parser(book_url: str, fetcher=None, auto: bool = True,
               lazy_auto: bool = False):
    """选择解析器，优先级："""
    rule = find_rule(book_url)
    if rule:
        return SiteRuleParser(rule)

    host = re.sub(r"^https?://", "", book_url).split("/")[0]
    for key, p in PARSERS.items():
        if key != "generic" and key in host:
            return p

    if auto and fetcher is not None:
        try:
            from .autodetect import load_learned
            learned = load_learned(host.split(":")[0])
            if learned:
                log.info("使用之前推断过的规则（%s）", learned.get("host"))
                return SiteRuleParser(learned)
        except Exception:
            pass

        if lazy_auto:
            return _LazyAutoParser(book_url, fetcher)
        try:
            from .autodetect import ensure_rule
            learned = ensure_rule(book_url, fetcher)
            if learned:
                return SiteRuleParser(learned)
        except Exception as e:
            log.info("自动推断不可用（%s），改用通用解析", e)

    return PARSERS["generic"]


class _LazyAutoParser:
    """先用通用解析；通用解析失败时才去推断站点结构。"""

    name = "generic+auto"

    def __init__(self, book_url, fetcher):
        self.book_url = book_url
        self.fetcher = fetcher
        self._generic = PARSERS["generic"]
        self._rule_parser = None
        self._tried = False

    def _try_detect(self):
        if self._tried:
            return self._rule_parser
        self._tried = True
        try:
            from .autodetect import ensure_rule
            learned = ensure_rule(self.book_url, self.fetcher)
            if learned:
                self._rule_parser = SiteRuleParser(learned)
        except Exception as e:
            log.info("自动推断未成功（%s），继续用通用解析", e)
        return self._rule_parser

    def parse_toc(self, html, base_url):
        try:
            return self._generic.parse_toc(html, base_url)
        except SystemExit:
            rp = self._try_detect()
            if rp is not None:
                return rp.parse_toc(html, base_url)
            raise

    def parse_chapter(self, html, **kw):
        if self._rule_parser is not None:
            return self._rule_parser.parse_chapter(html)
        return self._generic.parse_chapter(html, **kw)
