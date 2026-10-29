"""章节内容质量检测：乱码、空章、截断。

用户的核心诉求：「下载好了但其实是乱码，用户却不知道」。

所以不能只在解码环节尽力而为，还要**在结果上做体检**：
每章存下来之后，判断它是不是可信的中文正文。不可信的标出来，
让用户在预览里一眼看到，而不是等到打开 TXT 才发现。

检测分三类：
  1. 乱码 —— 问号方块、锟斤拷、Latin-1 误读、替换符
  2. 空壳 —— 正文过短，或只有标题/导航
  3. 异常 —— 中文字符占比过低
"""
import re

# 典型乱码特征，按可信度排列
GARBLE_PATTERNS = [
    (re.compile(r"[ï¿½\ufffd]"), "替换字符"),
    (re.compile(r"锟斤拷"), "锟斤拷"),
    (re.compile(r"(?:Ã[\u0080-\u00bf]){2,}"), "UTF-8 被当 Latin-1"),
    (re.compile(r"(?:â€[\u0099\u009c\u009d\u0093\u0094])+"), "引号误码"),
    (re.compile(r"[\u0080-\u009f]{2,}"), "C1 控制符"),
    (re.compile(r"(?:&#\d{4,5};){3,}"), "成片数字实体"),
    (re.compile(r"\?{4,}"), "成片问号"),
    (re.compile(r"(?:[\u4e00-\u9fa5][\ufffd?]){3,}"), "中英混杂问号"),
]

# 正文里正常会有的大量字符
CJK = re.compile(r"[\u4e00-\u9fa5]")
SENTENCE_END = re.compile(r"[。！？…」』”\n]")

# 乱码字符：私有区 + 拉丁扩展 + 各种方块。这些在正常中文正文里不该成片出现。
SUSPICIOUS = re.compile(
    r"[\ue000-\uf8ff"          # 私用区（图标字体/错误解码）
    r"\u0100-\u024f"           # 拉丁扩展（GBK 被当 UTF-8 的典型产物）
    r"\u0370-\u03ff"           # 希腊字母（同上）
    r"\u2500-\u257f"           # 制表符方块
    r"]")


def analyze(title: str, body: str) -> dict:
    """给一章做体检，返回结构化结论。

    返回：
        ok        —— 是否可信
        issues    —— 问题列表（人类可读）
        level     —— "ok" / "warn" / "bad"
        cjk_ratio —— 中文字符占比
        length    —— 正文字数
    """
    body = body or ""
    issues = []
    level = "ok"

    # 去掉标题行再评估正文
    text = body.split("\n\n", 1)[-1] if "\n\n" in body else body
    length = len(text.strip())
    cjk = len(CJK.findall(text))
    suspicious = len(SUSPICIOUS.findall(text))
    cjk_ratio = (cjk / length) if length else 0.0

    # ---- 1. 乱码 ----
    for pat, name in GARBLE_PATTERNS:
        m = pat.search(text)
        if m:
            issues.append(f"疑似乱码（{name}）")
            level = "bad"
            break

    # 可疑字符成片出现 = 解码错了。阈值取「每千字超过 5 个」，
    # 避免把正常的英文名、标点算成乱码。
    if length >= 100 and suspicious / length > 0.005:
        issues.append(f"疑似乱码（异常字符 {suspicious}/{length}）")
        level = "bad"
    elif suspicious and length >= 100:
        if level == "ok":
            issues.append(f"有 {suspicious} 个异常字符")
            level = "warn"

    # ---- 2. 空壳 ----
    if length == 0:
        issues.append("正文为空")
        level = "bad"
    elif length < 80:
        issues.append(f"正文过短（{length} 字）")
        if level != "bad":
            level = "warn"
    elif level != "bad" and not SENTENCE_END.search(text):
        issues.append("没有句子结束标点，可能是残缺抓取")
        level = "warn"

    # ---- 3. 中文占比 ----
    # 中文小说正常在 0.45 以上；但正文里标点、数字、空格占比不低，
    # 实测健康章节会在 0.34~0.45 之间，所以阈值不能定太高。
    if length >= 80:
        if cjk_ratio < 0.10:
            issues.append(f"中文字符占比过低（{cjk_ratio:.0%}）")
            level = "bad"
        elif cjk_ratio < 0.20:
            issues.append(f"中文字符占比偏低（{cjk_ratio:.0%}）")
            if level == "ok":
                level = "warn"

    return {
        "ok": level == "ok",
        "level": level,
        "issues": issues,
        "cjk_ratio": round(cjk_ratio, 3),
        "length": length,
    }


def summarize(reports: list[dict]) -> dict:
    """给整本书做汇总，用于在列表页显示「有没有问题」。"""
    bad = [r for r in reports if r.get("level") == "bad"]
    warn = [r for r in reports if r.get("level") == "warn"]
    return {
        "total": len(reports),
        "ok": len(reports) - len(bad) - len(warn),
        "warn": len(warn),
        "bad": len(bad),
        "healthy": not bad and not warn,
    }
