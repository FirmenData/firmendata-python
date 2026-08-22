# firmendata-python

Official Python client for the [firmendata](https://firmendata.com) API — data on
**2.4 million German companies** from the Unternehmensregister and Handelsregister:
register search, parsed annual financial statements, company profiles,
register documents, and ownership chains for KYC.

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

Keyless calls are rate limited, modestly and by address — enough to try the
API, back a search box, or run low-volume queries. Add a key for substantially
higher limits plus every other endpoint. On a `429`, honour `Retry-After`;
the client already does this for you.

## With an API key

Create one at [firmendata.com](https://firmendata.com/de/account/api-keys) — the
free plan includes 100 credits.

```python
from firmendata import FirmenData

fd = FirmenData(api_key="firmendata_live_...")
```

## Search the register

The main entry point: 37 filters over all 2.4 million companies. Different
filters combine with AND, repeated values with OR.

```python
results = fd.search(
    bundesland=["Bayern", "Baden-Württemberg"],
    industry_slug=["manufacturing"],
    total_assets_min=1_000_000,
    legal_status=["active"],
    sort="total_assets",
    limit=25,
)

for hit in results["data"]:
    print(hit["display_name"], hit["address"]["city"], hit["total_assets"])

if results["pagination"]["has_more"]:
    next_page = fd.search(cursor=results["pagination"]["next_cursor"])
```

Values are case-insensitive and tolerate German spelling both ways — `gmbh`,
`muenchen`, `NRW` and `Bavaria` all resolve. Filter by legal form, legal status,
register court, federal state, city, industry, founding date, size, web
presence, connected person or EU public-procurement role; see the
[filter reference](https://api.firmendata.com/v1/docs#tag/Search).

> **Filtering on size? Use `total_assets`, not `revenue`.** Small and
> medium-sized German companies file abridged accounts — a balance sheet, but
> no profit-and-loss statement and no headcount. A revenue or employee bound
> therefore narrows your results to the minority that publish a full P&L,
> while the balance-sheet total is available for every filing company.

## Company profile

```python
eu_id = "DEB1103R_HRB123456"  # from search or autocomplete

company = fd.get_company(eu_id)
history = fd.get_history(eu_id)   # chronological register entries
```

Identity and seat, register reference, legal status resolved from the merged
Handelsregister and Insolvenzbekanntmachungen timelines, industry
classification, contact details and web presence.

## Financial statements

Filed annual accounts, parsed into figures rather than handed to you as PDFs.
The deepest part of the dataset: German companies must publish, and we parse
what they file into structured multi-year figures.

```python
financials = fd.get_financials(eu_id)

summary = financials["summary"]
if summary:
    print(summary["latest_fiscal_year"], summary["latest_total_assets"])

for year in financials["history"]["metrics"]:
    print(year["year"], year["balance_sheet_total"], year["revenue"], year["profit"])
```

`history` also carries the structured `profit_and_loss`, `assets` and
`liabilities_and_equity` rows as filed, plus `employee_history` and the
underlying `financial_publications`.

`summary` is `None` when nothing is on file. Within it, figures resolve to the
most recent filing that actually carries each one, so revenue and profit can
come from *different* fiscal years — don't assume two share a year when
computing a ratio.

## Documents

```python
doc = fd.download_document(eu_id, file_type="CD")
```

Aktueller and Chronologischer Abdruck, Gesellschafterliste, Satzung, Anmeldung
and Musterprotokoll, as presigned download URLs.

## Ownership: shareholders and UBO

Cap tables and beneficial-owner chains, for **KYC and AML workflows**.

**Pass `fetch_realtime=True` on both of these.** Unlike the endpoints above,
which read an index we keep continuously fresh, cap tables are parsed from the
filed Gesellschafterliste on demand — the flag fetches and parses the current
filing for the company (and, for `get_ubo`, every German company in its
ownership chain). Without it you are limited to whatever has already been
parsed, and will often get `not_filed` for a company that has in fact filed.
It costs more credits and takes a few seconds per company in the chain, which
is the right trade for a KYC check.

```python
cap = fd.get_shareholders(eu_id, fetch_realtime=True)

if cap["coverage"]["status"] == "available":
    for s in cap["as_of_snapshot"]["shareholders"]:
        print(s["display_name"], s["share_percent"])

ubo = fd.get_ubo(eu_id, fetch_realtime=True)
print(ubo["coverage"]["status"], ubo["beneficial_owners"])
```

**What limits these:**

- **Only GmbH, UG and gGmbH file a Gesellschafterliste.** For an AG, KG, e.K.
  or any other form there is no cap table to read and `coverage["status"]` is
  `not_applicable` — not an error, and not something a retry will fix.
- **Without `fetch_realtime`, `not_filed` does not mean "never filed."** It
  means no parsed cap table is on hand for that company yet. Re-request with
  the flag before concluding anything about a company's ownership.
- **Always branch on `coverage["status"]`**, never on an empty list. The two
  endpoints have different vocabularies:
  `get_shareholders` → `available` | `not_filed` | `not_applicable` |
  `token_limit_reached` (the filing was too large to parse);
  `get_ubo` → `available` | `partial` | `not_filed` | `not_applicable`.
  **`partial` is the one to handle**: an unresolved branch could still hide a
  beneficial owner, so read `potential_beneficial_owners` and
  `coverage["reason"]` rather than treating the result as complete.
- **An empty `beneficial_owners` is a real answer**, not a failure: it means
  nobody crosses the 25% threshold. Fictional UBO under §3 Abs. 2 S. 5 GwG is
  not surfaced.
- **Attribution is all-or-nothing, not multiplicative.** A holds 60% of H and H
  holds 30% of the root → A is a UBO at **30%**, not 18%. Each link is
  independently tested against the 25% threshold, so a sub-threshold link
  breaks the chain entirely.

## Subscriptions

Get notified when a company's data changes, instead of polling:

```python
sub = fd.create_subscription(
    eu_id=eu_id,
    subscription_type="shareholders",  # or details, history, ubo, doc_*
    cadence="weekly",                  # immediately | daily | weekly | monthly
    notification_type="webhook",
    webhook_url="https://example.com/hooks/firmendata",
)
```

Webhook bodies are HMAC-SHA256 signed — verify `X-Firmendata-Signature` against
the secret returned at creation. Omit `notification_type` to poll
`list_events()` on your own schedule instead.

## Async

Same methods, same semantics — every method above has an `await`able twin:

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
- TypeScript SDK — <https://github.com/FirmenData/firmendata-node>
- n8n node — <https://github.com/FirmenData/n8n-nodes-firmendata>
- MCP server (for AI agents) — `https://mcp.firmendata.com/mcp`
- Website — <https://firmendata.com>

## License

MIT — see [LICENSE](LICENSE).
