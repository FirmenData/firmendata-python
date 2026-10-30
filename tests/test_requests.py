"""Request identification, live-call safety, rate-limit waits, response
metadata and the pagination helpers — on both clients.

Mirrors the Node SDK's ``test/requests.test.ts``. No network, no credentials.
"""

from __future__ import annotations

import time
import warnings
from types import SimpleNamespace

import httpx
import pytest

import firmendata.async_client
import firmendata.client
from firmendata import (
    APIConnectionError,
    APITimeoutError,
    AsyncFirmenData,
    FirmenData,
    NotFoundError,
    RateLimitError,
    ResponseInfo,
    ServerError,
    __version__,
)
from firmendata._retry import should_retry


@pytest.fixture(params=[FirmenData, AsyncFirmenData], ids=["sync", "async"])
async def make_client(request, monkeypatch):
    """Build either client over a mock transport, with backoff sleeps skipped."""
    monkeypatch.setattr(firmendata.client, "time", SimpleNamespace(sleep=lambda _s: None))

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(firmendata.async_client, "asyncio", SimpleNamespace(sleep=no_sleep))
    transports = []

    def make(handler, **kwargs):
        is_async = request.param is AsyncFirmenData
        transport = (httpx.AsyncClient if is_async else httpx.Client)(
            transport=httpx.MockTransport(handler),
        )
        transports.append(transport)
        kwargs.setdefault("max_retries", 0)
        return request.param(http_client=transport, **kwargs)

    yield make
    for transport in transports:
        if isinstance(transport, httpx.AsyncClient):
            await transport.aclose()
        else:
            transport.close()


async def call(client, method, *args, **kwargs):
    result = getattr(client, method)(*args, **kwargs)
    return await result if isinstance(client, AsyncFirmenData) else result


async def collect(client, method, **kwargs):
    items = getattr(client, method)(**kwargs)
    if isinstance(client, AsyncFirmenData):
        return [item async for item in items]
    return list(items)


def problem(slug, status):
    return {
        "type": f"https://api.firmendata.com/problems/{slug}",
        "title": slug,
        "status": status,
        "detail": "boom",
        "request_id": "req-body",
    }


# ---------------------------------------------------------------------------
# Client identification
# ---------------------------------------------------------------------------

async def test_client_headers(make_client):
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={"data": []})

    await call(make_client(handler), "autocomplete", "sap")
    assert seen["x-firmendata-client"] == f"python-sdk/{__version__}"
    assert seen["user-agent"] == f"firmendata-python/{__version__}"


async def test_app_info_is_appended_to_the_user_agent(make_client):
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, json={"data": []})

    client = make_client(
        handler, app_info={"name": "acme-crm", "version": "2.1.0", "url": "https://acme.example"},
    )
    await call(client, "autocomplete", "sap")
    assert seen["ua"] == f"firmendata-python/{__version__} acme-crm/2.1.0 (https://acme.example)"


def test_app_info_name_only_and_truncated():
    fd = FirmenData(app_info={"name": "x" * 200})
    assert fd._headers()["User-Agent"] == f"firmendata-python/{__version__} {'x' * 64}"


@pytest.mark.parametrize("app_info", [
    {"name": "evil\r\nX-Injected: 1"},
    {"name": "ok", "version": "1\n2"},
    {"name": "ok", "url": "https://a.example\r"},
    {"name": "  "},
])
def test_unusable_app_info_is_rejected(app_info):
    with pytest.raises(ValueError):
        FirmenData(app_info=app_info)


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------

async def test_get_lists(make_client):
    body = {"object": "list", "data": [{"list_id": 42, "name": "Targets", "company_count": 3}]}

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/lists"
        assert not request.url.params
        assert request.headers["authorization"] == "Bearer k"
        return httpx.Response(200, json=body)

    assert await call(make_client(handler, api_key="k"), "get_lists") == body


