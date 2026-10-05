package org.openrailfanai.app

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.*
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import org.commonmark.node.*
import org.commonmark.ext.gfm.tables.*
import org.commonmark.ext.gfm.strikethrough.Strikethrough

@Composable
internal fun MarkdownAnswer(source: String, modifier: Modifier = Modifier, firstTextTag: String? = null) {
    val document = remember(source) { MarkdownDocument.parse(source) }
    SelectionContainer(modifier) {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            document.children().forEachIndexed { index, block ->
                if (index == 0 && block is org.commonmark.node.Paragraph && firstTextTag != null &&
                    FareReceiptParser.parse(inlineText(block).text) == null)
                    BasicText(inlineText(block), Modifier.testTag(firstTextTag), style = bodyStyle())
                else MarkdownBlock(block)
            }
        }
    }
}

@Composable
private fun MarkdownBlock(node: Node, depth: Int = 0) {
    if (depth > 16) { MarkdownText(node); return }
    when (node) {
        is org.commonmark.node.Paragraph -> {
            val fare = FareReceiptParser.parse(inlineText(node).text)
            if (fare != null) FareReceiptCard(fare) else MarkdownText(node)
        }
        is Heading -> MarkdownText(node, TextStyle(color = NativeColors.ink,
            fontSize = (when(node.level) { 1 -> 24; 2 -> 21; else -> 18 }).appSp,
            fontWeight = FontWeight.SemiBold, lineHeight = 28.appSp))
        is FencedCodeBlock -> CodeBlock(node.literal)
        is IndentedCodeBlock -> CodeBlock(node.literal)
        is ThematicBreak -> Spacer(Modifier.fillMaxWidth().height(1.dp).background(NativeColors.line))
        is BlockQuote -> Row(Modifier.fillMaxWidth()) {
            Spacer(Modifier.width(3.dp).heightIn(min = 24.dp).background(NativeColors.line))
            Column(Modifier.weight(1f).padding(start = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                node.children().forEach { MarkdownBlock(it, depth+1) }
            }
        }
        is BulletList, is OrderedList -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            node.children().forEachIndexed { index, item ->
                Row(Modifier.fillMaxWidth()) {
                    BasicText(if (node is OrderedList) "${node.startNumber+index}." else "•",
                        Modifier.widthIn(min = 24.dp).padding(end = 6.dp), style = bodyStyle())
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        item.children().forEach { MarkdownBlock(it, depth+1) }
                    }
                }
            }
        }
        is TableBlock -> {
            val labels = node.children().firstOrNull()?.children()?.firstOrNull()
                ?.children()?.map { inlineText(it).text.trim() }.orEmpty()
            if (labels == listOf("席别", "票价")) FareCardFrame { MarkdownTable(node) }
            else MarkdownTable(node)
        }
        is HtmlBlock -> BasicText(node.literal, style = bodyStyle())
        else -> if (node.firstChild != null) node.children().forEach { MarkdownBlock(it, depth+1) }
    }
}

private fun bodyStyle() = TextStyle(color = NativeColors.ink, fontSize = 16.appSp, lineHeight = 24.appSp)

@Composable
private fun MarkdownText(node: Node, style: TextStyle = bodyStyle()) {
    BasicText(inlineText(node), style = style)
}

@Composable
private fun CodeBlock(literal: String) {
    Box(Modifier.fillMaxWidth().background(NativeColors.panel, RoundedCornerShape(10.dp))
        .border(1.dp, NativeColors.line, RoundedCornerShape(10.dp)).padding(12.dp)) {
        BasicText(literal.removeSuffix("\n"), Modifier.horizontalScroll(rememberScrollState()),
            style = bodyStyle().copy(fontFamily = FontFamily.Monospace, fontSize = 14.appSp))
    }
}

@Composable
private fun MarkdownTable(node: TableBlock) {
    val rows = node.children().flatMap { it.children() }
    val count = rows.maxOfOrNull { it.children().size } ?: return
    if (count == 0) return
    BoxWithConstraints(Modifier.fillMaxWidth().testTag("markdown-table")) {
        // Small fare tables fit the message width; wide tables scroll rather than squeeze text.
        val cellWidth = maxOf(maxWidth/count, 110.dp)
        Column(Modifier.horizontalScroll(rememberScrollState()).width(cellWidth*count)
            .border(1.dp, NativeColors.line, RoundedCornerShape(8.dp))) {
            rows.forEach { row ->
                val header = row.parent is TableHead
                Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min)
                    .background(if (header) NativeColors.panel else NativeColors.surface)) {
                    row.children().forEach { cell ->
                        Box(Modifier.width(cellWidth).fillMaxHeight().heightIn(min = 44.dp)
                            .border(.5.dp, NativeColors.line).padding(horizontal = 12.dp, vertical = 10.dp)) {
                            val align = when((cell as? TableCell)?.alignment) {
                                TableCell.Alignment.RIGHT -> TextAlign.End
                                TableCell.Alignment.CENTER -> TextAlign.Center
                                else -> TextAlign.Start
                            }
                            BasicText(inlineText(cell), Modifier.fillMaxWidth(), style = bodyStyle().copy(
                                fontWeight = if (header) FontWeight.SemiBold else FontWeight.Normal, textAlign = align))
                        }
                    }
                }
            }
        }
    }
}

private fun inlineText(node: Node): AnnotatedString = buildAnnotatedString {
    fun visit(n: Node) {
        fun children() { n.children().forEach { visit(it) } }
        when (n) {
            is Text -> append(n.literal)
            is SoftLineBreak, is HardLineBreak -> append("\n")
            is StrongEmphasis -> withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { children() }
            is Emphasis -> withStyle(SpanStyle(fontStyle = FontStyle.Italic)) { children() }
            is Strikethrough -> withStyle(SpanStyle(textDecoration = TextDecoration.LineThrough)) { children() }
            is Code -> withStyle(SpanStyle(fontFamily = FontFamily.Monospace, background = NativeColors.panel)) { append(n.literal) }
            is Link -> {
                val safe = MarkdownDocument.safeLink(n.destination)
                if (safe != null) withLink(LinkAnnotation.Url(safe, TextLinkStyles(
                    style = SpanStyle(color = NativeColors.blue, textDecoration = TextDecoration.Underline)))) { children() }
                else children()
            }
            is Image -> {
                append("图片：")
                val safe = MarkdownDocument.safeLink(n.destination)
                if (safe != null) withLink(LinkAnnotation.Url(safe)) { children() } else children()
            }
            is HtmlInline -> append(n.literal)
            else -> children()
        }
    }
    visit(node)
}
