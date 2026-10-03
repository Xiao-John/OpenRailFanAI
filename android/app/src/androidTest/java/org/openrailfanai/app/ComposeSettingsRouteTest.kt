package org.openrailfanai.app

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ComposeSettingsRouteTest {
    @get:Rule val compose = createAndroidComposeRule<MainComposeActivity>()

    @Test fun settingsRouteLoadsFromNativeChatScreen() {
        compose.onNodeWithContentDescription("对话历史").performClick()
        compose.onNodeWithText("模型与设置").performClick()
        compose.waitUntil(30_000) {
            compose.onAllNodesWithText("云端模型").fetchSemanticsNodes().isNotEmpty()
        }
        compose.onNodeWithText("云端模型").assertIsDisplayed()
        compose.onNodeWithText("外观").assertIsDisplayed()
        compose.onNodeWithText("系统操作").assertIsDisplayed()
    }
}
