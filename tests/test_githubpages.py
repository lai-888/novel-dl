"""GitHub Pages 型站点（Markdown 仓库）—— 专项测试。

这类站点和传统小说站完全不同：
  页面上没有章节链接，内容不在 HTML 里，而是一堆 Markdown 文件。

要验证的核心点：
  1. 能从页面识别出仓库配置
  2. 中文数字章号能正确转成排序键（第二 < 第十章 < 第十九章）
  3. GitHub 返回的乱序能被纠正
  4. Markdown 清理不破坏正文
  5. ZW_META 注释能被剥离

跑：python tests/test_githubpages.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from novel_dl.githubpages import (chapter_order_key, cn_to_int,  # noqa: E402
                                  build_toc, detect_github_pages,
                                  md_to_text, strip_meta)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  <- {detail}"))


def main():
    print("\n[1] 中文数字转换")
    cases = {
        "一": 1, "二": 2, "十": 10, "十一": 11, "二十": 20, "二十一": 21,
        "一百": 100, "一百零三": 103, "一百二十三": 123, "一千": 1000,
        "两千": 2000, "十九": 19, "九十九": 99, "3": 3, "42": 42,
    }
    bad = []
    for s, want in cases.items():
        got = cn_to_int(s)
        if got != want:
            bad.append(f"{s}={got}(期望{want})")
    check(f"{len(cases)} 个中文数字全部正确", not bad, "; ".join(bad))

    print("\n[2] 章节排序：GitHub 返回的顺序是乱的，必须纠正")
    raw = [
        "第二章 课桌藏心事.md", "第五章 孤灯赴中考.md", "第八章 隔屏皆是念.md",
        "第六章 盛夏的句号.md", "第十一章 分科定前路.md", "第十九章 百日燃星火.md",
        "第四章 三月春风辞城阙.md", "第十章 寒尽春声起.md", "彩蛋.md",
        "第一章 旧庭无温.md",
    ]
    ordered = sorted(raw, key=chapter_order_key)
    check("第1章排在最前", ordered[0].startswith("第"), ordered[0])
    check("第二章在第四章之前",
          ordered.index("第二章 课桌藏心事.md") < ordered.index("第四章 三月春风辞城阙.md"))
    check("第八章在第十章之前",
          ordered.index("第八章 隔屏皆是念.md") < ordered.index("第十章 寒尽春声起.md"))
    check("第十章在第十一章之前",
          ordered.index("第十章 寒尽春声起.md") < ordered.index("第十一章 分科定前路.md"))
    check("第十九章在第十一章之后",
          ordered.index("第十九章 百日燃星火.md") > ordered.index("第十一章 分科定前路.md"))
    check("彩蛋排在正文最后", ordered[-1] == "彩蛋.md", ordered[-1])
    print("     排序结果:", " → ".join(o.split()[0] for o in ordered))

    print("\n[3] 从页面识别仓库配置")
    html = '''
    <script>
    const CONFIG = {
        GH_USER: "Youreln",
        GH_REPO: "novel",
        GH_BRANCH: "main",
        GH_API: "https://api.github.com/repos",
        CDN_BASE: "https://cdn.jsdelivr.net/gh/Youreln/novel@main/",
        SORT_PATH: "novel/sort.json"
    };
    </script>'''
    info = detect_github_pages(html, "https://youreln.github.io/novel/")
    check("识别为 GitHub 仓库型", info is not None)
    if info:
        check("user 正确", info["user"] == "Youreln", info.get("user"))
        check("repo 正确", info["repo"] == "novel", info.get("repo"))
        check("branch 正确", info["branch"] == "main", info.get("branch"))
    check("普通小说页不误判",
          detect_github_pages("<html><body>普通页面</body></html>", "") is None)

    print("\n[4] 文件树 → 目录（含排序）")
    tree = [
        {"type": "blob", "path": "novel/A书/第二章 x.md"},
        {"type": "blob", "path": "novel/A书/第一章 y.md"},
        {"type": "blob", "path": "novel/A书/第十一章 z.md"},
        {"type": "blob", "path": "novel/B书/第一章 q.md"},
        {"type": "tree", "path": "novel/A书"},          # 目录项应被忽略
        {"type": "blob", "path": "novel/sort.json"},     # json 应被忽略
        {"type": "blob", "path": "README.md"},           # prefix 外应被忽略
    ]
    books = build_toc(tree, {"prefix": "novel/"})
    check("识别出 2 本书", len(books) == 2, str(list(books)))
    if "A书" in books:
        names = [c[0] for c in books["A书"]]
        check("A书 3 章", len(names) == 3, str(names))
        check("A书 排在第一章之后",
              names[0].startswith("第一章"), str(names))
        check("A书 第十一章在最后", names[-1].startswith("第十一章"), str(names))
    check("sort.json 未被当作章节",
          not any("sort" in c[0] for c in books.get("A书", [])))

    print("\n[5] ZW_META 元数据剥离")
    md = ('<!-- ZW_META:{"title":"第二章 课桌","author":"豆包",'
          '"book":"《晚风》"} -->\n\n正文开始了。')
    body, meta = strip_meta(md)
    check("元数据被剥离", "ZW_META" not in body, body[:40])
    check("title 解析出来", meta.get("title") == "第二章 课桌", str(meta))
    check("author 解析出来", meta.get("author") == "豆包", str(meta))
    check("正文保留", "正文开始了" in body, body[:40])

    print("\n[6] Markdown → 纯文本")
    md2 = """# 第一章 标题

这是**加粗**的正文，还有*斜体*和`代码`。

[一个链接](https://example.com) 后面还有文字。

![图片](https://example.com/a.png)

> 引用行

---

结尾。
```python
code fence
```"""
    out = md_to_text(md2)
    check("标题井号被去掉", "#" not in out, out[:60])
    check("加粗标记被去掉", "**" not in out)
    check("斜体标记被去掉", "*斜体*" not in out)
    check("行内代码标记被去掉", "`" not in out)
    check("链接保留文字去掉 URL",
          "一个链接" in out and "example.com" not in out, out[:100])
    check("图片保留 alt 去掉 URL",
          "图片" in out and "a.png" not in out, out[:100])
    check("引用符号被去掉", "> 引用行" not in out and "引用行" in out)
    check("正文内容完整", "这是加粗的正文" in out, out[:80])
    check("代码块内容保留", "code fence" in out, out[:120])

    print("\n[7] 边界情况")
    check("空字符串", md_to_text("") == "")
    check("只有元数据", strip_meta("<!-- ZW_META:{} -->")[0].strip() == "")
    check("文件名为数字章节", chapter_order_key("第3章 x.md")[0] == 0)
    check("无章号文件排后", chapter_order_key("随便.md")[0] > 0)

    print(f"\n{'='*46}\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for n in FAIL:
            print("  ❌ " + n)
        return 1
    print("✅ GitHub Pages 站点处理全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
