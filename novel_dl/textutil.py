"""正文清洗的公共工具。"""
import re
import unicodedata

NAV_JUNK = re.compile(
    r"^(本章完| PS[:：]|请记住本站|最新章节|一秒记住|请关闭浏览器"
    r"|上一章|下一章|上一页|下一页|回目录|返回目录|章节目录|目录页"
    r"|加书签|〔?加入书签〕?|加书签方便您下次阅读。?|相关阅读：?|目\s*录"
    r"|封面|繁体|繁體|首页|顶部|底部|书架|阅读记录|搜索小说|举报|投推荐票"
    r"|热门小说推荐|最近更新小说|相关推荐|猜你喜欢|推荐阅读"
    r"|.{0,15}提示您[：:]看后求收藏.*"
    r"|.*所有内容均来自互联网.*|.*只为原作者.*|.*进行宣传.*"
    r"|.*欢迎各位书友支持.*|.*并收藏.*|^[《》]+$|&\w+;?"
    r"|温馨提示[：:]按.*|返回目录，按.*|阅读上一?页.*|阅读下一页.*"
    r"|本站.{0,8}域名[:：].*"
    r"|^.{0,12}域名[:：]\s*[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+.*$"
    r"|[（(]?\s*第?\s*\d+\s*/\s*\d+\s*页?\s*[)）]?)$")

RECOMMEND_HEAD = re.compile(
    r"^(热门小说推荐|最近更新小说|相关推荐|猜你喜欢|推荐阅读|本周强推|最新入库)$")

REDUNDANT_TITLE = re.compile(
    r"^第[0-9零一二三四五六七八九十百千]{1,6}[章节回卷].{0,40}[（(]\s*\d+\s*/\s*\d+\s*[)）]$")

PAGE_MARK = re.compile(r"\s*[（(]\s*第?\s*\d+\s*/\s*\d+\s*页?\s*[)）]")

BRACKETED = re.compile(r"^[（(\[【]\s*(.+?)\s*[）)\]】]$")

WHITESPACE = re.compile(r"[ \t\u3000]+")
MANY_NEWLINES = re.compile(r"\n{3,}")

PUA_CHARS = re.compile(r"[\ue000-\uf8ff]")
ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
REPLACEMENT = re.compile(r"\ufffd")

INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\r\n\t\x00-\x1f]')
WIN_RESERVED = ({"CON", "PRN", "AUX", "NUL"}
                | {f"COM{i}" for i in range(1, 10)}
                | {f"LPT{i}" for i in range(1, 10)})


def safe_filename(name: str, max_len: int = 120) -> str:
    """书名 → 各平台都能落盘的文件名。"""
    s = INVALID_FILENAME.sub("_", (name or "").strip())
    s = WHITESPACE.sub(" ", s).strip(" .")
    if not s:
        return "未命名"
    if s.split(".")[0].upper() in WIN_RESERVED:
        s = "_" + s
    s = s[:max_len].strip(" .")
    return s or "未命名"


def strip_invisible(text: str) -> str:
    """剥掉私有区字符、零宽字符、替换字符 —— 这些是「看着像乱码」的主要来源。"""
    if not text:
        return text
    text = PUA_CHARS.sub("", text)
    text = ZERO_WIDTH.sub("", text)
    text = REPLACEMENT.sub("", text)
    return text


def clean_text(text: str) -> str:
    """清不可见字符 + 压缩空白（连续空格合一，3 个以上换行压成 2 个）。"""
    text = strip_invisible(text)
    text = WHITESPACE.sub(" ", text)
    text = MANY_NEWLINES.sub("\n\n", text)
    return text.strip()


def _fold(s: str) -> str:
    """只给判定用：把全角折成半角，正文本身一个字都不改。"""
    try:
        return unicodedata.normalize("NFKC", s or "")
    except Exception:
        return s or ""


def _drop_matches(pattern, s: str) -> str:
    """在折过的副本上找位置，按位置删原文 —— 保留原文里没被删的那些字。"""
    folded = _fold(s)
    if len(folded) != len(s or ""):        # 折完长度变了，位置对不上，退回整串
        return pattern.sub("", s or "")
    out, last = [], 0
    for m in pattern.finditer(folded):
        out.append(s[last:m.start()])
        last = m.end()
    out.append(s[last:])
    return "".join(out)


def is_noise(line: str) -> bool:
    """这一行是不是导航/广告噪声（整行匹配）。"""
    if not line:
        return True
    if len(line) <= 4 and line.isdigit():   # 分页数字
        return True
    folded = _fold(line)
    if NAV_JUNK.match(folded):
        return True
    inner = BRACKETED.match(folded)          # 「（本章完）」这种套了括号的
    return bool(inner and NAV_JUNK.match(inner.group(1)))


def strip_page_mark(title: str) -> str:
    return _drop_matches(PAGE_MARK, title).strip()


def _norm_title(s: str) -> str:
    return strip_page_mark(s or "")


def drop_recommend_blocks(lines: list) -> list:
    """删掉「热门小说推荐」这类板块：标记行之后连续的书名，直到正文重新开始。"""
    out, skipping, budget = [], False, 0
    for ln in lines:
        if RECOMMEND_HEAD.match(_fold(ln)):
            skipping, budget = True, 0
            continue
        if skipping:
            budget += 1
            prose = len(ln) > 30 or ln.endswith(("。", "！", "？", "”", "」", "』"))
            if prose or budget > 20:
                skipping = False
            else:
                continue
        out.append(ln)
    return out


def body_from(lines, *, drop_title=None) -> str:
    """把正文行列表过滤 + 合并成最终正文。"""
    target = _norm_title(drop_title) if drop_title else None
    cleaned = [strip_invisible(ln).strip() for ln in lines]
    # 先整块删推荐位（它的标题行本身就是噪声，先过滤就认不出块了）
    cleaned = drop_recommend_blocks([ln for ln in cleaned if ln])
    kept = []
    for ln in cleaned:
        if not ln or is_noise(ln):
            continue
        if target and _norm_title(ln) == target:
            continue
        if REDUNDANT_TITLE.match(_fold(ln)):   # 「第9章 xxx(1/2)」这类重复标题
            continue
        kept.append(ln)
    return clean_text("\n".join(kept))
