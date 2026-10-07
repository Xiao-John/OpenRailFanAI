# Main 客户端机位词典接入

日期：2026-10-07。依据 [新版词典合入交接](backend-photo-dictionary-integration-20261007.md) 接入；本轮修改 Android 设置页和打包准备，不修改后端业务、词典合并逻辑或聊天卡片。

## 客户端显示

- 设置页分别展示时刻词典版本和机位攻略库信息。机位信息来自 `current.photo_spots`，包括攻略篇数、收录范围和独立数据版本，不将篇数称为机位数量。
- 缺少可选字段的旧后端显示“当前服务未提供机位库信息”；明确 `available=false` 时显示尚未收录，不虚构数据版本或数量。
- 在线检查、更新和完成状态明确指向时刻词典。机位库通过携带完整词典的软件更新分发；在线 GTFS 更新成功不意味着机位库更新。
- 机位数据版本表示数据修订时间，不表示重新采集时间；顶层 `version` 继续只表示 GTFS 时刻版本。

## 完整词典打包

Main 正式包沿用 `includeDict=true`，从 `backend/data/dict.db` 取得完整 SQLite 备份。新增 `scripts/android/stage-dictionary.py`，正式打包检查机位两张表、`content_hash`、非空攻略与发现记录，以及有效数据版本和结构版本 `1`；验证通过后原子替换暂存资产，失败保留原暂存文件并终止打包。

包含词典时每次重新取得只读快照，不仅依赖 `dict.db` 修改时间，避免已提交的数据仅在 WAL 中时被构建缓存遗漏。不筛选或重写攻略正文、查询记录、其他词典表及元数据；完整词典仍是被 Git 忽略的本地数据文件。

既有启动接入保持有效：`assets/dict/dict.db` 解包至 `files/bundled-dict/dict.db`，通过 `DICT_BUNDLED_DB_PATH` 交给后端；运行库使用独立的 `files/dict.db` 和 `DICT_DB_PATH`。启动不直接用包内数据库覆盖运行库，版本合入由后端负责。

## 本轮验证

- 打包脚本 4 项专项检查通过：已提交 WAL 数据完整复制、无效机位版本拒绝且保留旧资产、旧词典与正式机位要求区分、禁止源库与目标路径相同。
- 实际完整词典快照校验通过：810 篇攻略、265 个收录范围、2562 条发现查询；`photo_spot_doc` 和 `photo_spot_seed` 与源库逐行哈希一致，机位元数据相同。快照 19,496,960 字节。
- 模拟器设置页专项检查通过：新版信息、无该字段的旧后端、明确无机位数据、GTFS 已最新状态。使用本地受控 HTTP 服务，不调用云端模型或在线更新源。
- Kotlin 完整编译通过。首次增量编译未解析已有顶层组件，禁用增量重新完整编译后通过；没有因此修改共享组件或全局构建参数。
- 桌面后端已启动，`/api/updates/dictionary/local` 返回独立 GTFS 版本及正确的机位数据版本、810 篇攻略、265 个范围。
- 将 Debug 包安装到 Android 15 模拟器后正常启动；确认 `files/bundled-dict/dict.db` 和 `files/dict.db` 是两个独立文件。实际设备内后端接口也返回 810 篇攻略、265 个范围及 `2026-10-07T07:59:19+00:00` 数据版本，证明包内数据已进入运行库。

可复跑专项检查：`python3 scripts/android/test_stage_dictionary.py`；设置页用 `bash scripts/android/build.sh -PincludeDict=true -Pkotlin.incremental=false connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=org.openrailfanai.app.ComposeDictionaryStatusTest`。设置页测试结果位于 `android/app/build/outputs/androidTest-results/connected/debug/`。

版本仍为 0.1.23。本轮为验证生成了 Debug 测试包，没有构建新版 Release、发布或推送远端；设置页不在现有九状态设计对照范围内，不将专项功能测试称为完整视觉验收。

下一步：正式出包时递增版本并携带完整词典；在保留旧运行库的设备上覆盖安装，检查新版机位合入及在线 GTFS 更新后机位版本保持。
