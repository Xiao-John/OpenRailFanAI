package org.openrailfanai.app

import android.util.Base64
import android.util.Log
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.click
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextClearance
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.time.LocalDate
import kotlin.math.roundToInt

/** Uses the same production MainChatScreen, result cards and HistoryScreen as MainComposeActivity. */
@RunWith(AndroidJUnit4::class)
class NativeAcceptanceCaptureTest {
    @get:Rule val compose = createComposeRule()
    private var fixtureStore: ConversationStore? = null

    @Test fun composerTracksChangingSafeBottomWithoutExtraGap() {
        var bottom by mutableStateOf(0)
        val events = mutableListOf<String>()
        compose.setContent {
            CompositionLocalProvider(LocalDensity provides Density(1f, 1f)) {
                MainFixture("train_schedule", events, systemInsetsOverride = WindowInsets(0, 24, 0, bottom))
            }
        }
        // Navigation-free, gesture-sized, three-button-sized and keyboard-sized
        // effective safe areas; this is controlled layout coverage, not a real IME test.
        for (inset in listOf(0, 24, 48, 300, 24, 0)) {
            compose.runOnIdle { bottom = inset }
            compose.waitForIdle()
            val root = compose.onNodeWithTag("native-capture-root").fetchSemanticsNode().boundsInRoot
            val safe = compose.onNodeWithTag("main-safe-content").fetchSemanticsNode().boundsInRoot
            val composer = compose.onNodeWithTag("main-fixed-input").fetchSemanticsNode().boundsInRoot
            assertEquals("safe bottom for inset=$inset", root.bottom - inset, safe.bottom, .5f)
            assertEquals("no extra gap for inset=$inset", safe.bottom, composer.bottom, .5f)
            assertTrue("composer stays inside safe area for inset=$inset", composer.top >= safe.top)
        }
    }

    @Test fun copyActionReceivesRealTap() {
        NativeColors.preference = "light"
        NativeColors.systemDark = false
        val events = mutableListOf<String>()
        val state = "train_schedule"
        compose.setContent {
            CompositionLocalProvider(LocalDensity provides Density(1f, 1f)) { MainFixture(state, events) }
        }
        compose.waitForIdle()
        compose.onNodeWithTag("result-copy-action", useUnmergedTree = true).performScrollTo()
        compose.waitForIdle()
        val nodes = compose.onAllNodesWithTag("result-copy-action", useUnmergedTree = true).fetchSemanticsNodes()
        assertEquals("copy target match count", 1, nodes.size)
        val target = nodes.single()
        val bounds = target.boundsInRoot
        val root = compose.onNodeWithTag("native-capture-root").fetchSemanticsNode().boundsInRoot
        val input = compose.onNodeWithTag("main-fixed-input").fetchSemanticsNode().boundsInRoot
        val visible = bounds.right > bounds.left && bounds.bottom > bounds.top && bounds.left >= root.left && bounds.top >= root.top && bounds.right <= root.right && bounds.bottom <= root.bottom
        val overlapsInput = bounds.left < input.right && bounds.right > input.left && bounds.top < input.bottom && bounds.bottom > input.top
        val hasClickAction = target.config.contains(SemanticsActions.OnClick)
        val diagnostic = "matches=${nodes.size}; bounds=$bounds; root=$root; fixedInput=$input; visibleInRoot=$visible; overlapsInput=$overlapsInput; semanticsOnClick=$hasClickAction"
        Log.i("NativeAcceptance", "COPY_TARGET|$diagnostic")
        compose.onNodeWithTag("result-copy-action", useUnmergedTree = true).assertIsDisplayed()
        assertTrue("copy target has no click semantics: $diagnostic", hasClickAction)
        compose.onNodeWithTag("result-copy-action", useUnmergedTree = true).performTouchInput { click() }
        assertTrue("real tap did not invoke copy callback: $diagnostic; events=$events", events.contains("copy:G8932"))
    }

    @Test fun diagnosesTrainCaptureScrollPosition() {
        NativeColors.preference = "light"
        NativeColors.systemDark = false
        val events = mutableListOf<String>()
        val records = mutableListOf<JSONObject>()
        var run by mutableStateOf(0)
        var insetRecord = Pair(intArrayOf(-1, -1, -1, -1), intArrayOf(-1, -1, -1, -1))
        var localDensityRecord = 1f
        var fontScaleRecord = 1f
        var scrollRecord = JSONObject()
        val layoutRecord = linkedMapOf<String, JSONObject>()
        compose.setContent {
            CompositionLocalProvider(LocalDensity provides Density(1f, 1f)) {
                androidx.compose.runtime.key(run) {
                    MainFixture("train_schedule", events,
                        insetObserver = { applied, platform, localDensity, fontScale ->
                            insetRecord = applied to platform
                            localDensityRecord = localDensity
                            fontScaleRecord = fontScale
                            Log.i("NativeAcceptance", "MT1_SAFE_INSETS|applied=${applied.contentToString()}|platformPx=${platform.contentToString()}|platformDensity=${InstrumentationRegistry.getInstrumentation().targetContext.resources.displayMetrics.density}|localDensity=1.0")
                        },
                        scrollObserver = { index, offset, start, end, visible ->
                            scrollRecord = JSONObject().put("firstVisibleItemIndex", index).put("firstVisibleItemScrollOffset", offset)
                                .put("viewportStartOffset", start).put("viewportEndOffset", end).put("visibleItemIndices", JSONArray(visible))
                        },
                        layoutObserver = { tag, constraints, size ->
                            layoutRecord[tag] = JSONObject().put("constraints_px", JSONArray(constraints.toList()))
                                .put("measured_size_px", JSONArray(size.toList()))
                        })
                }
            }
        }
        repeat(2) { sample ->
            if (sample > 0) compose.runOnIdle { run = sample }
            compose.waitForIdle()
            compose.waitUntil(5_000) { insetRecord.first[1] > 0 && insetRecord.first[3] > 0 && insetRecord.second[1] > 0 && insetRecord.second[3] > 0 }
            compose.waitForIdle()
            compose.waitUntil(5_000) {
                val list = compose.onNodeWithTag("main-message-list", useUnmergedTree = true).fetchSemanticsNode()
                val range = list.config.getOrNull(SemanticsProperties.VerticalScrollAxisRange)
                range != null && range.value() == 0f
            }
            var previousGeometry: List<Float>? = null
            var stableFrames = 0
            fun currentGeometry(): List<Float> {
                val rootBounds = compose.onNodeWithTag("native-capture-root").fetchSemanticsNode().boundsInRoot
                val listNode = compose.onNodeWithTag("main-message-list", useUnmergedTree = true).fetchSemanticsNode()
                val listRange = listNode.config.getOrNull(SemanticsProperties.VerticalScrollAxisRange)
                return listOf(listRange?.value()?.toFloat() ?: Float.NaN, listRange?.maxValue()?.toFloat() ?: Float.NaN) + listOf("main-topbar", "main-user-prompt-frame", "main-message-list", "schedule-G8932-success", "main-fixed-input")
                    .flatMap { tag ->
                        val node = compose.onAllNodesWithTag(tag, useUnmergedTree = true).fetchSemanticsNodes().singleOrNull()
                        node?.boundsInRoot?.let { listOf(it.left-rootBounds.left, it.top-rootBounds.top, it.right-rootBounds.left, it.bottom-rootBounds.top) }
                            ?: listOf(Float.NaN)
                    }
            }
            compose.waitUntil(5_000) {
                val current = currentGeometry()
                if (current == previousGeometry && current.none(Float::isNaN)) stableFrames++ else stableFrames = 0
                previousGeometry = current
                Thread.sleep(50)
                stableFrames >= 4
            }
            compose.waitForIdle()
            val root = compose.onNodeWithTag("native-capture-root").fetchSemanticsNode().boundsInRoot
            val names = listOf("main-safe-content", "main-topbar", "main-user-prompt-frame", "main-message-list", "schedule-G8932-success", "result-actions", "result-suggestions", "main-fixed-input")
            val geometry = JSONObject()
            names.forEach { tag ->
                val nodes = compose.onAllNodesWithTag(tag, useUnmergedTree = true).fetchSemanticsNodes()
                if (nodes.size == 1) {
                    val b = nodes.single().boundsInRoot
                    geometry.put(tag, JSONArray(listOf(b.left-root.left, b.top-root.top, b.right-root.left, b.bottom-root.top)))
                } else {
                    geometry.put(tag, JSONObject.NULL)
                }
            }
            val list = compose.onNodeWithTag("main-message-list", useUnmergedTree = true).fetchSemanticsNode()
            val range = list.config.getOrNull(SemanticsProperties.VerticalScrollAxisRange)
            val metrics = InstrumentationRegistry.getInstrumentation().targetContext.resources.displayMetrics
            val bitmap = compose.onNodeWithTag("native-capture-root").captureToImage().asAndroidBitmap()
            assertEquals(390, bitmap.width)
            assertEquals(844, bitmap.height)
            records += JSONObject().put("sample", sample + 1).put("root", JSONArray(listOf(root.left, root.top, root.right, root.bottom)))
                .put("platformDensity", metrics.density).put("localDensity", 1.0).put("rootConstraintsDp", JSONArray(listOf(390, 844)))
                .put("fontScale", InstrumentationRegistry.getInstrumentation().targetContext.resources.configuration.fontScale)
                .put("safeDrawingInsetsFixturePx", JSONArray(insetRecord.first)).put("safeDrawingInsetsPlatformPx", JSONArray(insetRecord.second))
                .put("scrollValue", range?.value?.invoke()).put("scrollMax", range?.maxValue?.invoke())
                .put("scroll", scrollRecord).put("rects", geometry)
                .put("layoutConstraints", JSONObject(layoutRecord.mapValues { it.value }))
        }
        val first = records[0].getJSONObject("rects")
        val second = records[1].getJSONObject("rects")
        val stableDeltas = JSONObject()
        listOf("main-safe-content", "main-topbar", "main-user-prompt-frame", "main-message-list", "schedule-G8932-success", "result-actions", "result-suggestions", "main-fixed-input").forEach { tag ->
            if (first.isNull(tag) || second.isNull(tag)) {
                stableDeltas.put(tag, JSONObject().put("status", "unmeasured").put("firstMatches", !first.isNull(tag)).put("secondMatches", !second.isNull(tag)))
                return@forEach
            }
            val a = first.getJSONArray(tag)
            val b = second.getJSONArray(tag)
            val deltas = JSONArray()
            for (i in 0 until 4) {
                val delta = kotlin.math.abs(a.getDouble(i) - b.getDouble(i))
                deltas.put(delta)
                if (delta > 2.0) stableDeltas.put("unstable_${tag}_$i", delta)
            }
            stableDeltas.put(tag, deltas)
        }
        val output = File(InstrumentationRegistry.getInstrumentation().targetContext.getExternalFilesDir(null), "acceptance/mt1-train-basis.json")
        output.parentFile?.mkdirs()
        output.writeText(JSONObject().put("state", "train_schedule").put("viewport", JSONArray(listOf(390, 844)))
            .put("samples", JSONArray(records)).put("deltas", stableDeltas).toString(2))
        Log.i("NativeAcceptance", "MT1_BASIS|${output.absolutePath}|${records.joinToString { it.toString() }}|deltas=$stableDeltas")
        assertTrue("independent train capture geometry differs by more than 2px: $stableDeltas", stableDeltas.keys().asSequence().none { it.startsWith("unstable_") })
    }

