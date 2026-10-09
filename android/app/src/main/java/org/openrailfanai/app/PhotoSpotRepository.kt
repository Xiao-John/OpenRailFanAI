package org.openrailfanai.app

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** Read-only companion data. Failure must never turn a successful chat into a model error. */
internal class PhotoSpotRepository(private val baseUrl: String) {
    fun snapshot(scope: String?, sources: List<String>): JSONObject {
        val result = JSONObject().put("documents", JSONArray())
        if (!scope.isNullOrBlank()) runCatching {
            get("/api/photo-spots/search?scope=${encode(scope)}&limit=3")
        }.onSuccess { result.put("search", it) }.onFailure { result.put("search_error", "机位卡片暂未加载，原回答仍可阅读。") }
        val documents = result.getJSONArray("documents")
        sources.distinct().take(3).forEach { url ->
            runCatching { document(url) }.onSuccess { doc ->
                if (doc.optString("status") != "not_annotated") documents.put(doc)
            }
        }
        return result
    }

    fun document(url: String): JSONObject = get("/api/photo-spots/documents?url=${encode(url)}")

    /** Recheck the index on every open; no cross-dictionary cached explanations. */
    fun details(url: String, occurrenceId: String?): List<JSONObject> {
        val index = document(url)
        val markers = photoObjects(index.optJSONArray("issue_markers")).filter {
            it.optString("applies_to") == "document" || (occurrenceId == null || it.optString("occurrence_id") == occurrenceId)
        }
        if (markers.isEmpty()) throw PhotoIssueUpdated()
        return markers.map { marker ->
            try { get("/api/photo-spots/issues/${encode(marker.getString("issue_id"))}") }
            catch (e: PhotoHttpException) {
                if (e.code == 404) { document(url); throw PhotoIssueUpdated() }
                throw e
            }
        }
    }

    private fun get(path: String): JSONObject {
        val connection = (URL(baseUrl + path).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"; connectTimeout = 4000; readTimeout = 6000
        }
        return try {
            if (connection.responseCode !in 200..299) throw PhotoHttpException(connection.responseCode)
            connection.inputStream.bufferedReader(Charsets.UTF_8).use { JSONObject(it.readText()) }
        } finally { connection.disconnect() }
    }
    private fun encode(value: String) = URLEncoder.encode(value, "UTF-8")
}
internal class PhotoHttpException(val code: Int) : Exception("HTTP $code")
internal class PhotoIssueUpdated : Exception("说明已更新")
internal fun photoObjects(values: JSONArray?): List<JSONObject> =
    (0 until (values?.length() ?: 0)).mapNotNull { values?.optJSONObject(it) }

internal fun photoScope(slots: JSONArray?): String? = photoObjects(slots)
    .firstOrNull { it.optString("name") == "location" }
    ?.opt("value")?.takeIf { it is String }?.toString()?.trim()?.takeIf { it.isNotBlank() }

internal fun photoLabel(value: String): String = mapOf(
    "park" to "公园", "bridge" to "桥", "roadside" to "路旁", "station_platform" to "车站站台",
    "viewing_platform" to "观景平台", "museum" to "博物馆", "other" to "其他",
    "operating_railway" to "运营铁路", "yard_or_depot" to "车场或车辆基地", "static_rolling_stock" to "静态车辆",
    "mixed" to "多类对象", "railway_remains" to "铁路遗存", "spring" to "春季", "summer" to "夏季",
    "autumn" to "秋季", "winter" to "冬季", "all_year" to "全年", "dawn" to "黎明",
    "morning" to "上午", "noon" to "中午", "afternoon" to "下午", "dusk" to "黄昏",
    "night" to "夜间", "any" to "不限时段", "front" to "顺光", "side" to "侧光", "back" to "逆光", "variable" to "光线变化",
    "walk" to "步行", "bicycle" to "骑行", "car" to "自驾", "bus" to "公交", "metro" to "地铁", "rail" to "铁路",
    "track_intrusion" to "侵入线路风险", "restricted_area" to "限制区域", "road_traffic" to "道路交通风险", "height_or_edge" to "高处或边缘风险", "open" to "来源所述开放", "conditional" to "来源所述有条件进入", "closed" to "来源所述关闭", "prohibited" to "来源所述禁止进入",
    "N" to "北", "NE" to "东北", "E" to "东", "SE" to "东南", "S" to "南", "SW" to "西南", "W" to "西", "NW" to "西北"
)[value] ?: value

