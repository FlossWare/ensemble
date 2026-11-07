import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

import pytest

from ensemble.transcripts import parse_jsonl
from ensemble.transcript_store import (
    InMemoryTranscriptCheckpointStore, SqliteTranscriptCheckpointStore,
    jobs, transcript_metadata,
)


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


def test_transcript_jobs_record_completion_failures_and_restart_recovery():
    store = InMemoryTranscriptCheckpointStore()
    job_id = "a" * 64

    first = asyncio.run(store.start_job(
        job_id, "source-1", "revision-1", "project", "transcript.jsonl",
        64, hashlib.sha256(b"slice").hexdigest(),
    ))
    duplicate = asyncio.run(store.start_job(
        job_id, "source-1", "revision-1", "project", "transcript.jsonl",
        64, hashlib.sha256(b"slice").hexdigest(),
    ))
    assert first == {"status": "running", "attempts": 1, "created": True}
    assert duplicate == {"status": "running", "attempts": 1, "created": False}

    store._jobs[job_id]["lease_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    recovered = asyncio.run(store.recover_interrupted())
    interrupted = asyncio.run(store.get_job(job_id))
    assert recovered == 1
    assert interrupted["status"] == "interrupted"
    assert "lease expired" in interrupted["last_error"]

    retry = asyncio.run(store.start_job(
        job_id, "source-1", "revision-1", "project", "transcript.jsonl",
        64, hashlib.sha256(b"slice").hexdigest(),
    ))
    assert retry == {"status": "running", "attempts": 2, "created": True}

    asyncio.run(store.fail_job(job_id, "temporary indexing failure"))
    failed = asyncio.run(store.get_job(job_id))
    assert failed["status"] == "failed"
    assert failed["last_error"] == "temporary indexing failure"

    retry = asyncio.run(store.start_job(
        job_id, "source-1", "revision-1", "project", "transcript.jsonl",
        64, hashlib.sha256(b"slice").hexdigest(),
    ))
    assert retry["status"] == "running"
    assert retry["attempts"] == 3

    completed = asyncio.run(store.complete_job(job_id, 96, {"inserted": 2}))
    assert completed["status"] == "completed"
    assert completed["end_offset"] == 96
    assert completed["result"] == {"inserted": 2}
    assert asyncio.run(store.get_job(job_id))["status"] == "completed"
    assert asyncio.run(store.list_jobs(10))[0]["job_id"] == job_id


def test_transcript_lease_prevents_live_job_recovery_and_stale_owner_completion():
    store = InMemoryTranscriptCheckpointStore()
    job_id = "d" * 64
    digest = hashlib.sha256(b"slice").hexdigest()
    owner_a, owner_b = "a" * 32, "b" * 32

    first = asyncio.run(store.start_job(
        job_id, "source-1", "revision-1", "project", "transcript.jsonl",
        64, digest, owner_a,
    ))
    duplicate = asyncio.run(store.start_job(
        job_id, "source-1", "revision-1", "project", "transcript.jsonl",
        64, digest, owner_b,
    ))
    assert first["created"] is True
    assert duplicate["created"] is False
    assert asyncio.run(store.heartbeat_job(job_id, owner_a)) is True
    assert asyncio.run(store.heartbeat_job(job_id, owner_b)) is False
    assert asyncio.run(store.recover_interrupted()) == 0
    assert asyncio.run(store.get_job(job_id))["status"] == "running"

    store._jobs[job_id]["lease_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    assert asyncio.run(store.recover_interrupted()) == 1
    retry = asyncio.run(store.start_job(
        job_id, "source-1", "revision-1", "project", "transcript.jsonl",
        64, digest, owner_b,
    ))
    assert retry == {"status": "running", "attempts": 2, "created": True}

    with pytest.raises(ValueError, match="lease ownership was lost"):
        asyncio.run(store.complete_job(job_id, 96, {"inserted": 1}, owner_a))
    completed = asyncio.run(store.complete_job(job_id, 96, {"inserted": 1}, owner_b))
    assert completed["status"] == "completed"


def test_sqlite_transcript_leases_are_persistent_and_exclusive(tmp_path):
    async def exercise():
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'transcript-leases.db'}"
        first_store = SqliteTranscriptCheckpointStore(database_url)
        second_store = SqliteTranscriptCheckpointStore(database_url)
        job_id = "e" * 64
        digest = hashlib.sha256(b"sqlite-slice").hexdigest()
        owner_a, owner_b = "c" * 32, "f" * 32
        try:
            async with first_store.engine.begin() as connection:
                await connection.run_sync(transcript_metadata.create_all)
            await first_store.initialize()
            await second_store.initialize()

            claims = await asyncio.gather(
                first_store.start_job(
                    job_id, "source-1", "revision-1", "project", "transcript.jsonl",
                    64, digest, owner_a,
                ),
                second_store.start_job(
                    job_id, "source-1", "revision-1", "project", "transcript.jsonl",
                    64, digest, owner_b,
                ),
            )
            assert sum(bool(claim["created"]) for claim in claims) == 1
            active_owner = (await first_store.get_job(job_id))["lease_owner"]
            stale_owner = owner_b if active_owner == owner_a else owner_a
            assert await second_store.heartbeat_job(job_id, active_owner) is True
            assert await second_store.heartbeat_job(job_id, stale_owner) is False
            assert await second_store.recover_interrupted() == 0
            assert (await second_store.get_job(job_id))["status"] == "running"

            expired = datetime.now(timezone.utc) - timedelta(seconds=1)
            async with first_store.engine.begin() as connection:
                await connection.execute(update(jobs).where(
                    jobs.c.job_id == job_id,
                ).values(lease_expires_at=expired))
            assert await second_store.recover_interrupted() == 1
            assert (await second_store.get_job(job_id))["status"] == "interrupted"

            new_owner = "9" * 32
            retry = await second_store.start_job(
                job_id, "source-1", "revision-1", "project", "transcript.jsonl",
                64, digest, new_owner,
            )
            assert retry == {"status": "running", "attempts": 2, "created": True}
            with pytest.raises(ValueError, match="lease ownership was lost"):
                await first_store.complete_job(job_id, 96, {"inserted": 1}, active_owner)
            completed = await second_store.complete_job(
                job_id, 96, {"inserted": 1}, new_owner,
            )
            assert completed["status"] == "completed"
            assert (await first_store.get_job(job_id))["status"] == "completed"
        finally:
            await first_store.close()
            await second_store.close()

    asyncio.run(exercise())
