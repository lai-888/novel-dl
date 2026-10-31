"""正文清洗的公共工具。"""
import re

NAV_JUNK = re.compile(
    r"^(本章完| PS[:：]|请记住本站|最新章节|一秒记住|请关闭浏览器"
    r"|上一章|下一章|上一页|下一页|回目录|返回目录|章节目录|目录页"
    r"|加书签|加书签方便您下次阅读。?|相关阅读：?|目\s*录"
    r"|.{0,15}提示您[：:]看后求收藏.*"
    r"|.*所有内容均来自互联网.*|.*只为原作者.*|.*进行宣传.*"
    r"|.*欢迎各位书友支持.*|.*并收藏.*|^[《》]+$|&\w+;?"
    r"|温馨提示[：:]按.*|返回目录，按.*|阅读上一?页.*|阅读下一页.*"
    r"|[（(]?\s*第?\s*\d+\s*/\s*\d+\s*页?\s*[)）]?)$")

PAGE_MARK = re.compile(r"\s*[（(]\s*第?\s*\d+\s*/\s*\d+\s*页?\s*[)）]")

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


def is_noise(line: str) -> bool:
    """这一行是不是导航/广告噪声（整行匹配）。"""
    if not line:
        return True
    if len(line) <= 4 and line.isdigit():   # 分页数字
        return True
    return bool(NAV_JUNK.match(line))


def strip_page_mark(title: str) -> str:
    return PAGE_MARK.sub("", title).strip()


def body_from(lines, *, drop_title=None) -> str:
    """把正文行列表过滤 + 合并成最终正文。"""
    kept = [strip_invisible(ln) for ln in lines]
    kept = [ln for ln in kept
            if ln and not is_noise(ln)
            and (drop_title is None or ln != drop_title)]
    return clean_text("\n".join(kept))
