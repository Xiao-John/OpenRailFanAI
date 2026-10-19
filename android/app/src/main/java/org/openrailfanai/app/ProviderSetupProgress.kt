package org.openrailfanai.app

/** Portable step state: no platform classes, credentials or inferred website completion. */
internal enum class ProviderSetupStep(val title: String) {
    SERVICE("选择服务商"), ACCOUNT("注册或登录账户"), AUTH("完成实名认证"),
    CREDIT("确认账户与计费"), KEY("创建服务密钥"), CONNECT("粘贴密钥并连接"), FINISH("配置完成");
    fun next(): ProviderSetupStep = entries[(ordinal + 1).coerceAtMost(entries.lastIndex)]
    fun previous(): ProviderSetupStep = entries[(ordinal - 1).coerceAtLeast(0)]
    companion object {
        fun restore(raw: String?): ProviderSetupStep = entries.firstOrNull { it.name == raw && it != FINISH } ?: SERVICE
    }
}
internal data class ProviderSetupProgress(val step: ProviderSetupStep, val rememberKey: Boolean, val model: String)
