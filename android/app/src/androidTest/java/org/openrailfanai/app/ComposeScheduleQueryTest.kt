package org.openrailfanai.app

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.isEnabled
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Rule
import org.junit.Test
import org.junit.Assume.assumeTrue
import org.junit.runner.RunWith

/** One explicit end-to-end query verifies the first Compose → device backend → schedule result path. */
@RunWith(AndroidJUnit4::class)
class ComposeScheduleQueryTest {
    @get:Rule val compose = createAndroidComposeRule<MainComposeActivity>()

    @Test fun trainScheduleQueryRendersStructuredBackendResult() {
        assumeTrue("Live railway lookup is opt-in; repeatable visual fixtures never call external sources.",
            InstrumentationRegistry.getArguments().getString("liveRailwayQuery") == "true")
        compose.waitUntil(timeoutMillis = 30_000) {
            compose.onAllNodes(hasTestTag("main-submit-control") and isEnabled()).fetchSemanticsNodes().isNotEmpty()
        }
        compose.onNodeWithContentDescription("消息输入框").performTextInput("G8932 经停哪些站")
        compose.onNodeWithTag("main-submit-control").performClick()
        compose.waitUntil(timeoutMillis = 120_000) {
            compose.onAllNodesWithTag("schedule-G8932-success").fetchSemanticsNodes().isNotEmpty()
        }
        // Dismiss the software keyboard before scrolling the LazyColumn; otherwise the
        // IME can leave the result node semantically present but outside the visible area.
        InstrumentationRegistry.getInstrumentation().sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK)
        compose.waitForIdle()
        compose.onNodeWithTag("schedule-G8932-success").performScrollTo().assertIsDisplayed()
    }
}
