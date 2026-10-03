package org.openrailfanai.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatStateMachineTest {
    @Test fun stopPreservesPartialAnswerAndIgnoresLateEvents() {
        val machine = ChatStateMachine()
        val id = machine.begin("G8932", "request-1")
        machine.onEvent(id, "answer", delta = "已返回内容")
        assertTrue(machine.stop(id))
        assertEquals(ChatPhase.STOPPED, machine.state.phase)
        assertEquals("已返回内容", machine.state.answer)
        assertFalse(machine.onEvent(id, "answer", delta = "迟到内容"))
    }

    @Test fun oldRequestCannotOverwriteNewRequest() {
        val machine = ChatStateMachine()
        val first = machine.begin("first", "one")
        machine.onEvent(first, "answer", delta = "keep")
        machine.stop(first)
        val second = machine.begin("second", "two")
        assertFalse(machine.fail(first, "late error"))
        assertEquals(second, machine.state.requestId)
        assertEquals(ChatPhase.CONNECTING, machine.state.phase)
    }

    @Test fun readingModeChangesOnlyWhenExplicitlyRequested() {
        val machine = ChatStateMachine()
        machine.setReading(true)
        assertTrue(machine.state.reading)
        machine.setReading(false)
        assertFalse(machine.state.reading)
    }

    @Test fun streamErrorWaitsForDoneAndStillAllowsCancellation() {
        val machine = ChatStateMachine()
        val id = machine.begin("G8932", "error-then-done")
        assertTrue(machine.onEvent(id, "error", message = "模型不可用"))
        assertTrue(machine.state.busy)
        assertEquals("模型不可用", machine.state.error)
        assertTrue(machine.onEvent(id, "done"))
        assertTrue(machine.stop(id))
        assertFalse(machine.onEvent(id, "done"))
    }
}
