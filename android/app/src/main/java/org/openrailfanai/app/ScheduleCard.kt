package org.openrailfanai.app

import android.app.DatePickerDialog
import android.content.Intent
import android.net.Uri
import org.json.JSONArray
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.Alignment
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.PlatformTextStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.LineHeightStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.json.JSONObject
import java.time.LocalDate

private val RailInk get() = NativeColors.ink
private val RailMuted get() = NativeColors.muted
private val RailLine get() = NativeColors.line
private val RailAccent get() = NativeColors.blue
private val RailPanel get() = NativeColors.panel

@Composable
fun QueryLoadingCard(recognized: String?, stage: String, modifier: Modifier = Modifier) {
    Column(
        modifier.fillMaxWidth().background(RailPanel, RoundedCornerShape(14.appDp))
            .border(1.appDp, RailLine, RoundedCornerShape(14.appDp)).testTag("query-loading")
            .padding(start = 14.appDp, end = 14.appDp, top = 16.appDp, bottom = 16.appDp),
        verticalArrangement = Arrangement.spacedBy(24.appDp),
    ) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.appDp)) {
            Canvas(Modifier.width(28.appDp).height(28.appDp).testTag("query-loading-spinner")) {
                drawDesignGlyph(NativeDesignPaths.querySpinner)
            }
                Column(Modifier.testTag("query-loading-title-and-stage"), verticalArrangement = Arrangement.spacedBy(1.appDp)) {
                BasicText(queryLoadingTitle(stage), Modifier.testTag("query-loading-title"), style = TextStyle(color = RailInk, fontWeight = FontWeight.Bold, fontSize = 16.appSp))
                BasicText(listOfNotNull(recognized?.takeIf(String::isNotBlank)?.let { "已识别 $it" }, queryLoadingCaption(stage)).joinToString(" · "), Modifier.testTag("query-loading-stage"), style = TextStyle(color = RailMuted, fontSize = 14.appSp, letterSpacing = .05.appSp))
            }
        }
        Column(Modifier.testTag("query-loading-skeletons"), verticalArrangement = Arrangement.spacedBy(10.appDp)) {
            Spacer(Modifier.fillMaxWidth().height(16.appDp).background(RailLine, RoundedCornerShape(8.appDp)).testTag("query-loading-skeleton-1"))
            Spacer(Modifier.fillMaxWidth(.74f).height(16.appDp).background(RailLine, RoundedCornerShape(8.appDp)).testTag("query-loading-skeleton-2"))
        }
    }
}

@Composable
fun DisplayResultCard(
    result: DisplayResult,
    request: ChatUiState,
    onAction: (JSONObject, String, Boolean) -> Unit,
    onRetry: () -> Unit,
    onSettings: () -> Unit,
    modifier: Modifier = Modifier,
    datePicker: ((LocalDate, (LocalDate) -> Unit) -> Unit)? = null,
) {
    when (result) {
        is TrainScheduleDisplay -> ScheduleCard(result.value, request, modifier)
        is TrainBatchDisplay -> BatchCard(result.value, request, onAction, modifier)
        is RoutingDisplay -> RoutingCard(result.value, request, onAction, modifier.padding(top = 5.appDp))
        is EmptyDisplay -> EmptyCard(result.value, onAction, modifier, datePicker)
        is ErrorDisplay -> ConnectionErrorCard(result.value, onRetry, onSettings, modifier)
        is UnsupportedDisplay -> InfoCard("暂不支持此结果格式", "旧版或未知结果保留为普通回答。", modifier)
    }
}

