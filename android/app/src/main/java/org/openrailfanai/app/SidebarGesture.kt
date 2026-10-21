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

/** 子控件优先处理拖动；只接管明显且未被消费的单指横划。 */
@Composable
internal fun Modifier.sidebarSwipe(opening: Boolean, onSwipe: () -> Unit): Modifier {
    val callback by rememberUpdatedState(onSwipe)
    return pointerInput(opening) {
        val threshold = 64.dp.toPx()
        awaitEachGesture {
            val down = awaitFirstDown(requireUnconsumed = false)
            var previous = down.position
            var horizontal = 0f
            var vertical = 0f
            while (true) {
                val event = awaitPointerEvent(PointerEventPass.Final)
                if (event.changes.count { it.pressed } > 1) break
                val change = event.changes.firstOrNull { it.id == down.id } ?: break
                if (!change.pressed || change.isConsumed) break
                val delta = change.position - previous
                previous = change.position
                horizontal += delta.x; vertical += delta.y
                if (abs(vertical) > viewConfiguration.touchSlop && abs(vertical) > abs(horizontal)) break
                val direction = if (opening) horizontal else -horizontal
                if (direction >= threshold && abs(horizontal) > abs(vertical) * 1.8f) {
                    change.consume(); callback(); break
                }
            }
        }
    }
}
