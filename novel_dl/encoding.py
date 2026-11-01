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

PRIVATE_USE = re.compile(r"[\ue000-\uf8ff]")

try:                                   # 可选依赖，没装就少一路判断
    from charset_normalizer import from_bytes as _cn_from_bytes
except Exception:
    _cn_from_bytes = None


def guess_encoding(raw: bytes, head: int = 20000) -> str:
    """charset_normalizer 投票出的第一名；没装、没结论就返回空串。"""
    if _cn_from_bytes is None or not raw:
        return ""
    try:
        best = _cn_from_bytes(raw[:head]).best()
    except Exception:
        return ""
    name = getattr(best, "encoding", "") if best is not None else ""
    return upgrade_encoding(name) if name else ""


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
    if not s:
        return False
    bad = len(MOJIBAKE_HINTS.findall(s))
    if bad > max(3, len(s) // 200):
        return True
    pua = len(PRIVATE_USE.findall(s))       # 私有区成片出现 = 解错编码
    return pua > max(2, len(s) // 10)


def _try_decode(raw: bytes, enc: str) -> str | None:
    try:
        return raw.decode(upgrade_encoding(enc))
    except (UnicodeDecodeError, LookupError):
        return None


def decode_body(raw: bytes, declared: str = "") -> tuple[str, str]:
    """把响应字节解成文本，尽最大努力不出乱码。"""
    if not raw:
        return "", "utf-8"

    # UTF-8 能严格解开，基本就是 UTF-8 —— 别被站点写错的声明带偏
    try:
        text = raw.decode("utf-8")
        if not looks_garbled(text):
            return text, "utf-8"
    except UnicodeDecodeError:
        pass

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

    # 前面都不像话，再问一次 charset_normalizer
    guess = guess_encoding(raw)
    if guess and guess not in seen:
        text = _try_decode(raw, guess)
        if text is not None and not looks_garbled(text):
            log.info("按 charset_normalizer 的判断用了 %s", guess)
            return text, guess

    if first_ok is not None:
        text, e = first_ok
        log.warning("编码可能不准（最接近的是 %s），已尽力解码", e)
        return text, e

    log.warning("无法确定网页编码，已用替换模式解码（可能有少量乱码）")
    return raw.decode("utf-8", errors="replace"), "utf-8/replace"
