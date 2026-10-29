"""novel-dl 配置文件。

优先级：命令行参数 > 环境变量 > 配置文件 > 内置默认值。

配置文件位置（第一个存在的即生效）：
    ./novel-dl.toml
    ~/.novel-dl/config.toml

示例：
    output_dir  = "books"
    driver      = "requests"
    delay       = 0.5
    workers     = 4
    rules_files = ["my-sites.json"]
"""
import os
import tomllib

DEFAULTS = {
    "output_dir": "books",
    "driver": "requests",
    # 保守默认：2 线程 + 1 秒间隔。这个组合对绝大多数站点都不会触发限流。
    # 想要更快可以自己调大 workers / 调小 delay，但要承担被封 IP 的风险。
    "delay": 1.0,
    "workers": 2,
    "rules_files": [],
    "max_pages": 200,
    "timeout": 45,
}

SEARCH = [
    lambda: os.path.join(os.getcwd(), "novel-dl.toml"),
    lambda: os.path.join(os.path.expanduser("~"), ".novel-dl", "config.toml"),
]


def find_config_path() -> str | None:
    for make in SEARCH:
        p = make()
        if os.path.isfile(p):
            return p
    return None


def load(path: str | None = None) -> dict:
    """读配置；文件不存在或字段缺失都退回默认值，不抛异常。"""
    cfg = dict(DEFAULTS)
    path = path or find_config_path()
    if not path:
        return cfg
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return cfg
    for k, v in data.items():
        if k in DEFAULTS:
            cfg[k] = v
    cfg["_path"] = path
    return cfg
