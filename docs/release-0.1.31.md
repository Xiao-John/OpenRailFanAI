<!-- release-config
{"version":"0.1.31","previous_version":"0.1.30","repository":"Xiao-John/OpenRailFanAI","target_branch":"main","include_dictionary":true,"title":"Main 0.1.31 · 交互与按压层修复","validation":"Kotlin 主代码 compileDebugKotlin 通过；backend/tests/test_android.py 通过。未运行完整 72 套测试，未做真机/模拟器视觉与 IME 动画验收。"}
-->
# Main 0.1.31 · 交互与按压层修复

## 开关

- 个人记忆开关、“记住 API Key”、图片开关、配置向导的“记住密钥”恢复整行可点击；右侧滑块仍可拖动。
- 修复这些开关此前只能拖、点文字或点开关几乎无反应的问题。
- 开关行增加 `Role.Switch` 语义；个人记忆与记住密钥共用同一套保存逻辑。

## 按压层

- `SettingsButton`、`HistoryButton` 以及供应商卡片/预设、欢迎示例卡、附件导入入口、机位待确认/来源行、时刻卡操作按钮、票价详情等圆角可点区域补 `clip(shape)`。
- 按压/ripple 不再按矩形边界绘制，圆角按钮不再露出方角。
- 记住 API Key、图片、个人记忆、向导记住密钥的整行 ripple 同步裁成 12dp 圆角。

## 输入区与消息区

- 消息列表距底部 **≤32dp** 时视为贴底；键盘弹出、附件面板展开或多行输入导致消息区视口收缩时，按同一像素增量同步上推。
- 非贴底状态不滚动，保持用户当前阅读位置；键盘造成的视口变化不再被误判为“离开底部”。
- 键盘收起、面板关闭时按同一路径恢复，不新增悬浮层。

## 验证范围

- `:app:compileDebugKotlin` 通过。
- `backend/tests/test_android.py` 全部通过。
- 未运行完整 72 套后端/前端测试；未做真机或模拟器的视觉、输入法动画与无障碍验收。发布脚本只核对构建、签名、包内版本/词典和 GitHub 资产摘要，不自动执行产品测试。
