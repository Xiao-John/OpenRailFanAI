package org.openrailfanai.app

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.Dp
import androidx.compose.foundation.layout.Arrangement

/** 短促、有限的动画；使用 Compose 的系统时长缩放，不引入持续动效。 */
@Composable
internal fun RailReveal(visible: Boolean, modifier: Modifier = Modifier, spacing: Dp = 0.dp, content: @Composable () -> Unit) {
    AnimatedVisibility(visible, modifier,
        enter = fadeIn(tween(160)) + expandVertically(tween(200, easing = FastOutSlowInEasing), expandFrom = Alignment.Top),
        exit = fadeOut(tween(120)) + shrinkVertically(tween(180, easing = FastOutSlowInEasing), shrinkTowards = Alignment.Top)) {
        androidx.compose.foundation.layout.Column(verticalArrangement = Arrangement.spacedBy(spacing)) { content() }
    }
}

/** 只改变绘制，不改变最终几何或重建页面状态。 */
@Composable
internal fun Modifier.railEntrance(fromLeft: Boolean = false): Modifier {
    var entered by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { entered = true }
    val progress by animateFloatAsState(if (entered) 1f else 0f,
        tween(220, easing = FastOutSlowInEasing), label = "rail-entrance")
    return graphicsLayer {
        alpha = progress
        if (fromLeft) translationX = -(1f-progress)*size.width
        else translationY = (1f-progress)*6.dp.toPx()
    }
}
