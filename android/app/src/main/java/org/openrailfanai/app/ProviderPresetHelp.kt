package org.openrailfanai.app

/** Presentation guidance only. The backend catalog remains the source of connection settings. */
internal data class ProviderPresetHelp(val description: String, val guideUrl: String, val modelHint: String? = null)
internal fun providerPresetHelp(id: String): ProviderPresetHelp? = when (id) {
    "deepseek" -> ProviderPresetHelp("DeepSeek 官方模型服务，使用开放平台账户。", "https://api-docs.deepseek.com/zh-cn/")
    "siliconflow" -> ProviderPresetHelp("一个账户可选择多家模型，平台密钥用于连接硅基流动。", "https://docs.siliconflow.cn/docs/userguide/quickstart", "deepseek-ai/DeepSeek-V4-Flash")
    "dashscope" -> ProviderPresetHelp("阿里云百炼模型服务；密钥和服务地域需要对应。", "https://help.aliyun.com/zh/model-studio/get-api-key")
    "zhipu" -> ProviderPresetHelp("智谱开放平台的 GLM 模型服务。", "https://docs.bigmodel.cn/cn/guide/start/quick-start")
    "openai" -> ProviderPresetHelp("OpenAI 模型接口；使用 API 平台账户。", "https://platform.openai.com/")
    "moonshot" -> ProviderPresetHelp("月之暗面的 Kimi 模型接口；使用开放平台账户。", "https://platform.moonshot.cn/")
    "openrouter" -> ProviderPresetHelp("汇集多家模型的服务，模型与费用由 OpenRouter 提供。", "https://openrouter.ai/")
    "gemini" -> ProviderPresetHelp("Google Gemini 模型接口；需确认账户与所在地可用。", "https://ai.google.dev/gemini-api/docs/api-key")
    else -> null
}

internal fun pickPresetModel(config: ProviderConfig, available: List<String>): String? {
    val models = available.filter(String::isNotBlank).distinct()
    val hint = providerPresetHelp(config)?.modelHint
    return listOfNotNull(config.model.takeIf(String::isNotBlank), hint).firstOrNull { it in models }
        ?: models.singleOrNull()
}

internal class PresetModelSelectionRequired(val available: List<String>) : Exception("请选择一个可用的对话模型")

internal fun providerPresetHelp(config: ProviderConfig): ProviderPresetHelp? {
    if (config.custom) return null
    val expected = mapOf("deepseek" to "api.deepseek.com", "siliconflow" to "api.siliconflow.cn", "dashscope" to "dashscope.aliyuncs.com", "zhipu" to "open.bigmodel.cn", "openai" to "api.openai.com", "moonshot" to "api.moonshot.cn", "openrouter" to "openrouter.ai", "gemini" to "generativelanguage.googleapis.com")
    val host = runCatching { java.net.URI(config.baseUrl).host }.getOrNull()
    if (host != expected[config.id]) return null
    return providerPresetHelp(config.id)
}
