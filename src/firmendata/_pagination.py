"""Search pagination, shared by the sync and async ``iter_search``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .types import SearchResponse

#: ``/companies/search`` serves at most this many results for one query.
SEARCH_RESULT_CAP = 10_000
_SEARCH_PAGE_MAX = 50


def search_plan(
        max_results: int | None, filters: Mapping[str, Any],
) -> tuple[int, dict[str, Any], int | None]:
    """The result cap, the first page's parameters and the page size to keep.

    Without an explicit ``limit``, a small ``max_results`` asks for just that
    many hits; otherwise the server default (50) applies.
    """
    cap = SEARCH_RESULT_CAP if max_results is None else min(max_results, SEARCH_RESULT_CAP)
    params = dict(filters)
    limit = params.get("limit")
    if limit is None and max_results is not None and cap > 0:
        limit = min(_SEARCH_PAGE_MAX, cap)
        params["limit"] = limit
    return cap, params, limit


def next_page_params(
        page: SearchResponse, previous: Mapping[str, Any], limit: int | None,
) -> dict[str, Any] | None:
    """Parameters for the next page — the cursor carries the filters — or ``None``."""
    pagination = page.get("pagination") or {}
    cursor = pagination.get("next_cursor")
    if not pagination.get("has_more") or not cursor or cursor == previous.get("cursor"):
        return None
    params: dict[str, Any] = {"cursor": cursor}
    if limit is not None:
        params["limit"] = limit
    return params
