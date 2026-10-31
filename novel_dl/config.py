"""novel-dl 配置文件。"""
import os
import tomllib

DEFAULTS = {
    "output_dir": "books",
    "driver": "requests",
    "delay": 1.0,
    "workers": 2,
    "rules_files": [],
    "max_pages": 200,
    "timeout": 45,
    "proxy": None,
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
