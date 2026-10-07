# 修复日志：模拟器 review 的 4 项缺陷（Main 0.1.22）

日期：2026-10-06。执行方：**非本项目主 agent** 的一次性修复，供主 agent 复核/合并。

- 基线结论见 [simulator-review-0.1.22-20261006.md](simulator-review-0.1.22-20261006.md)（模拟器实测 review）。
- 本次只动 review 报出的 4 项缺陷及其**直接**回归测试：**不改版本号、不动 `VERSION`/`VERSION.lm`、不产出 `dist/` 产物、不发布、不提交**。
- 工作区中他人未提交的改动原样保留；本次改动的准确边界见第 1 节。
- 修复全程处于**未提交状态**，验证针对 working tree。

> ⚠️ **交付前必读**：本仓库现有的 release 产物**不含本次修复**（见第 4 节 R1）。要发布必须先重新构建 release APK。

## 1. 改动清单

### D1 全新安装首次启动误报 `本地字典：不可用`

| | |
|---|---|
| 根因 | 该日志在 uvicorn 启动**之前**求值，而运行词典由应用 lifespan 合并包内词典时才建立（`app.main._lifespan` → `sync_bundled`），于是全新安装被如实报成"不可用" |
| 改法 | 探测块从 `serve()` 主体移到 `run_until_ready()` 内、`本地后端已监听 …` 之后（即 uvicorn `started` 之后），并补注释说明为何必须在这个时机 |
| 文件 | `android/app/src/main/python/server.py` |
| 影响面 | 仅日志时序；不改字典能力、启动流程与失败路径 |

```diff
         from app.main import app as asgi_app
-
-        # 一条把"字典到底有没有用上"摆到明处的日志：……
-        try:
-            from app.data.dict import available as dict_available, db_path as dict_db_path
-            _log.info("本地字典：%s（%s）", "可用" if dict_available() else "不可用", dict_db_path())
-        except Exception:
-            _log.warning("本地字典探测失败", exc_info=True)

         _beat(host, "导入 uvicorn…")
@@
             _log.info("本地后端已监听 http://%s:%d", HOST, port)
+            # ⚠️ 必须在 uvicorn **started 之后**探测：运行词典由应用 lifespan 启动时
+            # 合并包内词典才建起来（app.main._lifespan → sync_bundled），在那之前探测
+            # 会把"全新安装、尚未合并"如实报成"不可用"，而发布验收正是按首次安装判定的。
+            try:
+                from app.data.dict import available as dict_available, db_path as dict_db_path
+                _log.info("本地字典：%s（%s）", "可用" if dict_available() else "不可用", dict_db_path())
+            except Exception:
+                _log.warning("本地字典探测失败", exc_info=True)
             ok, detail = await asyncio.to_thread(_self_check, port)
```

### D2 低余量变动提示泄漏上游英文席别键

| | |
|---|---|
| 根因 | `ticket_query.py` 直接拼接上游原始席别键；仓库里另有两份各自维护的中文映射，未被这条用户可见文案使用 |
| 改法 | 席别名称收敛为**唯一来源** `app/ticket_copy.py::seat_label()`；删除 `fare_result.py`、`ticket_answer.py` 两份历史副本 |
| 文件 | `backend/app/ticket_copy.py`、`backend/app/tools/ticket_query.py`、`backend/app/fare_result.py`、`backend/app/pipeline/ticket_answer.py` |
| 影响面 | 仅**用户可见**席别名文案；机器可读字段（`seat_counts` 的键、工具 `text` 的事实块）保持原始键，`APP_VARIANT=lm` 口径不变 |

```diff
 # backend/app/tools/ticket_query.py（低余量二次校验）
-  changes.append(f"{t.get('train_no')} {k}: {v}→{ov}")
+  changes.append(f"{t.get('train_no')} {seat_label(k)}: {v}→{ov}")
```

### D3 用户可见文案 `。；` 连写（**7 处**，分三批修完）

