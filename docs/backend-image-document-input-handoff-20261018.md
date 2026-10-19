# 图片与文档输入交接（2026-10-18）

本轮扩展上一版资料工具，已接 Main Android 与 WebUI：图片可以走模型原生输入或本地 OCR；文档先离线提取正文，再交给当前配置的模型。普通铁路聊天接口与结构化结果协议保持原状，未更新版本或 release。

## 已落地的入口

- 对话输入“导入文件”或“导入图片”，打开系统/浏览器选择器。选好后填入可编辑提问，用户发送后才处理。
- 模型配置增加输入类型“文字”“图片”。文字为必需项；图片默认关闭，开启表示用户确认模型支持并允许原图输入。旧配置自动按仅文字处理。
- 输入类型按提供商配置保存与切换；更换模型或接口地址后重新关闭图片声明，避免沿用上一个模型的能力。只修改密钥不改变能力声明。
- 本轮复用现有输入框和设置控件，未新增完整附件管理页面。Android 与 Web 的布尔项统一使用可点击、可拖动的滑动开关，不使用打勾框。

## 支持范围

| 输入 | 当前处理 |
|---|---|
| TXT / MD / TEX / CSV / JSON | 读取文本；UTF-8，可带 BOM；也支持带 BOM 的 UTF-16。TEX 不编译、不执行宏 |
| DOCX | 提取正文段落和表格文字，不保留页面版式 |
| DOC | 支持未加密 Word 97–2003 CFB / CLX 文档；更早的 Word 二进制需另存 DOCX/RTF |
| RTF | 提取正文、Unicode 及常用中文/西文编码文字，不执行字段指令或嵌入对象 |
| PNG / JPEG / WebP / GIF | 图片模型直接识别，或本地 OCR 提取文字；Android GIF 识别首帧 |

单附件最多 **8 MiB**。客户端每次选择一个附件；接口保留最多两份附件的能力。图片最多2400万像素；后端单轴上限12000。单份提取文字最多60000字符，资料与最近20条历史合计最多80000字符，超限拒绝而非静默截断。输出文件仍限1 MiB，沿用 CSV/MD/JSON/ICS 草稿契约。

Office 内嵌图片、扫描页、页眉页脚、脚注和页面布局不属于当前正文提取范围；扫描内容请另行导入图片。没有新增 PDF、Excel 二进制、OCR版面还原或TeX渲染。

## 图片分流

| 配置/结果 | 路径 |
|---|---|
| 开启图片，模型接受图片 | 原图以当前 user 消息的图片块输入模型；不先 OCR |
| 未开启图片 | Android 先在设备上 OCR，只发送 UTF-8 文字；Web 使用后端主机本地 Tesseract OCR，只将文字发送给模型 |
| 已开启，但上游明确拒绝图片能力 | 只回退一次 OCR 文字请求。后端本地 OCR 可用时直接完成；不可用时返回 `needs_local_ocr`，Android 改走设备 OCR |
| 认证、额度、限流或服务故障 | 保持错误，不冒充“不支持图片”，不触发 OCR 降级 |
| 识别为空、损坏或超限 | 明确失败，保留待处理附件供重试，不编造内容 |

模型目录未提供可靠统一的模态元数据，因此本版采用**用户能力声明 + 明确错误回退**，不按模型名称猜测能力，也不把开启开关当成已经测过该模型。两种自然输入协议均已接入：Chat Completions 的 `image_url` 与 Responses 的 `input_image`。图片使用当前对话模型，避免误用单独配置的纯文本分类模型。

Android 使用捆绑模型的 ML Kit 中文识别组件 `com.google.mlkit:text-recognition-chinese:16.0.1`，不依赖首次启动联网下载模型。此依赖仅打入 Main，LM 运行包不新增该模型。选择器使用系统文档访问机制，未增加相册或全盘读取权限。

Web 的“本地”指 **后端运行主机**，不是浏览器内 OCR。后端部署在远端时，附件会先上传该后端；关闭图片表示不向模型供应商上传原图，并不表示不向后端上传。桌面后端需安装 Tesseract，可通过 `RAILFAN_TESSERACT` 指定路径。中英文语言数据已在 `backend/resources/ocr/` 固定版本、许可及哈希，无运行时下载。缺组件时返回可处理的 OCR 不可用结果，不假装识别成功。

## 接口增量

沿用 `POST /api/agent/run`，保留既有 message/history/actions/files/BYOK 字段，新增可选 `input_types`：

```json
{
  "message": "请阅读这张图片，提取主要信息",
  "actions": ["files"],
  "input_types": ["text", "image"],
  "files": [{
    "name": "photo.png",
    "mime": "image/png",
    "content_base64": "…"
  }]
}
```

- `input_types` 缺省为 `["text"]`，允许 `["text","image"]`，不能关闭文字或传未知类型。
- `mime` 可选，图片若提供 MIME 必须与真实头部类型一致。文件名不能包含路径。
- 客户端 OCR 后应发送 `name: "photo-ocr.txt"`、`mime: "text/plain"`、`source: "local_ocr"` 和识别文字的 base64，不能仍发送原图片字节。`source` 是资料来源提示，不是可信或已核实的标记。
- 正常返回保持 `handled/answer/files/usage`，新增 `input_processing` 数组：每项含 `name` 和 `mode`，取 `document_text`、`model_image`、`local_ocr`。
- 需要客户端 OCR 时返回 HTTP 200、`needs_local_ocr:true`、简短 `answer`、`files:[]`。客户端必须处理该分支，不能显示成“资料已读完”。
- OCR 重试仍使用同一用户问题与历史，只将图片换成识别文字、`input_types` 换为 `["text"]`。不循环重试，不换用其他会话附件。
- 非法内容/参数为422，模型不可用为503；错误不包含供应商原始报文或 Key。

