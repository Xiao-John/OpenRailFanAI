package org.openrailfanai.app

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class ComposeMotionTest {
    @get:Rule val compose = createComposeRule()

    @Test fun revealSettlesAndRemovesContentAfterExit() {
        val visible = mutableStateOf(false)
        compose.setContent {
            RailReveal(visible.value) {
                Box(Modifier.size(120.dp, 48.dp).testTag("motion-content")) { BasicText("展开选项") }
            }
        }
        compose.onNodeWithTag("motion-content").assertDoesNotExist()
        compose.mainClock.autoAdvance = false
        compose.runOnIdle { visible.value = true }
        compose.mainClock.advanceTimeBy(350)
        compose.onNodeWithTag("motion-content").assertIsDisplayed()
        compose.runOnIdle { visible.value = false }
        compose.mainClock.advanceTimeBy(350)
        compose.onNodeWithTag("motion-content").assertDoesNotExist()
        compose.mainClock.autoAdvance = true
    }

    @Test fun entryDoesNotChangeFinalLayoutOrRestartOnRecomposition() {
        val title = mutableStateOf("原始页面")
        compose.setContent { Box(Modifier.size(120.dp, 48.dp).railEntrance().testTag("entry")) { BasicText(title.value) } }
        val before = compose.onNodeWithTag("entry").fetchSemanticsNode().boundsInRoot
        compose.runOnIdle { title.value = "更新页面" }
        compose.onNodeWithText("更新页面").assertIsDisplayed()
        val after = compose.onNodeWithTag("entry").fetchSemanticsNode().boundsInRoot
        assertEquals(before, after)
    }
}
