package org.openrailfanai.app

import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.unit.dp
import kotlin.math.abs

/** 整页识别单指侧滑；方向明确后在 Initial pass 接管，取消子按钮点击。 */
@Composable
internal fun Modifier.sidebarSwipe(opening: Boolean, onSwipe: () -> Unit): Modifier {
    val callback by rememberUpdatedState(onSwipe)
    return pointerInput(opening) {
        val threshold = 40.dp.toPx()
        awaitEachGesture {
            val down = awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Initial)
            var previous = down.position
            var horizontal = 0f
            var vertical = 0f
            var horizontalIntent = false
            while (true) {
                val event = awaitPointerEvent(PointerEventPass.Initial)
                if (event.changes.count { it.pressed } > 1) break
                val change = event.changes.firstOrNull { it.id == down.id } ?: break
                if (!change.pressed) break
                val delta = change.position - previous
                previous = change.position
                horizontal += delta.x; vertical += delta.y
                val direction = if (opening) horizontal else -horizontal
                if (!horizontalIntent && abs(vertical) > viewConfiguration.touchSlop && abs(vertical) > abs(horizontal)) break
                if (!horizontalIntent && abs(horizontal) > viewConfiguration.touchSlop && abs(horizontal) > abs(vertical) * 1.5f) {
                    if (direction <= 0f) break
                    horizontalIntent = true
                }
                if (horizontalIntent) change.consume()
                if (horizontalIntent && direction >= threshold) {
                    callback()
                    break
                }
            }
        }
    }
}
