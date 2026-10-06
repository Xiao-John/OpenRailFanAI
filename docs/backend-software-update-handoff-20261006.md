# 软件更新：前端交接

2026-10-06。Main后端已实现GitHub正式版检测和校验下载。软件更新与词典独立更新分别实现、分别显示状态。现有聊天、展示协议和版本号未改变。

## 接口

- `GET /api/updates/software?current_version=0.1.17&abi=arm64-v8a`
- `POST /api/updates/software/download`，JSON：`{"current_version":"0.1.17","latest_version":"0.1.18","abi":"arm64-v8a"}`。返回APK文件，`Content-Disposition`提供文件名，`X-Content-SHA256`提供已验证的SHA-256。

检测响应含：`component=software`、`status=ok`、`current_version`、`latest_version`、`update_available`、`comparison`、`release_url`、`published_at`、`notes`、`asset`、`download_supported`、`installation`、`dictionary_policy`。`asset`为 `{id,name,size,sha256,url}` 或null。

版本按数字三元组比较；只选择本项目最近100个发布中的最高正式版本，排除草稿、预发布和debug标签。`current_version`由客户端传真实已安装版本，不能用远端版本代替。未知开发版本返回 `update_available=null`、`comparison=unknown`；不声称已是最新版。

当前支持Main Android正式APK；`arm64-v8a`映射现有资产命名`arm64`。没有对应架构资产时`download_supported=false`。服务器源码部署、LM封测版本及iOS不自动更新；本次没有授权修改这些打包轨道。

## 前端负责

1. 软件更新提供独立检测入口，依次展示检查中、有更新、已最新、版本不可比、失败。更新说明来自GitHub发布正文，应作为普通文本或安全Markdown呈现。
2. 用户选择下载后可读取后端已校验文件并保存至应用缓存；下载期间后端先校验完整文件再回传，不提供后端下载百分比，不伪造进度。客户端断开或取消后清理本地残片。
3. 调用Android系统安装器，处理安装来源许可和FileProvider。保持现有包名、签名及会话数据；系统安装完成后才显示已升级，下载完成不等于安装完成。
4. **软件升级自动接收包内词典**：正式构建需要启用既有`includeDict=true`，将APK的`assets/dict/dict.db`解包到单独的不可变路径，例如应用私有目录`bundled-dict/dict.db`。每次软件版本/包内词典内容变化时重新解包，用临时文件完成后再替换。
5. 启动Python前设置 `DICT_BUNDLED_DB_PATH` 为上述绝对路径；`DICT_DB_PATH`继续指向独立运行词典。后端启动时按数据版本合并，不覆盖更新的本地GTFS，不删除逐站线路及车站档案缓存。不能把两个路径设成同一个文件。

包内词典自动导入已实现并测试；**当前默认打包仍不包含词典，Android解包/设置环境变量/安装器及UI尚未接入**。这些交给前端执行，不能将本次后端完成记录当成APK更新验收。

## 错误与验证

失败返回HTTP错误，`detail={code,message}`：网络/限流/无发布/缺校验信息等为502；下载版本变化或没有更新/对应包为409；下载繁忙为429；下载超时为504。失败不显示“已是最新版”。最多同时交付两个安装包，文件上限512MiB，下载校验失败不交付残片。公网发布文件无需PAT，禁止把个人token打入客户端。

真实核验使用测试当前版本0.1.17：识别正式0.1.18，下载34,659,283字节APK，校验与GitHub `digest`一致。本机VERSION仍为0.1.18，测试不表示本机存在更高软件版本。没有实际安装APK、修改版本或推送发布。

实现依据：[GitHub发布资产接口](https://docs.github.com/en/rest/releases/assets)。证据：`.ai/github-updates/2026-10-06-v1/`。另见[词典更新交接](backend-dictionary-update-handoff-20261006.md)。

应用到当前项目后，14组既有后端回归通过。词典套件的虚构站名网页查询使用固定空页面夹具，其首次联网尝试已被离线守卫阻断并保留日志；真实下载证明另行归档，不把夹具视作真实网络成功。
