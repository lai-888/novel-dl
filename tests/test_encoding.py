"""编码与乱码处理专项测试。

覆盖三类乱码来源：
  1. 服务端编码声明错误 / 缺失
  2. 声明 gb2312/gbk 但实际是 gb18030 超集内容
  3. 图标字体混淆（私有区字符）、零宽字符、替换字符

跑：python tests/test_encoding.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from novel_dl.encoding import (decode_body, looks_garbled,  # noqa: E402
                               sniff_meta_charset, upgrade_encoding)
from novel_dl.textutil import clean_text, strip_invisible  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  <- {detail}"))


CN = "第一章 少年提着灯走过长街，夜色沉沉。"


def main():
    print("\n[1] 编码升级：gb2312/gbk 一律升级到超集")
    check("gb2312 -> gb18030", upgrade_encoding("gb2312") == "gb18030")
    check("GBK -> gb18030", upgrade_encoding("GBK") == "gb18030")
    check("cp936 -> gb18030", upgrade_encoding("cp936") == "gb18030")
    check("big5 -> big5hkscs", upgrade_encoding("big5") == "big5hkscs")
    check("Shift_JIS -> cp932", upgrade_encoding("shift_jis") == "cp932")

    print("\n[2] GBK 站点：没声明编码")
    raw = CN.encode("gbk")
    text, used = decode_body(raw, "")
    check("能正确解码", text == CN, repr(text[:20]))
    check("识别出 gb18030", used == "gb18030", used)

    print("\n[3] 服务端声明错了（声明 utf-8 实际 gbk）")
    text, used = decode_body(raw, "utf-8")
    check("不被错误声明带偏", text == CN, repr(text[:20]))

    print("\n[4] 声明 gb2312，内容含 GBK 扩展字")
    hard = "第一章 龘靐齉"
    raw2 = hard.encode("gbk")
    strict_fails = False
    try:
        raw2.decode("gb2312")
    except UnicodeDecodeError:
        strict_fails = True
    check("严格 gb2312 确实会崩（验证测试有效）", strict_fails)
    text2, used2 = decode_body(raw2, "gb2312")
    check("升级后正确解码", text2 == hard, repr(text2))

    print("\n[5] UTF-8 站点不受影响")
    text3, used3 = decode_body(CN.encode("utf-8"), "utf-8")
    check("utf-8 正确", text3 == CN, repr(text3[:20]))

    print("\n[6] meta charset 嗅探")
    raw4 = b'<html><head><meta charset="gbk"><body>x</body></html>'
    check("能读出 meta gbk", sniff_meta_charset(raw4) == "gbk")
    raw5 = b'<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312">'
    check("能读出 http-equiv 形式", sniff_meta_charset(raw5) == "gb2312")

    print("\n[7] 乱码检测")
    check("正常中文不算乱码", not looks_garbled(CN * 50))
    mojibake = "æ\u0096\u0087ç« ç¬¬ä¸€ç« " * 50
    check("Latin-1 误读被判为乱码", looks_garbled(mojibake))

    print("\n[8] 私有区字符（图标字体混淆）")
    src = "\ue001章 少年\ue002"
    out = strip_invisible(src)
    check("私有区字符被剥离", out == "章 少年", repr(out))
    check("normal 文本不受影响", strip_invisible(CN) == CN)

    print("\n[9] 零宽字符 / BOM / 替换字符")
    check("零宽空格被剥离", strip_invisible("正\u200b文") == "正文")
    check("BOM 被剥离", strip_invisible("\ufeff正文") == "正文")
    check("替换字符被剥离", strip_invisible("正\ufffd文") == "正文")
    check("方向控制符被剥离", strip_invisible("正\u202e文") == "正文")

    print("\n[10] clean_text 一体化")
    messy = "第一段\ue001正文\u200b内容。\n\n\n\n第二段"
    out = clean_text(messy)
    check("清掉不可见字符", "\ue001" not in out and "\u200b" not in out, repr(out))
    check("多余空行被压缩", "\n\n\n" not in out, repr(out))
    check("正文内容保留", "第一段正文内容" in out.replace(" ", ""), repr(out))

    print("\n[11] 空输入不炸")
    check("空 bytes", decode_body(b"", "")[0] == "")
    check("空字符串 strip", strip_invisible("") == "")
    check("空 clean_text", clean_text("") == "")

    print(f"\n{'='*46}\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for n in FAIL:
            print("  ❌ " + n)
        return 1
    print("✅ 编码与乱码处理全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