@Composable
private fun ScheduleCard(value: ScheduleResult, request: ChatUiState, modifier: Modifier) {
    Column(
        modifier.fillMaxWidth().border(1.appDp, RailLine, RoundedCornerShape(16.appDp))
            .background(NativeColors.surface, RoundedCornerShape(16.appDp)).testTag("schedule-${value.trainCode ?: "unknown"}-${value.status}")
            .padding(start = 12.appDp, end = 12.5.appDp, top = 15.appDp, bottom = 11.5.appDp),
    ) {
        Row(Modifier.fillMaxWidth().padding(start = 5.25.appDp, end = 1.9.appDp).heightIn(min = 31.appDp).testTag("schedule-title-row"), horizontalArrangement = Arrangement.SpaceBetween) {
            NativeLineText(value.trainCode ?: "列车时刻", 23.appSp, Modifier.testTag("schedule-title"), style = TextStyle(color = RailInk, fontSize = 27.appSp, lineHeight = 23.appSp, lineHeightStyle = LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.None, LineHeightStyle.Mode.Fixed), platformStyle = PlatformTextStyle(includeFontPadding = false), fontWeight = FontWeight.Bold))
            val badges = listOfNotNull(value.scheduleType?.takeIf(String::isNotBlank), "示例数据".takeIf { value.sampleData })
            if (badges.isNotEmpty()) BasicText(badges.joinToString(" · "), Modifier.testTag("schedule-classification"), style = TextStyle(color = RailMuted, fontSize = 13.appSp))
        }
        val route = listOfNotNull(value.fromStation, value.toStation).joinToString(" → ")
        if (route.isNotEmpty()) Row(Modifier.fillMaxWidth().padding(start = 5.25.appDp, end = 1.9.appDp).heightIn(min = 30.appDp).testTag("schedule-route-summary")) {
            NativeLineText(route, 26.appSp, Modifier.testTag("schedule-route"), style = TextStyle(color = RailInk, fontSize = 20.5.appSp, letterSpacing = .5.appSp, lineHeight = 26.appSp, lineHeightStyle = LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.None, LineHeightStyle.Mode.Fixed), platformStyle = PlatformTextStyle(includeFontPadding = false), fontWeight = FontWeight.Medium))
        }
        if (value.date != null || value.duration != null) {
            Spacer(Modifier.height(7.appDp))
            Row(Modifier.fillMaxWidth().padding(start = 4.25.appDp).heightIn(min = 37.appDp).testTag("schedule-meta"), horizontalArrangement = Arrangement.spacedBy(10.appDp)) {
            value.date?.let { MetaPill("calendar", dateLabel(it), "schedule-date") }
            value.duration?.let { MetaPill("clock", it, "schedule-duration") }
            }
        }
        Spacer(Modifier.height(13.appDp))
        if (value.status == "failed") {
            InfoCard("查询失败", value.error.orEmpty())
        } else if (value.status == "empty") {
            BasicText("未找到可显示的站点信息", style = TextStyle(color = RailMuted))
        } else {
            Column(Modifier.fillMaxWidth().padding(start = 0.5.appDp, end = 1.appDp).testTag("schedule-stations")) {
                Row(Modifier.fillMaxWidth().heightIn(min = 36.appDp).background(RailPanel, RoundedCornerShape(8.appDp)), verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.weight(132f)) {
                        BasicText("车站", Modifier.align(Alignment.CenterStart).padding(start = 34.appDp).testTag("schedule-header-station"), style = TextStyle(color = RailMuted, fontSize = 14.appSp, letterSpacing = 1.appSp))
                    }
                    Box(Modifier.weight(89f), contentAlignment = Alignment.Center) {
                        BasicText("到达", Modifier.testTag("schedule-header-arrival"), style = TextStyle(color = RailMuted, fontSize = 14.appSp, letterSpacing = 1.8.appSp))
                    }
                    Box(Modifier.weight(92f), contentAlignment = Alignment.Center) {
                        BasicText("出发", Modifier.testTag("schedule-header-departure"), style = TextStyle(color = RailMuted, fontSize = 14.appSp, letterSpacing = 1.appSp))
                    }
                }
                value.stops.forEachIndexed { index, stop ->
                    // Endpoints have one line; intermediate dwell rows reserve two lines.
                    // Keep the final station aligned to the table's bottom rather than its row midpoint.
                    val isLast = index == value.stops.lastIndex
                    val hasDwell = !stop.stopoverTime.isNullOrBlank()
                    val rowHeight = when {
                        index == 0 -> 48.appDp
                        isLast -> 41.5.appDp
                        hasDwell -> 66.appDp
                        else -> 55.appDp
                    }
                    val cellAlignment = if (isLast && index > 0) Alignment.BottomCenter else Alignment.Center
                    val stationAlignment = if (isLast && index > 0) Alignment.BottomStart else Alignment.CenterStart
                    val cellBottomPadding = if (isLast && index > 0) 2.appDp else 0.appDp
                    Row(Modifier.fillMaxWidth().heightIn(min = rowHeight).testTag("schedule-stop-${index + 1}"), verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.weight(132f).height(rowHeight), contentAlignment = stationAlignment) {
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                Canvas(Modifier.width(30.appDp).height(rowHeight).testTag("schedule-timeline-${index + 1}")) {
                                    val x = size.width / 2f
                                    val centerY = when {
                                        isLast && index > 0 -> size.height - 13.appDp.toPx()
                                        index > 0 && hasDwell -> size.height / 2f - 3.appDp.toPx()
                                        else -> size.height / 2f
                                    }
                                    val stroke = 2.appDp.toPx()
                                    if (index > 0) drawLine(RailAccent, Offset(x, 0f), Offset(x, centerY), stroke, cap = StrokeCap.Round)
                                    if (index < value.stops.lastIndex) drawLine(RailAccent, Offset(x, centerY), Offset(x, size.height), stroke, cap = StrokeCap.Round)
                                    drawCircle(RailAccent, radius = 6.appDp.toPx(), center = Offset(x, centerY))
                                }
                                Column(Modifier.weight(1f).height(rowHeight).padding(start = 8.appDp, bottom = cellBottomPadding),
                                    verticalArrangement = if (isLast && index > 0) Arrangement.Bottom else Arrangement.Center) {
                                    BasicText(stop.station ?: "—", Modifier.testTag("schedule-station-${index + 1}"), style = TextStyle(color = RailInk, fontSize = 17.appSp, letterSpacing = 1.6.appSp, fontWeight = FontWeight.Medium))
                                    if (!stop.stopoverTime.isNullOrBlank()) BasicText(stop.stopoverTime, Modifier.testTag("schedule-stopover-${index + 1}"), style = TextStyle(color = RailMuted, fontSize = 14.appSp, letterSpacing = 1.4.appSp))
                                }
                            }
                        }
                        Box(Modifier.weight(89f).height(rowHeight).padding(bottom = cellBottomPadding), contentAlignment = cellAlignment) {
                            val time = stop.arriveTime ?: "--"
                            BasicText(time, Modifier.testTag("schedule-arrival-${index + 1}"), style = TextStyle(color = RailInk, fontSize = 17.5.appSp, fontWeight = if (time == "--") FontWeight.Medium else FontWeight.Normal, letterSpacing = if (time == "--") 2.8.appSp else 0.appSp))
                        }
                        Box(Modifier.weight(92f).height(rowHeight).padding(start = 2.appDp, bottom = cellBottomPadding), contentAlignment = cellAlignment) {
                            val time = stop.startTime ?: "--"
                            BasicText(time, Modifier.testTag("schedule-departure-${index + 1}"), style = TextStyle(color = RailInk, fontSize = 17.5.appSp, fontWeight = if (time == "--") FontWeight.Medium else FontWeight.Normal, letterSpacing = if (time == "--") 2.8.appSp else 0.appSp))
                        }
                    }
                    if (index < value.stops.lastIndex) Spacer(Modifier.fillMaxWidth().padding(start = 34.appDp).height(1.appDp).background(RailLine))
                }
            }
        }
        if (value.timeBasis == "reference") {
            Spacer(Modifier.height(14.appDp))
            InfoNote("说明", "图定时刻不代表实际正晚点。", "schedule-source-note", verticalPadding = 9, modifier = Modifier.padding(start = 0.5.appDp, end = 1.appDp))
        }
        if (value.timeBasis == "stations_only") {
            Spacer(Modifier.height(14.appDp))
            InfoNote("说明", "当日时刻不可用，以上仅显示站名。", "schedule-source-note", verticalPadding = 9, modifier = Modifier.padding(start = 0.5.appDp, end = 1.appDp))
        }
        Spacer(Modifier.height(11.appDp))
        QueryDetailsPanel(
            value.sources.firstOrNull(), value.date, value.scheduleType, value.sampleData, request,
            modifier = Modifier.padding(start = 0.5.appDp, end = 1.appDp), sourceRowMinHeight = 40,
        )
    }
}

