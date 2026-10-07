package org.openrailfanai.app

import org.json.JSONArray
import org.json.JSONObject

data class ChatMessage(val role: String, val content: String) {
    fun toJson() = JSONObject().put("role", role).put("content", content)
}

data class ChatStreamRequest(
    val message: String,
    val history: List<ChatMessage> = emptyList(),
    val sessionId: String,
    val displayAction: JSONObject? = null,
    val llmSpec: JSONObject? = null,
    val clientCapabilities: List<String> = emptyList(),
) {
    fun toJson(): JSONObject = JSONObject()
        .put("message", message)
        .put("history", JSONArray().also { array -> history.forEach { array.put(it.toJson()) } })
        .put("session_id", sessionId)
        .put("client_capabilities", JSONArray().also { array -> clientCapabilities.distinct().forEach { array.put(it) } })
        .also { body ->
            displayAction?.let { body.put("display_action", it) }
            llmSpec?.let { spec ->
                val keys = spec.keys()
                while (keys.hasNext()) { val key = keys.next(); body.put(key, spec.opt(key)) }
            }
        }
}

data class ScheduleStop(
    val stationNo: String?,
    val station: String?,
    val arriveTime: String?,
    val startTime: String?,
    val stopoverTime: String?,
)

data class ScheduleResult(
    val status: String,
    val trainCode: String?,
    val date: String?,
    val fromStation: String?,
    val toStation: String?,
    val startTime: String?,
    val arriveTime: String?,
    val duration: String?,
    val scheduleType: String?,
    val timeBasis: String?,
    val todayTimesAvailable: Boolean?,
    val sampleData: Boolean,
    val stops: List<ScheduleStop>,
    val sources: List<String>,
    val error: String?,
)

data class BatchScheduleResult(val status: String, val items: List<DisplayResult>)
data class RoutingResult(val status: String, val query: String?, val focusDate: String?, val timeSemantics: String?, val records: List<JSONObject>, val sources: List<String>, val sampleData: Boolean, val queryKind: String? = null)
data class EmptyResult(val status: String, val date: String?, val query: String?, val historicalRecords: List<JSONObject>, val sources: List<String>)
data class ErrorResult(val status: String, val message: String?, val category: String?, val tool: String?)
data class UnsupportedResult(val kind: String?, val status: String?, val schemaVersion: Int?)
data class FarePrice(val seat: String, val amount: String?, val currency: String)
data class AvailabilitySeat(val seat: String, val availability: String?, val rawValue: String?, val status: String)
data class FareAvailability(val status: String, val seats: List<AvailabilitySeat>, val sources: List<String>,
    val fetchedAt: String?, val error: String?, val note: String?, val startTime: String?, val arriveTime: String?,
    val duration: String?, val timeDiscrepancy: List<String>)
data class FareResult(val status: String, val trainCode: String?, val date: String?,
    val fromStation: String?, val toStation: String?, val startTime: String?, val arriveTime: String?,
    val duration: String?, val prices: List<FarePrice>, val sources: List<String>,
    val fetchedAt: String?, val error: String?, val note: String, val fareBasis: String? = null,
    val fareStatus: String? = null, val fareError: String? = null, val availability: FareAvailability? = null) {
    val priceStatus: String get() = fareStatus ?: status
    val priceError: String? get() = if (fareStatus == null) error else fareError
}

sealed interface DisplayResult
data class TrainScheduleDisplay(val value: ScheduleResult) : DisplayResult
data class TrainBatchDisplay(val value: BatchScheduleResult) : DisplayResult
data class RoutingDisplay(val value: RoutingResult) : DisplayResult
data class EmptyDisplay(val value: EmptyResult) : DisplayResult
data class ErrorDisplay(val value: ErrorResult) : DisplayResult
data class UnsupportedDisplay(val value: UnsupportedResult) : DisplayResult
data class TicketFareDisplay(val value: FareResult) : DisplayResult

