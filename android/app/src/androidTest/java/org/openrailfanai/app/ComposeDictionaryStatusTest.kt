package org.openrailfanai.app

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import org.json.JSONObject
import org.junit.Rule
import org.junit.Test
import java.net.ServerSocket
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread

/** Local HTTP only. An up-to-date GTFS response must not claim to update photos. */
class ComposeDictionaryStatusTest {
    @get:Rule val compose = createComposeRule()

    @Test fun separateVersionsAndLegacyCompatibility() {
        val current = AtomicReference(JSONObject("""{"available":true,"version":"gtfs-fixture","photo_spots":{"available":true,"version":"2026-10-07T07:59:19+00:00","schema_version":"1","documents":810,"scopes":265}}"""))
        val socket = ServerSocket(0)
        val worker = thread(isDaemon = true) {
            while (!socket.isClosed) {
                val connection = runCatching { socket.accept() }.getOrNull() ?: break
                connection.use {
                    val reader = it.getInputStream().bufferedReader()
                    val path = reader.readLine().orEmpty().split(' ').getOrNull(1).orEmpty()
                    while (!reader.readLine().isNullOrEmpty()) Unit
                    val body = JSONObject().put("current", current.get()).apply {
                        if (path == "/api/updates/dictionary") {
                            put("status", "ok"); put("update_available", false); put("scope", "gtfs")
                        }
                    }.toString().toByteArray(Charsets.UTF_8)
                    it.getOutputStream().apply {
                        write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ${body.size}\r\nConnection: close\r\n\r\n".toByteArray())
                        write(body); flush()
                    }
                }
            }
        }
        fun waitFor(text: String) {
            compose.waitUntil(10_000) { compose.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithText(text).performScrollTo().assertIsDisplayed()
        }
        try {
            val client = SettingsClient("http://127.0.0.1:${socket.localPort}")
            compose.setContent {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    UpdateSettingsSection(client, onOpenUrl = {})
                }
            }
            waitFor("已收录 810 篇攻略 · 265 个收录范围")
            waitFor("机位数据版本：2026-10-07T07:59:19+00:00")
            compose.onNodeWithText("检查时刻词典更新").performScrollTo().performClick()
            waitFor("当前时刻词典已是最新版本。")
            waitFor("已收录 810 篇攻略 · 265 个收录范围")
            current.set(JSONObject("""{"available":true,"version":"legacy-gtfs"}"""))
            compose.onNodeWithText("刷新本地版本").performScrollTo().performClick()
            waitFor("当前服务未提供机位库信息。")
            current.set(JSONObject("""{"available":true,"version":"gtfs-fixture","photo_spots":{"available":false}}"""))
            compose.onNodeWithText("刷新本地版本").performScrollTo().performClick()
            waitFor("本机尚未收录机位攻略，随软件更新接收。")
        } finally {
            socket.close()
            worker.join(1000)
        }
    }
}
