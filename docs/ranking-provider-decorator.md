# Ranking Provider and Decorator Design

## Decision

Ensemble keeps reciprocal-rank fusion (RRF) as its deterministic baseline. Reranking is an optional post-retrieval decoration, not a required retrieval dependency and not a separately deployed Ensemble service.

A reusable reranking decorator wraps a compatible retriever. It obtains the underlying retrieval result, optionally submits the bounded candidate set to a ranking provider, and returns the same result contract with candidate ordering and ranking diagnostics updated. The decorator must not own document retrieval, persistence, source lifecycle, or provider-specific configuration policy.

## Components

- **Retriever contract:** produces candidate results with stable candidate identity, source/revision/chunk provenance, existing retrieval scores, and channel diagnostics.
- **Reranking decorator:** wraps any retriever implementing that contract. It applies ranking only when enabled and eligible candidates exist.
- **Ranking provider contract:** accepts a query and bounded candidate representations; returns one finite score per candidate in the original order, plus provider identity/status metadata.
- **Local cross-encoder provider:** adapts the existing pinned local model to the provider contract.
- **HTTP ranking provider:** optional adapter for an externally hosted ranking endpoint. It is configured explicitly and does not create another Ensemble service.

## Required behavior

1. RRF remains the baseline order when reranking is disabled, unavailable, times out, or returns invalid/misaligned scores.
2. Provider output must map one-to-one to submitted candidates. Candidate IDs are retained by the decorator; providers do not get to redefine identity or provenance.
3. Only a bounded top-N candidate window is sent to a provider. Query and candidate text limits remain enforced.
4. Ranking is stable: equal scores preserve the underlying retriever order.
5. Failures are visible in diagnostics. Responses distinguish not_requested, reranked, and fallback; an unavailable provider must not be reported as a successful rerank.
6. Ranking is optional. A provider outage must not make otherwise usable lexical/vector retrieval unavailable.
7. The HTTP adapter uses bounded connect/read/overall timeouts, response-size limits, strict schema validation, and no automatic unbounded retries. It sends only the query and candidate text/opaque request-local IDs required for scoring.
8. Provider configuration is explicit. No external provider is contacted unless the operator configures and enables it.
9. The abstraction remains language-neutral at the HTTP boundary and does not introduce provider-specific fields into the core retrieval contract.

## Deliberately out of scope

- A separate ranking microservice owned by Ensemble.
- Making an external ranking provider mandatory.
- Persisting ranking-provider state or candidate payloads.
- Provider-specific prompt caching.
- Learning/calibrating fusion weights as part of the first provider integration.

## Implementation sequence

1. Extract a stable retriever result contract and add a decorator that can wrap it without changing source metadata or channel diagnostics.
2. Adapt the current local cross-encoder to the ranking-provider contract.
3. Add an opt-in HTTP provider with strict bounds and explicit health/configuration reporting.
4. Expose ranking mode, provider identity, and fallback reason in hybrid-search diagnostics.
5. Verify that disabled, unavailable, malformed, timeout, and successful provider cases preserve the baseline contract.