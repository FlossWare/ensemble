import json

import pytest

from ensemble.transcripts import parse_jsonl


def _line(record):
    return json.dumps(record, ensure_ascii=False) + "\n"


def test_jsonl_parser_consumes_only_complete_lines_and_uses_utf8_byte_offsets():
    complete = _line({
        "sessionId": "session-utf8",
        "timestamp": "2026-11-02T10:00:00Z",
        "uuid": "message-utf8",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "Please preserve this résumé and its Unicode bytes."}],
        },
    })
    partial = '{"uuid":"unfinished","message":'
    parsed = parse_jsonl(complete + partial, session_id="fallback", offset=128)
    expected_bytes = len(complete.encode("utf-8"))
    assert parsed["session_id"] == "session-utf8"
    assert parsed["bytes_consumed"] == expected_bytes
    assert parsed["end_offset"] == 128 + expected_bytes
    assert parsed["incomplete_tail"] is True
    assert len(parsed["messages"]) == 1
    assert parsed["messages"][0]["text"].startswith("Please preserve")


def test_jsonl_parser_extracts_text_thinking_tools_and_tool_results():
    raw = "".join([
        _line({"uuid": "u1", "timestamp": "t1", "message": {
            "role": "assistant", "content": [
                {"type": "text", "text": "A sufficiently long assistant response."},
                {"type": "thinking", "thinking": "Reasoning trace"},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/work/a.py"}},
                {"type": "tool_result", "content": [{"type": "text", "text": "Read completed."}]},
            ],
        }}),
    ])
    parsed = parse_jsonl(raw, session_id="fallback")
    assert [m["kind"] for m in parsed["messages"]] == ["text", "thinking", "tool_use", "tool_result"]
    assert parsed["touches"] == [{"path": "/work/a.py", "tool": "Read", "ts": "t1"}]


def test_jsonl_parser_skips_malformed_complete_lines_but_advances_checkpoint():
    raw = "{not-json}\n" + _line({
        "uuid": "u2", "message": {"role": "user", "content": "A sufficiently long user message."}
    })
    parsed = parse_jsonl(raw, session_id="fallback")
    assert parsed["bytes_consumed"] == len(raw.encode("utf-8"))
    assert len(parsed["messages"]) == 1


@pytest.mark.parametrize("offset", [-1, True, 1.5])
def test_jsonl_parser_rejects_invalid_offsets(offset):
    with pytest.raises(ValueError):
        parse_jsonl("", session_id="session", offset=offset)
