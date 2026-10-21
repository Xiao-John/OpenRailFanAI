package org.openrailfanai.app

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

internal data class GeneratedConversationTitle(val title: String, val tokens: Long)

internal class ConversationTitleClient(private val base: String) {
    @Volatile private var active: HttpURLConnection? = null
    fun cancel() { active?.disconnect() }
    fun generate(messages: List<StoredMessage>, spec: JSONObject?): GeneratedConversationTitle {
        val items = messages.filter { it.role in setOf("user", "assistant") }.take(6)
        val history = JSONArray()
        var budget = 7600
        for (item in items) {
            val results = item.meta?.optJSONArray("displayResults")?.toString().orEmpty()
            val value = (item.content.ifBlank { results }).take(minOf(1200, budget))
            if (value.isNotBlank()) history.put(JSONObject().put("role", item.role).put("content", value))
            budget -= value.length
        }
        val body = JSONObject(spec?.toString() ?: "{}").put("message", "总结对话标题").put("history", history)
        val connection = URL(base.trimEnd('/') + "/api/conversations/title").openConnection() as HttpURLConnection
        active = connection
        try {
            connection.requestMethod = "POST"; connection.doOutput = true
            connection.connectTimeout = 8000; connection.readTimeout = 30000
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            check(connection.responseCode in 200..299) { "标题生成未成功" }
            val result = JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
            val title = result.getString("title").trim()
            require(title.isNotBlank() && title.length <= 24 && !title.contains('\n'))
            return GeneratedConversationTitle(title, result.optJSONObject("usage")?.optLong("total_tokens")?.coerceAtLeast(0L) ?: 0L)
        } finally { connection.disconnect(); if (active === connection) active = null }
    }
}
