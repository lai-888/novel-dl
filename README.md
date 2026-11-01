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
| `--show` | 开一个有窗口的浏览器，人机验证由你在窗口里过 |
| `--attach HOST:PORT` | 接管你自己开着的 Chrome（先加 `--remote-debugging-port=9222` 启动） |
| `--ua TEXT` | 指定 User-Agent，和 `--cookie` 配套用 |
| `--no-auto` | 只做通用解析，不推断页面结构 |
| `--backup URL` | 备用镜像地址 |
| `--rules FILE` | 追加规则文件 |
| `--cookie TEXT` | 浏览器的 Cookie（F12 → 网络 → 请求头），也可以写文件路径 |
| `--config FILE` | 指定配置文件 |
| `--reclean` | 不联网，按当前规则重新清洗已下载的章节并重导出 TXT |
| `--inspect` | 只看不下载：打印配置、解析器、目录章数和已有进度 |
| `--trace FILE` | 把内核事件写成 JSONL（请求、编码、熔断、章节、并发窗口） |
| `-v` | 输出调试日志 |

中断、报错、站点改版之后，重跑同一条命令即可，已下载的章节不会重复请求。

## 被拦住的时候

程序不替人过验证，也不伪装成普通用户。被拦住时它把卡在哪一步说出来，用下面任意一种方式接着下。

**贴 Cookie。** 浏览器里通过验证后，把请求头里的 Cookie 整行复制过来：

```bash
novel-dl "<网址>" --cookie "sid=...; uid=..."
```

**让它开浏览器。** 需要在页面上点一下才给内容时用 `--show`：程序开一个有窗口的浏览器，页面由你操作，默认等你五分钟。

```bash
novel-dl "<网址>" --show
```

**接管已开的 Chrome。** 页面由你自己的浏览器加载，Cookie 和登录状态都用它的：

```bash
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\Google\Chrome\User Data"
novel-dl "<网址>" --attach 127.0.0.1:9222
```

同一个配置同时只能开一个 Chrome 实例；接管期间关掉窗口，下载就停。

**Cookie 加 UA。** 有些通行证和 User-Agent 绑定，两个要一起给，UA 对不上等于没带：

```bash
novel-dl "<网址>" --cookie "cf_clearance=...; uid=..." --ua "<同一个浏览器的 User-Agent>"
```

两样都在 F12 → 网络 → 刷新 → 第一个请求 → 请求头里，整行复制即可。

登录墙、付费墙、加密字体不在处理范围。

## 正文质量

有的站会往正文里灌填充句。导出时按两条判据统计删掉：这句话在多章里都出现，而且平均每章重复不止一次。两条都满足才删，同一章里只写一次的话不会被误删。

正文里混进广告、站点改版之后，用 `--reclean` 就地重洗，不用重下：

```bash
novel-dl https://example.com/book/123/ --reclean
```

两种情况会单独标注，不当成正常章节：

- 字体加密：整本停下，退出码 `2`，不留下残缺文件。
- 付费章节只返回开头几百字：照存，该章在 TXT 里标「付费章节，只取到开头一段」，书架里也标「付费预览」。

## 下载不对时

先确认它把页面解析成了什么，不下载、不写文件：

```bash
novel-dl https://example.com/book/123/ --inspect
```

输出里有解析器名称、目录章数、第一章和最后一章、已有进度、依赖是否齐全。

要逐章排查，把内核事件写成 JSONL：

```bash
novel-dl https://example.com/book/123/ --trace run.jsonl
```

`run.jsonl` 每行一个事件：`req.start`/`req.ok`/`req.fail`（请求与耗时）、`retry`（第几次重试）、`enc`（站点声明的编码和实际用的）、`ch.ok`/`ch.fail`（章节结果）、`breaker.open`/`breaker.rise`（被限流时降速和恢复）、`win`（并发窗口变化）。可以直接打开看，也可以贴出来排查。

网页界面：`python webui.py`，浏览器打开 `http://127.0.0.1:8766`。

## 输出

```
books/书名（网址摘要）/
├── cache/00001.txt   # 一章一个文件
├── state.json        # 目录快照、已完成、失败名单、付费预览名单
└── 书名.txt           # 合并成品
```

目录用书名命名，后面括号里是网址摘要，同名书不会互相覆盖。目录被别人打开着改不了名时（资源管理器停在那一层、同步盘正在同步），程序会提示一句，下次跑同一条命令再试。

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

目录、正文默认全靠通用解析：目录按网址形状聚类，正文按页面结构挑块，不认具体网站，也不带任何网站名单。

结构特别怪的站，程序会自动推断一次「正文在哪」，结果缓存在 `~/.novel-dl/learned.json`（可用 `NOVEL_DL_CACHE` 指定路径）。推断只找正文，目录仍然走通用解析；想彻底关掉用 `--no-auto`。

推断也不灵的时候，才需要自己写规则放进 `sites.json`，格式见 [docs/RULES.md](docs/RULES.md)。

## 测试

`tests/` 下有 11 个断言套件，共 483 项，不依赖 pytest，不联网：

```bash
python tests/test_all.py    # 单个套件
python tests/test_diag.py   # 调试工具（状态快照、轨迹）
python tests/bench.py       # 性能基准
```

## License

MIT，见 [LICENSE](LICENSE)。