| | |
|---|---|
| 根因 | 多处"拼句子"的代码无条件插「；」，而前一段常以「。」收尾 |
| 改法 | 拼接规则收敛为 `app/ticket_copy.py::join_clause(head, tail)` 与 `join_clauses(parts)`（去重版）；所有会接在「。」后面的拼接点统一走它们 |
| 文件 | `backend/app/ticket_copy.py`、`backend/app/tools/registry.py`、`backend/app/tools/ticket_price.py`、`backend/app/fare_result.py`（3 处）、`backend/app/tools/ticket_query.py`、`backend/app/pipeline/retrieve.py`、`backend/app/pipeline/service_batch.py` |
| 影响面 | 仅标点；普通段落仍以「；」分隔、空段不留前导/尾随分号、`tail` 为空时原样返回、`parts` 按出现顺序去重 |

```python
def join_clause(head: str, tail: str) -> str:
    """把 `tail` 接到 `head` 之后，且不产生「。；」这类连写。"""
    text = (head or "").strip().lstrip("；").strip()
    rest = (tail or "").lstrip("；")
    if not rest:
        return text
    if not text:
        return rest
    return text + rest if text[-1] in "。！？；" else text + "；" + rest


def join_clauses(parts) -> str:
    """按出现顺序去重后逐个拼接（旧 `"；".join(dict.fromkeys(parts))` 的安全版）。"""
    out = ""
    for part in dict.fromkeys(p for p in parts if p):
        out = join_clause(out, str(part))
    return out
```

七个站点（**第一批只修了前两处，后五处是复验时被设备端"整份响应递归扫描"和两轮独立复核抓出来的**）：
| # | 站点 | 触发条件 |
|---|---|---|
| 1 | `registry.py` 追加"快照最多复用" | 每次 `ticket.query`/`train.schedule` 命中缓存 TTL（review 实测到的那条） |
| 2 | `ticket_price.py` note 三段拼接（日期提醒 + 口径句 + 卧铺说明） | 日期表述无法识别 和/或 卧铺席别 |
| 3 | `fare_result.py::_base` 卡片 note（工具 note + 口径说明） | **每次成功票价查询**（最常出现的一条） |
| 4 | `fare_result.py` 空结果追加"未找到该查询区间的精确票价记录…" | 票价接口返回空记录 |
| 5 | `fare_result.py` 追加"接口未提供完整有效的席别金额…" | 金额缺失/非法（`status=partial`） |
| 6 | `ticket_query.py::_availability_note` 四段组装 | 日期表述无法识别（余票查询） |
| 7 | `retrieve.py` / `service_batch.py` 的聚合 note（`"；".join(dict.fromkeys(notes))`） | 多条工具 note 入聚合，且其中一条以「。」结尾（第二轮独立复核发现；已追踪消费方不读该字段，属机制性存量，一并修复） |

**红 → 绿记录**（每一处都先复现再改）：

```
# 站点 3（改前，与设备端实测字符串一致）
AssertionError: '。；' unexpectedly found in '12306 实际执行票价。；票价可作为购票参考，具体以购票页面为准。到发时刻为接口区间时刻，不代表实际运行状态。'
# 站点 4（改前）
AssertionError: '。；' unexpectedly found in '12306 实际执行票价。票价可作为购票参考…运行状态。；未找到该查询区间的精确票价记录，不能据此判断停运或无票。'
# 站点 2（改前，一次暴露两处）
AssertionError: '。；' unexpectedly found in '⚠️ 未能识别时间表述「下个礼拜三」…这类表述。；12306 实际执行票价。；卧铺为接口返回的席别价格，未细分上、中、下铺。'
# 站点 7（改前，用同一条 retrieve 路径换回旧聚合写法）
[BASELINE] note = [ticket.price] 12306 实际执行票价。；[ticket.query] 12306 实时余票（2026-10-05）；余票为查询时快照，…显示为准。
# 改后（同一路径）
[FIXED] note = [ticket.price] 12306 实际执行票价。[ticket.query] 12306 实时余票（2026-10-05）；余票为查询时快照，…显示为准。
Ran 11 tests ... OK
```

### D4 余票失败提示前缀重复

| | |
|---|---|
| 根因 | 前端给 `availability.error` 再加一次 `余票查询失败：`，而后端该字段已自带标题（`12306 余票查询失败：…`） |
| 改法 | 文本里已说过"余票查询失败"就原样展示，否则才补前缀 |
| 文件 | `android/app/src/main/java/org/openrailfanai/app/FareCard.kt` |
| 影响面 | 仅该提示文案；票价侧（`priceError` 直出）本来就无前缀，口径因此统一 |

