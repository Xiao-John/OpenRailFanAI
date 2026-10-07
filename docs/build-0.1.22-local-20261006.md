# Main 0.1.22 本地构建

日期：2026-10-06。依据 backend-ticket-copy-alignment-20261006.md 与 main-copy-review-fixes-20261006.md 构建新版。

- 版本名称 0.1.22，版本号 122；Main arm64 Release，携带本地词典，沿用现有发布签名。
- 纳入客户端错误分类、设置失败反馈、票价口径、余票快照及词典更新文案修订。
- 后端文案交接涉及的六个源码文件已与交接工作树逐字节核对一致；统一票价参考说明、余票快照说明及查询时有票车次数量统计。
- 客户端优先采用后端提供的余票备注，缺少备注时才使用同口径默认说明，避免再次追加相似快照提示。
- 构建：bash scripts/android/build.sh -PincludeDict=true assembleRelease，成功。
- 包名 org.openrailfanai.app，versionName 0.1.22，versionCode 122；签名校验通过。
- APK：dist/android/OpenRailFanAI-0.1.22-arm64-release.apk；39,559,176 字节。
- SHA-256：fc15122469de60a3f462811efc192be2a9a7075258d093db9adde889482d8f1b；同目录附 .apk.sha256。
- 构建仍有既有 D8 Kotlin 元数据警告，未阻止打包。未运行模拟器、真机或端到端测试，本次仅确认构建、包元数据与签名。
- 本次未推送远程或发布 GitHub Release。

下一步：真机覆盖安装，检查票价与余票说明只出现一次，并确认网络、认证和铁路服务错误提示分别准确。