data class ChatStreamOutcome(
    val answer: String,
    val error: String?,
    val intent: String?,
    val sources: List<String>,
    val displayResults: List<DisplayResult>,
    val usage: JSONObject,
    val latencyMs: Double?,
    val processLogs: List<String>,
    val displayResultsJson: String,
)

object DisplayResultParser {
    const val SCHEMA_VERSION = 1

    fun parseArray(array: JSONArray?): List<DisplayResult> = buildList {
        if (array == null) return@buildList
        for (index in 0 until array.length()) {
            val item = array.optJSONObject(index) ?: continue
            add(parse(item))
        }
    }

    private fun parse(item: JSONObject): DisplayResult {
        val kind = item.optString("kind").takeIf(String::isNotEmpty)
        val status = item.optString("status").takeIf(String::isNotEmpty)
        val version = if (item.has("schema_version")) item.optInt("schema_version", -1) else SCHEMA_VERSION
        if (version != SCHEMA_VERSION) return UnsupportedDisplay(UnsupportedResult(kind, status, version))
        return when (kind) {
            "train_schedule" -> TrainScheduleDisplay(schedule(item))
            "ticket_fare" -> fare(item)?.let(::TicketFareDisplay)
                ?: UnsupportedDisplay(UnsupportedResult(kind, status, version))
            "train_schedule_batch" -> TrainBatchDisplay(
                BatchScheduleResult(status ?: "unknown", parseArray(item.optJSONArray("items")))
            )
            "emu_routing" -> RoutingDisplay(
                RoutingResult(status ?: "unknown", item.string("query"), item.string("focus_date"),
                    item.string("time_semantics"), item.objectArray("records"), item.stringArray("sources"), item.optBoolean("sample_data"), item.string("query_kind"))
            )
            "empty" -> EmptyDisplay(
                EmptyResult(status ?: "unknown", item.string("date"), item.string("query"), item.objectArray("historical_records"), item.stringArray("sources"))
            )
            "error" -> ErrorDisplay(ErrorResult(status ?: "failed", item.string("message"), item.string("category"), item.string("tool")))
            else -> UnsupportedDisplay(UnsupportedResult(kind, status, version))
        }
    }