```diff
-  "failed" -> FareNotice("余票查询失败：${available.error ?: "请稍后重试"}", error = true)
+  "failed" -> {
+      // 后端该字段可能已自带同名标题（如「12306 余票查询失败：…」）；
+      // 只要文本里已经说过这件事，就不再补前缀，避免「X：X：」重复。
+      val detail = available.error?.takeIf(String::isNotBlank) ?: "请稍后重试"
+      FareNotice(if (detail.contains("余票查询失败")) detail else "余票查询失败：$detail", error = true)
+  }
```

> 第一版用的是 `startsWith("余票查询失败")`，**在设备上被证伪**（后端文本以 `12306 ` 开头，仍会重复加前缀），改用 `contains` 后重新构建才通过。这条留下来是因为"改完必须回设备上看一眼"正是本轮 review 的教训。

## 2. 验证

### 2.1 后端单测

| 套件 | 结果 |
|---|---|
| `test_ticket_copy`（本次主战场） | PASS，**11 项**，其中 7 项为本次新增 |
| `test_ticket_query_delivery` | PASS，**17 项**（含新增的聚合 note 用例） |
| `test_r1_fixes2` | PASS（含更新后的低余量席别名断言） |
| `test_r1_fixes` / `test_ticket_pair` / `test_fare_availability` / `test_fare_contract` / `test_executed_fare` / `test_multidate_services` / `test_pipeline` / `test_routing` / `test_structured_sse_visibility` / `test_mock_render` | PASS |
| 全量 `bash tests/run_all.sh` | **35 / 41**，失败集合与修复前逐个一致（见第 3 节归因）；原始输出 `.ai/review-20261006/fix/full_suite_final_v3.log` |

新增/更新的测试（`backend/tests/test_ticket_copy.py`）：

- `test_snapshot_tail_never_doubles_sentence_punctuation`（站点 1 + `join_clause` 边界）
- `test_fare_note_never_doubles_sentence_punctuation`（站点 2，三段拼接）
- `test_fare_card_note_never_doubles_sentence_punctuation`（站点 3，**每次查询都会走的路径**）
- `test_empty_fare_card_note_never_doubles_sentence_punctuation`（站点 4）
- `test_availability_note_segments_never_double_punctuation`（站点 6，含 `；；` 与 LM 分支）
- `test_join_clauses_dedups_and_never_doubles_punctuation`（站点 7 用的新聚合 helper）
- `test_seat_label_is_single_source_for_user_facing_copy`（D2）
- `backend/tests/test_ticket_query_delivery.py::test_aggregate_note_never_doubles_sentence_punctuation`（站点 7 端到端：真实 `retrieve()` 路径）
- `backend/tests/test_r1_fixes2.py::test_low_count_second_verification`（D2：断言改为 `一等座: 1→无`，并**新增** `assert "first_class" not in res.note`）

> ⚠️ **覆盖缺口（比 A 报的更宽）**：`tests/run_all.sh` 的 41 个 `SUITES` 里**没有任何一个票务专项套件**——`test_ticket_copy`、`test_ticket_query_delivery`、`test_fare_availability`、`test_ticket_pair`、`test_ticket_delivery_dedup`、`test_executed_fare`、`test_fare_contract`、`test_multidate_services`、`test_structured_sse_visibility` 全都不在。也就是说"35/41"**完全不覆盖本次修复的回归测试**（本次新增的 3 个断言文件里，只有 `test_r1_fixes2` 在套件内）。这些文件本次都**直接逐个跑过并 PASS**，但要让它们在 CI/日常回归里生效，必须先把它们接进 `run_all.sh`——该文件属共享入口，本次未改，留给主 agent。见第 4 节 R2。

### 2.2 设备端实测（最终产物）

构建：`gradle -PincludeDict=true assembleDebug`（**不产出 `dist/` 产物**，只写 `android/app/build/outputs/apk/debug/app-debug.apk`），装到 `emulator-5554` 的 debug 包 `org.openrailfanai.app.debug`（**未触碰用户的 `org.openrailfanai.app`**）。

