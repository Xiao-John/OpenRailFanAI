package org.openrailfanai.app

import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.ServerSocket
import java.net.URL
import java.security.MessageDigest
import kotlin.concurrent.thread

class UpdateDownloadTest {
    private val content = ByteArray(160_000) { (it % 251).toByte() }
    private val hash get() = MessageDigest.getInstance("SHA-256").digest(content).joinToString("") { "%02x".format(it) }
    private val assetUrl = "https://github.com/Xiao-John/OpenRailFanAI/releases/download/v0.1.25/OpenRailFanAI-0.1.25-arm64-release.apk"
    private fun metadata(size: Int = content.size, url: String = assetUrl) = JSONObject().put("status", "ok")
        .put("update_available", true).put("download_supported", true).put("latest_version", "0.1.25")
        .put("release_url", "https://github.com/Xiao-John/OpenRailFanAI/releases/tag/v0.1.25")
        .put("asset", JSONObject().put("url", url).put("size", size).put("sha256", hash)).toString()
    private fun run(metadata: String = metadata(), transport: (URL) -> HttpURLConnection, operation: (UpdateClient, File) -> Unit) {
        val server = ServerSocket(0)
        val worker = thread(isDaemon = true) {
            runCatching { server.accept().use { socket ->
                val reader = socket.getInputStream().bufferedReader()
                reader.readLine(); while (!reader.readLine().isNullOrEmpty()) Unit
                val body = metadata.toByteArray()
                socket.getOutputStream().apply {
                    write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ${body.size}\r\nConnection: close\r\n\r\n".toByteArray()); write(body); flush()
                }
            } }
        }
        val directory = File(InstrumentationRegistry.getInstrumentation().targetContext.cacheDir, "download-test-${System.nanoTime()}").apply { mkdirs() }
        try { operation(UpdateClient("http://127.0.0.1:${server.localPort}", transport), directory) }
        finally { directory.deleteRecursively(); server.close(); worker.join(1000) }
    }
    private fun connection(url: URL, bytes: ByteArray = content, code: Int = 200, location: String? = null) = object : HttpURLConnection(url) {
        override fun connect() {}
        override fun disconnect() {}
        override fun usingProxy() = false
        override fun getResponseCode() = code
        override fun getInputStream() = ByteArrayInputStream(bytes)
        override fun getHeaderField(name: String?) = when (name) { "Content-Length" -> bytes.size.toString(); "Location" -> location; else -> null }
    }
    @Test fun realByteProgressAndHash() {
        val events = mutableListOf<JSONObject>()
        run(transport = { url -> assertNull(url.userInfo); connection(url) }) { client, directory ->
            val result = client.download("0.1.24", "0.1.25", "arm64-v8a", directory, hash) { events.add(it) }
            assertArrayEquals(content, result.readBytes())
            val downloaded = events.filter { it.optString("stage") == "download" }.map { it.getLong("completed") }
            assertEquals(0L, downloaded.first()); assertEquals(content.size.toLong(), downloaded.last())
            assertTrue(downloaded.any { it in 1 until content.size.toLong() })
            assertEquals("verify", events.last().getString("stage"))
            assertEquals(0, directory.listFiles()!!.count { it.extension == "part" })
        }
    }
    @Test fun mismatchAndCancellationCleanPartialFiles() {
        run(transport = { connection(it, content.copyOf().apply { this[0] = 99 }) }) { client, directory ->
            assertTrue(runCatching { client.download("0.1.24", "0.1.25", "arm64-v8a", directory, hash) }.isFailure)
            assertTrue(directory.listFiles()!!.isEmpty())
        }
        run(transport = { connection(it) }) { client, directory ->
            assertTrue(runCatching { client.download("0.1.24", "0.1.25", "arm64-v8a", directory, hash) {
                if (it.optString("stage") == "download" && it.optLong("completed") > 0) client.cancel()
            } }.isFailure)
            assertTrue(directory.listFiles()!!.isEmpty())
        }
        run(metadata(size = content.size - 1), transport = { connection(it) }) { client, directory ->
            assertTrue(runCatching { client.download("0.1.24", "0.1.25", "arm64-v8a", directory, hash) }.isFailure)
            assertTrue(directory.listFiles()!!.isEmpty())
        }
    }
    @Test fun rejectsForeignAssetAndRedirect() {
        run(metadata(url = assetUrl.replace("Xiao-John", "foreign")), transport = { error("must not open") }) { client, directory ->
            assertTrue(runCatching { client.download("0.1.24", "0.1.25", "arm64-v8a", directory, hash) }.exceptionOrNull()?.message.orEmpty().contains("不属于"))
        }
        run(transport = { connection(it, code = 302, location = "http://127.0.0.1/secret") }) { client, directory ->
            assertTrue(runCatching { client.download("0.1.24", "0.1.25", "arm64-v8a", directory, hash) }.exceptionOrNull()?.message.orEmpty().contains("不受支持"))
            assertTrue(directory.listFiles()!!.isEmpty())
        }
    }
}
