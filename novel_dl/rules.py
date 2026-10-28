"""站点规则：加载、合并、按域名匹配。

设计约定（这也是本项目的立身之本）：
    程序自身不内置任何具体网站的规则。
    规则由使用者自己放在 JSON 文件里，本项目只提供格式和解析实现。

搜索顺序（先找到的同 host 规则优先，后加载的补充）：
    1. 环境变量 NOVEL_DL_RULES 指向的 JSON 文件（可多个，用 os.pathsep 分隔）
    2. 当前工作目录下的 sites.json
    3. 用户配置目录 ~/.novel-dl/sites.json
    4. 包目录内的 sites.json（如果你想把规则直接塞进安装目录）

规则字段见 docs/RULES.md。
"""
import json
import os
import re

from . import log

# 允许的字段——未知字段会被原样保留（向前兼容），这里只用于文档与校验提示
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

    # 兜底：主域相同也算（规则写 xxx.com，访问的是 www.xxx.com）
    domain = ".".join(host.split(".")[-2:])
    if domain:
        for r in rules:
            h = r["host"]
            if h == domain or h.endswith("." + domain):
                return r
    return None


def apply_url_fixes(book_url: str, rule: dict | None) -> str:
    """按规则的 url_fix 纠正用户贴错的目录页网址。

    url_fix 形如 [[正则, 替换], ...]，依次 re.sub。
    """
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
    """剥离 so-novel 风格的 `@js:` / `@java:` 后缀，只留 CSS 选择器。

    这类规则依赖页面里执行 JS 解密，本项目不做（也不该做）——
    但至少要能退化到「按选择器取原始文本」，而不是让非法 CSS 把整章炸掉。
    """
    return re.split(r"@(?:js|java):", selector or "", maxsplit=1)[0].strip()
