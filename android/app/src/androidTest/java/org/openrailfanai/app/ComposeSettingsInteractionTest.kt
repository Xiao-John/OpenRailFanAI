package org.openrailfanai.app

import androidx.compose.ui.test.assertIsOff
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeLeft
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.net.ServerSocket
import java.nio.charset.StandardCharsets
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread

/** Actual settings controls backed by controlled HTTP, without a real credential or cloud call. */
class ComposeSettingsInteractionTest {
    @get:Rule val compose = createComposeRule()

    @Test fun providerPickerConnectionFeedbackAndSessionOnlySave() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val dir = File(context.cacheDir, "settings-ui-${System.nanoTime()}").apply { mkdirs() }
        val repo = MainSettingsRepository(dir, isolated = true)
        val returned = AtomicBoolean(false)
        SettingsHttpFixture().use { fixture ->
            try {
                compose.setContent {
                    UsabilityCaptureRoot {
                        MainSettingsScreen(repo, SettingsClient(fixture.url), onBack = { throw AssertionError("保存不应调用返回回调") },
                            onCopy = {}, onPaste = { "" }, keyError = false, onKeyEdited = {},
                            onShare = {}, onExport = {}, onOpenUrl = {}, onThemeChanged = {}, onSaved = { returned.set(true) })
                    }
                }
                compose.waitUntil(10_000) { fixture.catalogRequests.get() > 0 }
                compose.onNodeWithText("添加模型提供商").performClick()
                compose.waitUntil(10_000) { compose.onAllNodesWithText("第二提供商").fetchSemanticsNodes().isNotEmpty() }
                compose.onNodeWithText("第一提供商").assertIsDisplayed()
                compose.onNodeWithText("第二提供商").assertIsDisplayed()
                compose.onNodeWithText("添加自定义提供商").assertIsDisplayed()
                compose.onNodeWithText("第二提供商").performClick()
                compose.onNodeWithTag("usability-capture-root").logUsabilityCapture("settings-selected-provider")
                compose.onNodeWithContentDescription("API Key输入框").performScrollTo().performTextInput("temporary-fixture-key")
                fixture.delayNextTest.set(true)
                compose.onNodeWithText("测试连接").performScrollTo().performClick()
                compose.waitUntil(10_000) { fixture.testRequests.get() == 1 }
                compose.onNodeWithText("正在测试…").assertIsNotEnabled()
                compose.onNodeWithText("保存").assertIsNotEnabled()
                fixture.releaseTest.countDown()
                compose.waitUntil(10_000) { compose.onAllNodesWithText("连接成功，可以使用当前模型。").fetchSemanticsNodes().isNotEmpty() }
                compose.onNodeWithText("连接成功，可以使用当前模型。").performScrollTo().assertIsDisplayed()
                compose.onNodeWithTag("usability-capture-root").logUsabilityCapture("settings-connection-success")
                assertEquals("temporary-fixture-key", fixture.lastTestBody.get()?.getString("api_key"))
                assertEquals("second", fixture.lastTestBody.get()?.getString("provider"))
                fixture.succeed.set(false)
                compose.onNodeWithText("测试连接").performScrollTo().performClick()
                compose.waitUntil(10_000) { compose.onAllNodesWithText("连接失败，请检查密钥、模型和接口地址。").fetchSemanticsNodes().isNotEmpty() }
                compose.onNodeWithText("连接失败，请检查密钥、模型和接口地址。").performScrollTo().assertIsDisplayed()
                compose.onNodeWithTag("usability-capture-root").logUsabilityCapture("settings-connection-failure")
                compose.onNodeWithText("记住 API Key").performScrollTo().assertIsOff().performClick().assertIsOn()
                compose.onNodeWithTag("remember-key-switch", useUnmergedTree = true).performTouchInput { swipeLeft() }
                compose.onNodeWithText("记住 API Key").assertIsOff()
                compose.onNodeWithText("高级设置").performScrollTo().performClick()
                compose.onNodeWithText("API 方言：自动识别").performScrollTo().performClick()
                compose.onNodeWithText("Responses").performScrollTo()
                compose.onNodeWithTag("usability-capture-root").logUsabilityCapture("settings-api-options")
                compose.onNodeWithText("Responses").performClick()
                compose.onNodeWithText("API 方言：Responses").assertIsDisplayed().performClick()
                compose.onNodeWithText("自动识别").performScrollTo().performClick()
                compose.onNodeWithText("主题：跟随系统").performScrollTo().performClick()
                compose.onNodeWithText("浅色").performScrollTo()
                compose.onNodeWithTag("usability-capture-root").logUsabilityCapture("settings-theme-options")
                compose.onNodeWithText("浅色").performClick()
                compose.onNodeWithText("主题：浅色").assertIsDisplayed()
                compose.onNodeWithText("添加模型提供商").performScrollTo().performClick()
                compose.onNodeWithText("第一提供商").performClick()
                compose.onNodeWithTag("provider-card-second").performScrollTo().performClick()
                compose.onNodeWithTag("usability-capture-root").logUsabilityCapture("settings-provider-cards")
                compose.onNodeWithTag("provider-edit-second").performClick()
                compose.onNodeWithContentDescription("API Key输入框").performScrollTo().assertIsDisplayed()
                compose.onNodeWithText("保存").performClick()
                compose.waitUntil(5_000) { returned.get() }
                assertEquals("temporary-fixture-key", MainSettingsRepository(dir, isolated = true).requestLlmSpec()?.getString("api_key"))
                assertFalse(repo.rememberKey())
                assertFalse(File(dir, "state.json").readText().contains("temporary-fixture-key"))
                assertFalse(File(dir, "secrets.json").readText().contains("temporary-fixture-key"))
            } finally { dir.deleteRecursively() }
        }
    }
}

