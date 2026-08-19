"""Typed exceptions mapped from the API's RFC 7807 problem responses.

Every non-2xx response carries a JSON body shaped like::

    {
      "type":   "https://api.firmendata.com/problems/insufficient-credits",
      "title":  "Insufficient credits.",
      "status": 402,
      "detail": "Your credit balance is too low for this request.",
      "instance": "/v1/companies/DE.../ubo",
      "request_id": "8e510d86-..."
    }

Exceptions are selected by the ``type`` slug rather than the status code,
because the slug is the stable part of the contract — a status can be shared
by several distinct failures. ``request_id`` is preserved on every exception;
quote it in support requests and it identifies the exact call in our logs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "FirmenDataError",
    "APIError",
    "AuthenticationError",
    "TokenExpiredError",
    "InsufficientCreditsError",
    "NotFoundError",
    "ConflictError",
    "ValidationError",
    "RateLimitError",
    "ServerError",
    "APIConnectionError",
    "APITimeoutError",
]


class FirmenDataError(Exception):
    """Base class for everything this library raises."""


class APIConnectionError(FirmenDataError):
    """The request never produced a response (DNS, TLS, connection reset)."""


class APITimeoutError(APIConnectionError):
    """The request exceeded the configured timeout."""


class APIError(FirmenDataError):
    """A structured error response from the API."""

    def __init__(
            self,
            message: str,
            *,
            status_code: int,
            problem_type: str | None = None,
            title: str | None = None,
            detail: str | None = None,
            instance: str | None = None,
            request_id: str | None = None,
            errors: list[Mapping[str, Any]] | None = None,
            headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.problem_type = problem_type
        self.title = title
        self.detail = detail
        self.instance = instance
        self.request_id = request_id
        #: Per-field validation failures. Only populated on 422.
        self.errors = list(errors or [])
        self.headers = dict(headers or {})

    def __str__(self) -> str:  # pragma: no cover - trivial
        base = super().__str__()
        return f"{base} (request_id={self.request_id})" if self.request_id else base


class AuthenticationError(APIError):
    """No valid API key. Also raised when a keyless call uses a paid feature."""


class TokenExpiredError(AuthenticationError):
    """The key was valid but has expired."""


class InsufficientCreditsError(APIError):
    """Not enough credits for this call. Top up or upgrade the plan."""


class NotFoundError(APIError):
    """No such company, subscription or event."""


class ConflictError(APIError):
    """The request conflicts with existing state (e.g. duplicate subscription)."""


class ValidationError(APIError):
    """Request parameters failed validation. See ``.errors`` for the fields."""


class RateLimitError(APIError):
    """Rate limit exceeded.

    ``retry_after`` is the server's ``Retry-After`` in seconds when present.
    The client retries these automatically unless ``max_retries=0``; seeing
    one means the retry budget was exhausted.
    """

    def __init__(self, *args: Any, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class ServerError(APIError):
    """5xx. Retried automatically for idempotent requests."""


# Slug → exception. Slugs are the trailing segment of the problem `type` URI
# and mirror `type_slug` values on the server side.
_BY_SLUG: dict[str, type[APIError]] = {
    "unauthenticated": AuthenticationError,
    "token-expired": TokenExpiredError,
    "insufficient-credits": InsufficientCreditsError,
    "not-found": NotFoundError,
    "conflict": ConflictError,
    "validation-error": ValidationError,
    "rate-limit-exceeded": RateLimitError,
}

# Fallback when the body is missing or unparseable — a proxy 502, say.
_BY_STATUS: dict[int, type[APIError]] = {
    401: AuthenticationError,
    402: InsufficientCreditsError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
    429: RateLimitError,
}


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        # The HTTP-date form is legal but the API always sends seconds; if
        # that ever changes, fall back to the caller's backoff rather than
        # guessing at a date parse.
        return None


def build_error(
        status_code: int,
        body: Any,
        headers: Mapping[str, str],
) -> APIError:
    """Turn a non-2xx response into the most specific exception available."""
    problem: Mapping[str, Any] = body if isinstance(body, dict) else {}

    problem_type = problem.get("type")
    slug = problem_type.rstrip("/").rsplit("/", 1)[-1] if isinstance(problem_type, str) else None

    cls = _BY_SLUG.get(slug or "") or _BY_STATUS.get(status_code)
    if cls is None:
        cls = ServerError if status_code >= 500 else APIError

    detail = problem.get("detail")
    title = problem.get("title")
    message = detail or title or f"HTTP {status_code}"

    kwargs: dict[str, Any] = dict(
        status_code=status_code,
        problem_type=problem_type if isinstance(problem_type, str) else None,
        title=title,
        detail=detail,
        instance=problem.get("instance"),
        request_id=problem.get("request_id") or headers.get("X-Request-Id"),
        errors=problem.get("errors"),
        headers=headers,
    )
    if cls is RateLimitError:
        kwargs["retry_after"] = _retry_after_seconds(headers)
    return cls(message, **kwargs)
