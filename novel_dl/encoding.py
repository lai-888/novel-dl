"""编码处理：把「乱码」这一类问题集中解决。"""
import codecs
import re

from . import log

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

FALLBACKS = ["utf-8", "gb18030", "big5hkscs"]

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
    """判断解出来的文本是不是乱码。"""
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
    """把响应字节解成文本，尽最大努力不出乱码。"""
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

    log.warning("无法确定网页编码，已用替换模式解码（可能有少量乱码）")
    return raw.decode("utf-8", errors="replace"), "utf-8/replace"