| 项 | 动作 | 结果 | 证据 |
|---|---|---|---|
| D1 | `pm clear` 后首次启动抓 logcat | `本地后端已监听 …` → **`本地字典：可用（…/files/dict.db）`** → `自检通过：/ → HTTP 200` | `.ai/review-20261006/fix/d1_final_fresh_install.logcat.txt` |
| D1 对照 | 修复前 release 0.1.22 全新安装 | `本地字典：不可用（…/files/dict.db）` | review 报告第 3 节 D1 |
| D3 | 对**设备内打包后端**发 7 组真实请求（票价/余票/多日期 3 卡/时刻卡/交路卡/卧铺+无法识别日期/声明能力），对**整份响应 JSON** 递归扫描 `。；` | **全部 0 处**；note 形如 `12306 实际执行票价。票价可作为购票参考…未找到该查询区间的精确票价记录，不能据此判断停运或无票。` | `.ai/review-20261006/fix/d3_final_all_cases.json` |
| D3 对照 | 修复前同一请求 | `…显示为准。；查询快照最多复用…`、`12306 实际执行票价。；票价可作为购票参考…` | review 报告 2.3 节 + `.ai/review-20261006/fix/d3_after_legacy_receipt.txt` |
| D4 | 断网 → adb 驱动 `查票价`→`查询` → dump 全文计数 | `余票查询失败` 出现 **1** 次；文本 `12306 余票查询失败：网络请求失败 (已重试3次): …` | `ui/d4_final_availability_failure.png`（验证后已恢复网络） |
| D4 对照 | 修复前同一场景 | `余票查询失败：12306 余票查询失败：…` | `ui/error-card-5s.png` |
| 打包一致性 | 反汇编 APK 内 `assets/chaquopy/app.imy/*.pyc` | `ticket_copy`→`join_clause,seat_label`；`registry`→`_append_snapshot_tail,join_clause`；`ticket_price`→`join_clause`；`ticket_query`→`join_clause,seat_label`；`fare_result`→`join_clause,seat_label`；`ticket_answer`→`seat_label`；`_SEAT_NAMES` 全部消失；`server.pyc` 的字典探测常量位于 `run_until_ready`（uvicorn `started` 之后）而非 `serve` 主体 | `.ai/review-20261006/fix/final_reverify.sh` 输出 |
| D2 | 设备端无法按需制造低余量波动（需上游两次采样恰好不同），以单测复现为准 | 单测输出 `…G4 一等座: 1→无…`，且 note 内不含 `first_class` | `test_r1_fixes2` 运行输出 |

验证脚本：`.ai/review-20261006/fix/final_reverify.sh`（重建 + 字节码核对 + 重装 + `pm clear` + D1 日志 + D3 设备扫描，可重复执行）。

**最终产物上的最后一次 D1 复核**（含 `join_clauses` 的 v3 修订，22:21 重装后 `pm clear` 首启）：

```
10-06 22:21:06.749 W python.stderr: INFO railfan.android: 本地后端已监听 http://127.0.0.1:56317
10-06 22:21:06.749 W python.stderr: INFO railfan.android: 本地字典：可用（/data/user/0/org.openrailfanai.app.debug/files/dict.db）
10-06 22:21:06.758 W python.stderr: INFO railfan.android: 自检通过：/ → HTTP 200（4000B）；/src/main.js → HTTP 200（4000B）
```

> 按用户指示，模拟器复验到此为止（**真机测试由用户另行安排**）。第 2.2 节的 D3/D4 设备证据取自同一份源码构建的前一版 debug 包，两版之间只差 `retrieve.py`/`service_batch.py` 的聚合拼接（已由单测与 `retrieve()` 级端到端用例覆盖）。

### 2.3 独立验证

- **第一轮（验证方 A，未参与修复）**：对 D1–D4 **全部判定"已修复"**，并用"把修复逐点改回原样"的基线复现了原缺陷。原文见第 3 节。
- **第二轮（验证方 B）**：针对"验证方 A 之后提交方追加的增量"（`fare_result.py` 三处、`ticket_query.py::_availability_note`、`join_clause` 的 `lstrip("；")`）做独立复核，判定**全部成立**，并额外指出**聚合 note 仍有存量「。；」**（提交方据此又修了站点 7）。原文见 3.3。
- B 之后再无代码改动被独立复核：站点 7（`join_clauses`）与 `join_clause` 的 `tail` 加固是**提交方自证**（红→绿 + 单测 + `retrieve()` 端到端用例），真机测试由用户安排的人完成。

