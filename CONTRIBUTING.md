# 参与开发

## 本地跑起来

```bash
git clone https://github.com/<你的用户名>/novel-dl.git
cd novel-dl
pip install -e .
python tests/test_all.py
```

## 提交前

- `python tests/test_all.py` 必须全绿（离线，不联网）。
- 不要提交 `books/`、`logs/`、`sites.json`（个人规则）——`.gitignore` 已经挡了。
- 新功能请带上对应的断言，测试文件是 `tests/test_all.py`，不依赖 pytest。

## 可以贡献什么

**最欢迎：通用解析能力的改进。** 比如新的噪声过滤规则、目录排序推断、分页识别。
这类改动对所有人有效，且不涉及具体网站。

**也欢迎：**

- 文档、报错信息、日志措辞的改进
- 新的抓取驱动（如 Playwright）
- 引擎健壮性：网络异常、目录改版、账本迁移的边界情况

**请不要提交：**

- 具体的盗版小说站规则、域名列表
- 绕过验证码 / 付费墙 / 登录墙的代码
- 任何形式的「站点免死金牌」——本项目不内置站点规则是刻意的设计

## 代码风格

- 无第三方格式化工具的强制要求，跟着现有风格写：4 空格、类型标注可选但新函数建议加。
- 核心模块用 `from . import log` 输出，不要 `print`（webui 靠 logging 拿状态）。
- 面向用户的文案用中文，代码注释中英不限但优先中文。
