# firmendata-python

Official Python client for the [firmendata](https://firmendata.com) API — data on
**2.4 million German companies** from the Unternehmensregister and Handelsregister:
register profiles, parsed annual financial statements, shareholder cap tables,
UBO chains, insolvency notices and public-tender links.

[![PyPI](https://img.shields.io/pypi/v/firmendata)](https://pypi.org/project/firmendata/)
[![Python](https://img.shields.io/pypi/pyversions/firmendata)](https://pypi.org/project/firmendata/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

```bash
pip install firmendata
```

## Try it without signing up

Company-name autocomplete is free and needs **no API key**:

```python
from firmendata import FirmenData

for hit in FirmenData().autocomplete("siemens")["data"]:
    print(hit["eu_id"], hit["display_name"])
```

Keyless calls are limited to 1 request/second, 30/minute and 1000/day per IP.
Add a key and you get the standard account limits (5/s, 100/min) plus every
other endpoint.

## With an API key

Create one at [firmendata.com](https://firmendata.com/de/account/api-keys) — the
free plan includes 100 credits.

```python
from firmendata import FirmenData

fd = FirmenData(api_key="firmendata_live_...")

# Advanced search — filters combine with AND, lists with OR
results = fd.search(
    city=["Berlin", "Hamburg"],
    revenue_min=1_000_000,
    legal_status=["insolvent"],
    limit=25,
)

for hit in results["data"]:
    print(hit["display_name"], hit["address"]["city"])

# Paginate
if results["pagination"]["has_more"]:
    next_page = fd.search(cursor=results["pagination"]["next_cursor"])
```

```python
eu_id = "DEB1103R_HRB123456"

fd.get_company(eu_id)        # full profile
fd.get_financials(eu_id)     # multi-year statements, parsed into figures
fd.get_shareholders(eu_id)   # cap table from the Gesellschafterliste
fd.get_ubo(eu_id)            # beneficial owners through ownership chains
fd.get_history(eu_id)        # chronological register history
```

### Async

Same methods, same semantics:

```python
import asyncio
from firmendata import AsyncFirmenData

async def main():
    async with AsyncFirmenData(api_key="firmendata_live_...") as fd:
        company = await fd.get_company("DEB1103R_HRB123456")
        print(company["display_name"])

asyncio.run(main())
```

## Errors

Every failure is a typed exception carrying the API's RFC 7807 problem detail,
including a `request_id` you can quote to support.

```python
from firmendata import FirmenData, InsufficientCreditsError, RateLimitError

try:
    fd.get_ubo(eu_id)
except InsufficientCreditsError:
    ...                      # top up or upgrade
except RateLimitError as e:
    ...                      # e.retry_after is the server's own hint
```

| Exception | Status | Meaning |
|---|---|---|
| `AuthenticationError` | 401 | Missing/invalid key, or a keyless call used a paid feature |
| `TokenExpiredError` | 401 | Key expired |
| `InsufficientCreditsError` | 402 | Balance too low for this call |
| `NotFoundError` | 404 | No such company, subscription or event |
| `ConflictError` | 409 | Conflicts with existing state |
| `ValidationError` | 422 | Bad parameters — see `.errors` for the fields |
| `RateLimitError` | 429 | Retry budget exhausted — see `.retry_after` |
| `ServerError` | 5xx | Retried automatically for idempotent calls |
| `APIConnectionError` / `APITimeoutError` | — | No response at all |

### Retries

Automatic and deliberately conservative:

- **429 is always retried**, on any method — the server rejects rate-limited
  calls before the handler runs, so nothing happened and nothing was billed.
  The server's `Retry-After` is used verbatim.
- **5xx and connection failures are retried only for idempotent methods.** A
  `create_subscription` that times out may already have been applied; replaying
  it would create a second one.
- Backoff is exponential with full jitter, so clients that trip the same limit
  together don't all return at the same instant.

Tune with `FirmenData(max_retries=...)`; `0` disables it.

## Types

Responses are plain dictionaries described by generated `TypedDict`s, so editors
complete every field and `mypy` checks them — with **no pydantic dependency** to
collide with your own. The only runtime requirement is `httpx`.

Both `src/firmendata/types.py` and `src/firmendata/params.py` are generated from
[`contracts/openapi.v1.json`](contracts/openapi.v1.json), a vendored copy of the
published spec:

```bash
python scripts/generate_types.py
```

CI regenerates them and fails if the result differs from what is committed, so
the SDK cannot silently drift from the API it targets.

## Development

```bash
pip install -e '.[dev]'
pytest              # no network, no credentials
mypy && ruff check
```

## Links

- API reference — <https://api.firmendata.com/v1/docs>
- MCP server (for AI agents) — `https://mcp.firmendata.com/mcp`
- Website — <https://firmendata.com>

## License

MIT — see [LICENSE](LICENSE).
