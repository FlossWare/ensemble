from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Generic, Protocol, TypeVar

TContext = TypeVar("TContext")
TValue = TypeVar("TValue")


@dataclass(frozen=True, slots=True)
class DeletionContext:
    """Language-neutral lifecycle intent carried into a deletion strategy."""

    source_id: str
    actor: str | None = None
    reason: str | None = None
    requested_at: datetime | None = None
    retain_until: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id or len(self.source_id) > 1024:
            raise ValueError("source_id must contain 1-1024 characters")
        if self.requested_at is not None and self.requested_at.tzinfo is None:
            raise ValueError("requested_at must be timezone-aware")
        if self.retain_until is not None and self.retain_until.tzinfo is None:
            raise ValueError("retain_until must be timezone-aware")


@dataclass(frozen=True, slots=True)
class DeletionResult(Generic[TValue]):
    source_id: str
    state: str
    value: TValue | None = None
    retryable: bool = False
    detail: str | None = None
    processed_count: int | None = None


class DeletionStrategy(Protocol[TContext, TValue]):
    async def execute(self, context: TContext) -> DeletionResult[TValue]: ...


class ReclamationQueue(Protocol):
    async def enqueue(self, source_id: str, *, actor: str | None = None, reason: str | None = None, requested_at: datetime | None = None, retain_until: datetime | None = None) -> dict[str, Any]: ...


class DeferredReclamationStrategy:
    """Persist deletion work for the background reclamation worker."""

    def __init__(self, queue: ReclamationQueue):
        self._queue = queue

    async def execute(self, context: DeletionContext) -> DeletionResult[dict[str, Any]]:
        job = await self._queue.enqueue(context.source_id, actor=context.actor, reason=context.reason, requested_at=context.requested_at, retain_until=context.retain_until)
        return DeletionResult(
            source_id=context.source_id,
            state="queued",
            value=job,
            retryable=True,
        )


class ImmediateReclamationStrategy:
    """Queue work durably, then ask the configured worker to drain due work."""

    def __init__(
        self,
        queue: ReclamationQueue,
        drain: Callable[[], Awaitable[int]],
    ):
        self._queue = queue
        self._drain = drain

    async def execute(self, context: DeletionContext) -> DeletionResult[dict[str, Any]]:
        job = await self._queue.enqueue(context.source_id, actor=context.actor, reason=context.reason, requested_at=context.requested_at, retain_until=context.retain_until)
        processed = await self._drain()
        return DeletionResult(
            source_id=context.source_id,
            state="dispatched",
            value=job,
            retryable=True,
            processed_count=processed,
        )


class DeletionStrategyDecorator(Generic[TContext, TValue]):
    """Base decorator preserving the wrapped strategy's context/result types."""

    def __init__(self, inner: DeletionStrategy[TContext, TValue]):
        self._inner = inner

    async def execute(self, context: TContext) -> DeletionResult[TValue]:
        return await self._inner.execute(context)


class RetentionDeletionDecorator(DeletionStrategyDecorator[DeletionContext, TValue], Generic[TValue]):
    """Persist retention deadlines so reclamation cannot run before eligibility."""

    def __init__(self, inner: DeletionStrategy[DeletionContext, TValue]):
        super().__init__(inner)

    async def execute(self, context: DeletionContext) -> DeletionResult[TValue]:
        # Retention is enforced by the durable queue's available_at timestamp.
        # Returning early here would lose the scheduled work on process restart.
        return await super().execute(context)


class RetryingDeletionDecorator(DeletionStrategyDecorator[TContext, TValue], Generic[TContext, TValue]):
    """Retry transient strategy failures without changing the strategy contract."""

    def __init__(
        self,
        inner: DeletionStrategy[TContext, TValue],
        attempts: int = 3,
        delay_seconds: Callable[[int], float] = lambda attempt: min(2 ** (attempt - 1), 30),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retry_if: Callable[[Exception], bool] = lambda _error: True,
    ):
        super().__init__(inner)
        if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 10:
            raise ValueError("attempts must be between 1 and 10")
        self._attempts = attempts
        self._delay_seconds = delay_seconds
        self._sleep = sleep
        self._retry_if = retry_if

    async def execute(self, context: TContext) -> DeletionResult[TValue]:
        for attempt in range(1, self._attempts + 1):
            try:
                return await super().execute(context)
            except Exception as error:
                if attempt >= self._attempts or not self._retry_if(error):
                    raise
                await self._sleep(max(0.0, self._delay_seconds(attempt)))
        raise RuntimeError("unreachable deletion retry state")


class AuditingDeletionDecorator(DeletionStrategyDecorator[TContext, TValue], Generic[TContext, TValue]):
    """Record the result of a deletion strategy through an injected audit sink."""

    def __init__(
        self,
        inner: DeletionStrategy[TContext, TValue],
        audit: Callable[[TContext, DeletionResult[TValue]], Awaitable[None]],
    ):
        super().__init__(inner)
        self._audit = audit

    async def execute(self, context: TContext) -> DeletionResult[TValue]:
        result = await super().execute(context)
        await self._audit(context, result)
        return result
