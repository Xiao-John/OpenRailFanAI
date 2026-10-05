package org.openrailfanai.app

import org.commonmark.parser.Parser
import org.commonmark.ext.gfm.tables.TablesExtension
import org.commonmark.ext.gfm.strikethrough.StrikethroughExtension
import org.commonmark.node.Node
import java.net.URI

/** 与平台无关的标准语法解析；禁止将模型提供的HTML作为网页执行。 */
internal object MarkdownDocument {
    private val parser = Parser.builder().extensions(listOf(TablesExtension.create(), StrikethroughExtension.create())).build()
    fun parse(source: String): Node = parser.parse(source)
    fun safeLink(destination: String): String? = runCatching {
        val uri = URI(destination)
        if (uri.scheme?.lowercase() in setOf("http", "https", "mailto")) destination else null
    }.getOrNull()
}

internal fun Node.children(): List<Node> = buildList {
    var next = firstChild
    while (next != null) { add(next); next = next.next }
}
