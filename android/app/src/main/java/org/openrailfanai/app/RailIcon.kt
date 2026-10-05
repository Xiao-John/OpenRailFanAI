package org.openrailfanai.app

import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp

/** 核心标志保留品牌几何；功能图标统一使用固定版本的 Lucide 矢量。 */
@Composable
internal fun RailIcon(name: String, modifier: Modifier = Modifier, tint: Color = NativeColors.blue, semanticTag: String? = null) {
    val resource = when (name) {
        "more" -> R.drawable.ic_more_lucide
        "calendar" -> R.drawable.ic_calendar_lucide
        "clock" -> R.drawable.ic_clock_lucide
        "link" -> R.drawable.ic_link_lucide
        "info" -> R.drawable.ic_info_lucide
        "chevron" -> R.drawable.ic_chevron_lucide
        "chevron-down" -> R.drawable.ic_chevron_down_lucide
        "chevron-up" -> R.drawable.ic_chevron_up_lucide
        "chevron-left" -> R.drawable.ic_chevron_left_lucide
        "search" -> R.drawable.ic_search_lucide
        "train-search" -> R.drawable.ic_train_search_lucide
        "person" -> R.drawable.ic_person_lucide
        "cloud-error" -> R.drawable.ic_cloud_error_lucide
        "file" -> R.drawable.ic_file_lucide
        "plus" -> R.drawable.ic_plus_lucide
        "back" -> R.drawable.ic_back_lucide
        "down" -> R.drawable.ic_down_lucide
        "up" -> R.drawable.ic_up_lucide
        "edit" -> R.drawable.ic_edit_lucide
        "trash" -> R.drawable.ic_trash_lucide
        "history" -> R.drawable.ic_history_lucide
        "settings" -> R.drawable.ic_settings_lucide
        "help" -> R.drawable.ic_help_lucide
        "close" -> R.drawable.ic_close_lucide
        "stop" -> R.drawable.ic_stop_lucide
        "send" -> R.drawable.ic_send_lucide
        "check" -> R.drawable.ic_check_lucide
        "warning" -> R.drawable.ic_warning_lucide
        "share" -> R.drawable.ic_share_lucide
        "copy" -> R.drawable.ic_copy_lucide
        "refresh" -> R.drawable.ic_refresh_lucide
        else -> null
    }
    val painter = resource?.let { painterResource(it) }
    Canvas(modifier.testTag(semanticTag ?: "icon-$name")) {
        val s = size.minDimension
        val left = (size.width - s) / 2f
        val top = (size.height - s) / 2f
        fun p(x: Float, y: Float) = Offset(left + x * s, top + y * s)
        if (painter != null) {
            withTransform({ translate(left, top) }) {
                with(painter) { draw(Size(s, s), colorFilter = ColorFilter.tint(tint)) }
            }
        } else when (name) {
            "train-logo" -> {
                // Header and reply avatars share the same source-derived 32×32
                // brand geometry, keeping both marks crisp at their existing sizes.
                fun brandPoint(x: Float, y: Float) = p(x / 32f, y / 32f)
                val brandBlue = if (NativeColors.dark) tint else Color(0xFF075BFF)
                val body = Path().apply {
                    moveTo(brandPoint(4.5f, 23f).x, brandPoint(4.5f, 23f).y)
                    lineTo(brandPoint(5.6f, 8f).x, brandPoint(5.6f, 8f).y)
                    cubicTo(brandPoint(6f, 3.4f).x, brandPoint(6f, 3.4f).y,
                        brandPoint(8.5f, 1.5f).x, brandPoint(8.5f, 1.5f).y,
                        brandPoint(16f, 1.5f).x, brandPoint(16f, 1.5f).y)
                    cubicTo(brandPoint(23.5f, 1.5f).x, brandPoint(23.5f, 1.5f).y,
                        brandPoint(26f, 3.4f).x, brandPoint(26f, 3.4f).y,
                        brandPoint(26.4f, 8f).x, brandPoint(26.4f, 8f).y)
                    lineTo(brandPoint(27.5f, 23f).x, brandPoint(27.5f, 23f).y)
                    cubicTo(brandPoint(27.5f, 24.66f).x, brandPoint(27.5f, 24.66f).y,
                        brandPoint(26.16f, 26f).x, brandPoint(26.16f, 26f).y,
                        brandPoint(24.5f, 26f).x, brandPoint(24.5f, 26f).y)
                    lineTo(brandPoint(7.5f, 26f).x, brandPoint(7.5f, 26f).y)
                    cubicTo(brandPoint(5.84f, 26f).x, brandPoint(5.84f, 26f).y,
                        brandPoint(4.5f, 24.66f).x, brandPoint(4.5f, 24.66f).y,
                        brandPoint(4.5f, 23f).x, brandPoint(4.5f, 23f).y)
                    close()
                }
                drawPath(body, brandBlue)
                val railStroke = (2.2f / 32f * s).coerceAtLeast(1.dp.toPx())
                drawLine(brandBlue, brandPoint(8f, 28f), brandPoint(3f, 31f), railStroke, cap = StrokeCap.Round)
                drawLine(brandBlue, brandPoint(24f, 28f), brandPoint(29f, 31f), railStroke, cap = StrokeCap.Round)
                drawLine(brandBlue, brandPoint(6f, 29.5f), brandPoint(26f, 29.5f), railStroke, cap = StrokeCap.Round)
                drawRect(Color.White, brandPoint(11f, 4f), Size(s * 10f / 32f, s * 2f / 32f))
                drawRect(Color.White, brandPoint(8f, 9f), Size(s * 6.5f / 32f, s * 6f / 32f))
                drawRect(Color.White, brandPoint(17f, 9f), Size(s * 7f / 32f, s * 6f / 32f))
                drawCircle(Color.White, s * 1.65f / 32f, brandPoint(10f, 21f))
                drawCircle(Color.White, s * 1.65f / 32f, brandPoint(22f, 21f))
            }
        }
    }
}