> 为什么需要第二轮：验证方 A 固定的是 `ticket_copy a41e4069 … ticket_price dd9c2ac1` 这一修订，其结论**不覆盖**之后的改动。第二轮结论出来前，第 2.2 节的设备证据是提交方自证。

## 3. 独立验证结论（原文摘要，未润色）

### 3.1 验证方 A：D1–D4 逐条判定

复核对象修订（A 固定）：`ticket_copy a41e4069`、`registry 6f6ed360`、`ticket_query 12f7dc40`、`ticket_answer 7cb78279`、`fare_result fc6fd2f4`、`ticket_price dd9c2ac1`。

> **判定：D1 已修复 / D2 已修复 / D3 已修复 / D4 已修复。**
>
> - D1：`pm clear` 后 `本地后端已监听 21:45:11.750` → `本地字典：可用`，`dict.db` mtime = `21:45:11.692`（本次启动 lifespan 才生成）；反汇编 `server.pyc`，字典探测在 `run_until_ready` 内 offset 734 > 监听 662。二次复现 21:51:28 同序。
> - D2：自写脚本（未引用仓库测试）替换 `tq.rt`，两次采样 `business 3→2`、`first_class 1→无` → note 为「…G1 商务座: 3→2；G1 一等座: 1→无…」，`has_raw_english_key: False`；**反例**：改回原始键后输出 `G1 business: 3→2；G1 first_class: 1→无`。
> - D3：端到端 receipt 为「…为准。查询快照最多复用 15 秒…」，`contains '。；': False`；**反例**：只回退 registry 拼接后复现原文「…为准。；查询快照最多复用…」。
> - D4：断网驱动 UI，两次 dump 均只出现 1 次「余票查询失败」；网络已恢复（`airplane_mode=0`、`MOBILE CONNECTED`）。
>
> **回归归因**：`rsync` 出 `backend/` 副本并逐点回退后跑同样 6 套，失败断言**逐字相同**——`test_policy`「knowledge 策略未被注入 prompt」、`test_api`「上下文继承 target=G1… 3 次未满足」、`test_pipeline_performance` `TimeoutError`→`assert decision[3]=="llm-merged"`、`test_config_docs`「.env.example 缺少 DICT_BUNDLED_DB_PATH」、`test_static_assets` `KeyError 'content-length'`、`test_android`「app-debug-androidTest.apk 里没有 assets/webapp/build.json」。且 6 套件**均不引用** `ticket_copy/seat_label/join_clause/_append_snapshot_tail`。→ **6 个失败全部是既有失败，无一由本次修复引入**（`test_api`/`test_pipeline_performance` 依赖真实 LLM，属环境性）。

A 的额外发现（提交方采纳的处理见第 4 节）：

1. **release 产物是旧的**：`app-release.apk` 构建于 20:38:44，早于 `server.py` 修复时间 21:38:10；反汇编其 `server.pyc`，字典探测仍在 `serve`（旧位置）→ **该 APK 仍带 D1 缺陷**。（A 未安装该包，仅字节码比对。）
2. **新增测试未接入 `run_all.sh`**：`test_ticket_copy`、`test_fare_availability`、`test_ticket_delivery_dedup`、`test_ticket_pair` 都不在 `SUITES` 里；直接跑 4 个均 exit=0。属**覆盖缺口**。
3. **D2 只覆盖了 note**：`ticket_query.py` 的 `_seat_summary` 与 `_seat_counts` 仍把英文键写进工具 `text`（模型可见事实块）。按设计机器字段保留原键，但若被模型转述进用户可见散文，英文键仍可能漏出。
4. **D4 守卫是中文子串匹配**：后端标题措辞一变，重复前缀就会回归。
5. **无法验证**：release 包（`org.openrailfanai.app`）的**运行时**首启日志（A 按要求未安装、未启动该包）。

### 3.2 提交方对 A 的处理

| A 的发现 | 处理 |
|---|---|
| 1 release 产物是旧的 | **确认并升级为交付阻塞项**（第 4 节 R1）：本次刻意不重建 release（不产出 `dist/`），但明确要求发布前重建 |
| 2 测试未接入 `run_all.sh` | 确认（第 4 节 R2）。`run_all.sh` 是共享入口，本次不改，避免与主 agent 的在途工作冲突 |
| 3 `_seat_summary`/`_seat_counts` 仍用英文键 | 确认并**有意保留**：`seat_counts` 的键被既有测试钉住（`test_ticket_copy.py:22` 断言 `{"second_class": 2}`），改键会改机器口径；`_seat_summary` 只进模型事实块。**未修**，记录为残留路径 |
| 4 D4 守卫脆弱 | 确认。**未改**：改成结构化标志需要动后端契约，超出本次范围；记录为已知脆弱点 |
| 5 无法验证 release 运行时 | 接受该边界，未强行安装 release 包 |

