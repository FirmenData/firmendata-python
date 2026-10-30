"""Shared plumbing between the sync and async clients."""

from __future__ import annotations

import json as _json
import re
import warnings
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict
from urllib.parse import quote

import httpx

from ._retry import DEFAULT_MAX_RETRIES
from ._version import __version__
from .errors import APIConnectionError, APITimeoutError

DEFAULT_BASE_URL = "https://api.firmendata.com"
DEFAULT_TIMEOUT = 30.0
#: The minimum timeout, in seconds, for live calls — ``fetch_realtime=True``
#: and document listings and downloads — which can take up to about two
#: minutes. Applies unless the call passes its own ``timeout``.
REALTIME_TIMEOUT = 150.0

ParamValue = str | int | float | bool | Sequence[str] | Sequence[int] | None

#: Exactly the element type httpx declares for `params`. We only ever emit
#: strings, but `list` is invariant, so a narrower `list[tuple[str, str]]`
#: would not be accepted where httpx wants this.
QueryParamPairs = list[tuple[str, str | int | float | bool | None]]

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_APP_INFO_MAX_LENGTH = {"name": 64, "version": 32, "url": 256}

_LIVE_NOTE = (
    "Live calls (fetch_realtime=True, document listings and downloads) are not retried "
    "once they may have reached the server: the server can still finish this one, and it "
    "is billed when it does."
)


class AppInfo(TypedDict):
    """Identifies your integration in the ``User-Agent``, like Stripe's ``app_info``."""

    #: Your application or plugin name, e.g. ``"acme-crm"``.
    name: str
    version: NotRequired[str]
    url: NotRequired[str]


@dataclass(frozen=True, slots=True)
class ResponseInfo:
    """Metadata of one HTTP response, passed to ``on_response``."""

    status: int
    #: Response headers; lookups are case-insensitive.
    headers: Mapping[str, str]
    #: ``X-Request-Id`` — quote it to support.
    request_id: str | None
    #: ``X-Credits-Charged``: credits this call deducted. ``None`` when absent.
    credits_charged: int | None


#: Called for every HTTP response. The async client also awaits a coroutine.
ResponseHook = Callable[[ResponseInfo], object]


def _segment(value: str) -> str:
    """Percent-encode a caller-supplied path segment.

    ``safe=""`` so ``/``, ``?`` and ``#`` are encoded too — otherwise an id
    taken from untrusted input could rewrite the request path or smuggle in
    extra query parameters (e.g. ``fetch_realtime=true``) under the caller's
    API key.
    """
    return quote(value, safe="")


def _app_info_part(field: str, value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"app_info[{field!r}] must be a string")
    # Control characters (CR/LF above all) would let the value break out of the header.
    if _CONTROL_CHARS.search(value):
        raise ValueError(f"app_info[{field!r}] must not contain line breaks or control characters")
    return value.strip()[: _APP_INFO_MAX_LENGTH[field]] or None


def _user_agent(app_info: Mapping[str, Any] | None) -> str:
    agent = f"firmendata-python/{__version__}"
    if app_info is None:
        return agent
    name = _app_info_part("name", app_info.get("name"))
    if not name:
        raise ValueError("app_info['name'] must be a non-empty string")
    version = _app_info_part("version", app_info.get("version"))
    url = _app_info_part("url", app_info.get("url"))
    agent += f" {name}"
    if version:
        agent += f"/{version}"
    if url:
        agent += f" ({url})"
    return agent


def _request_sent(exc: httpx.TransportError) -> bool:
    """Whether a failed request may have reached the server.

    Only failures to *establish* a connection prove it did not.
    """
    return not isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))


def _financial_params(
        details: bool | None, include_line_items: bool | None, years: int | None,
) -> dict[str, Any]:
    if include_line_items is not None:
        warnings.warn(
            "include_line_items is deprecated: line items are included by default. "
            "Pass details=False for the lean response.",
            DeprecationWarning,
            stacklevel=3,
        )
        if details is None:
            details = include_line_items
    return {"details": details, "years": years}


class _BaseClient:
    """URL, header and parameter handling. No I/O lives here."""

    def __init__(
            self,
            api_key: str | None = None,
            *,
            base_url: str = DEFAULT_BASE_URL,
            timeout: float = DEFAULT_TIMEOUT,
            max_retries: int = DEFAULT_MAX_RETRIES,
            app_info: AppInfo | None = None,
            on_response: ResponseHook | None = None,
    ) -> None:
        #: ``None`` is legitimate: ``autocomplete()`` works without a key.
        #: Every other endpoint will raise ``AuthenticationError``.
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.on_response = on_response
        self._user_agent = _user_agent(app_info)

    # -- request shaping ---------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _headers(self) -> dict[str, str]:
        """The one place request headers are built."""
        headers = {
            "Accept": "application/json",
            "User-Agent": self._user_agent,
            "X-FirmenData-Client": f"python-sdk/{__version__}",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _timeout_for(self, live: bool, timeout: float | None) -> float:
        """An explicit per-call ``timeout`` wins; live calls wait at least 150 s."""
        if timeout is not None:
            return timeout
        return max(self.timeout, REALTIME_TIMEOUT) if live else self.timeout

    @staticmethod
    def _params(raw: Mapping[str, ParamValue]) -> QueryParamPairs:
        """Flatten query parameters, dropping ``None``.

        Returned as a list of pairs rather than a dict so array filters
        (``city``, ``industry_slug``, …) repeat the key — ``?city=Berlin&
        city=Hamburg`` — which is the form the API parses. Booleans are
        lower-cased because ``str(True)`` is ``"True"``, which FastAPI's bool
        coercion rejects.
        """
        out: QueryParamPairs = []
        for key, value in raw.items():
            if value is None:
                continue
            if isinstance(value, bool):
                out.append((key, "true" if value else "false"))
            elif isinstance(value, (str, int, float)):
                out.append((key, str(value)))
            elif isinstance(value, Iterable):
                for item in value:
                    if item is None:
                        continue
                    out.append((key, str(item)))
            else:  # pragma: no cover - defensive
                out.append((key, str(value)))
        return out

    # -- response handling -------------------------------------------------

    @staticmethod
    def _parse(content: bytes) -> Any:
        """Parse a response body; ``None`` when it is empty or not JSON."""
        if not content:
            return None
        try:
            return _json.loads(content)
        except ValueError:
            return None

    @staticmethod
    def _response_info(status: int, headers: Mapping[str, str], body: Any) -> ResponseInfo:
        request_id = headers.get("X-Request-Id")
        if request_id is None and isinstance(body, dict):
            from_body = body.get("request_id")
            request_id = from_body if isinstance(from_body, str) else None
        try:
            credits: int | None = int(headers.get("X-Credits-Charged", ""))
        except ValueError:
            credits = None
        return ResponseInfo(
            status=status, headers=headers, request_id=request_id, credits_charged=credits,
        )

    @staticmethod
    def _transport_error(
            exc: httpx.TransportError, *, live: bool, sent: bool, timeout: float,
    ) -> APIConnectionError:
        if isinstance(exc, httpx.TimeoutException):
            message = f"Request timed out after {timeout:g}s ({exc})."
            if live and sent:
                message += (
                    f" {_LIVE_NOTE} Pass a higher timeout for this call (or on the client)"
                    " if it needs longer."
                )
            error: APIConnectionError = APITimeoutError(message)
        else:
            error = APIConnectionError(f"{exc}. {_LIVE_NOTE}" if live and sent else str(exc))
        error.__cause__ = exc
        return error