    @Test fun diagnosesReadingCaptureScrollPosition() {
        NativeColors.preference = "light"
        NativeColors.systemDark = false
        val events = mutableListOf<String>()
        compose.setContent {
            CompositionLocalProvider(LocalDensity provides Density(1f, 1f)) { MainFixture("reading_followup", events) }
        }
        compose.waitForIdle()
        val listNode = compose.onNodeWithTag("main-message-list", useUnmergedTree = true).fetchSemanticsNode()
        val range = listNode.config.getOrNull(SemanticsProperties.VerticalScrollAxisRange)
        val names = listOf("main-user-prompt-frame", "schedule-G8932-success", "return-to-bottom", "result-followup-1", "result-followup-2")
        val visibleItems = names.map { tag ->
            val nodes = compose.onAllNodesWithTag(tag, useUnmergedTree = true).fetchSemanticsNodes()
            val bounds = nodes.firstOrNull()?.boundsInRoot
            "$tag(count=${nodes.size},bounds=$bounds)"
        }
        val diagnostic = "state=reading_followup; listBounds=${listNode.boundsInRoot}; scroll=${range?.value?.invoke()}/${range?.maxValue?.invoke()}; items=${visibleItems.joinToString()}; events=$events"
        Log.i("NativeAcceptance", "SCROLL_DIAGNOSTIC|$diagnostic")
        assertTrue(diagnostic.isNotBlank())
    }