async def test_get_list(make_client):
    body = {"object": "list", "list_id": 42, "name": "Targets", "data": []}

    def handler(request):
        assert request.url.path == "/v1/lists/42"
        assert dict(request.url.params) == {
            "limit": "200", "cursor": "c+/=", "sort": "revenue", "sort_direction": "asc",
        }
        return httpx.Response(200, json=body)

    result = await call(
        make_client(handler, api_key="k"), "get_list", 42,
        limit=200, cursor="c+/=", sort="revenue", sort_direction="asc",
    )
    assert result == body


async def test_unknown_list_is_not_found(make_client):
    def handler(request):
        return httpx.Response(404, json=problem("not-found", 404))

    with pytest.raises(NotFoundError):
        await call(make_client(handler, api_key="k"), "get_list", 7)


# ---------------------------------------------------------------------------
# Financials detail level
# ---------------------------------------------------------------------------

async def test_financials_details(make_client):
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={})

    client = make_client(handler)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # the new spelling never warns
        await call(client, "get_financials", "DE1")
        await call(client, "get_financials", "DE1", details=False, years=3)
    assert seen == [{}, {"details": "false", "years": "3"}]


async def test_include_line_items_is_deprecated_and_maps_to_details(make_client):
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={})

    client = make_client(handler)
    with pytest.warns(DeprecationWarning, match="details"):
        await call(client, "get_financials", "DE1", include_line_items=True)
    with pytest.warns(DeprecationWarning):
        await call(client, "get_financials", "DE1", include_line_items=True, details=False)
    assert seen == [{"details": "true"}, {"details": "false"}]


# ---------------------------------------------------------------------------
# Live calls are never billed twice
# ---------------------------------------------------------------------------

LIVE_CALLS = [
    ("get_company", {"fetch_realtime": True}),
    ("get_shareholders", {"fetch_realtime": True}),
    ("get_ubo", {"fetch_realtime": True}),
    ("get_history", {"fetch_realtime": True}),
    ("list_documents", {}),
    ("download_document", {"file_type": "shareholder_list"}),
]


@pytest.mark.parametrize("method,kwargs", LIVE_CALLS)
async def test_live_calls_wait_at_least_150_seconds(make_client, method, kwargs):
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={})

    await call(make_client(handler), method, "DE1", **kwargs)
    await call(make_client(handler, timeout=200.0), method, "DE1", **kwargs)
    await call(make_client(handler, timeout=200.0), method, "DE1", **kwargs, timeout=5.0)
    assert seen == [150.0, 200.0, 5.0]


async def test_ordinary_calls_keep_the_client_timeout(make_client):
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"data": []})

    client = make_client(handler)
    await call(client, "get_company", "DE1")
    await call(client, "search", q="acme")
    await call(client, "search", q="acme", fetch_realtime=True)
    await call(client, "autocomplete", "acme", fetch_realtime=True, timeout=9.0)
    assert seen == [30.0, 30.0, 150.0, 9.0]


@pytest.mark.parametrize("method,kwargs", LIVE_CALLS)
async def test_timed_out_live_call_is_not_retried(make_client, method, kwargs):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(APITimeoutError) as exc:
        await call(make_client(handler, max_retries=2), method, "DE1", **kwargs)
    assert len(calls) == 1
    assert "billed" in str(exc.value)
    assert "higher timeout" in str(exc.value)


async def test_timed_out_ordinary_get_is_retried(make_client):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(APITimeoutError) as exc:
        await call(make_client(handler, max_retries=2), "get_company", "DE1")
    assert len(calls) == 3
    assert "billed" not in str(exc.value)


