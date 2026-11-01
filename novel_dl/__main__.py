"""命令行入口。"""
import argparse

try:
    from requests.exceptions import RequestException as _REQ_ERR
    _NET_ERRORS = (ConnectionError, OSError, _REQ_ERR)
except Exception:                                   # 没装 requests 也能跑
    _NET_ERRORS = (ConnectionError, OSError)
import logging
import sys

from . import __version__, config, log
from .engine import Engine
from .fetchers import list_fetchers


def _harden_console():
    """Windows GBK 控制台下 print emoji 会 UnicodeEncodeError 崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="novel-dl",
        description="小说下载器：按目录页下载整本小说为 TXT")
    ap.add_argument("url", help="小说目录页网址")
    ap.add_argument("-f", "--from", dest="from_ch", type=int, metavar="N",
                    help="起始章节号（从 1 开始）")
    ap.add_argument("-t", "--to", dest="to_ch", type=int, metavar="N",
                    help="结束章节号（含）")
    ap.add_argument("-d", "--driver", default=None, choices=list_fetchers(),
                    help="抓取驱动，动态渲染站点用 selenium")
    ap.add_argument("-o", "--out", default=None, help="输出目录（默认 ./books）")
    ap.add_argument("--delay", type=float, default=None,
                    help="每章最小间隔秒数，被限速时调大（如 3）")
    ap.add_argument("-w", "--workers", type=int, default=None, metavar="N",
                    help="并发抓取线程数（默认 1=串行）。4~8 通常最合适；"
                         "站点限流时调小")
    ap.add_argument("--no-auto", action="store_true",
                    help="关闭自动分析网页结构（默认开启：未收录的站点会在"
                         "通用解析失败时自动推断选择器并缓存）")
    ap.add_argument("--backup", default=None, metavar="URL",
                    help="备用网址（镜像站），主站某章失败时自动改用")
    ap.add_argument("--rules", default=None, metavar="FILE",
                    help="额外规则文件，可多次指定；也可用环境变量 NOVEL_DL_RULES")
    ap.add_argument("--config", default=None, metavar="FILE",
                    help="配置文件路径（默认 ./novel-dl.toml 或 ~/.novel-dl/config.toml）")
    ap.add_argument("--proxy", default=None, metavar="URL",
                    help="走代理抓取，例如 http://127.0.0.1:7890。"
                         "浏览器能打开、程序报「连接被重置(10054)」时用它；"
                         "也可用环境变量 NOVEL_DL_PROXY_URL")
    ap.add_argument("--cookie", default=None, metavar="TEXT",
                    help="浏览器里已经过完人机验证的那份 Cookie（F12 → 网络 → 请求头里复制），"
                         "也可以写成文件路径；也可用环境变量 NOVEL_DL_COOKIE")
    ap.add_argument("--reclean", action="store_true",
                    help="不联网，按当前规则重新清洗已下载的章节并重导出 TXT")
    ap.add_argument("--inspect", action="store_true",
                    help="只看状态不下载：打印配置、解析器、目录章数、已有进度")
    ap.add_argument("--trace", default=None, metavar="FILE",
                    help="把内核事件（请求/编码/熔断/章节/并发窗口）写成 JSONL 便于事后排查")
    ap.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    ap.add_argument("-V", "--version", action="version", version=f"novel-dl {__version__}")
    return ap


def _read_cookie(raw):
    """--cookie 既可以是一整串，也可以是一个文件路径（省得每次贴）。"""
    val = (raw or "").strip()
    if not val:
        return None
    import os
    if os.path.isfile(val):
        try:
            with open(val, encoding="utf-8") as f:
                val = f.read().strip()
        except OSError:
            pass
    return val or None


def main(argv=None):
    _harden_console()
    ap = build_parser()
    args = ap.parse_args(argv)

    log.setup(logging.DEBUG if args.verbose else logging.INFO)

    cfg = config.load(args.config)
    if args.rules:
        import os
        os.environ["NOVEL_DL_RULES"] = args.rules
        from . import rules
        rules.load_rules(force=True)

    if args.inspect:
        from . import diag
        url = args.url.strip()
        data = diag.collect(url=url)
        data["站点"] = diag.peek(
            url, driver=args.driver or cfg["driver"],
            proxy=args.proxy or cfg.get("proxy"),
            cookie=_read_cookie(args.cookie or cfg.get("cookie")))
        data["进度"] = diag.disk_progress(args.out or cfg["output_dir"], url)
        print(diag.report(data))
        return 0

    engine = Engine(
        args.url.strip(),
        driver=args.driver or cfg["driver"],
        out_dir=args.out or cfg["output_dir"],
        from_ch=args.from_ch,
        to_ch=args.to_ch,
        min_delay=args.delay if args.delay is not None else cfg["delay"],
        backup_url=args.backup,
        max_toc_pages=cfg["max_pages"],
        workers=args.workers if args.workers is not None else cfg["workers"],
        auto_detect=not args.no_auto,
        proxy=args.proxy or cfg.get("proxy"),
        cookie=_read_cookie(args.cookie or cfg.get("cookie")),
    )
    if args.reclean:
        changed, path = engine.reclean()
        print(f"已重新清洗 {changed} 章，TXT：{path}")
        return 0

    if args.trace:
        from . import diag
        trace = diag.Trace(args.trace)
        diag.set_trace(trace)
        try:
            engine.run()
        except _NET_ERRORS as e:
            _say_unreachable(e, args, cfg)
            return 2
        finally:
            diag.set_trace(None)
            kinds = {}
            for rec in trace.events():
                kinds[rec["kind"]] = kinds.get(rec["kind"], 0) + 1
            trace.close()
            print("轨迹已写入 {}：{}".format(
                args.trace, "，".join(f"{k}×{v}" for k, v in sorted(kinds.items()))))
        return 0

    try:
        engine.run()
    except _NET_ERRORS as e:
        _say_unreachable(e, args, cfg)
        return 2
    return 0


def _say_unreachable(e, args, cfg):
    """连不上站点时说人话，不甩一屏报错。"""
    host = ""
    try:
        from urllib.parse import urlsplit
        host = urlsplit(args.url.strip()).netloc
    except Exception:
        pass
    print(f"\n连不上 {host or '这个站点'}。")
    why = str(e)
    if "10061" in why or "拒绝" in why or "Refused" in why:
        print("  它直接拒绝了连接：多半是刚才请求太密，被挡了一会儿。")
    elif "10054" in why or "Reset" in why:
        print("  连接被对面掐断了：站点在拦，隔几分钟再来。")
    else:
        print(f"  原因：{why[:120]}")
    print("\n可以试试：")
    print("  · 等几分钟再跑一次（进度会接着下，不会重下）")
    if cfg.get("proxy"):
        print("  · 换代理：--proxy http://127.0.0.1:端口")
    else:
        print("  · 走代理：--proxy http://127.0.0.1:端口")
    print("  · 浏览器能打开但这里打不开：把浏览器 Cookie 贴进来（--cookie）")
    print("  · 还是不行就换驱动：--driver selenium")
    print(f"\n详情：novel-dl {args.url.strip()[:60]} --inspect")


if __name__ == "__main__":
    sys.exit(main())