### 3.3 验证方 B：增量复核

B 的做法：自写脚本（含 28 例 `join_clause` 边界逐例比对）、`rsync` 出 `backend/` 副本并**逐点回退 5 处**（`fare_result.py` 三处 + `ticket_query.py` 旧 f-string + `join_clause` 去掉 `lstrip("；")`）后跑**同一批**脚本。原文摘要：

> **主张 1（`join_clause` 语义）成立**：28 例逐例比对，R2/R3/R4 全对；R1 有 3 处**字面偏差**——返回的是 `head.strip().lstrip("；").strip()` 而非"原样 head"（`("；低余量…","")→"低余量…"`、`("abc  ","")→"abc"`），且以「；」结尾的 head 会保留尾随「；」。无实际影响，但 docstring 措辞过强。
>
> **主张 2（`fare_result.py` 三处）成立**：真实 `TicketPriceTool` + 打桩 + 真实 `serialize_display_results`，三种载荷（正常有票 / `data=[]` empty / 金额缺失与非法 partial）note 均无 `。；`、无 `；；`，`FARE_REFERENCE` 与"到发时刻"句各恰 1 次。**实测该文件其实是 4 处 `join_clause`**（另有 `:226` 时刻不一致拼接），第 4 处也干净。
>
> **主张 3（`_availability_note`）成立**：真实 `TicketQueryTool().invoke`（两次采样 1→5 触发低余量变化 + 日期=`下个礼拜三`），main 下四段齐全、snapshot 恰 1 次、render 后仍 1 次；LM 两例**均不含快照**；"与旧行为一致"在**交付正文**层面成立，**工具 note 层面已被有意改变**（main 下快照由 `ticket_answer.render` 移进 tool note，render 有 dedup 保证不重复）。
>
> **主张 4（反例对照）复现成立**：回退后 fare 三处全部复现 `。；`（如 `12306 实际执行票价。；票价可作为购票参考…`、`…运行状态。；未找到该查询区间的精确票价记录…`、`…运行状态。；接口未提供完整有效的席别金额…`），ticket_query 复现 `…这类表述。；**低余量数据在两次采样间发生变化**…`。
>
> **主张 5（回归）10/10 通过**：`test_ticket_copy` OK(10)、`test_r1_fixes2` ✔、`test_executed_fare` OK(13)、`test_fare_contract` OK(12)、`test_fare_availability` OK(12)、`test_ticket_query_delivery` OK(16)、`test_ticket_pair` OK(5)、`test_multidate_services` OK(25)、`test_pipeline` ✔、`test_routing` ✔。
>
> **B 自己发现的问题**：
> 1. **同类缺陷仍有存量点**：`retrieve.py:978` 与 `service_batch.py:213` 的 `"；".join(dict.fromkeys(notes))` 仍能产出 `。；`（实测聚合串 `'[ticket.query] …显示为准。；[ticket.price] …'`）。B 追踪消费方后判定**在已追踪路径上不是用户可见**（`generate`/`orchestrator`/`api` 只读 data 里逐条 entry 的 note），但属同一缺陷类。
> 2. **`join_clause` 不设防 `tail`**：`("abc。","；低余量…") → "abc。；低余量…"`，防 `。；` 完全依赖调用点自己 `lstrip`，属脆弱点。
> 3. 主张 2 的"三处"少算了一处（见上）。
> 4. R1 字面偏差（见上）。
> 5. **LM 无副作用**：会追加 `AVAILABILITY_SNAPSHOT` 的 `ticket_answer.render` 仅被 `retrieve.py:987` 与 `service_batch.py:185` 调用，两条路径都在 `APP_VARIANT != "lm"` 分支内 → LM 交付面未因本批改变。
>
> **总评**：这批增量独立可复现地成立；唯一存量隐患是 retrieve/service_batch 的聚合 `"；".join` 仍能拼出 `。；`，以及 `join_clause` 不防 tail 前导分号的脆弱性。