    @Test fun captureNineFixedMainStatesAt390By844Px() {
        InstrumentationRegistry.getArguments().getString("acceptanceRunToken")?.let { token ->
            require(token.matches(Regex("[A-Za-z0-9-]+")))
            Log.i("NativeAcceptance", "NATIVE_RUN|$token")
        }
        NativeColors.preference = "light"
        NativeColors.systemDark = false
        val output = File(InstrumentationRegistry.getInstrumentation().targetContext.getExternalFilesDir(null), "acceptance")
            .apply { mkdirs() }
        val measurements = mutableListOf<JSONObject>()
        val copyChecks = mutableListOf<JSONObject>()
        val states = listOf("train_schedule", "emu_routing", "query_loading", "batch_partial", "routing_empty", "connection_error", "reading_followup", "query_details", "history")
        var currentState by mutableStateOf("")
        var sceneEpoch by mutableStateOf(0)
        var insetRecord = Pair(intArrayOf(-1, -1, -1, -1), intArrayOf(-1, -1, -1, -1))
        var localDensityRecord = 1f
        var fontScaleRecord = 1f
        var scrollRecord: JSONObject? = null
        val layoutRecord = linkedMapOf<String, JSONObject>()
        val interactionEvents = mutableListOf<String>()
        compose.setContent {
            CompositionLocalProvider(LocalDensity provides Density(1f, 1f)) {
                androidx.compose.runtime.key(currentState to sceneEpoch) {
                    when (currentState) {
                        "history" -> HistoryFixture(interactionEvents,
                            insetObserver = { applied, platform, density, fontScale ->
                                insetRecord = applied to platform; localDensityRecord = density; fontScaleRecord = fontScale
                            })
                        else -> MainFixture(currentState, interactionEvents,
                            insetObserver = { applied, platform, density, fontScale ->
                                insetRecord = applied to platform; localDensityRecord = density; fontScaleRecord = fontScale
                            },
                            scrollObserver = { index, offset, start, end, visible ->
                                scrollRecord = JSONObject().put("first_visible_item_index", index)
                                    .put("first_visible_item_offset_px", offset).put("viewport_start_px", start)
                                    .put("viewport_end_px", end).put("visible_item_indices", JSONArray(visible))
                            },
                            layoutObserver = { tag, constraints, size ->
                                layoutRecord[tag] = JSONObject().put("constraints_px", JSONArray(constraints.toList()))
                                    .put("measured_size_px", JSONArray(size.toList()))
                            })
                    }
                }
            }
        }
        states.forEach { state ->
            insetRecord = Pair(intArrayOf(-1, -1, -1, -1), intArrayOf(-1, -1, -1, -1))
            scrollRecord = null
            layoutRecord.clear()
            compose.runOnIdle { currentState = state }
            compose.waitForIdle()
            if (state == "query_details") {
                compose.onNodeWithTag("query-details-toggle").performClick()
                interactionEvents += "details-expanded"
                compose.onNodeWithTag("technical-log-toggle").performScrollTo()
            }
            compose.waitForIdle()
            compose.waitUntil(5_000) {
                insetRecord.first[1] >= 0 && insetRecord.first[3] >= 0 &&
                    insetRecord.second[1] > 0 && insetRecord.second[3] > 0 &&
                    compose.onAllNodesWithTag(if (state == "history") "history-safe-content" else "main-safe-content", useUnmergedTree = true)
                        .fetchSemanticsNodes().size == 1
            }
            if (state == "history") {
                val scrollNode = compose.onNodeWithTag("history-scroll-container", useUnmergedTree = true).fetchSemanticsNode()
                val range = scrollNode.config.getOrNull(SemanticsProperties.VerticalScrollAxisRange)
                val bounds = scrollNode.boundsInRoot
                val rootForScroll = compose.onNodeWithTag("native-capture-root").fetchSemanticsNode().boundsInRoot
                val offset = range?.value?.invoke()?.roundToInt() ?: 0
                scrollRecord = JSONObject().put("scroll_offset_px", offset)
                    .put("scroll_max_px", range?.maxValue?.invoke()?.roundToInt())
                    .put("viewport_start_px", (bounds.top - rootForScroll.top).roundToInt())
                    .put("viewport_end_px", (bounds.bottom - rootForScroll.top).roundToInt())
            }
            compose.waitUntil(5_000) {
                val contentNodes = compose.onAllNodesWithTag(if (state == "history") "history-safe-content" else "main-safe-content", useUnmergedTree = true).fetchSemanticsNodes()
                val scrollReady = scrollRecord?.optInt("viewport_end_px", 0)?.let { it > (scrollRecord?.optInt("viewport_start_px", 0) ?: 0) } == true
                contentNodes.size == 1 && contentNodes.single().boundsInRoot.width > 0 && scrollReady
            }
            // Hold the same scene until its content geometry stops changing before measuring and capturing.
            var previousBasisGeometry: List<Float>? = null
            var stableBasisFrames = 0
            compose.waitUntil(5_000) {
                val rootNow = compose.onNodeWithTag("native-capture-root").fetchSemanticsNode().boundsInRoot
                val contentNow = compose.onNodeWithTag(if (state == "history") "history-safe-content" else "main-safe-content", useUnmergedTree = true).fetchSemanticsNode().boundsInRoot
                val current = listOf(contentNow.left-rootNow.left, contentNow.top-rootNow.top, contentNow.right-rootNow.left, contentNow.bottom-rootNow.top)
                if (current == previousBasisGeometry) stableBasisFrames++ else stableBasisFrames = 0
                previousBasisGeometry = current
                Thread.sleep(50)
                stableBasisFrames >= 4
            }
            compose.waitForIdle()
            val root = compose.onNodeWithTag("native-capture-root")
            val rootBounds = root.fetchSemanticsNode().boundsInRoot
            val copyBindings = JSONObject(InstrumentationRegistry.getInstrumentation().context.assets
                .open("native-copy-bindings.json").bufferedReader().use { it.readText() })
                .getJSONObject("states").getJSONArray(state)
            val copyTags = (0 until copyBindings.length()).mapNotNull { index ->
                copyBindings.getJSONObject(index).optString("native_tag").takeIf { it.isNotBlank() && it != "null" }
            }
            val iconStates = JSONObject(InstrumentationRegistry.getInstrumentation().context.assets
                .open("native-icon-tags.json").bufferedReader().use { it.readText() }).getJSONObject("states")
            val iconBindings = iconStates.optJSONArray(state) ?: JSONArray()
            val iconTags = (0 until iconBindings.length()).map { iconBindings.getString(it) }
            val targetTags = (targets(state) + copyTags + iconTags +
                if (state == "history") emptyList() else listOf("main-message-list")).distinct()
            copyExpectations(state).forEach { expected ->
                val matches = compose.onAllNodesWithText(expected, substring = false, useUnmergedTree = true).fetchSemanticsNodes()
                val visibleMatches = matches.count { node ->
                    val bounds = node.boundsInRoot
                    bounds.right > bounds.left && bounds.bottom > bounds.top && bounds.right > rootBounds.left && bounds.left < rootBounds.right && bounds.bottom > rootBounds.top && bounds.top < rootBounds.bottom
                }
                val present = visibleMatches > 0
                copyChecks += JSONObject().put("state", state).put("expected", expected).put("semantic_match_count", matches.size).put("visible_match_count", visibleMatches).put("exact_visible_match", present)
                val encoded = Base64.encodeToString(expected.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)
                Log.i("NativeAcceptance", "NATIVE_COPY|$state|$encoded|$present")
            }
            if (state == "query_loading") {
                val accessible = "停止生成" in compose.onNodeWithTag("main-submit-control").fetchSemanticsNode()
                    .config.getOrElse(SemanticsProperties.ContentDescription) { emptyList() }
                copyChecks += JSONObject().put("state", state).put("expected", "停止生成").put("exact_visible_match", accessible)
                Log.i("NativeAcceptance", "NATIVE_COPY|$state|${Base64.encodeToString("停止生成".toByteArray(Charsets.UTF_8), Base64.NO_WRAP)}|$accessible")
            }
            val stateMeasurements = JSONObject()
            var historyDiagnostics: JSONObject? = null
            val contentTag = if (state == "history") "history-safe-content" else "main-safe-content"
            val contentNodes = compose.onAllNodesWithTag(contentTag, useUnmergedTree = true).fetchSemanticsNodes()
            assertEquals("$state content container match count", 1, contentNodes.size)
            val contentBounds = contentNodes.single().boundsInRoot
            val contentRect = listOf(contentBounds.left - rootBounds.left, contentBounds.top - rootBounds.top,
                contentBounds.width, contentBounds.height)
            stateMeasurements.put(contentTag, JSONArray(contentRect))
            Log.i("NativeAcceptance", "NATIVE_RECT|$state|$contentTag|${contentRect[0]}|${contentRect[1]}|${contentRect[0] + contentRect[2]}|${contentRect[1] + contentRect[3]}")
            targetTags.forEach { tag ->
                var derivedUnionEvidence: JSONObject? = null
                val bounds = if (tag in listOf("result-actions-visible", "reading-followup-visual-union", "result-suggestions-visible-union")) {
                    val contentTags = if (tag == "result-actions-visible")
                        listOf("result-copy-icon", "result-copy-label", "result-regenerate-icon", "result-regenerate-label")
                    else listOf("result-followup-1-visual", "result-followup-2-visual")
                    var sourceEvidence: JSONArray? = null
                    val content = if (state == "reading_followup" && tag == "reading-followup-visual-union") {
                        val evidence = JSONArray()
                        val sourceBounds = contentTags.map { contentTag ->
                            val nodes = compose.onAllNodesWithTag(contentTag, useUnmergedTree = true).fetchSemanticsNodes()
                            assertEquals("derived union source must be unique: $contentTag", 1, nodes.size)
                            val node = nodes.single()
                            val visible = node.boundsInRoot
                            val unclipped = compose.onNodeWithTag(contentTag, useUnmergedTree = true).getUnclippedBoundsInRoot()
                            val visibleRect = listOf(visible.left-rootBounds.left, visible.top-rootBounds.top,
                                visible.right-rootBounds.left, visible.bottom-rootBounds.top)
                            val unclippedRect = listOf(unclipped.left.value-rootBounds.left, unclipped.top.value-rootBounds.top,
                                unclipped.right.value-rootBounds.left, unclipped.bottom.value-rootBounds.top)
                            assertTrue("derived union source is clipped: $contentTag; visible=$visibleRect unclipped=$unclippedRect",
                                visibleRect.zip(unclippedRect).all { (a, b) -> kotlin.math.abs(a-b) <= 0.01f })
                            val ancestors = JSONArray()
                            var ancestor = node.parent
                            while (ancestor != null) {
                                ancestor.config.getOrNull(SemanticsProperties.TestTag)?.let { ancestors.put(it) }
                                ancestor = ancestor.parent
                            }
                            evidence.put(JSONObject().put("source_tag", contentTag).put("node_id", node.id)
                                .put("match_count", nodes.size).put("visible_rect_px", JSONArray(visibleRect))
                                .put("unclipped_rect_px", JSONArray(unclippedRect))
                                .put("unclipped_unit", "local_dp_at_density_1").put("ancestor_tags", ancestors))
                            visible
                        }
                        sourceEvidence = evidence
                        sourceBounds
                    } else {
                        contentTags.mapNotNull { contentTag ->
                            compose.onAllNodesWithTag(contentTag, useUnmergedTree = true).fetchSemanticsNodes().singleOrNull()?.boundsInRoot
                        }
                    }
                    val union = if (content.size == contentTags.size) androidx.compose.ui.geometry.Rect(
                        content.minOf { it.left }, content.minOf { it.top }, content.maxOf { it.right }, content.maxOf { it.bottom },
                    ) else null
                    if (state == "reading_followup" && tag == "reading-followup-visual-union" && union != null) {
                        val rect = listOf(union.left-rootBounds.left, union.top-rootBounds.top,
                            union.right-rootBounds.left, union.bottom-rootBounds.top)
                        derivedUnionEvidence = JSONObject().put("evidence_kind", "derived_visible_bounds_union")
                            .put("semantic_tag_present", false)
                            .put("derived_bounds_rule", "union of unique source semantics visible_rect_px")
                            .put("source_tags", JSONArray(contentTags))
                            .put("source_semantic_nodes", sourceEvidence
                                ?: throw AssertionError("derived union source semantics were not recorded"))
                            .put("visible_rect_px", JSONArray(rect)).put("unclipped_rect_px", JSONArray(rect))
                            .put("unclipped_unit", "local_dp_at_density_1")
                    }
                    union
                } else {
                    compose.onAllNodesWithTag(tag, useUnmergedTree = true).fetchSemanticsNodes().singleOrNull()?.boundsInRoot
                }
                if (state == "reading_followup" && tag == "reading-followup-visual-union") {
                    assertNotNull("reading follow-up icon origin requires both measured visual capsules", bounds)
                }
                if (bounds == null || bounds.right <= bounds.left || bounds.bottom <= bounds.top) {
                    val matchCount = if (tag == "result-actions-visible") listOf("result-copy-icon", "result-copy-label", "result-regenerate-icon", "result-regenerate-label")
                        .sumOf { compose.onAllNodesWithTag(it, useUnmergedTree = true).fetchSemanticsNodes().size }
                    else compose.onAllNodesWithTag(tag, useUnmergedTree = true).fetchSemanticsNodes().size
                    Log.i("NativeAcceptance", "NATIVE_RECT_MISSING|$state|$tag|matches=$matchCount; reason=content_bounds_unavailable")
                    return@forEach
                }
                val rect = listOf(bounds.left - rootBounds.left, bounds.top - rootBounds.top, bounds.right - rootBounds.left, bounds.bottom - rootBounds.top)
                Log.i("NativeAcceptance", "NATIVE_RECT|$state|$tag|${rect[0]}|${rect[1]}|${rect[2]}|${rect[3]}")
                stateMeasurements.put(tag, JSONArray(rect))
                val textNode = compose.onAllNodesWithTag(tag, useUnmergedTree = true).fetchSemanticsNodes().singleOrNull()
                if (state == "reading_followup" && tag == "reading-followup-visual-union") {
                    // This capture-root rectangle is derived from two real semantics nodes;
                    // it does not claim that the union itself is a Compose semantics node.
                    val derived = derivedUnionEvidence
                        ?: throw AssertionError("derived reading follow-up union evidence was not produced")
                    Log.i("NativeAcceptance", "NATIVE_NODE|$state|$tag|${Base64.encodeToString(derived.toString().toByteArray(Charsets.UTF_8), Base64.NO_WRAP)}")
                }
                if (textNode != null) {
                    val unclipped = compose.onNodeWithTag(tag, useUnmergedTree = true).getUnclippedBoundsInRoot()
                    val ancestors = JSONArray()
                    var ancestor = textNode.parent
                    while (ancestor != null) {
                        ancestor.config.getOrNull(SemanticsProperties.TestTag)?.let { ancestors.put(it) }
                        ancestor = ancestor.parent
                    }
                    val evidence = JSONObject().put("node_id", textNode.id).put("match_count", 1)
                        .put("text", JSONArray(textNode.config.getOrNull(SemanticsProperties.Text)?.map { it.text } ?: emptyList<String>()))
                        .put("editable_text", textNode.config.getOrNull(SemanticsProperties.EditableText)?.text ?: JSONObject.NULL)
                        .put("content_description", JSONArray(textNode.config.getOrNull(SemanticsProperties.ContentDescription) ?: emptyList<String>()))
                        .put("text_children_count", textNode.children.count { it.config.contains(SemanticsProperties.Text) })
                        .put("ancestor_tags", ancestors)
                        .put("visible_rect_px", JSONArray(rect))
                        .put("unclipped_rect_px", JSONArray(listOf(unclipped.left.value-rootBounds.left,
                            unclipped.top.value-rootBounds.top, unclipped.right.value-rootBounds.left, unclipped.bottom.value-rootBounds.top)))
                        .put("unclipped_unit", "local_dp_at_density_1")
                    Log.i("NativeAcceptance", "NATIVE_NODE|$state|$tag|${Base64.encodeToString(evidence.toString().toByteArray(Charsets.UTF_8), Base64.NO_WRAP)}")
                }
                val textValues = textNode?.config?.getOrNull(SemanticsProperties.Text)
                if (textValues?.size == 1) {
                    val text = textValues.single().text
                    Log.i("NativeAcceptance", "NATIVE_NODE_TEXT|$state|$tag|${Base64.encodeToString(text.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)}")
                }
            }
            if (state == "history") {
                val scrollNode = compose.onNodeWithTag("history-scroll-container", useUnmergedTree = true).fetchSemanticsNode()
                val scrollRange = scrollNode.config.getOrNull(SemanticsProperties.VerticalScrollAxisRange)
                val viewport = scrollNode.boundsInRoot
                val panelNodes = compose.onAllNodesWithTag("history-actions-panel", useUnmergedTree = true).fetchSemanticsNodes()
                val cancelNodes = compose.onAllNodesWithTag("history-actions-cancel", useUnmergedTree = true).fetchSemanticsNodes()
                fun rect(bounds: androidx.compose.ui.geometry.Rect) = JSONArray(listOf(
                    bounds.left-rootBounds.left, bounds.top-rootBounds.top, bounds.right-rootBounds.left, bounds.bottom-rootBounds.top,
                ))
                fun intersects(a: androidx.compose.ui.geometry.Rect, b: androidx.compose.ui.geometry.Rect) =
                    a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top
                fun overlapArea(a: androidx.compose.ui.geometry.Rect, b: androidx.compose.ui.geometry.Rect): Float =
                    (minOf(a.right, b.right) - maxOf(a.left, b.left)).coerceAtLeast(0f) *
                        (minOf(a.bottom, b.bottom) - maxOf(a.top, b.top)).coerceAtLeast(0f)
                val rows = JSONArray()
                listOf("selected", "schedule", "tomorrow", "yesterday").forEach { id ->
                    val rowNodes = compose.onAllNodesWithTag("history-row-$id", useUnmergedTree = true).fetchSemanticsNodes()
                    val bounds = rowNodes.singleOrNull()?.boundsInRoot
                    val row = JSONObject().put("id", id)
                        .put("match_count", rowNodes.size).put("bounds_px", bounds?.let(::rect) ?: JSONObject.NULL)
                        .put("intersects_scroll_viewport", bounds?.let { intersects(it, viewport) } ?: false)
                        .put("overlaps_action_panel", bounds?.let { panelNodes.any { panel -> intersects(it, panel.boundsInRoot) } } ?: false)
                        .put("overlaps_cancel_action", bounds?.let { cancelNodes.any { cancel -> intersects(it, cancel.boundsInRoot) } } ?: false)
                    rows.put(row)
                }
                fun measured(tag: String) = compose.onAllNodesWithTag(tag, useUnmergedTree = true).fetchSemanticsNodes().singleOrNull()?.boundsInRoot
                val requiredFooterTags = listOf("history-settings-label", "history-help-label", "history-settings-icon", "history-help-icon", "history-footer-divider")
                val footerVisibility = JSONArray()
                requiredFooterTags.forEach { tag ->
                    val node = measured(tag) ?: throw AssertionError("history footer node missing: $tag")
                    val panelOverlap = panelNodes.maxOfOrNull { overlapArea(node, it.boundsInRoot) } ?: 0f
                    val cancelOverlap = cancelNodes.maxOfOrNull { overlapArea(node, it.boundsInRoot) } ?: 0f
                    val insideSafeContent = node.left >= contentBounds.left && node.top >= contentBounds.top &&
                        node.right <= contentBounds.right && node.bottom <= contentBounds.bottom
                    footerVisibility.put(JSONObject().put("selector", tag).put("bounds_px", rect(node))
                        .put("inside_safe_content", insideSafeContent).put("panel_overlap_area_px2", panelOverlap)
                        .put("cancel_overlap_area_px2", cancelOverlap))
                    assertTrue("history footer $tag is outside safe content: ${rect(node)}", insideSafeContent)
                    assertEquals("history footer $tag overlaps action panel", 0f, panelOverlap, 0.01f)
                    assertEquals("history footer $tag overlaps cancel action", 0f, cancelOverlap, 0.01f)
                }
                assertEquals("closed drawer capture must not include an action panel", 0, panelNodes.size)
                assertEquals("closed drawer capture must not include a cancel action", 0, cancelNodes.size)
                listOf("history-settings-hit-target", "history-help-hit-target").forEach { tag ->
                    val bounds = measured(tag) ?: throw AssertionError("history footer hit target missing: $tag")
                    assertTrue("history footer hit target is narrower than 44px: $tag $bounds", bounds.width >= 44f)
                    assertTrue("history footer hit target is shorter than 44px: $tag $bounds", bounds.height >= 44f)
                }
                val footerLabels = listOf("history-settings-label", "history-help-label").mapNotNull(::measured)
                val footerContentUnion = if (footerLabels.size == 2) androidx.compose.ui.geometry.Rect(
                    footerLabels.minOf { it.left }, footerLabels.minOf { it.top },
                    footerLabels.maxOf { it.right }, footerLabels.maxOf { it.bottom },
                ) else null
                historyDiagnostics = JSONObject().put("menu_state", "closed").put("scroll_kind", "continuous_vertical_scroll")
                    .put("scroll_offset_px", scrollRange?.value?.invoke()?.roundToInt())
                    .put("scroll_max_px", scrollRange?.maxValue?.invoke()?.roundToInt())
                    .put("viewport_bounds_px", rect(viewport)).put("safe_content_bounds_px", rect(contentBounds))
                    .put("session_rows", rows).put("footer_visibility_and_occlusion", footerVisibility)
                    .put("footer_layout_container_bounds_px", measured("history-fixed-bottom-links")?.let(::rect) ?: JSONObject.NULL)
                    .put("footer_visible_label_union_bounds_px", footerContentUnion?.let(::rect) ?: JSONObject.NULL)
                    .put("footer_visible_labels", JSONArray(listOf("history-settings-label", "history-help-label").map { tag ->
                        JSONObject().put("selector", tag).put("bounds_px", measured(tag)?.let(::rect) ?: JSONObject.NULL)
                    }))
                    .put("footer_visible_icons", JSONArray(listOf("history-settings-icon", "history-help-icon").map { tag ->
                        JSONObject().put("selector", tag).put("bounds_px", measured(tag)?.let(::rect) ?: JSONObject.NULL)
                    }))
                    .put("footer_divider_bounds_px", measured("history-footer-divider")?.let(::rect) ?: JSONObject.NULL)
                    .put("footer_hit_targets", JSONArray(listOf("history-settings-hit-target", "history-help-hit-target").map { tag ->
                        JSONObject().put("selector", tag).put("bounds_px", measured(tag)?.let(::rect) ?: JSONObject.NULL)
                    }))
                Log.i("NativeAcceptance", "NATIVE_HISTORY_GEOMETRY|${Base64.encodeToString(historyDiagnostics.toString().toByteArray(Charsets.UTF_8), Base64.NO_WRAP)}")
            }
            val metrics = InstrumentationRegistry.getInstrumentation().targetContext.resources.displayMetrics
            val scroll = scrollRecord ?: throw AssertionError("$state scroll basis was not observed")
            if (state == "train_schedule") {
                assertEquals("formal schedule capture must preserve its first item", 0, scroll.getInt("first_visible_item_index"))
                assertEquals("formal schedule capture must preserve its initial offset", 0, scroll.getInt("first_visible_item_offset_px"))
            }
            val basis = JSONObject().put("schema_version", 1).put("coordinate_origin", "capture_root")
                .put("phase", "before_interaction").put("root_rect_px", JSONArray(listOf(0, 0, 390, 844)))
                .put("content_tag", contentTag).put("content_rect_px", JSONArray(contentRect))
                .put("system_insets_platform_px", JSONArray(insetRecord.second))
                .put("system_insets_applied_px", JSONArray(insetRecord.first))
                .put("platform_density", metrics.density).put("local_density", localDensityRecord)
                .put("font_scale", fontScaleRecord)
                .put("scroll_mode", if (state == "history") "continuous" else "list").put("scroll", scroll)
            val encodedBasis = Base64.encodeToString(basis.toString().toByteArray(Charsets.UTF_8), Base64.NO_WRAP)
            Log.i("NativeAcceptance", "NATIVE_BASIS|$state|$encodedBasis")
            val bitmap = root.captureToImage().asAndroidBitmap()
            assertEquals("$state width", 390, bitmap.width)
            assertEquals("$state height", 844, bitmap.height)
            val png = ByteArrayOutputStream().also { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }.toByteArray()
            FileOutputStream(File(output, "$state.png")).use { it.write(png) }
            val encoded = Base64.encodeToString(png, Base64.NO_WRAP)
            val chunks = encoded.chunked(2000)
            chunks.forEachIndexed { index, chunk -> Log.i("NativeAcceptance", "NATIVE_CAPTURE|$state|$index|${chunks.size}|$chunk") }
            Log.i("NativeAcceptance", "NATIVE_CAPTURE_META|$state|${bitmap.width}|${bitmap.height}|1.0|1.0|390|844")
            val stateMeasurement = JSONObject().put("state", state).put("measurements", stateMeasurements)
            if (state != "history") stateMeasurement.put("layout_constraints_px", JSONObject(layoutRecord.mapValues { it.value }))
            historyDiagnostics?.let { stateMeasurement.put("history_diagnostics", it) }
            measurements += stateMeasurement
            when (state) {
                "query_loading" -> {
                    compose.onNodeWithTag("main-submit-control").performClick()
                    assertTrue(interactionEvents.contains("query:stop"))
                    compose.onNodeWithTag("main-submit-control").assertExists()
                }
                "batch_partial" -> {
                    compose.onNodeWithTag("batch-retry-failed").performClick()
                    assertTrue("unexpected batch retry action: $interactionEvents", interactionEvents.any { it.startsWith("action:train_schedule_batch:[\"G8928\"]:true") })
                    compose.onNodeWithText("C2203").assertExists()
                    compose.onNodeWithText("G8927").assertExists()
                }
                "train_schedule" -> {
                    compose.onNodeWithTag("result-copy-action", useUnmergedTree = true).performScrollTo()
                    compose.waitForIdle()
                    val listBounds = compose.onNodeWithTag("main-message-list", useUnmergedTree = true).fetchSemanticsNode().boundsInRoot
                    val actionsBounds = compose.onNodeWithTag("result-actions", useUnmergedTree = true).fetchSemanticsNode().boundsInRoot
                    val actionHeight = compose.onNodeWithTag("result-copy-action", useUnmergedTree = true).fetchSemanticsNode().boundsInRoot.height
                    assertTrue("copy interaction is clipped by the list viewport: actions=$actionsBounds viewport=$listBounds", actionsBounds.top >= listBounds.top && actionsBounds.bottom <= listBounds.bottom)
                    assertEquals("copy target retains its minimum touch height", 44f, actionHeight, 0.01f)
                    compose.onNodeWithTag("result-copy-action", useUnmergedTree = true).performTouchInput { click() }
                    compose.waitForIdle()
                    assertTrue("copy callback mismatch: $interactionEvents", interactionEvents.contains("copy:G8932"))
                    compose.onNodeWithTag("result-regenerate-action").performClick()
                    compose.waitForIdle()
                    assertTrue("regenerate callback mismatch: $interactionEvents", interactionEvents.contains("regenerate:查一下 G8932 今天的时刻表"))
                }
                "emu_routing" -> {
                    compose.onNodeWithTag("routing-batch").performClick()
                    assertTrue(interactionEvents.any { it.startsWith("action:train_schedule_batch:[\"C2203\",\"G8927\",\"G8928\"]:false") })
                }
                "routing_empty" -> {
                    compose.onNodeWithTag("empty-change-date").performClick()
                    assertTrue("date picker dependency was not invoked", interactionEvents.contains("date-picker-opened:2026-09-25"))
                    val datePayload = interactionEvents.firstOrNull { it.startsWith("payload:{\"kind\":\"emu_routing\"") }
                    assertTrue("date change action was not emitted: $interactionEvents", datePayload?.contains("\"date\":\"2026-09-26\"") == true)
                    compose.onNodeWithTag("empty-recent").performClick()
                    assertTrue(interactionEvents.any { it.contains("emu_routing") && it.contains("查看最近交路记录") })
                }
                "connection_error" -> {
                    compose.onNodeWithTag("error-details-toggle").performClick()
                    compose.onNodeWithText("LLM 鉴权失败(HTTP 401)：API Key 无效或被拒绝").assertExists()
                    compose.onNodeWithTag("connection-retry").performClick()
                    compose.onNodeWithTag("connection-settings").performClick()
                    assertTrue("unexpected error actions: $interactionEvents", interactionEvents.contains("query:retry") && interactionEvents.contains("settings:auth"))
                }
                "reading_followup" -> {
                    // Return-to-bottom is verified in the independently reset scene above.
                    // Keep this scene in reading mode so its date action remains available.
                    val action = compose.onNodeWithContentDescription("换个日期", useUnmergedTree = true)
                    action.assertIsDisplayed()
                    val actionBounds = action.fetchSemanticsNode().boundsInRoot
                    val inputBounds = compose.onNodeWithTag("main-input-field", useUnmergedTree = true).fetchSemanticsNode().boundsInRoot
                    val rootBounds = compose.onNodeWithTag("native-capture-root").fetchSemanticsNode().boundsInRoot
                    assertTrue("change-date action is outside the viewport: $actionBounds / $rootBounds",
                        actionBounds.left >= rootBounds.left && actionBounds.right <= rootBounds.right && actionBounds.top >= rootBounds.top && actionBounds.bottom <= rootBounds.bottom)
                    val submitCountBefore = interactionEvents.count { it.startsWith("query:submit") }
                    action.performTouchInput { click() }
                    compose.waitForIdle()
                    val expectedDraft = "查一下 G8932 其他日期的时刻表"
                    val actualDraft = compose.onNodeWithTag("main-input-field", useUnmergedTree = true).fetchSemanticsNode()
                        .config.getOrNull(SemanticsProperties.EditableText)?.text
                    assertTrue("change-date action did not emit its draft: $interactionEvents", interactionEvents.contains("followup:draft:$expectedDraft"))
                    assertEquals("input field draft", expectedDraft, actualDraft)
                    assertEquals("change-date action must not submit", submitCountBefore, interactionEvents.count { it.startsWith("query:submit") })
                    assertTrue("change-date tap overlapped the input control: $actionBounds / $inputBounds",
                        actionBounds.bottom <= inputBounds.top || actionBounds.top >= inputBounds.bottom)
                    interactionEvents += "followup:change-date-touch:bounds=$actionBounds:input=$actualDraft:submit_delta=${interactionEvents.count { it.startsWith("query:submit") } - submitCountBefore}"
                }
                "history" -> {
                    compose.onNodeWithTag("history-group-今天").assertExists()
                    compose.onNodeWithTag("history-group-昨天").assertExists()
                    compose.onNodeWithTag("history-row-yesterday").assertExists()
                    compose.onNodeWithTag("history-row-selected").assertExists()
                    compose.onNodeWithTag("native-capture-root").logUsabilityCapture("history-drawer-closed")
                    compose.onNodeWithTag("history-more-selected", useUnmergedTree = true).performClick()
                    interactionEvents += "history:actions-open"
                    compose.onNodeWithTag("native-capture-root").logUsabilityCapture("history-actions-open")
                    compose.onNodeWithTag("history-actions-dismiss").performTouchInput { click(androidx.compose.ui.geometry.Offset(4f, 4f)) }
                    compose.onNodeWithTag("history-create").performClick()
                    compose.onNodeWithText("北京南到上海虹桥票价").performClick()
                    compose.onNodeWithTag("history-more-selected", useUnmergedTree = true).performClick()
                    compose.onNodeWithTag("history-actions-panel").assertExists()
                    compose.onNodeWithTag("history-rename-menu").assertExists()
                    compose.onNodeWithTag("history-rename-menu").performClick()
                    compose.onNodeWithTag("history-rename").performTextClearance()
                    compose.onNodeWithTag("history-rename").performTextInput("票价查询")
                    compose.onNodeWithText("保存").performClick()
                    assertEquals("票价查询", compose.runOnIdle { fixtureStore?.search("")?.firstOrNull { it.id == "selected" }?.title })
                    interactionEvents += "history:rename-saved"
                    compose.onNodeWithTag("history-more-selected", useUnmergedTree = true).performClick()
                    compose.onNodeWithTag("history-delete-menu").performClick()
                    compose.onNodeWithTag("history-delete-confirm").assertExists()
                    compose.onAllNodesWithText("取消").get(0).performClick()
                    assertTrue(compose.runOnIdle { fixtureStore?.search("")?.any { it.id == "selected" } == true })
                    interactionEvents += "history:delete-cancelled"
                }
            }
        }
        assertTrue("history action panel callback was exercised", interactionEvents.contains("history:actions-open"))
        interactionEvents.forEach { Log.i("NativeAcceptance", "NATIVE_INTERACTION|$it") }
        File(output, "verification.json").writeText(JSONObject().put("platform", "Android Compose")
            .put("device", "railfan-arm64 API 35").put("interactions", JSONArray(interactionEvents))
            .put("copy_checks", JSONArray(copyChecks))
            .put("results", JSONArray(measurements)).toString(2))
    }

