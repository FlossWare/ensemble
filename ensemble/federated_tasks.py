"""Backward-compatible aliases for the pre-orchestrator federation task module.

New code should use :mod:`ensemble.task_store` and the transport-neutral
TaskRequest, TaskRecord, SqliteTaskStore, and TaskSubmission names.
"""

from .task_store import (
    SqliteTaskStore as FederatedTaskStore,
    TaskRecord as FederatedTaskRecord,
    TaskRequest as FederatedTaskRequest,
    TaskSubmission as FederatedTaskSubmission,
)

__all__ = [
    "FederatedTaskStore",
    "FederatedTaskRecord",
    "FederatedTaskRequest",
    "FederatedTaskSubmission",
]
