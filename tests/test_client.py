"""Client behaviour, against a mocked transport.

No network, no credentials. What these pin is the stuff that is easy to get
quietly wrong: how parameters are serialised onto the query string, which
failures are safe to replay, and that a keyless client is genuinely usable.
"""

from __future__ import annotations

import httpx
import pytest

from firmendata import (
    AsyncFirmenData,
    AuthenticationError,
    FirmenData,
    InsufficientCreditsError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from firmendata._retry import backoff_seconds, should_retry
from firmendata.errors import APIConnectionError, ServerError


def problem(slug: str, status: int, detail: str = "boom", **extra):
    return {
        "type": f"https://api.firmendata.com/problems/{slug}",
        "title": slug.replace("-", " ").title(),
        "status": status,
        "detail": detail,
        "instance": "/v1/companies/autocomplete",
        "request_id": "req-abc123",
        **extra,
    }


def client_with(handler, **kw) -> FirmenData:
    return FirmenData(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)), **kw
    )


# ---------------------------------------------------------------------------
# Request shaping
# ---------------------------------------------------------------------------

class TestRequestShaping:
    def test_no_auth_header_without_a_key(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"data": []})

        client_with(handler).autocomplete("sap")
        assert seen["auth"] is None

    def test_bearer_header_with_a_key(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"data": []})

        client_with(handler, api_key="firmendata_live_xyz").autocomplete("sap")
        assert seen["auth"] == "Bearer firmendata_live_xyz"

    def test_booleans_serialise_lowercase(self):
        """`str(True)` is 'True', which FastAPI's bool coercion rejects."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json={})

        client_with(handler, api_key="k").get_company("DE1", fetch_realtime=True)
        assert "fetch_realtime=true" in seen["url"]

    def test_none_parameters_are_dropped(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json={})

        client_with(handler, api_key="k").download_document(
            "DE1", file_type="Bilanz", file_id=None,
        )
        assert "file_id" not in seen["url"]

    def test_list_filters_repeat_the_key(self):
        """`?city=Berlin&city=Hamburg` is what the API parses — not a
        comma-joined single value."""
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["params"] = request.url.params.get_list("city")
            return httpx.Response(200, json={"data": []})

        client_with(handler, api_key="k").search(city=["Berlin", "Hamburg"])
        assert seen["params"] == ["Berlin", "Hamburg"]

    def test_user_agent_is_identifiable(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["ua"] = request.headers.get("user-agent")
            return httpx.Response(200, json={})

        client_with(handler).autocomplete("sap")
        assert seen["ua"].startswith("firmendata-python/")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class TestErrorMapping:
    @pytest.mark.parametrize("slug,status,expected", [
        ("unauthenticated", 401, AuthenticationError),
        ("insufficient-credits", 402, InsufficientCreditsError),
        ("not-found", 404, NotFoundError),
        ("validation-error", 422, ValidationError),
        ("rate-limit-exceeded", 429, RateLimitError),
    ])
    def test_slug_selects_the_exception(self, slug, status, expected):
        def handler(request):
            return httpx.Response(status, json=problem(slug, status))

        with pytest.raises(expected) as exc:
            client_with(handler, max_retries=0).autocomplete("sap")
        assert exc.value.status_code == status
        assert exc.value.request_id == "req-abc123"

    def test_falls_back_to_status_when_body_is_not_a_problem(self):
        """A proxy 502 has no problem body — still map it usefully."""
        def handler(request):
            return httpx.Response(404, text="<html>nope</html>")

        with pytest.raises(NotFoundError):
            client_with(handler, max_retries=0).autocomplete("sap")

    def test_validation_errors_expose_the_fields(self):
        def handler(request):
            return httpx.Response(422, json=problem(
                "validation-error", 422,
                errors=[{"path": "q", "code": "too_short"}],
            ))

        with pytest.raises(ValidationError) as exc:
            client_with(handler, max_retries=0).autocomplete("ab")
        assert exc.value.errors[0]["path"] == "q"

    def test_rate_limit_carries_retry_after(self):
        def handler(request):
            return httpx.Response(
                429, json=problem("rate-limit-exceeded", 429),
                headers={"Retry-After": "7"},
            )

        with pytest.raises(RateLimitError) as exc:
            client_with(handler, max_retries=0).autocomplete("sap")
        assert exc.value.retry_after == 7.0

    def test_keyless_realtime_surfaces_as_auth_error(self):
        """The server rejects fetch_realtime without a key; the SDK should
        make that legible rather than a bare 401."""
        def handler(request):
            return httpx.Response(401, json=problem(
                "unauthenticated", 401,
                detail="`fetch_realtime=true` requires an API key.",
            ))

        with pytest.raises(AuthenticationError) as exc:
            client_with(handler, max_retries=0).autocomplete("sap", fetch_realtime=True)
        assert "API key" in exc.value.detail


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------

class TestRetryPolicy:
    def test_429_is_retried_on_any_method(self):
        """Rate-limited calls are rejected before the handler runs, so even
        a POST is safe to replay."""
        assert should_retry(method="POST", status_code=429, attempt=0, max_retries=2)

    def test_5xx_is_not_retried_on_post(self):
        """A create that 502s may already have been applied."""
        assert not should_retry(method="POST", status_code=503, attempt=0, max_retries=2)

    def test_5xx_is_retried_on_get(self):
        assert should_retry(method="GET", status_code=503, attempt=0, max_retries=2)

    def test_connection_failure_not_retried_on_post(self):
        assert not should_retry(method="POST", status_code=None, attempt=0, max_retries=2)

    def test_4xx_is_never_retried(self):
        assert not should_retry(method="GET", status_code=404, attempt=0, max_retries=5)

    def test_budget_is_respected(self):
        assert not should_retry(method="GET", status_code=500, attempt=2, max_retries=2)

    def test_retry_after_wins_over_backoff(self):
        assert backoff_seconds(0, retry_after=3.0) == 3.0

    def test_backoff_is_jittered_within_the_ceiling(self):
        """Full jitter: without it, every client that trips the same limit
        comes back at the same instant."""
        values = {backoff_seconds(3) for _ in range(50)}
        assert len(values) > 1
        assert all(0.0 <= v <= 4.0 for v in values)

    def test_get_recovers_after_a_500(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(500, json=problem("server-error", 500))
            return httpx.Response(200, json={"data": [{"name": "SAP SE"}]})

        result = client_with(handler, max_retries=2).autocomplete("sap")
        assert calls["n"] == 2
        assert result["data"][0]["name"] == "SAP SE"

    def test_gives_up_and_raises_the_last_error(self):
        def handler(request):
            return httpx.Response(500, json=problem("server-error", 500))

        with pytest.raises(ServerError):
            client_with(handler, max_retries=1).autocomplete("sap")

    def test_transport_error_becomes_a_typed_exception(self):
        def handler(request):
            raise httpx.ConnectError("no route to host")

        with pytest.raises(APIConnectionError):
            client_with(handler, max_retries=0).autocomplete("sap")


# ---------------------------------------------------------------------------
# Async parity
# ---------------------------------------------------------------------------

class TestAsyncClient:
    async def test_autocomplete_without_a_key(self):
        def handler(request):
            assert request.headers.get("authorization") is None
            return httpx.Response(200, json={"data": [{"name": "SAP SE"}]})

        async with AsyncFirmenData(
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ) as fd:
            result = await fd.autocomplete("sap")
        assert result["data"][0]["name"] == "SAP SE"

    async def test_errors_map_identically_to_sync(self):
        def handler(request):
            return httpx.Response(402, json=problem("insufficient-credits", 402))

        async with AsyncFirmenData(
            api_key="k", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ) as fd:
            with pytest.raises(InsufficientCreditsError):
                await fd.get_ubo("DE1")

    async def test_method_surface_matches_the_sync_client(self):
        """The two clients must not drift apart."""
        public = lambda c: {  # noqa: E731
            n for n in dir(c)
            if not n.startswith("_") and callable(getattr(c, n))
        }
        sync = public(FirmenData) - {"close"}
        asyn = public(AsyncFirmenData) - {"aclose"}
        assert sync == asyn