    @androidx.compose.runtime.Composable
    private fun MainFixture(
        state: String,
        events: MutableList<String>,
        insetObserver: ((IntArray, IntArray, Float, Float) -> Unit)? = null,
        scrollObserver: ((Int, Int, Int, Int, List<Int>) -> Unit)? = null,
        layoutObserver: ((String, IntArray, IntArray) -> Unit)? = null,
        systemInsetsOverride: WindowInsets? = null,
    ) {
        val localDensity = LocalDensity.current
        val safeInsets = WindowInsets.safeDrawing
        val metrics = InstrumentationRegistry.getInstrumentation().targetContext.resources.displayMetrics
        val safeTopPx = safeInsets.getTop(localDensity)
        val safeBottomPx = safeInsets.getBottom(localDensity)
        val safeLeftPx = safeInsets.getLeft(localDensity, LayoutDirection.Ltr)
        val safeRightPx = safeInsets.getRight(localDensity, LayoutDirection.Ltr)
        // The fixture is a 390x844 logical-pixel viewport at density 1. Convert real Android
        // window inset pixels back through device density before applying them to that viewport.
        val fixtureInsets = systemInsetsOverride ?: WindowInsets(
            (safeLeftPx / metrics.density).toInt(), (safeTopPx / metrics.density).toInt(),
            (safeRightPx / metrics.density).toInt(), (safeBottomPx / metrics.density).toInt(),
        )
        SideEffect {
            insetObserver?.invoke(
                intArrayOf(fixtureInsets.getLeft(localDensity, LayoutDirection.Ltr), fixtureInsets.getTop(localDensity),
                    fixtureInsets.getRight(localDensity, LayoutDirection.Ltr), fixtureInsets.getBottom(localDensity)),
                intArrayOf(safeLeftPx, safeTopPx, safeRightPx, safeBottomPx), localDensity.density, localDensity.fontScale,
            )
        }
        val messages = when (state) {
            "train_schedule", "emu_routing", "batch_partial", "routing_empty", "connection_error", "reading_followup", "query_details" -> listOf(
                StoredMessage("user", prompt(state), null),
                StoredMessage("assistant", if (state == "reading_followup") "以下是 G8932 次列车的时刻信息：" else "", metadata(state)),
            )
            "query_loading" -> listOf(StoredMessage("user", prompt(state), null))
            else -> emptyList()
        }
        val busy = state == "query_loading"
        var input by remember(state) { mutableStateOf(if (busy) "再看看明天的" else "") }
        var chatState by remember(state) { mutableStateOf(
            ChatUiState(
                query = prompt(state),
                phase = if (busy) ChatPhase.CONNECTING else ChatPhase.COMPLETED,
                recognized = if (busy) "G8932" else "",
                stage = if (busy) "正在连接 12306" else "",
                reading = state == "reading_followup",
            ),
        ) }
        Box(Modifier.size(390.dp, 844.dp).testTag("native-capture-root")) {
            MainChatScreen(
                status = "后端已连接", connected = true, chatState = chatState, messages = messages, input = input,
                onInputChange = { input = it }, onOpenHistory = { events += "history:open" }, onNewConversation = { events += "conversation:new" },
                onSubmit = { events += "query:submit:$input" }, onStop = { events += "query:stop"; chatState = chatState.copy(phase = ChatPhase.STOPPED) },
                onReadingChange = { reading ->
                    if (state == "reading_followup") chatState = chatState.copy(reading = reading)
                    events += "reading:$reading"
                },
                onAction = { action, text, preserve ->
                    events += "action:${action.optString("kind")}:${action.optJSONArray("trains")?.toString()}:$preserve:$text"
                    events += "payload:${action.toString()}"
                },
                onRetry = { events += "query:retry" }, onSettings = { events += "settings:${it.orEmpty()}" },
                onFollowup = { input = it; events += "followup:draft:$it" },
                datePicker = if (state == "routing_empty") { initial, select ->
                    events += "date-picker-opened:$initial"
                    select(initial.plusDays(1))
                } else null,
                onCopy = { Log.i("NativeAcceptance", "COPY_CALLBACK|$it"); events += "copy:${it.substringBefore(' ')}" },
                onRegenerate = { Log.i("NativeAcceptance", "REGENERATE_CALLBACK|$it"); events += "regenerate:$it" },
                modifier = Modifier.fillMaxSize(),
                applySystemInsets = true,
                systemInsets = fixtureInsets,
                onScrollDiagnostics = scrollObserver,
                onLayoutDiagnostics = layoutObserver,
                autoScrollToLatest = state != "train_schedule",
            )
        }
    }

