package org.openrailfanai.app

import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import java.net.ServerSocket
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread

class ComposeInputsSettingsTest {
    @get:Rule val compose = createComposeRule()

    @Test fun attachmentControlsPreserveDraftAndRequireExplicitSend() {
        var attachment by mutableStateOf<PendingAttachment?>(PendingAttachment("出行资料.docx", 2048, false))
        var input by mutableStateOf("请整理这份资料")
        val imported = mutableListOf<Boolean>()
        var sent = 0
        compose.setContent { UsabilityCaptureRoot {
            MainChatScreen("后端已连接", true, ChatUiState(), emptyList(), input, { input = it }, {}, {}, { sent++ }, {}, {}, { _, _, _ -> }, {}, {},
                applySystemInsets = false, pendingAttachment = attachment, onImportAttachment = { imported += it }, onRemoveAttachment = { attachment = null })
        } }
        compose.onNodeWithTag("pending-attachment").assertIsDisplayed()
        compose.onNodeWithText("文档 · 2 KiB · 发送后处理").assertIsDisplayed()
        assertTrue(compose.onNodeWithTag("remove-attachment").fetchSemanticsNode().boundsInRoot.height >= 44)
        compose.onNodeWithTag("usability-capture-root").logUsabilityCapture("attachment-composer")
        compose.onNodeWithTag("import-attachment").performClick()
        compose.onNodeWithText("相册").performClick()
        assertEquals(listOf(true), imported)
        assertEquals(0, sent)
        assertEquals("请整理这份资料", input)
        compose.onNodeWithTag("import-attachment").performClick()
        compose.onNodeWithText("文件").performClick()
        assertEquals(listOf(true, false), imported)
        compose.onNodeWithTag("remove-attachment").performClick()
        compose.onNodeWithTag("pending-attachment").assertDoesNotExist()
        assertEquals("请整理这份资料", input)
        assertEquals(0, sent)
    }

    @Test fun unavailableCompatibilityNeverPostsAndExplanationIsLazy() {
        CompatibilityFixture().use { fixture ->
            compose.setContent { UsabilityCaptureRoot {
                Column(Modifier.fillMaxSize().padding(16.dp)) { FuckCrSettingsSection(fixture.url) }
            } }
            compose.waitUntil(8_000) { compose.onAllNodesWithText("适配准备中").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithTag("fuck-cr-activate").assertIsNotEnabled()
            compose.onNodeWithText("开关已经到岗", substring = true).assertDoesNotExist()
            compose.onNodeWithTag("usability-capture-root").logUsabilityCapture("fuck-cr-settings")
            compose.onNodeWithTag("fuck-cr-explanation").performClick()
            compose.onNodeWithText("开关已经到岗", substring = true).assertIsDisplayed()
            assertEquals(0, fixture.posts.get())
            val denied = FuckCrClient(fixture.url).activate()
            assertTrue(denied.rejected)
            assertFalse(denied.status.enabled)
            fixture.unsupported = true
            try { FuckCrClient(fixture.url).status(); fail("404 应保持入口不支持") } catch (_: FuckCrUnsupported) { }
        }
    }
}

private class CompatibilityFixture : AutoCloseable {
    private val socket = ServerSocket(0)
    val url = "http://127.0.0.1:${socket.localPort}"
    val posts = AtomicInteger()
    @Volatile var unsupported = false
    private val worker = thread(isDaemon = true) {
        while (!socket.isClosed) runCatching {
            socket.accept().use { connection ->
                connection.soTimeout = 5_000
                val reader = connection.getInputStream().bufferedReader()
                val line = reader.readLine().orEmpty()
                var length = 0
                while (true) { val header = reader.readLine() ?: break; if (header.isEmpty()) break; if (header.startsWith("Content-Length:", true)) length = header.substringAfter(':').trim().toInt() }
                repeat(length) { reader.read() }
                val status = JSONObject("""{"schema_version":1,"feature":"fuck_cr","enabled":false,"new_rules_ready":false,"can_enable":false,"mode":"shadow"}""")
                val post = line.startsWith("POST")
                if (post) posts.incrementAndGet()
                val code = if (unsupported) 404 else if (post) 409 else 200
                val body = if (unsupported) "{}" else if (post) JSONObject().put("detail", JSONObject().put("status", status)).toString() else status.toString()
                val bytes = body.toByteArray(Charsets.UTF_8)
                connection.getOutputStream().use { it.write("HTTP/1.1 $code Response\r\nContent-Type: application/json\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n".toByteArray()); it.write(bytes) }
            }
        }
    }
    override fun close() { socket.close(); worker.join(1_000) }
}
