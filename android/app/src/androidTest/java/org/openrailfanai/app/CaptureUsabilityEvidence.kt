package org.openrailfanai.app

import android.graphics.Bitmap
import android.util.Base64
import android.util.Log
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.test.SemanticsNodeInteraction
import androidx.compose.ui.test.captureToImage
import java.io.ByteArrayOutputStream

@Composable
internal fun UsabilityCaptureRoot(content: @Composable () -> Unit) {
    CompositionLocalProvider(androidx.compose.ui.platform.LocalDensity provides Density(1f, 1f)) {
        Box(Modifier.size(390.dp, 844.dp).testTag("usability-capture-root")) {
            content()
        }
    }
}

internal fun SemanticsNodeInteraction.logUsabilityCapture(slug: String) {
    val bitmap = captureToImage().asAndroidBitmap()
    check(bitmap.width == 390 && bitmap.height == 844) {
        "Usability capture must be 390x844, got ${bitmap.width}x${bitmap.height}"
    }
    val bytes = ByteArrayOutputStream().use { stream ->
        check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, stream))
        stream.toByteArray()
    }
    val encoded = Base64.encodeToString(bytes, Base64.NO_WRAP)
    val chunks = encoded.chunked(2_000)
    chunks.forEachIndexed { index, chunk ->
        Log.i("UsabilityCapture", "UX_CAPTURE|$slug|$index|${chunks.size}|$chunk")
    }
}