    @androidx.compose.runtime.Composable
    private fun HistoryFixture(
        events: MutableList<String>,
        insetObserver: ((IntArray, IntArray, Float, Float) -> Unit)? = null,
    ) {
        val localDensity = LocalDensity.current
        val metrics = InstrumentationRegistry.getInstrumentation().targetContext.resources.displayMetrics
        val safeInsets = WindowInsets.safeDrawing
        val safeInsetsPlatform = intArrayOf(
            safeInsets.getLeft(localDensity, LayoutDirection.Ltr), safeInsets.getTop(localDensity),
            safeInsets.getRight(localDensity, LayoutDirection.Ltr), safeInsets.getBottom(localDensity),
        )
        val fixtureInsets = WindowInsets(
            (safeInsetsPlatform[0] / metrics.density).toInt(), (safeInsetsPlatform[1] / metrics.density).toInt(),
            (safeInsetsPlatform[2] / metrics.density).toInt(), (safeInsetsPlatform[3] / metrics.density).toInt(),
        )
        SideEffect {
            insetObserver?.invoke(
                intArrayOf(fixtureInsets.getLeft(localDensity, LayoutDirection.Ltr), fixtureInsets.getTop(localDensity),
                    fixtureInsets.getRight(localDensity, LayoutDirection.Ltr), fixtureInsets.getBottom(localDensity)),
                safeInsetsPlatform, localDensity.density, localDensity.fontScale,
            )
        }
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = androidx.compose.runtime.remember {
            val directory = File(context.cacheDir, "acceptance-history-fixture").apply { deleteRecursively(); mkdirs() }
            val today = LocalDate.now().atStartOfDay(java.time.ZoneId.systemDefault()).toInstant().toEpochMilli()
            val yesterday = today - 86_400_000L
            val sessions = JSONArray()
            sessions.put(historyConversation("selected", "北京南到上海虹桥票价", "G1 · 明天出发", today + 2_000))
            sessions.put(historyConversation("schedule", "G8932 列车时刻", "秦皇岛 → 北京南", today))
            sessions.put(historyConversation("tomorrow", "CR400BF-5033 今日交路", "3 条交路记录", today + 1_000))
            sessions.put(historyConversation("yesterday", "京沪线沿线车站", "", yesterday))
            File(directory, "state.json").writeText(JSONObject().put("railfan_conversations_v1", sessions.toString())
                .put("railfan_current_conv_v1", "selected").toString())
            ConversationStore(directory, isolated = true)
        }
        fixtureStore = store
        androidx.compose.runtime.key(store.currentId()) {
            Box(Modifier.size(390.dp, 844.dp).testTag("native-capture-root")) {
                HistoryScreen(
                    store = store, currentId = "selected", revision = 1,
                    onBack = { events += "history:back" }, onCreate = { events += "history:new" },
                    onSelect = { events += "history:select:$it" }, onRename = { id, title -> store.rename(id, title) },
                    onDelete = { store.delete(it) }, onSettings = { events += "history:settings" }, onHelp = { events += "history:help" },
                    modifier = Modifier.fillMaxSize(),
                    applySystemInsets = true,
                    systemInsets = fixtureInsets,
                )
            }
        }
    }