private class SettingsHttpFixture : AutoCloseable {
    private val socket = ServerSocket(0)
    val url = "http://127.0.0.1:${socket.localPort}"
    val catalogRequests = AtomicInteger()
    val testRequests = AtomicInteger()
    val succeed = AtomicBoolean(true)
    val delayNextTest = AtomicBoolean(false)
    val releaseTest = CountDownLatch(1)
    val lastTestBody = AtomicReference<JSONObject?>()
    private val worker = thread(isDaemon = true, name = "settings-http-fixture") {
        while (!socket.isClosed) {
            runCatching {
                socket.accept().use { connection ->
                    connection.soTimeout = 10_000
                    val input = connection.getInputStream().bufferedReader(StandardCharsets.UTF_8)
                    val request = input.readLine().orEmpty()
                    var length = 0
                    while (true) {
                        val header = input.readLine() ?: break
                        if (header.isEmpty()) break
                        if (header.startsWith("Content-Length:", ignoreCase = true)) length = header.substringAfter(':').trim().toInt()
                    }
                    val body = CharArray(length)
                    var read = 0
                    while (read < length) {
                        val amount = input.read(body, read, length - read)
                        if (amount < 0) break
                        read += amount
                    }
                    val response = if (request.contains("/api/providers/test")) {
                        lastTestBody.set(JSONObject(String(body, 0, read)))
                        testRequests.incrementAndGet()
                        if (delayNextTest.getAndSet(false)) releaseTest.await(10, TimeUnit.SECONDS)
                        if (succeed.get()) """{"ok":true}""" else """{"ok":false,"error":"controlled authentication failure"}"""
                    } else {
                        catalogRequests.incrementAndGet()
                        """{"providers":[{"id":"first","label":"第一提供商","base_url":"https://first.invalid/v1","model":"fixture-model"},{"id":"second","label":"第二提供商","base_url":"https://second.invalid/v1","model":"fixture-model"}]}"""
                    }
                    val bytes = response.toByteArray(StandardCharsets.UTF_8)
                    connection.getOutputStream().use { output ->
                        output.write("HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n".toByteArray(StandardCharsets.US_ASCII))
                        output.write(bytes)
                    }
                }
            }
        }
    }
    override fun close() { releaseTest.countDown(); socket.close(); worker.join(1_000) }
}
