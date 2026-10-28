"""统一日志：库里只输出到 logging，不在核心模块里 print。

好处：CLI 可以用 StreamHandler 打到控制台，webui 可以把同一份日志喂进网页。
"""
import logging

log = logging.getLogger("novel_dl")


def setup(level=logging.INFO, stream=None):
    """CLI / webui 入口调用；重复调用安全。"""
    if log.handlers:
        log.setLevel(level)
        return log
    h = logging.StreamHandler(stream)
    h.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(h)
    log.setLevel(level)
    log.propagate = False
    return log


def info(msg, *a, **kw):
    log.info(msg, *a, **kw)


def warning(msg, *a, **kw):
    log.warning(msg, *a, **kw)


def error(msg, *a, **kw):
    log.error(msg, *a, **kw)


def debug(msg, *a, **kw):
    log.debug(msg, *a, **kw)