    private fun historyConversation(id: String, title: String, subtitle: String, updatedAt: Long) = JSONObject()
        .put("id", id).put("title", title).put("titleAuto", false).put("createdAt", updatedAt - 1000).put("updatedAt", updatedAt)
        .put("messages", JSONArray().put(JSONObject().put("role", "user").put("content", subtitle)))

    private fun targets(state: String): List<String> = when (state) {
        "train_schedule" -> listOf("main-topbar", "main-user-prompt-frame", "schedule-G8932-success", "schedule-title-row", "schedule-route-summary", "schedule-meta", "schedule-stations", "schedule-stop-1", "schedule-timeline-1", "schedule-date-icon", "schedule-source-note", "query-details-host", "result-actions-visible", "result-suggestions", "result-suggestions-visible-union", "result-followup-1-visual", "result-followup-2-visual", "result-copy-action", "result-regenerate-action", "result-copy-icon", "result-copy-label", "result-regenerate-icon", "result-regenerate-label", "main-history-icon", "main-new-icon", "main-brand-icon", "main-user-avatar", "main-user-avatar-icon", "main-assistant-avatar", "main-assistant-avatar-icon", "main-fixed-input") + (1..4).flatMap { listOf("schedule-arrival-$it", "schedule-departure-$it", "schedule-stop-$it", "schedule-timeline-$it") }
        "reading_followup" -> listOf("main-topbar", "main-user-prompt", "schedule-G8932-success", "schedule-title", "schedule-stations", "return-to-bottom", "return-to-bottom-visual", "reading-followup-visual-union", "result-followup-1", "result-followup-2", "result-followup-1-visual", "result-followup-2-visual", "main-fixed-input", "main-submit-control")
        "query_details" -> listOf("query-details", "details-heading-and-source", "details-source", "details-sample-badge", "details-date", "details-time-basis", "details-latency", "details-usage", "technical-log-toggle", "technical-log-visual")
        "emu_routing" -> listOf("main-topbar", "main-user-prompt-frame", "routing-result", "routing-title-row", "routing-date-record", "routing-time-semantics", "routing-records", "routing-record-C2203", "routing-record-link-C2203", "query-details-host", "routing-batch", "routing-integrity-note", "main-fixed-input", "routing-batch-label") + listOf("C2203", "G8927", "G8928").flatMap { listOf("routing-train-$it", "routing-time-$it", "routing-record-link-$it", "routing-record-chevron-$it") }
        "query_loading" -> listOf("query-loading", "query-loading-spinner", "query-loading-title-and-stage", "query-loading-skeletons", "main-fixed-input", "main-submit-control", "query-preserve-note")
        "batch_partial" -> listOf("batch-result", "batch-card", "batch-heading", "batch-count", "batch-row-C2203", "batch-row-G8927", "batch-row-G8928", "batch-status-icon-C2203", "batch-status-icon-G8928", "batch-retry-failed", "batch-retry-failed-visual", "batch-retain-success", "batch-retain-success-text")
        "routing_empty" -> listOf("routing-empty", "empty-icon-group", "empty-title", "empty-message", "empty-date-pill", "empty-date-icon", "empty-change-date", "empty-change-date-visual", "empty-recent", "empty-recent-visual", "empty-history-note")
        "connection_error" -> listOf("connection-error", "error-icon", "error-title", "error-explanation", "connection-retry", "connection-retry-visual", "connection-settings", "connection-settings-visual", "error-details-toggle", "error-details-toggle-visual", "api-key-label", "api-key-field", "api-key-field-visual", "api-key-error")
        "history" -> listOf("history-safe-content","history-header", "history-scroll-container", "history-search-box", "history-create", "history-today-group", "history-group-今天",
            "history-row-selected", "history-row-schedule", "history-row-tomorrow", "history-yesterday-group", "history-group-昨天",
            "history-row-yesterday", "history-select-selected", "history-select-schedule", "history-select-tomorrow", "history-select-yesterday",
            "history-more-selected", "history-more-schedule", "history-more-tomorrow", "history-more-yesterday",
            "history-actions-panel", "history-actions-cancel", "history-fixed-bottom-links", "history-settings-label", "history-help-label",
            "history-settings-hit-target", "history-help-hit-target")
        else -> emptyList()
    }

