# novel-dl

一个写给自己用的**小说下载器**：命令行 + 本地网页控制台两种用法。

它的重点不是「支持多少个网站」，而是把三件容易做砸的事做扎实：

| 能力 | 做法 |
|---|---|
| **断点续传** | 一章一个文件，原子写入；账本记录进度。Ctrl+C、断电、关网页都不丢 |
| **增量更新** | 每次启动对比目录快照，网站更新后重跑同一条命令，只下新增章节 |
| **战术性跳过** | 单章解析失败不中断全书，记入账本，下次运行自动重试 |

外加：目录翻页、章节内分页合并、镜像站备用、暂停/停止、失败熔断。

## 它不做什么

- **不内置任何网站规则**。抓取和解析都靠你自己写的规则文件（见 [docs/RULES.md](docs/RULES.md)），本仓库只提供规则格式和解析引擎。
- 不做验证码识别、不执行页面里的解密脚本、不绕付费墙。
- 请自行确认目标网站的 `robots.txt` 与使用条款；下载内容的版权归原作者所有，仅供个人阅读。

## 安装

```bash
git clone https://github.com/<你的用户名>/novel-dl.git
cd novel-dl
pip install -e .
```

要求 Python 3.10+（用到了 `str | None` 语法）。

想用 Selenium 处理动态渲染站点：

```bash
pip install -e ".[selenium]"
```

## 快速上手

```bash
# 1. 写下第一条站点规则（也可以直接用通用解析器试试）
cp sites.example.json sites.json

# 2. 全本下载
novel-dl https://example.com/book/123/

# 3. 断了？重跑同一条命令，自动续传
novel-dl https://example.com/book/123/

# 4. 只想下 100~200 章
novel-dl https://example.com/book/123/ -f 100 -t 200

# 5. 被限速，放慢一点
novel-dl https://example.com/book/123/ --delay 3
```

输出结构：

```
books/
└── https_example.com_book_123/
    ├── state.json          # 账本：目录快照 / 已完成 / 失败名单
    ├── cache/
    │   ├── 00001.txt       # 一章一个文件，原子写入
    │   └── 00002.txt
    └── 某本小说.txt         # 合并后的成品
```

## 网页控制台

```bash
python webui.py
```

双击 `novel-dl.bat` 也行（会自动开浏览器）。页面轮询 `/api/status` 显示进度，
关掉网页不影响下载，关掉终端才停止服务。只监听 `127.0.0.1`，无鉴权，别暴露到公网。

## 配置

优先级：命令行参数 > 配置文件 > 内置默认值。

`novel-dl.toml`（当前目录）或 `~/.novel-dl/config.toml`：

```toml
output_dir  = "books"
driver      = "requests"
delay       = 0.5
max_pages   = 200
rules_files = ["my-sites.json"]
```

## 架构

```
novel_dl/
├── __main__.py   # CLI 入口（argparse）
├── engine.py     # 编排：目录 → 增量对比 → 逐章抓取 → 跳过 → 合并
├── fetchers.py   # 抓取层插件：requests / selenium，注册表切换
├── parser.py     # 解析层插件：规则解析器 + 通用启发式 + 自定义注册
├── rules.py      # 规则文件加载、按域名匹配、url_fix 纠错
├── progress.py   # 断点续传账本与章节缓存  ← 本项目最值得读的文件
├── textutil.py   # 正文噪声过滤的公共正则
├── config.py     # TOML 配置
└── log.py        # 统一日志
```

三个扩展点，都只要「新增一个类 + 一行注册」：

- **新抓取策略** → 继承 `fetchers.BaseFetcher`，`@register("名字")`
- **新解析器**（乱序/字体加密等特殊站） → 继承 `parser.BaseParser`，`@register_parser("域名片段")`
- **普通网站** → 不用写代码，加一条 JSON 规则即可

## 测试

```bash
python tests/test_all.py
```

完全离线：假 HTML 模拟整条链路（首轮下载 → 续传 → 增量 → 失败重试 → 规则解析 → 网址纠错）。
不依赖 pytest。

## License

MIT，见 [LICENSE](LICENSE)。