@Composable
private fun RoutingCard(value: RoutingResult, request: ChatUiState, onAction: (JSONObject, String, Boolean) -> Unit, modifier: Modifier) {
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(0.appDp)) {
      Column(Modifier.fillMaxWidth()
          .border(1.appDp, RailLine, RoundedCornerShape(16.appDp)).testTag("routing-result")
          .background(NativeColors.surface, RoundedCornerShape(16.appDp))
          .padding(start = 13.5.appDp, end = 12.5.appDp, top = 14.appDp, bottom = 12.appDp), verticalArrangement = Arrangement.spacedBy(13.appDp)) {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(3.appDp)) {
          Row(Modifier.fillMaxWidth().padding(horizontal = 2.appDp).heightIn(min = 31.appDp).testTag("routing-title-row"), horizontalArrangement = Arrangement.SpaceBetween) {
            BasicText(value.query ?: "车组交路", Modifier.testTag("routing-title"), style = TextStyle(color = RailInk, fontSize = 24.appSp, lineHeight = 29.appSp, platformStyle = PlatformTextStyle(includeFontPadding = false), fontWeight = FontWeight.Bold))
            if (value.sampleData) BasicText("示例数据", Modifier.testTag("routing-sample-badge"), style = TextStyle(color = RailMuted, fontSize = 13.appSp))
          }
          if (value.focusDate.isNullOrBlank()) BasicText("最近交路记录 · 按原始日期展示", style = TextStyle(color = RailMuted, fontSize = 15.appSp))
          value.focusDate?.let { BasicText("${dateLabel(it)} · 交路记录", Modifier.fillMaxWidth().padding(horizontal = 2.appDp).testTag("routing-date-record"), style = TextStyle(color = RailMuted, fontSize = 17.appSp, letterSpacing = 1.05.appSp, lineHeight = 22.appSp, lineHeightStyle = LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.None, LineHeightStyle.Mode.Fixed), platformStyle = PlatformTextStyle(includeFontPadding = false))) }
        }
        InfoNote("时间口径", value.timeSemantics ?: "以下为记录时间，不是列车到发时间。", "routing-time-semantics", verticalPadding = 14,
            textStyle = TextStyle(color = RailAccent, fontSize = 15.appSp, letterSpacing = .65.appSp, lineHeight = 20.appSp, lineHeightStyle = LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.None, LineHeightStyle.Mode.Fixed), platformStyle = PlatformTextStyle(includeFontPadding = false)),
            verticalPaddingOverride = 13.appDp, textStartPadding = 11.5.appDp)
        Column(Modifier.fillMaxWidth().testTag("routing-records")) {
        Row(Modifier.fillMaxWidth().heightIn(min = 38.appDp).background(RailPanel, RoundedCornerShape(8.appDp)).padding(start = 20.appDp, end = 24.appDp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.weight(.45f)) {
                BasicText(if (value.queryKind == "train") "担当车组" else "车次", Modifier.testTag("routing-header-train"), style = TextStyle(color = RailInk, fontSize = 15.appSp, lineHeight = 20.appSp, platformStyle = PlatformTextStyle(includeFontPadding = false), fontWeight = FontWeight.Medium))
            }
            Box(Modifier.weight(.55f)) {
                BasicText("记录时间", Modifier.testTag("routing-header-time"), style = TextStyle(color = RailInk, fontSize = 15.appSp, lineHeight = 20.appSp, platformStyle = PlatformTextStyle(includeFontPadding = false), fontWeight = FontWeight.Medium))
            }
        }
        value.records.forEachIndexed { index, record ->
            val train = record.optString("train_code").takeIf(String::isNotBlank) ?: "—"
            val rowHeight = if (value.focusDate.isNullOrBlank()) 88.appDp else 64.appDp
            val openTrainSchedule: () -> Unit = {
                onAction(action("train_schedule_batch", "trains" to listOf(train), "date" to (value.focusDate ?: record.optString("date"))), "查询 $train 的时刻表", false)
            }
            Row(Modifier.fillMaxWidth().heightIn(min = rowHeight).clickable(onClick = openTrainSchedule)
                .testTag("routing-record-$train").padding(start = 15.appDp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(.46f).heightIn(min = rowHeight).padding(vertical = 8.appDp), verticalArrangement = Arrangement.Center) {
                    val units = record.optJSONArray("units")
                    val labels = if (units == null) emptyList() else (0 until units.length()).mapNotNull {
                        units.optJSONObject(it)?.optString("emu_no_display")?.takeIf(String::isNotBlank)
                    }
                    BasicText(if (value.queryKind == "train") labels.joinToString(" + ").ifBlank { "车组未提供" } else train,
                        Modifier.testTag("routing-train-$train"), style = TextStyle(color = RailInk,
                            fontSize = if (value.queryKind == "train") 16.appSp else 22.appSp,
                            fontWeight = FontWeight.Bold))
                    if (record.optBoolean("coupled")) BasicText("重联", style = TextStyle(color = RailAccent, fontSize = 13.appSp))
                    if (value.queryKind != "train" && labels.size > 1) BasicText(labels.joinToString(" + "), style = TextStyle(color = RailMuted, fontSize = 13.appSp))
                }
                Column(Modifier.weight(.54f).height(rowHeight).padding(top = 16.5.appDp)) {
                    BasicText(if (value.focusDate.isNullOrBlank()) "${record.optString("date")}\n${record.optString("time", "—")}" else record.optString("time", "—"), Modifier.testTag("routing-time-$train"), style = TextStyle(color = RailInk, fontSize = 20.appSp, lineHeight = 24.appSp, platformStyle = PlatformTextStyle(includeFontPadding = false)))
                    BasicText("查看该车次时刻", Modifier.clickable(onClick = openTrainSchedule).testTag("routing-record-link-$train"), style = TextStyle(color = RailMuted, fontSize = 15.appSp, letterSpacing = .95.appSp, lineHeight = 20.appSp, platformStyle = PlatformTextStyle(includeFontPadding = false)))
                }
                Box(Modifier.size(24.appDp), contentAlignment = Alignment.Center) {
                    RailIcon("chevron", Modifier.size(16.appDp), RailMuted, "routing-record-chevron-$train")
                }
            }
            if (index < value.records.lastIndex) Spacer(Modifier.fillMaxWidth().height(1.appDp).background(RailLine))
        }
        }
        QueryDetailsPanel(value.sources.firstOrNull(), value.focusDate, value.timeSemantics, value.sampleData, request)
      }
      if (!value.focusDate.isNullOrBlank()) {
      Spacer(Modifier.height(8.appDp))
      val codes = value.records.mapNotNull { it.optString("train_code").takeIf(String::isNotBlank) }.distinct()
      ActionButton("查询这${chineseCount(codes.size)}趟车的时刻表", "routing-batch", minWidth = 209, minHeight = 46, centerText = true,
          textStyle = TextStyle(color = RailAccent, fontSize = 17.appSp, letterSpacing = .45.appSp, lineHeight = 22.appSp, platformStyle = PlatformTextStyle(includeFontPadding = false), textAlign = androidx.compose.ui.text.style.TextAlign.Center)) {
          onAction(action("train_schedule_batch", "trains" to codes, "date" to value.focusDate), "查询 ${codes.size} 趟车的时刻表", false)
      }
      Spacer(Modifier.height(8.appDp))
      }
      InfoNote("说明", "记录可能不完整，以实际运行情况为准。", "routing-integrity-note")
    }
}

