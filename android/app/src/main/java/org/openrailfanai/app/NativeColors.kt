package org.openrailfanai.app

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.Color

/** Palette for the native Main surface; preference changes update all composed routes. */
object NativeColors {
    var preference by mutableStateOf("auto")
    var systemDark by mutableStateOf(false)
    val dark: Boolean get() = preference == "dark" || (preference == "auto" && systemDark)
    val ink: Color get() = if (dark) Color(0xFFE6ECF8) else Color(0xFF0B1738)
    val muted: Color get() = if (dark) Color(0xFFA8B7D1) else Color(0xFF687A9C)
    val line: Color get() = if (dark) Color(0xFF35435A) else Color(0xFFDCE5F2)
    val blue: Color get() = Color(0xFF2563EB)
    val panel: Color get() = if (dark) Color(0xFF202B3C) else Color(0xFFF2F6FB)
    val surface: Color get() = if (dark) Color(0xFF172131) else Color.White
    val background: Color get() = if (dark) Color(0xFF101827) else Color(0xFFF8FAFD)
    val selected: Color get() = if (dark) Color(0xFF203B69) else Color(0xFFEAF2FF)
    val danger: Color get() = Color(0xFFF0524F)
}
