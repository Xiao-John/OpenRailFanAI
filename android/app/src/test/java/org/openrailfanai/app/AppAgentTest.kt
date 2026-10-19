package org.openrailfanai.app

import org.junit.Assert.*
import org.junit.Test

class AppAgentTest {
    @Test fun directCommandsAndQuestionsAreDistinct() {
        assertEquals("dark", AppAgent.themeCommand("切换到深色模式"))
        assertEquals("light", AppAgent.themeCommand("请把主题改成浅色"))
        assertEquals("auto", AppAgent.themeCommand("主题跟随系统"))
        assertEquals("undo", AppAgent.themeCommand("撤销刚才的主题切换"))
        for (text in listOf("不要切换深色模式", "如何开启深色模式？", "“开启深色模式”是什么意思", "G1明天时刻表")) {
            assertNull(AppAgent.themeCommand(text))
        }
    }

    @Test fun fileOperationsDoNotInterceptRailwayQueries() {
        assertTrue(AppAgent.importCommand("导入文件"))
        assertTrue(AppAgent.fileCommand("把这段行程生成日历"))
        assertTrue(AppAgent.fileCommand("请将已导入文件整理成 CSV 表格"))
        assertFalse(AppAgent.fileCommand("查询G1和G3明天的票价"))
        assertFalse(AppAgent.importCommand("怎么导入文件？"))
    }
    @Test fun supportedInputKindsAndImagePicker() {
        assertTrue(AppAgent.importCommand("导入图片"))
        assertTrue(AppAgent.importCommand("请选择图片"))
        assertFalse(AppAgent.importCommand("如何导入图片？"))
        assertTrue(AppAgent.documentExtensions.containsAll(listOf("tex", "doc", "docx", "rtf")))
        assertTrue(AppAgent.isImage("照片.JPG"))
        assertTrue(AppAgent.isImage("opaque", "image/png"))
        assertFalse(AppAgent.isImage("notes.tex", "text/plain"))
        assertEquals(8 * 1024 * 1024, AppAgent.MAX_INPUT_BYTES)
        assertEquals(setOf("text"), ProviderConfig("legacy", "Legacy", "", "").inputTypes)
    }
    @Test fun changingModelOrEndpointRequiresNewImageDeclaration() {
        val vision = ProviderConfig("v", "Vision", "https://example.com/v1", "vision-1", inputTypes = setOf("text", "image"))
        assertEquals(setOf("text"), vision.withModel("text-2").inputTypes)
        assertEquals(setOf("text"), vision.withBaseUrl("https://another.example/v1").inputTypes)
        assertEquals(setOf("text", "image"), vision.withModel("vision-1").inputTypes)
        assertEquals(setOf("text", "image"), vision.withBaseUrl(" https://example.com/v1 ").inputTypes)
        assertEquals(setOf("text", "image"), vision.copy(key = "another-key").inputTypes)
    }
}
