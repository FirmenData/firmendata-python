"""Asynchronous client for the firmendata public API.

Mirrors :class:`firmendata.client.FirmenData` method for method. The
signatures and semantics are identical — only the transport differs — so the
docstrings there are the reference for both. Retry policy is shared verbatim
via :mod:`firmendata._retry`.
"""

from __future__ import annotations

import asyncio
from typing import Any, Unpack

import httpx

from ._base import DEFAULT_BASE_URL, DEFAULT_TIMEOUT, _BaseClient, _segment
from ._retry import DEFAULT_MAX_RETRIES, backoff_seconds, should_retry
from .errors import APIConnectionError, APITimeoutError, RateLimitError
from .params import SearchFilters, SubscriptionFilters
from .types import (
    AutocompleteResponse,
    CompanyDetail,
    CompanyDocumentDownload,
    CompanyFinancials,
    CompanyHistory,
    SearchResponse,
    ShareholdersReport,
    Subscription,
    SubscriptionCreated,
    SubscriptionEvent,
    SubscriptionEventList,
    SubscriptionEventResendResponse,
    SubscriptionList,
    SubscriptionTestResponse,
    UboReport,
)

__all__ = ["AsyncFirmenData"]


class AsyncFirmenData(_BaseClient):
    """Async client for ``https://api.firmendata.com``.

        >>> async with AsyncFirmenData() as fd:
        ...     hits = await fd.autocomplete("siemens")

    The API key is optional; without one only :meth:`autocomplete` works.
    """

    def __init__(
            self,
            api_key: str | None = None,
            *,
            base_url: str = DEFAULT_BASE_URL,
            timeout: float = DEFAULT_TIMEOUT,
            max_retries: int = DEFAULT_MAX_RETRIES,
            http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            api_key, base_url=base_url, timeout=timeout, max_retries=max_retries,
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
    ) -> Any:
        url = self._url(path)
        query = self._params(params or {})
        attempt = 0

        while True:
            status: int | None = None
            retry_after: float | None = None
            try:
                response = await self._http.request(
                    method, url,
                    params=httpx.QueryParams(query) if query else None,
                    json=json,
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                status = response.status_code
                if 200 <= status < 300:
                    return self._decode(status, response.content, response.headers)

                try:
                    self._decode(status, response.content, response.headers)
                except RateLimitError as exc:
                    retry_after = exc.retry_after
                    error: Exception = exc
                except Exception as exc:  # noqa: BLE001 - re-raised below
                    error = exc
                else:  # pragma: no cover - _decode always raises here
                    raise AssertionError("non-2xx did not raise")

            except (httpx.TimeoutException, httpx.TransportError) as exc:
                error = APITimeoutError(str(exc)) if isinstance(
                    exc, httpx.TimeoutException
                ) else APIConnectionError(str(exc))

            if not should_retry(
                method=method, status_code=status,
                attempt=attempt, max_retries=self.max_retries,
            ):
                raise error

            await asyncio.sleep(backoff_seconds(attempt, retry_after=retry_after))
            attempt += 1

    # -- companies ---------------------------------------------------------

    async def autocomplete(
            self, q: str, *, limit: int = 10, fetch_realtime: bool = False,
    ) -> AutocompleteResponse:
        """Free, keyless company-name suggestions. See the sync client."""
        return await self._request(
            "GET", "/v1/companies/autocomplete",
            params={"q": q, "limit": limit, "fetch_realtime": fetch_realtime},
        )

    async def search(self, **filters: Unpack[SearchFilters]) -> SearchResponse:
        """Advanced register search."""
        return await self._request("GET", "/v1/companies/search", params=dict(filters))

    async def get_company(
            self, eu_id: str, *, fetch_realtime: bool = False,
    ) -> CompanyDetail:
        return await self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}", params={"fetch_realtime": fetch_realtime},
        )

    async def get_financials(self, eu_id: str) -> CompanyFinancials:
        return await self._request("GET", f"/v1/companies/{_segment(eu_id)}/financials")

    async def get_shareholders(
            self, eu_id: str, *, fetch_realtime: bool = False,
    ) -> ShareholdersReport:
        return await self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/shareholders",
            params={"fetch_realtime": fetch_realtime},
        )

    async def get_ubo(self, eu_id: str, *, fetch_realtime: bool = False) -> UboReport:
        return await self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/ubo",
            params={"fetch_realtime": fetch_realtime},
        )

    async def get_history(
            self, eu_id: str, *, fetch_realtime: bool = False,
    ) -> CompanyHistory:
        return await self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/history",
            params={"fetch_realtime": fetch_realtime},
        )

    async def download_document(
            self,
            eu_id: str,
            *,
            file_type: str,
            file_id: str | None = None,
            fetch_realtime: bool = False,
    ) -> CompanyDocumentDownload:
        return await self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/documents/download",
            params={
                "file_type": file_type,
                "file_id": file_id,
                "fetch_realtime": fetch_realtime,
            },
        )

    # -- subscriptions -----------------------------------------------------

    async def list_subscriptions(
            self, **filters: Unpack[SubscriptionFilters],
    ) -> SubscriptionList:
        return await self._request("GET", "/v1/subscriptions", params=dict(filters))

    async def create_subscription(self, **body: Any) -> SubscriptionCreated:
        """Not retried on 5xx — a replay could create a duplicate."""
        return await self._request("POST", "/v1/subscriptions", json=body)

    async def get_subscription(self, subscription_id: str) -> Subscription:
        return await self._request("GET", f"/v1/subscriptions/{_segment(subscription_id)}")

    async def delete_subscription(self, subscription_id: str) -> Any:
        return await self._request("DELETE", f"/v1/subscriptions/{_segment(subscription_id)}")

    async def list_events(
            self, subscription_id: str, *, limit: int | None = None,
            offset: int | None = None,
    ) -> SubscriptionEventList:
        return await self._request(
            "GET", f"/v1/subscriptions/{_segment(subscription_id)}/events",
            params={"limit": limit, "offset": offset},
        )

    async def get_event(self, event_id: str) -> SubscriptionEvent:
        return await self._request("GET", f"/v1/subscriptions/events/{_segment(event_id)}")

    async def resend_event(self, event_id: str) -> SubscriptionEventResendResponse:
        return await self._request(
            "POST", f"/v1/subscriptions/events/{_segment(event_id)}/resend",
        )

    async def test_delivery(self, **body: Any) -> SubscriptionTestResponse:
        return await self._request("POST", "/v1/subscriptions/test", json=body)
