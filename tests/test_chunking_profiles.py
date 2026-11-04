import pytest
from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.chunking import ChunkingError, chunk, describe_profiles
from ensemble.store import InMemoryDocumentStore


def test_same_input_gives_deterministic_chunks_and_ids():
    text = "alpha beta gamma " * 300
    assert chunk("window-v1", "source-1", text, "text/plain") == chunk(
        "window-v1", "source-1", text, "text/plain"
    )


def test_chunk_id_depends_on_profile_and_source():
    text = "same content"
    first = chunk("window-v1", "source-1", text, "text/plain")[0]["chunk_id"]
    other_source = chunk("window-v1", "source-2", text, "text/plain")[0]["chunk_id"]
    other_profile = chunk("markdown-v1", "source-1", text, "text/plain")[0]["chunk_id"]
    assert len({first, other_source, other_profile}) == 3


def test_windows_cover_text_with_overlap_and_exact_offsets():
    text = "a" * 4_000
    chunks = chunk("window-v1", "source-1", text, "text/plain")
    assert len(chunks) == 3
    assert chunks[0]["start"] == 0
    assert chunks[0]["end"] == 1_800
    assert chunks[1]["start"] == 1_600
    assert chunks[-1]["end"] == len(text)
    assert all(text[item["start"]:item["end"]] == item["text"] for item in chunks)


def test_unicode_offsets_are_code_point_exact():
    text = "😀" + ("a" * 2_000)
    chunks = chunk("window-v1", "unicode-source", text, "text/plain")
    assert text[chunks[0]["start"]:chunks[0]["end"]] == chunks[0]["text"]
    assert chunks[0]["text"].startswith("😀")


def test_empty_and_short_input():
    assert chunk("window-v1", "source", "", "text/plain") == []
    short = chunk("window-v1", "source", "short", "text/plain")
    assert len(short) == 1
    assert short[0]["text"] == "short"


def test_markdown_headings_give_header_provenance():
    text = "# Title\nfirst section\n## Next\nsecond section"
    chunks = chunk("markdown-v1", "source", text, "text/markdown")
    assert [item["header"] for item in chunks] == ["Title", "Next"]
    assert [item["text"] for item in chunks] == ["# Title\nfirst section\n", "## Next\nsecond section"]


def test_markdown_headings_are_plain_text_for_text_plain():
    text = "# Title\nfirst section\n## Next\nsecond section"
    chunks = chunk("markdown-v1", "source", text, "text/plain")
    assert len(chunks) == 1
    assert chunks[0]["header"] == ""
    assert chunks[0]["text"] == text


def test_oversized_markdown_section_is_windowed_and_keeps_header():
    text = "# Large\n" + ("x" * 4_100)
    chunks = chunk("markdown-v1", "source", text, "text/markdown")
    assert len(chunks) == 3
    assert all(item["header"] == "Large" for item in chunks)
    assert chunks[0]["start"] == 0
    assert chunks[-1]["end"] == len(text)


def test_message_profile_is_one_chunk_and_clips_at_limit():
    text = "m" * 20_100
    chunks = chunk("message-v1", "source", text, "text/plain")
    assert len(chunks) == 1
    assert len(chunks[0]["text"]) == 20_000
    assert chunks[0]["end"] == 20_000


def test_chunking_rejects_invalid_profile_source_and_media_type():
    with pytest.raises(ChunkingError) as profile_error:
        chunk("missing", "source", "text", "text/plain")
    assert profile_error.value.status == 422
    with pytest.raises(ChunkingError):
        chunk("window-v1", "", "text", "text/plain")
    with pytest.raises(ChunkingError) as media_error:
        chunk("window-v1", "source", "text", "application/json")
    assert media_error.value.status == 415


def test_chunking_rejects_non_string_and_oversized_text():
    with pytest.raises(ChunkingError):
        chunk("window-v1", "source", None, "text/plain")
    with pytest.raises(ChunkingError) as error:
        chunk("window-v1", "source", "x" * 4_000_001, "text/plain")
    assert error.value.status == 413


def test_profiles_are_versioned_and_descriptive():
    profiles = describe_profiles()
    assert [profile["name"] for profile in profiles] == [
        "markdown-v1", "message-v1", "window-v1"
    ]
    assert all(profile["version"] == "1" for profile in profiles)


def test_chunking_rest_contract():
    app = create_app(store=InMemoryDocumentStore())
    with TestClient(app) as client:
        assert client.get("/api/v1/chunking/ready").json()["ready"] is True
        response = client.post(
            "/api/v1/chunking/chunk",
            json={
                "profile": "markdown-v1",
                "source_id": "rest-source",
                "media_type": "text/markdown",
                "text": "# Heading\nBody",
            },
        )
        assert response.status_code == 200
        assert response.json()["chunks"][0]["header"] == "Heading"
