package org.openrailfanai.app

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight

internal data class FareSeat(val label: String, val amount: String)
internal data class FareReceiptRow(val description: String, val seats: List<FareSeat>)
internal sealed interface FareReceiptLine {
    data class Text(val value: String) : FareReceiptLine
    data class Prices(val row: FareReceiptRow) : FareReceiptLine
}

/** Recognise only the existing explicit fare receipt; retain every other line verbatim. */
internal object FareReceiptParser {
    private val scope = Regex("^.+→.+（\\d{4}-\\d{2}-\\d{2}）票价：$")
    private val row = Regex("^((?:[A-Z]?[0-9]{1,5}[A-Z]?) .+（历时 [^）]+）)：(.+)$")
    private val seat = Regex("^(.+?) ([0-9]+(?:\\.[0-9]+)?) 元$")

    fun parse(text: String): List<FareReceiptLine>? {
        val lines = text.lines()
        if (lines.none { scope.matches(it.trim()) }) return null
        var count = 0
        val result = lines.map { line ->
            val match = row.matchEntire(line.trim())
            if (match == null) FareReceiptLine.Text(line)
            else {
                val seats = match.groupValues[2].split("、").map { price ->
                    val value = seat.matchEntire(price.trim()) ?: return null
                    FareSeat(value.groupValues[1], value.groupValues[2])
                }
                count++
                FareReceiptLine.Prices(FareReceiptRow(match.groupValues[1], seats))
            }
        }
        return result.takeIf { count > 0 }
    }
}

@Composable
internal fun FareCardFrame(modifier: Modifier = Modifier, title: String = "车票票价", badge: String = "票价信息", content: @Composable ColumnScope.() -> Unit) {
    Column(modifier.fillMaxWidth().testTag("fare-card")
        .background(NativeColors.surface, RoundedCornerShape(16.appDp))
        .border(1.appDp, NativeColors.line, RoundedCornerShape(16.appDp))
        .padding(14.appDp), verticalArrangement = Arrangement.spacedBy(12.appDp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.appDp),
            verticalAlignment = Alignment.CenterVertically) {
            BasicText(title, Modifier.weight(1f), style = TextStyle(color = NativeColors.ink,
                fontSize = 20.appSp, fontWeight = FontWeight.Bold))
            BasicText(badge, Modifier.background(NativeColors.selected, RoundedCornerShape(6.appDp))
                .padding(horizontal = 8.appDp, vertical = 5.appDp),
                style = TextStyle(color = NativeColors.muted, fontSize = 12.appSp))
        }
        content()
    }
}

@Composable
internal fun StructuredFareCard(value: FareResult, modifier: Modifier = Modifier) {
    var details by remember(value) { mutableStateOf(false) }
    val badge = fareBasisLabel(value)
    FareCardFrame(modifier, listOfNotNull(value.trainCode, "票价").joinToString(" · "), badge) {
        Column(Modifier.fillMaxWidth().background(NativeColors.panel, RoundedCornerShape(10.appDp))
            .padding(12.appDp), verticalArrangement = Arrangement.spacedBy(6.appDp)) {
            BasicText("乘车区间", style = fareCaptionStyle())
            BasicText("${value.fromStation ?: "起点未提供"} → ${value.toStation ?: "终点未提供"}",
                style = TextStyle(color = NativeColors.ink, fontSize = 18.appSp, fontWeight = FontWeight.SemiBold))
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.appDp)) {
            FareMetaPill("calendar", value.date ?: "日期未提供", Modifier.weight(1f))
            FareMetaPill("clock", value.duration?.let { "历时 $it" } ?: "历时未提供", Modifier.weight(1f))
        }
        if (value.status == "partial") FareNotice("金额不完整；未提供的席别金额不是0元。")
        when (value.status) {
            "empty" -> FareNotice("未查到该车次、日期和区间的精确票价记录。")
            "failed" -> FareNotice(value.error ?: "票价查询失败，请稍后重试。", error = true)
            else -> {
                if (value.startTime != null || value.arriveTime != null) {
                    Column(Modifier.fillMaxWidth().background(NativeColors.panel, RoundedCornerShape(10.appDp))
                        .padding(12.appDp), verticalArrangement = Arrangement.spacedBy(8.appDp)) {
                        BasicText("接口区间时刻", style = fareCaptionStyle())
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(16.appDp)) {
                            FareTime("出发", value.startTime ?: "—", Modifier.weight(1f))
                            FareTime("到达", value.arriveTime ?: "—", Modifier.weight(1f))
                        }
                    }
                }
                Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(1.appDp)) {
                    FareSeatRow("席别", "票价", header = true)
                    value.prices.forEachIndexed { index, price ->
                        FareSeatRow(price.seat, price.amount?.let { "$it ${if (price.currency == "CNY") "元" else price.currency}" } ?: "未提供")
                        if (index < value.prices.lastIndex) Box(Modifier.fillMaxWidth().height(1.appDp).background(NativeColors.line.copy(alpha = 0.45f)))
                    }
                }
                if (value.prices.isEmpty()) FareNotice("接口未提供席别金额。")
            }
        }
        FareNotice(value.note.ifBlank { "票价不代表实时余票。" })
        Column(Modifier.fillMaxWidth().background(NativeColors.panel, RoundedCornerShape(8.appDp)).padding(horizontal = 10.appDp)) {
            Row(Modifier.fillMaxWidth().heightIn(min = 44.appDp).clickable { details = !details },
                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.appDp)) {
                RailIcon("link", Modifier.size(18.appDp), NativeColors.muted)
                BasicText("票价来源与查询详情", Modifier.weight(1f), style = TextStyle(color = NativeColors.muted, fontSize = 14.appSp))
                RailIcon(if (details) "chevron-up" else "chevron-down", Modifier.size(16.appDp), NativeColors.muted)
            }
            if (details) {
                Box(Modifier.fillMaxWidth().height(1.appDp).background(NativeColors.line))
                FareDetailRow("数据日期", value.date ?: "未提供")
                value.fetchedAt?.let { FareDetailRow("采样时刻", it) }
                FareDetailRow("数据口径", "$badge，非余票")
                BasicText("数据来源", Modifier.padding(top = 8.appDp, bottom = 4.appDp), style = fareCaptionStyle())
                if (value.sources.isEmpty()) BasicText("来源未提供", style = fareBodyStyle())
                value.sources.forEach { MarkdownAnswer("<$it>") }
                Spacer(Modifier.height(10.appDp))
            }
        }
    }
}

