"""Asynchronous client for the firmendata public API.

Mirrors :class:`firmendata.client.FirmenData` method for method. The
signatures and semantics are identical — only the transport differs — so the
docstrings there are the reference for both. Retry policy is shared verbatim
via :mod:`firmendata._retry`.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from typing import Any, Literal, Unpack

import httpx

from ._base import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    AppInfo,
    ResponseHook,
    _BaseClient,
    _financial_params,
    _request_sent,
    _segment,
)
from ._feeds import AsyncFeedIterator
from ._pagination import next_page_params, search_plan
from ._retry import DEFAULT_MAX_RETRIES, backoff_seconds, should_retry
from .errors import RateLimitError, build_error
from .params import (
    CompanyListFilters,
    FeedInsolvenciesFilters,
    FeedNewCompaniesFilters,
    SearchFilters,
    SubscriptionFilters,
)
from .types import (
    AutocompleteResponse,
    CompanyDetail,
    CompanyDocumentDownload,
    CompanyDocumentList,
    CompanyFinancials,
    CompanyHistory,
    CompanyListDetailResponse,
    CompanyListsResponse,
    FeedResponse,
    SearchHit,
    SearchResponse,
    ShareholdersReport,
    Subscription,
    SubscriptionCreated,
    SubscriptionCreateRequest,
    SubscriptionEvent,
    SubscriptionEventList,
    SubscriptionEventResendResponse,
    SubscriptionList,
    SubscriptionTestRequest,
    SubscriptionTestResponse,
    UboReport,
)

__all__ = ["AsyncFirmenData"]


class AsyncFirmenData(_BaseClient):
    """Async client for ``https://api.firmendata.com``.

        >>> async with AsyncFirmenData() as fd:
        ...     hits = await fd.autocomplete("siemens")

    The API key is optional; without one only :meth:`autocomplete` works.
    ``on_response`` may be a plain function or a coroutine function; the
    client awaits the latter.
    """

    def __init__(
            self,
            api_key: str | None = None,
            *,
            base_url: str = DEFAULT_BASE_URL,
            timeout: float = DEFAULT_TIMEOUT,
            max_retries: int = DEFAULT_MAX_RETRIES,
            http_client: httpx.AsyncClient | None = None,
            app_info: AppInfo | None = None,
            on_response: ResponseHook | None = None,
    ) -> None:
        super().__init__(
            api_key, base_url=base_url, timeout=timeout, max_retries=max_retries,
            app_info=app_info, on_response=on_response,
        )
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=timeout)

    # -- lifecycle ---------------------------------------------------------

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def __aenter__(self) -> AsyncFirmenData:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- transport ---------------------------------------------------------

    async def _request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            json: dict[str, Any] | None = None,
            live: bool = False,
            timeout: float | None = None,
    ) -> Any:
        url = self._url(path)
        query = self._params(params or {})
        live = live or (params or {}).get("fetch_realtime") is True
        effective_timeout = self._timeout_for(live, timeout)
        attempt = 0

        while True:
            status: int | None = None
            retry_after: float | None = None
            sent = True
            try:
                response = await self._http.request(
                    method, url,
                    params=httpx.QueryParams(query) if query else None,
                    json=json,
                    headers=self._headers(),
                    timeout=effective_timeout,
                )
            except httpx.TransportError as exc:
                sent = _request_sent(exc)
                error: Exception = self._transport_error(
                    exc, live=live, sent=sent, timeout=effective_timeout,
                )
            else:
                status = response.status_code
                body = self._parse(response.content)
                if self.on_response is not None:
                    result = self.on_response(self._response_info(status, response.headers, body))
                    if inspect.isawaitable(result):
                        await result
                if 200 <= status < 300:
                    return body
                error = build_error(status, body, response.headers)
                if isinstance(error, RateLimitError):
                    retry_after = error.retry_after

            if not should_retry(
                method=method, status_code=status,
                attempt=attempt, max_retries=self.max_retries,
                retry_after=retry_after, live=live, request_sent=sent,
            ):
                raise error

            await asyncio.sleep(backoff_seconds(attempt, retry_after=retry_after))
            attempt += 1

    # -- companies ---------------------------------------------------------

    async def autocomplete(
            self, q: str, *, limit: int = 10, fetch_realtime: bool = False,
            country: Literal["DE", "CH", "AT"] | None = None, timeout: float | None = None,
    ) -> AutocompleteResponse:
        """Free, keyless company-name suggestions. See the sync client."""
        return await self._request(
            "GET", "/v1/companies/autocomplete",
            params={
                "q": q, "limit": limit, "fetch_realtime": fetch_realtime, "country": country,
            },
            timeout=timeout,
        )

    async def search(
            self, *, timeout: float | None = None, **filters: Unpack[SearchFilters],
    ) -> SearchResponse:
        """Advanced register search."""
        return await self._request(
            "GET", "/v1/companies/search", params=dict(filters), timeout=timeout,
        )

    async def iter_search(
            self, *, max_results: int | None = None, **filters: Unpack[SearchFilters],
    ) -> AsyncIterator[SearchHit]:
        """Use ``async for`` to page through every hit. See the sync client."""
        cap, params, limit = search_plan(max_results, filters)
        if cap <= 0:
            return
        yielded = 0
        while True:
            page: SearchResponse = await self._request(
                "GET", "/v1/companies/search", params=params,
            )
            for hit in page.get("data") or []:
                yield hit
                yielded += 1
                if yielded >= cap:
                    return
            next_params = next_page_params(page, params, limit)
            if next_params is None:
                return
            params = next_params

    async def get_company(
            self, eu_id: str, *, fetch_realtime: bool = False, timeout: float | None = None,
    ) -> CompanyDetail:
        return await self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}",
            params={"fetch_realtime": fetch_realtime}, timeout=timeout,
        )

    async def get_financials(
            self,
            eu_id: str,
            *,
            details: bool | None = None,
            include_line_items: bool | None = None,
            years: int | None = None,
    ) -> CompanyFinancials:
        """Multi-year financial statements. See the sync client."""
        return await self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/financials",
            params=_financial_params(details, include_line_items, years),
        )

    async def get_shareholders(
            self, eu_id: str, *, fetch_realtime: bool = False, timeout: float | None = None,
    ) -> ShareholdersReport:
        return await self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/shareholders",
            params={"fetch_realtime": fetch_realtime}, timeout=timeout,
        )

    async def get_ubo(
            self, eu_id: str, *, fetch_realtime: bool = False, timeout: float | None = None,
    ) -> UboReport:
        return await self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/ubo",
            params={"fetch_realtime": fetch_realtime}, timeout=timeout,
        )

    async def get_history(
            self, eu_id: str, *, fetch_realtime: bool = False, timeout: float | None = None,
    ) -> CompanyHistory:
        return await self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/history",
            params={"fetch_realtime": fetch_realtime}, timeout=timeout,
        )

    async def list_documents(
            self, eu_id: str, *, timeout: float | None = None,
    ) -> CompanyDocumentList:
        """Check the registry live and list documents. See the sync client."""
        return await self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/documents", live=True, timeout=timeout,
        )

    async def download_document(
            self,
            eu_id: str,
            *,
            file_type: str,
            file_id: str | None = None,
            document_id: str | None = None,
            fetch_realtime: bool = False,
            timeout: float | None = None,
    ) -> CompanyDocumentDownload:
        return await self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/documents/download",
            params={
                "file_type": file_type,
                "file_id": file_id,
                "document_id": document_id,
                "fetch_realtime": fetch_realtime,
            },
            live=True,
            timeout=timeout,
        )

    # -- event feeds (Beta) ------------------------------------------------

    async def feed_new_companies(
            self, **filters: Unpack[FeedNewCompaniesFilters],
    ) -> FeedResponse:
        """One page of newly registered companies (Beta). See the sync client."""
        return await self._request("GET", "/v1/feeds/new-companies", params=dict(filters))

    async def feed_insolvencies(
            self, **filters: Unpack[FeedInsolvenciesFilters],
    ) -> FeedResponse:
        """One page of insolvency notices (Beta). See the sync client."""
        return await self._request("GET", "/v1/feeds/insolvencies", params=dict(filters))

    def iter_feed_new_companies(
            self, **filters: Unpack[FeedNewCompaniesFilters],
    ) -> AsyncFeedIterator:
        """Use ``async for`` to drain events, then persist the iterator's ``next_cursor``.

        See the sync client for checkpoint and deduplication semantics.
        """
        return AsyncFeedIterator(lambda params: self.feed_new_companies(**params), dict(filters))

    def iter_feed_insolvencies(
            self, **filters: Unpack[FeedInsolvenciesFilters],
    ) -> AsyncFeedIterator:
        """Async iteration over insolvency notices. See :meth:`iter_feed_new_companies`."""
        return AsyncFeedIterator(lambda params: self.feed_insolvencies(**params), dict(filters))

    # -- lists -------------------------------------------------------------

    async def get_lists(self) -> CompanyListsResponse:
        """Your company lists, newest first. Free. See the sync client."""
        return await self._request("GET", "/v1/lists")

    async def get_list(
            self, list_id: int, **params: Unpack[CompanyListFilters],
    ) -> CompanyListDetailResponse:
        """One page of companies from one of your lists. Free. See the sync client."""
        return await self._request(
            "GET", f"/v1/lists/{_segment(str(list_id))}", params=dict(params),
        )

    # -- subscriptions -----------------------------------------------------

    async def list_subscriptions(
            self, **filters: Unpack[SubscriptionFilters],
    ) -> SubscriptionList:
        return await self._request("GET", "/v1/subscriptions", params=dict(filters))

    async def create_subscription(
            self, **body: Unpack[SubscriptionCreateRequest],
    ) -> SubscriptionCreated:
        """Not retried on 5xx — a replay could create a duplicate."""
        return await self._request("POST", "/v1/subscriptions", json=dict(body))

    async def get_subscription(self, subscription_id: str) -> Subscription:
        return await self._request("GET", f"/v1/subscriptions/{_segment(subscription_id)}")

    async def delete_subscription(self, subscription_id: str) -> Any:
        return await self._request("DELETE", f"/v1/subscriptions/{_segment(subscription_id)}")

    async def list_events(
            self, subscription_id: str, *, limit: int | None = None,
            cursor: str | None = None, offset: int | None = None,
    ) -> SubscriptionEventList:
        return await self._request(
            "GET", f"/v1/subscriptions/{_segment(subscription_id)}/events",
            params={"limit": limit, "cursor": cursor, "offset": offset},
        )

    async def get_event(self, event_id: str) -> SubscriptionEvent:
        return await self._request("GET", f"/v1/subscriptions/events/{_segment(event_id)}")

    async def resend_event(self, event_id: str) -> SubscriptionEventResendResponse:
        return await self._request(
            "POST", f"/v1/subscriptions/events/{_segment(event_id)}/resend",
        )

    async def test_delivery(
            self, **body: Unpack[SubscriptionTestRequest],
    ) -> SubscriptionTestResponse:
        return await self._request("POST", "/v1/subscriptions/test", json=dict(body))
