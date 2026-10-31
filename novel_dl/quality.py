"""章节内容质量检测：乱码、空章、截断。"""
import re

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

CJK = re.compile(r"[\u4e00-\u9fa5]")
SENTENCE_END = re.compile(r"[。！？…」』”\n]")

SUSPICIOUS = re.compile(
    r"[\ue000-\uf8ff"          # 私用区（图标字体/错误解码）
    r"\u0100-\u024f"
    r"\u0370-\u03ff"           # 希腊字母（同上）
    r"\u2500-\u257f"           # 制表符方块
    r"]")


def analyze(title: str, body: str) -> dict:
    """给一章做体检，返回结构化结论。"""
    body = body or ""
    issues = []
    level = "ok"

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
