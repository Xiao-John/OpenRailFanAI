package org.openrailfanai.app

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
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
internal fun FareCardFrame(content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxWidth().testTag("fare-card")
        .background(NativeColors.surface, RoundedCornerShape(16.appDp))
        .border(1.appDp, NativeColors.line, RoundedCornerShape(16.appDp))
        .padding(14.appDp), verticalArrangement = Arrangement.spacedBy(12.appDp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            BasicText("车票票价", style = TextStyle(color = NativeColors.ink,
                fontSize = 20.appSp, fontWeight = FontWeight.Bold))
            BasicText("票价信息", style = TextStyle(color = NativeColors.muted, fontSize = 13.appSp))
        }
        content()
    }
}

@Composable
internal fun FareReceiptCard(lines: List<FareReceiptLine>) {
    FareCardFrame {
        lines.forEach { line ->
            when (line) {
                is FareReceiptLine.Text -> if (line.value.isNotBlank()) MarkdownAnswer(line.value)
                is FareReceiptLine.Prices -> {
                    BasicText(line.row.description, style = TextStyle(color = NativeColors.ink,
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
