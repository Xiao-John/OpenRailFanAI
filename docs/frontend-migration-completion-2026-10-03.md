# Main 前端迁移完成记录

记录日期：2026-10-03。当前版本：0.1.9。

## 当前结论

Main 已从旧网页入口转入原生 Compose。依据用户最新确认的整体协调优先标准，时刻、交路、查询中、部分成功、无记录、连接失败、阅读追问、查询详情及历史九种状态均通过。原图及旧像素偏差保留，未把旧的逐像素失败记录改称通过。

正式证据：`acceptance/index.yaml`、`acceptance/_failed/index.yaml`及`acceptance/runs/20261003T103246-78d2c7c3/manifest.yaml`。逐项完成审查见`.ai/reports/frontend-completion-audit.yaml`。

## 功能与实际运行

- 自动原生测试19项：18通过、0失败、1项跳过。跳过的实时铁路查询已另行显式执行并通过，记录在`.ai/reports/native-live-query-validation/passed/`。
- 桌面入口打开原生页；聊天、历史和设置可以往返；重复打开保留原页面与草稿；真实键盘弹出和收起后输入栏保持可用。
- 旧会话字段及设置迁移检查通过，密钥使用现有加密存储约定。
- 修复错误事件之后丢弃结束事件及结构化事实、历史卡片重试使用其他问题、停止和重试时重复显示内容的问题。
- 0.1.9正式包已在API35辅助模拟器完成安装、启动及服务就绪检查。构建与安装包哈希、体积记录在`.ai/reports/native-release-runtime.json`。

## 当前产物

`dist/android/OpenRailFanAI-0.1.9-arm64-debug.apk`和`dist/android/OpenRailFanAI-0.1.9-arm64-release.apk`为本次交付。正式包约34.5MB，较迁移前基线增加约5MB；期间包含应用整体变更，不能全部归因于Compose。保留原WebView排障意图，Main以原生页为正常入口。

## 后续维护

当前运行证据来自API35模拟器；建议在真机复查手势导航、大字体及个人云模型配置，再扩展旧Android版本验证。Main已为原生日历使用的java.time启用兼容支持，没有提高minSdk24。既有后端并行开发边界文档继续有效。