同名图片按稳定索引绑定，回退时不会相互覆盖。附件、图片和历史中的指令都视为资料，不执行其中的脚本、宏、链接或角色指令。专用 Prompt 已说明 OCR 错误可能性、看不清时不得猜数值、文档不保证版式，以及不得伪称已保存文件或已写入日历；普通问答 Prompt 也补充了导入入口。

## 客户端状态与后续接手

待处理附件按会话隔离，只保存在内存。成功后释放，失败保留；输入“移除附件”可清除。附件字节不写入聊天存储，不作为持久化聊天附件提供再次下载。

附件回复的“重新生成”不能拿会话里新导入的另一份附件替代原件。当前会提示重新导入后重新发送问题；重启后同样保护。Android 因既有回调只传原问题文字，遇到同文附件历史时采用保守拦截。Web 的回复标题使用“资料处理”，不向用户显示内部 `file_tool` 名称。

资料工具负责基于附件回答、整理和导出；不自动把图片中的车次转成实时余票/票价查询。需要实时数据时仍走现有铁路查询入口，不将 OCR 或模型记忆当成实时事实。

前端如继续完善入口，可在现有输入区提供同一选择器的可见按钮，并展示文件名、大小、移除操作；无需重做后端契约。不得将整幅图片或文档正文写入会话 JSON，不把 OCR 输出标为已核实事实。

## 验证与证据

- 后端 `test_agent*.py` **56项通过**：含真实 DOC/DOCX/RTF/TEX 输入、实际中文 Tesseract OCR、图片开关、同名绑定、错误分类、回退及限额；模型回复用固定替身，不花费真实 API 额度。
- 双协议图片 SDK 测试 **9项通过**：实际 SDK 请求经过本地 MockTransport，核验图片块、模型选择、schema、重试、用量及纯文本兼容。既有供应商27组、连接池9组回归通过。
- Web 模块新增/累计23项检查通过，原有存储47项与原生桥存储13项通过。另在真实 WebUI 完成输入选项保存/刷新、图片选择导入、资料回复和附件重生成保护；使用固定模型替身，OCR实际在本机运行。
- Android Kotlin/JVM 检查与**16项单元测试通过**。独立模拟器 `emulator-5588` 上核心 **9项集成测试通过**，含中文 ML Kit OCR、Chaquopy 读取真实 Office 文件、两条分流、能力回退、认证不回退、取消及附件历史保护。最终代码已于2026-10-19重跑通过；运行时读回 Android 15 / API35、Wi-Fi关闭、移动数据关闭及无默认网络，原始记录见 `android/run-final-20261019.json` 和 `network-runtime-final-20261019.json`。
- 未做真实云端模型视觉准确率评测、用户实拍图片集评测或完整原生视觉验收；不以协议/夹具测试代替这些结论。未发布新版安装包或 release。

证据目录：`.ai/reports/agent-inputs-20261018/`，Android 原始输出和命令在 `android/`；Web 滑块版配置截图为 `web-input-sliders-20261019.jpg`（先前打勾版截图仅为历史记录，已纠正）。旧的10月9日 Android 测试 XML 与本次无关，不作为本次证据。

## 文件入口

- 后端：`backend/app/agent.py`、`agent_documents.py`、`agent_images.py`、`api/agent.py`、`llm/client.py`、`pipeline/generate.py`。
- Android：`ImageTextRecognizer.kt`、`AppAgent.kt`、`ChatRepository.kt`、`MainComposeActivity.kt`、`MainSettingsRepository.kt`、`MainSettingsScreen.kt`、`ConversationStore.kt`、`ProviderSetupWizard.kt`，以及 Main 的 ML Kit 依赖。
- Web：`frontend/src/agent.js`、`main.js`、`pages.js`。
- 测试：`test_agent_documents.py`、`test_agent_images.py`、`test_agent_input_routes.py`、`test_llm_image_transport.py`、`frontend/tests/agent.test.mjs`、Android `AppAgentTest` 与 `AgentInputIntegrationTest`。

组件资料：[ML Kit Android](https://developers.google.com/ml-kit/vision/text-recognition/v2/android)、[Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html)。

## 2026-10-19 增补：“一键FUCK_CR”预备按钮

前端按钮另有可直接预接的 `GET /api/fuck-cr/status` 和 `POST /api/fuck-cr/activation`。当前 `can_enable=false`，新版未就绪，开启返回409，不执行模式热切换；不得显示为已经兼容新规则。仅 Main 暴露，LM没有此入口。本轮只提供后端预备接口，没有添加客户端按钮。

完整字段、示例、错误处理与按钮接线见 [“一键FUCK_CR”交接文档](backend-fuck-cr-button-handoff-20261019.md)。原有资料输入与滑动开关约定不变。
