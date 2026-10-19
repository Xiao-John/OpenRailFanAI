package org.openrailfanai.app

internal data class PendingAttachment(val name: String, val bytes: Long, val image: Boolean) {
    val sizeLabel: String get() = if (bytes >= 1024 * 1024) "%.1f MiB".format(java.util.Locale.ROOT, bytes / (1024.0 * 1024.0)) else "${(bytes + 1023) / 1024} KiB"
}

/** Explicit commands only; no quoted text or model output is executable. */
object AppAgent {
    fun themeCommand(message: String): String? {
        val text = message.replace(Regex("[\\s。！!，,]+"), "").lowercase()
        if (Regex("不要|别|不想|如果|假如|怎么|如何|什么|为什么|[“”\"「」]").containsMatchIn(text)) return null
        if (Regex("(?:请|帮我|给我)?撤销(?:刚才的?|上次的?)?(?:主题|外观)(?:切换|修改|设置)?").matches(text)) return "undo"
        val match = Regex("(?:请|帮我|给我)?(?:把(?:主题|外观)?|将(?:主题|外观)?)?(?:(?:主题|外观)(?:设置为|设为|改成|改为|切换到)?|切换到|切换成|换成|改成|改为|开启|打开|用|设置为|设为)?(深色|浅色|暗色|亮色|黑夜|夜间|白天|跟随系统)(?:主题|模式)?").matchEntire(text) ?: return null
        return when (match.groupValues[1]) {
            "跟随系统" -> "auto"
            "深色", "暗色", "黑夜", "夜间" -> "dark"
            else -> "light"
        }
    }

    // The existing regenerate callback carries query text only. Ambiguous same-text rounds
    // require a fresh import/send rather than substituting another attachment.
    fun requiresReimport(messages: List<StoredMessage>, query: String): Boolean = messages.any {
        it.role == "user" && it.content == query && it.meta?.optBoolean("attachment_input") == true
    }

    fun fileCommand(message: String): Boolean = Regex("导出|生成.{0,12}(?:文件|日历|日程|表格|csv|ics)|整理.{0,12}(?:表格|文件)|读取.{0,8}文件", RegexOption.IGNORE_CASE).containsMatchIn(message)
    const val MAX_INPUT_BYTES = 8 * 1024 * 1024
    val documentExtensions = setOf("txt", "md", "tex", "csv", "json", "doc", "docx", "rtf")
    val imageExtensions = setOf("png", "jpg", "jpeg", "webp", "gif")
    fun isImage(name: String, mime: String = ""): Boolean = mime.startsWith("image/") || name.substringAfterLast('.', "").lowercase() in imageExtensions
    fun importCommand(message: String): Boolean = Regex("(?:请|帮我)?(?:导入|打开|选择|上传)(?:文件|图片)[。！!]?").matches(message)
}
