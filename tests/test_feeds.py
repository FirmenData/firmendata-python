"""Feed polling and resumable iteration against the existing mocked transport."""

from __future__ import annotations

import httpx
import pytest

from firmendata import (
    AsyncFeedIterator,
    AsyncFirmenData,
    AuthenticationError,
    FirmenData,
    ValidationError,
)
from firmendata.errors import APIConnectionError

FEEDS = [
    ("feed_new_companies", "/v1/feeds/new-companies", "company.founded"),
    ("feed_insolvencies", "/v1/feeds/insolvencies", "company.insolvency_notice"),
]
SINCE = "2026-10-11T12:00:00+00:00"
CURSOR = "opaque+/= &?%signed"


def event(event_id, event_type):
    return {
        "id": event_id,
        "object": "feed_event",
        "type": event_type,
        "discovered_at": SINCE,
        "company": {"country": "DE", "name": "Example GmbH", "eu_id": "DE1"},
        "links": {"company": "https://api.firmendata.com/v1/companies/DE1"},
    }


@pytest.fixture(params=[FirmenData, AsyncFirmenData], ids=["sync", "async"])
async def make_client(request):
    """Run each scenario on both clients and close the injected HTTP clients."""
    transports = []

    def make(handler, api_key="k"):
        http_class = httpx.AsyncClient if request.param is AsyncFirmenData else httpx.Client
        transport = http_class(transport=httpx.MockTransport(handler))
        transports.append(transport)
        return request.param(api_key=api_key, max_retries=0, http_client=transport)

    yield make
    for transport in transports:
        if isinstance(transport, httpx.AsyncClient):
            await transport.aclose()
        else:
            transport.close()


async def call(client, method, **filters):
    result = getattr(client, method)(**filters)
    return await result if isinstance(client, AsyncFirmenData) else result


async def drain(events):
    if isinstance(events, AsyncFeedIterator):
        return [item async for item in events]
    return list(events)


async def take(events):
    return await anext(events) if isinstance(events, AsyncFeedIterator) else next(events)


async def close(events):
    if isinstance(events, AsyncFeedIterator):
        await events.aclose()
    else:
        events.close()


@pytest.mark.parametrize("country", [None, "DE", "CH", "AT"])
async def test_autocomplete_country(make_client, country):
    response = {"data": [{"eu_id": "AT1", "country_code": "AT"}]}

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/companies/autocomplete"
        expected = {"q": "example", "limit": "5", "fetch_realtime": "true"}
        if country is not None:
            expected["country"] = country
        assert dict(request.url.params) == expected
        return httpx.Response(200, json=response)

    assert await call(
        make_client(handler), "autocomplete", q="example", country=country,
        limit=5, fetch_realtime=True,
    ) == response


@pytest.mark.parametrize("method,path,event_type", FEEDS)
async def test_feed_query_encoding(make_client, method, path, event_type):
    response = {"data": [event("event-1", event_type)], "next_cursor": CURSOR, "has_more": True}
    filters = {
        "cursor": CURSOR,
        "limit": 100,
        "country": "CH",
        "canton": ["ZH", "BE"],
        "industry_slug": ["technology", "manufacturing"],
        "rechtsform": ["AG (CH)", "GmbH (CH)"],
    }
    if method == "feed_insolvencies":
        filters.update(category=["Konkurseröffnung", "Schuldenruf"], revenue_min=0,
                       revenue_max=1_000_000.5)

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == path
        assert request.headers["authorization"] == "Bearer k"
        for key, value in filters.items():
            expected = list(map(str, value)) if isinstance(value, list) else [str(value)]
            assert request.url.params.get_list(key) == expected
        assert set(request.url.params) == set(filters)
        return httpx.Response(200, json=response, headers={"X-Credits-Charged": "1"})

    assert await call(make_client(handler), method, **filters) == response


@pytest.mark.parametrize("method,path,event_type", FEEDS)
async def test_feed_first_call_and_defaults(make_client, method, path, event_type):
    seen = []
    response = {"data": [], "next_cursor": CURSOR, "has_more": False}

    def handler(request):
        assert request.url.path == path
        seen.append(request.url.params)
        return httpx.Response(200, json=response, headers={"X-Credits-Charged": "0"})

    client = make_client(handler)
    assert await call(client, method) == response
    assert not seen[0]  # Server supplies limit=50 and the initial discovery time.
    assert await call(client, method, since=SINCE, bundesland=["Bayern", "Berlin"]) == response
    assert seen[1]["since"] == SINCE
    assert seen[1].get_list("bundesland") == ["Bayern", "Berlin"]
    assert "cursor" not in seen[1]


