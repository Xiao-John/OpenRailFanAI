package org.openrailfanai.app

import android.app.Activity
import android.content.Intent
import android.view.KeyEvent
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.isEnabled
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import androidx.test.runner.lifecycle.ActivityLifecycleCallback
import java.util.concurrent.atomic.AtomicBoolean
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/** Exercise the installed launcher, production service, navigation and real IME. */
class MainLauncherRouteTest {
    @get:Rule val compose = createEmptyComposeRule()

    private fun resumedNativeActivity(): MainComposeActivity? {
        var activity: MainComposeActivity? = null
        InstrumentationRegistry.getInstrumentation().runOnMainSync {
            activity = ActivityLifecycleMonitorRegistry.getInstance()
                .getActivitiesInStage(Stage.RESUMED).filterIsInstance<MainComposeActivity>().singleOrNull()
        }
        return activity
    }

    private fun bottomInsets(activity: Activity): Pair<Int, Int> {
        var result = 0 to 0
        InstrumentationRegistry.getInstrumentation().runOnMainSync {
            val view = activity.window.decorView
            val insets = ViewCompat.getRootWindowInsets(view)
            result = view.height to maxOf(
                insets?.getInsets(WindowInsetsCompat.Type.ime())?.bottom ?: 0,
                insets?.getInsets(WindowInsetsCompat.Type.systemBars())?.bottom ?: 0,
            )
        }
        return result
    }

    @Test fun mainLauncherOpensNativeAndComposerTracksRealKeyboard() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        assertTrue(context.resources.getBoolean(R.bool.main_native_ui))
        val intent = Intent(context, MainActivity::class.java)
            .setAction(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val launcher = ActivityScenario.launch<MainActivity>(intent)
        var native: MainComposeActivity? = null
        try {
            compose.waitUntil(30_000) {
                native = resumedNativeActivity()
                native != null && compose.onAllNodes(hasTestTag("main-submit-control"))
                    .fetchSemanticsNodes().isNotEmpty()
            }
            compose.onNodeWithContentDescription("对话历史").performClick()
            compose.onNodeWithText("模型与设置").performClick()
            compose.waitUntil(30_000) {
                compose.onAllNodesWithText("云端模型").fetchSemanticsNodes().isNotEmpty()
            }
            compose.onNodeWithText("云端模型").assertIsDisplayed()
            compose.onNodeWithText("返回").performClick()
            compose.onNodeWithTag("history-back-control").performClick()

            val before = compose.onNodeWithTag("main-fixed-input").fetchSemanticsNode().boundsInWindow
            compose.onNodeWithTag("main-input-field").performClick().performTextInput("键盘适配检查草稿")
            compose.waitUntil(10_000) { bottomInsets(native!!).second > 100 }
            compose.waitUntil(10_000) {
                val bounds = compose.onNodeWithTag("main-fixed-input").fetchSemanticsNode().boundsInWindow
                val (height, inset) = bottomInsets(native!!)
                bounds.bottom <= height - inset + 2 && bounds.bottom < before.bottom - 100
            }
            instrumentation.sendKeyDownUpSync(KeyEvent.KEYCODE_BACK)
            compose.waitUntil(10_000) {
                val bounds = compose.onNodeWithTag("main-fixed-input").fetchSemanticsNode().boundsInWindow
                kotlin.math.abs(bounds.bottom - before.bottom) <= 2
            }
            compose.onNodeWithTag("main-input-field").assertTextContains("键盘适配检查草稿")
            val existing = native
            val redirected = AtomicBoolean(false)
            val callback = ActivityLifecycleCallback { activity, stage ->
                if (activity is MainActivity && stage == Stage.DESTROYED) redirected.set(true)
            }
            instrumentation.runOnMainSync {
                ActivityLifecycleMonitorRegistry.getInstance().addLifecycleCallback(callback)
                existing!!.startActivity(Intent(context, MainActivity::class.java)
                    .setAction(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
            try {
                compose.waitUntil(10_000) { redirected.get() && resumedNativeActivity() === existing }
                compose.onNodeWithTag("main-input-field").assertTextContains("键盘适配检查草稿")
            } finally {
                instrumentation.runOnMainSync {
                    ActivityLifecycleMonitorRegistry.getInstance().removeLifecycleCallback(callback)
                }
            }
            val currentId = ConversationStore(context).currentId()
            compose.onNodeWithContentDescription("对话历史").performClick()
            compose.onNodeWithTag("history-select-$currentId").performClick()
            compose.onNodeWithTag("main-input-field").assertTextContains("键盘适配检查草稿")
            compose.onNodeWithContentDescription("新建对话").performClick()
            compose.onNodeWithContentDescription("对话历史").performClick()
            compose.onNodeWithTag("history-select-$currentId").performClick()
            compose.onNodeWithTag("main-input-field").assertTextContains("键盘适配检查草稿")
        } finally {
            instrumentation.runOnMainSync { native?.finish() }
            launcher.close()
        }
    }
}
