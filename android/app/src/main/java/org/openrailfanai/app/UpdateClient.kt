package org.openrailfanai.app

import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/** Local backend only. No GitHub credential or API key is involved. One operation at a time. */
internal class UpdateClient(baseUrl: String) {
    private val base = baseUrl.trimEnd('/').also {
        require(URL(it).host in setOf("127.0.0.1", "localhost", "::1"))
    }
    private val connection = AtomicReference<HttpURLConnection?>()
    private val cancelled = AtomicBoolean(false)
    private val downloaded = AtomicReference<File?>()
    fun cancel() { cancelled.set(true); connection.get()?.disconnect(); downloaded.getAndSet(null)?.delete() }

    private fun open(path: String, body: JSONObject? = null): HttpURLConnection {
        check(!cancelled.get()) { "操作已取消" }
        val conn = URL(base + path).openConnection() as HttpURLConnection
        conn.connectTimeout = 8_000
        conn.readTimeout = 180_000
        conn.instanceFollowRedirects = false
        connection.set(conn)
        if (cancelled.get()) { conn.disconnect(); error("操作已取消") }
        try {
            if (body != null) {
                conn.requestMethod = "POST"; conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            if (conn.responseCode !in 200..299) {
                val text = conn.errorStream?.bufferedReader()?.use { it.readText().take(8192) }.orEmpty()
                val message = runCatching { JSONObject(text).optJSONObject("detail")?.optString("message") }.getOrNull()
                error(message?.takeIf(String::isNotBlank) ?: "更新请求失败（HTTP ${conn.responseCode}）")
            }
            return conn
        } catch (error: Throwable) { conn.disconnect(); connection.set(null); throw error }
    }

    private fun json(path: String, body: JSONObject? = null): JSONObject {
        val conn = open(path, body)
        return try { JSONObject(conn.inputStream.bufferedReader().use { it.readText() }) }
        finally { conn.disconnect(); connection.set(null) }
    }
    fun software(current: String, abi: String): JSONObject = json("/api/updates/software?current_version=${java.net.URLEncoder.encode(current, "UTF-8")}&abi=${java.net.URLEncoder.encode(abi, "UTF-8")}")
    fun dictionaryLocal(): JSONObject = json("/api/updates/dictionary/local")
    fun dictionary(): JSONObject = json("/api/updates/dictionary")
    fun applyDictionary(version: String): JSONObject = json("/api/updates/dictionary/apply", JSONObject().put("latest_version", version))

    fun applyDictionaryStream(version: String, progress: (JSONObject) -> Unit): JSONObject {
        val conn = open("/api/updates/dictionary/apply/stream", JSONObject().put("latest_version", version))
        try {
            require(conn.contentType.orEmpty().substringBefore(';').trim() == "text/event-stream") { "服务未返回词典更新进度，请更新后端" }
            var terminal: JSONObject? = null
            conn.inputStream.bufferedReader(Charsets.UTF_8).use { reader ->
                val data = StringBuilder()
                fun dispatch() {
                    if (data.isEmpty()) return
                    val event = JSONObject(data.toString()); data.clear()
                    when (event.optString("type")) {
                        "progress" -> progress(event)
                        "done" -> terminal = event
                        "error" -> error(event.optString("message", "词典更新失败"))
                        else -> error("无法识别词典更新事件")
                    }
                }
                while (terminal == null) {
                    check(!cancelled.get()) { "操作已取消" }
                    val line = reader.readLine() ?: break
                    require(line.length <= 64 * 1024) { "词典更新事件超过大小限制" }
                    when {
                        line.isEmpty() -> dispatch()
                        line.startsWith("data:") -> {
                            if (data.isNotEmpty()) data.append('\n')
                            data.append(line.removePrefix("data:").removePrefix(" "))
                            require(data.length <= 64 * 1024) { "词典更新事件超过大小限制" }
                        }
                    }
                }
            }
            return terminal ?: error("词典更新连接已中断，请刷新本地版本确认")
        } finally { conn.disconnect(); connection.set(null) }
    }

    fun download(current: String, latest: String, abi: String, directory: File, expectedHash: String): File {
        require(Regex("[a-fA-F0-9]{64}").matches(expectedHash)) { "发布缺少有效校验信息" }
        directory.mkdirs()
        val part = File.createTempFile("software-", ".part", directory)
        val dest = File(directory, part.name.removeSuffix(".part") + ".apk")
        var success = false
        try {
            val conn = open("/api/updates/software/download", JSONObject().put("current_version", current).put("latest_version", latest).put("abi", abi))
            try {
                val digest = MessageDigest.getInstance("SHA-256")
                val headerHash = conn.getHeaderField("X-Content-SHA256").orEmpty().removePrefix("sha256:")
                require(headerHash.equals(expectedHash, ignoreCase = true)) { "下载校验信息与检查结果不一致，请重新检查更新" }
                var total = 0L
                conn.inputStream.use { input -> part.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        check(!cancelled.get()) { "操作已取消" }
                        val count = input.read(buffer)
                        if (count < 0) break
                        total += count; require(total <= 512L * 1024 * 1024) { "安装包超过大小限制" }
                        digest.update(buffer, 0, count); output.write(buffer, 0, count)
                    }
                } }
                val actual = digest.digest().joinToString("") { "%02x".format(it) }
                require(total > 0 && actual.equals(expectedHash, ignoreCase = true)) { "安装包校验失败" }
                check(!cancelled.get()) { "操作已取消" }
                check(part.renameTo(dest)) { "无法保存安装包" }
                downloaded.set(dest)
                check(!cancelled.get()) { "操作已取消" }
                success = true
                return dest
            } finally { conn.disconnect(); connection.set(null) }
        } finally { part.delete(); if (!success) dest.delete() }
    }
}