@Composable
private fun BatchCard(value: BatchScheduleResult, request: ChatUiState, onAction: (JSONObject, String, Boolean) -> Unit, modifier: Modifier) {
    Column(modifier.fillMaxWidth().testTag("batch-result"), verticalArrangement = Arrangement.spacedBy(10.appDp)) {
      Column(Modifier.fillMaxWidth().padding(horizontal = 16.appDp).background(NativeColors.surface, RoundedCornerShape(14.appDp))
          .border(1.appDp, RailLine, RoundedCornerShape(14.appDp)).testTag("batch-card"),
          verticalArrangement = Arrangement.spacedBy(0.appDp)) {
        val scheduleItems = value.items.filterIsInstance<TrainScheduleDisplay>()
        val succeeded = scheduleItems.count { it.value.status == "success" }
        Row(Modifier.fillMaxWidth().background(RailPanel, RoundedCornerShape(topStart = 14.appDp, topEnd = 14.appDp))
            .padding(horizontal = 14.appDp, vertical = 6.appDp).testTag("batch-title-row"),
            horizontalArrangement = Arrangement.SpaceBetween) {
            BasicText("时刻查询", Modifier.testTag("batch-heading"), style = TextStyle(color = RailInk, fontSize = 18.appSp, fontWeight = FontWeight.Bold))
            BasicText("$succeeded / ${value.items.size} 已返回", Modifier.background(Color(0xFFFFF0C2), RoundedCornerShape(16.appDp)).padding(horizontal = 12.appDp, vertical = 7.appDp).testTag("batch-count"), style = TextStyle(color = Color(0xFF754B00), fontSize = 14.appSp))
        }
        scheduleItems.forEachIndexed { index, item ->
            val trainCode = item.value.trainCode ?: "unknown"
            Row(Modifier.fillMaxWidth().heightIn(min = 40.appDp).padding(horizontal = 14.appDp)
                .testTag("batch-row-$trainCode"), verticalAlignment = Alignment.CenterVertically) {
                BasicText(item.value.trainCode ?: "—", Modifier.weight(1f).testTag("batch-train-$trainCode"),
                    style = TextStyle(color = RailInk, fontSize = 16.appSp))
                Box(Modifier.width(120.appDp), contentAlignment = Alignment.CenterStart) {
                    BatchItemStatus(item.value.status, trainCode)
                }
            }
            if (index < scheduleItems.lastIndex) {
                Spacer(Modifier.fillMaxWidth().padding(horizontal = 14.appDp).height(1.appDp).background(RailLine))
            }
        }
        Spacer(Modifier.height(6.appDp))
      }
        // The summary reports task status; each item still owns its complete timetable.
        value.items.filterIsInstance<TrainScheduleDisplay>().forEach { item ->
            ScheduleCard(item.value, request, Modifier)
        }
        val failed = value.items.filterIsInstance<TrainScheduleDisplay>().filter { it.value.status == "failed" }
        if (failed.isNotEmpty()) {
            val failedCodes = failed.mapNotNull { it.value.trainCode }
            StateActionButton("仅重试 ${failedCodes.joinToString("、")}", "batch-retry-failed", fillWidth = true) {
                onAction(action("train_schedule_batch", "trains" to failedCodes, "date" to failed.firstNotNullOfOrNull { it.value.date }), "重试 ${failedCodes.joinToString("、")} 的时刻查询", true)
            }
            Box(Modifier.fillMaxWidth().heightIn(min = 22.appDp).testTag("batch-retain-success"), contentAlignment = Alignment.Center) {
                BasicText("保留已查到的结果", Modifier.testTag("batch-retain-success-text"), style = TextStyle(color = RailMuted, fontSize = 14.appSp, letterSpacing = .3.appSp))
            }
        }
    }
}

