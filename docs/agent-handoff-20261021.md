# Agent 交接文档（Main 0.1.30 之后）

日期：2026-10-21。维护者：本轮 AI 协作者。
性质：**单一维护入口**。后续所有 AI/人工改动都追加到本文件末尾的“变更记录”，不要重写历史结论；已解决项标注解决提交，未解决项保留现场。

## 1. 当前快照

| 项 | 值 |
|---|---|
| HEAD | `c7f9c9d`（fix(release): 回执改用公开后的版本资产 URL） |
| 版本 | `0.1.30` |
| Release tag | `v0.1.30` → `ace82ff`（release: prepare Main 0.1.30） |
| 工作区 | 写本文档前干净；本文档为新增文件 |
| 主要文档 | `docs/release-0.1.30.md`、`docs/personal-memory-mvp-20261021.md`、`docs/independent-review-20261019.md`、`docs/backend-fuck-cr-parser-recommendations-20261019.md` |

## 2. 我做的更改

### 2.1 本阶段提交

| 提交 | 内容 | 验证 |
|---|---|---|
| `4525593` | 测试更新：`run_all.sh` 59→63 套；`test_api` 的「那明天呢」改为「那今天呢」；`test_static_assets` 改用 `num_bytes_downloaded`；`test_android` 过滤 androidTest APK、放宽 providerRow/icon 断言 | 更新后 60/63；失败仅为 `test_tools`（本机 DNS）与两个前端套件（本机 Node） |
| `63583cc` | 复核版独立评审：0.1.29 发布闭环、空车次语义回归、测试与清理评估 | 文档 |
| `8aed092` | 新列车车次解析器的独立评审与改进建议（未改 parser/shadow） | 文档；18 个输入实测 |
| `b767f4f` | 记忆开关与供应商向导「记住密钥」恢复整行点击 | `:app:compileDebugKotlin` BUILD SUCCESSFUL |
| `7220b02` | 圆角按钮的 ripple/indication 补 `clip(shape)`；覆盖 SettingsButton、HistoryButton、供应商卡/预设、欢迎示例、附件入口、机位按钮、时刻卡操作、票价详情 | `:app:compileDebugKotlin` BUILD SUCCESSFUL |
| `34cf43e` | 开关行（记住 API Key、图片、个人记忆、向导记住密钥）补 `clip(12dp)`，整行 ripple 同步圆角 | `:app:compileDebugKotlin` BUILD SUCCESSFUL |
| `1e5de3d` | 贴底（≤32dp）时消息区随输入栏/IME 同步上推；非贴底保持阅读位置 | `:app:compileDebugKotlin` BUILD SUCCESSFUL；`test_android` 通过 |
| `7d06869` | 0.1.31 发布准备（VERSION、发布说明、构建） | `scripts/release.sh`；正式签名、词典与 GitHub 资产校验通过 |
| `2173d90` | 0.1.31 发布回执 | Release `v0.1.31` 公开并 latest |
| `c7f9c9d` | 发布回执改用公开后的版本资产 URL；更正 0.1.30/0.1.31 回执 | py_compile 通过；远端资产/摘要核对 |

### 2.2 更早的相关提交（本仓库历史）

| 提交 | 内容 |
|---|---|
| `78b4ea7` | mock 升级为确定性规则回复，并接成模型不可用时的降级 |
| `a52ed8e` | 多轮槽位/意图继承 |
| `ae8d3d9` | 修掉继承引入的“拿错区间”缺陷 |
| `dce65d0` | `parse_od` 重构为「规则 + 站点库引导」 |
| `c8cf914` | `all_stations()` 结果缓存（P2-1） |

### 2.3 记忆/记住密钥开关的具体改动

- `PersonalMemorySection.kt`：`Row` 改为 `.heightIn(min = 48.dp).toggleable(value = enabled, role = Role.Switch)`，标签 `weight(1f)`；抽取 `applyEnabled(value)`，行点击和滑块拖动共用。
- `ProviderSetupWizard.kt`：`记住密钥` 行同样加 `.toggleable(value = rememberKey, enabled = !busy, role = Role.Switch)` 与 padding。
- 原因：`SettingsSwitch` 只负责拖动，点击/语义由父行提供；这两行此前只放了滑块。**不是开关状态或持久化问题。**

