# novel-dl

输入小说章节目录页的网址，把整本下载成 TXT。

## 安装

```bash
pip install -e .
# 需要渲染动态页面时
pip install -e ".[selenium]"
```

Python 3.10+。

## 用法

```bash
novel-dl https://example.com/book/123/                    # 全本
novel-dl https://example.com/book/123/ -f 100 -t 200      # 只下 100~200 章
novel-dl https://example.com/book/123/ -w 4               # 4 线程
novel-dl https://example.com/book/123/ --delay 3 -w 1     # 站点限流时放慢
novel-dl https://example.com/book/123/ --proxy http://127.0.0.1:7890
novel-dl https://example.com/book/123/ -v                 # 调试日志
```

| 参数 | 作用 |
|---|---|
| `url` | 目录页地址；给阅读页/正文页地址也行，程序会顺着页面里的「目录」链接找回去 |
| `-f N` / `-t N` | 起始章 / 结束章（含） |
| `-w N` | 并发线程数，默认 2 |
| `--delay N` | 每章最小间隔秒数，默认 1 |
| `--proxy URL` | 走代理抓取（浏览器能打开但程序报连接重置时用） |
| `-o DIR` | 输出目录，默认 `./books` |
| `-d NAME` | 抓取驱动，`requests` 或 `selenium` |
| `--no-auto` | 只做通用解析，不自动推断页面结构 |
| `--backup URL` | 备用镜像地址 |
| `--rules FILE` | 追加规则文件 |
| `--config FILE` | 指定配置文件 |
| `--reclean` | 不联网，按当前规则重新清洗已下载的章节并重导出 TXT |
| `-v` | 输出调试日志 |

中断、报错、站点更新之后，重跑同一条命令即可，已下载的章节不会重复请求。

正文里混进广告、站点改版之后，用 `--reclean` 就地重洗，不用重下：

```bash
novel-dl https://example.com/book/123/ --reclean
```

网页界面：`python webui.py`，浏览器打开 `http://127.0.0.1:8766`。

两种会中断或标注的情况：

- 站点用自定义字体加密正文：整本停下，退出码 `2`，不产生文件。
- 付费章节只返回开头几百字：照下，该章标为「仅预览」。

## 输出

```
books/<slug>/
├── cache/00001.txt   # 一章一个文件
├── state.json        # 目录快照、已完成、失败名单
└── 书名.txt           # 合并成品
```

想重下某一章，删掉对应的 `cache/NNNNN.txt` 再跑一次。

## 配置

`novel-dl.toml`（当前目录）或 `~/.novel-dl/config.toml`，命令行参数优先。

```toml
output_dir = "books"
driver     = "requests"
delay      = 1.0
workers    = 2
proxy      = "http://127.0.0.1:7890"   # 可选
```

## 站点规则

仓库里不带任何具体网站的选择器。通用解析器跑不通时，写一条 JSON 规则放进
`sites.json`，格式见 [docs/RULES.md](docs/RULES.md)；也可以先试自动推断
（默认开启，结果缓存在 `~/.novel-dl/learned.json`，可用 `NOVEL_DL_CACHE` 指定路径）。

## 测试

`tests/` 下有 10 个断言套件，共 335 项，不依赖 pytest，不联网：

```bash
python tests/test_all.py    # 单个套件
python tests/bench.py       # 性能基准
```

## License

MIT，见 [LICENSE](LICENSE)。
