# Ensemble deployment profiles

Ensemble is one codebase and one application identity with multiple deployment profiles. A worker is a lightweight execution participant, not a fork or a separate product.

## Profiles

| Profile | Intended deployment | Current composition |
|---|---|---|
| `worker` | R9000-class devices, Raspberry Pis, small hosts | Standalone Worker Runtime only; does not import the full API or initialize Ensemble stores |
| `standard` | General-purpose host | Existing full API composition |
| `full` | Server/workstation | Existing full API composition, including the configured persistence and intelligence subsystems |

The `standard` and `full` names are stable deployment intent, not yet independently selectable subsystem bundles. Today they deliberately use the same application composition. We must not pretend that disabling a profile label removes imports or storage initialization. The next refactoring step is to extract shared core contracts and make Standard/Full subsystem composition genuinely incremental while preserving the current Full behavior.

## Run

Run a lightweight worker:

```bash
ensemble-runtime --profile worker --host 0.0.0.0 --port 8091
```

Run the existing full application:

```bash
ensemble-runtime --profile full --host 0.0.0.0 --port 8080
```

The profile can also be selected with `ENSEMBLE_PROFILE=worker|standard|full`. The existing `ensemble` and `ensemble-worker` commands remain available for compatibility.

## Upgrade path

A deployment can change its selected profile and install the corresponding package dependencies as profile composition is modularized. Worker identity and HTTP operation contracts are independent of the selected coordinator profile. A profile change does not imply an automatic database migration or enable capabilities for which the host lacks dependencies.

The worker profile imports its application lazily and does not import `ensemble.api`. Standard and Full currently import the existing application. Provider credentials should remain in a central provider gateway initially; no credentials or shared secrets are introduced by the profile model.

## Design constraints

- One codebase, shared contracts, no worker fork.
- Full remains the compatibility baseline until modularization is proven.
- Worker does not initialize full stores, Control Center, GA, Thompson, or learning.
- No message broker, durable queue, or guaranteed-delivery promise.
- No shared-secret or certificate infrastructure.
- Never expose unauthenticated worker or federation endpoints to untrusted networks.
