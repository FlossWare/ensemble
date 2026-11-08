# Ensemble Worker Runtime

**Status:** The first standalone worker-runtime slice is implemented. It provides a small HTTP process for registering and invoking local capabilities without constructing the full Ensemble application or its persistence stores. Automatic coordinator registration and built-in provider adapters are not included in this slice.

## Purpose

A worker is an execution participant, not a miniature Ensemble installation. Full Ensemble instances remain responsible for orchestration, policy, persistence, federation, and Control Center operations. A worker runtime exposes a bounded set of registered operations and executes direct request/response invocations.

The runtime deliberately does not initialize Ensemble document, vector, index, graph, knowledge, memory, GA, Thompson, or learning stores; the full Ensemble API or Control Center; or a local task database, durable queue, message broker, retry system, or task scheduler.

It uses the existing FastAPI/Pydantic runtime dependencies and a small in-memory operation registry. It is suitable for network-bound provider adapters and small stateless transformations. Heavy indexing, model inference, and long-running durable work belong on full Ensemble instances or separately designed workers.

## Run

Install the base Ensemble package, then start the worker:

```bash
python -m pip install .
ensemble-worker --host 0.0.0.0 --port 8091
```

A worker with no registered operations reports healthy at `/health`, but not ready at `/ready`. A deployment supplies an adapter module that registers the operations it can execute.

Set `ENSEMBLE_WORKER_ADAPTER` to a trusted Python import path in `module:register` form. The module must expose a synchronous function accepting a `WorkerRuntime` instance:

```python
from ensemble.worker_runtime import WorkerRuntime

def register(runtime: WorkerRuntime) -> None:
    runtime.register(
        "provider",
        "models.complete",
        complete_model_request,
        description="Submit a model completion request to the configured provider",
    )
```

The adapter is operator-installed code and executes inside the worker process. Do not point this setting at untrusted modules. Provider credentials remain a separate concern: initially, keep them in a central provider gateway and let worker adapters call that gateway. Do not copy provider credentials into router or Pi images merely to make the worker runtime deployable.

## Configuration

| Variable | Default | Meaning |
|---|---:|---|
| `ENSEMBLE_WORKER_ID` | generated UUID | Stable worker identifier; persist this value if stable identity across restarts is required |
| `ENSEMBLE_WORKER_NAME` | system hostname | Display name |
| `ENSEMBLE_WORKER_HOST` | `0.0.0.0` | HTTP bind address |
| `ENSEMBLE_WORKER_PORT` | `8091` | HTTP port |
| `ENSEMBLE_WORKER_MAX_CONCURRENCY` | `2` | Maximum simultaneous invocations, from 1 to 256 |
| `ENSEMBLE_WORKER_ADAPTER` | unset | Trusted `module:register` adapter import path |

The worker reports CPU count and a Linux cgroup memory limit when available. These are observations, not reservations or guarantees. Capability-specific resource requirements and scheduler placement remain future work.

## HTTP contract

- `GET /health`: process health.
- `GET /ready`: readiness; returns 503 until at least one operation is registered.
- `GET /worker/status`: worker identity, concurrency, counters, labels, resources, and capabilities.
- `GET /worker/capabilities`: versioned operation catalogue.
- `POST /worker/invoke`: bounded direct request/response invocation.

The request includes a caller-generated request ID, capability, operation, JSON payload, and optional absolute Unix deadline in milliseconds. Synchronous handlers run in a thread so they do not block the event loop; asynchronous handlers are awaited directly. Both are subject to the invocation deadline and concurrency limit. Handlers must return a JSON object.

This is intentionally a direct invocation protocol. It does not create a durable task record, guarantee delivery, retry calls, or persist results. Callers own retry and ambiguity policy. A timeout does not prove that an external provider did not receive or process the request.

## Deployment boundary and next integration

The worker runtime currently exposes its own HTTP catalogue and invocation endpoint. It is not yet automatically announced through Ensemble's multicast peer registry, and the full Ensemble service-delegation client does not yet invoke this worker contract. The next integration slice should add explicit worker registration/discovery and a scheduler-facing worker client, while keeping worker execution independent of full Ensemble persistence.

For the R9000, firmware and package availability are the deployment gate. The runtime requires Python 3.12 or newer plus FastAPI, Uvicorn, and Pydantic. Do not assume stock router firmware can host it. Validate the target firmware, Python ABI, writable storage, process supervision, and available RAM before building an image. Raspberry Pi 3B is also a lightweight target; use low concurrency and keep provider credentials centralized.

Federation endpoints and worker endpoints assume an operator-controlled network. Do not expose them to untrusted networks. This runtime adds no authentication or secret-sharing mechanism.
