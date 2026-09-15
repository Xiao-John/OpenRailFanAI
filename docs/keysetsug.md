# RailFanAI · API Key 安全指南

> 目标：真实 LLM API Key 不落盘、不进入 Git、不被他人读取。  
> 原则：Key 只存在于进程内存或系统级安全存储中。

---

## ⚠️ 实施时机（已决策）

**本项已决定：推迟到「真正部署 / 上线阶段」再实施。**

| 阶段 | 处置 |
|---|---|
| 当前（开发 / 本机演示） | **暂缓**。`.env` 保留真实 Key 以便联调；风险可控（仅本机、已 gitignore） |
| **部署 / 上线前** | **必做**。执行本文件 §2 起全部操作，并完成 §6 检查清单 |

> 📌 **提醒锚点**：本项已登记在
> [`docs/plan.md`](plan.md) 的 **「上线前必做清单（Pre-launch Checklist）」** 第 1 条。
> 进入部署阶段时，从该清单开始逐项核对。
>
> 触发条件（任一满足即须立即执行）：
> 1. 服务要暴露到公网或局域网内非本人设备；
> 2. 仓库要分享 / 推送到远端 / 交付给第三方。

---

## 1. 当前风险

- 本地 `.env` 含明文有效 Key（`LLM_API_KEY=sk-xxxxx`）
- 虽已 `.gitignore`，但任何读取工作区的人都能看到
- 终端输出、日志文件、共享文档可能间接泄露

**第一步永远是去平台轮换已暴露的 Key。**

---

## 2. 立即操作（三步骤）

### ① 轮换 Key

进入 SiliconFlow / 你所用的 LLM 供应商控制台 → API Keys → 删除旧 Key → 新建 Key。

### ② 清除 `.env` 中的真实值

```bash
cd /Users/xylo/Documents/RailFanAI

# 备份后替换为占位
cp .env .env.bak
# 把 LLM_API_KEY 行改成空值
sed -i '' 's/^LLM_API_KEY=.*/LLM_API_KEY=/' .env
# 或者直接删掉该行
sed -i '' '/^LLM_API_KEY=/d' .env
```

### ③ 验证 Git 历史安全

```bash
git log --all --oneline -- .env          # 看 .env 是否被提交过
git log --all -p -- .env | grep -i 'sk-' # 搜索历史中的 key
```

---

## 3. 安全方案：Key 只通过环境变量注入

### 方案 A：shell 变量启动（最简单）

```bash
# 将 Key 设为当前 shell 的临时环境变量（不落盘）
export LLM_API_KEY="sk-你的新Key"

# 启动服务
cd backend
LLM_MOCK=false .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- ✅ Key 仅在进程中，关闭终端即消失
- ❌ 每次启动需手动 export

### 方案 B：写入 shell 配置文件（日常使用）

```bash
# ~/.zshrc 末尾添加
export RAILFAN_LLM_API_KEY="sk-你的新Key"
```

然后修改 `config.py` 读取该变量名：

```python
# backend/app/config.py
llm_api_key: str = ""   # 默认空，运行期从环境变量注入
```

启动时：

```bash
LLM_API_KEY="$RAILFAN_LLM_API_KEY" uvicorn app.main:app
```

- ✅ 无需每次输入
- ⚠️ Key 仍在 `~/.zshrc` 中（本地可读），需确保该文件权限 `chmod 600 ~/.zshrc`

### 方案 C：direnv（推荐 — 项目级隔离）

```bash
# 安装 direnv
brew install direnv

# 在项目根目录创建 .envrc（已 gitignored）
cat > .envrc << 'EOF'
export LLM_API_KEY="sk-你的新Key"
export LLM_MOCK=false
EOF

