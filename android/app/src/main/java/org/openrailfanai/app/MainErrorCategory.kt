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
