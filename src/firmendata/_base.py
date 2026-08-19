"""Shared plumbing between the sync and async clients."""

from __future__ import annotations

import json as _json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ._retry import DEFAULT_MAX_RETRIES
from ._version import __version__
from .errors import build_error

DEFAULT_BASE_URL = "https://api.firmendata.com"
DEFAULT_TIMEOUT = 30.0

ParamValue = str | int | float | bool | Sequence[str] | Sequence[int] | None

#: Exactly the element type httpx declares for `params`. We only ever emit
#: strings, but `list` is invariant, so a narrower `list[tuple[str, str]]`
#: would not be accepted where httpx wants this.
QueryParamPairs = list[tuple[str, str | int | float | bool | None]]


class _BaseClient:
    """URL, header and parameter handling. No I/O lives here."""

    def __init__(
            self,
            api_key: str | None = None,
            *,
            base_url: str = DEFAULT_BASE_URL,
            timeout: float = DEFAULT_TIMEOUT,
            max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        #: ``None`` is legitimate: ``autocomplete()`` works without a key.
        #: Every other endpoint will raise ``AuthenticationError``.
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))

    # -- request shaping ---------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": f"firmendata-python/{__version__}",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

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
    def _decode(status_code: int, content: bytes, headers: Mapping[str, str]) -> Any:
        """Parse a response body, raising the mapped error for non-2xx."""
        body: Any = None
        if content:
            try:
                body = _json.loads(content)
            except ValueError:
                body = None

        if 200 <= status_code < 300:
            return body
        raise build_error(status_code, body, headers)
