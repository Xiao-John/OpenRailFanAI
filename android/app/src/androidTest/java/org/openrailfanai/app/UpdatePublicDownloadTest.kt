package org.openrailfanai.app

import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import java.io.File

/** Explicitly selected live probe: localhost metadata, real public APK transfer, then cancel. */
class UpdatePublicDownloadTest {
    @Test fun publicReleaseReportsBytesBeforeCancellation() {
        val args = InstrumentationRegistry.getArguments()
        org.junit.Assume.assumeTrue("Only run with liveUpdates=true and adb reverse", args.getString("liveUpdates") == "true")
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val client = UpdateClient("http://127.0.0.1:8000")
        val metadata = client.software("0.1.23", "arm64-v8a")
        val asset = metadata.getJSONObject("asset")
        val directory = File(context.cacheDir, "live-update-probe-${System.nanoTime()}").apply { mkdirs() }
        var received = 0L
        try {
            val failure = runCatching {
                client.download("0.1.23", metadata.getString("latest_version"), "arm64-v8a", directory, asset.getString("sha256")) { event ->
                    if (event.optString("stage") == "download" && event.optLong("completed") > 0) {
                        received = event.getLong("completed")
                        android.util.Log.i("UPDATE_LIVE", "actual_public_bytes=$received total=${event.getLong("total")} percent=${received * 100 / event.getLong("total")}")
                        client.cancel()
                    }
                }
            }
            assertTrue(failure.isFailure)
            assertTrue("Must receive real public APK bytes; failure=${failure.exceptionOrNull()?.javaClass?.simpleName}: ${failure.exceptionOrNull()?.message}", received > 0)
            assertTrue(directory.listFiles()!!.isEmpty())
        } finally { client.cancel(); directory.deleteRecursively() }
    }
}
