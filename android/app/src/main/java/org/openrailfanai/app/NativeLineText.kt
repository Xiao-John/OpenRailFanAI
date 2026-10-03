package org.openrailfanai.app

import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.layout
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.constrainHeight
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.platform.LocalDensity
import kotlin.math.roundToInt

/** BasicText whose single line uses an explicit outer line box; wrapped text keeps its natural height. */
@Composable
fun NativeLineText(
    text: AnnotatedString,
    lineHeight: TextUnit,
    modifier: Modifier = Modifier,
    style: TextStyle,
    testTag: String? = null,
) {
    var lineCount by remember(text) { mutableIntStateOf(1) }
    val density = LocalDensity.current
    val lineBoxPx = with(density) { lineHeight.toPx().roundToInt().coerceAtLeast(1) }

    val measuredModifier = Modifier.layout { measurable, constraints ->
        val placeable = measurable.measure(constraints)
        val boxHeight = if (lineCount == 1) {
            constraints.constrainHeight(lineBoxPx)
        } else {
            constraints.constrainHeight(placeable.height)
        }
        layout(placeable.width, boxHeight) {
            val y = if (lineCount == 1) (boxHeight - placeable.height) / 2 else 0
            placeable.placeRelative(0, y)
        }
    }

    BasicText(
        text = text,
        modifier = modifier.then(measuredModifier).then(if (testTag == null) Modifier else Modifier.testTag(testTag)),
        style = style,
        onTextLayout = { result: TextLayoutResult -> lineCount = result.lineCount },
    )
}

@Composable
fun NativeLineText(
    text: String,
    lineHeight: TextUnit,
    modifier: Modifier = Modifier,
    style: TextStyle,
    testTag: String? = null,
) = NativeLineText(AnnotatedString(text), lineHeight, modifier, style, testTag)
