"""Retry policy, kept as pure functions so sync and async share one brain.

Two rules do the real work:

* **429 is always safe to retry, on any method.** The server rejects a
  rate-limited call *before* the handler runs — nothing happened, nothing was
  billed. Its ``Retry-After`` is authoritative and is used verbatim.

* **5xx and connection failures are retried only for idempotent methods.**
  A ``POST /v1/subscriptions`` that fails with a 502 may well have created the
  subscription before the proxy gave up; retrying it would create a second
  one. GET and DELETE can be repeated safely, POST cannot.

Backoff is exponential with **full jitter** (``random(0, base * 2**n)``)
rather than a fixed ramp: when many clients trip the same limit at once, an
undithered backoff simply reconvenes them all at the same instant.
"""

from __future__ import annotations

import random

#: Methods that may be replayed after an ambiguous failure.
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

DEFAULT_MAX_RETRIES = 2
_BASE_DELAY_SECONDS = 0.5
_MAX_DELAY_SECONDS = 20.0


def should_retry(
        *,
        method: str,
        status_code: int | None,
        attempt: int,
        max_retries: int,
) -> bool:
    """Whether to make attempt ``attempt + 1``.

    ``status_code`` is ``None`` for a transport failure (no response).
    """
    if attempt >= max_retries:
        return False

    idempotent = method.upper() in IDEMPOTENT_METHODS

    if status_code is None:          # connection reset, timeout, DNS
        return idempotent
    if status_code == 429:           # never executed — always replayable
        return True
    if status_code == 408:           # server-side timeout, same reasoning as 5xx
        return idempotent
    if status_code >= 500:
        return idempotent
    return False


def backoff_seconds(
        attempt: int,
        *,
        retry_after: float | None = None,
        rng: random.Random | None = None,
) -> float:
    """Delay before the next attempt. ``attempt`` is 0-based.

    A server-supplied ``Retry-After`` wins outright — it knows when the window
    actually resets, and guessing shorter just burns another rejection.
    """
    if retry_after is not None and retry_after >= 0:
        return min(retry_after, _MAX_DELAY_SECONDS)
    ceiling = min(_BASE_DELAY_SECONDS * (2 ** attempt), _MAX_DELAY_SECONDS)
    return (rng or random).uniform(0.0, ceiling)