## 3. 重要发现

### 3.1 0.1.29 → 0.1.30 已解决的项

- **发布链路**：0.1.29 已从干净提交 `83794ad` 构建、打 tag、发布并同步 `main`；上一版评审的“未提交/脏构建”阻断项不再成立。
- **空车次语义（H-1）**：0.1.30 已把 `ticket_snapshot.availability` 改为：`rows` 为空时返回空 `SnapshotTrains`，只有“有记录但解析不出”才抛错（`backend/app/tools/ticket_snapshot.py:158-160`）。不再把空区间报成“查询失败”。
- **生产解析截号（B-1）**：0.1.30 已给 `_TRAIN_CODE_IN_TEXT_RE` 加左边界、给 `_PURE_DIGIT_TRAIN_RE` 排除字母/数字/下划线/连字符前缀（`backend/app/tools/_rt12306.py:232-234`）；`X1次列车`/`V6001次`/`DH1次`/`AB123次` 已不再截成错误号码。

### 3.2 测试现状

- 0.1.29 的全量（63 套）：**60 通过 / 3 失败**；失败均为环境：`test_tools`（本机 DNS 把 `example.com` 解析为 `198.18.1.22`）、`test_frontend_store`/`test_frontend_render`（本机 Node 缺 `libsimdjson.33.dylib`）。
- `test_api` 原先的失败不是网络：它用「那明天呢」要求 `emu.routing: ok`，而产品红线是“未来日期不查交路”；改为「那今天呢」后通过。`test_future_routing_guard` 已登记，继续覆盖该红线。
- 0.1.30 的 `release-0.1.30.md` 记录“67 套完整通过、2 套明确跳过”，`run_all.sh` 当前 72 套全部登记；**本轮没有复跑 0.1.30 的 72 套**，不要把发布记录当成独立复测。
- 遗留：`test_tools`/`test_api` 一类网络/模型依赖仍与离线套件混在同一清单；前端测试依赖宿主机 Node。

### 3.3 “图片”开关阴影问题（未复现）

- 用户反馈有截图：模型配置里的“图片”开关点一次后阴影不自动消失；当前**无法复现**。
- 代码事实：`MainSettingsScreen.kt:258-264` 的图片行是 `.toggleable(...)` 父行 + 子 `SettingsSwitch`（`:448-466`，只带 `draggable`）。设置页没有 `shadow()`/elevation（唯一 `Modifier.shadow` 是 `HistoryScreen.kt:225`，且显式传了 `RoundedCornerShape(16.dp)`），所以用户看到的“阴影”更可能是 **ripple/按压状态层**，不是控件投影。
- 待验证假设（按可能性）：
  1. 模拟器鼠标停在行上，Compose 的 **hover 状态层**一直保留；移开指针即消失，不是 bug。
  2. 父行 `toggleable` 负责 indication、子滑块 `draggable` 负责拖动，同一手势两个处理者；父行的 press 状态在子控件消费/重组时没收到干净的 release/cancel，ripple 卡住。
  3. 图片行是唯一位于 `RailReveal`（`RailMotion.kt:22` 的 `AnimatedVisibility`）内的开关行，且缺少其他开关行的 `.padding(vertical = 8.dp)`；可能放大 ripple 的生命周期问题。
- 未改代码；先保留截图与复现条件。若再次出现，优先区分“移开鼠标就消失”（hover）与“移开仍在”（indication 未释放）。

### 3.4 方角按压层（“方形阴影”）审计

结论：**不是 `Modifier.shadow` 的问题**。设置页/聊天页的按钮没有 elevation；方角来自 `Modifier.clickable`/`toggleable` 的 **ripple/indication 没有被 `clip(shape)` 约束**——背景是圆角，但按压层画在矩形布局边界上。

**2026-10-21 已修复（`7220b02`）**：下列位置已在 `background/border` 之后、`clickable` 之前补 `.clip(shape)`；共享组件 `SettingsButton` 和 `HistoryButton` 的修复覆盖了绝大多数文字按钮。

已确认的组件/位置：

