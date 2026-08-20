"""Synchronous client for the firmendata public API."""

from __future__ import annotations

import time
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

__all__ = ["FirmenData"]


class FirmenData(_BaseClient):
    """Client for ``https://api.firmendata.com``.

    The API key is optional. Without one you can still call
    :meth:`autocomplete`, which is free and needs no signup; every other
    method will raise :class:`~firmendata.errors.AuthenticationError`.

        >>> from firmendata import FirmenData
        >>> FirmenData().autocomplete("siemens")          # no key needed
        >>> FirmenData(api_key="firmendata_live_...").search(city=["Berlin"])

    Usable as a context manager, which closes the underlying connection pool::

        with FirmenData(api_key=key) as fd:
            company = fd.get_company(eu_id)

    Retries are automatic and conservative: 429s always (the server rejects
    them before doing any work), 5xx and connection failures only for
    idempotent methods. See :mod:`firmendata._retry`.
    """

    def __init__(
            self,
            api_key: str | None = None,
            *,
            base_url: str = DEFAULT_BASE_URL,
            timeout: float = DEFAULT_TIMEOUT,
            max_retries: int = DEFAULT_MAX_RETRIES,
            http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            api_key, base_url=base_url, timeout=timeout, max_retries=max_retries,
        )
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> FirmenData:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transport ---------------------------------------------------------

    def _request(
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
                response = self._http.request(
                    method, url,
                    params=httpx.QueryParams(query) if query else None,
                    json=json,
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                status = response.status_code
                if 200 <= status < 300:
                    return self._decode(status, response.content, response.headers)

                # Build the error now so a 429's Retry-After informs the wait.
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

            time.sleep(backoff_seconds(attempt, retry_after=retry_after))
            attempt += 1

    # -- companies ---------------------------------------------------------

    def autocomplete(
            self,
            q: str,
            *,
            limit: int = 10,
            fetch_realtime: bool = False,
    ) -> AutocompleteResponse:
        """Company-name suggestions for a search box.

        Free and callable without an API key — the one endpoint that is.
        Keyless calls are rate limited by address; supplying a key raises
        that substantially. The exact keyless limits are not published and
        may be tightened without notice — honour ``Retry-After`` on a 429
        (this client does).

        ``fetch_realtime`` searches the German registers live so a company
        founded days ago is findable immediately. It **requires an API key**
        and costs credits; keyless calls that set it raise
        :class:`~firmendata.errors.AuthenticationError`.
        """
        return self._request(
            "GET", "/v1/companies/autocomplete",
            params={"q": q, "limit": limit, "fetch_realtime": fetch_realtime},
        )

    def search(self, **filters: Unpack[SearchFilters]) -> SearchResponse:
        """Advanced search over the German commercial register.

        Every filter is optional and they combine with AND. Array filters
        take a list and combine with OR within themselves::

            fd.search(city=["Berlin", "Hamburg"], revenue_min=1_000_000)

        Paginate by passing ``pagination.next_cursor`` back as ``cursor``.
        """
        return self._request("GET", "/v1/companies/search", params=dict(filters))

    def get_company(self, eu_id: str, *, fetch_realtime: bool = False) -> CompanyDetail:
        """Full profile for one company."""
        return self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}",
            params={"fetch_realtime": fetch_realtime},
        )

    def get_financials(self, eu_id: str) -> CompanyFinancials:
        """Multi-year financial statements."""
        return self._request("GET", f"/v1/companies/{_segment(eu_id)}/financials")

    def get_shareholders(
            self, eu_id: str, *, fetch_realtime: bool = False,
    ) -> ShareholdersReport:
        """Cap table from the most recent Gesellschafterliste (GmbH/UG)."""
        return self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/shareholders",
            params={"fetch_realtime": fetch_realtime},
        )

    def get_ubo(self, eu_id: str, *, fetch_realtime: bool = False) -> UboReport:
        """Ultimate beneficial owners, resolved through ownership chains."""
        return self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/ubo",
            params={"fetch_realtime": fetch_realtime},
        )

    def get_history(self, eu_id: str, *, fetch_realtime: bool = False) -> CompanyHistory:
        """Chronological register history."""
        return self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/history",
            params={"fetch_realtime": fetch_realtime},
        )

    def download_document(
            self,
            eu_id: str,
            *,
            file_type: str,
            file_id: str | None = None,
            fetch_realtime: bool = False,
    ) -> CompanyDocumentDownload:
        """Presigned download URL for a register document."""
        return self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/documents/download",
            params={
                "file_type": file_type,
                "file_id": file_id,
                "fetch_realtime": fetch_realtime,
            },
        )

    # -- subscriptions -----------------------------------------------------

    def list_subscriptions(self, **filters: Unpack[SubscriptionFilters]) -> SubscriptionList:
        """List change subscriptions."""
        return self._request("GET", "/v1/subscriptions", params=dict(filters))

    def create_subscription(self, **body: Any) -> SubscriptionCreated:
        """Create a change subscription.

        Not retried on 5xx: a create that times out may already have been
        applied, and a blind replay would produce a duplicate.
        """
        return self._request("POST", "/v1/subscriptions", json=body)

    def get_subscription(self, subscription_id: str) -> Subscription:
        return self._request("GET", f"/v1/subscriptions/{_segment(subscription_id)}")

    def delete_subscription(self, subscription_id: str) -> Any:
        return self._request("DELETE", f"/v1/subscriptions/{_segment(subscription_id)}")

    def list_events(
            self, subscription_id: str, *, limit: int | None = None,
            offset: int | None = None,
    ) -> SubscriptionEventList:
        return self._request(
            "GET", f"/v1/subscriptions/{_segment(subscription_id)}/events",
            params={"limit": limit, "offset": offset},
        )

    def get_event(self, event_id: str) -> SubscriptionEvent:
        return self._request("GET", f"/v1/subscriptions/events/{_segment(event_id)}")

    def resend_event(self, event_id: str) -> SubscriptionEventResendResponse:
        return self._request("POST", f"/v1/subscriptions/events/{_segment(event_id)}/resend")

    def test_delivery(self, **body: Any) -> SubscriptionTestResponse:
        """Send a synthetic event to a webhook URL to verify delivery."""
        return self._request("POST", "/v1/subscriptions/test", json=body)
