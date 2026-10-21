package org.openrailfanai.app

import android.content.Context
import android.util.AtomicFile
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID

/** 用户明确保存的资料。应用私有文件，不混入会话、密钥导出或模型设置。 */
internal class PersonalMemoryStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("personal-memory", Context.MODE_PRIVATE)
    private val file = AtomicFile(File(context.applicationContext.filesDir, "personal-memory.json"))
    val enabled: Boolean get() = prefs.getBoolean("enabled", false)
    val revision: Long get() = prefs.getLong("revision", 0)
    private fun changed() { check(prefs.edit().putLong("revision", revision + 1).commit()) }
    fun setEnabled(value: Boolean) { check(prefs.edit().putBoolean("enabled", value).putLong("revision", revision + 1).commit()) }
    @Synchronized fun records(): JSONArray {
        if (!file.baseFile.exists() && !File(file.baseFile.path + ".bak").exists()) return JSONArray()
        // 损坏时明确报错，不能以空数组覆盖原文件。
        val root = JSONObject(file.openRead().bufferedReader().use { it.readText() })
        require(root.getInt("schema_version") == 1) { "记忆文件版本不支持" }
        return root.getJSONArray("records")
    }
    @Synchronized private fun write(rows: JSONArray) {
        val stream = file.startWrite()
        try {
            stream.write(JSONObject().put("schema_version", 1).put("records", rows).toString().toByteArray(Charsets.UTF_8))
            file.finishWrite(stream)
        } catch (error: Exception) { file.failWrite(stream); throw error }
        changed()
    }
    @Synchronized fun remember(text: String, source: String) {
        check(enabled) { "请先在设置中开启个人记忆" }
        val value = text.trim()
        require(value.isNotBlank() && value.length <= 2000) { "每条记忆需为 1–2000 字，请先精简内容" }
        require(!Regex("(?i)(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{8,}").containsMatchIn(value)) { "请勿将 API Key 或访问令牌存入记忆" }
        val rows = records()
        if ((0 until rows.length()).any { rows.getJSONObject(it).optString("text") == value }) return
        require(rows.length() < 64) { "最多保存 64 条，请先整理或删除旧记忆" }
        require((0 until rows.length()).sumOf { rows.getJSONObject(it).optString("text").length } + value.length <= 32000) { "记忆总量已达 32000 字，请先整理" }
        rows.put(JSONObject().put("id", UUID.randomUUID().toString()).put("text", value)
            .put("source", source).put("saved_at", java.time.Instant.now().toString()))
        write(rows)
    }
    @Synchronized fun remove(id: String) {
        val old = records()
        write(JSONArray().also { out -> (0 until old.length()).map { old.getJSONObject(it) }.filter { it.optString("id") != id }.forEach { out.put(it) } })
    }
    @Synchronized fun clear() { file.delete(); changed() }
    private fun receipt(row: JSONObject) = "[${row.optString("saved_at")}；${row.optString("source")}] ${row.optString("text")}"
    @Synchronized fun summarySnapshot(): JSONArray {
        check(enabled) { "记忆已关闭" }
        return JSONArray().also { out -> val rows = records(); (0 until rows.length()).forEach { out.put(receipt(rows.getJSONObject(it))) } }
    }
    @Synchronized fun relevant(query: String): List<String> {
        if (!enabled) return emptyList()
        val rows = records()
        val terms = Regex("[A-Za-z0-9-]{2,}|[\\p{IsHan}]{2,}").findAll(query).map { it.value.lowercase() }.toList()
        val candidates = (0 until rows.length()).map { rows.getJSONObject(it) }.reversed()
        val sorted = candidates.sortedByDescending { row ->
            val text = row.optString("text").lowercase()
            terms.sumOf { term -> if (term in text) 5 else if (term.length > 2 && term.windowed(2).any { it in text }) 1 else 0 }
        }
        var used = 0
        return sorted.map(::receipt).filter { value -> if (used + value.length <= 4000) { used += value.length; true } else false }.take(8)
    }
}
