from mtm_core.runtime.trace import parse_codex_trace, parse_agy_trace


def test_parse_codex_trace():
    trace_sample = """{"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 200, "output_tokens": 150, "reasoning_output_tokens": 50}}
{"type": "item.completed", "item": {"type": "command_execution", "command": "pytest", "exit_code": 0}}
{"type": "item.completed", "item": {"type": "file_change"}}
{"type": "item.completed", "item": {"type": "message", "role": "assistant", "text": "Fixed the discount bug."}}
"""
    trace = parse_codex_trace(trace_sample)
    assert trace.turns == 1
    assert trace.input_tokens == 1000
    assert trace.cached_input_tokens == 200
    assert trace.output_tokens == 150
    assert trace.total_tokens == 1150
    assert trace.billable_tokens == (1000 - 200) + 150
    assert len(trace.commands) == 1
    assert trace.commands[0]["command"] == "pytest"
    assert trace.file_changes == 1
    assert trace.last_message == "Fixed the discount bug."


def test_parse_agy_trace():
    agy_sample = """{"conversation_id": "test-123", "status": "SUCCESS", "response": "Tests passed.", "usage": {"input_tokens": 5000, "cache_read_tokens": 1000, "output_tokens": 200, "thinking_tokens": 100}, "num_turns": 2}
"""
    trace = parse_agy_trace(agy_sample)
    assert trace.turns == 2
    assert trace.input_tokens == 5000
    assert trace.cached_input_tokens == 1000
    assert trace.output_tokens == 200
    assert trace.reasoning_output_tokens == 100
    assert trace.billable_tokens == 4200
    assert trace.last_message == "Tests passed."
