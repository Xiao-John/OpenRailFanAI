package org.openrailfanai.app

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.*
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import java.util.concurrent.atomic.AtomicInteger

class ComposePhotoSpotTest {
    @get:Rule val compose = createComposeRule()
    private fun snapshot() = JSONObject("""{"search":{"available":true,"total":1,"shown":1,"truncated":false,"items":[{"occurrence_id":"opaque-occurrence","name_raw":"笋岗桥","url":"https://example.org/guide","review_status":"needs_review","claims":[{"field":"location","value":{"city":"深圳"},"group_id":null},{"field":"point_type","value":"bridge","group_id":null}],"issue_markers":[{"issue_id":"opaque-issue","applies_to":"occurrence"}]}]},"documents":[]}""")
    private fun issue() = JSONObject("""{"issue_id":"opaque-issue","occurrence_id":"opaque-occurrence","title":"铁路实体待确认","note":"测试说明：保留原名，不猜测线路。","url":"https://example.org/guide","source_status":"current","evidence":[{"quote":"测试原文依据"}]}""")
    private fun waitFor(text: String) = compose.waitUntil(8000) { compose.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty() }

    @Test fun pendingIsLazyAndClosingKeepsDraft() {
        val calls = AtomicInteger()
        var draft by mutableStateOf("未发送草稿")
        compose.setContent { UsabilityCaptureRoot {
            Column(Modifier.fillMaxSize().background(NativeColors.background).padding(15.appDp), verticalArrangement = Arrangement.spacedBy(12.appDp)) {
                Row(Modifier.fillMaxWidth()) { Spacer(Modifier.width(50.appDp)); PhotoSpotResults(snapshot(), null, Modifier.weight(1f), loadDetails = { calls.incrementAndGet(); listOf(issue()) }) }
                BasicTextField(draft, { draft = it }, Modifier.testTag("photo-test-draft"))
            }
        } }
        compose.onNodeWithText("笋岗桥").assertIsDisplayed()
        compose.onNodeWithText("深圳").assertIsDisplayed()
        compose.onNodeWithText("铁路实体待确认").assertDoesNotExist()
        assertEquals(0, calls.get())
        assertTrue(compose.onNodeWithTag("photo-pending").fetchSemanticsNode().boundsInRoot.height >= 44f)
        compose.onNodeWithTag("usability-capture-root").logUsabilityCapture("photo-spot-default")
        compose.onNodeWithText("待确认").performClick()
        waitFor("铁路实体待确认")
        assertEquals(1, calls.get())
        compose.onNodeWithText("测试原文依据").assertDoesNotExist()
        compose.onNodeWithText("查看原文依据").performClick()
        compose.onNodeWithText("测试原文依据").assertIsDisplayed()
        compose.onNodeWithTag("photo-issue-close").performClick()
        compose.onNodeWithTag("photo-issue-dialog").assertDoesNotExist()
        assertEquals("未发送草稿", draft)
        compose.onNodeWithText("笋岗桥").assertIsDisplayed()
    }

    @Test fun detailFailureIsLocalAndCanRetry() {
        val calls = AtomicInteger()
        compose.setContent { UsabilityCaptureRoot {
            PhotoSpotResults(snapshot(), null, Modifier.padding(15.appDp), loadDetails = {
                if (calls.incrementAndGet() == 1) throw PhotoIssueUpdated()
                listOf(issue(), issue().put("issue_id", "document-issue").put("occurrence_id", JSONObject.NULL).put("title", "来源整体说明"))
            })
        } }
        compose.onNodeWithText("待确认").performClick()
        waitFor("说明已更新，请重新查看来源。")
        compose.onNodeWithText("重新加载").performClick()
        waitFor("来源整体说明")
        compose.onNodeWithText("本机位").assertIsDisplayed()
        compose.onNodeWithText("整篇攻略").assertIsDisplayed()
        compose.onNodeWithTag("photo-issue-close").performClick()
        compose.onNodeWithText("笋岗桥").assertIsDisplayed()
        assertEquals(2, calls.get())
    }

    @Test fun oldDictionaryAndStaleSourcesDoNotClaimNoSpots() {
        val data = JSONObject("""{"search":{"available":false,"items":[]},"documents":[{"status":"stale","url":"https://example.org/changed","issue_markers":[]},{"status":"unreviewed","url":"https://example.org/new","issue_markers":[]}]}""")
        compose.setContent { UsabilityCaptureRoot { Column(Modifier.padding(15.appDp).verticalScroll(rememberScrollState())) { PhotoSpotResults(data, null) } } }
        compose.onNodeWithText("原文已变化，旧标注暂不作为已确认信息。").assertIsDisplayed()
        compose.onNodeWithText("尚未复核，保留原攻略阅读。").assertIsDisplayed()
        compose.onNodeWithText("旧词典未提供标注，继续阅读原攻略。").performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("没有机位").assertDoesNotExist()
    }

    @Test fun realBackendSearchAndLazyDetails() {
        // Host backend with real packaged dictionary, forwarded by the acceptance script.
        val repo = PhotoSpotRepository("http://127.0.0.1:8017")
        val data = repo.snapshot("笋岗桥", emptyList())
        val items = photoObjects(data.getJSONObject("search").getJSONArray("items"))
        assertEquals(1, items.size)
        val item = items.first()
        assertEquals("笋岗桥", item.getString("name_raw"))
        assertTrue(photoObjects(item.getJSONArray("claims")).flatMap(::photoClaimRows).contains("城市" to "深圳"))
        assertFalse(photoObjects(item.getJSONArray("claims")).any { it.optString("field") == "rail_relation" })
        val details = repo.details(item.getString("url"), item.getString("occurrence_id"))
        assertTrue(details.any { it.optString("field") == "rail_relation" })
        assertTrue(details.any { it.isNull("occurrence_id") })
        assertEquals("深圳", photoScope(JSONArray("""[{"name":"location","value":"深圳"}]""")))
        assertNull(photoScope(JSONArray("""[{"name":"target","value":"CR400AF"}]""")))
    }
    @Test fun persistedReplyShowsCardAndCopiesItsFacts() {
        var copied = ""
        val meta = JSONObject().put("intent", "photo_spot").put("photoSpots", snapshot()).put("query", "深圳机位")
        compose.setContent { UsabilityCaptureRoot {
            MainChatScreen(status = "查询完成", connected = true, chatState = ChatUiState(),
                messages = listOf(StoredMessage("assistant", "", meta)), input = "未发送草稿", onInputChange = {},
                onOpenHistory = {}, onNewConversation = {}, onSubmit = {}, onStop = {}, onReadingChange = {},
                onAction = { _, _, _ -> }, onRetry = {}, onSettings = {}, onCopy = { copied = it }, applySystemInsets = false, autoScrollToLatest = false)
        } }
        compose.onNodeWithText("笋岗桥").assertIsDisplayed()
        compose.onNodeWithTag("result-copy-action").performClick()
        assertTrue(copied.contains("城市：深圳"))
        assertTrue(copied.contains("来源：https://example.org/guide"))
        assertTrue(copied.contains("部分信息待确认"))
        compose.onNodeWithTag("main-assistant-avatar").assertIsDisplayed()
    }

}
