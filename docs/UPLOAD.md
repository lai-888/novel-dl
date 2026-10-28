# 上传到 GitHub 的完整步骤

仓库已经在 `C:\Users\31383\Desktop\novel-dl` 初始化好，两次提交也都做完了。
作者身份已配置为 `lai-888 <3138358994@qq.com>`（你 GitHub 账号上已验证的那个邮箱）。

**只剩两步：网页建空仓库 → 推送。**

---

## 0. 本机状态（已确认）

| 工具 | 状态 |
|---|---|
| git | ✅ 2.55，`C:\Program Files\Git\bin\git.exe` |
| gh（GitHub CLI） | ❌ 未安装（可选，装了能省事） |
| git 本地身份 | ✅ `lai-888` / `3138358994@qq.com`（配置在仓库内，不是全局） |
| python | ✅ 3.14，`C:\Python314\python.exe` |
| GitHub 账号 | `lai-888`，验证邮箱 `3138358994@qq.com` |

> 另一个邮箱 `zhengkailai92@gmail.com` 在 GitHub 上是 **Unverified**，
> 不要用它做提交邮箱，否则 GitHub 不会把提交归到你名下。

---

## 1. 在 GitHub 网页建空仓库

1. 打开 https://github.com/new
2. **Repository name** 填 `novel-dl`
3. 选 **Public**
4. **千万不要**勾下面三个（本地已有，勾了会冲突，推送会被拒）：

   - ❌ Add a README file
   - ❌ Add .gitignore
   - ❌ Choose a license

5. 点 **Create repository**

建完会跳到仓库页，上面显示远程地址：

```
https://github.com/lai-888/novel-dl.git
```

---

## 2. 推送

在本机执行：

```bash
cd C:\Users\31383\Desktop\novel-dl
git remote add origin https://github.com/lai-888/novel-dl.git
git push -u origin main
```

`-u` 把 origin/main 设为默认上游，以后直接 `git push` 就够了。

### 首次推送会要求认证

GitHub 早就不接受账号密码了。两个选择：

**A. 浏览器授权（最简单）**
Git for Windows 自带 Git Credential Manager，正常会弹窗让你登录 GitHub，点授权即可。
（如果弹的是命令行要账号密码，说明 GCM 没生效，改用 B。）

**B. Personal Access Token**
1. GitHub → 右上头像 → Settings → Developer settings
2. Personal access tokens → **Tokens (classic)** → Generate new token (classic)
3. 勾 `repo` 权限
4. 生成的 token 当密码用：用户名填 `lai-888`，密码粘贴 token

> token 只显示一次，记得先复制。它等同于密码，别提交到仓库里。

---

## 3. 验证

```bash
git remote -v          # 远程地址应为 https://github.com/lai-888/novel-dl.git
git log --oneline      # 两条提交，作者都是 lai-888
git status             # 应为 clean
```

刷新 GitHub 页面，应能看到 23 个文件和两条提交记录。
点提交记录里的 `lai-888`，能看到头像和账号链接 —— 说明归属正确。

---

## 4. 以后怎么更新

日常三步：

```bash
git add -A
git commit -m "feat: 做了什么"
git push
```

改最后一次提交（漏文件 / 提交信息写错）：

```bash
git add 忘记的文件
git commit --amend --no-edit
git push --force-with-lease     # 已经推过才需要
```

`--force-with-lease` 比 `--force` 安全：远程有别人的新提交时会拒绝覆盖。
本项目只有你在推，基本用不上，先记住别用裸 `--force`。

---

## 5. 可选：装 GitHub CLI 会省很多事

```bash
winget install --id GitHub.cli
gh auth login
```

之后这一条就顶掉上面第 1、2 步的全部内容：

```bash
cd C:\Users\31383\Desktop\novel-dl
gh repo create novel-dl --public --source=. --push
```

---

## 附：这次推送前做过的事（备查）

- 已从发布版**移除** `novel_dl/sites.json`（原本内置 12 个盗版小说站规则）
- 已剔除 `books/`（987 个文件 / 21.4 MB 成品小说）、`logs/`、`__pycache__/`
- 已剔除 `tests/patch_*.py` 等 20 个会改写源码的一次性脚本
- `.gitignore` 已挡住 `sites.json`、`novel-dl.toml`、`books/`、`logs/`

推送前可以随时自查：

```bash
git ls-files    # 只应有 23 项，不含 books/ logs/ sites.json
```
