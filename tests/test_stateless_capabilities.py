import pytest
from fastapi.testclient import TestClient

from ensemble.api import create_app
from ensemble.extraction import extract
from ensemble.intent import classify
from ensemble.ranking import fuse
from ensemble.store import InMemoryDocumentStore
from ensemble.tokenization import TokenizationError, tokenize


def test_tokenization_case_folding_and_short_token_filter():
    terms = [token["term"] for token in tokenize("lexical-v1", "Hello WORLD x a7")]
    assert terms == ["hello", "world", "a7"]


def test_tokenization_preserves_compound_identifier_and_components():
    tokens = tokenize("lexical-v1", "memory-service")
    assert [(token["term"], token["kind"]) for token in tokens] == [
        ("memory", "part"),
        ("memory-service", "whole"),
        ("service", "part"),
    ]


def test_tokenization_uuid_and_unicode_offsets():
    text = "café 550e8400-e29b-41d4-a716-446655440000"
    tokens = tokenize("lexical-v1", text)
    uuid_token = next(token for token in tokens if token["term"].startswith("550e"))
    assert text[uuid_token["start"]:uuid_token["end"]] == uuid_token["term"]
    assert any(token["term"] == "café" for token in tokens)


def test_tokenization_is_deterministic():
    text = "pkg.module-name and 550e8400-e29b-41d4-a716-446655440000"
    assert tokenize("lexical-v1", text) == tokenize("lexical-v1", text)


def test_tokenization_rejects_unknown_profile_and_non_string_text():
    with pytest.raises(TokenizationError) as error:
        tokenize("missing", "hello")
    assert error.value.status == 422
    with pytest.raises(TokenizationError):
        tokenize("lexical-v1", None)


def test_extraction_finds_exact_identifiers_and_preserves_spelling():
    text = "CPSEARCH-10177 at v2.4.1 and https://example.com/x"
    entities = extract(text)
    found = {(entity["type"], entity["text"]) for entity in entities}
    assert ("issue_key", "CPSEARCH-10177") in found
    assert ("version", "v2.4.1") in found
    assert ("url", "https://example.com/x") in found
    assert all(entity["exact"] and entity["confidence"] == 1.0 for entity in entities)


def test_extraction_flags_uncertain_names_and_avoids_overlap():
    entities = extract("please ask Alice Smith about memory-service in /tmp/work")
    uncertain = next(entity for entity in entities if entity["text"] == "Alice Smith")
    assert uncertain["exact"] is False
    assert uncertain["confidence"] < 1.0
    spans = [(entity["start"], entity["end"]) for entity in entities if entity["exact"]]
    assert all(not (a < d and c < b) for i, (a, b) in enumerate(spans)
               for c, d in spans[i + 1:])


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("what did we decide about cache TTL last time", "history"),
        ("why does the hook time out", "debug"),
        ("how do I register a Stop hook", "howto"),
        ("where is the graph store file", "lookup"),
        ("implement a retry for the embedding client", "change"),
    ],
)
def test_intent_classification_labels(query, expected):
    assert classify(query)["label"] == expected


def test_intent_returns_evidence_and_zero_confidence_without_evidence():
    result = classify("why does the service fail")
    assert result["evidence"]
    assert result["scores"]["debug"] > 0
    empty = classify("blue chair")
    assert empty["label"] == "other"
    assert empty["confidence"] == 0.0
    assert empty["evidence"] == []
    assert empty["scores"] == {}
    assert empty["intents"] == []
    assert empty["constraints"] == []
    assert empty["required_capabilities"] == []


def test_intent_is_deterministic_and_bounded():
    query = "what did we decide earlier and why does it fail"
    assert classify(query) == classify(query)
    with pytest.raises(OverflowError):
        classify("x" * 20_001)


def test_ranking_fuses_lists_and_explains_contributions():
    results = fuse(
        {
            "lexical": [{"key": "a", "rank": 1}, {"key": "b", "rank": 2}],
            "vector": [{"key": "b", "rank": 1}, {"key": "c", "rank": 2}],
        },
        3,
    )
    assert results[0]["key"] == "b"
    assert results[0]["contributions"] == {
        "lexical": round(1 / 62, 6),
        "vector": round(1 / 61, 6),
    }


