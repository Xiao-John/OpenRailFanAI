package org.openrailfanai.app

import androidx.compose.foundation.layout.Column
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextClearance
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.compose.ui.test.onAllNodesWithText
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class ComposeHistoryScreenTest {
    @get:Rule val compose = createComposeRule()

    @Test fun searchRenameDeleteCancelAndSelectUseExistingSessions() {
        val dir = File(InstrumentationRegistry.getInstrumentation().targetContext.cacheDir, "history-ui-${System.nanoTime()}").apply { mkdirs() }
        try {
            val store = ConversationStore(dir, isolated = true)
            val session = store.create()
            store.addUser(session.id, "查询 G8932 列车时刻")
            var selectedId = ""
            compose.setContent {
                var revision by remember { mutableIntStateOf(0) }
                Column {
                    HistoryScreen(store, store.currentId(), revision, {}, {}, { selectedId = it },
                        { id, title -> store.rename(id, title); revision++ },
                        { id -> store.delete(id); revision++ }, {}, {})
                }
            }
            compose.onAllNodesWithText("G8932", substring = true, useUnmergedTree = true).get(0).assertIsDisplayed()
            compose.onNodeWithTag("history-search").performTextInput("不存在")
            compose.onNodeWithText("没有匹配的对话").assertIsDisplayed()
            compose.onNodeWithTag("history-search").performTextClearance()
            compose.onNodeWithTag("history-more-${session.id}", useUnmergedTree = true).performScrollTo().performClick()
            compose.onNodeWithText("重命名").performClick()
            compose.onNodeWithTag("history-rename").performTextClearance()
            compose.onNodeWithTag("history-rename").performTextInput("调试会话")
            compose.onNodeWithText("保存").performClick()
            compose.onNodeWithText("调试会话").assertIsDisplayed()
            compose.onNodeWithTag("history-more-${session.id}", useUnmergedTree = true).performScrollTo().performClick()
            compose.onNodeWithTag("history-delete-menu").performClick()
            compose.onNodeWithText("取消").performClick()
            assertEquals(1, store.all().size)
            compose.onNodeWithText("调试会话").performClick()
            assertEquals(session.id, selectedId)
            compose.onNodeWithTag("history-more-${session.id}", useUnmergedTree = true).performScrollTo().performClick()
            compose.onNodeWithTag("history-delete-menu").performClick()
            compose.onNodeWithTag("history-delete-confirm").performClick()
            assertTrue(store.all().none { it.id == session.id })
        } finally { dir.deleteRecursively() }
    }
}
