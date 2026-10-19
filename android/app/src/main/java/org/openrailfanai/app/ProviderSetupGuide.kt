package org.openrailfanai.app

/** Official entry points and provider-specific wording; connection details stay in the backend catalog. */
internal data class ProviderSetupGuide(
    val id: String,
    val name: String,
    val accountUrl: String,
    val accessUrl: String,
    val accessLabel: String,
    val accessInstructions: String,
    val keyUrl: String,
    val keyLabel: String,
    val keyInstructions: String,
    val creditInstructions: String,
    val authUrl: String? = null,
    val authInstructions: String? = null,
    val note: String = "",
) {
    val steps: List<ProviderSetupStep> get() = ProviderSetupStep.entries.filter { it != ProviderSetupStep.AUTH || authUrl != null }
}

internal fun providerSetupGuides(): List<ProviderSetupGuide> =
    listOf("siliconflow", "deepseek", "dashscope", "zhipu", "moonshot", "openai", "openrouter", "gemini").mapNotNull(::providerSetupGuide)

/** Links not verified behind login point to official instructions or the provider console. */
internal fun providerSetupGuide(id: String): ProviderSetupGuide? = when (id) {
    "siliconflow" -> ProviderSetupGuide(id, "硅基流动", "https://cloud.siliconflow.cn/",
        "https://cloud.siliconflow.cn/expensebill", "查看额度 / 充值", "先检查可用额度；需要时自行选择金额和支付方式，返回官网确认到账。",
        "https://cloud.siliconflow.cn/me/account/ak", "打开密钥管理", "在密钥管理中新建密钥，复制完整内容。",
        "已具备可用额度即可继续；无需为向导开通自动充值。",
        "https://cloud.siliconflow.cn/account/authentication", "按官网提示完成实名认证；确认官网显示已完成后返回。")
    "deepseek" -> ProviderSetupGuide(id, "DeepSeek", "https://platform.deepseek.com/",
        "https://platform.deepseek.com/", "查看开放平台余额", "在开放平台检查 API 账户余额；如需充值，按官网账户页面操作。",
        "https://platform.deepseek.com/api_keys", "打开密钥管理", "在开放平台的 API Keys 页面创建并复制密钥。",
        "这是 API 开放平台账户；聊天产品会员不等于 API 余额。")
    "dashscope" -> ProviderSetupGuide(id, "阿里云百炼", "https://bailian.console.aliyun.com/",
        "https://help.aliyun.com/zh/model-studio/first-api-call-to-qwen", "查看服务开通说明", "按官网提示开通模型调用服务，确认计费账户可用。",
        "https://help.aliyun.com/zh/model-studio/get-api-key/", "查看密钥与地域说明", "在百炼控制台选定地域，创建该地域及对应业务空间的 API Key。",
        "确认普通按量调用的计费方式；订阅套餐专用 Key 与当前预设接口可能不同。",
        note = "当前预设对应的接入地域须与 API Key 地域一致；若不同，请返回普通编辑流程确认地址。")
    "zhipu" -> ProviderSetupGuide(id, "智谱开放平台", "https://open.bigmodel.cn/",
        "https://open.bigmodel.cn/", "查看开放平台账户", "在开放平台查看普通 API 调用额度与计费状态，按官网提示开通。",
        "https://docs.bigmodel.cn/cn/guide/start/quick-start", "查看官方密钥说明", "按照官方快速开始，在开放平台创建并复制 API Key。",
        "确认选用的模型有权限及可用额度，以官网账户显示为准。")
    "openai" -> ProviderSetupGuide(id, "OpenAI API", "https://platform.openai.com/",
        "https://platform.openai.com/", "查看 API 平台计费", "在 API 平台确认组织、项目与计费设置；按官网提示配置付款方式。",
        "https://platform.openai.com/api-keys", "打开 API 密钥页面", "在正确的项目中创建并复制 API Key。",
        "请确认 API 服务在你的账户与所在地可用；ChatGPT 订阅不自动提供 API 额度。")
    "moonshot" -> ProviderSetupGuide(id, "Kimi 开放平台", "https://platform.kimi.com/",
        "https://platform.kimi.com/", "查看开放平台额度", "在开放平台核对 API 账户的可用额度，按官网提示处理计费。",
        "https://platform.kimi.com/console/api-keys", "打开密钥管理", "在开放平台的 API Keys 页面创建并复制密钥。",
        "Kimi 聊天账户的权益与开放平台 API 调用额度请分别核对。")
    "openrouter" -> ProviderSetupGuide(id, "OpenRouter", "https://openrouter.ai/",
        "https://openrouter.ai/", "查看账户额度", "在 OpenRouter 账户中核对可用 credits 与所选模型的计费要求。",
        "https://openrouter.ai/keys", "打开密钥管理", "创建 OpenRouter API Key，复制完整密钥。",
        "不同模型及提供商的价格和可用性不同，以官网当前页面为准。")
    "gemini" -> ProviderSetupGuide(id, "Gemini API", "https://aistudio.google.com/",
        "https://ai.google.dev/gemini-api/docs/billing/?hl=zh-cn", "查看项目及计费说明", "先确认所在地区可用；在 AI Studio 检查项目、配额和是否需要关联计费。",
        "https://ai.google.dev/gemini-api/docs/api-key?hl=zh-cn", "查看官方密钥说明", "在 AI Studio 的 API Keys 页面为可用项目创建或取得密钥。",
        "免费额度与付费条件随模型、项目和地区变化；按项目的实际状态决定。")
    else -> null
}
