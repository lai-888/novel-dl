"""内容质检 + 书架 + 章节预览 —— 专项测试。

用户诉求：「下载好了但其实是乱码，用户却不知道」。
所以这里的重点是**脏数据必须被认出来**，健康数据不能被误报。

跑：python tests/test_library.py
"""
import io
import json
import os
import shutil
import sys
import uuid

sys.path.insert(0, r"C:\Users\31383\Desktop\novel-dl")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from novel_dl import library  # noqa: E402
from novel_dl.quality import analyze, summarize  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  <- {detail}"))


GOOD = "夜色沉沉，他提灯走过长街，远处传来打更的声音。风从巷口灌进来，吹得灯焰一阵摇晃。"
# 实测到的两种真实乱码样本
GARBLE_ECM = "νêΘνè§νë†οΦ¨ν£Ξν™Ξνë‰νêΕψÄ²δΦëφ¹·νêΕνë¥νëâνê²οΦ¨ν£öνêΘνè§γîöνêûνî΄εΩΒψÄ²"
GARBLE_GBK = "ÚŤĚšüźÚŽć´╝îţĹŹÚÖóŃÇéţÉÇţô╝ňĄĽÚś│ţöĽŔÉŻ´╝îţĺëŔ║»Šî║Šőö"


def main():
    print("\n[1] 健康正文不能误报")
    r = analyze("第1章 测试", f"第1章 测试\n\n{GOOD * 10}")
    check("判定为 ok", r["level"] == "ok", str(r["issues"]))
    check("中文占比合理", 0.3 < r["cjk_ratio"] <= 1.0, str(r["cjk_ratio"]))
    check("字数统计正确", r["length"] > 200, str(r["length"]))

    print("\n[2] 真实乱码必须抓到")
    for name, sample in (("UTF-8 被当 ECM", GARBLE_ECM),
                         ("GBK 被当 UTF-8", GARBLE_GBK)):
        r = analyze("第X章", f"第X章\n\n{sample * 6}")
        check(f"{name} → bad", r["level"] == "bad", f"{r['level']} {r['issues']}")

    print("\n[3] 替换字符 / 锟斤拷")
    for name, bad in (("替换字符", "\ufffd" * 30),
                      ("锟斤拷", "锟斤拷" * 10),
                      ("问号方块", "?????" * 10)):
        r = analyze("t", f"t\n\n{GOOD * 3}{bad}")
        check(f"{name} → 抓到", r["level"] in ("bad", "warn"), str(r["issues"]))

    print("\n[4] 空章与短章")
    r = analyze("第1章", "")
    check("空正文 → bad", r["level"] == "bad", str(r))
    r = analyze("第1章", "第1章\n\n短")
    check("极短正文 → warn/bad", r["level"] in ("warn", "bad"), str(r))
    r = analyze("第1章", f"第1章\n\n{GOOD * 2}")
    check("正常短章 → ok", r["level"] == "ok", str(r["issues"]))

    print("\n[5] 中英混排的正文不应误判")
    mixed = "他打开了 GitHub，clone 了一个 repository，然后运行 npm install。" * 8
    r = analyze("t", f"t\n\n{mixed}")
    check("中英混排 → ok", r["level"] == "ok", f"{r['level']} cjk={r['cjk_ratio']}")

    print("\n[6] 汇总统计")
    s = summarize([{"level": "ok"}, {"level": "ok"},
                   {"level": "warn"}, {"level": "bad"}])
    check("total 正确", s["total"] == 4, str(s))
    check("ok 计数正确", s["ok"] == 2, str(s))
    check("warn 计数正确", s["warn"] == 1, str(s))
    check("bad 计数正确", s["bad"] == 1, str(s))
    check("有问题时 healthy=False", s["healthy"] is False)
    s2 = summarize([{"level": "ok"}, {"level": "ok"}])
    check("全好时 healthy=True", s2["healthy"] is True)

    # ------------------------------------------------------------------
    print("\n[7] 书架扫描：标准结构（cache/ + state.json）")
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".tmp", f"lib_{uuid.uuid4().hex[:8]}")
    book = os.path.join(root, "example.com_book_1")
    cache = os.path.join(book, "cache")
    os.makedirs(cache, exist_ok=True)
    try:
        toc = [["第1章 开端", "http://x/1.html"],
               ["第2章 乱码", "http://x/2.html"],
               ["第3章 未下载", "http://x/3.html"]]
        with open(os.path.join(book, "state.json"), "w", encoding="utf-8") as f:
            json.dump({"title": "测试书", "book_url": "http://x/book/1/",
                       "toc": toc, "done": [0, 1], "failed": [],
                       "fail_log": {}}, f, ensure_ascii=False)
        with open(os.path.join(cache, "00000.txt"), "w", encoding="utf-8") as f:
            f.write(f"第1章 开端\n\n{GOOD * 5}")
        with open(os.path.join(cache, "00001.txt"), "w", encoding="utf-8") as f:
            f.write(f"第2章 乱码\n\n{GARBLE_GBK * 5}")

        books = library.scan(root)
        check("扫描到 1 本书", len(books) == 1, str(len(books)))
        bk = books[0]
        check("书名正确", bk["title"] == "测试书", bk["title"])
        check("章节数正确", len(bk["chapters"]) == 3, str(len(bk["chapters"])))
        check("健康章判 ok", bk["chapters"][0]["level"] == "ok",
              str(bk["chapters"][0]))
        check("乱码章判 bad", bk["chapters"][1]["level"] == "bad",
              str(bk["chapters"][1]))
        check("未下载章标 pending", bk["chapters"][2]["status"] == "pending",
              str(bk["chapters"][2]))
        check("汇总 bad=1", bk["stats"]["bad"] == 1, str(bk["stats"]))

        print("\n[8] 章节读取")
        ch = library.read_chapter(bk, 1)
        check("能读到第1章", ch is not None)
        check("标题正确", ch["title"] == "第1章 开端", str(ch and ch["title"]))
        check("正文非空", len(ch["text"]) > 100, str(len(ch and ch["text"])))
        check("带质检信息", "quality" in ch and ch["quality"]["level"] == "ok",
              str(ch and ch["quality"]))
        check("越界返回 None", library.read_chapter(bk, 99) is None)
        check("越界 0 返回 None", library.read_chapter(bk, 0) is None)

        # ------------------------------------------------------------------
        print("\n[9] 书架扫描：合并 TXT 结构（GitHub 型）")
        gh = os.path.join(root, "gh_《某书》")
        os.makedirs(gh, exist_ok=True)
        with open(os.path.join(gh, "《某书》.txt"), "w", encoding="utf-8") as f:
            f.write("《某书》\n来源: http://x\n\n")
            f.write(f"=== 第一章 开始 ===\n\n{GOOD * 4}\n\n")
            f.write(f"=== 第二章 坏了 ===\n\n{GARBLE_ECM * 4}\n\n")

        books2 = library.scan(root)
        g = next((b for b in books2 if b["slug"].startswith("gh_")), None)
        check("识别出合并型书", g is not None)
        if g:
            check("书名去掉 gh_ 和书名号", g["title"] == "某书", g["title"])
            check("切出 2 章", len(g["chapters"]) == 2, str(len(g["chapters"])))
            check("第1章健康", g["chapters"][0]["level"] == "ok",
                  str(g["chapters"][0]))
            check("第2章乱码", g["chapters"][1]["level"] == "bad",
                  str(g["chapters"][1]))
            ch2 = library.read_chapter(g, 1)
            check("合并型能读章节", ch2 and "第一章" in ch2["title"],
                  str(ch2 and ch2["title"]))

        print("\n[10] 有问题的书排前面")
        titles = [b["title"] for b in books2]
        check("bad 的排在最前", books2[0]["stats"]["bad"] > 0 or
              books2[0]["stats"]["warn"] > 0, str(titles))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n[11] 边界")
    check("不存在的目录返回空", library.scan("/no/such/dir") == [])
    r = analyze("t", None)
    check("None 正文不崩", r["level"] == "bad", str(r))

    print(f"\n{'='*46}\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for n in FAIL:
            print("  ❌ " + n)
        return 1
    print("✅ 质检与书架全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
