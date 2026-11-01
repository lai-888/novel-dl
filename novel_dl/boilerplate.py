"""找出站点塞进正文的填充句，并在导出时去掉。

判据是统计出来的，不认具体网站：

* 一句话在很多章里都出现 —— 不是剧情，是站点灌的水
* 平均每章重复 1.3 次以上 —— 真作者的口头禅每章也就一次
* 或者它压根不像句子（全是分隔符、通篇只有水印词）

作者在后记里反复用的口头禅只满足第一条（每章一次），所以不会被误删。
"""

import math
import re

SENT = re.compile(r"[^。！？…!?\n]{8,}")
HAN = re.compile(r"[\u4e00-\u9fff]")
WATERMARK = ("免费阅读", "免费轻小说", "本站", "网址", "最新章节", "无弹窗",
             "手机版", "请记住", "全文阅读")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT.findall(text or "")]


def find_boilerplate(bodies: list[str], min_df: int = 4,
                     df_ratio: float = 0.05,
                     repeat_ratio: float = 1.3) -> set[str]:
    """返回「站点填充句」集合。bodies 是一本书每章的正文。"""
    chapters = [b for b in bodies if b]
    n = len(chapters)
    if n < 5:
        return set()

    df, tf = {}, {}
    for body in chapters:
        sents = split_sentences(body)
        for s in set(sents):
            df[s] = df.get(s, 0) + 1
        for s in sents:
            tf[s] = tf.get(s, 0) + 1

    need = max(min_df, math.ceil(n * df_ratio))
    bad = set()
    for s, c in df.items():
        if c < need:
            continue
        repeats = tf.get(s, 0) / c
        # 全是标点/分隔符，或者带水印词
        weird = not HAN.search(s) or any(w in s for w in WATERMARK)
        if weird or repeats >= repeat_ratio:
            bad.add(s)
    return bad


def strip_boilerplate(body: str, bad: set[str]) -> tuple[str, int]:
    """把命中的句子从正文里删掉，返回 (新正文, 删了几处)。

    站点常把填充句插进句子中间，句号也跟着断句，所以连它的句号一起删。
    """
    if not body or not bad:
        return body, 0
    removed = 0
    out_lines = []
    for line in body.splitlines():
        new = line
        for s in bad:
            for form in (s + "。", s + ".", s):
                if form in new:
                    removed += new.count(form)
                    new = new.replace(form, "")
        # 删完只剩标点就整行不要
        if HAN.search(new) or len(new.strip(" ，。、；：—-…·　")) > 0:
            out_lines.append(new.strip())
    text = "\n".join(ln for ln in out_lines if ln.strip())
    return re.sub(r"\n{3,}", "\n\n", text).strip(), removed
