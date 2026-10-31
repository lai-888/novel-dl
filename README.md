# novel-dl

给一个小说章节目录页的网址，把整本书下载成本地 TXT。

支持断点续传、增量更新、并发抓取；不需要预先为每个站点写规则。

## 功能

| 能力 | 说明 |
|---|---|
| 自动分析页面结构 | 通用解析器跑不通时，推断「章节链接 / 正文 / 标题」选择器，验证能解析后才缓存 |
| 编码处理 | 按响应头 → meta → utf-8 → gb18030 → big5hkscs 逐级试解；gb2312/gbk 升级为 gb18030；剥离私有区与零宽字符 |
| 断点续传 | 每章正文立即写盘（tmp + rename），账本每 20 章落盘一次，启动时扫描 `cache/` 补回漏记章节 |
| 增量更新 | 重跑同一条命令，只下新增章节；目录改版时按章节 URL 重映射进度 |
| 并发 | `-w N`；两阶段重试，失败章节整批结束后补抓 3 轮 |
| 代理 | `--proxy` 或 `NOVEL_DL_PROXY_URL`，用于直连被 CDN 重置的站点 |

## 三种「看起来下完了、其实没有」的情况

| 情况 | 处理 |
|---|---|
| 站点用 `@font-face` 把正文换成私有区码位 | 整本拒绝，退出码 2，不产生任何章节文件 |
| 付费章节只返回开头百余字 | 照下，逐章标「仅预览」并可查，仍计入已完成以免每次重跑重下 |
| 正文只收到半截，或是一句占位提示 | 补抓一次取较长者；仍是占位页则记为失败，写明原因，下次重试 |

字体加密的判定看解析前的原始 HTML（私有区字符 ≥ 300 且页面声明 `@font-face`；
兜底看解析结果里占比 > 15%），并发模式会先同步预检第一章。

## 安装

```bash
pip install -e .
pip install -e ".[selenium]"    # 需要动态渲染的站点
```

Python 3.10+。

## 用法

```bash
novel-dl https://example.com/book/123/                     # 全本
novel-dl https://example.com/book/123/ -w 4                # 4 线程
novel-dl https://example.com/book/123/ -f 100 -t 200       # 只下 100~200 章
novel-dl https://example.com/book/123/ --delay 3 -w 1      # 被限速时放慢
novel-dl https://example.com/book/123/ --proxy http://127.0.0.1:7890
novel-dl https://example.com/book/123/ --no-auto           # 只用通用解析器
novel-dl https://example.com/book/123/ -v                  # 调试日志
```

中断或站点更新后，重跑同一条命令即可。

网页控制台：`python webui.py`，加 `--lan` 可让同一 Wi-Fi 下的手机访问。

## 配置

`novel-dl.toml`（当前目录）或 `~/.novel-dl/config.toml`，优先级：命令行 > 配置文件 > 默认值。

```toml
output_dir = "books"
driver     = "requests"
delay      = 0.5
workers    = 4
proxy      = "http://127.0.0.1:7890"   # 可选
```

站点规则写在 `sites.json`，格式见 [docs/RULES.md](docs/RULES.md)。

## 输出结构

```
books/<slug>/
├── cache/00001.txt   # 一章一个文件
├── cache/00002.txt
├── state.json        # 账本：目录快照 / 已完成 / 失败名单 / 预览标记
└── 书名.txt          # 合并成品
```

## 测试

`tests/` 下是自包含断言套件，不依赖 pytest、不联网，共 284 项。

```bash
python tests/test_all.py            # 21 项：功能 / 规则 / 网址纠错
python tests/test_stability.py      # 21 项：并发 / 中断 / 崩溃恢复
python tests/test_encoding.py       # 27 项：编码与乱码
python tests/test_autodetect.py     # 29 项：自动推断站点规则
python tests/test_webui.py          # 38 项：网页端接口与页面
python tests/test_progress_stats.py # 17 项：进度统计
python tests/test_ratelimit.py      #  9 项：限速与封禁防护
python tests/test_githubpages.py    # 34 项：GitHub Pages 型站点
python tests/test_library.py        # 45 项：内容质检 / 书架 / 章节预览
python tests/test_guard.py          # 43 项：加密站 / 付费预览 / 占位页
python tests/bench.py               # 性能基准（可注入延迟与失败率）
```

覆盖的边界：8 线程抓 60 章不丢章不串章、并发请求去重、中途停止后账本完好、
模拟断电后不重复请求、500 次并发落盘账本始终合法、每 10 章坏一个仍能出成品、
旧签名插件（`fetch(url)`）仍可用。

性能（`tests/bench.py`，本地模拟站点，60 章 × 30ms）：

| 并发 | 耗时 | 加速比 |
|---|---|---|
| 1 | 3.43s | 1.00x |
| 2 | 1.65s | 1.94x |
| 4 | 0.97s | 3.31x |
| 8 | 0.85s | 3.76x |

## 它不做什么

- 不内置站点规则：仓库里没有任何具体网站的选择器，规则由使用者提供。
- 不做验证码识别，不执行页面解密脚本，不绕付费墙。
- 下载内容的版权归原作者，请自行确认目标站点的条款。

## 架构

```
novel_dl/
├── __main__.py   # CLI
├── engine.py     # 编排：目录 → 增量对比 → 串行/并发抓取 → 合并
├── fetchers.py   # 抓取层插件（requests / selenium）
├── parser.py     # 解析层插件：规则解析器 + 通用启发式
├── rules.py      # 规则加载 / 域名匹配 / url_fix
├── autodetect.py # 页面结构推断与缓存
├── guard.py      # 字体加密 / 付费预览 / 占位页判定
├── progress.py   # 账本与断点续传
├── library.py    # 书架与内容质检
├── textutil.py   # 正文噪声过滤
├── config.py     # TOML 配置
└── log.py        # 日志
```

三个扩展点：新抓取驱动（继承 `fetchers.BaseFetcher` + `@register`）、
特殊站点解析器（继承 `parser.BaseParser`）、普通站点（加一条 JSON 规则）。

## License

MIT，见 [LICENSE](LICENSE)。