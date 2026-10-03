# 计划：本地模型支持多档共存与切换（**尚未实现**）

> 状态：**方案待做**。当前版本（lm19）仍然只认一个模型，行为见下文"现状"。
> 本文是给下一版用的施工图，不是现有行为的说明。

## 0. 为什么值得做

调试时最常用的动作是**同一台设备上对比 2B / 4B**。现在做不到：

| 现状 | 代码位置 |
|---|---|
| `find_model()` 取 `sorted(glob("*.gguf"))[0]` —— **字母序第一个**，2B 永远排在 4B 前面 | `local_inference.py:209` |
| `delete_model()` **删掉全部** `*.gguf` | `local_inference.py:551` |
| 没有"选中哪个"的概念，`status()` 也只报一个模型 | `local_inference.py` / `api/local_model.py` |
| 前端卡片只有一个模型 + 一个「删除模型」 | `frontend/src/pages.js` 的 `localModelCard` |

于是"换模型"= **删掉再下载（508MB–2.6GB）**。对比一次要十几分钟，这已经把对比评测变成不可做的事。

## 1. 目标（按优先级）

- **T1（必做）**：列出已下载的模型；选中其中一个运行；只删指定的那一个。
- **T2（可选，真香）**：**同时**跑多个（不同端口），切换时秒换，不用重启。
  - 16G 设备放得下 2B(1.2G) + 4B(2.6G) = 3.8G；做 A/B 对照时特别有用。
  - 但会带来"两个 provider 同时注册"的复杂度，建议 T1 上线后再评估。

## 2. 后端改动（`backend/app/local_inference.py`）

### 2.1 新增 `list_models()`

```python
def list_models() -> list[dict]:
    """所有已下载的模型（跨候选目录去重），带体积与是否当前生效。"""
```
- 扫描 `candidate_dirs()` 下的 `*.gguf`（**含 `.part` 要排除**）
- 每项：`{name, size_mb, dir, active, warning}`
- `warning` 复用现有的 `_model_warning()`（0.8B 那条警告要跟着每个模型走，不能只挂在当前生效的那个上）

### 2.2 新增"选中"的持久化

```python
_SELECTED_FILE = ".selected_model"      # 放在 _data_dir 下

def selected_model() -> str | None
def select_model(name: str) -> dict      # 记下来 + stop() + start()
```
- **必须落盘**：否则每次重启 App 又回到字母序第一个，"选过"等于没选。
- 落盘内容只存**文件名**，不存绝对路径（目录可能变）。

### 2.3 `find_model()` 改为优先返回被选中的

```python
def find_model() -> str | None:
    # 1) LOCAL_MODEL_PATH 环境变量（保持最高优先级，桌面冒烟用）
    # 2) 选中的那个（存在且文件还在）
    # 3) 都没选过 → 退回原来的"字母序第一个"（**保持向后兼容**）
```
第 3 条很重要：不选的时候行为与现在完全一致，线上不会因为这次改动换掉任何人正在用的模型。

### 2.4 `delete_model(name: str | None = None)`

- **给了名字**：只删那一个；若正被使用则先 `stop()`
- **不给名字**：保持现状（删全部），但要在 API 层显式传 `all=true` 才允许，避免误删

### 2.5 `status()` 增加字段

```python
"models": list_models(),
"active_model": <当前生效的文件名 or None>,
```

## 3. API 改动（`backend/app/api/local_model.py`）

| 端点 | 说明 |
|---|---|
| `POST /api/local-model/select` `{"name": "..."}` | 切换模型：`stop()` → 写选中 → `start(wait=True)`，返回新状态 |
| `POST /api/local-model/delete` `{"name": "..."}` | 只删指定模型；`{"all": true}` 才是全删（显式） |
| `GET /api/local-model` | 响应里多 `models` / `active_model` |

- 切模型是**同步等待**（`start(wait=True)`，实测设备端加载 1–3.5s），前端要显示"正在加载…"
- **校验 name 必须来自 `list_models()`**，不能接受任意路径 —— 否则就是一个任意文件删除漏洞（现有 `delete_model` 只匹配 `*.gguf` 已经很紧了，别退回去）

## 4. 前端改动（`frontend/src/pages.js` 的 `localModelCard`）

已下载区从"一行 + 删除"改成**列表**：

```
已下载
  ○ Qwen3.5-2B-Q4_K_M.gguf   1.2 GB      [使用]
  ● Qwen3.5-4B-Q4_K_M.gguf   2.6 GB  使用中   [删除]
  ⚠ Qwen3.5-0.8B-Q4_K_M.gguf 508 MB   （警告文案）  [删除]
```

- 「使用」→ `POST /select` → 期间显示"正在切换…" → 完成后 `rerender()`
- 每个模型一个「删除」按钮，**沿用两步确认**（WebView 里 `window.confirm` 是哑的，见 `android.md`）
- 顶部仍保留"未下载时"的下载区（`choices` 那部分不动）

## 5. 必须同时处理的关联问题

### 5.1 前端条目里存的 `model` 会过期 ⚠️

`localModelCard` 的「设为当前使用」往 `store.upsertLlmEntry` 里写了 `model: d.model`，
而 `main.js:llmSpec()` 会把条目里的 `model` 作为**请求级覆盖**下发。

**换了模型之后，前端存的还是旧模型名 → 请求里带着旧名字发到新模型上。**

这与之前那个 SSRF bug **同源**：把服务端自己的事实回传给服务端。
既然 `provider_item()` 已经带了 `model`，前端就不该再存一份。

→ **改法：条目里只留 `{id, label}`，`model` 一并去掉。** 顺手把这条也写进回归测试。

### 5.2 下载中的互斥

`_download` 是单例状态。多档共存后用户可能连点两个下载 —— 现有代码用
`if _download["state"] == "downloading": raise ValueError("已有下载在进行中")` 挡住了，
行为可接受（明确报错而不是静默排队）。**保持不变，但在 UI 上把其它档位的下载按钮禁用**，别让用户点了才被拒。

### 5.3 切换时的 provider 一致性

`provider_item()` 每次都从 `_status["model"]` 现取，切换后自动更新 ✓。
但**切换瞬间正在飞的那次生成**会打到旧进程上 —— `stop()` 会终止它。
可接受（用户主动切换 = 放弃当前这次），但要在日志里说明，别让它表现成"答到一半断了"。

## 6. 验收标准

1. 设备上放 2B + 4B 两个模型，`GET /api/local-model` 能列出两条
2. 点「使用 4B」→ 服务带 4B 重启 → `status.active_model` 变成 4B，`backend` 字段仍能读出 GPU/CPU
3. 重启 App 后**仍然是 4B**（选中状态落盘生效）
4. 删 2B 不影响正在用的 4B
5. 只放一个模型时，行为与现在**完全一致**（向后兼容）
6. `POST /select` 传一个不在列表里的名字 → 400，且**不得**删改任何文件

## 7. 顺带值得做的（同一次改动里很便宜）

- `status()` 里给每个模型带上**实测过的** `backend`（现在只有一个全局值）——
  切换模型后用户能立刻看到这个模型是跑在 GPU 还是 CPU 上。
- 把 `list_models()` 的顺序改成**按体积降序**（大模型排前面），比字母序更符合直觉。
