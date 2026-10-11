# 开发规范

本项目采用 Git Flow 分支约定，使用标准 Git 命令即可，无需额外安装 git-flow 插件。首次上传的项目作为共同基线，同时放在 `main` 和 `develop`；初始化不创建发布标签。

## 分支用途

| 分支 | 从哪里创建 | 合并目标 | 用途 |
| --- | --- | --- | --- |
| `main` | 长期保留 | — | 发布版本；日常开发不直接提交 |
| `develop` | `main` | — | 开发集成；日常工作默认分支 |
| `feature/<short-name>` | `develop` | `develop` | 新功能 |
| `fix/<short-name>` | `develop` | `develop` | 未发布版本的修复 |
| `release/<version>` | `develop` | `main`、`develop` | 发布准备、版本号与发布说明 |
| `hotfix/<short-name>` | `main` | `main`、`develop` | 已发布版本的紧急修复 |

分支名称使用英文小写及短横线，例如 `feature/news-filter`。提交信息使用 `feat:`、`fix:`、`docs:`、`test:`、`refactor:`、`chore:` 前缀，说明实际变化。

## 日常开发

```bash
git switch develop
git pull --ff-only
git switch -c feature/news-filter
# 修改文件并运行测试
git add <files>
git commit -m "feat: add news filter"
git push -u origin feature/news-filter
```

在 GitHub 创建目标为 `develop` 的 PR，说明变化及验证结果。自动测试通过并检查差异后合并，随后删除功能分支。开发分支禁止强制推送，更新长期分支使用 `git pull --ff-only`，避免意外产生合并提交。

## 发布与紧急修复

1. 发布时从最新 `develop` 创建 `release/<version>`，只提交发布准备和必要修复，更新 `CHANGELOG.md`。
2. 测试通过后，先通过 PR 以 merge commit 合并到 `main`，保留发布分支历史；在该合并提交创建 `v<version>` 标签并推送。版本号使用 `MAJOR.MINOR.PATCH`。
3. 将更新后的 `main` 通过 PR 同步合并回 `develop`，确保版本号、说明及发布修复都进入后续开发，最后删除发布分支。
4. 紧急修复从 `main` 创建 `hotfix/<short-name>`。修复与测试后，同样合并到 `main`、创建补丁版本标签，再同步回 `develop`；若存在进行中的发布分支，也需同步修复。

## 本地验证与 CI

创建 Python 3.12 虚拟环境并安装依赖：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q
npm install --prefix /tmp/radar-dom-tests jsdom@29.1.1 --no-save --ignore-scripts
NODE_PATH=/tmp/radar-dom-tests/node_modules node --test tests/frontend_settings.test.cjs
```

前端 DOM 测试需要 Node.js 24.13 或更新的 24.x 版本，并调用项目 `.venv/bin/python`。测试使用临时数据、合成行情和模拟接口，不要求真实行情、模型或推送密钥。

GitHub Actions 的 `tests` 检查运行同样的后端及 DOM 测试：推送到 `main`、`develop`、`feature/*`、`fix/*`、`release/*`、`hotfix/*` 时运行，目标为 `main` 或 `develop` 的 PR 也会运行。

上述合并规则是团队约定。若需要 GitHub 强制执行，可在仓库设置中保护 `main` 和 `develop`：要求 PR 和 `tests` 检查通过，禁止强制推送及删除长期分支。仓库内的文件不会自动开启服务器端分支保护。

`.env`、`data/`、`lb_tokens/`、虚拟环境及本地嵌套副本不提交。分享配置时使用 `.env.example`，不要将实际密钥复制到示例文件。