def test_ranking_is_order_independent_and_ties_break_by_key():
    left = fuse({"x": [{"key": "b", "rank": 1}, {"key": "a", "rank": 1}]}, 2)
    right = fuse({"x": [{"key": "a", "rank": 1}, {"key": "b", "rank": 1}]}, 2)
    assert left == right
    assert [item["key"] for item in left] == ["a", "b"]


def test_ranking_ignores_duplicate_candidates_within_one_list():
    once = fuse({"x": [{"key": "a", "rank": 1}]}, 10)
    twice = fuse({"x": [{"key": "a", "rank": 1}, {"key": "a", "rank": 2}]}, 10)
    assert once == twice


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"lists": {"x": [{"key": "a", "rank": True}]}},
        {"lists": {"x": [{"key": "", "rank": 1}]}},
        {"lists": {"x": [{"key": "a", "rank": 0}]}},
        {"lists": {"x": []}, "top_k": 0},
    ],
)
def test_ranking_rejects_invalid_inputs(payload):
    with pytest.raises(ValueError):
        fuse(payload.get("lists"), payload.get("top_k", 10), payload.get("k", 60))


def test_stateless_capabilities_are_exposed_through_rest():
    app = create_app(store=InMemoryDocumentStore())
    with TestClient(app) as client:
        assert client.get("/api/v1/tokenization/ready").json()["ready"] is True
        tokenized = client.post(
            "/api/v1/tokenization/tokenize",
            json={"profile": "lexical-v1", "text": "memory-service"},
        )
        assert tokenized.status_code == 200
        assert any(token["term"] == "memory-service" for token in tokenized.json()["tokens"])

        extracted = client.post(
            "/api/v1/entities/extract",
            json={"text": "issue CPSEARCH-10177", "source": "control-center.entity-workbench"},
        )
        assert extracted.status_code == 200
        candidates = extracted.json()["entities"]
        assert candidates[0]["text"] == "CPSEARCH-10177"
        assert candidates[0]["normalized"] == "cpsearch-10177"
        assert candidates[0]["provenance"]["source"] == "control-center.entity-workbench"

        resolved = client.post("/api/v1/entities/resolve", json={"entities": candidates})
        assert resolved.status_code == 200
        canonical = resolved.json()["results"][0]["entity"]
        assert canonical["type"] == "entity"
        assert resolved.json()["results"][0]["status"] == "created"

        repeated = client.post("/api/v1/entities/resolve", json={"entities": candidates})
        assert repeated.json()["results"][0]["status"] == "matched"

        second_candidate = client.post(
            "/api/v1/entities/extract", json={"text": "OPS-7"}
        ).json()["entities"][0]
        second = client.post(
            "/api/v1/entities/resolve", json={"entities": [second_candidate]}
        ).json()["results"][0]["entity"]
        linked = client.post("/api/v1/entities/link", json={
            "source_id": canonical["id"], "target_id": second["id"],
            "relationship": "references",
        })
        assert linked.status_code == 200
        assert linked.json()["edge"]["type"] == "references"

        intent = client.post(
            "/api/v1/intent/classify",
            json={"text": "why does search fail and compare it with the previous approach"},
        )
        assert intent.status_code == 200
        assert intent.json()["label"] == "debug"
        assert {item["label"] for item in intent.json()["intents"]} >= {"debug", "compare"}
        assert "search" in intent.json()["required_capabilities"]

        ranked = client.post(
            "/api/v1/ranking/rank",
            json={"lists": {"lexical": [{"key": "doc-1", "rank": 1}]}},
        )
        assert ranked.status_code == 200
        assert ranked.json()["results"][0]["key"] == "doc-1"


def test_stateless_capability_errors_use_problem_details():
    app = create_app(store=InMemoryDocumentStore())
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/tokenization/tokenize",
            json={"profile": "unknown", "text": "hello"},
        )
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")
