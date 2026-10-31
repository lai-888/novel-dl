"""识别「技术上取不到干净正文」的站点，并在下载前就拒绝。

有些站点故意用字体加密保护正文：把常用汉字替换成私有区码位
（U+E000–U+F8FF），页面只有加载特定字体才显示正常。

对这种站点，程序拿到的原文是：
    “喜\ue483喜\ue483，\ue423喜\ue483！”雷火馆\ue44d院，紧闭\ue4f3院...
一半字符是不可用的占位符。下载几百章全是废的。

这不是 bug，是站点有意的版权保护。正确的产品行为是：
**在抓第一页时就认出来，明确告诉用户这里下不了，不要再浪费时间和流量。**

检测放在**解析之前、看原始 HTML** —— 因为解析时 textutil 会把占位符
直接删掉，加密正文就变成「喜喜，喜！」这种看似通顺、实则缺一半字的东西，
清洗之后再检查就查不出来了。命中即整本停下，后续章节不再请求。
"""
import re

# 私有区字符：字体加密的典型产物
PUA = re.compile(r"[\ue000-\uf8ff]")

# 字体声明，作为辅助证据
FONT_FACE = re.compile(r"@font-face[^}]{0,400}", re.I)

# 去掉 HTML 标签，好从原文里取一段干净样本给用户看
HTML_TAG = re.compile(r"<[^>]{0,300}>")

# 判定阈值：私有区字符占比超过 15% 基本可以确定是字体加密。
# 正常中文小说里几乎不会出现私有区字符（图标字体仅出现在导航，不在正文）。
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


def detect_font_encryption(html: str, text: str) -> float:
    """返回正文里私有区字符的占比（0~1）。"""
    if not text or len(text) < MIN_SAMPLE:
        return 0.0
    pua = len(PUA.findall(text))
    return pua / len(text)


def sample_of(html: str, width: int = 80) -> str:
    """从 HTML 里取一段「像正文、且占位符最密」的片段做样本。

    直接截原始 HTML 会把 `content="`、属性名混进来，用户看不懂；
    而页面里预加载的 JSON（`\\u003Cp\\u003E...`）占位符密度最高，
    却是一串转义代码。所以先去掉标签，再只挑「像中文正文」的那几行。
    """
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
    """正文疑似字体加密时抛 EncryptedContent。

    检测必须基于**原始 HTML**，不能用清洗后的正文。
    原因：textutil.strip_invisible() 会把私有区字符直接删掉，
    于是加密正文变成「喜喜，喜！」这种看似通顺、实则缺字的东西 ——
    比明显乱码更危险。所以这里以 HTML 为准。
    """
    raw_pua = len(PUA.findall(html or ""))
    has_font = bool(FONT_FACE.search(html or ""))

    # 主判据：原始 HTML 里有大量私有区字符 + 声明了自定义字体
    if raw_pua >= 300 and has_font:
        raise EncryptedContent(
            1.0, sample_of(html),
            "该站点用自定义字体保护正文：页面里有 "
            f"{raw_pua} 个私有区占位符，并加载了专用 @font-face 字体。"
            "浏览器靠字体把它们显示成汉字，程序拿到的是原始码位。")

    # 次判据：解析出的正文里仍有私有区字符（清洗未生效的情况）
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
