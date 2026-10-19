# Main 0.1.29

本次整合 0.1.26 以来的配置向导、图片文档输入、票务取数优化与车次规则迁移准备。

## FUCK_CR：字母升官，代码加班

China Railway has yet to officially roll out its upcoming train numbering scheme, but why wait for administrative innovation to become reality before suffering its consequences? This release introduces a prebuilt, versioned parser and a one-click compatibility entry, preparing for the new rules while retaining current production behavior and historical interpretations. FUCK_CR has reported for duty; activation is still waiting for validated business rules and data sources. We sincerely thank CR for reminding us that a working system is merely a system that hasn't been improved yet.

**The switch is not ready to activate.** The current backend reports `can_enable=false`; the button remains disabled. Shadow observation still uses existing business rules. The new parser is preparation, not an announcement that upcoming train numbers or classifications are supported. No automatic date-based activation is included.

当前按钮如实显示“适配准备中”，点击说明可见讽刺提示。未知或未来车次格式尚不能作为当前查询能力使用；新版业务语义、逐趟映射和数据源准入仍待验证。

## 其他更新

- 配置向导扩展至硅基流动、DeepSeek、百炼、智谱、Kimi、OpenAI、OpenRouter、Gemini；各平台的步骤、密钥草稿及记住选项独立保存。连接验证成功后才启用配置。
- 官网步骤及对话中的数据来源链接使用 Custom Tabs，支持返回向导和普通浏览器回退；修正硅基流动中文参数与密钥管理入口。
- 输入栏提供图片、文档导入，展示文件名、大小和移除操作；保留未发送问题，点击发送后才处理。每次一份，最大 8 MiB。
- 支持 TXT、MD、TEX、CSV、JSON、DOC、DOCX、RTF 与 PNG、JPEG、WebP、GIF。文档提取正文与表格文字；不宣称保留原版式或支持 PDF、Excel 二进制。
- 模型图片能力默认关闭，支持时可主动开启。关闭时在设备上 OCR，只向模型发送文字；明确的图片能力拒绝最多回退一次，认证或服务故障不冒充能力问题。
- Main 捆绑中文 OCR 模型，无需首次联网下载。安装包约 56 MB，较上一公开版本增大；包含离线词典。
- 同一检索内、同日期和区间的票价/余票查询共享原始售票记录，减少重复取数；执行票价仍按车次和区间独立获取，低余量核对强制重新采样。
- 纳入确定性知识分流、站名匹配及提示缓存用量观测。缓存收益未作固定速度承诺。
- 同步源代码、第三方 OCR 许可、测试素材与最新验收证据；历史运行缓存和旧包保留本地，不进入 Git。

## 验证范围

17 项 Android 定向检查通过，0 失败、0 跳过，覆盖新控件、图片/OCR 分流、兼容状态及密钥隔离；默认组件布局对照已审阅。后端各交接文档保留其专项验证范围。

完整项目回归尚未全部通过，独立评审记录了环境相关失败、测试假设问题及旧解析器对未知字头的边界缺陷；本版本没有将其包装成“完整改革兼容”。尚未逐家验证真实注册付款，也未评测真实云端模型的视觉准确率。

最终发布从干净的 Git 提交重新构建，版本、签名、包内词典、安装运行及 GitHub 资产校验记录见 `docs/verification/release-0.1.29.json`（发布后生成）。