| 组件/位置 | 圆角来源 | 状态 |
|---|---|---|
| `SettingsButton`（`MainSettingsScreen.kt:423-425`） | 12dp | 已修 |
| `HistoryButton`（`HistoryScreen.kt:273-275`） | 10dp | 已修 |
| `PhotoPendingButton`（`PhotoSpotCard.kt:126-127`） | 9dp | 已修 |
| `PhotoSourceRow` 标题行（`PhotoSpotCard.kt:140-141`） | 9dp | 已修 |
| 供应商预设行（`MainSettingsScreen.kt:346-349`） | 10dp | 已修 |
| 供应商卡片行（`MainSettingsScreen.kt:143-147`） | 16dp | 已修 |
| 历史选中会话行（`HistoryScreen.kt:172-174`） | 12dp | 已修（外层 clip） |
| 欢迎示例卡（`MainChatScreen.kt:581-584`） | 16dp | 已修 |
| 附件导入大按钮（`MainChatScreen.kt:548-550`） | 20dp | 已修 |
| 时刻卡 `ActionButton`（`ScheduleCard.kt:522-532`） | 10dp | 已修 |
| 时刻卡 `StateActionButton`（`ScheduleCard.kt:544-550`） | 10dp | 已修（外层 clip） |
| 票价卡详情行（`FareCard.kt:156-157`） | 8dp | 已修（外层 clip） |

**正确对照**：

- `HistoryScreen.kt:146` 的记忆入口行先 `.clip(RoundedCornerShape(12.dp)).clickable(...)`，ripple 是圆的。
- `MainSettingsScreen.kt:473` 的下拉框在父 `Column` 上 `.clip(dropdownShape)`，子项 ripple 被裁切。
- `HistoryScreen.kt:225` 的 `.shadow(8.dp, RoundedCornerShape(16.dp))` 形状参数正确。

**开关行也已处理（`34cf43e`）**：记住 API Key、图片、个人记忆、向导记住密钥的 `toggleable` 行补 `clip(12dp)`，整行 ripple 不再露出方角。

**仍未处理**：未做真机/模拟器截图复验；本次只验证 Kotlin 编译。

### 3.5 新解析器

详细建议见 [`backend-fuck-cr-parser-recommendations-20261019.md`](backend-fuck-cr-parser-recommendations-20261019.md)。当前 parser 未改，仍只有 `parse()` 语法候选能力；P0 建议（前导零状态、强弱候选、canonical 身份、号段证据、尾号链、reason 枚举）均未实施。

### 3.6 工作区清理

| 项 | 体积 | 建议 |
|---|---:|---|
| `dist/android`（76 APK） | 2.5 GB | 保留 0.1.30/0.1.29 与校验文件，其余归档 |
| `.android-build` | 18 GB | 保留 SDK/gradle/jdk；llama 构建目录按需归档 |
| `acceptance` | 488 MB | `runs/` 归档；正式 PNG/索引不动 |
| `.ai` | 293 MB | 未跟踪证据归档；10 个已跟踪脚本保留 |
| `.git` | 252 MB | 历史含两枚 ~29 MB APK；不建议为体积改写历史 |

本轮**没有删除任何文件**。

### 3.9 0.1.31 发布

- 版本 `0.1.31`，tag `v0.1.31` 指向构建提交 `7d06869`；Release 已公开并 latest。
- APK：`dist/android/OpenRailFanAI-0.1.31-arm64-release.apk`，56,344,832 B，SHA-256 `45f9f62ae333b64be91656c380fbe209197263b6094c95b6311b23d1a668dd60`。
- `docs/verification/release-0.1.31.json`：签名证书 `e644691a...` 与 0.1.30 一致；包内词典 19,898,368 B；GitHub 资产摘要一致。
- 发布脚本可用；但发现并修正了一个回执缺陷（见 §3.10）。

### 3.10 发布回执的 untagged URL 缺陷

