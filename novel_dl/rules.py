"""站点规则：加载、合并、按域名匹配。"""
import json
import os
import re

from . import log

KNOWN_FIELDS = {
    "host", "name", "url", "toc_item", "toc_desc", "ch_title", "ch_content",
    "filter_txt", "filter_tag", "next_page", "url_fix", "encoding",
}

_CACHE = {}


def _candidate_paths() -> list[str]:
    paths = []
    env = os.environ.get("NOVEL_DL_RULES", "")
    if env:
        paths.extend(p for p in env.split(os.pathsep) if p.strip())
    paths.append(os.path.join(os.getcwd(), "sites.json"))
    paths.append(os.path.join(os.path.expanduser("~"), ".novel-dl", "sites.json"))
    paths.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sites.json"))
    return paths


def _normalize(rule: dict) -> dict:
    """补全 host，并剔除注释用的 _ 开头字段。"""
    rule = {k: v for k, v in rule.items() if not k.startswith("_")}
    if not rule.get("host"):
        m = re.search(r"https?://([^/]+)", rule.get("url") or "")
        rule["host"] = m.group(1).lower() if m else ""
    else:
        rule["host"] = str(rule["host"]).lower()
    return rule


def load_rules(force: bool = False) -> list[dict]:
    """读取并合并所有规则文件。文件损坏只警告，不影响其他来源。"""
    if _CACHE.get("rules") is not None and not force:
        return _CACHE["rules"]

    rules, seen, used = [], set(), []
    for path in _candidate_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception as e:
            log.warning("规则文件 %s 读取失败：%s", path, e)
            continue
        if not isinstance(data, list):
            log.warning("规则文件 %s 顶层应为数组，已跳过", path)
            continue
        added = 0
        for raw in data:
            if not isinstance(raw, dict):
                continue
            rule = _normalize(raw)
            if not rule.get("host") or rule["host"] in seen:
                continue
            seen.add(rule["host"])
            rules.append(rule)
            added += 1
        used.append((path, added))

    _CACHE["rules"] = rules
    _CACHE["sources"] = used
    return rules


def rule_sources() -> list[tuple[str, int]]:
    load_rules()
    return _CACHE.get("sources", [])


def find_rule(book_url: str) -> dict | None:
    """按域名匹配规则。先精确/子域匹配，再退回注册域匹配。"""
    m = re.search(r"https?://([^/]+)", book_url)
    if not m:
        return None
    host = m.group(1).lower().split(":")[0]
    rules = load_rules()

    for r in rules:
        h = r["host"]
        if host == h or host.endswith("." + h):
            return r

    domain = ".".join(host.split(".")[-2:])
    if domain:
        for r in rules:
            h = r["host"]
            if h == domain or h.endswith("." + domain):
                return r
    return None


def apply_url_fixes(book_url: str, rule: dict | None) -> str:
    """按规则的 url_fix 纠正用户贴错的目录页网址。"""
    if not rule or not rule.get("url_fix"):
        return book_url
    for pair in rule["url_fix"]:
        try:
            pattern, repl = pair[0], pair[1]
        except (TypeError, IndexError, KeyError):
            log.warning("url_fix 条目格式错误（应为 [正则, 替换]），已跳过：%r", pair)
            continue
        fixed = re.sub(pattern, repl, book_url)
        if fixed != book_url:
            log.info("已按站点规则纠正网址：%s", fixed)
            book_url = fixed
    return book_url


def plain_selector(selector: str) -> str:
    """剥离 so-novel 风格的 `@js:` / `@java:` 后缀，只留 CSS 选择器。"""
    return re.split(r"@(?:js|java):", selector or "", maxsplit=1)[0].strip()
