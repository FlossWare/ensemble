from __future__ import annotations

import hashlib
import json
from typing import Any


MAX_TRANSCRIPT_BYTES = 8 * 1024 * 1024
MAX_TOOL_TEXT_CHARS = 32_000
FILE_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep"}


def parse_jsonl(content: str, *, session_id: str, offset: int = 0) -> dict[str, Any]:
    """Parse complete JSONL transcript lines without consuming a partial tail.

    Offsets are absolute UTF-8 byte offsets, not Python character positions. The
    caller owns file access and checkpoint persistence; this function is pure.
    """
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 256:
        raise ValueError("session_id must be a non-empty string of at most 256 characters")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    raw = content.encode("utf-8")
    if len(raw) > MAX_TRANSCRIPT_BYTES:
        raise OverflowError("transcript chunk exceeds 8 MiB")

    messages: list[dict[str, str]] = []
    touches: list[dict[str, str]] = []
    first_ts = last_ts = ""
    consumed = 0
    for raw_line in raw.splitlines(keepends=True):
        if not raw_line.endswith(b"\n"):
            break
        consumed += len(raw_line)
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        session_id = record.get("sessionId") or session_id
        timestamp = record.get("timestamp") or ""
        if timestamp:
            first_ts = first_ts or timestamp
            last_ts = timestamp
        message = record.get("message")
        role = message.get("role") if isinstance(message, dict) else None
        blocks = message.get("content") if isinstance(message, dict) else None
        record_uuid = record.get("uuid")
        if not isinstance(record_uuid, str) or not record_uuid:
            record_uuid = "line-" + hashlib.sha256(raw_line).hexdigest()[:32]

        def add(kind: str, message_role: str, value: str) -> None:
            if value and value.strip():
                messages.append({
                    "uuid": record_uuid, "role": message_role, "kind": kind,
                    "text": value[:MAX_TOOL_TEXT_CHARS], "ts": timestamp,
                })

        if isinstance(blocks, str):
            if role in {"user", "assistant"} and len(blocks.strip()) >= 20:
                add("text", role, blocks)
            continue
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and role in {"user", "assistant"}:
                value = block.get("text")
                if isinstance(value, str) and len(value.strip()) >= 20:
                    add("text", role, value)
            elif kind == "thinking":
                value = block.get("thinking")
                if isinstance(value, str):
                    add("thinking", "assistant", value)
            elif kind == "tool_use":
                name = block.get("name") or "tool"
                args = block.get("input") or {}
                if not isinstance(args, dict):
                    args = {"input": args}
                add("tool_use", "assistant", f"{name} {json.dumps(args, ensure_ascii=False)}")
                if name in FILE_TOOLS:
                    for key in ("file_path", "path", "notebook_path"):
                        value = args.get(key)
                        if isinstance(value, str) and value.startswith("/"):
                            touches.append({"path": value, "tool": name, "ts": timestamp})
                            break
            elif kind == "tool_result":
                value = block.get("content")
                if isinstance(value, list):
                    value = " ".join(
                        item.get("text", "") for item in value if isinstance(item, dict)
                    )
                if not isinstance(value, str):
                    value = json.dumps(value, ensure_ascii=False)
                add("tool_result", "user", value)

    return {
        "session_id": session_id,
        "messages": messages,
        "touches": touches,
        "first_ts": first_ts,
        "last_ts": last_ts or first_ts,
        "bytes_consumed": consumed,
        "end_offset": offset + consumed,
        "incomplete_tail": consumed < len(raw),
    }
