# 多平台配置向导接入

后续验证更新：同日图片文档入口接入批次已补齐主代码、测试源码编译及配置仓库 6 项模拟器检查，全部通过。原审批阻塞已解除；各平台真实账户流程仍未逐家验证。详见 `frontend-inputs-fuck-cr-integration-20261019.md`。

日期：2026-10-19。范围：Main Android 配置向导。

## 本次实现

设置页的“配置向导”先展示服务商选择层，包括硅基流动、DeepSeek、阿里云百炼、智谱、Kimi、OpenAI、OpenRouter 和 Gemini。各预设编辑区也有直接进入对应向导的入口。目录暂未加载时可以先阅读、打开官网；连接前仍要求取得官方连接预设。

共用 `ProviderSetupWizard.kt` 与不引用 Android 的 `ProviderSetupProgress.kt`。各服务商的账户、计费、密钥入口及说明集中在 `ProviderSetupGuide.kt`。硅基流动保留认证步骤；其他平台按账户或官网提示处理认证与开通，不强制套用硅基流动的实名、充值流程。

向导链接使用 Custom Tabs，关闭后返回当前步骤；不支持时回退普通浏览器。硅基流动继续声明中文参数，Google 官方说明链接指定中文语言。浏览器跳转仍不代表注册、开通或付款已完成，用户确认后才推进步骤。

连接参数和默认模型继续来自后端目录。新增读取账户模型的入口；预设模型不可用时可在向导中更换。实际连接测试通过、配置保存成功后才显示完成，并返回原对话。

## 凭据与兼容

向导步骤、模型选择和记住选项按服务商分别存储；密钥草稿使用独立的加密或当前进程存储。退出一个向导、完成一个平台不会清除其他平台的草稿或账户。普通设置保存也保留所有向导草稿。

旧 `railfan_silicon_setup_v1` 进度及旧硅基流动草稿兼容读取，下一次保存迁移到按服务商命名的空间。向导保存只更新选定服务商，保留其他账户的密钥及各自记住选项。

## 官网素材与证据边界

- DeepSeek：官方首次调用说明链接到 `https://platform.deepseek.com/api_keys`。直接抓取仍返回 403；已核对官方链接目标，未验证登录后的移动页面。
- 百炼：使用官方首次调用与 API Key/地域说明，提供开通、业务空间和地域提示。没有自动更改现有接入地域。
- 智谱：使用既有官方控制台与快速开始地址。本轮公共文档抓取超时，登录后路径尚待实测；不虚构内部菜单直达地址。
- Kimi：原 Moonshot 快速开始重定向到 `https://platform.kimi.com/docs/get-api-key`，其中官方密钥链接为 `https://platform.kimi.com/console/api-keys`。模型 API 参数仍读取既有后端预设。
- OpenAI：使用 API 平台及其密钥页面；计费由用户在 API 平台确认。
- OpenRouter：使用官网与密钥管理入口，模型价格及额度按官网实际显示确认。
- Gemini：使用 AI Studio 和官方密钥、计费说明，提示地区、项目及配额差异，不承诺固定免费额度。

公开资料不能证明普通用户已完成“零搜索”配置。各平台的真实登录、账户计费、密钥权限、手机回跳仍需分别验收。

官方资料：

- [DeepSeek 首次调用](https://api-docs.deepseek.com/zh-cn/)
- [百炼首次调用](https://help.aliyun.com/zh/model-studio/first-api-call-to-qwen)
- [百炼 API Key](https://help.aliyun.com/zh/model-studio/get-api-key/)
- [智谱快速开始](https://docs.bigmodel.cn/cn/guide/start/quick-start)
- [Kimi 密钥与首次调用](https://platform.kimi.com/docs/get-api-key)
- [OpenAI API 密钥](https://platform.openai.com/api-keys)
- [OpenRouter 快速开始](https://openrouter.ai/docs/quickstart)
- [Gemini API 密钥](https://ai.google.dev/gemini-api/docs/api-key?hl=zh-cn)
- [Gemini 计费](https://ai.google.dev/gemini-api/docs/billing/?hl=zh-cn)

## 验证状态

通用向导、选择入口和隔离仓库的第一轮 `compileDebugKotlin` 通过。随后增加模型读取入口及配置仓库的三个定向回归用例：分别覆盖多平台草稿隔离、保存其他账户与加密草稿、旧硅基流动进度迁移。

最终源码与测试复编、定向模拟器运行尚未执行：自动审批审查因所用模型容量不足拒绝启动，提示这是审批服务故障，并非安全性判定。没有绕过审查或宣称这些用例通过。未做真实账户连接、注册、充值，也未执行正式 UI 验收或生成验收图片。

后续执行：先完成最终编译及配置仓库定向模拟器检查，再检查向导选择、暂停恢复、官方窗口返回、密钥不落盘和完成返回对话。按正式验收脚本生成证据后再记录验收通过。

本次未递增版本、打包或上传；后端协议无需配套变动。
