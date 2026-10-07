package org.openrailfanai.app

/** Classify recovery actions by evidence, not merely by mention of a credential. */
internal fun mainErrorCategory(message: String): String = when {
    Regex("ZoneInfoNotFoundError|服务内部异常|internal server error", RegexOption.IGNORE_CASE).containsMatchIn(message) -> "service"
    Regex("未配置|尚未配置|未填写|没有.*(?:密钥|API.?Key)|missing.*(?:key|config)|not configured", RegexOption.IGNORE_CASE).containsMatchIn(message) -> "configuration"
    Regex("base_url|非公网|地址被拒绝|拒绝.*地址", RegexOption.IGNORE_CASE).containsMatchIn(message) -> "configuration"
    Regex("HTTP\\s*401|\\b401\\b|invalid[_ ](?:api[_ ])?key|incorrect.*api.*key|API.?Key.{0,4}无效|密钥无效|无效.*密钥|认证失败|鉴权失败|unauthorized", RegexOption.IGNORE_CASE).containsMatchIn(message) -> "auth"
    Regex("连接|网络|超时|DNS|timeout|connection", RegexOption.IGNORE_CASE).containsMatchIn(message) -> "network"
    else -> "service"
}

internal fun mainErrorTitle(message: String): String = when (mainErrorCategory(message)) {
    "configuration" -> "请检查云端模型配置"
    "auth" -> "模型认证失败，请检查 API Key"
    "network" -> "网络连接失败，请稍后重试"
    else -> "查询服务暂时异常"
}

internal fun providerFailureCopy(message: String): String = when {
    Regex("model.{0,30}(?:not found|does not exist)|模型.{0,10}(?:不存在|不可用)", RegexOption.IGNORE_CASE).containsMatchIn(message) -> "模型不存在或暂不可用，请检查模型名称。"
    else -> when (mainErrorCategory(message)) {
        "auth" -> "API Key 认证失败，请检查后重试。"
        "configuration" -> "请检查提供商配置与接口地址。"
        "network" -> "网络连接失败或超时，请稍后重试。"
        else -> "请求未成功，请稍后重试或查看提供商服务状态。"
    }
}
