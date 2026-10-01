# Changelog

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
