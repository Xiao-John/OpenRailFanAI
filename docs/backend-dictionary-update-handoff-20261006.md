# 词典更新：前端交接

2026-10-06。词典更新独立于软件版本、APK下载和安装。Main后端从原GTFS数据源的GitHub Releases检查并拉取，不在本项目重新发布第三方整表。

## 接口

- `GET /api/updates/dictionary/local`：无需联网，返回本地`current={available,version,pulled_at,counts}`。缺库时只有前三项；`counts`为三个GTFS表的记录数，车次数包含占位车次。
- `GET /api/updates/dictionary`：检查上游新版本。返回 `component=dictionary`、`status=ok`、`current`、`latest_version`、`update_available`、`asset={id,name,size,sha256,url}`、`release_url`、`scope=gtfs`、`preserved`。
- `POST /api/updates/dictionary/apply`，JSON：`{"latest_version":"gtfs-20261004-052300"}`。重新核对发布版本，下载、验证、导入并提交，返回 `component=dictionary`、`status=updated|unchanged`、`current`；更新时另有`updated_tables`。

写入接口仅允许回环地址请求，拒绝跨站来源；适用于Android本机后端。远端服务器词典维护由服务器管理员执行，不开放匿名远程写入。`release_changed`为409，`busy`为409，下载超时504，其他上游/数据错误502，均带 `detail={code,message}`。

## 数据与自动合并

独立更新源：`wensimehrp/chinese-railway-gtfs`，正式标签`gtfs-YYYYMMDD-HHMMSS`，资产`output_gtfs.zip`。检查来源、大小及GitHub SHA-256；压缩包上限32MiB、解压声明总量上限256MiB，读取CSV不提取任意路径。完整性和车站/车次引用验证通过后，一次SQLite事务更新`g_stop/g_trip/g_stop_time`及GTFS版本元数据。

保留`line_master`、`line_station`、`station_profile`和其他元数据；失败事务回滚。更新前保留相邻的`dict.update-backup.db`快照。取消下载清理残片；取消本地解析/提交会先完成或回滚该短阶段再清理文件，因此提交期间取消不保证旧版本未变，客户端应重新读取`/local`确认。

软件更新中的词典合并是另一入口：启动时读取`DICT_BUNDLED_DB_PATH`，与`DICT_DB_PATH`运行库比较。相对路径按backend目录解析，Android推荐绝对路径。只导入更晚GTFS；若包内线路汇总采样日期更晚，也更新`line_master`。本地独立更新较新时保留它，不因重启或安装旧包降级。逐站线路、车站档案缓存始终保留。

单独更新目前刷新**GTFS离线站序、时刻、坐标和里程**。12306站名资源、2022旧车次目录仍随软件资源管理；个人网站线路汇总及档案不属于该GitHub更新包，不能显示为全部已刷新。既有时刻工具查询缓存最多复用30秒；词典本地读连接可直接读取已提交的新数据。

## 前端负责与已验证范围

- 独立“词典更新”入口，展示数据版本和采样日期，不能用软件版本当作词典版本。
- 检查、更新、失败分别显示状态；仅`updated`/`unchanged`响应后重新读取本地版本。离线或GitHub访问失败不表示已最新。
- 软件更新须打包词典并在启动前解包至独立路径；具体步骤见[软件更新交接](backend-software-update-handoff-20261006.md)。当前Android这部分尚未接入。

14项新增离线测试通过，覆盖独立检查、下载完整性、跨站拒绝、部分文件清理、事务回滚、取消排空、跨连接写锁与版本比较、包内新数据合并及防降级。

真实匿名验证：下载1,940,400字节GTFS包，SHA-256匹配；在临时库成功导入`gtfs-20261004-052300`，5,410站、19,407车次（含占位）、161,494停站记录。**当前共享词典未被更新**，原库保持不变；此为后端下载/导入验证，不是Android运行验收。

证据：`.ai/github-updates/2026-10-06-v1/`。未使用个人GitHub token，未修改前端、安装包或GitHub发布。

应用到当前项目后，14组既有后端回归通过。词典套件的虚构站名网页查询使用固定空页面夹具，其首次联网尝试已被离线守卫阻断并保留日志；真实下载证明另行归档，不把夹具视作真实网络成功。
