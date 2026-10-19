package org.openrailfanai.app

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.util.Base64
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.ByteArrayOutputStream
import java.io.File
import java.net.ServerSocket
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

@RunWith(AndroidJUnit4::class)
class AgentInputIntegrationTest {
    private fun image(): JSONObject {
        val bitmap = Bitmap.createBitmap(1200, 250, Bitmap.Config.ARGB_8888)
        Canvas(bitmap).apply {
            drawColor(Color.WHITE)
            drawText("北京 Shanghai 12345", 35f, 145f, Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.BLACK; textSize = 80f })
        }
        val bytes = ByteArrayOutputStream().also { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }.toByteArray()
        bitmap.recycle()
        return JSONObject().put("name", "sample.png").put("mime", "image/png")
            .put("content_base64", Base64.encodeToString(bytes, Base64.NO_WRAP))
    }

    @Test fun bundledChineseOcrRecognizesTextWithoutModelApi() {
        val result = ImageTextRecognizer.convert(JSONArray().put(image())).getJSONObject(0)
        assertEquals("sample-ocr.txt", result.getString("name"))
        assertEquals("local_ocr", result.getString("source"))
        val text = String(Base64.decode(result.getString("content_base64"), Base64.DEFAULT), Charsets.UTF_8).replace(Regex("\\s"), "")
        assertTrue(text, text.contains("12345"))
        assertTrue(text, text.contains("北京"))
    }

    @Test fun providerInputTypesAreIsolatedAndSurviveReload() {
        val dir = File(InstrumentationRegistry.getInstrumentation().targetContext.cacheDir, "input-types-${System.nanoTime()}").apply { mkdirs() }
        try {
            val repository = MainSettingsRepository(dir, true)
            val text = ProviderConfig("text-only", "text", "https://example.com", "t")
            val vision = ProviderConfig("vision", "vision", "https://example.com", "v", inputTypes = setOf("text", "image"))
            repository.save(listOf(text, vision), text.id, false)
            assertEquals("[\"text\"]", MainSettingsRepository(dir, true).requestLlmSpec()!!.getJSONArray("input_types").toString())
            repository.save(repository.entries(), vision.id, false)
            assertTrue(MainSettingsRepository(dir, true).requestLlmSpec()!!.getJSONArray("input_types").toString().contains("image"))
            assertEquals(setOf("text"), repository.entries().first { it.id == text.id }.inputTypes)
        } finally { dir.deleteRecursively() }
    }

    private fun exchange(vision: Boolean, fallback: Boolean): List<JSONObject> {
        val server = ServerSocket(0)
        val executor = Executors.newSingleThreadExecutor()
        val capture = executor.submit<List<JSONObject>> {
            val bodies = mutableListOf<JSONObject>()
            repeat(if (fallback) 2 else 1) { index ->
                server.accept().use { socket ->
                    socket.soTimeout = 60_000
                    val input = socket.getInputStream()
                    fun line(): String = buildString { while (true) { val value = input.read(); if (value < 0 || value == 10) break; if (value != 13) append(value.toChar()) } }
                    line()
                    var size = 0
                    while (true) {
                        val header = line(); if (header.isEmpty()) break
                        if (header.startsWith("Content-Length:", true)) size = header.substringAfter(':').trim().toInt()
                    }
                    val bytes = ByteArray(size); var offset = 0
                    while (offset < size) { val count = input.read(bytes, offset, size - offset); require(count > 0); offset += count }
                    bodies += JSONObject(String(bytes, Charsets.UTF_8))
                    val response = (if (fallback && index == 0) JSONObject().put("handled", true).put("needs_local_ocr", true) else JSONObject().put("handled", true).put("answer", "已读取").put("files", JSONArray())).toString().toByteArray(Charsets.UTF_8)
                    socket.getOutputStream().apply { write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ${response.size}\r\nConnection: close\r\n\r\n".toByteArray()); write(response); flush() }
                }
            }
            bodies
        }
        try {
            val spec = JSONObject().put("input_types", if (vision) JSONArray().put("text").put("image") else JSONArray().put("text"))
            ChatRepository("http://127.0.0.1:${server.localPort}").stream(ChatStreamRequest("读取图片", sessionId = "test", llmSpec = spec, agentFiles = JSONArray().put(image()))) {}
            return capture.get(90, TimeUnit.SECONDS)
        } finally { server.close(); executor.shutdownNow() }
    }

    @Test fun textOnlyRequestNeverUploadsOriginalImage() {
        val body = exchange(false, false).single()
        assertEquals("sample-ocr.txt", body.getJSONArray("files").getJSONObject(0).getString("name"))
        assertEquals("[\"text\"]", body.getJSONArray("input_types").toString())
    }

    @Test fun visionRequestUsesOriginalAndExplicitCapabilityFallbackUsesOnlyText() {
        val body = exchange(true, false).single()
        assertEquals("sample.png", body.getJSONArray("files").getJSONObject(0).getString("name"))
        val fallback = exchange(true, true)
        assertEquals("sample.png", fallback[0].getJSONArray("files").getJSONObject(0).getString("name"))
        assertEquals("sample-ocr.txt", fallback[1].getJSONArray("files").getJSONObject(0).getString("name"))
        assertEquals("[\"text\"]", fallback[1].getJSONArray("input_types").toString())
    }
    @Test fun emptyOcrIsAnExplicitFailure() {
        val bitmap = Bitmap.createBitmap(600, 300, Bitmap.Config.ARGB_8888)
        Canvas(bitmap).drawColor(Color.WHITE)
        val bytes = ByteArrayOutputStream().also { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }.toByteArray()
        bitmap.recycle()
        try { ImageTextRecognizer.text(bytes); fail("Blank images must not become invented text") }
        catch (error: IllegalArgumentException) { assertTrue(error.message.orEmpty().contains("未识别出文字")) }
    }

    @Test fun cancellationBeforeOcrDoesNotStartAnUpload() {
        val repository = ChatRepository("http://127.0.0.1:1")
        try {
            repository.stream(ChatStreamRequest("读取图片", sessionId = "cancel", agentFiles = JSONArray().put(image()))) { event ->
                if (event.optString("stage") == "local_ocr") repository.cancel()
            }
            fail("Cancelled OCR must not continue to the API")
        } catch (_: InterruptedException) {
            // Expected: cancellation interrupts local OCR before any HTTP request exists.
        } finally { Thread.interrupted() }
    }

    @Test fun authenticationFailureDoesNotBecomeAnOcrRetry() {
        val server = ServerSocket(0)
        val executor = Executors.newSingleThreadExecutor()
        val capture = executor.submit<String> {
            server.accept().use { socket ->
                socket.soTimeout = 10_000
                val input = socket.getInputStream()
                fun line(): String = buildString { while (true) { val n = input.read(); if (n < 0 || n == 10) break; if (n != 13) append(n.toChar()) } }
                line(); var size = 0
                while (true) { val header = line(); if (header.isEmpty()) break; if (header.startsWith("Content-Length:", true)) size = header.substringAfter(':').trim().toInt() }
                val bytes = ByteArray(size); var offset = 0
                while (offset < size) { val count = input.read(bytes, offset, size - offset); require(count > 0); offset += count }
                socket.getOutputStream().apply { write("HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".toByteArray()); flush() }
                JSONObject(String(bytes, Charsets.UTF_8)).getJSONArray("files").getJSONObject(0).getString("name")
            }
        }
        val events = mutableListOf<JSONObject>()
        try {
            val spec = JSONObject().put("input_types", JSONArray().put("text").put("image"))
            try {
                ChatRepository("http://127.0.0.1:${server.localPort}").stream(ChatStreamRequest("读取图片", sessionId = "auth", llmSpec = spec, agentFiles = JSONArray().put(image()))) { events += it }
                fail("401 must remain an error")
            } catch (error: ChatHttpException) { assertEquals(401, error.statusCode) }
            assertEquals("sample.png", capture.get(15, TimeUnit.SECONDS))
            assertFalse(events.any { it.optString("stage") == "local_ocr" })
        } finally { server.close(); executor.shutdownNow() }
    }
    @Test fun embeddedPythonReadsRealOfficeFilesWithoutDesktopConverters() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        if (!com.chaquo.python.Python.isStarted()) com.chaquo.python.Python.start(com.chaquo.python.android.AndroidPlatform(instrumentation.targetContext))
        val python = com.chaquo.python.Python.getInstance()
        val documents = python.getModule("app.agent_documents")
        for (name in listOf("sample.doc", "sample.docx", "sample.rtf")) {
            val raw = instrumentation.context.assets.open("agent-documents/$name").use { it.readBytes() }
            val bytes = python.getModule("builtins").callAttr("bytes", raw)
            val text = documents.callAttr("read_document", name, bytes).toString()
            assertTrue("$name: $text", text.contains("RailFan 文档输入验证"))
            assertTrue("$name: $text", text.contains("北京 → 上海虹桥"))
            assertTrue("$name: $text", text.contains("G1 2026-10-18 09:00"))
        }
    }
    @Test fun attachmentRoundGuardSurvivesHistoryReloadWithoutPersistingBytes() {
        val dir = File(InstrumentationRegistry.getInstrumentation().targetContext.cacheDir, "input-history-${System.nanoTime()}").apply { mkdirs() }
        try {
            val store = ConversationStore(dir, true)
            val conversation = store.create()
            store.addUser(conversation.id, "阅读图片", JSONObject().put("attachment_input", true))
            store.addAssistant(conversation.id, "已整理", JSONObject().put("intent", "file_tool"))
            val reloaded = ConversationStore(dir, true).current()!!.messages
            assertTrue(AppAgent.requiresReimport(reloaded, "阅读图片"))
            assertFalse(AppAgent.requiresReimport(reloaded, "G1时刻表"))
            val raw = File(dir, "state.json").readText()
            assertFalse(raw.contains("content_base64"))
            assertFalse(raw.contains("api_key"))
        } finally { dir.deleteRecursively() }
    }
}