    private fun fare(item: JSONObject): FareResult? {
        val status = item.string("status")?.takeIf { it in setOf("success", "partial", "empty", "failed") } ?: return null
        val fields = listOf("train_code", "date", "from_station", "to_station", "start_time", "arrive_time", "duration", "fetched_at", "error", "note", "fare_basis", "fare_error")
        if (fields.any { item.has(it) && !item.isNull(it) && item.opt(it) !is String }) return null
        val values = item.optJSONArray("prices") ?: return null
        val prices = buildList {
            for (i in 0 until values.length()) {
                val price = values.optJSONObject(i) ?: return null
                val seat = (price.opt("seat") as? String)?.takeIf(String::isNotBlank) ?: return null
                val currency = (price.opt("currency") as? String)?.takeIf(String::isNotBlank) ?: return null
                val amount = if (price.isNull("amount")) null else price.opt("amount") as? String ?: return null
                if (amount != null && !Regex("[0-9]+(?:\\.[0-9]+)?").matches(amount)) return null
                add(FarePrice(seat, amount, currency))
            }
        }
        val singleStatuses = setOf("success", "partial", "empty", "failed", "not_requested")
        val fareStatus = if (item.has("fare_status")) item.string("fare_status")?.takeIf { it in singleStatuses } ?: return null else null
        val priceStatus = fareStatus ?: status
        if (priceStatus in setOf("empty", "failed", "not_requested") && prices.isNotEmpty()) return null
        if (priceStatus == "success" && (prices.isEmpty() || prices.any { it.amount == null })) return null
        val availability = if (item.has("availability") && !item.isNull("availability")) {
            val raw = item.optJSONObject("availability") ?: return null
            val state = raw.string("status")?.takeIf { it in singleStatuses } ?: return null
            if (listOf("fetched_at", "error", "note", "start_time", "arrive_time", "duration").any {
                raw.has(it) && !raw.isNull(it) && raw.opt(it) !is String
            }) return null
            val rows = raw.optJSONArray("seats") ?: return null
            val seats = buildList {
                for (i in 0 until rows.length()) {
                    val row = rows.optJSONObject(i) ?: return null
                    val seat = (row.opt("seat") as? String)?.takeIf(String::isNotBlank) ?: return null
                    val seatStatus = row.string("status")?.takeIf { it in setOf("available", "unavailable", "waitlist", "unknown") } ?: return null
                    for (key in listOf("availability", "raw_value")) {
                        val v = row.opt(key)
                        if (v != null && v != JSONObject.NULL && v !is String && v !is Number) return null
                    }
                    add(AvailabilitySeat(seat, row.string("availability"), row.string("raw_value"), seatStatus))
                }
            }
            if (state in setOf("empty", "failed", "not_requested") && seats.isNotEmpty()) return null
            FareAvailability(state, seats, raw.stringArray("sources"), raw.string("fetched_at"), raw.string("error"),
                raw.string("note"), raw.string("start_time"), raw.string("arrive_time"), raw.string("duration"), raw.stringArray("time_discrepancy"))
        } else null
        return FareResult(status, item.string("train_code"), item.string("date"), item.string("from_station"),
            item.string("to_station"), item.string("start_time"), item.string("arrive_time"), item.string("duration"),
            prices, item.stringArray("sources"), item.string("fetched_at"), item.string("error"), item.string("note").orEmpty(), item.string("fare_basis"), fareStatus, item.string("fare_error"), availability)
    }

    private fun schedule(item: JSONObject): ScheduleResult = ScheduleResult(
        status = item.optString("status", "unknown"),
        trainCode = item.string("train_code"), date = item.string("date"),
        fromStation = item.string("from_station"), toStation = item.string("to_station"),
        startTime = item.string("start_time"), arriveTime = item.string("arrive_time"),
        duration = item.string("duration"), scheduleType = item.string("schedule_type"),
        timeBasis = item.string("time_basis"),
        todayTimesAvailable = if (item.has("today_times_available") && !item.isNull("today_times_available")) item.optBoolean("today_times_available") else null,
        sampleData = item.optBoolean("sample_data"),
        stops = item.optJSONArray("stops").objectArray().map { stop ->
            ScheduleStop(stop.string("station_no"), stop.string("station"), stop.string("arrive_time"),
                stop.string("start_time"), stop.string("stopover_time"))
        },
        sources = item.stringArray("sources"),
        error = item.string("error"),
    )

    private fun JSONObject.string(key: String): String? = opt(key)?.takeUnless { it == JSONObject.NULL }?.toString()?.takeIf(String::isNotEmpty)

    private fun JSONObject.objectArray(key: String): List<JSONObject> = optJSONArray(key).objectArray()
    private fun JSONObject.stringArray(key: String): List<String> = optJSONArray(key).stringArray()

    private fun JSONArray?.objectArray(): List<JSONObject> {
        if (this == null) return emptyList()
        return (0 until length()).mapNotNull { optJSONObject(it) }
    }
}

private fun JSONArray?.stringArray(): List<String> {
    if (this == null) return emptyList()
    return (0 until length()).mapNotNull { optString(it).takeIf(String::isNotBlank) }
}

private fun JSONObject.string(key: String): String? = opt(key)?.takeUnless { it == JSONObject.NULL }?.toString()?.takeIf(String::isNotEmpty)
private fun JSONObject.objectArray(key: String): List<JSONObject> {
    val array = optJSONArray(key) ?: return emptyList()
    return (0 until array.length()).mapNotNull { array.optJSONObject(it) }
}
