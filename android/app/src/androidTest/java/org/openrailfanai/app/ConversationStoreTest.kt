package org.openrailfanai.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class ConversationStoreTest {
    @Test fun oldSessionFormatSurvivesAndManagementKeepsUnknownFields() {
        val dir = createTempDir(prefix = "sessions-")
        try {
            val original = """{"other":"keep","railfan_current_conv_v1":"legacy","railfan_conversations_v1":"[{\"id\":\"legacy\",\"title\":\"旧会话\",\"titleAuto\":false,\"createdAt\":10,\"updatedAt\":11,\"extra\":\"preserve\",\"messages\":[{\"role\":\"assistant\",\"content\":\"历史回答\",\"meta\":{\"displayResults\":[{\"kind\":\"train_schedule\",\"train_code\":\"G8932\"}]}}]}]"}"""
            File(dir, "state.json").writeText(original)
            val store = ConversationStore(dir, isolated = true)
            assertEquals("旧会话", store.current()?.title)
            store.addUser("legacy", "查询 G8932 经停站")
            store.addAssistant("legacy", "结果", JSONObject().put("intent", "列车时刻查询"))
            store.rename("legacy", "保留的名称")
            val saved = JSONObject(File(dir, "state.json").readText())
            val updated = JSONArrayHelper.first(saved.getString("railfan_conversations_v1"))
            assertEquals("keep", saved.getString("other"))
            assertEquals("preserve", updated.getString("extra"))
            assertEquals("G8932", updated.getJSONArray("messages").getJSONObject(0).getJSONObject("meta").getJSONArray("displayResults").getJSONObject(0).getString("train_code"))
            assertEquals("保留的名称", updated.getString("title"))
            assertTrue(File(dir, "state.json.pre-compose-sessions.bak").isFile)
        } finally { dir.deleteRecursively() }
    }

    @Test fun titleSearchGroupingAndDeleteFlowAreDeterministic() {
        val dir = createTempDir(prefix = "sessions-")
        try {
            val store = ConversationStore(dir, isolated = true)
            val created = store.create()
            store.addUser(created.id, "查询 CR400BF-5033 今日交路")
            assertTrue(store.search("5033").isNotEmpty())
            assertTrue(store.current()?.title?.endsWith("…") == true)
            val other = store.create()
            store.select(created.id)
            assertEquals(created.id, store.currentId())
            store.delete(created.id)
            assertTrue(store.all().any { it.id == other.id })
            assertTrue(store.currentId().isNotBlank())
        } finally { dir.deleteRecursively() }
    }
}

private object JSONArrayHelper {
    fun first(text: String): JSONObject = JSONObject().put("v", org.json.JSONArray(text)).getJSONArray("v").getJSONObject(0)
}
