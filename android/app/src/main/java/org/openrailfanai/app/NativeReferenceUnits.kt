package org.openrailfanai.app

import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Converts legacy visual dimensions, authored against the 406px inner content
 * frame, to the complete application canvas (438px, including page padding).
 * This changes design dimensions, never platform density, user font scale,
 * operating-system insets or the independently specified 44dp touch minimum.
 */
private const val APP_REFERENCE_RATIO = 406f / 438f

internal val Int.appDp: Dp get() = (this * APP_REFERENCE_RATIO).dp
internal val Float.appDp: Dp get() = (this * APP_REFERENCE_RATIO).dp
internal val Double.appDp: Dp get() = (toFloat() * APP_REFERENCE_RATIO).dp
internal val Int.appSp: TextUnit get() = (this * APP_REFERENCE_RATIO).sp
internal val Float.appSp: TextUnit get() = (this * APP_REFERENCE_RATIO).sp
internal val Double.appSp: TextUnit get() = (toFloat() * APP_REFERENCE_RATIO).sp