private fun fareBodyStyle() = TextStyle(color = NativeColors.ink, fontSize = 16.appSp, lineHeight = 24.appSp)

private fun fareCaptionStyle() = TextStyle(color = NativeColors.muted, fontSize = 13.appSp, lineHeight = 19.appSp)

@Composable
private fun FareMetaPill(icon: String, label: String, modifier: Modifier) {
    Row(modifier.background(NativeColors.selected, RoundedCornerShape(8.appDp))
        .padding(horizontal = 10.appDp, vertical = 9.appDp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.appDp)) {
        RailIcon(icon, Modifier.size(17.appDp), NativeColors.blue)
        BasicText(label, Modifier.weight(1f), style = TextStyle(color = NativeColors.ink, fontSize = 14.appSp))
    }
}

@Composable
private fun FareTime(label: String, value: String, modifier: Modifier) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(4.appDp)) {
        BasicText(label, style = fareCaptionStyle())
        BasicText(value, style = TextStyle(color = NativeColors.ink, fontSize = 20.appSp, fontWeight = FontWeight.SemiBold))
    }
}

@Composable
private fun FareNotice(text: String, error: Boolean = false) {
    Row(Modifier.fillMaxWidth().background(if (error) NativeColors.danger.copy(alpha = 0.08f) else NativeColors.selected,
        RoundedCornerShape(8.appDp)).padding(10.appDp), horizontalArrangement = Arrangement.spacedBy(8.appDp)) {
        RailIcon("info", Modifier.size(18.appDp), if (error) NativeColors.danger else NativeColors.blue)
        BasicText(text, Modifier.weight(1f), style = TextStyle(color = NativeColors.muted, fontSize = 13.appSp, lineHeight = 20.appSp))
    }
}

@Composable
private fun FareDetailRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 8.appDp), horizontalArrangement = Arrangement.spacedBy(12.appDp)) {
        BasicText(label, Modifier.weight(0.38f), style = fareCaptionStyle())
        BasicText(value, Modifier.weight(0.62f), style = fareCaptionStyle().copy(color = NativeColors.ink))
    }
}

internal fun fareBasisLabel(value: FareResult): String = when (value.fareBasis) {
    "executed" -> "实际执行票价"
    "published" -> "公布参考票价"
    // Explicit unknown values remain unknown; do not reinterpret them using current defaults.
    null -> when {
        value.sources.any { "queryAllPublicPrice" in it } -> "公布参考票价"
        "公布参考票价" in value.note || "公布票价" in value.note -> "公布参考票价"
        "实际执行票价" in value.note -> "实际执行票价"
        else -> "票价信息"
    }
    else -> "票价信息"
}

