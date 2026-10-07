package org.openrailfanai.app

import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/** Metadata/dictionary use localhost. APK bytes use validated public GitHub HTTPS without credentials. */
internal class UpdateClient(baseUrl: String,
    private val assetConnection: (URL) -> HttpURLConnection = { it.openConnection() as HttpURLConnection }) {
    private val base = baseUrl.trimEnd('/').also {
        require(URL(it).host in setOf("127.0.0.1", "localhost", "::1"))
    }
    private val connection = AtomicReference<HttpURLConnection?>()
    private val cancelled = AtomicBoolean(false)
    private val downloaded = AtomicReference<File?>()
    fun cancel() { cancelled.set(true); connection.get()?.disconnect(); downloaded.getAndSet(null)?.delete() }

    private fun open(path: String, body: JSONObject? = null): HttpURLConnection {
        check(!cancelled.get()) { "操作已取消" }
        val conn = URL(base + path).openConnection(java.net.Proxy.NO_PROXY) as HttpURLConnection
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

    fun download(current: String, latest: String, abi: String, directory: File, expectedHash: String,
                 progress: (JSONObject) -> Unit = {}): File {
        require(Regex("[a-fA-F0-9]{64}").matches(expectedHash)) { "发布缺少有效校验信息" }
        progress(JSONObject().put("stage", "check"))
        val metadata = software(current, abi)
        require(metadata.optString("status") == "ok" && metadata.optBoolean("download_supported") &&
            metadata.optBoolean("update_available") && metadata.optString("latest_version") == latest) { "发布版本已变化，请重新检查更新" }
        val asset = metadata.optJSONObject("asset") ?: error("发布缺少安装包信息")
        require(asset.optString("sha256").removePrefix("sha256:").equals(expectedHash, true)) { "发布校验信息已变化，请重新检查更新" }
        val size = asset.optLong("size", -1)
        require(size in 1..512L * 1024 * 1024) { "安装包声明大小无效" }
        val url = URL(asset.optString("url"))
        val normalizedAbi = if (abi == "arm64-v8a") "arm64" else abi
        require(normalizedAbi in setOf("arm64", "armeabi-v7a", "x86_64")) { "处理器架构不受支持" }
        val release = URL(metadata.optString("release_url"))
        val tag = release.path.substringAfter("/Xiao-John/OpenRailFanAI/releases/tag/", "")
        require(release.protocol == "https" && release.host == "github.com" && tag in setOf(latest, "v$latest")) { "发布版本地址无效" }
        val expectedPath = "/Xiao-John/OpenRailFanAI/releases/download/$tag/OpenRailFanAI-$latest-$normalizedAbi-release.apk"
        require(url.host == "github.com" && url.path == expectedPath && url.query == null) { "安装包不属于目标发布版本" }
        directory.mkdirs()
        val part = File.createTempFile("software-", ".part", directory)
        val dest = File(directory, part.name.removeSuffix(".part") + ".apk")
        var success = false
        try {
            val conn = openAsset(url)
            try {
                val digest = MessageDigest.getInstance("SHA-256")
                val headerSize = conn.getHeaderField("Content-Length")?.toLongOrNull()
                require(headerSize == null || headerSize == size) { "下载大小与发布信息不一致" }
                var count = 0L
                var lastProgress = 0L
                val deadline = System.nanoTime() + 30L * 60 * 1_000_000_000
                progress(JSONObject().put("stage", "download").put("unit", "bytes").put("completed", 0).put("total", size))
                conn.inputStream.use { input -> part.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        check(!cancelled.get()) { "操作已取消" }
                        check(System.nanoTime() < deadline) { "下载超时，请重新下载" }
                        val read = input.read(buffer)
                        if (read < 0) break
                        count += read; require(count <= size) { "安装包超过声明大小" }
                        digest.update(buffer, 0, read); output.write(buffer, 0, read)
                        val now = System.nanoTime()
                        if (now - lastProgress >= 200_000_000 || count == size) {
                            progress(JSONObject().put("stage", "download").put("unit", "bytes").put("completed", count).put("total", size))
                            lastProgress = now
                        }
                    }
                } }
                progress(JSONObject().put("stage", "verify"))
                val actual = digest.digest().joinToString("") { "%02x".format(it) }
                require(count == size && actual.equals(expectedHash, ignoreCase = true)) { "安装包校验失败" }
                check(!cancelled.get()) { "操作已取消" }
                check(part.renameTo(dest)) { "无法保存安装包" }
                downloaded.set(dest)
                check(!cancelled.get()) { "操作已取消" }
                success = true
                return dest
            } finally { conn.disconnect(); connection.set(null) }
        } finally { part.delete(); if (!success) dest.delete() }
    }

    private fun openAsset(initial: URL): HttpURLConnection {
        var url = initial
        repeat(6) {
            require(url.protocol == "https" && url.port in setOf(-1, 443) && url.userInfo == null &&
                url.host in setOf("github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com")) { "下载地址不受支持" }
            check(!cancelled.get()) { "操作已取消" }
            val conn = assetConnection(url)
            conn.connectTimeout = 15_000; conn.readTimeout = 60_000; conn.instanceFollowRedirects = false
            conn.setRequestProperty("Accept-Encoding", "identity")
            connection.set(conn)
            try {
                check(!cancelled.get()) { "操作已取消" }
                val code = conn.responseCode
                if (code in setOf(301, 302, 303, 307, 308)) {
                    val location = conn.getHeaderField("Location") ?: error("下载跳转缺少地址")
                    url = URL(url, location)
                    conn.disconnect(); connection.set(null)
                } else {
                    require(code == 200) { "安装包下载失败（HTTP $code）" }
                    return conn
                }
            } catch (error: Throwable) { conn.disconnect(); connection.set(null); throw error }
        }
        error("下载跳转次数过多")
    }
}