@Composable
private fun EmptyCard(
    value: EmptyResult,
    onAction: (JSONObject, String, Boolean) -> Unit,
    modifier: Modifier,
    datePicker: ((LocalDate, (LocalDate) -> Unit) -> Unit)?,
) {
    val context = LocalContext.current
    Column(modifier.fillMaxWidth().border(1.appDp, RailLine, RoundedCornerShape(16.appDp)).testTag("routing-empty").padding(start = 16.appDp, end = 16.appDp, top = 20.2.appDp, bottom = 5.appDp)) {
        BoxWithConstraints(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            // The source illustration's viewport centre is 15px right of the module centre.
            val sourceUnit = (maxWidth + 32.appDp) / 462f
            RailIcon("train-search", Modifier.offset(x = sourceUnit * 15f).width(sourceUnit * 108f).height(sourceUnit * 80f),
                if (NativeColors.dark) RailMuted else Color(0xFF98A6BC), "empty-icon-group")
        }
        Spacer(Modifier.height(14.appDp))
        BasicText("未找到当天交路记录", Modifier.align(Alignment.CenterHorizontally).testTag("empty-title"), style = TextStyle(color = RailInk, fontSize = 18.appSp, letterSpacing = 3.appSp, lineHeight = 24.appSp, platformStyle = PlatformTextStyle(includeFontPadding = false), fontWeight = FontWeight.Bold))
        Spacer(Modifier.height(4.appDp))
        BasicText("暂无记录，不能据此判断停运。", Modifier.align(Alignment.CenterHorizontally).testTag("empty-message"), style = TextStyle(color = RailMuted, fontSize = 14.9.appSp, letterSpacing = 1.55.appSp, lineHeight = 19.4.appSp, platformStyle = PlatformTextStyle(includeFontPadding = false)))
        Spacer(Modifier.height(11.appDp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
            Row(Modifier.widthIn(min = 146.appDp).heightIn(min = 38.appDp).border(1.appDp, RailLine, RoundedCornerShape(22.appDp)).testTag("empty-date-pill").padding(horizontal = 18.appDp, vertical = 7.appDp),
                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.appDp)) {
                RailIcon("calendar", Modifier.width(18.appDp).height(18.appDp), semanticTag = "empty-date-icon")
                BasicText(value.date?.let(::dateLabel) ?: "选择日期", Modifier.testTag("empty-date"), style = TextStyle(color = RailInk, fontSize = 16.appSp))
            }
        }
        Spacer(Modifier.height(10.appDp))
        Row(horizontalArrangement = Arrangement.spacedBy(11.appDp)) {
            Box(Modifier.weight(203f)) {
                StateActionButton("更换日期", "empty-change-date", fillWidth = true, visualMinHeight = 40.5.appDp) {
                    val initial = runCatching { LocalDate.parse(value.date) }.getOrElse { LocalDate.now() }
                    val selected: (LocalDate) -> Unit = { picked ->
                        val date = picked.toString()
                        onAction(action("emu_routing", "query" to value.query, "date" to date), "查询 $date 的交路记录", false)
                    }
                    if (datePicker != null) datePicker(initial, selected)
                    else DatePickerDialog(context, { _, year, month, day ->
                        selected(LocalDate.of(year, month + 1, day))
                    }, initial.year, initial.monthValue - 1, initial.dayOfMonth).show()
                }
            }
            Box(Modifier.weight(208f)) {
                StateActionButton("查看最近记录", "empty-recent", fillWidth = true, visualMinHeight = 40.5.appDp, backgroundColor = Color(0xFFDCE9FC)) {
                    onAction(action("emu_routing", "query" to value.query, "date" to null, "recent" to true), "查看最近交路记录", false)
                }
            }
        }
        Spacer(Modifier.height(9.appDp))
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            BasicText("历史记录将单独标注日期", Modifier.testTag("empty-history-note"), style = TextStyle(color = RailMuted, fontSize = 14.appSp, letterSpacing = .18.appSp, lineHeight = 17.appSp, platformStyle = PlatformTextStyle(includeFontPadding = false)))
        }
    }
}

