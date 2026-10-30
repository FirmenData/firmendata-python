# Changelog

## 1.3.0

Regenerated from the updated API contract. **Additive, non-breaking**, except
where noted for `get_financials()`.

**Coverage and fields**

- Austria: `country="AT"`, the Austrian register type (`AT-FB`), Firmenbuch
  courts in `register_court`, the Austrian states in `bundesland`, and
  Austrian legal forms.
- Optional `country` (`DE`, `CH` or `AT`) on autocomplete in both clients;
  autocomplete hits include `registered_seat`.
- Company contact details include `phones` (phone and fax numbers) and
  `primary_email`.
- Financial summaries include `latest_equity`; each year in
  `history["metrics"]` carries its top-level `balance_sheet` and
  `income_statement` positions.
- Error messages fall back to the problem's new `message` field.

**New methods**

- Beta `feed_new_companies(**filters)` and `feed_insolvencies(**filters)`
  methods on sync and async clients, with generated filter and response types.
  Both accept `register_court` (German Registergericht or Austrian Firmenbuch
  court names, repeated values combined with OR); on the insolvency feed it
  filters by the company's register court, not the insolvency court.
- `iter_feed_new_companies` and `iter_feed_insolvencies` drain available pages
  while `has_more` is true, deduplicate event IDs within each run, and expose
  `.next_cursor` after fully consumed pages for resuming, including empty pages.
  Async variants use `async for`.
- `get_lists()` and `get_list(list_id, limit=, cursor=, sort=, sort_direction=)`
  on both clients read your company lists. Both are free.
- `iter_search(..., max_results=None)` on both clients yields every search hit,
  following `next_cursor` with only the cursor and `limit` after the first page,
  and stops at `has_more=False`, `max_results` or 10,000 results. Each page of
  up to 50 results costs 1 credit.
- `create_subscription(**body)` and `test_delivery(**body)` are typed from the
  contract (`SubscriptionCreateRequest`, `SubscriptionTestRequest`) instead of
  `**body: Any`.

**Financials**

- `get_financials()` takes `details`. The API now returns the full line items
  by default; pass `details=False` for the lean response. `include_line_items`
  is deprecated: it emits a `DeprecationWarning` and is sent as `details` (it
  no longer sends `include=line_items`). Calls that passed nothing now receive
  the full line items, as the API does.

**Client behaviour**

- Every request sends `X-FirmenData-Client: python-sdk/<version>`; the
  `User-Agent` stays `firmendata-python/<version>`. The new
  `app_info={"name": ..., "version": ..., "url": ...}` option appends your
  integration to it.
- New `on_response` option: called for every HTTP response with a
  `ResponseInfo` (`status`, `headers`, `request_id`, `credits_charged`). The
  async client also awaits a coroutine callback.
- Realtime calls (`fetch_realtime=True`), document listings and downloads are
  no longer retried after a timeout, a gateway timeout or a connection that
  broke after the request was sent — the server bills them when it finishes,
  so a retry could be billed twice. Their timeout is at least 150 seconds
  (`REALTIME_TIMEOUT`) unless the call passes its own `timeout`, which the
  live-capable methods now accept.
- A `429` whose `Retry-After` exceeds 20 seconds raises `RateLimitError` at
  once, with `retry_after` in seconds, instead of waiting 20 seconds per retry
  and failing anyway.
- The generator toolchain (`datamodel-code-generator`, `black`) is pinned in
  the `dev` extra; CI also runs on Python 3.14.

## 1.2.1

Regenerated from the updated API contract. **Additive, non-breaking:**

- Search filter `company_size` now includes `kleinst`
  (Kleinstkapitalgesellschaft) alongside `klein` and `mittelgross`.
  Its description now references § 267 / § 267a HGB.

## 1.2.0

Regenerated from API contract 1.2.0. **Additive, non-breaking:**

- Required `country_code` (`DE` or `CH`) on company detail, search hits
  (including list rows) and autocomplete hits.
- Search filters `country` (single country) and `canton` (multiple Swiss
  canton codes, OR-merged with `bundesland` and `city`); `rechtsform` includes
  Swiss legal forms. Search hits include `registered_seat`, and `sort="name"`
  defaults to ascending.
- `list_documents(eu_id)` on both sync and async clients checks the registry
  live and returns a generated `CompanyDocumentList`, including older DK
  versions, labels, dates, `is_latest`, stored-copy metadata, `is_outdated`,
  `coverage`, `freshness` and `country_code`. Costs 5 credits; Swiss, empty
  and registry-unreachable answers are unbilled.
- `download_document()` accepts `document_id` (`doc_<int>`) from the list for
  a specific DK version, with matching `file_type`. It cannot be combined
  with `file_id` or `fetch_realtime=True`. Download responses include
  `document_id` and `label`.

## 1.0.0

Regenerated from API contract 1.1.0. **Breaking:**

- `CompanyDetail["status"]` is now `legal_status`.
- `CompanyFinancials["id"]` is now `eu_id`.
- `SubscriptionList` and `SubscriptionEventList` return `data` (was `items`)
  plus the standard `pagination` object; `list_events()` takes a `cursor`.
- `get_financials()` is lean by default: pass `include_line_items=True` for the
  P&L / balance-sheet rows; `years` limits the history. Subsidiaries are capped
  at 25 (`subsidiaries_total` has the count).
- `CompanyHistory` uses English keys and ISO dates (see the API reference).
- `APIError.errors` entries are `{"param", "message"}` (were free-form).
- Cursors are signed: pass `next_cursor` back unchanged.
- The API now rejects unknown parameters, unknown values and inverted ranges
  with `ValidationError`.
- `execution_time_ms` is an integer; `Wz2025Score` `score` is 0–1.

New fields include `is_branch`, `register_canton`, `uid` (Swiss companies),
`is_outdated` on documents, and the latest revenue, profit and headcount with
their years in `financial_summary`. Responses carry `X-Credits-Charged`;
answers without data (e.g. no cap table on file) cost no credits.
