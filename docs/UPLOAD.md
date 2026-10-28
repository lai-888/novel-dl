# 上传到 GitHub 的完整步骤

仓库已经在 `C:\Users\31383\Desktop\novel-dl` 初始化好，首次提交也做完了。
下面从「还没建远程仓库」开始。

---

## 0. 先确认本机状态（30 秒）

本机实际情况：

| 工具 | 状态 |
|---|---|
| git | ✅ `C:\Program Files\Git\bin\git.exe`（2.55） |
| gh（GitHub CLI） | ❌ 未安装 |
| git 全局身份 | ❌ 未配置（`user.name` / `user.email` 都是空） |
| python | ✅ `C:\Python314\python.exe` |

---

## 1. 配置 git 身份（只需一次）

```bash
git config --global user.name  "你的名字"
git config --global user.email "你的GitHub邮箱"
```

> 为什么重要：提交记录里的作者信息来自这里。想让 GitHub 把提交算到你头上，
> 邮箱必须是你 GitHub 账号里验证过的那个（或 `@users.noreply.github.com` 形式）。

---

## 2. 在 GitHub 网页建空仓库

1. 打开 https://github.com/new
2. Repository name 填 `novel-dl`
3. 选 **Public**
4. **不要**勾 "Add a README file" / ".gitignore" / "license"——本地已经有了，勾了会冲突
5. 点 Create repository

建完页面会显示远程地址，形如：

```
https://github.com/<你的用户名>/novel-dl.git
```

---

## 3. 关联远程并推送

在本机执行（把 URL 换成你自己的）：

```bash
cd C:\Users\31383\Desktop\novel-dl
git remote add origin https://github.com/<你的用户名>/novel-dl.git
git push -u origin main
```

`-u` 的作用：把这个远程设为默认，以后直接 `git push` 就行。

**首次推送会弹窗要认证。** GitHub 早就不能用账号密码了，两个选择：

- **浏览器授权（最简单）**：装 Git Credential Manager 的话，会弹浏览器让你登录 GitHub，点授权即可。Git for Windows 2.55 自带 GCM，正常都能弹出来。
- **Personal Access Token**：GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token，勾 `repo` 权限。密码框粘贴这个 token。

---

## 4. 验证

```bash
git remote -v          # 看远程地址对不对
git log --oneline      # 看提交在不在
git status             # 应该是 clean
```

然后刷新 GitHub 页面，应该能看到 21 个文件和你的提交记录。

---

## 5. 推送前建议再改两处

### 5.1 把 `<your-name>` 换成你的用户名

`pyproject.toml` 里有占位符：

```toml
[project.urls]
Homepage = "https://github.com/<your-name>/novel-dl"
Issues   = "https://github.com/<your-name>/novel-dl/issues"
```

README 和 CONTRIBUTING 里的 `git clone` 示例同理。改完再提交：

```bash
git add -A
git commit -m "docs: 填入仓库地址"
```

### 5.2 确认没有个人内容混进去

```bash
git ls-files
```

应该只有这 21 项，**不应该**出现 `books/`、`logs/`、`sites.json`、`test_out/`。
这些 `.gitignore` 都挡住了——但养成推送前看一眼的习惯。

---

## 关于「以后怎么更新」

日常流程就三步：

```bash
git add -A                        # 或 git add 具体文件
git commit -m "feat: 做了什么"
git push
```

想改最后一次提交（比如提交信息写错、漏了文件）：

```bash
git add 忘记的文件
git commit --amend                # 合并进上一次提交
git push --force-with-lease       # 已经推过才需要（只在自己一个人用的分支上这么做）
```

---

## 可选：装 GitHub CLI 会省很多事

```bash
winget install --id GitHub.cli
gh auth login
```

之后建仓库、推送、开 PR 都能一条命令搞定：

```bash
gh repo create novel-dl --public --source=. --push
```

这一条就等于上面第 2、3 步的全部内容。
