package org.openrailfanai.app

import androidx.compose.foundation.layout.Column
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.click
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextClearance
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTouchInput
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
            val row = compose.onNodeWithTag("history-select-${session.id}", useUnmergedTree = true).fetchSemanticsNode().boundsInRoot
            val title = compose.onNodeWithTag("history-title-${session.id}", useUnmergedTree = true).fetchSemanticsNode().boundsInRoot
            assertEquals(row.center.y, title.center.y, 1f)
            compose.onNodeWithTag("history-search").performTextInput("不存在")
            compose.onNodeWithText("没有匹配的对话").assertIsDisplayed()
            compose.onNodeWithTag("history-search").performTextClearance()
            compose.onNodeWithTag("history-more-${session.id}", useUnmergedTree = true).performScrollTo().performClick()
            val panel = compose.onNodeWithTag("history-actions-panel").fetchSemanticsNode().boundsInRoot
            val drawer = compose.onNodeWithTag("history-content").fetchSemanticsNode().boundsInRoot
            assertTrue("操作菜单应小于侧栏宽度", panel.width < drawer.width * .8f)
            compose.onNodeWithTag("history-actions-dismiss").performTouchInput { click(androidx.compose.ui.geometry.Offset(4f, 4f)) }
            compose.onNodeWithTag("history-more-${session.id}", useUnmergedTree = true).performClick()
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
    @Test fun drawerNavigationAndScrimHaveWorkingCallbacks() {
        val dir = File(InstrumentationRegistry.getInstrumentation().targetContext.cacheDir, "history-drawer-${System.nanoTime()}").apply { mkdirs() }
        try {
            val store = ConversationStore(dir, isolated = true)
            var closed = 0
            var created = 0
            var settings = 0
            var help = 0
            compose.setContent {
                HistoryScreen(store, store.currentId(), 0, { closed++ }, { created++ }, {},
                    { _, _ -> }, {}, { settings++ }, { help++ })
            }
            compose.onNodeWithTag("history-create").performClick()
            compose.onNodeWithTag("history-settings-hit-target").performClick()
            compose.onNodeWithTag("history-help-hit-target").performClick()
            compose.onNodeWithTag("history-back-control").performClick()
            // Touch the exposed edge, not the panel lying above the full-screen scrim.
            compose.onNodeWithTag("history-drawer-scrim").performTouchInput {
                click(androidx.compose.ui.geometry.Offset(width - 2f, height / 2f))
            }
            assertEquals(1, created)
            assertEquals(1, settings)
            assertEquals(1, help)
            assertEquals(2, closed)
        } finally { dir.deleteRecursively() }
    }

}
