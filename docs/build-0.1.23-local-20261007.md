# Main 0.1.23 本地构建

日期：2026-10-07。依据 `fix-log-0.1.22-simulator-review-20261006.md` 完成回归入口整理并重建 Main Release。

- 将 8 个票价/余票相关专项套件加入 `backend/tests/run_all.sh`：文案、余票交付、余票结构、成对查询、交付去重、执行票价、票价契约、多日期服务。
- 逐项运行上述 8 个套件，110 项测试全部通过；`bash -n tests/run_all.sh` 与 `git diff --check` 通过。未运行完整 `run_all.sh`，因此不代表全项目回归全绿。
- 版本名称 0.1.23，版本号 123；Main arm64 Release，携带本地词典。
- 构建：`bash scripts/android/build.sh -PincludeDict=true assembleRelease`，成功。
- 包名 `org.openrailfanai.app`；版本元数据和签名验证通过，签名证书与现有 Main 包一致。
- APK：`dist/android/OpenRailFanAI-0.1.23-arm64-release.apk`；39,575,560 字节。
- SHA-256：见同目录 `.apk.sha256`。
- Gradle 报告既有 D8 Kotlin 元数据重写警告，但打包成功。
- 未运行模拟器或真机；未推送远程、提交或发布 GitHub Release。

下一步：在真机覆盖安装，检查首次启动词典日志、余票快照说明和低余量席别中文提示。
