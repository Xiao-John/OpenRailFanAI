package org.openrailfanai.app

import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Paint
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.drawscope.withTransform
import kotlin.math.floor
import kotlin.math.round

/** Draw source-derived vector ink at its original viewBox proportions. */
internal fun DrawScope.drawDesignGlyph(glyph: DesignGlyph, darkTint: Color? = null) {
    val xScale = size.width / glyph.width
    val yScale = size.height / glyph.height
    withTransform({ scale(size.width / glyph.width, size.height / glyph.height, Offset.Zero) }) {
        if (darkTint != null && glyph.tintPath != null) drawPath(glyph.tintPath, darkTint)
        // A continuous source contour keeps subpixel hairlines visible. Shade
        // cells are then painted over it; enclosed apertures stay in the path.
        if (darkTint == null && glyph.tintPath != null && glyph.coverageUnderlay != null)
            drawPath(glyph.tintPath, glyph.coverageUnderlay)
        // These adjacent shade regions already contain source antialias colours.
        // A second antialias pass along every internal boundary creates pale seams.
        val sourcePaint = if (glyph.sourceShadedCells && darkTint == null) Paint().apply { isAntiAlias = false } else null
        glyph.layers.forEach { layer ->
            if (darkTint != null && glyph.tintPath != null && layer.tintable) return@forEach
            if (sourcePaint != null) {
                sourcePaint.color = layer.color
                drawIntoCanvas { it.drawPath(layer.path, sourcePaint) }
            } else drawPath(layer.path, if (layer.tintable) darkTint ?: layer.color else layer.color)
        }
        // A complete isolated source hairline must remain visible when its
        // projected thickness is less than one device pixel. Snap the thin
        // axis to the pixel grid; do not enlarge other contours or apertures.
        glyph.hairlines.forEach { strip ->
            if (strip.width * xScale < 1f || strip.height * yScale < 1f) {
                val left = if (strip.width * xScale < 1f)
                    floor((strip.x + strip.width / 2f) * xScale) else round(strip.x * xScale)
                val top = if (strip.height * yScale < 1f)
                    floor((strip.y + strip.height / 2f) * yScale) else round(strip.y * yScale)
                val right = if (strip.width * xScale < 1f) left + 1f else round((strip.x + strip.width) * xScale)
                val bottom = if (strip.height * yScale < 1f) top + 1f else round((strip.y + strip.height) * yScale)
                val paint = Paint().apply { isAntiAlias = false; color = darkTint ?: strip.color }
                drawIntoCanvas { it.drawRect(Rect(left / xScale, top / yScale, right / xScale, bottom / yScale), paint) }
            }
        }
    }
}
