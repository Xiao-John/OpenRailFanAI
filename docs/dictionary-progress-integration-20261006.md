# 词典更新实时进度

日期：2026-10-06。范围为词典更新基础设施及Main Android设置页，不修改铁路查询、票价业务或已发布安装包。

## 兼容接口

新增 POST /api/updates/dictionary/apply/stream，请求仍为 latest_version。保留原 POST /apply 的请求、JSON响应和行为；新接口继续使用本机写入与来源限制，不新增凭证需求。

返回UTF-8 SSE，data为JSON，空行结束；每10秒无进度事件时发出注释心跳，避免导入阶段被视为静默失联。事件：

- type=progress；stage=check：核对远端版本。
- stage=download，completed/total为实际字节数，unit=bytes；total来自已校验的发布附件声明，不按时间模拟增长。
- stage=verify：检查累计文件大小和SHA-256。
- stage=import，table为 g_stop/g_trip/g_stop_time，completed为本表实际插入记录数，unit=records。没有总行数时不显示导入百分比。
- stage=validate：检查数据库完整性与引用关系。
- stage=commit：进入既有事务合并，保留原有缓存及版本规则。
- type=done，status=updated/unchanged，current为提交后的本地信息；只有该事件才能作为正常完成响应。
- type=error，code/message为错误；流被截断或未知事件不能解释为成功。

下载百分比100%仅表示接收完成，不表示词典更新完成。取消与断线清理下载；本地工作线程按既有run_io等待结束再清理临时文件。提交阶段可能在取消后完成，客户端继续重新读取本地版本，不声称回滚。

## 客户端

设置页显示当前阶段、耗时、实际已下载/总字节数及下载百分比；导入显示表的中文名称与已处理记录数。无可测分母的阶段使用进行中指示，不以虚构百分比替代。正常完成、失败或取消后结束活动指示；回读本地版本时单独显示确认阶段。

客户端只消费本地服务事件；旧服务不支持新路径时明确报错并回读本地版本，不自动重复提交旧更新请求。需要前后端配套打包。

## 验证与发布

Kotlin编译及Python语法检查通过。未执行下载/导入实测、模拟器测试或正式视觉验收；没有更新版本、构建APK或发布。0.1.19 Release不包含本次实时进度。下一轮实测应核对真实网络下载、各导入表计数、完成提交，以及下载中和提交中取消的不同结果。