@Composable
private fun ConnectionErrorCard(value: ErrorResult, onRetry: () -> Unit, onSettings: () -> Unit, modifier: Modifier) {
    var expanded by remember(value) { mutableStateOf(false) }
    Column(modifier.fillMaxWidth().border(1.appDp, RailLine, RoundedCornerShape(14.appDp)).testTag("connection-error")
        .padding(start = 18.appDp, end = 18.appDp, top = 24.appDp, bottom = 25.appDp), verticalArrangement = Arrangement.spacedBy(9.appDp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.appDp)) {
            Box(Modifier.width(86.appDp).height(60.appDp), contentAlignment = Alignment.Center) {
                RailIcon("cloud-error", Modifier.width(66.appDp).height(59.appDp), Color(0xFFF0524F), "error-icon")
            }
            Column {
                BasicText(when (mainErrorCategory(value.message.orEmpty())) { "configuration" -> "请检查云端模型配置"; "service" -> "查询服务暂时异常"; else -> "暂时无法连接模型服务" }, Modifier.testTag("error-title"), style = TextStyle(color = RailInk, fontSize = 18.appSp, fontWeight = FontWeight.Bold))
                BasicText("你的提问已保留，可稍后重试。", Modifier.testTag("error-explanation"), style = TextStyle(color = RailMuted, fontSize = 14.appSp, letterSpacing = 2.1.appSp, lineHeight = 19.appSp))
            }
        }
        Spacer(Modifier.height(1.appDp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.appDp)) {
            Box(Modifier.weight(.91f)) { StateActionButton("重试", "connection-retry", fillWidth = true, visualMinHeight = 39.17.appDp, backgroundColor = RailAccent, foregroundColor = Color.White, onClick = onRetry) }
            Box(Modifier.weight(1f)) {
                if (mainErrorCategory(value.message.orEmpty()) == "service") StateActionButton("查看错误详情", "connection-settings", fillWidth = true, visualMinHeight = 39.17.appDp, onClick = { expanded = !expanded })
                else StateActionButton("检查模型设置", "connection-settings", fillWidth = true, visualMinHeight = 39.17.appDp, onClick = onSettings)
            }
        }
        Box(Modifier.fillMaxWidth().heightIn(min = 44.dp).clickable { expanded = !expanded }.testTag("error-details-toggle"), contentAlignment = Alignment.Center) {
            Row(Modifier.fillMaxWidth().height(45.appDp).border(1.appDp, RailLine, RoundedCornerShape(10.appDp)).testTag("error-details-toggle-visual"),
                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween) {
                BasicText("错误详情", Modifier.padding(horizontal = 12.appDp).testTag("error-details-label"), style = TextStyle(color = RailInk, fontSize = 14.appSp))
                RailIcon(if (expanded) "chevron-up" else "chevron-down", Modifier.width(18.appDp).height(18.appDp).padding(end = 4.appDp), RailInk, "error-details-chevron")
            }
        }
        if (expanded) BasicText(value.message ?: "查询失败", style = TextStyle(color = RailMuted, fontSize = 13.appSp))
        if (mainErrorCategory(value.message.orEmpty()) == "auth") {
            BasicText("API Key", Modifier.testTag("api-key-label"), style = TextStyle(color = RailInk, fontSize = 14.appSp, letterSpacing = .15.appSp))
            Box(Modifier.fillMaxWidth().heightIn(min = 44.dp).testTag("api-key-field"), contentAlignment = Alignment.Center) {
                Row(Modifier.fillMaxWidth().height(44.appDp).border(1.appDp, Color(0xFFF0524F), RoundedCornerShape(8.appDp)).testTag("api-key-field-visual").padding(horizontal = 12.appDp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                    BasicText("••••••••••••••••", style = TextStyle(color = RailInk))
                    RailIcon("warning", Modifier.size(20.appDp), Color(0xFFF0524F), "api-key-error-icon")
                }
            }
            BasicText("密钥无效，请检查后重试", Modifier.testTag("api-key-error"), style = TextStyle(color = Color(0xFFF0524F), fontSize = 14.appSp, letterSpacing = .25.appSp))
        }
    }
}

@Composable
fun QueryDetailsPanel(
    source: String?,
    date: String?,
    timeBasis: String?,
    sampleData: Boolean,
    request: ChatUiState,
    modifier: Modifier = Modifier,
    sourceRowMinHeight: Int = 42,
) {
    var expanded by remember(source, date, timeBasis, request.requestId) { mutableStateOf(false) }
    var showLogs by remember(request.requestId) { mutableStateOf(false) }
    Column(modifier.fillMaxWidth().testTag("query-details-host")) {
        Column(Modifier.fillMaxWidth().background(RailPanel, RoundedCornerShape(8.appDp)).testTag("query-details")) {
            Column(Modifier.fillMaxWidth().testTag("details-heading-and-source")) {
                if (expanded) {
                    Row(Modifier.fillMaxWidth().heightIn(min = 48.appDp).clickable { expanded = false }
                        .padding(horizontal = 10.appDp).testTag("query-details-toggle"),
                        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween) {
                        BasicText("查询详情", Modifier.testTag("query-details-title"), style = TextStyle(color = RailInk, fontSize = 18.appSp, lineHeight = 22.appSp, lineHeightStyle = LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.None, LineHeightStyle.Mode.Fixed), platformStyle = PlatformTextStyle(includeFontPadding = false), fontWeight = FontWeight.Bold))
                        RailIcon("chevron-up", Modifier.size(16.appDp), RailInk, "query-details-chevron")
                    }
                }
                val effectiveSourceRowMinHeight = if (expanded) maxOf(sourceRowMinHeight, 42) else sourceRowMinHeight
                Row(Modifier.fillMaxWidth().heightIn(min = effectiveSourceRowMinHeight.appDp).padding(horizontal = 10.appDp), verticalAlignment = Alignment.CenterVertically) {
                    SourceLink(source, sourceLabel(source), "details-source", Modifier.weight(1f), expanded = expanded)
                    if (expanded && sampleData) BasicText("示例数据", Modifier.testTag("details-sample-badge"), style = TextStyle(color = RailMuted, fontSize = 13.appSp))
                    if (!expanded) {
                        BasicText("查询详情", Modifier.clickable { expanded = true }.padding(horizontal = 6.appDp, vertical = 8.appDp).testTag("query-details-toggle"),
                            style = TextStyle(color = RailMuted, fontSize = 14.appSp))
                        RailIcon(if (source?.contains("rail.re") == true) "chevron" else "chevron-down", Modifier.size(16.appDp), RailMuted, "query-details-chevron")
                    }
                }
            }
            if (expanded) {
                DetailRow("数据日期", date?.let(::dateLabel), "details-date")
                DetailRow("时间口径", timeBasis, "details-time-basis")
                DetailRow("查询耗时", request.latencyMs?.let { "%.1f 秒".format(it / 1000.0) }, "details-latency")
                DetailRow("用量", request.usageJson.let(::tokenLabel), "details-usage")
            }
        }
        if (expanded && request.processLogs.isNotEmpty()) {
            Spacer(Modifier.height(12.appDp))
            Box(Modifier.fillMaxWidth().heightIn(min = 44.dp)
                .clickable { showLogs = !showLogs }.testTag("technical-log-toggle"), contentAlignment = Alignment.Center) {
            Row(Modifier.fillMaxWidth().heightIn(min = 44.appDp)
                .background(NativeColors.surface, RoundedCornerShape(10.appDp)).border(1.appDp, RailLine, RoundedCornerShape(10.appDp))
                .testTag("technical-log-visual").padding(horizontal = 10.appDp, vertical = 8.appDp),
                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.appDp)) {
                RailIcon("file", Modifier.size(24.appDp), RailInk, "technical-log-file-icon")
                BasicText(if (showLogs) "收起技术日志" else "查看技术日志", Modifier.weight(1f).testTag("technical-log-label"),
                    style = TextStyle(color = RailInk, fontSize = 16.appSp, lineHeight = 22.appSp, lineHeightStyle = LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.None, LineHeightStyle.Mode.Fixed), platformStyle = PlatformTextStyle(includeFontPadding = false)))
                RailIcon(if (showLogs) "chevron-up" else "chevron", Modifier.size(16.appDp), RailInk, "technical-log-chevron")
            }
            }
            if (showLogs) request.processLogs.forEach { BasicText(it, Modifier.padding(horizontal = 10.appDp).testTag("technical-log-entry"), style = TextStyle(color = RailMuted, fontSize = 13.appSp)) }
        }
    }
}