@pytest.mark.parametrize("method,path,event_type", FEEDS)
@pytest.mark.parametrize("status,error", [(401, AuthenticationError), (422, ValidationError)])
async def test_feed_errors(make_client, method, path, event_type, status, error):
    def handler(request):
        assert request.url.path == path
        return httpx.Response(status, json={
            "status": status, "title": "Feed rejected", "detail": "Invalid or expired cursor",
            "errors": [{"param": "cursor", "message": "Expired"}],
        })

    with pytest.raises(error) as exc:
        await call(make_client(handler, api_key=None), method, cursor=CURSOR)
    assert exc.value.status_code == status
    if status == 422:
        assert exc.value.errors == [{"param": "cursor", "message": "Expired"}]


@pytest.mark.parametrize("method,path,event_type", FEEDS)
async def test_iterator_drains_deduplicates_and_resumes(make_client, method, path, event_type):
    seen = []
    pages = [
        {"data": [event("one", event_type), event("one", event_type)],
         "next_cursor": CURSOR, "has_more": True},
        {"data": [], "next_cursor": "empty-page", "has_more": True},
        {"data": [event("one", event_type), event("two", event_type)],
         "next_cursor": "done", "has_more": False},
        {"data": [], "next_cursor": "later", "has_more": False},
    ]

    def handler(request):
        assert request.url.path == path
        seen.append(request.url.params)
        return httpx.Response(200, json=pages[len(seen) - 1])

    client = make_client(handler)
    iterator_method = getattr(client, f"iter_{method}")
    events = iterator_method(since=SINCE, country="DE", bundesland=["Bayern"], limit=2)
    assert seen == []  # Creating an iterator is lazy, including on the async client.
    assert events.next_cursor is None
    assert await drain(events) == [event("one", event_type), event("two", event_type)]
    assert events.next_cursor == "done"
    assert len(seen) == 3
    assert "cursor" not in seen[0] and seen[0]["since"] == SINCE
    assert seen[1]["cursor"] == CURSOR and "since" not in seen[1]
    assert seen[2]["cursor"] == "empty-page" and "since" not in seen[2]
    for params in seen:
        assert params["country"] == "DE"
        assert params.get_list("bundesland") == ["Bayern"]
        assert params["limit"] == "2"
    assert await drain(events) == []  # Exhaustion never polls again.
    assert len(seen) == 3

    resumed = iterator_method(cursor=events.next_cursor, country="DE", limit=2)
    assert resumed.next_cursor == "done"
    assert await drain(resumed) == []
    assert seen[3]["cursor"] == "done"
    assert resumed.next_cursor == "later"


@pytest.mark.parametrize("method,path,event_type", FEEDS)
async def test_iterator_empty_final_page(make_client, method, path, event_type):
    seen = []

    def handler(request):
        assert request.url.path == path
        seen.append(request.url.params)
        # data may be omitted: the contract gives it a default empty list.
        return httpx.Response(200, json={"next_cursor": CURSOR, "has_more": False})

    events = getattr(make_client(handler), f"iter_{method}")(cursor="start", since=SINCE)
    assert events.next_cursor == "start"
    assert await drain(events) == []
    assert events.next_cursor == CURSOR
    assert len(seen) == 1
    assert dict(seen[0]) == {"cursor": "start"}  # since is first-call-only.


@pytest.mark.parametrize("method,path,event_type", FEEDS)
@pytest.mark.parametrize("cursor", [None, CURSOR])
async def test_interrupted_page_keeps_starting_cursor(
        make_client, method, path, event_type, cursor,
):
    seen = []

    def handler(request):
        assert request.url.path == path
        seen.append(request)
        return httpx.Response(200, json={
            "data": [event("one", event_type), event("two", event_type)],
            "next_cursor": "unprocessed-page", "has_more": True,
        })

    events = getattr(make_client(handler), f"iter_{method}")(cursor=cursor)
    assert await take(events) == event("one", event_type)
    assert events.next_cursor == cursor
    await close(events)
    assert await drain(events) == []
    assert events.next_cursor == cursor
    assert len(seen) == 1


@pytest.mark.parametrize("method,path,event_type", FEEDS)
async def test_iterator_failure_keeps_consumed_page_cursor(make_client, method, path, event_type):
    seen = []

    def handler(request):
        assert request.url.path == path
        seen.append(request.url.params)
        if len(seen) == 1:
            return httpx.Response(200, json={
                "data": [event("one", event_type)], "next_cursor": CURSOR, "has_more": True,
            })
        assert request.url.params["cursor"] == CURSOR
        raise httpx.ConnectError("connection lost")

    events = getattr(make_client(handler), f"iter_{method}")()
    assert await take(events) == event("one", event_type)
    with pytest.raises(APIConnectionError):
        await take(events)
    assert events.next_cursor == CURSOR
    assert len(seen) == 2
