# Ensemble deployment profiles

Ensemble is one codebase and one application identity with multiple deployment profiles. A worker is a lightweight execution participant, not a fork or a separate product.

## Profiles

| Profile | Intended deployment | Current composition |
|---|---|---|
| `worker` | R9000-class devices, Raspberry Pis, small hosts | Standalone Worker Runtime only; does not import the full API or initialize Ensemble stores |
| `standard` | General-purpose host | Core application, retrieval, Memory, Knowledge, Graph, Thompson and collaboration. GA and Learning are omitted. |
| `full` | Server/workstation | Standard capabilities plus GA and Learning, including optional analytics and model-training dependencies. |

Standard and Full now have a real first capability boundary: Standard does not construct or initialize GA or Learning stores, and those route groups are not exposed. The analytics and GA-configuration dependencies are imported lazily and are installed only with Full. Both profiles still share the existing core application and its persistence, ingestion, retrieval, Memory, Knowledge, Graph and Thompson composition. Further capability groups will be migrated incrementally behind the shared lifecycle contracts.

## Run

Install only the worker runtime for a small device:

```bash
python -m pip install 'flossware-ensemble[worker]'
ensemble-runtime --profile worker --host 0.0.0.0 --port 8091
```

Install the existing application dependencies for a Standard deployment:

```bash
python -m pip install 'flossware-ensemble[standard]'
ensemble-runtime --profile standard --host 0.0.0.0 --port 8080
```

Install the complete optional dependency set for a Full deployment:

```bash
python -m pip install 'flossware-ensemble[full]'
ensemble-runtime --profile full --host 0.0.0.0 --port 8080
```

The profile can also be selected with `ENSEMBLE_PROFILE=worker|standard|full`. The base package contains only FastAPI and Uvicorn; database, vector, graph, document, and local intelligence dependencies are optional extras. The existing `ensemble` and `ensemble-worker` commands remain available for compatibility.

## Capability lifecycle

Extensions implement the shared `Capability` contract in `ensemble.core`: a descriptor, asynchronous initialization and shutdown, and a health report. Register implementations in a `CapabilityRegistry` and pass that registry to `create_runtime(..., capability_registry=...)` or `create_app(..., capability_registry=...)`.

The registry resolves profile eligibility and dependencies before startup. The lifecycle starts dependencies first, rolls back a partial startup on failure, and shuts down in reverse order. Runtime discovery is exposed at:

- `GET /api/v1/runtime/capabilities`
- `GET /api/v1/runtime/capabilities/health`

The current migration has made GA and Learning profile-specific. Further built-in capability groups will move behind this lifecycle incrementally; the shared contracts do not imply that every legacy subsystem has already been extracted.

## Upgrade path

A deployment can change its selected profile and install the corresponding package dependencies. Moving from Standard to Full enables the GA and Learning routes and initializes their stores on startup. Moving from Full to Standard stops exposing those routes and skips their stores; existing database records are retained. Profile changes do not run schema migrations automatically.

The worker profile imports its application lazily and does not import `ensemble.api`. Standard and Full import the shared API composition, but optional GA and Learning dependencies are lazy and profile-gated. Provider credentials should remain in a central provider gateway initially; no credentials or shared secrets are introduced by the profile model.

## Design constraints

- One codebase, shared contracts, no worker fork.
- Full remains the compatibility baseline until modularization is proven.
- Worker does not initialize full stores, Control Center, GA, Thompson, or learning.
- No message broker, durable queue, or guaranteed-delivery promise.
- No shared-secret or certificate infrastructure.
- Never expose unauthenticated worker or federation endpoints to untrusted networks.
