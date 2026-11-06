# Collaboration REST Contracts

**Status:** Initial provider-neutral collaboration contract implemented over the existing Ensemble SQLite persistence. These records complement Memory and Knowledge; they do not replace them.

## Design boundaries

- Ensemble owns durable identity, project membership concepts, attribution, contributions, handoff continuity, synthesis provenance, proposals, and decisions.
- Integrations own environment-specific capture. A Claude Code, Cursor, Codex, or other adapter may publish checkpoints and handoffs through these REST resources. Ensemble does not install hooks or schedule agents.
- Handoffs are structured continuity records, not chat transcripts. The contract supports incremental checkpoints so interrupted sessions can preserve the last known state without waiting for a final summary.
- REST resource shapes are language-neutral. Python types, database tables, and model/provider identifiers are implementation metadata, not public type requirements.
- The initial implementation uses the existing SQLite database and one application. It introduces neither a service nor another database.
- Membership roles describe read, contribute, review, approve, and owner capabilities. Project read routes require a `viewer_id` query parameter and enforce membership; write routes enforce the corresponding contribution/review/approval role. When bearer authentication is enabled, the viewer/actor must match the authenticated principal. These are domain-level authorization concepts, not a replacement for enterprise IAM.
- Contributions and handoff events/checkpoints are append-only records. Mutable lifecycle resources use optimistic version checks; source records remain addressable.

## Identity and projects

A participant has a stable identifier, display name, and type (`human` or `agent`). Agent provider/model metadata is optional and must never be required to interpret an agent identity.

Example:

```json
{
  "participant_id": "agent:build-assistant",
  "display_name": "Build Assistant",
  "participant_type": "agent",
  "provider": "optional-provider",
  "model": "optional-model"
}
```

A project has a stable identifier, name, description, owner, and a `human_approval_required` policy. Creating a project also creates its owner membership. Membership roles are `read`, `contribute`, `review`, `approve`, and `owner`.

## Contributions and activity

`POST /api/v1/collaboration/projects/{project_id}/contributions` creates an independently addressable contribution with actor, type, concise summary, optional work-item identifier, optional stable source reference, and review status. The server assigns a stable contribution ID and timestamps. Concurrent writes do not overwrite one another.

`GET /api/v1/collaboration/projects/{project_id}/contributions` supports actor, status, and bounded-limit filters. `GET /api/v1/collaboration/projects/{project_id}/activity` provides the project-scoped chronological activity ledger and supports actor, record-kind, status, and limit filters. Git/GitHub artifacts are linked by reference; their full canonical content is not copied.

## Handoffs

A handoff contains project and work-item context, sender, optional recipient, status, timestamps, objective, completed work, current state, blockers, next actions, and references. Status transitions are:

- `open` → `acknowledged` → `completed`
- `open` or `acknowledged` → `superseded`

Invalid transitions return HTTP 409. The intended recipient acknowledges and completes a directed handoff. Updates use compare-and-swap versions so concurrent transitions cannot silently overwrite each other.

`POST /api/v1/collaboration/projects/{project_id}/handoffs` creates a handoff. `GET /api/v1/collaboration/handoffs/{handoff_id}` returns the current handoff and its append-only history.

`POST /api/v1/collaboration/handoffs/{handoff_id}/checkpoints` records incremental progress: current state, completed work, blockers, next actions, and references. Each checkpoint is a distinct immutable record and event. An integration can publish checkpoints during a session or at lifecycle boundaries; no transcript is required as the source of truth.

`POST /api/v1/collaboration/handoffs/{handoff_id}/acknowledge`, `/complete`, and `/supersede` implement the lifecycle transitions. Superseding creates a replacement while retaining the original handoff and its event history.

## Synthesis, proposals, and decisions

`POST /api/v1/collaboration/projects/{project_id}/syntheses` creates a synthesis from all project contributions or an explicit selected set. Every statement retains contribution IDs, source references, and actor attribution. The default strategy is deterministic and extractive: it presents contribution summaries without inventing consensus. A replaceable `SynthesisStrategy` contract permits a future summarizer without changing persistence or REST shapes.

`POST /api/v1/collaboration/syntheses/{synthesis_id}/refresh` creates a new synthesis record that references the prior synthesis. Refresh does not overwrite the previous result.

Proposals preserve title, summary, rationale, human-approval policy, and status. Objections are separate attributable records and may cite project contributions. Competing proposals remain independently addressable; accepting one does not silently erase or supersede alternatives.

`POST /api/v1/collaboration/projects/{project_id}/proposals` creates a proposal. `POST /api/v1/collaboration/proposals/{proposal_id}/objections` adds an objection. `GET /api/v1/collaboration/proposals/{proposal_id}` returns the proposal, objections, and decision history.

`POST /api/v1/collaboration/proposals/{proposal_id}/decisions` records a decision with decision-maker, timestamp, rationale, status, and human-approval evidence. When human approval is required, an agent may recommend or move a proposal under review but cannot accept or reject it. A final decision requires a human participant with the project `approve` or `owner` role.

## Endpoint inventory

| Method | Path | Purpose |
|---|---|---|
| POST / GET | `/participants`, `/participants/{id}` | Create and read human or agent identities |
| POST / GET | `/projects`, `/projects/{id}` | Create and read projects |
| POST / GET | `/projects/{id}/members` | Add and list project memberships |
| POST / GET | `/projects/{id}/contributions` | Create and query attributable contributions |
| GET | `/projects/{id}/activity` | Query project activity |
| POST / GET | `/projects/{id}/handoffs`, `/handoffs/{id}` | Create and inspect handoffs and history |
| POST | `/handoffs/{id}/checkpoints` | Append an incremental continuity checkpoint |
| POST | `/handoffs/{id}/acknowledge`, `/complete`, `/supersede` | Apply validated handoff transitions |
| POST / GET | `/projects/{id}/syntheses` | Create and list project syntheses |
| POST | `/syntheses/{id}/refresh` | Create a provenance-linked refreshed synthesis |
| POST / GET | `/projects/{id}/proposals` | Create and list competing proposals |
| POST / GET | `/proposals/{id}/objections`, `/proposals/{id}` | Add objections and inspect proposal history |
| POST / GET | `/proposals/{id}/decisions`, `/projects/{id}/decisions` | Record and query attributable decisions |

Project-scoped read routes require `viewer_id` and return 403 for non-members. All collection endpoints are bounded to 500 records per request. IDs are stable opaque strings. All writes are attributable to participant IDs; when bearer authentication is enabled, a supplied actor must match the authenticated principal.

## Persistence and compatibility

The initial SQLite implementation stores typed record kinds in the existing database with indexed project, actor, status, and creation-time columns, a JSON payload, and optimistic version. Membership identity keys are unique per project/participant pair. This keeps the initial implementation compact while preserving REST-level resource boundaries. Database choice and schema are not exposed in the API.

Contract tests cover valid and invalid participant roles, independent concurrent contributions, handoff transitions and optimistic concurrency, incremental checkpoints, synthesis provenance and refresh, objections, and human-gated decisions. SQLite migration/restart tests verify durable records.