@pytest.mark.parametrize("failure", [httpx.ReadError, httpx.RemoteProtocolError, httpx.WriteError])
async def test_live_call_broken_after_sending_is_not_retried(make_client, failure):
    calls = []

    def handler(request):
        calls.append(request)
        raise failure("connection lost", request=request)

    with pytest.raises(APIConnectionError) as exc:
        await call(make_client(handler, max_retries=2), "get_ubo", "DE1", fetch_realtime=True)
    assert not isinstance(exc.value, APITimeoutError)
    assert "billed" in str(exc.value)
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ConnectTimeout])
async def test_live_call_that_never_left_is_retried(make_client, failure):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise failure("refused", request=request)
        return httpx.Response(200, json={})

    await call(make_client(handler, max_retries=2), "get_ubo", "DE1", fetch_realtime=True)
    assert len(calls) == 2


async def test_gateway_timeout_retried_only_for_ordinary_calls(make_client):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(504, json=problem("server-error", 504))

    with pytest.raises(ServerError):
        await call(make_client(handler, max_retries=2), "get_company", "DE1", fetch_realtime=True)
    assert len(calls) == 1
    with pytest.raises(ServerError):
        await call(make_client(handler, max_retries=2), "get_company", "DE1")
    assert len(calls) == 4


def test_should_retry_live_rules():
    base = {"method": "GET", "attempt": 0, "max_retries": 2}
    assert not should_retry(status_code=None, live=True, **base)
    assert should_retry(status_code=None, live=True, request_sent=False, **base)
    assert not should_retry(status_code=504, live=True, **base)
    assert not should_retry(status_code=408, live=True, **base)
    assert should_retry(status_code=503, live=True, **base)
    assert should_retry(status_code=429, live=True, **base)
    assert should_retry(status_code=None, **base)
    assert should_retry(status_code=504, **base)


# ---------------------------------------------------------------------------
# Retry-After
# ---------------------------------------------------------------------------

async def test_long_retry_after_raises_at_once(make_client):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            429, json=problem("rate-limit-exceeded", 429), headers={"Retry-After": "86400"},
        )

    started = time.monotonic()
    with pytest.raises(RateLimitError) as exc:
        await call(make_client(handler, max_retries=2), "autocomplete", "sap")
    assert exc.value.retry_after == 86_400.0
    assert len(calls) == 1
    assert time.monotonic() - started < 1.0


async def test_short_retry_after_is_honoured(make_client):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                429, json=problem("rate-limit-exceeded", 429), headers={"Retry-After": "3"},
            )
        return httpx.Response(200, json={"data": []})

    await call(make_client(handler, max_retries=2), "autocomplete", "sap")
    assert len(calls) == 2


def test_should_retry_caps_retry_after():
    base = {"method": "GET", "status_code": 429, "attempt": 0, "max_retries": 2}
    assert should_retry(retry_after=20.0, **base)
    assert not should_retry(retry_after=21.0, **base)
    assert should_retry(**base)


# ---------------------------------------------------------------------------
# on_response
# ---------------------------------------------------------------------------

async def test_on_response_sees_every_response(make_client):
    seen: list[ResponseInfo] = []
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                429, json=problem("rate-limit-exceeded", 429),
                headers={"Retry-After": "0", "X-Credits-Charged": "0"},
            )
        return httpx.Response(
            200, json={"data": []},
            headers={"X-Request-Id": "req-1", "X-Credits-Charged": "3"},
        )

    client = make_client(handler, max_retries=1, on_response=seen.append)
    await call(client, "search", q="acme")
    assert [(i.status, i.request_id, i.credits_charged) for i in seen] == [
        (429, "req-body", 0), (200, "req-1", 3),
    ]
    assert seen[1].headers["x-credits-charged"] == "3"  # case-insensitive


async def test_on_response_on_errors_without_credits_header(make_client):
    seen: list[ResponseInfo] = []

    def handler(request):
        return httpx.Response(404, json=problem("not-found", 404))

    with pytest.raises(NotFoundError):
        await call(make_client(handler, on_response=seen.append), "get_company", "DE1")
    assert len(seen) == 1 and seen[0].credits_charged is None and seen[0].status == 404


