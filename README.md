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
| `--show` | 开一个看得见的浏览器（自动用 selenium），人机验证由你在窗口里点 |
| `--ua TEXT` | 指定 User-Agent，贴 Cookie 时一起给（通行证认 UA） |
| `--no-auto` | 只做通用解析，不自动推断页面结构 |
| `--backup URL` | 备用镜像地址 |
| `--rules FILE` | 追加规则文件 |
| `--cookie TEXT` | 站点要人机验证时，贴浏览器的 Cookie（F12 → 网络 → 请求头）；也可以直接写文件路径 |
| `--config FILE` | 指定配置文件 |
| `--reclean` | 不联网，按当前规则重新清洗已下载的章节并重导出 TXT |
| `--inspect` | 只看不下载：打印配置、解析器、目录章数和已有进度 |
| `--trace FILE` | 把内核事件写成 JSONL（请求、编码、熔断、章节、并发窗口） |
| `-v` | 输出调试日志 |

中断、报错、站点更新之后，重跑同一条命令即可，已下载的章节不会重复请求。

有些站会先弹人机验证（滑块、点选）。程序不替人过验证，会直接告诉你卡在哪一步，这时用浏览器打开那本书、把验证过掉，再把请求头里的 Cookie 贴上：

```bash
novel-dl "<网址>" --cookie "sid=...; uid=..."
```

Cloudflare 那种「请稍候…」的站（幻夢ACG、部分书屋）连 Cookie 都拿不到，因为它先让你在页面上点一下。这种情况让程序开一个看得见的浏览器，你在窗口里点过去，它就在同一个浏览器里接着下：

```bash
novel-dl "<网址>" --show
```

`--show` 会自动切到 selenium 驱动，最多等你五分钟；期间窗口不要关。过了这一关，整本书都在这个浏览器里下完，不用再点。

有些站（掌阅，以及盾比较硬的站）连可见窗口都过不去 —— 它认得出这是自动化的浏览器，就一直转圈、不给勾选框。这种只能借你自己浏览器里的那份通行证：先在你平时用的浏览器里打开这本书、把验证过掉、能正常看到正文，然后把两样东西交给程序：请求头里的 Cookie，和同一个浏览器的 User-Agent（通行证和 UA 是绑在一起的，UA 不对等于没带）。

```bash
novel-dl "<网址>" --cookie "cf_clearance=...; uid=..." --ua "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
```

两样在同一处拿：F12 → 网络 → 刷新页面 → 点第一个请求 → 请求头里的 `Cookie` 和 `User-Agent`，整行复制粘贴即可。

正文里混进广告、站点改版之后，用 `--reclean` 就地重洗，不用重下：

```bash
novel-dl https://example.com/book/123/ --reclean
```

下载不对时，先看它到底认成了什么（不下载、不写文件）：

```bash
novel-dl https://example.com/book/123/ --inspect
```

输出里有解析器名称、目录章数、第一章和最后一章、已有进度、依赖是否齐全。想看清每一章发生了什么，把它记下来：

```bash
novel-dl https://example.com/book/123/ --trace run.jsonl
```

`run.jsonl` 每行一个事件：`req.start`/`req.ok`/`req.fail`（请求与耗时）、`retry`（第几次重试）、`enc`（站点声明的编码和实际用的）、`ch.ok`/`ch.fail`（章节结果）、`breaker.open`/`breaker.rise`（被限流时降速和恢复）、`win`（并发窗口变化）。文件可以直接打开看，也可以贴出来排查。

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

`tests/` 下有 11 个断言套件，共 443 项，不依赖 pytest，不联网：

```bash
python tests/test_all.py    # 单个套件
python tests/test_diag.py   # 调试工具（状态快照、轨迹）
python tests/bench.py       # 性能基准
```

## License

MIT，见 [LICENSE](LICENSE)。
