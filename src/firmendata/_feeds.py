"""Feed iterators with a checkpoint for the last fully consumed page."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Generator, Iterator
from typing import Any

from .types import FeedEvent, FeedResponse


class FeedIterator(Iterator[FeedEvent]):
    """Drain available feed events, deduplicating IDs within this iteration.

    Persist ``next_cursor`` after exhausting the iterator, then poll again
    later with that cursor and the same filters. It starts at the supplied
    cursor (or ``None``) and advances only after each page is fully consumed.
    Stopping partway through a page leaves its starting cursor for replay.
    Deduplicate IDs in persistent storage across separate polls.
    """

    def __init__(
            self,
            fetch_page: Callable[[dict[str, Any]], FeedResponse],
            filters: dict[str, Any],
    ) -> None:
        self._next_cursor: str | None = filters.get("cursor")
        self._events = self._iterate(fetch_page, dict(filters))

    @property
    def next_cursor(self) -> str | None:
        """Resume cursor for the last fully consumed page, including empty pages."""
        return self._next_cursor

    def __iter__(self) -> FeedIterator:
        return self

    def __next__(self) -> FeedEvent:
        return next(self._events)

    def close(self) -> None:
        """Stop iteration without advancing past unconsumed events."""
        self._events.close()

    def _iterate(
            self,
            fetch_page: Callable[[dict[str, Any]], FeedResponse],
            params: dict[str, Any],
    ) -> Generator[FeedEvent, None, None]:
        seen: set[str] = set()
        if self._next_cursor is not None:
            params.pop("since", None)
        while True:
            page = fetch_page(params)
            for event in page.get("data", []):
                if event["id"] not in seen:
                    seen.add(event["id"])
                    yield event
            self._next_cursor = page["next_cursor"]
            if not page["has_more"]:
                return
            params["cursor"] = self._next_cursor
            params.pop("since", None)


class AsyncFeedIterator(AsyncIterator[FeedEvent]):
    """Async counterpart of :class:`FeedIterator`, with the same checkpoint semantics."""

    def __init__(
            self,
            fetch_page: Callable[[dict[str, Any]], Awaitable[FeedResponse]],
            filters: dict[str, Any],
    ) -> None:
        self._next_cursor: str | None = filters.get("cursor")
        self._events = self._iterate(fetch_page, dict(filters))

    @property
    def next_cursor(self) -> str | None:
        """Resume cursor for the last fully consumed page, including empty pages."""
        return self._next_cursor

    def __aiter__(self) -> AsyncFeedIterator:
        return self

    async def __anext__(self) -> FeedEvent:
        return await anext(self._events)

    async def aclose(self) -> None:
        """Stop iteration without advancing past unconsumed events."""
        await self._events.aclose()

    async def _iterate(
            self,
            fetch_page: Callable[[dict[str, Any]], Awaitable[FeedResponse]],
            params: dict[str, Any],
    ) -> AsyncGenerator[FeedEvent, None]:
        seen: set[str] = set()
        if self._next_cursor is not None:
            params.pop("since", None)
        while True:
            page = await fetch_page(params)
            for event in page.get("data", []):
                if event["id"] not in seen:
                    seen.add(event["id"])
                    yield event
            self._next_cursor = page["next_cursor"]
            if not page["has_more"]:
                return
            params["cursor"] = self._next_cursor
            params.pop("since", None)