**提交方对 B 的处理**：

| B 的发现 | 处理 |
|---|---|
| 1 聚合 note 仍能产出 `。；` | **已修**：新增 `join_clauses(parts)`（去重 + 折叠），替换 `retrieve.py` 与 `service_batch.py` 的旧聚合；补 `retrieve()` 级端到端用例 + 红→绿对照（见 D3 站点 7） |
| 2 `join_clause` 不防 `tail` | **已修**：`rest = (tail or "").lstrip("；")`，调用方不再需要自己 `lstrip`；`ticket_query.py` 里那处 `.lstrip("；")` 保留但已非必需 |
| 3 "三处"少算 | 已确认为 4 处（多出 `fare_result.py:226`），该处本就是 `join_clause` 且干净；D3 站点表已按实际列全 |
| 4 R1 字面偏差 | **已修 docstring**：明确"返回规范化后的 `head`"与"已有的尾随分号会保留" |
| 5 LM 无副作用 | 采信，未再改动 LM 相关分支 |

## 4. 残留与交付前必做

| # | 项 | 说明 |
|---|---|---|
| **R1** | **release 产物不含修复** | 现有 `android/app/build/outputs/apk/release/app-release.apk`（20:38）与 `dist/android/OpenRailFanAI-0.1.22-arm64-release.apk` 仍是修复前构建，**首启仍会误报字典不可用**。发布前必须 `bash scripts/android/build.sh -PincludeDict=true assembleRelease` 重建（本次刻意不产出 `dist/` 产物） |
| **R2** | 回归测试未接入 `run_all.sh` | 41 个 `SUITES` 里**没有任何票务专项套件**（见 2.1 的覆盖缺口）；本次新增/更新的断言只在手工直跑时生效。建议主 agent 至少加入 `test_ticket_copy`、`test_ticket_query_delivery`、`test_fare_availability`、`test_ticket_pair`、`test_executed_fare`、`test_fare_contract`、`test_multidate_services` |
| R3 | `_seat_summary` / `_seat_counts` 仍用英文席别键 | 机器字段按设计保留；只进模型事实块。若将来该文本进入用户可见散文，需再过 `seat_label()` |
| R4 | D4 守卫是中文子串 | 后端标题措辞变化会让重复前缀回归；彻底解法是后端给出结构化标志 |
| R5 | `.env.example` 缺 `DICT_BUNDLED_DB_PATH` | 既有失败（`test_config_docs`），属他人 in-flight 改动的文档缺口，未越界修改 |
| R6 | `test_api` / `test_pipeline_performance` 失败 | 依赖真实 LLM，属环境性；A 已确认与本次修复无关 |
| R7 | D5（review 观察项：回到底部按钮遮挡内容） | 既有设计取舍（`ui-design-boundaries.md` 接受覆盖消息区域），未动 |
| R8 | 余票失败文案里的英文 `[Errno 7]` | 可读性可优化，不属本次 4 项缺陷，未改 |
| **R9** | **真机测试未做** | 按用户指示，模拟器复验已停止，**真机（覆盖安装、首启日志、票价/余票说明只出现一次、网络/认证/铁路服务三类错误提示）由用户另行安排的人执行**——这正是 `build-0.1.22-local-20261006.md:16` 原定的下一步 |
| R10 | 站点 7 与 `join_clause` 的 `tail` 加固**未经独立复核** | 验证方 B 的结论不覆盖这两处（B 之后才改）。已由提交方红→绿 + 单测 + `retrieve()` 端到端用例自证；如需三方背书，可在真机测试同批做 |

## 5. 未做的事（明确声明）

- 未改 `VERSION`、未跑 `scripts/android/build.sh`（**没有**新的 `dist/android/*.apk`）、未提交 git、未发布。
- 未触碰他人未提交的改动（`VERSION`、`acceptance/**`、`android/**` 其他文件、`backend/**` 其他文件、`frontend/**`、`docs/README.md` 等）。
- 未做整体重构：席别名称只在 3 个调用点收敛到 `ticket_copy`，没有新建分层。
- 未修改 `tests/run_all.sh`（共享入口，见 R2）。
- 按用户指示**未继续做模拟器复验**；未安装、未启动 release 包（`org.openrailfanai.app`），用户设备的既有配置与数据未被触碰。