internal fun fareClipboardText(value: FareResult): String = buildString {
    append(listOfNotNull(value.trainCode, value.date, value.fromStation, value.toStation).joinToString(" · "))
    append(" · ").append(fareBasisLabel(value)).append("\n")
    if (value.status == "failed") append(value.error ?: "票价查询失败")
    else if (value.status == "empty") append("未查到精确票价记录")
    else {
        if (value.startTime != null || value.arriveTime != null) append("接口区间时刻：${value.startTime ?: "—"}–${value.arriveTime ?: "—"}\n")
        value.duration?.let { append("历时：$it\n") }
        value.prices.forEach { append("${it.seat}：${it.amount?.let { amount -> "$amount ${if (it.currency == "CNY") "元" else it.currency}" } ?: "未提供"}\n") }
    }
    append("\n").append(value.note.ifBlank { "票价不代表实时余票。" })
    value.fetchedAt?.let { append("\n采样时刻：$it") }
    value.sources.forEach { append("\n来源：$it") }
}

/** Remove only receipt rows whose identity, interval, times and every amount match a parsed card. */
internal fun farePresentationBody(source: String, results: List<DisplayResult>): String {
    val fares = results.filterIsInstance<TicketFareDisplay>().map { it.value }
    if (fares.isEmpty()) return source
    val scope = Regex("^(.+)→(.+)（(\\d{4}-\\d{2}-\\d{2})）票价：$")
    val row = Regex("^([A-Z]?[0-9]{1,5}[A-Z]?) (.+?)→(.+?) (.+?)–(.+?)（历时 (.+?)）：(.+)$")
    val price = Regex("^(.+?) ([0-9]+(?:\\.[0-9]+)?) 元$")
    return source.split(Regex("\\n[ \\t]*\\n")).joinToString("\n\n") { block ->
        val lines = block.lines()
        val heading = lines.mapNotNull { scope.matchEntire(it.trim()) }.singleOrNull()
        if (heading == null) block else {
            val candidates = fares.filter { it.date == heading.groupValues[3] }
            val covered = lines.filter { line ->
                val match = row.matchEntire(line.trim()) ?: return@filter false
                val seats = match.groupValues[7].split("、").map { price.matchEntire(it.trim()) }
                candidates.any { fare ->
                    fare.status == "success" && fare.trainCode == match.groupValues[1] &&
                        fare.fromStation == match.groupValues[2] && fare.toStation == match.groupValues[3] &&
                        fare.startTime == match.groupValues[4] && fare.arriveTime == match.groupValues[5] &&
                        fare.duration == match.groupValues[6] && seats.all { it != null } &&
                        fare.prices.all { it.currency == "CNY" && it.amount != null } &&
                        seats.map { "${it!!.groupValues[1]}:${it.groupValues[2].toBigDecimal().stripTrailingZeros().toPlainString()}" }.sorted() ==
                        fare.prices.map { "${it.seat}:${it.amount!!.toBigDecimal().stripTrailingZeros().toPlainString()}" }.sorted()
                }
            }.toSet()
            lines.filterNot { it in covered }.joinToString("\n")
        }
    }
}

@Composable
internal fun FareReceiptCard(lines: List<FareReceiptLine>) {
    FareCardFrame {
        lines.forEach { line ->
            when (line) {
                is FareReceiptLine.Text -> if (line.value.isNotBlank()) {
                    Column(Modifier.fillMaxWidth().background(NativeColors.panel, RoundedCornerShape(8.appDp)).padding(10.appDp)) {
                        MarkdownAnswer(line.value)
                    }
                }
                is FareReceiptLine.Prices -> {
                    BasicText(line.row.description, Modifier.fillMaxWidth().background(NativeColors.selected, RoundedCornerShape(8.appDp)).padding(10.appDp), style = TextStyle(color = NativeColors.ink,
                        fontSize = 16.appSp, lineHeight = 24.appSp, fontWeight = FontWeight.SemiBold))
                    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(1.appDp)) {
                        FareSeatRow("席别", "票价", header = true)
                        line.row.seats.forEach { FareSeatRow(it.label, "${it.amount} 元") }
                    }
                }
            }
        }
    }
}

@Composable
private fun FareSeatRow(label: String, amount: String, header: Boolean = false) {
    Row(Modifier.fillMaxWidth().background(if (header) NativeColors.panel else NativeColors.surface,
        RoundedCornerShape(8.appDp)).heightIn(min = 44.appDp)
        .padding(horizontal = 12.appDp, vertical = 10.appDp),
        horizontalArrangement = Arrangement.spacedBy(12.appDp)) {
        BasicText(label, Modifier.weight(1f), style = TextStyle(
            color = if (header) NativeColors.muted else NativeColors.ink, fontSize = 16.appSp))
        BasicText(amount, style = TextStyle(color = if (header) NativeColors.muted else NativeColors.blue,
            fontSize = 16.appSp, fontWeight = if (header) FontWeight.Normal else FontWeight.SemiBold))
    }
}
