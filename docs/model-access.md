# Model Access

**Status:** Architectural direction accepted; detailed contracts and implementation remain to be designed.

## 1. Responsibility

Model Access is an Ensemble capability. It provides a stable, provider-neutral REST contract for model discovery, inference, token counting, and usage reporting.

It is not a wrapper around an AI coding assistant. Ensemble calls model gateways or inference providers directly through provider adapters.

## 2. Provider versus client

These are different architectural roles:

- A **model provider** accepts inference requests and returns model responses. Examples include EnMaaS, Anthropic API, and other compatible gateways.
- A **client application** consumes Ensemble capabilities. Examples include Claude Code, Crush, Cursor, Codex, and ordinary applications.

Client applications do not belong in the model-provider registry merely because they can generate text.

The following do not belong in Ensemble core:

- Claude CLI subprocess execution.
- Claude Code authentication inspection or expected-provider checks.
- Claude-specific runtime selection.
- A ClaudeCodeProvider whose responsibility is to launch or control Claude Code.

If Claude-specific client behavior is needed, it belongs in ensemble-claude or another client integration. That integration may consume Ensemble APIs while allowing Claude Code to use its own inference route.

## 3. Provider-neutral contract

The internal model invocation contract must not be defined by the wire format of the first provider.

The initial contract should represent, at minimum:

- Explicit provider and model identity.
- A structured sequence of messages and optional system instruction.
- Generation limits and supported generic inference options.
- Optional provider-specific extension metadata, clearly namespaced and never required by core consumers.
- Response text or structured content as supported by the contract.
- Stop reason, usage, latency, and provider request identifier when available.
- A typed error with provider, model, retryability, and safe diagnostic context.

Protocol-specific details such as Anthropic headers, vendor-specific thinking request fields, vendor-specific message blocks, and streaming event formats remain inside the corresponding adapter.

Do not pretend all providers support identical features. Capabilities must be declared per model/provider and validated before invocation.

## 4. Initial EnMaaS adapter

EnMaaS is the first identified enterprise gateway and the first adapter candidate.

Known investigation results:

- Base URL: https://api.enmaas.devshift.net.
- Inference endpoint: POST /v1/messages.
- Wire protocol: Anthropic Messages API.
- Authentication is configured through the gateway credential; the intended credential variable still needs confirmation between ANTHROPIC_API_KEY and ENMAAS_KEY.
- GET /v1/models was confirmed live and returned 12 models.
- POST /v1/messages/count_tokens was confirmed live for the tested Claude, GLM, and DeepSeek model IDs.
- The tested model IDs included claude-haiku-5-5, rits/zai-org/glm-5-3, and rits/deepseek-ai/DeepSeek-V4.1-Flash-1017.
- The gateway's /health returned HTTP 200. Other guessed health paths did not.
- Streaming, tool use, quotas, rate limits, and per-model advanced reasoning behavior remain unverified.

These are investigation findings, not a guarantee of current gateway availability or enterprise workload approval.

Implement the EnMaaS adapter against the Anthropic Messages wire protocol. Keep its base URL and credential configurable. Do not hard-code credentials or read them from Claude Code settings.

## 5. Catalog and policy

Gateway discovery is useful input, but it is not the policy source of truth.

Ensemble should maintain explicit model configuration per provider profile, including:

- Provider identity and endpoint.
- Model identifier.
- Declared capabilities and known limitations.
- Workload/profile eligibility.
- Credential reference.
- Discovery/verification timestamp where applicable.

Distinguish:

- **Discovered:** the gateway lists the model.
- **Reachable:** the provider can currently be contacted.
- **Authorized:** the configured identity can invoke it.
- **Approved:** policy permits the model for the requested workload.

These states are not interchangeable. Unknown, unavailable, unauthorized, or unapproved model requests must fail clearly. Never silently fall back to another model or provider.

Ensemble must respect upstream gateway authorization and enterprise policy rather than attempting to bypass or reproduce them.

## 6. Enterprise and personal isolation

Enterprise and personal model access are separate profiles with independent:

- Provider configuration.
- Credential references.
- Model allowlists.
- Workload policies.
- Usage accounting.

No credential, provider, or fallback route may cross profile boundaries implicitly. Every inference and token-count request must identify its profile/provider context explicitly or resolve it through an unambiguous caller policy.

The exact EnMaaS credential must be confirmed before implementation. The existing investigation found both ANTHROPIC_API_KEY and ENMAAS_KEY; the review tool currently uses the former. Do not infer that both are interchangeable.

## 7. Initial REST surface

Candidate endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| GET | /api/v1/providers | List configured providers and profiles without exposing secrets |
| GET | /api/v1/models | List configured/approved models and declared capabilities |
| POST | /api/v1/inference | Invoke a selected provider/model through the neutral contract |
| POST | /api/v1/tokens/count | Count tokens using the selected provider/model |

Detailed schemas, status codes, and error conventions are deferred to the REST contract design.

An Anthropic-compatible facade exposed by Ensemble is not part of the initial design. Add one only when a concrete client requires it; it must remain a thin translation layer over the canonical provider-neutral contract.

## 8. Initial scope and deferred features

Initial implementation should focus on non-streaming inference and token counting, with explicit model selection, profile isolation, clear errors, and usage records.

Defer streaming, tool use, extended thinking, and other advanced provider features until support is verified per model and a concrete consumer requires them.

Do not introduce a model broker, distributed routing system, or provider federation layer. A provider adapter and explicit local routing are sufficient initially.
