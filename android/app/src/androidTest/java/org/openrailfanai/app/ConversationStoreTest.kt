package org.openrailfanai.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class ConversationStoreTest {
    @Test fun tokenTotalsRemainIsolatedAndSurviveMessageTrimming() {
        val dir = createTempDir(prefix = "usage-")
        try {
            val store = ConversationStore(dir, isolated = true)
            val first = store.create().id
            fun usage(tokens: Long) = JSONObject().put("usage", JSONObject().put("total_tokens", tokens))
            store.addAssistant(first, "回复", usage(100))
            val second = store.create().id
            assertEquals(0L, store.current()?.totalTokens)
            store.addAssistant(second, "另一回复", usage(7))
            repeat(125) { store.addAssistant(first, "回复$it", usage(1)) }
            store.select(first)
            assertEquals(225L, store.current()?.totalTokens)
            assertEquals(120, store.current()?.messages?.size)
            val restored = ConversationStore(dir, isolated = true)
            assertEquals(225L, restored.current()?.totalTokens)
            restored.select(second)
            assertEquals(7L, restored.current()?.totalTokens)
            assertEquals(null, replyTokenUsage(JSONObject()))
        } finally { dir.deleteRecursively() }
    }

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

    @Test fun draftsSurviveSwitchingAndStoreRecreation() {
        val dir = createTempDir(prefix = "drafts-")
        try {
            val store = ConversationStore(dir, isolated = true)
            val first = store.create().id
            store.saveDraft(first, "明天 G1 南京南到上海虹桥")
            val second = store.create().id
            store.saveDraft(second, "另一个未发送问题")
            store.select(first)
            assertEquals("明天 G1 南京南到上海虹桥", store.draft(first))
            assertEquals("另一个未发送问题", ConversationStore(dir, true).draft(second))
            store.saveDraft(first, "")
            assertEquals("", store.draft(first))
            assertEquals("另一个未发送问题", store.draft(second))
        } finally { dir.deleteRecursively() }
    }

    @Test fun newConversationReusesOnlyPendingSessionAndPreservesDraft() {
        val dir = createTempDir(prefix = "pending-")
        try {
            val store = ConversationStore(dir, true)
            val first = store.pendingOrCreate()
            store.saveDraft(first.id, "未发送草稿")
            repeat(5) { assertEquals(first.id, store.pendingOrCreate().id) }
            assertEquals(1, store.all().size)
            assertEquals("未发送草稿", store.draft(first.id))
            store.addUser(first.id, "第一个问题")
            val pending = store.pendingOrCreate()
            store.select(first.id)
            assertEquals(pending.id, store.pendingOrCreate().id)
            assertEquals(2, store.all().size)
        } finally { dir.deleteRecursively() }
    }

    @Test fun errorCategoriesRequireSpecificEvidence() {
        assertEquals("configuration", mainErrorCategory("未配置 API Key"))
        assertEquals("configuration", mainErrorCategory("拒绝该 base_url：非公网地址"))
        assertEquals("network", mainErrorCategory("连接失败，请检查网络、密钥及接口地址"))
        assertEquals("auth", mainErrorCategory("鉴权失败(HTTP 401)：API Key 无效"))
        assertEquals("service", mainErrorCategory("请检查 API Key 与模型配置"))
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
