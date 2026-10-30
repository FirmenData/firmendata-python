# firmendata-python

Official Python client for the [firmendata](https://firmendata.com) API — data on
German, Swiss and Austrian companies from the Unternehmensregister,
Handelsregister, the Swiss commercial register and the Austrian Firmenbuch:
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

Optionally pass `country="DE"`, `"CH"` or `"AT"` to restrict autocomplete
to one company register; omitting it searches all countries.

Keyless calls are rate limited, modestly and by address — enough to try the
API or run low-volume queries. Add a key for substantially higher limits plus
every other endpoint. On a `429`, honour `Retry-After`; the client already
does this for you (see [Retries](#retries)).

## With an API key

Create one at [firmendata.com](https://firmendata.com/de/developer/api-keys) — the
free plan includes 100 credits.

```python
from firmendata import FirmenData

fd = FirmenData(api_key="firmendata_live_...")
```

## Search the register

The main entry point: search German, Swiss and Austrian companies. Different
filters combine with AND, repeated values with OR; `city`, `bundesland` and
`canton` combine into one location filter with OR.

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

Pass `next_cursor` back unchanged — cursors are opaque and signed, and an
edited one is rejected. A cursor remembers the filters it was issued for, so
it can be sent alone. Unknown filter names, unknown values and inverted
ranges (`revenue_min` above `revenue_max`) raise `ValidationError` instead of
being ignored.

`iter_search()` does the paging for you: it yields every hit, sends only the
cursor (and `limit`) after the first page, and stops when `has_more` is false,
after `max_results` hits, or at the 10,000 results a query can return. **Each
page of up to 50 results costs 1 credit**, like a `search()` call — set
`max_results`, or stop iterating, to stop paying for pages you do not need.

```python
for hit in fd.iter_search(bundesland=["Bayern"], max_results=500):
    print(hit["eu_id"], hit["display_name"])
```

Values are case-insensitive and tolerate German spelling both ways — `gmbh`,
`muenchen`, `NRW` and `Bavaria` all resolve. Filter by legal form, legal status,
register court, federal state, city, industry, founding date, size, web
presence, connected person or EU public-procurement role; see the
[filter reference](https://api.firmendata.com/v1/docs#tag/Search).

Use `country="DE"`, `"CH"` or `"AT"` for a single country and
`canton=["ZH", "BE"]` for Swiss cantons; `bundesland` also takes the Austrian
states (`"Wien"`, `"Tirol"`, …). `rechtsform` includes Swiss and Austrian
forms such as `"AG (CH)"`, `"GmbH (CH)"` and `"GmbH (AT)"`, and
`register_court` the Austrian Firmenbuch courts (`"Handelsgericht Wien"`, …).
`sort="name"` defaults to ascending. Search and autocomplete hits include
`registered_seat`; company profiles, search and autocomplete hits include
`country_code` (`"DE"`, `"CH"` or `"AT"`).

> **Filtering on size? Use `total_assets`, not `revenue`.** Small and
> medium-sized German companies file abridged accounts — a balance sheet, but
> no profit-and-loss statement and no headcount. A revenue or employee bound
> therefore narrows your results to the minority that publish a full P&L,
> while the balance-sheet total is available for every filing company.

## Company profile

```python
eu_id = "DEF1103R.HRB12300B"  # from search or autocomplete

company = fd.get_company(eu_id)
history = fd.get_history(eu_id)   # chronological register entries
```

Identity and seat, register reference, legal status from the commercial
register and insolvency notices, industry classification, contact details
(including `phones` and `primary_email`) and web presence.

## Financial statements

Filed annual accounts, parsed into figures rather than handed to you as PDFs.
The deepest part of the dataset: companies in Germany and Austria must publish
their accounts, and we parse what they file into structured multi-year
figures.

```python
financials = fd.get_financials(eu_id)

summary = financials["summary"]
if summary:
    print(summary["latest_fiscal_year"], summary["latest_total_assets"],
          summary["latest_equity"])

for year in financials["history"]["metrics"]:
    print(year["year"], year["balance_sheet_total"], year["revenue"], year["profit"])
```

By default the response carries every filed `profit_and_loss`, `assets` and
`liabilities_and_equity` row at every depth — often 100 KB or more for
groups. Pass `details=False` for the lean response: those lists stay empty,
and each year in `history["metrics"]` keeps its top-level `balance_sheet` and
`income_statement` positions. Both cost the same. `years` trims every
per-year array to the most recent fiscal years:

```python
lean = fd.get_financials(eu_id, details=False, years=5)
```

`include_line_items` from earlier versions still works but is deprecated: it
emits a `DeprecationWarning` and is sent as `details`.

`employee_history` and the underlying `financial_publications` are always
included; `relationships["subsidiaries"]` lists the first 25, with
`subsidiaries_total` for the count.

`summary` is `None` when nothing is on file. Within it, figures resolve to the
most recent filing that actually carries each one, so revenue and profit can
come from *different* fiscal years — don't assume two share a year when
computing a ratio.

## Documents

```python
documents = fd.list_documents(eu_id)
doc = fd.download_document(eu_id, file_type="register_extract_current")

for item in documents["data"]:
    if item.get("document_id") and not item["is_latest"]:
        older = fd.download_document(
            eu_id, file_type=item["type"], document_id=item["document_id"],
        )
        break
```

Aktueller and Chronologischer Abdruck, Gesellschafterliste, Satzung, Anmeldung
and Musterprotokoll, as presigned download URLs.

`list_documents()` checks the registry live and returns current and older
versions with labels, dates, `is_latest`, stored-copy metadata, `coverage`
and `freshness`. It costs 5 credits; Swiss, empty and registry-unreachable
answers are unbilled. Swiss companies return an empty list with
`coverage["status"] == "not_applicable"`.

Pass a `doc_<int>` identifier from the list as `document_id` with its matching
`file_type` to download a specific version. Omit it for the latest version.
It cannot be combined with `file_id` or `fetch_realtime=True`. Register
extracts have no `document_id`; download them by `file_type`. Download
responses include `document_id` and the registry `label` when available.

Document listings and downloads are live calls — see
[Realtime calls](#realtime-calls).

## Ownership: shareholders and UBO

Cap tables and beneficial-owner chains, for **KYC and AML workflows**.

**Pass `fetch_realtime=True` on both of these.** Unlike the endpoints above,
which answer from data we keep continuously up to date, cap tables are parsed
from the filed Gesellschafterliste on demand — the flag fetches and parses the
current filing for the company (and, for `get_ubo`, every German company in its
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

## Realtime calls

Calls with `fetch_realtime=True` (`autocomplete`, `search`, `get_company`,
`get_shareholders`, `get_ubo`, `get_history`, `download_document`),
`list_documents` and every `download_document` reach the registers live.
Realtime calls cost more credits (see
[pricing](https://firmendata.com/en/plans)) and can take up to about two
minutes. They are **billed when the server finishes them, even if your client
has already given up**, so the client treats them differently:

- It waits at least 150 seconds (`firmendata.REALTIME_TIMEOUT`) for them, or
  your client `timeout` if that is longer. A `timeout` passed to the call
  itself always wins: `fd.get_ubo(eu_id, fetch_realtime=True, timeout=60)`.
- It **never retries one after a timeout**, or after a connection that broke
  once the request was sent — the retry could be billed a second time. It
  raises `APITimeoutError` (or `APIConnectionError`) instead; the call may
  still have completed and been billed.

## Event feeds (Beta)

Poll newly registered companies with `feed_new_companies(**filters)` or
insolvency notices with `feed_insolvencies(**filters)`. Both require an API
key and return `data` (a list of `FeedEvent` dictionaries), `next_cursor`
(always present) and `has_more` at the top level.

```python
page = fd.feed_new_companies(country="DE", limit=50)
for event in page["data"]:
    print(event["id"], event["company"].get("name"))

next_cursor = page["next_cursor"]  # persist this, even when data is empty
page = fd.feed_new_companies(country="DE", cursor=next_cursor, limit=50)
```

Pass `next_cursor` back unchanged with the same filters. When `has_more` is
true, request the next page immediately; otherwise poll again later. On the
first call only, `since` accepts an ISO datetime within the last seven days;
omitting it starts from the last day. `limit` is 1–100 and defaults to 50.
Cursors survive restarts, but positions older than seven days expire; restart
with a recent `since`. Each page with events costs 1 credit; empty pages are
free. Events arrive in the order they become available, after a short
settlement delay.

Both feeds accept `country` (`DE`, `CH` or `AT`), `bundesland`, `canton`,
`register_court`, `industry_slug` and `rechtsform`. List filters repeat query
keys and match with OR. `register_court` takes German Registergericht or
Austrian Firmenbuch court names (the same values as search); select Swiss
companies with `canton`. On the insolvency feed it is the company's register
court, not the insolvency court. Insolvencies default to `country="DE"` and
also accept `category`, `revenue_min` (inclusive) and `revenue_max`
(exclusive). Swiss new-company events cover newly registered companies in the
Swiss commercial register; Swiss Schuldenruf notices require an explicit
category selection:

```python
page = fd.feed_insolvencies(country="CH", canton=["ZH"], category=["Schuldenruf"])
```

Use `iter_feed_new_companies` or `iter_feed_insolvencies` to drain available
pages automatically. They stop when `has_more` is false and expose
`.next_cursor` for the last fully consumed page, including empty pages:

```python
events = fd.iter_feed_new_companies(country="DE", limit=100)
for event in events:
    print(event["id"], event["company"].get("name"))

next_cursor = events.next_cursor  # persist after processing all events
assert next_cursor is not None   # available after successful exhaustion
# On the next scheduled poll:
events = fd.iter_feed_new_companies(country="DE", cursor=next_cursor, limit=100)
```

The cursor starts at the supplied cursor (or `None`) and advances when
iteration continues past a page's events. If processing stops partway through
a page, the checkpoint stays at that page's starting position so it can be
replayed. Iterators deduplicate event `id` values within one run; persist
processed IDs across polls and restarts too, because the same event can
appear again in a later run. Save a cursor only after successfully processing
its page's events.

Async clients have the same page methods with `await`. Create async feed
iterators without `await`, consume them with `async for`, then save their
`.next_cursor`:

```python
from firmendata import AsyncFirmenData

async with AsyncFirmenData(api_key="firmendata_live_...") as fd:
    events = fd.iter_feed_insolvencies(country="DE")
    async for event in events:
        print(event["id"], event["company"].get("name"))
    next_cursor = events.next_cursor
```

## Lists

Company lists you build in the web app are readable through the API, for
free:

```python
lists = fd.get_lists()  # newest first
for entry in lists["data"]:
    print(entry["list_id"], entry["name"], entry.get("company_count"))

page = fd.get_list(lists["data"][0]["list_id"], limit=100, sort="revenue")
for row in page["data"]:
    print(row["eu_id"], row["display_name"])  # same shape as search hits
```

`get_list` pages like search: pass `page["pagination"]["next_cursor"]` back as
`cursor`. `sort` orders the whole list before the page is cut. A list id that
does not exist or is not yours raises `NotFoundError`.

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

Check your receiver before subscribing with `fd.test_delivery(eu_id=eu_id,
subscription_type="details", notification_type="webhook", webhook_url=...)`,
and deliver a past event again with `fd.resend_event(event_id)`. Both bodies
are typed (`SubscriptionCreateRequest`, `SubscriptionTestRequest`), so `mypy`
and editors flag a misspelt field.

## Async

Same methods, same semantics — request methods have an `await`able twin,
and feed and search iterators use `async for`:

```python
import asyncio
from firmendata import AsyncFirmenData

async def main():
    async with AsyncFirmenData(api_key="firmendata_live_...") as fd:
        company = await fd.get_company("DEF1103R.HRB12300B")
        print(company["display_name"])

        async for hit in fd.iter_search(q="siemens", max_results=100):
            print(hit["eu_id"])

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
    ...                      # e.retry_after: seconds to wait, from the server
```

| Exception | Status | Meaning |
|---|---|---|
| `AuthenticationError` | 401 | Missing/invalid key, or a keyless call used a paid feature |
| `TokenExpiredError` | 401 | Key expired |
| `InsufficientCreditsError` | 402 | Balance too low for this call |
| `NotFoundError` | 404 | No such company, list, subscription or event |
| `ConflictError` | 409 | Conflicts with existing state |
| `ValidationError` | 422 | Bad parameters — see `.errors` (`{"param", "message"}` each) |
| `RateLimitError` | 429 | Retries exhausted, or a long `Retry-After` — see `.retry_after` |
| `ServerError` | 5xx | Retried automatically for idempotent calls |
| `APIConnectionError` / `APITimeoutError` | — | No response at all |

### Retries

Automatic and deliberately conservative:

- **429 is retried on any method** — the server rejects rate-limited calls
  before the handler runs, so nothing happened and nothing was billed. The
  client waits for the server's `Retry-After`, up to 20 seconds. When the
  server asks for longer — the keyless daily limit can ask for hours — the
  `RateLimitError` is raised at once, with `retry_after` in seconds, instead of
  holding your call open.
- **5xx and connection failures are retried only for idempotent methods.** A
  `create_subscription` that times out may already have been applied; replaying
  it would create a second one.
- **Realtime calls are never retried once they may have reached the server**
  — see [Realtime calls](#realtime-calls).
- Backoff is exponential with full jitter, so clients that trip the same limit
  together don't all return at the same instant.

Tune with `FirmenData(max_retries=...)`; `0` disables it.

## Credits and response metadata

Successful responses carry `X-Credits-Charged`, `X-Request-Id` and the
`RateLimit-*` headers. Pass `on_response` to see them: it is called for every
HTTP response — successes, errors, and responses the client then retries —
with a `ResponseInfo` holding `status`, `headers` (case-insensitive),
`request_id` and `credits_charged`.

```python
from firmendata import FirmenData, ResponseInfo

credits_used = 0

def track(info: ResponseInfo) -> None:
    global credits_used
    credits_used += info.credits_charged or 0
    print(info.status, info.request_id, info.headers.get("RateLimit-Remaining"))

fd = FirmenData(api_key="firmendata_live_...", on_response=track)
fd.search(q="siemens")
print("credits used so far:", credits_used)
```

An exception raised in the callback reaches the caller of the method. On
`AsyncFirmenData` the callback may also be an `async def`; the client awaits
it.

## Configuration

```python
FirmenData(
    api_key="...",            # optional — omit for the free autocomplete tier
    base_url="...",           # default https://api.firmendata.com
    timeout=30.0,             # per attempt; realtime calls wait at least 150
    max_retries=2,
    app_info={"name": "acme-crm", "version": "2.1.0", "url": "https://acme.example"},
    on_response=track,
    http_client=None,         # bring your own httpx.Client (AsyncClient for async)
)
```

Every request sends `X-FirmenData-Client: python-sdk/<version>` and the
`User-Agent` `firmendata-python/<version>`. `app_info` identifies your
integration by appending ` name/version (url)` to it.

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

CI regenerates them with the toolchain pinned in the `dev` extra and fails if
the result differs from what is committed, so the SDK cannot silently drift
from the API it targets.

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