    private fun copyExpectations(state: String): List<String> = when (state) {
        "train_schedule" -> listOf("查一下 G8932 今天的时刻表", "G8932", "图定时刻 · 示例数据", "秦皇岛 → 北京南", "09月25日", "1小时52分", "车站", "到达", "出发", "图定时刻不代表实际正晚点。", "12306 · 列车时刻", "查询详情", "复制", "重新生成", "查票价", "查担当车组", "继续追问…")
        "emu_routing" -> listOf("CR400BF-5033 今天的交路", "CR400BF-5033", "09月25日 · 交路记录", "以下为记录时间，不是列车到发时间。", "C2203", "07:42", "G8927", "11:06", "G8928", "13:26", "查看该车次时刻", "rail.re · 交路记录", "查询这三趟车的时刻表", "记录可能不完整，以实际运行情况为准。")
        "query_loading" -> listOf("正在检索相关资料…", "已识别 G8932 · 正在连接 12306", "再看看明天的", "可停止生成，已返回内容会保留")
        "batch_partial" -> listOf("时刻查询", "2 / 3 已返回", "C2203", "G8927", "G8928", "已查到", "暂未返回", "仅重试 G8928", "保留已查到的结果")
        "routing_empty" -> listOf("未找到当天交路记录", "暂无记录，不能据此判断停运。", "09月25日", "更换日期", "查看最近记录", "历史记录将单独标注日期")
        "connection_error" -> listOf("暂时无法连接模型服务", "你的提问已保留，可稍后重试。", "重试", "检查模型设置", "错误详情", "API Key", "密钥无效，请检查后重试")
        "reading_followup" -> listOf("以下是 G8932 次列车的时刻信息：", "回到底部", "查余票", "换个日期", "继续追问…")
        "query_details" -> listOf("查询详情", "12306 · 列车时刻", "示例数据", "数据日期", "09月25日", "时间口径", "图定时刻", "查询耗时", "5.8 秒", "用量", "1,555 Token", "查看技术日志")
        "history" -> listOf("对话历史", "搜索对话…", "新建对话", "今天", "昨天", "北京南到上海虹桥票价", "CR400BF-5033 今日交路", "G8932 列车时刻", "京沪线沿线车站", "模型与设置", "帮助")
        else -> emptyList()
    }

