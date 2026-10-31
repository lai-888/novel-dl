"""命令行入口。"""
import argparse
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
    ap.add_argument("--reclean", action="store_true",
                    help="不联网，按当前规则重新清洗已下载的章节并重导出 TXT")
    ap.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    ap.add_argument("-V", "--version", action="version", version=f"novel-dl {__version__}")
    return ap


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
    )
    if args.reclean:
        changed, path = engine.reclean()
        print(f"已重新清洗 {changed} 章，TXT：{path}")
        return 0
    engine.run()
    return 0

if __name__ == "__main__":
    main()
