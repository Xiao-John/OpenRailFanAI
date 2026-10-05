package org.openrailfanai.app

import androidx.compose.ui.test.junit4.createAndroidComposeRule
import com.chaquo.python.Python
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId

class EmbeddedRailwayDateTest {
    @get:Rule val compose = createAndroidComposeRule<MainComposeActivity>()
    @Test fun embeddedDateLookupDoesNotRequireIanaData() {
        compose.waitUntil(30_000) { Python.isStarted() }
        val dates = Python.getInstance().getModule("app.dates")
        val tomorrow = LocalDate.now(ZoneId.of("Asia/Shanghai")).plusDays(1).toString()
        assertEquals(tomorrow, dates.callAttr("future_railway_date", "明天").toString())
        assertEquals("", dates.callAttr("future_railway_date", "今天").toString())
        assertEquals(tomorrow, dates.callAttr("normalize_date", "明天").toString())
    }
}
