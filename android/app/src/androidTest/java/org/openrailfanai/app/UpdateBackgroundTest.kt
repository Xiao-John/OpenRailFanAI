package org.openrailfanai.app

import android.app.ActivityManager
import android.app.NotificationManager
import android.os.Build
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.BasicText
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import java.net.ServerSocket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread

/** Real service and localhost SSE; navigation and Home must not abort the update. */
class UpdateBackgroundTest {
    @get:Rule val compose = createComposeRule()
    @Test fun dictionarySurvivesNavigationAndHomeSoftwareCancelIsIndependent() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        if (Build.VERSION.SDK_INT >= 33) instrumentation.uiAutomation.executeShellCommand("pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS").close()
        val manager = UpdateTasks.get(context)
        val opened = CountDownLatch(1)
        val advance = CountDownLatch(1)
        val finish = CountDownLatch(1)
        val softwareWait = CountDownLatch(1)
        val requests = AtomicInteger(0)
        val server = ServerSocket(0)
        val version = java.util.concurrent.atomic.AtomicReference("old-fixture")
        val worker = thread(isDaemon = true) {
            while (!server.isClosed) {
                val socket = runCatching { server.accept() }.getOrNull() ?: break
                thread(isDaemon = true) { runCatching { socket.use {
                    val reader = it.getInputStream().bufferedReader()
                    val path = reader.readLine().orEmpty().split(' ').getOrNull(1).orEmpty()
                    var length = 0
                    while (true) {
                        val header = reader.readLine().orEmpty(); if (header.isEmpty()) break
                        if (header.startsWith("Content-Length:", true)) length = header.substringAfter(':').trim().toInt()
                    }
                    if (length > 0) { val body = CharArray(length); var read = 0; while (read < length) read += reader.read(body, read, length - read) }
                    val output = it.getOutputStream()
                    if (path == "/api/updates/dictionary/apply/stream") {
                        requests.incrementAndGet()
                        output.write("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n".toByteArray()); output.flush()
                        fun event(body: String) { output.write("data: $body\n\n".toByteArray()); output.flush() }
                        event("""{"type":"progress","stage":"download","unit":"bytes","completed":10,"total":100}""")
                        opened.countDown(); advance.await(20, TimeUnit.SECONDS)
                        event("""{"type":"progress","stage":"download","unit":"bytes","completed":50,"total":100}""")
                        finish.await(20, TimeUnit.SECONDS)
                        version.set("new-fixture")
                        event("""{"type":"progress","stage":"commit"}""")
                        event("""{"type":"done","status":"updated"}""")
                    } else {
                        if (path.startsWith("/api/updates/software")) softwareWait.await(20, TimeUnit.SECONDS)
                        val body = if (path.startsWith("/api/updates/software")) """{"status":"invalid"}"""
                        else JSONObject().put("current", JSONObject().put("available", true).put("version", version.get()))
                            .put("status", "ok").put("update_available", true).put("latest_version", "new-fixture").toString()
                        val bytes = body.toByteArray()
                        output.write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n".toByteArray()); output.write(bytes); output.flush()
                    }
                } } }
            }
        }
        val visible = mutableStateOf(true)
        val base = "http://127.0.0.1:${server.localPort}"
        val client = SettingsClient(base)
        fun waitUntil(predicate: () -> Boolean) { compose.waitUntil(12_000, predicate) }
        try {
            compose.setContent { if (visible.value) Column(Modifier.verticalScroll(rememberScrollState())) { UpdateSettingsSection(client) {} } else BasicText("模拟对话页面") }
            waitUntil { !manager.dictionaryBusy && manager.local?.optString("version") == "old-fixture" }
            compose.onNodeWithText("检查时刻词典更新").performScrollTo().performClick()
            waitUntil { !manager.dictionaryBusy && manager.dictionary != null }
            compose.onNodeWithText("更新时刻词典").performScrollTo().performClick()
            assertTrue(opened.await(10, TimeUnit.SECONDS))
            waitUntil { manager.dictionaryProgress?.optLong("completed") == 10L }
            compose.runOnIdle { manager.startDownload(base, "0.1.23", "arm64-v8a", "0.1.24", "0".repeat(64)); visible.value = false }
            waitUntil { manager.softwareBusy }
            instrumentation.uiAutomation.executeShellCommand("input keyevent 3").use { descriptor ->
                java.io.FileInputStream(descriptor.fileDescriptor).use { it.readBytes() }
            }
            advance.countDown()
            waitUntil { manager.dictionaryProgress?.optLong("completed") == 50L }
            assertTrue(manager.hasForegroundWork)
            val notifications = context.getSystemService(NotificationManager::class.java)
            waitUntil { notifications.activeNotifications.any { it.id == 4200 } }
            assertTrue(notifications.activeNotifications.first { it.id == 4200 }.notification.flags and android.app.Notification.FLAG_ONGOING_EVENT != 0)
            instrumentation.runOnMainSync { manager.cancel("software") }
            softwareWait.countDown()
            waitUntil { !manager.softwareBusy }
            assertTrue(manager.dictionaryBusy)
            assertEquals(1, requests.get())
            finish.countDown()
            waitUntil { !manager.dictionaryBusy && manager.local?.optString("version") == "new-fixture" }
            waitUntil { notifications.activeNotifications.none { it.id == 4200 } }
            instrumentation.runOnMainSync {
                (context.getSystemService(ActivityManager::class.java)).appTasks.first().moveToFront()
                visible.value = true
            }
            waitUntil { manager.local?.optString("version") == "new-fixture" && !manager.dictionaryBusy }
            assertEquals(1, requests.get())
            assertFalse(manager.hasForegroundWork)
            assertTrue(manager.apkPath.isBlank())
        } finally {
            advance.countDown(); finish.countDown(); softwareWait.countDown()
            instrumentation.runOnMainSync { manager.cancel("software"); manager.cancel("dictionary") }
            server.close(); worker.join(1000)
        }
    }
}