/** Explicit whitelist, preserving condition groups and original validity wording. */
internal fun photoClaimRows(claim: JSONObject): List<Pair<String, String>> {
    val value = claim.opt("value")?.takeUnless { it == JSONObject.NULL } ?: return emptyList()
    return when (claim.optString("field")) {
        "location" -> (value as? JSONObject)?.let { location ->
            listOf("province" to "省份", "city" to "城市", "district" to "区域", "address" to "地址", "landmark" to "参照位置").mapNotNull { (key, label) ->
                (location.opt(key) as? String)?.takeIf { it.isNotBlank() }?.let { label to it }
            }
        }.orEmpty()
        "point_type", "view_target", "season", "time_of_day" -> (value as? String)?.let {
            listOf((mapOf("point_type" to "机位类型", "view_target" to "拍摄对象", "season" to "季节", "time_of_day" to "时段").getValue(claim.optString("field"))) to photoLabel(it))
        }.orEmpty()
        // Rail bindings may be objects: do not stringify them into a verified railway name.
        "rail_relation" -> if (claim.optString("state") == "resolved" && value is JSONObject &&
            !value.isNull("entity_id") && value.optString("entity_id").isNotBlank()) {
            val name = value.optString("entity_raw")
            val relation = mapOf("photographed" to "原文拍摄对象", "nearby" to "原文邻近铁路", "access" to "原文交通铁路", "mention" to "原文提及铁路")[value.optString("relation")] ?: "铁路关系"
            if (name.isNotBlank()) listOf(relation to name) else emptyList()
        } else emptyList()
        "time_window" -> (value as? JSONObject)?.let { window ->
            val parts = listOf("start", "end").mapNotNull { (window.opt(it) as? String)?.takeIf(String::isNotBlank) }
            if (parts.isEmpty()) emptyList() else listOf("原文时间段" to (parts.joinToString("–") + listOf("timezone", "qualifier").mapNotNull { (window.opt(it) as? String)?.takeIf(String::isNotBlank) }.joinToString(" · ", prefix = " ").trimEnd()))
        }.orEmpty()
        "light", "camera_direction", "train_direction_raw", "access_mode", "access_instruction_raw", "access_status", "fee_raw", "hazard", "equipment_raw" -> {
            val labels = mapOf("time_window" to "原文时间段", "light" to "光线", "camera_direction" to "拍摄方向", "train_direction_raw" to "原文列车方向", "access_mode" to "交通方式", "access_instruction_raw" to "原文到达说明", "access_status" to "原文进入条件", "fee_raw" to "原文费用", "hazard" to "原文风险提示", "equipment_raw" to "设备建议")
            if (value is String) listOf(labels.getValue(claim.optString("field")) to photoLabel(value)) else emptyList()
        }
        else -> emptyList()
    }
}

internal fun photoClipboardText(snapshot: JSONObject?): String = buildList {
    photoObjects(snapshot?.optJSONObject("search")?.optJSONArray("items")).forEach { item ->
        add(item.optString("name_raw").ifBlank { "机位线索" })
        photoObjects(item.optJSONArray("claims")).forEach { claim ->
            photoClaimRows(claim).forEach { (label, value) -> add("$label：$value") }
            (claim.opt("valid_time_raw") as? String)?.takeIf(String::isNotBlank)?.let { add("原文时效：$it") }
        }
        if (photoObjects(item.optJSONArray("issue_markers")).isNotEmpty()) add("部分信息待确认")
        add("来源：${item.optString("url")}")
    }
}.joinToString("\n")
