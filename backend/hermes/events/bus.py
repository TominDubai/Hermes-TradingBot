from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TypeVar

from hermes.events.types import BaseEvent

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseEvent)
Handler = Callable[[BaseEvent], Awaitable[None]]


class EventBus:
    """
    Lightweight in-process async pub/sub bus.

    Usage:
        bus = EventBus()

        @bus.subscribe(SignalDetected)
        async def handle(event: SignalDetected): ...

        await bus.publish(SignalDetected(...))

    Designed to be swap-compatible with FastStream/Redis Streams later
    without changing handler signatures.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._queue: asyncio.Queue[BaseEvent] = asyncio.Queue()
        self._running = False

    def subscribe(self, event_type: type[T]) -> Callable[[Handler], Handler]:
        def decorator(fn: Handler) -> Handler:
            self._handlers[event_type.__name__].append(fn)
            logger.debug("Subscribed %s to %s", fn.__name__, event_type.__name__)
            return fn
        return decorator

    async def publish(self, event: BaseEvent) -> None:
        await self._queue.put(event)

    async def _dispatch(self, event: BaseEvent) -> None:
        handlers = self._handlers.get(type(event).__name__, [])
        if not handlers:
            logger.debug("No handlers for %s", type(event).__name__)
            return
        await asyncio.gather(
            *(h(event) for h in handlers),
            return_exceptions=True,
        )

    async def run(self) -> None:
        self._running = True
        logger.info("EventBus started")
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._dispatch(event)
                self._queue.task_done()
            except TimeoutError:
                continue
            except Exception:
                logger.exception("EventBus dispatch error")

    async def stop(self) -> None:
        self._running = False
        logger.info("EventBus stopped")


# Singleton — imported and used everywhere
bus = EventBus()
