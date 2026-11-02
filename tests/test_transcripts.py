import asyncio
import hashlib
import json

import pytest

from ensemble.transcripts import parse_jsonl
from ensemble.transcript_store import InMemoryTranscriptCheckpointStore


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


def _checkpoint_commit(store, *, start=0, end=4, payload=b"data", result=None):
    return asyncio.run(store.commit(
        "source-1", "revision-1", "project", "transcript.jsonl",
        start, end, hashlib.sha256(payload).hexdigest(),
        result or {"inserted": 1},
    ))


def test_checkpoint_accepts_caller_supplied_initial_offset():
    store = InMemoryTranscriptCheckpointStore()
    first = _checkpoint_commit(store, start=4, end=8)

    assert first["replayed"] is False
    assert first["checkpoint"]["byte_offset"] == 8


def test_checkpoint_advances_and_exact_retry_is_reported_as_replay():
    store = InMemoryTranscriptCheckpointStore()
    first = _checkpoint_commit(store)
    replay = _checkpoint_commit(store)

    assert first["replayed"] is False
    assert first["checkpoint"]["byte_offset"] == 4
    assert replay["replayed"] is True
    assert replay["result"] == {"inserted": 1}


def test_checkpoint_rejects_gap_or_overlap_without_mutating_state():
    store = InMemoryTranscriptCheckpointStore()
    _checkpoint_commit(store)

    with pytest.raises(ValueError, match="expected byte offset 4"):
        _checkpoint_commit(store, start=8, end=12, payload=b"next")

    checkpoint = asyncio.run(store.get("source-1", "revision-1"))
    assert checkpoint["byte_offset"] == 4


def test_zero_byte_incomplete_tail_retry_is_idempotent_but_changed_tail_can_retry():
    store = InMemoryTranscriptCheckpointStore()
    first = _checkpoint_commit(store, start=0, end=0, payload=b'{"partial":')
    replay = _checkpoint_commit(store, start=0, end=0, payload=b'{"partial":')
    appended = _checkpoint_commit(
        store, start=0, end=13, payload=b'{"partial":}\n', result={"inserted": 0}
    )

    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert appended["replayed"] is False
    assert appended["checkpoint"]["byte_offset"] == 13


@pytest.mark.parametrize(
    ("start", "end", "digest"),
    [
        (-1, 0, "0" * 64),
        (0, -1, "0" * 64),
        (True, 1, "0" * 64),
        (2, 1, "0" * 64),
        (0, 1, "not-a-digest"),
    ],
)
def test_checkpoint_rejects_invalid_offsets_and_payload_digests(start, end, digest):
    store = InMemoryTranscriptCheckpointStore()
    with pytest.raises(ValueError):
        asyncio.run(store.commit(
            "source-1", "revision-1", "project", "transcript.jsonl",
            start, end, digest, {"inserted": 0},
        ))
