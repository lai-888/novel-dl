"""编码处理：把「乱码」这一类问题集中解决。

乱码有三种不同来源，得分开治：

1. **服务端编码声明错误或缺失**
   requests 的 apparent_encoding 用 chardet 猜，对小样本（几 KB 的中文页）
   经常猜错；猜成 ISO-8859-1 时中文会变成 "æ–‡" 这种。
   对策：按优先级解析 —— 响应头 → HTML meta → 试解码 → 猜。

2. **声明 gb2312 / gbk，实际内容是 gb18030**
   很多老站声明 gb2312，但正文里有生僻字或标点用的是 gbk/gb18030 扩展区，
   严格按 gb2312 解码会抛 UnicodeDecodeError。
   对策：把 gb2312/gbk 一律升级为超集 gb18030（能解 gb2312/gbk 的全部内容）。

3. **图标字体混淆**（第X章 的「第」被换成私有区字符 U+E000–U+F8FF）
   这不是编码问题，是字体缺字。需在解析层剥离。

本模块只负责 1 和 2，第 3 类在 parser.py 处理。
"""
import codecs
import re

from . import log

# 声明的编码 → 实际应该用的超集编码。
# gb2312、gbk 都是 gb18030 的子集，用 gb18030 解码永远比它们更宽松。
SUPERSET = {
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "x-gbk": "gb18030",
    "cp936": "gb18030",
    "ms936": "gb18030",
    "euc-cn": "gb18030",
    "big5": "big5hkscs",     # 港台站常见，big5hkscs 是超集
    "shift_jis": "cp932",    # 日文站，cp932 是超集
    "sjis": "cp932",
}

META_CHARSET = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w\-]+)""", re.I)

# 候选顺序：中文站绝大多数是这三种
FALLBACKS = ["utf-8", "gb18030", "big5hkscs"]

# 「乱码」的典型特征：C1 控制符和典型的 Latin-1 误读产物
MOJIBAKE_HINTS = re.compile(r"[\u0080-\u009f]|(?:Ã[\u0080-\u00bf])|(?:â€)")


def upgrade_encoding(name: str) -> str:
    """把声明的编码升级为超集，避免「声明 gb2312 实则 gbk」的炸裂。"""
    if not name:
        return ""
    n = name.strip().lower()
    n = SUPERSET.get(n, n)
    try:
        return codecs.lookup(n).name
    except LookupError:
        return n


def sniff_meta_charset(raw: bytes, head: int = 4096) -> str:
    """从 HTML 头部找 <meta charset=...>。声明经常是错的，仅作候选。"""
    m = META_CHARSET.search(raw[:head])
    if not m:
        return ""
    try:
        return m.group(1).decode("ascii", "ignore").strip()
    except Exception:
        return ""


def looks_garbled(text: str, sample: int = 2000) -> bool:
    """判断解出来的文本是不是乱码。

    只看特征字符比例：正常中文小说里不会有 C1 控制符，
    也不会有成片的 "Ã..." 这种 UTF-8 被当 Latin-1 读的产物。
    """
    if not text:
        return False
    s = text[:sample]
    bad = len(MOJIBAKE_HINTS.findall(s))
    return bad > max(3, len(s) // 200)


def _try_decode(raw: bytes, enc: str) -> str | None:
    try:
        return raw.decode(upgrade_encoding(enc))
    except (UnicodeDecodeError, LookupError):
        return None


def decode_body(raw: bytes, declared: str = "") -> tuple[str, str]:
    """把响应字节解成文本，尽最大努力不出乱码。

    参数 declared 是服务端/HTTP 头声明的编码。
    返回 (文本, 实际使用的编码)。

    策略（按顺序，先成功且不像乱码的胜出）：
        1. 声明的编码（升级为超集后）
        2. HTML meta 里声明的编码
        3. utf-8（现代站主流）
        4. gb18030（老中文站主流，覆盖 gb2312/gbk 全部码位）
        5. big5hkscs（港台）
        6. 最后退回 chardet 猜测
    每一步解出来后检查是否像乱码，像就继续试下一个。
    """
    if not raw:
        return "", "utf-8"

    candidates = []
    if declared:
        candidates.append(declared)
    meta = sniff_meta_charset(raw)
    if meta:
        candidates.append(meta)
    candidates.extend(FALLBACKS)

    seen = set()
    first_ok = None
    for enc in candidates:
        e = upgrade_encoding(enc)
        if not e or e in seen:
            continue
        seen.add(e)
        text = _try_decode(raw, e)
        if text is None:
            continue
        if first_ok is None:
            first_ok = (text, e)
        if not looks_garbled(text):
            return text, e

    if first_ok is not None:
        text, e = first_ok
        log.warning("编码可能不准（最接近的是 %s），已尽力解码", e)
        return text, e

    # 全部失败：用替换字符兜底，至少不中断下载
    log.warning("无法确定网页编码，已用替换模式解码（可能有少量乱码）")
    return raw.decode("utf-8", errors="replace"), "utf-8/replace"