async def test_on_response_errors_propagate(make_client):
    class Boom(Exception):
        pass

    def hook(info):
        raise Boom

    def handler(request):
        return httpx.Response(200, json={})

    with pytest.raises(Boom):
        await call(make_client(handler, on_response=hook), "get_company", "DE1")


async def test_async_on_response_may_be_a_coroutine():
    seen = []

    async def hook(info):
        seen.append(info.credits_charged)

    def handler(request):
        return httpx.Response(200, json={}, headers={"X-Credits-Charged": "2"})

    async with AsyncFirmenData(
        on_response=hook,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as fd:
        await fd.get_company("DE1")
    assert seen == [2]


# ---------------------------------------------------------------------------
# iter_search
# ---------------------------------------------------------------------------

def hit(n):
    return {"eu_id": f"DE{n}", "display_name": f"Company {n}"}


async def test_iter_search_follows_cursors_with_cursor_and_limit_only(make_client):
    pages = [
        {"data": [hit(1), hit(2)], "pagination": {"next_cursor": "c1", "has_more": True}},
        {"data": [hit(3)], "pagination": {"next_cursor": "c2", "has_more": True}},
        {"data": [hit(4)], "pagination": {"next_cursor": None, "has_more": False}},
    ]
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=pages[len(seen) - 1])

    hits = await collect(
        make_client(handler), "iter_search",
        bundesland=["Bayern"], q="acme", fetch_realtime=True, limit=2,
    )
    assert [h["eu_id"] for h in hits] == ["DE1", "DE2", "DE3", "DE4"]
    assert seen == [
        {"bundesland": "Bayern", "q": "acme", "fetch_realtime": "true", "limit": "2"},
        {"cursor": "c1", "limit": "2"},
        {"cursor": "c2", "limit": "2"},
    ]


async def test_iter_search_stops_at_max_results(make_client):
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={
            "data": [hit(1), hit(2), hit(3)],
            "pagination": {"next_cursor": "more", "has_more": True},
        })

    hits = await collect(make_client(handler), "iter_search", q="acme", max_results=2)
    assert [h["eu_id"] for h in hits] == ["DE1", "DE2"]
    assert seen == [{"q": "acme", "limit": "2"}]


async def test_iter_search_never_passes_the_cap(make_client):
    counter = {"n": 0, "calls": 0}

    def handler(request):
        counter["calls"] += 1
        start = counter["n"]
        counter["n"] += 50
        return httpx.Response(200, json={
            "data": [hit(i) for i in range(start, start + 50)],
            "pagination": {"next_cursor": f"c{start}", "has_more": True},
        })

    hits = await collect(make_client(handler), "iter_search", max_results=20_000)
    assert len(hits) == 10_000
    assert counter["calls"] == 200


async def test_iter_search_is_lazy(make_client):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [], "pagination": {"has_more": False}})

    client = make_client(handler)
    client.iter_search(q="acme")
    assert seen == []


# ---------------------------------------------------------------------------
# Typed request bodies
# ---------------------------------------------------------------------------

async def test_create_subscription_and_test_delivery_bodies(make_client):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.read()))
        return httpx.Response(200, json={"receiver_succeeded": True})

    client = make_client(handler, api_key="k")
    await call(
        client, "create_subscription",
        eu_id="DEF1103R.HRB12300B", subscription_type="details", cadence="weekly",
    )
    await call(
        client, "test_delivery",
        eu_id="DEF1103R.HRB12300B", subscription_type="details",
        notification_type="webhook", webhook_url="https://example.com/hook",
    )
    assert [(m, p) for m, p, _ in seen] == [
        ("POST", "/v1/subscriptions"), ("POST", "/v1/subscriptions/test"),
    ]
    assert b'"cadence":"weekly"' in seen[0][2].replace(b" ", b"")
    assert b'"notification_type":"webhook"' in seen[1][2].replace(b" ", b"")