# 授权
direnv allow .
```

之后每次 `cd` 进项目目录，环境变量自动加载；离开目录自动卸载。

- ✅ 项目隔离，不进 Git
- ✅ direnv 的 `.envrc` 默认受 `.gitignore` 保护
- ✅ 启动时无需额外操作

### 方案 D：macOS Keychain（最安全）

```bash
# 存入 Keychain
security add-generic-password -a "$USER" -s "railfan-llm-key" -w "sk-你的新Key"

# 启动脚本从 Keychain 读取
export LLM_API_KEY=$(security find-generic-password -a "$USER" -s "railfan-llm-key" -w)
cd backend && uvicorn app.main:app
```

- ✅ Key 不存于任何文本文件
- ✅ macOS Keychain 有系统级加密保护
- ❌ 每次启动弹授权确认（可设置始终允许）

---

## 4. 团队协作与 CI

| 场景 | 做法 |
|------|------|
| **本地开发** | 方案 B 或 C，环境变量注入 |
| **团队共享** | `.env.example` 只保留结构和 mock 值，真实 Key 由每人通过个人环境变量注入 |
| **CI/CD** | 使用平台 Secrets 功能（GitHub Actions secrets / GitLab CI variables），用 `${{ secrets.LLM_API_KEY }}` 注入 |
| **Docker 部署** | `docker run -e LLM_API_KEY="..."` 或 Docker secrets，绝不写入 `Dockerfile` 或镜像 |

---

## 5. 预防误提交

### `.gitignore` 强化

```gitignore
# 原有
.env

# 追加
.env.local
.env.*.local
.envrc
*.key
*.pem
secrets/
```

### Git pre-commit 钩子

创建 `backend/.git/hooks/pre-commit`（或项目根 `.githooks/pre-commit`）：

```bash
#!/bin/bash
# 扫描暂存区中的疑似 API Key 模式
PATTERNS='sk-[a-zA-Z0-9]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z\-_]{35}'
if git diff --cached -U0 | grep -qE "$PATTERNS"; then
    echo "❌ 疑似 API Key / Secret 被暂存！"
    echo "   请移除后再提交。若误报，用 git commit --no-verify 跳过。"
    exit 1
fi
```

### GitHub Push 保护（若使用 GitHub）

在仓库 Settings → Secrets and variables → Actions → 开启 **Secret scanning** 和 **Push protection**（GitHub 会自动检测并阻止包含已知 Key 格式的 push）。

---

## 6. 修改建议清单

| 涉及文件 | 改动 |
|---------|------|
| `.env` | 删除 `LLM_API_KEY` 真实值，或整行删除 |
| `.env.example` | 保持 `LLM_API_KEY=your-api-key-here` 作为提示 |
| `.gitignore` | 追加 `.env.local` `.envrc` `*.key` |
| `config.py` | 无需改动（`llm_api_key` 默认空，已从环境变量读取） |
| `scripts/setup.sh` | 第 46 行 Key 检测逻辑改用 `source` 方式解析（见下） |

### `setup.sh` 检测逻辑优化

当前方案脆弱（grep + cut + tr 链）。建议改为：

```bash
# 安全解析 .env 中 LLM_API_KEY 的值
set -a; source "$ENV_FILE" 2>/dev/null; set +a
if [ -n "${LLM_API_KEY:-}" ] && [ "$LLM_API_KEY" != "your-api-key-here" ]; then
    export LLM_MOCK="false"
else
    export LLM_MOCK="true"
    echo "    未检测到 LLM_API_KEY，已自动启用 LLM_MOCK=true"
fi
```

---

## 7. 操作检查清单

- [ ] 已在 LLM 供应商平台轮换（删除旧 Key，生成新 Key）
- [ ] `.env` 中已移除真实 Key
- [ ] `git log` 确认 Key 从未进入 Git 历史
- [ ] 已选择并配置一种环境变量注入方案（shell / direnv / keychain）
- [ ] `.gitignore` 已追加补充规则
- [ ] 已安装/启用 pre-commit 扫描钩子
- [ ] 已通知可能接触过该仓库的所有人旧 Key 已吊销

---

*最后更新：2025-09-13*