- 现象：`release-0.1.30.json` 与 `release-0.1.31.json` 的资产 URL 是 `.../releases/download/untagged-<hash>/...`，而 GitHub Release 实际公开后的 URL 是 `.../download/v0.1.30/...`、`.../download/v0.1.31/...`。
- 原因：`scripts/release/publish.py` 在上传草稿资产后就把 `browser_download_url` 写进回执；草稿阶段的 GitHub 资产 URL 是 untagged，公开后脚本没有重新读取发布态资产。
- 影响：回执中的下载地址不可作为稳定引用；App 更新器读取的是 GitHub API 的实时资产 URL，不受这份回执影响。
- 修复：脚本在 PATCH 公开后重新 `GET /releases/tags/<tag>`，校验摘要/大小/URL 前缀，再用发布态资产写回执；两个历史回执已按实际公开 URL 更正。
- 未改已公开 Release 的资产内容，也未移动 tag。

### 3.8 输入区 / 消息区 IME 同步

- 规则：消息列表距底部 **≤32dp** 视为“贴底”；该状态下消息区视口尺寸变化时，按同一像素增量 `scrollBy`，让内容跟着输入栏同步上推。否则不滚动，保持当前阅读位置。
- 实现不依赖单独监听 IME，而是监听 **消息列表容器 `onSizeChanged`**：键盘弹出、附件面板展开、输入框多行增高导致的视口收缩都走同一路径，避免“输入栏先动、内容后动”。
- `followBottom` 只在用户停止滚动时重算（`snapshotFlow { isScrollInProgress }`），IME 造成的视口变化不会把它误判成“用户离开底部”；阅读状态也只在真实滚动时更新。
- 未做真机动画复验；32dp 阈值是产品口径的暂定值，可在 `MainChatScreen.kt` 调整。

### 3.7 文档索引问题

`docs/README.md` 当前引用 `independent-review-fixes-20261019.md`，但该文件不存在（截至 2026-10-21）。要么补文件，要么删掉/改正这条索引，避免新读者点空。

## 4. 未决事项

1. “图片”开关阴影：保留截图；下次复现时先验证 hover 与 ripple，再决定是否把父行/子滑块的 indication 收敛为一个所有者。
2. 方角按压层：`7220b02` + `34cf43e` 已修共享组件、主要圆角行与开关行；待真机/截图复验。
3. 新解析器 P0 六项：见独立文档；parser/shadow 保持不动。
4. 测试分层：`run_all.sh` 拆 `--offline`/`--live`；前端 Node 缺失时显式 `SKIP`。
5. 文档索引的失效链接（§3.7）。
6. 真机/模拟器复验 IME 同步：贴底应跟手、非贴底不应跳；32dp 阈值可按实际手感调整。
7. 用 0.1.31 APK 实测更新器（从 0.1.30 检查更新→下载→签名校验→安装）；本轮只核对了 GitHub API 资产 URL 与摘要。

## 5. 验证命令

```bash
cd /Users/xylo/Documents/OpenRailFanAI
git log --oneline -6
cat VERSION
cd backend && bash tests/run_all.sh        # 72 套；本轮未复跑 0.1.30 全量
bash ../scripts/android/build.sh :app:compileDebugKotlin
```

## 6. 变更记录（追加，不重写）

| 日期 | 提交 | 内容 |
|---|---|---|
| 2026-10-21 | `4525593` | 测试修正与 4 个离线套件登记；0.1.29 全量 60/63 |
| 2026-10-21 | `63583cc` | 复核版独立评审 |
| 2026-10-21 | `8aed092` | 新解析器评审与建议 |
| 2026-10-21 | `b767f4f` | 记忆/记住密钥开关整行点击；compile 通过 |
| 2026-10-21 | `7220b02` | 圆角按压层裁切；SettingsButton/HistoryButton 等已修 |
| 2026-10-21 | `34cf43e` | 开关行 ripple 裁切（记忆/图片/记住密钥/向导） |
| 2026-10-21 | `1e5de3d` | 贴底时消息区随输入栏同步上推（≤32dp） |
| 2026-10-22 | `7d06869` / `2173d90` | 0.1.31 发布与回执 |
| 2026-10-22 | `c7f9c9d` | 修复回执 untagged URL；更正 0.1.30/0.1.31 回执 |
| 2026-10-21 | 本文 | 建立交接文档；记录方角按压层审计与未决项 |
