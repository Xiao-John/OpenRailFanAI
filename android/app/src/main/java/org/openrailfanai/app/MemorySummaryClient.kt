package org.openrailfanai.app

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

internal class MemorySummaryClient(private val base: String) {
    @Volatile private var connection: HttpURLConnection? = null
    fun cancel() { connection?.disconnect() }
    fun summarize(snapshot: JSONArray, spec: JSONObject): String {
        require(snapshot.length() > 0) { "还没有记忆可总结" }
        require(spec.optString("model").isNotBlank()) { "请先选择并保存当前对话模型" }
        val body = JSONObject(spec.toString()).put("message", "总结当前个人记忆").put("records", snapshot)
        val c = URL(base.trimEnd('/') + "/api/memory/summary").openConnection() as HttpURLConnection
        connection = c
        try {
            c.requestMethod = "POST"; c.doOutput = true; c.connectTimeout = 10000; c.readTimeout = 90000
            c.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            c.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            val ok = c.responseCode in 200..299
            val text = (if (ok) c.inputStream else c.errorStream)?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (!ok) throw IllegalStateException(when (c.responseCode) {
                413 -> "当前模型窗口不足以完整读取记忆，请更换更大窗口模型或整理记忆。"
                422 -> "记忆格式或大小不符合要求，请整理后重试。"
                else -> "当前模型未能生成总结（HTTP ${c.responseCode}），请检查模型设置。"
            })
            val result = JSONObject(text)
            require(result.optInt("records_read") == snapshot.length()) { "未确认完整读取记忆，不展示总结" }
            return result.getString("summary")
        } finally { c.disconnect(); if (connection === c) connection = null }
    }
}
