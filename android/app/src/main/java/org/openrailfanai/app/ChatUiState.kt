package org.openrailfanai.app

import java.util.UUID

enum class ChatPhase { IDLE, CONNECTING, STREAMING, COMPLETED, FAILED, STOPPED }

data class ChatUiState(
    val requestId: String? = null,
    val query: String = "",
    val phase: ChatPhase = ChatPhase.IDLE,
    val stage: String = "",
    val recognized: String = "",
    val answer: String = "",
    val results: List<DisplayResult> = emptyList(),
    val error: String? = null,
    val intent: String? = null,
    val sources: List<String> = emptyList(),
    val usageJson: String = "{}",
    val latencyMs: Double? = null,
    val processLogs: List<String> = emptyList(),
    val reading: Boolean = false,
) {
    val busy: Boolean get() = phase == ChatPhase.CONNECTING || phase == ChatPhase.STREAMING
    val terminal: Boolean get() = phase == ChatPhase.COMPLETED || phase == ChatPhase.FAILED || phase == ChatPhase.STOPPED
}

/** Request-id guarded reducer: late callbacks can never replace a newer conversation state. */
class ChatStateMachine(initial: ChatUiState = ChatUiState()) {
    var state: ChatUiState = initial
        private set
    private var preserveResultsOnComplete = false

    fun reset() {
        state = ChatUiState()
        preserveResultsOnComplete = false
    }

    fun begin(query: String, id: String = UUID.randomUUID().toString(), preserveResults: Boolean = false): String {
        check(!state.busy) { "An active request must be stopped before another request starts" }
        preserveResultsOnComplete = preserveResults
        state = ChatUiState(requestId = id, query = query, phase = ChatPhase.CONNECTING,
            results = if (preserveResults) state.results else emptyList())
        return id
    }

    fun onEvent(id: String, type: String, delta: String = "", text: String = "", message: String = "", stage: String = "", recognized: String = ""): Boolean {
        if (!accepts(id)) return false
        state = when (type) {
            "stage" -> state.copy(phase = ChatPhase.STREAMING, stage = stage, recognized = recognized.ifBlank { state.recognized })
            "answer" -> state.copy(phase = ChatPhase.STREAMING, answer = state.answer + delta)
            "replace" -> state.copy(phase = ChatPhase.STREAMING, answer = text)
            // An SSE error is followed by done (which can still contain useful
            // structured results). The HTTP outcome owns terminal completion.
            "error" -> state.copy(phase = ChatPhase.STREAMING, error = message.ifBlank { "查询失败" })
            else -> state
        }
        return true
    }

    fun complete(id: String, outcome: ChatStreamOutcome): Boolean {
        if (!accepts(id)) return false
        state = state.copy(
            phase = if (outcome.error == null) ChatPhase.COMPLETED else ChatPhase.FAILED,
            answer = outcome.answer,
            results = if (preserveResultsOnComplete) mergeResults(state.results, outcome.displayResults) else outcome.displayResults,
            error = outcome.error,
            intent = outcome.intent,
            sources = outcome.sources,
            usageJson = outcome.usage.toString(),
            latencyMs = outcome.latencyMs,
            processLogs = outcome.processLogs,
        )
        preserveResultsOnComplete = false
        return true
    }

    fun fail(id: String, message: String): Boolean {
        if (!accepts(id)) return false
        state = state.copy(phase = ChatPhase.FAILED, error = message)
        return true
    }

    fun stop(id: String): Boolean {
        if (state.requestId != id || !state.busy) return false
        state = state.copy(phase = ChatPhase.STOPPED)
        return true
    }

    fun setReading(reading: Boolean) { state = state.copy(reading = reading) }

    private fun accepts(id: String): Boolean = state.requestId == id && state.busy

    private fun mergeResults(previous: List<DisplayResult>, incoming: List<DisplayResult>): List<DisplayResult> {
        fun flatten(items: List<DisplayResult>) = items.flatMap {
            if (it is TrainBatchDisplay) it.value.items else listOf(it)
        }
        val oldItems = flatten(previous)
        val newItems = flatten(incoming)
        val merged = LinkedHashMap<String, DisplayResult>()
        (oldItems + newItems).forEachIndexed { index, result ->
            val code = when (result) {
                is TrainScheduleDisplay -> result.value.trainCode
                else -> null
            }
            merged[code?.uppercase() ?: "#${index}"] = result
        }
        val items = merged.values.toList()
        val wasBatch = previous.any { it is TrainBatchDisplay } || incoming.any { it is TrainBatchDisplay }
        if (!wasBatch) return items
        val batchStatus = if (items.any { it is TrainScheduleDisplay && it.value.status != "success" }) "partial" else "success"
        return listOf(TrainBatchDisplay(BatchScheduleResult(batchStatus, items)))
    }
}
