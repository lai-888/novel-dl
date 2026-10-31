"""识别「技术上取不到干净正文」的站点，并在下载前就拒绝。"""
import re

PUA = re.compile(r"[\ue000-\uf8ff]")

FONT_FACE = re.compile(r"@font-face[^}]{0,400}", re.I)

HTML_TAG = re.compile(r"<[^>]{0,300}>")

ENCRYPTED_RATIO = 0.15
MIN_SAMPLE = 200


class EncryptedContent(Exception):
    """站点用字体加密保护正文，无法取得干净文本。"""

    def __init__(self, ratio: float, sample: str, hint: str = ""):
        self.ratio = ratio
        self.sample = sample
        self.hint = hint
        super().__init__(
            f"正文被字体加密（{ratio:.0%} 的字符是不可读占位符）")


class FillerPage(Exception):
    """拿到的不是正文，而是站点的占位提示（「正在手打中」之类）。"""


class DuplicatePage(Exception):
    """站点把别的章节正文又放了一遍（排版错位），取到的内容不属于本章。"""


def detect_font_encryption(html: str, text: str) -> float:
    """返回正文里私有区字符的占比（0~1）。"""
    if not text or len(text) < MIN_SAMPLE:
        return 0.0
    pua = len(PUA.findall(text))
    return pua / len(text)

# ---------- 付费章节：只拿到「免费预览」 ----------
PAYWALL_FLAG = re.compile(r"""isfree["']?\s*[:=]\s*false""", re.I)
PAYWALL_WORDS = ("购买本章", "余额不足", "订阅本章", "付费章节")
PREVIEW_MAX = 400

FILLER_PATTERNS = ("正在手打中", "手打中", "请稍等片刻", "内容更新后",
                   "重新刷新页面", "内容正在加载", "本章还没")
SHORT_CHAPTER = 400
_SENTENCE_END = "。！？…”」』）~～.!?"


def filler_reason(text: str):
    """是不是站点的占位页；是则返回原因，否则 None。"""
    s = (text or "").strip()
    if not s:
        return None
    head = s[:800]
    for kw in FILLER_PATTERNS:
        if kw in head:
            return f"站点占位页（{len(s)} 字，提示「{kw}」），这一章还没生成"
    return None


def looks_truncated(text: str, floor: int = SHORT_CHAPTER) -> bool:
    """正文短得可疑，且结尾不像一句话说完 —— 多半只抓到了半截。"""
    s = (text or "").strip()
    if not s or len(s) >= floor:
        return False
    return s[-1] not in _SENTENCE_END


def preview_reason(html: str, text: str):
    """这一章是不是只拿到了「免费预览」；是则返回原因，否则返回 None。"""
    n = len(text or "")
    if n >= PREVIEW_MAX:
        return None
    h = html or ""
    if PAYWALL_FLAG.search(h):
        return f"付费章节，只拿到 {n} 字的免费预览"
    if any(w in h for w in PAYWALL_WORDS):
        return f"疑似付费章节，只拿到 {n} 字的预览"
    return None


def sample_of(html: str, width: int = 80) -> str:
    """从 HTML 里取一段「像正文、且占位符最密」的片段做样本。"""
    plain = HTML_TAG.sub("\n", html or "")
    lines = [ln.strip() for ln in plain.splitlines() if ln.strip()]

    def prose(s):
        """像正文：有真实汉字，且几乎没有代码符号。"""
        cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
        noise = sum(1 for ch in s if ch in '{}[]\\"/:;=<>')
        return cjk >= 8 and noise <= max(3, len(s) // 40)

    with_pua = [ln for ln in lines if PUA.search(ln)]
    cands = [ln for ln in with_pua if prose(ln)] or with_pua
    if not cands:
        return ""
    line = max(cands, key=lambda s: len(PUA.findall(s)))
    m = PUA.search(line)
    start = max(0, m.start() - 20) if m else 0
    return "".join(ch if ch.isprintable() else "?"
                   for ch in line[start:start + width])


def check_encrypted(html: str, text: str, url: str = "") -> None:
    """正文疑似字体加密时抛 EncryptedContent。"""
    raw_pua = len(PUA.findall(html or ""))
    has_font = bool(FONT_FACE.search(html or ""))

    if raw_pua >= 300 and has_font:
        raise EncryptedContent(
            1.0, sample_of(html),
            "该站点用自定义字体保护正文：页面里有 "
            f"{raw_pua} 个私有区占位符，并加载了专用 @font-face 字体。"
            "浏览器靠字体把它们显示成汉字，程序拿到的是原始码位。")

    ratio = detect_font_encryption(html, text)
    if ratio > ENCRYPTED_RATIO:
        raise EncryptedContent(ratio, sample_of(text), "正文里大量字符是私有区占位符。")


def explain(e: EncryptedContent) -> str:
    """给用户看的说明。"""
    return (
        f"这个网站把正文用字体加密了（{e.ratio:.0%} 的字符是不可读占位符）。\n\n"
        f"这是站点有意的版权保护措施：文字在网页上能看，是因为浏览器加载了"
        f"它专用的字体；程序拿到的是原始占位符，无法还原成正常文字。\n\n"
        f"样本：{e.sample[:60]}\n\n"
        f"已停止下载，没有产生无效文件。"
    )