@Composable
private fun SourceLink(url: String?, label: String, tag: String? = null, modifier: Modifier = Modifier, expanded: Boolean = false) {
    if (url.isNullOrBlank()) return
    val context = LocalContext.current
    Row(modifier.fillMaxWidth().clickable {
        runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
    }.then(if (tag == null) Modifier else Modifier.testTag(tag)).padding(vertical = if (expanded) 4.appDp else 8.appDp), verticalAlignment = Alignment.CenterVertically) {
        if (expanded) {
            Box(Modifier.size(34.appDp).background(NativeColors.selected, CircleShape), contentAlignment = Alignment.Center) {
                RailIcon("link", Modifier.size(22.appDp), RailAccent, "${tag ?: "source"}-icon")
            }
        } else RailIcon("link", Modifier.size(18.appDp), RailMuted, "${tag ?: "source"}-icon")
        BasicText(label, Modifier.padding(start = if (expanded) 8.appDp else 13.appDp).then(if (tag == null) Modifier else Modifier.testTag("$tag-label")), style = if (expanded)
            TextStyle(color = RailInk, fontSize = 16.appSp, lineHeight = 22.appSp, lineHeightStyle = LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.None, LineHeightStyle.Mode.Fixed), platformStyle = PlatformTextStyle(includeFontPadding = false))
            else TextStyle(color = RailMuted, fontSize = 14.appSp))
    }
}

@Composable
private fun DetailRow(label: String, value: String?, tag: String) {
    if (value.isNullOrBlank()) return
    Row(Modifier.fillMaxWidth().heightIn(min = 36.appDp).border(1.appDp, RailPanel).testTag(tag).padding(horizontal = 10.appDp, vertical = 8.appDp), horizontalArrangement = Arrangement.SpaceBetween) {
        BasicText(label, Modifier.testTag("$tag-label"), style = TextStyle(color = RailMuted, fontSize = 14.appSp, lineHeight = 20.appSp, lineHeightStyle = LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.None, LineHeightStyle.Mode.Fixed), platformStyle = PlatformTextStyle(includeFontPadding = false)))
        BasicText(value, Modifier.testTag("$tag-value"), style = TextStyle(color = RailMuted, fontSize = 14.appSp, lineHeight = 20.appSp, lineHeightStyle = LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.None, LineHeightStyle.Mode.Fixed), platformStyle = PlatformTextStyle(includeFontPadding = false)))
    }
}

@Composable
private fun ActionButton(
    label: String,
    tag: String,
    fillWidth: Boolean = false,
    minWidth: Int = 0,
    minHeight: Int = 44,
    centerText: Boolean = false,
    textStyle: TextStyle? = null,
    onClick: () -> Unit,
) {
    val widthModifier = if (fillWidth) Modifier.fillMaxWidth() else Modifier
    if (textStyle != null) {
        Box(widthModifier.widthIn(min = minWidth.appDp).border(1.appDp, RailAccent, RoundedCornerShape(10.appDp))
            .heightIn(min = maxOf(44.dp, minHeight.appDp)).clickable(onClick = onClick).testTag(tag).padding(horizontal = 13.appDp),
            contentAlignment = Alignment.Center) {
            BasicText(label, Modifier.testTag("$tag-label"), style = textStyle)
        }
        return
    }
    BasicText(label, widthModifier.widthIn(min = minWidth.appDp).border(1.appDp, RailAccent, RoundedCornerShape(10.appDp)).heightIn(min = maxOf(44.dp, minHeight.appDp))
        .clickable(onClick = onClick).testTag(tag).padding(horizontal = 13.appDp, vertical = 8.appDp),
        style = textStyle ?: TextStyle(color = RailAccent, fontSize = 16.appSp, textAlign = if (centerText) androidx.compose.ui.text.style.TextAlign.Center else androidx.compose.ui.text.style.TextAlign.Start))
}

