"""Synchronous client for the firmendata public API."""

from __future__ import annotations

import time
from collections.abc import Iterator
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
from ._feeds import FeedIterator
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
    them before doing any work) unless ``Retry-After`` asks for more than 20
    seconds, 5xx and connection failures only for idempotent methods, and live
    calls (``fetch_realtime=True``, document listings and downloads) never once
    they may have reached the server. See :mod:`firmendata._retry`.

    ``timeout`` is per attempt; live calls wait at least
    :data:`~firmendata.REALTIME_TIMEOUT` (150 s) unless the call passes its
    own ``timeout``. ``app_info={"name": ..., "version": ..., "url": ...}``
    identifies your integration in the ``User-Agent``. ``on_response`` is
    called with a :class:`~firmendata.ResponseInfo` for every HTTP response.
    """

    def __init__(
            self,
            api_key: str | None = None,
            *,
            base_url: str = DEFAULT_BASE_URL,
            timeout: float = DEFAULT_TIMEOUT,
            max_retries: int = DEFAULT_MAX_RETRIES,
            http_client: httpx.Client | None = None,
            app_info: AppInfo | None = None,
            on_response: ResponseHook | None = None,
    ) -> None:
        super().__init__(
            api_key, base_url=base_url, timeout=timeout, max_retries=max_retries,
            app_info=app_info, on_response=on_response,
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
                response = self._http.request(
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
                # Outside the try, so an exception from the hook reaches the caller as-is.
                if self.on_response is not None:
                    self.on_response(self._response_info(status, response.headers, body))
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

            time.sleep(backoff_seconds(attempt, retry_after=retry_after))
            attempt += 1

    # -- companies ---------------------------------------------------------

    def autocomplete(
            self,
            q: str,
            *,
            limit: int = 10,
            fetch_realtime: bool = False,
            country: Literal["DE", "CH", "AT"] | None = None,
            timeout: float | None = None,
    ) -> AutocompleteResponse:
        """Company-name suggestions for a search box.

        ``country`` restricts suggestions to DE, CH or AT; omit it to search
        all countries.

        Free and callable without an API key — the one endpoint that is.
        Keyless calls are rate limited by address; supplying a key raises
        that substantially. The exact keyless limits are not published and
        may be tightened without notice — honour ``Retry-After`` on a 429
        (this client does, up to 20 seconds; a longer wait raises
        :class:`~firmendata.errors.RateLimitError` at once).

        ``fetch_realtime`` searches the German registers live so a company
        founded days ago is findable immediately. It **requires an API key**
        and costs credits; keyless calls that set it raise
        :class:`~firmendata.errors.AuthenticationError`.
        """
        return self._request(
            "GET", "/v1/companies/autocomplete",
            params={
                "q": q, "limit": limit, "fetch_realtime": fetch_realtime, "country": country,
            },
            timeout=timeout,
        )

    def search(
            self, *, timeout: float | None = None, **filters: Unpack[SearchFilters],
    ) -> SearchResponse:
        """Advanced search over the German, Swiss and Austrian commercial registers.

        Every filter is optional. Different filters combine with AND;
        ``city``, ``bundesland`` and ``canton`` form one OR location filter.
        Array filters take a list and combine with OR within themselves::

            fd.search(city=["Berlin", "Hamburg"], revenue_min=1_000_000)

        Paginate by passing ``pagination.next_cursor`` back as ``cursor``,
        unchanged — cursors are signed, and an edited one is rejected. Unknown
        filter names and inverted ranges raise ``ValidationError``.
        :meth:`iter_search` pages for you.
        """
        return self._request(
            "GET", "/v1/companies/search", params=dict(filters), timeout=timeout,
        )

    def iter_search(
            self, *, max_results: int | None = None, **filters: Unpack[SearchFilters],
    ) -> Iterator[SearchHit]:
        """Yield every hit of a search, page by page, following ``next_cursor``.

        After the first page only the cursor (and ``limit``) is sent — the
        cursor carries the filters. Stops when ``has_more`` is false, after
        ``max_results`` hits, or at the API's 10,000-result cap. Each page (up
        to 50 hits) is one call, billed like :meth:`search`; stop iterating
        to stop paying for pages you do not need.
        """
        cap, params, limit = search_plan(max_results, filters)
        if cap <= 0:
            return
        yielded = 0
        while True:
            page: SearchResponse = self._request("GET", "/v1/companies/search", params=params)
            for hit in page.get("data") or []:
                yield hit
                yielded += 1
                if yielded >= cap:
                    return
            next_params = next_page_params(page, params, limit)
            if next_params is None:
                return
            params = next_params

    def get_company(
            self, eu_id: str, *, fetch_realtime: bool = False, timeout: float | None = None,
    ) -> CompanyDetail:
        """Full profile for one company."""
        return self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}",
            params={"fetch_realtime": fetch_realtime}, timeout=timeout,
        )

    def get_financials(
            self,
            eu_id: str,
            *,
            details: bool | None = None,
            include_line_items: bool | None = None,
            years: int | None = None,
    ) -> CompanyFinancials:
        """Multi-year financial statements.

        By default the response carries every filed P&L, assets and
        liabilities-and-equity row (``history.profit_and_loss``,
        ``history.assets``, ``history.liabilities_and_equity``) — often
        100 KB+ for groups. Pass ``details=False`` for the lean response:
        those lists stay empty, and each year keeps its top-level
        ``balance_sheet`` and ``income_statement`` positions in
        ``history.metrics``. Both cost the same. ``years`` keeps the N most
        recent fiscal years in every per-year array.

        ``include_line_items`` is deprecated (it emits a
        :class:`DeprecationWarning`) and is sent as ``details``; an explicit
        ``details`` wins.
        """
        return self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/financials",
            params=_financial_params(details, include_line_items, years),
        )

    def get_shareholders(
            self, eu_id: str, *, fetch_realtime: bool = False, timeout: float | None = None,
    ) -> ShareholdersReport:
        """Cap table from the most recent Gesellschafterliste (GmbH/UG)."""
        return self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/shareholders",
            params={"fetch_realtime": fetch_realtime}, timeout=timeout,
        )

    def get_ubo(
            self, eu_id: str, *, fetch_realtime: bool = False, timeout: float | None = None,
    ) -> UboReport:
        """Ultimate beneficial owners, resolved through ownership chains."""
        return self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/ubo",
            params={"fetch_realtime": fetch_realtime}, timeout=timeout,
        )

    def get_history(
            self, eu_id: str, *, fetch_realtime: bool = False, timeout: float | None = None,
    ) -> CompanyHistory:
        """Chronological register history."""
        return self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/history",
            params={"fetch_realtime": fetch_realtime}, timeout=timeout,
        )

    def list_documents(
            self, eu_id: str, *, timeout: float | None = None,
    ) -> CompanyDocumentList:
        """Check the registry live and list documents, including older versions.

        A live, billed call: never retried once it may have reached the server.
        """
        return self._request(
            "GET", f"/v1/companies/{_segment(eu_id)}/documents", live=True, timeout=timeout,
        )

    def download_document(
            self,
            eu_id: str,
            *,
            file_type: str,
            file_id: str | None = None,
            document_id: str | None = None,
            fetch_realtime: bool = False,
            timeout: float | None = None,
    ) -> CompanyDocumentDownload:
        """Presigned download URL for a register document.

        Pass ``document_id`` from :meth:`list_documents` for a specific
        version, with its matching ``file_type``. It cannot be combined with
        ``file_id`` or ``fetch_realtime=True``. Omit it for the latest version.

        A live, billed call: never retried once it may have reached the
        server, with a timeout of at least 150 s unless ``timeout`` is set.
        """
        return self._request(
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

    def feed_new_companies(self, **filters: Unpack[FeedNewCompaniesFilters]) -> FeedResponse:
        """One page of newly registered companies (Beta); requires an API key.

        ``since`` is an ISO datetime for the first call only, at most seven
        days back (defaults to the last day). ``limit`` is 1–100, default 50.
        Persist ``next_cursor`` even after an empty page and pass it back
        unchanged. Page immediately while ``has_more`` is true, otherwise
        poll later. Deduplicate on event ``id``. A page with events costs
        one credit; empty pages are free. Positions older than seven days
        expire; restart with a recent ``since``.
        """
        return self._request("GET", "/v1/feeds/new-companies", params=dict(filters))

    def feed_insolvencies(self, **filters: Unpack[FeedInsolvenciesFilters]) -> FeedResponse:
        """One page of insolvency notices (Beta).

        See :meth:`feed_new_companies` for polling, cursors and pricing.
        ``country`` defaults to DE; Schuldenruf must be selected by name
        using ``category``.
        """
        return self._request("GET", "/v1/feeds/insolvencies", params=dict(filters))

    def iter_feed_new_companies(
            self, **filters: Unpack[FeedNewCompaniesFilters],
    ) -> FeedIterator:
        """Yield available new-company events; persist the iterator's ``next_cursor``.

        Stops when ``has_more`` is false. The cursor advances after each
        fully consumed page. IDs are deduplicated within this iteration;
        persist IDs separately to deduplicate across polls.
        """
        return FeedIterator(lambda params: self.feed_new_companies(**params), dict(filters))

    def iter_feed_insolvencies(
            self, **filters: Unpack[FeedInsolvenciesFilters],
    ) -> FeedIterator:
        """Yield available insolvency notices. See :meth:`iter_feed_new_companies`."""
        return FeedIterator(lambda params: self.feed_insolvencies(**params), dict(filters))

    # -- lists -------------------------------------------------------------

    def get_lists(self) -> CompanyListsResponse:
        """Your company lists, newest first. Free.

        Each entry carries the list's ``list_id``, ``name``, ``company_count``
        and alert setting. Lists are created and edited in the web app.
        """
        return self._request("GET", "/v1/lists")

    def get_list(
            self, list_id: int, **params: Unpack[CompanyListFilters],
    ) -> CompanyListDetailResponse:
        """One page of companies from one of your lists. Free.

        Rows have the same shape as search hits; ``sort`` orders the whole
        list before the page is cut. Paginate with ``pagination.next_cursor``
        as ``cursor``. An unknown list id, or one that belongs to someone
        else, raises :class:`~firmendata.errors.NotFoundError`.
        """
        return self._request("GET", f"/v1/lists/{_segment(str(list_id))}", params=dict(params))

    # -- subscriptions -----------------------------------------------------

    def list_subscriptions(self, **filters: Unpack[SubscriptionFilters]) -> SubscriptionList:
        """List change subscriptions."""
        return self._request("GET", "/v1/subscriptions", params=dict(filters))

    def create_subscription(
            self, **body: Unpack[SubscriptionCreateRequest],
    ) -> SubscriptionCreated:
        """Create a change subscription.

        Not retried on 5xx: a create that times out may already have been
        applied, and a blind replay would produce a duplicate.
        """
        return self._request("POST", "/v1/subscriptions", json=dict(body))

    def get_subscription(self, subscription_id: str) -> Subscription:
        return self._request("GET", f"/v1/subscriptions/{_segment(subscription_id)}")

    def delete_subscription(self, subscription_id: str) -> Any:
        return self._request("DELETE", f"/v1/subscriptions/{_segment(subscription_id)}")

    def list_events(
            self, subscription_id: str, *, limit: int | None = None,
            cursor: str | None = None, offset: int | None = None,
    ) -> SubscriptionEventList:
        """One page of delivery events; pass ``pagination.next_cursor`` back as ``cursor``."""
        return self._request(
            "GET", f"/v1/subscriptions/{_segment(subscription_id)}/events",
            params={"limit": limit, "cursor": cursor, "offset": offset},
        )

    def get_event(self, event_id: str) -> SubscriptionEvent:
        return self._request("GET", f"/v1/subscriptions/events/{_segment(event_id)}")

    def resend_event(self, event_id: str) -> SubscriptionEventResendResponse:
        """Deliver a past event again; the result records the new delivery attempt."""
        return self._request("POST", f"/v1/subscriptions/events/{_segment(event_id)}/resend")

    def test_delivery(
            self, **body: Unpack[SubscriptionTestRequest],
    ) -> SubscriptionTestResponse:
        """Send a synthetic event to a webhook URL or email address to verify delivery."""
        return self._request("POST", "/v1/subscriptions/test", json=dict(body))
