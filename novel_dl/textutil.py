"""正文清洗的公共工具。

两个解析器（规则解析器 / 通用解析器）原先各自复制了一份 `nav_junk` 正则，
改一处漏一处。这里统一成一份，避免漂移。
"""
import re

# 正文里需要整行丢弃的导航噪声。
# 注意：这是「整行匹配」，不要写会误伤正文的半截模式。
NAV_JUNK = re.compile(
    r"^(本章完| PS[:：]|请记住本站|最新章节|一秒记住|请关闭浏览器"
    r"|上一章|下一章|上一页|下一页|回目录|返回目录|章节目录|目录页"
    r"|加书签|加书签方便您下次阅读。?|相关阅读：?|目\s*录"
    r"|.{0,15}提示您[：:]看后求收藏.*"
    r"|.*所有内容均来自互联网.*|.*只为原作者.*|.*进行宣传.*"
    r"|.*欢迎各位书友支持.*|.*并收藏.*|^[《》]+$|&\w+;?"
    r"|温馨提示[：:]按.*|返回目录，按.*|阅读上一?页.*|阅读下一页.*"
    r"|[（(]?\s*第?\s*\d+\s*/\s*\d+\s*页?\s*[)）]?)$")

# 章节标题里的分页标记：「(第2/3页)」「（1 / 2）」
PAGE_MARK = re.compile(r"\s*[（(]\s*第?\s*\d+\s*/\s*\d+\s*页?\s*[)）]")

WHITESPACE = re.compile(r"[ \t\u3000]+")
MANY_NEWLINES = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """压缩空白：连续空格合一，3 个以上换行压成 2 个。"""
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
    kept = [ln for ln in lines
            if not is_noise(ln) and (drop_title is None or ln != drop_title)]
    return clean_text("\n".join(kept))
