package org.openrailfanai.app

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ComposeDisplayFixtureTest {
    @get:Rule val compose = createComposeRule()

    @Test fun errorThenDoneKeepsRetrievedScheduleFacts() {
        val machine = ChatStateMachine()
        val id = machine.begin("G8932", "model-error-with-facts")
        machine.onEvent(id, "error", message = "模型不可用")
        val facts = listOf(TrainScheduleDisplay(scheduleFixture()))
        val completed = machine.complete(id, ChatStreamOutcome(
            answer = "", error = "模型不可用", intent = "schedule", sources = emptyList(),
            displayResults = facts, usage = JSONObject(), latencyMs = 1000.0,
            processLogs = emptyList(), displayResultsJson = "[]",
        ))
        org.junit.Assert.assertTrue(completed)
        assertEquals(ChatPhase.FAILED, machine.state.phase)
        assertEquals(facts, machine.state.results)
    }

    @Test fun fixedScheduleFixtureShowsRouteAndStops() {
        compose.setContent {
            DisplayResultCard(
                TrainScheduleDisplay(scheduleFixture()), ChatUiState(),
                onAction = { _, _, _ -> }, onRetry = {}, onSettings = {},
            )
        }
        compose.onNodeWithText("G8932").assertIsDisplayed()
        compose.onNodeWithText("秦皇岛 → 北京南").assertIsDisplayed()
        compose.onNodeWithText("唐山").assertIsDisplayed()
        compose.onNodeWithText("21:22").assertIsDisplayed()
    }

    @Test fun batchRetryPayloadContainsOnlyFailedTrain() {
        var action: JSONObject? = null
        compose.setContent {
            DisplayResultCard(
                TrainBatchDisplay(BatchScheduleResult("partial", listOf(
                    TrainScheduleDisplay(scheduleFixture().copy(trainCode = "C2203")),
                    TrainScheduleDisplay(scheduleFixture().copy(trainCode = "G8927")),
                    TrainScheduleDisplay(scheduleFixture().copy(trainCode = "G8928", status = "failed")),
                ))), ChatUiState(),
                onAction = { value, _, _ -> action = value }, onRetry = {}, onSettings = {},
            )
        }
        compose.onNodeWithTag("batch-retry-failed").performClick()
        assertEquals("[\"G8928\"]", action?.getJSONArray("trains")?.toString())
    }

    @Test fun storedFailureRetriesItsOwnQueryAfterOpeningHistory() {
        var retried: String? = null
        val original = "查一下 G8932 今天的时刻表"
        compose.setContent {
            MainChatScreen(
                status = "", connected = true, chatState = ChatUiState(query = "另一条当前问题"),
                messages = listOf(StoredMessage("assistant", "", JSONObject()
                    .put("query", original).put("error", "网络连接失败").put("errorCategory", "network"))),
                input = "另一份草稿", onInputChange = {}, onOpenHistory = {}, onNewConversation = {},
                onSubmit = {}, onStop = {}, onReadingChange = {}, onAction = { _, _, _ -> },
                onRetry = { throw AssertionError("不能使用其他请求的重试闭包") }, onSettings = {},
                onRetryQuery = { retried = it },
            )
        }
        compose.onNodeWithTag("connection-retry").performScrollTo().performClick()
        assertEquals(original, retried)
    }

    @Test fun stoppedAnswerIsRenderedOnceAfterPersistence() {
        compose.setContent {
            MainChatScreen(
                status = "", connected = true,
                chatState = ChatUiState(phase = ChatPhase.STOPPED, answer = "已返回内容"),
                messages = listOf(StoredMessage("assistant", "已返回内容", JSONObject().put("stopped", true))),
                input = "", onInputChange = {}, onOpenHistory = {}, onNewConversation = {},
                onSubmit = {}, onStop = {}, onReadingChange = {}, onAction = { _, _, _ -> },
                onRetry = {}, onSettings = {},
            )
        }
        compose.onNodeWithText("已返回内容").assertIsDisplayed()
        assertEquals(1, compose.onAllNodesWithText("已返回内容").fetchSemanticsNodes().size)
    }

    @Test fun emptyErrorAndDetailsFixturesExposeTheirIntendedControls() {
        compose.setContent {
            androidx.compose.foundation.lazy.LazyColumn {
                item {
                    DisplayResultCard(EmptyDisplay(EmptyResult("empty", "2026-09-25", "CR400BF-5033", emptyList(), listOf("https://rail.re"))), ChatUiState(),
                        onAction = { _, _, _ -> }, onRetry = {}, onSettings = {})
                }
                item {
                    DisplayResultCard(ErrorDisplay(ErrorResult("failed", "invalid key", "auth", "model")), ChatUiState(),
                        onAction = { _, _, _ -> }, onRetry = {}, onSettings = {})
                }
                item {
                    DisplayResultCard(TrainScheduleDisplay(scheduleFixture()), ChatUiState(latencyMs = 5800.0, usageJson = "{\"total_tokens\":1555}", processLogs = listOf("fixed log")),
                        onAction = { _, _, _ -> }, onRetry = {}, onSettings = {})
                }
            }
        }
        compose.onNodeWithTag("routing-empty").assertIsDisplayed()
        compose.onNodeWithTag("connection-error").performScrollTo().assertIsDisplayed()
        compose.onNodeWithTag("query-details").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("API Key").assertIsDisplayed()
    }

    private fun scheduleFixture() = ScheduleResult(
        status = "success", trainCode = "G8932", date = "2026-09-25", fromStation = "秦皇岛", toStation = "北京南",
        startTime = "20:42", arriveTime = "22:34", duration = "1小时52分", scheduleType = "图定时刻",
        timeBasis = "reference", todayTimesAvailable = true, sampleData = true,
        stops = listOf(
            ScheduleStop("1", "秦皇岛", "--", "20:42", null),
            ScheduleStop("2", "唐山", "21:10", "21:22", "停12分钟"),
        ),
        sources = listOf("https://kyfw.12306.cn/otn/czxx/queryByTrainNo"), error = null,
    )
}
