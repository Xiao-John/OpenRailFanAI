package org.openrailfanai.app

import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.unit.dp
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.text.font.FontWeight
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class ComposeMarkdownTest {
    @get:Rule val compose = createComposeRule()

    @Test fun fareAnswerRendersBoldAndNativeTable() {
        compose.setContent { UsabilityCaptureRoot {
            Column(Modifier.fillMaxSize().padding(16.dp)) {
                MarkdownAnswer("""**G1 次（北京南 → 上海虹桥）票价**

（2026-10-05）

| 席别 | 票价 |
|---|---:|
| 二等座 | 795 元 |
| 一等座 | 1272 元 |
| 商务座 | 2782 元 |

该车次在票价数据中对应时刻为06:30–11:24，历时04:54。

说明：以上仅为**票价信息**，并不表示当前有余票。

[12306](https://www.12306.cn)""")
            }
        } }
        compose.onNodeWithTag("markdown-table").assertIsDisplayed()
        compose.onNodeWithText("二等座").assertIsDisplayed()
        compose.onNodeWithText("795 元").assertIsDisplayed()
        val title = compose.onNodeWithText("G1 次（北京南 → 上海虹桥）票价").fetchSemanticsNode().config[SemanticsProperties.Text].first()
        assertTrue(title.spanStyles.any { it.item.fontWeight == FontWeight.Bold })
        compose.onNodeWithText("|---|---:|").assertDoesNotExist()
        compose.onNodeWithTag("usability-capture-root").logUsabilityCapture("markdown-fare-answer")
    }

    @Test fun streamingPartialSyntaxCanBecomeACompleteTable() {
        val answer = mutableStateOf("**正在查")
        compose.setContent { UsabilityCaptureRoot { MarkdownAnswer(answer.value) } }
        compose.runOnIdle { answer.value = "**查询结果**\n\n|席别|票价|\n|---|---|\n|二等座|795元|" }
        compose.onNodeWithText("查询结果").assertIsDisplayed()
        compose.onNodeWithText("795元").assertIsDisplayed()
        compose.onNodeWithTag("markdown-table").assertIsDisplayed()
    }

    @Test fun formattedLinksUseNativeHandlerAndUnsafeLinksAreNotActive() {
        val opened = mutableListOf<String>()
        compose.setContent {
            CompositionLocalProvider(LocalUriHandler provides object : UriHandler {
                override fun openUri(uri: String) { opened.add(uri) }
            }) { MarkdownAnswer("[12306](https://www.12306.cn)") }
        }
        compose.onNodeWithText("12306").performClick()
        assertEquals(listOf("https://www.12306.cn"), opened)
        assertNull(MarkdownDocument.safeLink("javascript:alert(1)"))
        assertNull(MarkdownDocument.safeLink("file:///secret"))
        assertNotNull(MarkdownDocument.safeLink("https://www.12306.cn"))
    }

    @Test fun internalDateFailureDoesNotBlameTheModel() {
        var settings = 0
        compose.setContent { UsabilityCaptureRoot {
            DisplayResultCard(ErrorDisplay(ErrorResult("failed", "服务内部异常（ZoneInfoNotFoundError），请稍后重试", "service", null)),
                ChatUiState(), { _, _, _ -> }, {}, { settings++ })
        } }
        compose.onNodeWithText("查询服务暂时异常").assertIsDisplayed()
        compose.onNodeWithText("暂时无法连接模型服务").assertDoesNotExist()
        compose.onNodeWithText("查看错误详情").performClick()
        compose.onNodeWithText("服务内部异常（ZoneInfoNotFoundError），请稍后重试").assertIsDisplayed()
        assertEquals(0, settings)
        compose.onNodeWithTag("usability-capture-root").logUsabilityCapture("internal-date-error")
    }
}
