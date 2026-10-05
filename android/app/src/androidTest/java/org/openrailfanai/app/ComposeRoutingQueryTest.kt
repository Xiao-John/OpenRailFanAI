package org.openrailfanai.app

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class ComposeRoutingQueryTest {
    @get:Rule val compose = createComposeRule()
    @Test fun trainQueryShowsBothCoupledUnitsAndQueriesTheActualTrain() {
        val result = DisplayResultParser.parseArray(org.json.JSONArray().put(JSONObject("""{"schema_version":1,"kind":"emu_routing","status":"success","query":"G1","query_kind":"train","focus_date":"2026-10-04","records":[{"train_code":"G1","date":"2026-10-04","time":"06:30","coupled":true,"units":[{"emu_no_display":"CR400BF-5033"},{"emu_no_display":"CR400BF-5034"}]}]}"""))).single()
        var payload = JSONObject()
        compose.setContent { UsabilityCaptureRoot {
            DisplayResultCard(result, ChatUiState(), { action, _, _ -> payload = action }, {}, {})
        } }
        compose.onNodeWithText("担当车组").assertIsDisplayed()
        compose.onNodeWithText("CR400BF-5033 + CR400BF-5034").assertIsDisplayed()
        compose.onNodeWithText("重联").assertIsDisplayed()
        compose.onNodeWithTag("routing-record-G1").performClick()
        assertEquals("G1", payload.getJSONArray("trains").getString(0))
        assertEquals("2026-10-04", payload.getString("date"))
        compose.onNodeWithTag("usability-capture-root").logUsabilityCapture("train-assignment-coupled")
    }
    @Test fun recentRecordRetainsItsDateWhenOpeningTimetable() {
        val result = DisplayResultParser.parseArray(org.json.JSONArray().put(JSONObject("""{"schema_version":1,"kind":"emu_routing","status":"success","query":"CR400BF5033","query_kind":"emu","records":[{"train_code":"G1","date":"2026-10-03","time":"06:30","units":[{"emu_no_display":"CR400BF-5033"}]}]}"""))).single()
        var payload = JSONObject()
        compose.setContent { UsabilityCaptureRoot {
            DisplayResultCard(result, ChatUiState(), { action, _, _ -> payload = action }, {}, {})
        } }
        compose.onNodeWithText("2026-10-03", substring = true).assertIsDisplayed()
        compose.onNodeWithTag("routing-record-G1").performClick()
        assertEquals("2026-10-03", payload.getString("date"))
        compose.onNodeWithTag("routing-batch").assertDoesNotExist()
        compose.onNodeWithTag("usability-capture-root").logUsabilityCapture("routing-recent-records")
    }
}
