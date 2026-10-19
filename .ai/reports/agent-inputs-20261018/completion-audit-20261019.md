# 图片与文档输入完成核验

日期：2026-10-19。范围沿用用户原目标：图片、文本类文档/TEX/MD、DOC/DOCX/RTF输入，模型输入类型设置，多模态原图与本地OCR分流，前端交接。未用单元测试推断视觉准确率或完整产品验收。

| 要求 | 当前证据 | 结论 |
|---|---|---|
| 支持文本类文档、TEX、MD | `agent_documents.py`真实读取，`test_agent_documents.py`及API路由测试；纯标准库，无TeX执行 | 已实现并验证 |
| 支持DOC、DOCX、RTF | 真实Office容器夹具；后端测试；最终Android集成测试在Chaquopy上实际提取三种文档 | 已实现并验证本轮正文范围；旧Word/加密/嵌入图片限制见交接 |
| 支持图片输入 | Android与Web选择器接线；PNG/JPEG/WebP/GIF真实容器测试；Web选择器实际导入截图及操作记录 | 已实现并验证 |
| 多模态模型使用原生图片输入 | `chat_structured(images=...)`；9项真实SDK→MockTransport协议测试覆盖Chat Completions/Responses与对话模型选择；Android最终集成测试捕获原图请求 | 已实现；不宣称实测所有云端供应商视觉效果 |
| 配置中提供文字/图片输入选项 | Main设置源代码；Web设置实际保存刷新截图；Android提供商切换/重载集成测试，模型/地址更改重置的JVM测试 | 已实现并验证；文字为必需项，图片默认关闭 |
| 关闭图片或不支持图片时本地OCR | Android捆绑ML Kit中文模型，最终9项测试含中文识别和请求体捕获；本机Tesseract中文测试；API开启/关闭/unsupported测试 | 已实现并验证；Web OCR运行在后端主机，交接已明确 |
| 不误将鉴权/限流当能力不足 | SDK分类测试与后端API测试；Android401不回退集成测试 | 已验证 |
| 原始附件不落聊天存储、取消和历史保护 | `ConversationStore`与客户端接线；Android空识别/取消/持久化标记测试；Web重生成实际提示 | 已验证当前一次性附件契约 |
| 更新Prompt | 专用资料Prompt声明图片、OCR错误、Office正文提取和不执行资料指令；Main普通问答Prompt说明入口 | 已实现，原有及新增Prompt测试通过 |
| 提供前端交接 | `docs/backend-image-document-input-handoff-20261018.md`，含请求/响应、错误、分流、限制、客户端入口与证据位置 | 已交付 |

最终检查：后端资料相关56项、图片SDK协议9项、Web模块23项通过；Android最终构建成功、JVM16项通过、独立只读模拟器9项集成测试通过。实际运行时读回Android15/API35、Wi-Fi与移动数据关闭、无默认网络。最新原始输出、命令、APK/源码哈希在 `android/run-final-20261019.json`，网络读回在 `android/network-runtime-final-20261019.json`。旧轮原始记录保留，不使用10月9日旧XML。

独立测试模拟器已关闭；本任务创建的Web模型替身服务已停止。未恢复或覆盖并行设置向导重命名，最终按当前 `ProviderSetupWizard` 编译通过。未改版本或发布release。

范围之外且不作完成声明：真实云端模型视觉质量评测、实拍图片集准确率、完整原生视觉验收、PDF/Excel二进制、扫描Office内嵌图片、Office版式还原、自动实时票务查询。
