# “一键FUCK_CR”按钮：后端预备接口

日期：2026-10-19。状态：接口已实现，新版车次业务规则仍未就绪。供 Main Android / Web 前端预接按钮；本轮不修改客户端、版本或 release。

字母可以先升官，开关不能先报喜。此接口只如实报告准备状态，不把影子观察包装成已经修好的新版分流。

## 1. 查询状态

`GET /api/fuck-cr/status`

```json
{
  "schema_version": 1,
  "feature": "fuck_cr",
  "button_label": "一键FUCK_CR",
  "mode": "legacy",
  "enabled": false,
  "new_rules_ready": false,
  "can_enable": false,
  "message": "新版车次兼容规则尚未就绪。"
}
```

`mode` 是后端进程当前生效的既有模式，当前可为 `legacy` 或 `shadow`。`enabled` 专指**新版业务规则**是否启用；`shadow` 仍运行旧业务规则，因此同样返回 `enabled:false`。`new_rules_ready` 表示新版业务适配是否完成，`can_enable` 是前端是否可执行启用动作的依据。本版两者均为 false。

不要根据日期、按钮是否已显示、解析器是否识别 X/V/DH 等格式，或用户本机保存的偏好，推断新版已启用。

## 2. 按钮动作

`POST /api/fuck-cr/activation`

```json
{"enabled": true}
```

本版明确返回 **HTTP 409**：

```json
{
  "detail": {
    "code": "fuck_cr_not_ready",
    "message": "新版车次兼容规则尚未就绪。",
    "status": {
      "schema_version": 1,
      "feature": "fuck_cr",
      "button_label": "一键FUCK_CR",
      "mode": "legacy",
      "enabled": false,
      "new_rules_ready": false,
      "can_enable": false,
      "message": "新版车次兼容规则尚未就绪。"
    }
  }
}
```

如果当前是 `shadow`，响应中的 `mode` 为 `shadow`，其他就绪与启用状态不变。409不是网络故障，不自动重试，也不能将按钮标成已成功开启。

提交 `{"enabled":false}` 返回 **HTTP 200**，表示新版原本就未启用的幂等确认：

```json
{
  "requested_enabled": false,
  "changed": false,
  "status": {
    "schema_version": 1,
    "feature": "fuck_cr",
    "button_label": "一键FUCK_CR",
    "mode": "legacy",
    "enabled": false,
    "new_rules_ready": false,
    "can_enable": false,
    "message": "新版车次兼容规则尚未就绪。"
  }
}
```

关闭请求不会顺带关闭影子观察；当前为shadow时，返回的 `status.mode` 仍为 `shadow`。

`enabled` 必须是真正的 JSON boolean，字符串/数字、缺字段、额外字段均返回422。没有提供任意模式切换、改写 `.env`、重启服务、改词典或下载规则的后门。

## 3. 前端接线约定

1. 页面进入或后端连接恢复时读取状态；当前显示“一键FUCK_CR”，根据 `can_enable:false` 禁用启用动作，可配简短“适配准备中”。详细原因按既有约定点击展开，避免直接铺满设置页或结果卡片。
2. 只有后端明确允许启用才发送开启请求。点击期间禁止重复提交，不先乐观写入 `enabled:true`。
3. 成功、409或网络异常后，均以服务器返回的完整状态或重新 GET 的状态为准；网络失败保留最后确认的状态，不伪造开启或关闭成功。
4. 老后端返回404时按“不支持该入口”处理，不以“默认允许开启”兜底。LM 不挂载此接口，前端不向 LM 暴露该按钮。
5. “一键FUCK_CR”是用户明确命名的操作按钮。设置中如另展示布尔开关，仍遵守滑动开关规范，不新增打勾框。
6. 当前无需给 `/api/chat`、`/api/chat/stream` 增加字段，也不改变 SSE 或结果卡片。客户端本地设置不能改变后端真实规则版本。

这是一份**预备调用契约**，不是已经开放的热切换接口。后续新版完整业务规则与供应商验证完成后，才能另行实现真正的启用/回退、配置持久化及多用户隔离；当前这些写入行为均未开放。

## 4. 现有运维开关保持原样

`FUCK_CR_MODE=legacy|shadow` 仍由后端环境配置并重启生效；`reform_2026` 仍被启动校验明确拒绝。新接口不改变 Settings 缓存、查询缓存键或其他用户的模式，不请求铁路或模型 API。

代码：`backend/app/api/fuck_cr.py`；仅 Main 在 `backend/app/main.py` 挂载。测试：`backend/tests/test_fuck_cr_api.py`，并加入既有测试入口。

验证覆盖 legacy/shadow 状态、开启拒绝后的读回不变、关闭幂等、不误关闭shadow、严格请求校验及原聊天路径保留。新旧规则现有回归另行复跑；不把接口测试称为改革业务验收。

本轮结果：新增接口测试5项、既有模式边界7项、语法解析10项，共22项通过；独立进程确认LM不挂载这两个路径；`git diff --check`通过。
