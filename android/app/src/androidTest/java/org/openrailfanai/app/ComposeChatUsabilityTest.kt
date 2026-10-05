package org.openrailfanai.app

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeDown
import org.junit.Assert.assertEquals
import org.json.JSONObject
import org.json.JSONArray
import java.time.LocalDate
import java.time.ZoneId
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class ComposeChatUsabilityTest {
    @get:Rule val compose = createComposeRule()

    @Test fun networkErrorDoesNotAccuseTheApiKey() {
        compose.setContent {
            UsabilityCaptureRoot {
                DisplayResultCard(ErrorDisplay(ErrorResult("failed", "连接失败，请检查网络、密钥及接口地址", "auth", null)),
                    ChatUiState(), { _, _, _ -> }, {}, {})
            }
        }
        compose.onNodeWithTag("api-key-error").assertDoesNotExist()
        compose.onNodeWithText("检查模型设置").assertIsDisplayed()
    }

    @Test fun emptyChatExamplesFillDraftAndSettingsReplacesBack() {
        var settings = 0
        var submits = 0
        compose.setContent {
            UsabilityCaptureRoot {
                var draft by remember { mutableStateOf("") }
                MainChatScreen("", true, ChatUiState(), emptyList(), draft,
                    { draft = it }, {}, {}, { submits++ }, {}, {}, { _, _, _ -> }, {},
                    { settings++ }, onFollowup = { draft = it }, applySystemInsets = false)
            }
        }
        compose.onNodeWithTag("chat-welcome").assertIsDisplayed()
        compose.onNodeWithTag("usability-capture-root").logUsabilityCapture("chat-empty-home")
        compose.onNodeWithTag("welcome-查时刻").performClick()
        compose.onNodeWithTag("main-input-field").assertTextContains("G1 今天的时刻表")
        assertEquals(0, submits)
        compose.onNodeWithContentDescription("设置").performClick()
        assertEquals(1, settings)
        compose.onNodeWithTag("main-back-control").assertDoesNotExist()
    }

    @Test fun longFinalMessageReturnsToBottomAndHidesReadingControl() {
        compose.setContent {
            var state by remember { mutableStateOf(ChatUiState(phase = ChatPhase.COMPLETED)) }
            MainChatScreen("", true, state,
                listOf(StoredMessage("assistant", (1..100).joinToString("\n") { "第${it}行列车查询内容" }, null)),
                "继续查询", {}, {}, {}, {}, {}, { state = state.copy(reading = it) },
                { _, _, _ -> }, {}, {}, applySystemInsets = false)
        }
        compose.waitForIdle()
        compose.onNodeWithTag("return-to-bottom").assertDoesNotExist()
        val listBefore = compose.onNodeWithTag("main-message-list").fetchSemanticsNode().boundsInRoot
        compose.onNodeWithTag("main-message-list").performTouchInput { swipeDown() }
        compose.waitForIdle()
        val listReading = compose.onNodeWithTag("main-message-list").fetchSemanticsNode().boundsInRoot
        val floatingButton = compose.onNodeWithTag("return-to-bottom").fetchSemanticsNode().boundsInRoot
        assertEquals(listBefore.height, listReading.height, 1f)
        assertTrue(floatingButton.width < listReading.width * .75f)
        assertTrue(floatingButton.left > listReading.left && floatingButton.right < listReading.right)
        compose.onNodeWithTag("return-to-bottom").assertIsDisplayed().performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("return-to-bottom").assertDoesNotExist()
        compose.onNodeWithTag("main-send-icon", useUnmergedTree = true).assertIsDisplayed()
    }
    private fun scheduleMessage(date: String): StoredMessage = StoredMessage("assistant", "",
        JSONObject().put("displayResults", JSONArray().put(JSONObject()
            .put("schema_version", 1).put("kind", "train_schedule").put("status", "success")
            .put("train_code", "G1").put("date", date).put("from_station", "北京南")
            .put("to_station", "上海虹桥").put("stops", JSONArray()))))

    @Test fun futureScheduleHidesAssignmentAndPreservesDateInFareDraft() {
        val date = LocalDate.now(ZoneId.of("Asia/Shanghai")).plusDays(1).toString()
        var draft = ""
        compose.setContent {
            MainChatScreen("", true, ChatUiState(), listOf(scheduleMessage(date)), "", {}, {}, {}, {}, {}, {},
                { _, _, _ -> }, {}, {}, onFollowup = { draft = it }, applySystemInsets = false)
        }
        compose.onNodeWithText("查担当车组").assertDoesNotExist()
        compose.onNodeWithText("查票价").performClick()
        assertTrue(draft.contains(date))
        assertTrue(draft.contains("G1"))
    }

    @Test fun oldScheduleDoesNotKeepSuggestionsOnUnrelatedAnswer() {
        compose.setContent {
            MainChatScreen("", true, ChatUiState(), listOf(scheduleMessage("2026-09-25"), StoredMessage("assistant", "新的普通回答", null)),
                "", {}, {}, {}, {}, {}, {}, { _, _, _ -> }, {}, {}, applySystemInsets = false)
        }
        compose.onNodeWithTag("result-suggestions").assertDoesNotExist()
    }

    @Test fun plainReplySupportsCopyRegenerateAndShare() {
        var copied = ""
        var shared = ""
        var regenerated = ""
        compose.setContent { UsabilityCaptureRoot {
            MainChatScreen("", true, ChatUiState(phase = ChatPhase.COMPLETED),
                listOf(StoredMessage("user", "介绍一下高铁", null), StoredMessage("assistant", "**高铁**采用高速列车。", null)),
                "", {}, {}, {}, {}, {}, {}, { _, _, _ -> }, {}, {},
                onCopy = { copied = it }, onShare = { shared = it }, onRegenerate = { regenerated = it },
                applySystemInsets = false)
        } }
        compose.onNodeWithTag("result-copy-action").assertIsDisplayed().performClick()
        compose.onNodeWithTag("result-share-action").assertIsDisplayed().performClick()
        compose.onNodeWithTag("result-regenerate-action").assertIsDisplayed().performClick()
        assertEquals("**高铁**采用高速列车。", copied)
        assertEquals(copied, shared)
        assertEquals("介绍一下高铁", regenerated)
        compose.onNodeWithTag("usability-capture-root").logUsabilityCapture("plain-reply-actions")
    }

    @Test fun loadingAndStreamingKeepTheSameReplyAvatar() {
        val state = mutableStateOf(ChatUiState(phase = ChatPhase.STREAMING, stage = "正在连接 12306"))
        compose.setContent { UsabilityCaptureRoot {
            MainChatScreen("", true, state.value, emptyList(), "", {}, {}, {}, {}, {}, {}, { _, _, _ -> }, {}, {}, applySystemInsets = false)
        } }
        compose.onNodeWithText("正在检索相关资料…").assertIsDisplayed()
        val initial = compose.onNodeWithTag("main-live-assistant-avatar").fetchSemanticsNode().boundsInRoot
        compose.runOnIdle { state.value = state.value.copy(stage = "retrieve") }
        compose.onNodeWithText("正在生成回复…").assertIsDisplayed()
        compose.runOnIdle { state.value = state.value.copy(answer = "第一段回答") }
        compose.waitForIdle()
        compose.onNodeWithText("第一段回答").assertIsDisplayed()
        val streaming = compose.onNodeWithTag("main-live-assistant-avatar").fetchSemanticsNode().boundsInRoot
        assertEquals(initial.left, streaming.left, 1f)
        assertEquals(initial.top, streaming.top, 1f)
        compose.onNodeWithTag("usability-capture-root").logUsabilityCapture("streaming-reply-avatar")
    }

}