    private fun prompt(state: String) = when (state) {
        "emu_routing" -> "CR400BF-5033 今天的交路"
        "query_loading" -> "查一下 G8932 今天的时刻表"
        "routing_empty" -> "CR400BF-5033 今天的交路"
        else -> "查一下 G8932 今天的时刻表"
    }

    private fun metadata(state: String): JSONObject {
        val results = when (state) {
            "emu_routing" -> JSONArray().put(JSONObject().put("kind", "emu_routing").put("status", "success")
                .put("query", "CR400BF-5033").put("focus_date", "2026-09-25").put("time_semantics", "以下为记录时间，不是列车到发时间。")
                .put("records", JSONArray().put(record("C2203", "07:42")).put(record("G8927", "11:06")).put(record("G8928", "13:26")))
                .put("sources", JSONArray().put("https://rail.re")).put("sample_data", true))
            "batch_partial" -> JSONArray().put(JSONObject().put("kind", "train_schedule_batch").put("status", "partial")
                .put("items", JSONArray().put(scheduleJson("C2203", "success")).put(scheduleJson("G8927", "success")).put(scheduleJson("G8928", "failed"))))
            "routing_empty" -> JSONArray().put(JSONObject().put("kind", "empty").put("status", "empty").put("date", "2026-09-25")
                .put("query", "CR400BF-5033").put("sources", JSONArray().put("https://rail.re")))
            "connection_error" -> JSONArray().put(JSONObject().put("kind", "error").put("status", "failed")
                .put("message", "LLM 鉴权失败(HTTP 401)：API Key 无效或被拒绝").put("category", "auth").put("tool", "model"))
            else -> JSONArray().put(scheduleJson("G8932", "success"))
        }
        return JSONObject().put("query", prompt(state)).put("sources", JSONArray().put("https://12306.cn"))
            .put("displayResults", results).put("usage", JSONObject().put("total_tokens", 1555)).put("latencyMs", 5800)
            .put("processLogs", JSONArray().put("[数据检索] train.schedule: ok"))
    }

    private fun scheduleJson(code: String, status: String) = JSONObject().put("kind", "train_schedule").put("status", status)
        .put("train_code", code).put("date", "2026-09-25").put("from_station", "秦皇岛").put("to_station", "北京南")
        .put("start_time", "20:42").put("arrive_time", "22:34").put("duration", "1小时52分")
        .put("schedule_type", "图定时刻").put("time_basis", "reference").put("today_times_available", true).put("sample_data", true)
        .put("stops", JSONArray().put(stop("1", "秦皇岛", null, "20:42", null)).put(stop("2", "唐山", "21:10", "21:22", "停12分钟"))
            .put(stop("3", "天津", "21:55", "21:58", "停3分钟")).put(stop("4", "北京南", "22:34", null, null)))
        .put("sources", JSONArray().put("https://12306.cn"))
        .also { if (status == "failed") it.put("error", "该车次暂未返回") }

    private fun stop(number: String, station: String, arrive: String?, start: String?, stopover: String?) = JSONObject()
        .put("station_no", number).put("station", station).put("arrive_time", arrive ?: JSONObject.NULL)
        .put("start_time", start ?: JSONObject.NULL).put("stopover_time", stopover ?: JSONObject.NULL)

    private fun record(code: String, time: String) = JSONObject().put("train_code", code).put("time", time)
}