@Composable
private fun StateActionButton(
    label: String,
    tag: String,
    fillWidth: Boolean = false,
    minWidth: Int = 0,
    visualMinHeight: androidx.compose.ui.unit.Dp = 44.appDp,
    backgroundColor: Color = Color.Transparent,
    foregroundColor: Color = RailAccent,
    onClick: () -> Unit,
) {
    val widthModifier = if (fillWidth) Modifier.fillMaxWidth() else Modifier
    Box(
        widthModifier.widthIn(min = minWidth.appDp).heightIn(min = 44.dp).clickable(onClick = onClick).testTag(tag),
        contentAlignment = Alignment.Center,
    ) {
        Box(
            Modifier.fillMaxWidth().testTag("$tag-visual").background(backgroundColor, RoundedCornerShape(10.appDp))
                .border(1.appDp, RailAccent, RoundedCornerShape(10.appDp)).heightIn(min = visualMinHeight)
                .padding(horizontal = 13.appDp),
            contentAlignment = Alignment.Center,
        ) {
            BasicText(label, Modifier.testTag("$tag-label"), style = TextStyle(color = foregroundColor, fontSize = 16.appSp, textAlign = androidx.compose.ui.text.style.TextAlign.Center))
        }
    }
}

@Composable
private fun BatchItemStatus(status: String, trainCode: String) {
    val success = status == "success"
    Row(Modifier.offset(y = 2.5.appDp).testTag("batch-status-$trainCode"), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.appDp)) {
        RailIcon(if (success) "check" else "warning", Modifier.size(24.appDp),
            if (success) Color(0xFF10B981) else Color(0xFFF59E0B), "batch-status-icon-$trainCode")
        BasicText(if (success) "已查到" else "暂未返回", Modifier.testTag("batch-status-label-$trainCode"), style = TextStyle(color = if (success) Color(0xFF38665B) else Color(0xFFB66A00), fontSize = 14.appSp))
    }
}

@Composable
private fun InfoCard(title: String, detail: String, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth().border(1.appDp, RailLine, RoundedCornerShape(10.appDp)).padding(12.appDp), verticalArrangement = Arrangement.spacedBy(6.appDp)) {
        BasicText(title, style = TextStyle(color = RailInk, fontWeight = FontWeight.Medium))
        if (detail.isNotBlank()) BasicText(detail, style = TextStyle(color = RailMuted))
    }
}

@Composable
private fun InfoNote(
    icon: String,
    text: String,
    tag: String? = null,
    verticalPadding: Int = 11,
    modifier: Modifier = Modifier,
    textStyle: TextStyle? = null,
    verticalPaddingOverride: androidx.compose.ui.unit.Dp? = null,
    textStartPadding: androidx.compose.ui.unit.Dp = 8.appDp,
) {
    Row(modifier.fillMaxWidth().background(RailPanel, RoundedCornerShape(8.appDp))
        .then(if (tag == null) Modifier else Modifier.testTag(tag))
        .padding(horizontal = 15.appDp, vertical = verticalPaddingOverride ?: verticalPadding.appDp)) {
        RailIcon(if (icon == "时间口径") "clock" else "info", Modifier.width(18.appDp).height(18.appDp))
        BasicText(text, Modifier.padding(start = textStartPadding).then(if (tag == null) Modifier else Modifier.testTag("$tag-text")), style = textStyle ?: TextStyle(color = RailMuted, fontSize = 14.appSp))
    }
}

@Composable
private fun MetaPill(icon: String, label: String, tag: String) {
    Row(Modifier.widthIn(min = 134.appDp).heightIn(min = 37.appDp)
        .background(RailPanel, RoundedCornerShape(9.appDp)).testTag(tag)
        .padding(horizontal = 12.appDp, vertical = 7.appDp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.appDp)) {
        RailIcon(icon, Modifier.width(18.appDp).height(18.appDp), semanticTag = "${tag}-icon")
        NativeLineText(label, 20.appSp, Modifier.testTag("$tag-label"), style = TextStyle(color = RailInk, fontSize = 18.appSp))
    }
}

private fun action(kind: String, vararg fields: Pair<String, Any?>): JSONObject = JSONObject().put("kind", kind).also { json ->
    fields.forEach { (key, value) ->
        val normalized = when (value) {
            is Iterable<*> -> JSONArray().also { array -> value.forEach { array.put(it) } }
            else -> value
        }
        json.put(key, normalized)
    }
}
private fun dateLabel(value: String): String = runCatching { LocalDate.parse(value.take(10)).let { "%02d月%02d日".format(it.monthValue, it.dayOfMonth) } }.getOrDefault(value)
private fun sourceLabel(url: String?): String = if (url?.contains("rail.re") == true) "rail.re · 交路记录" else "12306 · 列车时刻"
private fun tokenLabel(json: String): String = runCatching {
    JSONObject(json).optInt("total_tokens").takeIf { it > 0 }?.let { String.format(java.util.Locale.US, "%,d Token", it) } ?: ""
}.getOrDefault("")
private fun chineseCount(value: Int): String = when (value) { 1 -> "一"; 2 -> "二"; 3 -> "三"; 4 -> "四"; 5 -> "五"; else -> value.toString() }

internal fun queryLoadingTitle(stage: String): String = when {
    stage == "intent" || stage == "extract" -> "正在理解你的问题…"
    stage == "retrieve" || stage.contains("生成") -> "正在生成回复…"
    stage.contains("连接") || stage.contains("检索") || stage.contains("查询") -> "正在检索相关资料…"
    else -> "正在处理你的问题…"
}

private fun queryLoadingCaption(stage: String): String = when (stage) {
    "intent" -> "正在分析提问"
    "extract" -> "正在确认查询条件"
    "retrieve" -> "正在整理已返回结果"
    "" -> "请稍候"
    else -> stage
}
