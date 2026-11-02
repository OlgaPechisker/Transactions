# REST API contract – UI ↔ backend

Status: **current V1 planning contract; approved policies synchronized**. Machine-readable version: [`openapi.yaml`](openapi.yaml). Data mapping: [`screen-to-data-map.md`](screen-to-data-map.md). Architecture: [`Phase 1`](../api-design-phase-1.md).

This is the canonical API proposal, not a decision-history document. New persistence is required for users, ownership claims, sessions, trips, categories, and currency-validation audit.

**Planning boundary:** approved policies and field shapes are reflected here and in OpenAPI. The reviewed wire-contract gaps are closed. Exact provider interfaces, DDL, and implementation plans remain gated.

**Out of scope for v1:** sharing between users (invitations, "Shared with me", merged view, view-mode preference). Every endpoint returns and changes only the caller's own data.

---

## 1. Conventions

### 1.1 Base URL and versioning
`/api/v1`. All paths below are relative to it. Breaking changes require a new major version.

### 1.2 Authentication
- Users are provisioned administratively; no self-service registration endpoint. Store Argon2id password hashes. Trim login email and match it case-insensitively; preserve passwords exactly.
- Planned HTTP stack: FastAPI + Uvicorn. Services and database providers remain synchronous and must not block the ASGI event loop.
- `POST /sessions` exchanges credentials for a session. The session is returned both as an `HttpOnly; Secure; SameSite=Lax` cookie and as a bearer token (`Authorization: Bearer <token>`) for non-browser clients.
- **Every endpoint except `POST /sessions` requires authentication.** Missing or invalid credentials → `401`.
- Requests that change state with cookie auth must carry an `X-CSRF-Token` header (value from `GET /me`).
- Sessions expire after 12 absolute hours, with no sliding extension. Store token digests, not raw tokens.
- Bearer credentials take precedence over cookies. Invalid bearer credentials return `401` without cookie fallback. Cookie-only mutations with missing/invalid CSRF return `403 CSRF_VALIDATION_FAILED`.
- After 10 failed sign-ins in 15 minutes, throttle independently by normalized email and client IP with `429`. State is shared across workers; no permanent account lockout.

### 1.3 Data visibility
- A user sees only cards associated with their ownership claims and those cards' transactions. Statement-holder names are metadata, not authorization.
- Creating a card claims issuer + encrypted canonical last-four globally for the app user. Another user cannot import that key: `409 DATA_ALREADY_OWNED` in dry runs and real imports, without other-user details.
- Issuer/last-four collisions can reject genuinely different cards; this is the accepted V1 policy. File hashes coordinate imports but never establish ownership.
- Anything outside that set returns `404` (existence is never revealed).

### 1.4 Naming and formats
| Item | Convention |
|---|---|
| JSON keys | `camelCase` |
| Paths | plural nouns, kebab-case (`/currency-validation-batches`, `/category-rules`) |
| IDs | opaque strings (do not parse). Internal integer keys are not exposed as-is |
| Dates | `YYYY-MM-DD` (ISO 8601 calendar date) |
| Timestamps | RFC 3339 in UTC, e.g. `2025-03-14T09:21:07Z` |
| Months | `YYYY-MM` |
| "Today" | computed in **UTC** |
| Money | `{"amount": "-123.45", "currency": "ILS"}`. Decimal **string** (2 dp). Preserve source amounts; signed billing values drive analytics. Currency is ISO 4217, or `null` when unknown |
| Rates | nonnegative magnitude as a decimal string, 6 dp, e.g. `"3.712300"`; stored signed rates remain unchanged |
| Countries | ISO 3166-1 alpha-2 codes (`"JP"`). Names are rendered by the frontend i18n dictionary |
| Bank categories | Hebrew bank names only. English labels live in the frontend i18n dictionary |
| Enums | `UPPER_SNAKE_CASE`, matching the domain enums |
| Card numbers | only `last4` is ever returned; full numbers / account numbers are never returned |
| Nullable fields | always present with `null`, never omitted |

### 1.5 Pagination
- Query: `page` (1-based, default 1), `pageSize` (default 50, max 200).
- Response envelope for collections:
```json
{ "items": [ ... ], "meta": { "page": 1, "pageSize": 50, "totalItems": 312, "totalPages": 7 } }
```
- Out-of-range `page` returns an empty `items` array (not an error).

### 1.6 Sorting
`sort` = comma-separated field names; prefix `-` for descending. Example: `sort=-transactionDate,merchant`. Unknown field → `400 INVALID_SORT`. Each endpoint lists its sortable fields.

### 1.7 Filtering
- Simple equality filters use the field name: `cardId=…`.
- **Repeating a parameter means OR** (`currency=USD&currency=EUR`); different parameters are ANDed.
- Ranges use `from`/`to` (dates, inclusive) and `minAmount`/`maxAmount` (signed billing amount, inclusive). Amount ranges require an explicit `billingCurrency`.
- `currency` filters original/purchase currency. `billingCurrency` filters billing currency; lists otherwise include all billing currencies. Never default or convert non-ILS billing to ILS.
- Special values: `none` (no value, e.g. no custom category, no trip) and `UNKNOWN` (unknown original currency).
- Text search is **merchant-only** everywhere (`merchant`, case-insensitive contains, repeatable = OR). The only exception is trip candidates (`q`, §7.4).

### 1.8 Errors
All errors use RFC 9457 `application/problem+json`:
```json
{
  "type": "https://api.example/problems/validation-error",
  "title": "Validation failed",
  "status": 422,
  "code": "VALIDATION_ERROR",
  "detail": "End date must be on or after start date.",
  "instance": "/api/v1/trips",
  "errors": [ { "field": "endDate", "code": "BEFORE_START_DATE", "message": "End date must be on or after start date." } ]
}
```
Common status codes: `400` malformed request / bad query param, `401` not authenticated, `403 CSRF_VALIDATION_FAILED` for cookie-only mutations, `404` not found **or not visible to the caller** (never reveals existence), `409` conflict (duplicate / state), `413` payload too large, `415` unsupported media type, `422` semantic validation failure, `429` rate limited, `500` unexpected.

Error messages never contain card numbers, account numbers or file paths.

### 1.9 Shared shapes
- **CardRef** – `{ "id", "label", "issuer": "ISRACARD|MAX|CAL", "last4", "nickname", "ownerName" }`. `label` is the server-computed display label (e.g. `"Dana · Max ••5530"`).
- **TripRef** – `{ "id", "name", "membership": "TAGGED|AUTO" }`.
- **Category** – `{ "bank": "מזון וצריכה", "custom": "Groceries"|null, "effective": "Groceries", "source": "OVERRIDE|RULE|MAPPING|BANK" }`. Effective category = override › newest matching rule › bank-category mapping › bank category.
- **ReviewReason** – user-visible review reasons. **v1 exposes `UNKNOWN_CURRENCY` only.** `UNMATCHED_FOREIGN_FEE` and `AMBIGUOUS_INSTALLMENT` stay in the database enum (still raised by the parser, for monitoring) but are never returned or accepted by the API.
- **ReviewReasonCount** – `{ "reason": "UNKNOWN_CURRENCY", "count": 4 }`.
- **BillingTotal** – `{ "count": 2, "net": { "amount": "296.00", "currency": "ILS" } }`. One entry per known billing currency, including every positive, negative and zero row in its count. No per-transaction average.
- **BillingSummary** – `{ "count": 3, "unknownBillingCurrencyCount": 0, "billingTotals": [BillingTotal] }`. Used for transaction summaries/groups and trip stats/category/day/outside-range breakdowns. `count` is the all-row count; it equals per-currency counts plus `unknownBillingCurrencyCount`. Missing billing currency never enters monetary sums. Empty sets return `billingTotals: []`; a zero-net currency with rows remains present. Existing `unknownCurrencyCount` means missing foreign original currency and is separate.
- **Missing groups** – transaction/analysis groups and matrix rows use `key: null` for a missing category, custom category, or country. Their fallback labels are `Uncategorized`, `No custom category`, and `Unknown country`, respectively; clients may localize by grouping dimension. Real keys/labels remain unchanged. Trip category groups use `category: null` and `label: "Uncategorized"`. Missing groups retain counts and signed billing totals.

---

## 2. Endpoint index

| # | Method | Path | Purpose |
|---|---|---|---|
| 1 | POST | `/sessions` | Sign in |
| 2 | DELETE | `/sessions/current` | Sign out |
| 3 | GET | `/me` | Profile and counters |
| 4 | GET | `/cards` | My cards |
| 5 | PATCH | `/cards/{cardId}` | Set card nickname |
| 6 | POST | `/imports` | Upload + dry run or real import (incl. identity choices) |
| 7 | GET | `/imports` | Import history |
| 8 | GET | `/transactions` | Filtered list + summary + groups |
| 9 | GET | `/transactions/{transactionId}` | Transaction detail |
| 10 | PATCH | `/transactions/{transactionId}` | Set / clear custom category override |
| 11 | GET | `/filter-options` | Values for filter pickers |
| 12 | GET | `/trips` | Trips with stats |
| 13 | POST | `/trips` | Create trip |
| 14 | GET | `/trips/{tripId}` | Trip detail & members |
| 15 | GET | `/trips/{tripId}/candidates` | Transactions that could be tagged |
| 16 | PUT | `/trips/{tripId}/tagged-transactions/{transactionId}` | Tag (or move) transaction to trip |
| 17 | DELETE | `/trips/{tripId}/tagged-transactions/{transactionId}` | Remove tag |
| 18 | PUT | `/trips/{tripId}/excluded-transactions/{transactionId}` | Exclude auto member |
| 19 | DELETE | `/trips/{tripId}/excluded-transactions/{transactionId}` | Restore excluded |
| 20 | GET | `/spending-analysis` | All aggregates for the analysis screen |
| 21 | GET | `/currency-validation-queue` | Queue progress & manual currency catalogue |
| 22 | GET | `/currency-validation-queue/items` | Pending/validated rows |
| 23 | POST | `/currency-validation-batches` | Manually validate selected/all filtered rows |
| 24 | DELETE | `/currency-validation-batches/{batchId}` | Undo a validation batch |
| 25 | DELETE | `/currency-validations/{transactionId}` | Move one row back to the queue |
| 26 | GET | `/categorization` | Category mapping screen overview |
| 27 | POST | `/custom-categories` | Create custom category |
| 28 | PUT | `/category-mappings/{bankCategory}` | Map bank → custom category |
| 29 | DELETE | `/category-mappings/{bankCategory}` | Reset mapping |
| 30 | POST | `/category-rules` | Add merchant-text rule |
| 31 | DELETE | `/category-rules/{ruleId}` | Delete rule |

**Out-of-scope endpoints:** `PATCH /me/preferences`, `GET`/`POST /invitations`, `PATCH /invitations/{id}` (sharing deferred); `POST /statement-files`, `DELETE /statement-files/{id}` (no server-side upload storage); `DELETE`/`PUT /category-settings` ("Reset everything" dropped); `DELETE /transactions/{transactionId}/review-reasons/{reason}` ("Mark as reviewed" dropped).

---

## 3. Auth and profile

### 3.1 `POST /sessions` – sign in
- **Screen:** Login.
- **Auth:** none.
- **Body:** `{ "email": "dana@example.com", "password": "…" }`
- **201:** `{ "token": "…", "expiresAt": "2025-03-14T21:00:00Z", "user": Me }`. Also sets the session cookie.
- **Errors:** `400` malformed; `401 INVALID_CREDENTIALS` (same message for unknown email and wrong password); `429` too many attempts.

### 3.2 `DELETE /sessions/current` – sign out
- **Screen:** top bar "Log out".
- **204.** Clears the cookie. `401` if not signed in.

### 3.3 `GET /me`
- **Screens:** top bar (name, avatar initial), Profile, review-proto nav badge, needs-review entry points.
- **200:**
```json
{
  "id": "usr_7f3a",
  "name": "Dana Levi",
  "email": "dana@example.com",
  "role": "OWNER",
  "csrfToken": "…",
  "counters": {
    "pendingCurrencyValidations": 37,
    "needsReviewByReason": [
      { "reason": "UNKNOWN_CURRENCY", "count": 37 }
    ]
  }
}
```
`role` is a constant label `OWNER`. `needsReviewByReason` lists only user-visible reasons (v1: `UNKNOWN_CURRENCY`) with a non-zero count.
- **Errors:** `401`.

---

## 4. Cards

### 4.1 `GET /cards`
- **Screens:** Profile & cards, Import step 3 (choose existing card).
- Returns only cards covered by the caller's ownership claims. No query parameters.
- **200:** `{ "items": [Card] }` (not paginated – a user has few cards).
```json
{ "id": "crd_91", "label": "Dana · Max ••5530", "issuer": "MAX", "last4": "5530", "nickname": "Groceries card",
  "owner": { "name": "Dana Levi" } }
```
- **Errors:** `401`.

### 4.2 `PATCH /cards/{cardId}`
- **Screen:** Profile & cards → nickname + Save.
- **Body:** `{ "nickname": "Groceries card" | null }` (max 120 chars, maps to `cards.display_name`).
- **200:** `Card`.
- **Errors:** `401`; `404`; `422` too long.

---

## 5. Statement import

The import wizard is: choose file → dry run → (identity) → real import. **No upload is stored on the server**: step 1 (choose / drop file, reject non-`.xlsx` by extension) is purely client-side, and the client sends the same file bytes with every `POST /imports` call. The detected issuer and SHA-256 are shown after the dry run. "Cancel import" is client-only (nothing to delete).

### 5.1 `POST /imports`
- **Screens:** Import step 2 (dry run), step 3 (identity – re-run with selections), step 4 (import for real).
- **Request:** `multipart/form-data`:

| Part | Required | Content |
|---|---|---|
| `file` | yes | the `.xlsx` workbook |
| `dryRun` | yes | `true` / `false` |
| `identitySelections` | no | JSON string: array answering `identityQuestions` of a previous response |

```json
[
  { "questionId": "iq_5b1e…", "action": "USE_EXISTING_CARD", "cardId": "crd_91" },
  { "questionId": "iq_77c0…", "action": "CREATE_CARD", "nickname": "Travel card" }
]
```
Maps to `UseCard` / `CreateCard`. Identity question ids are **deterministic** (derived from issuer + card hint, e.g. a hash), so ids from a dry run stay valid when the same file is re-uploaded. The import service resolves reliable file evidence or explicitly unresolved owner/account placeholders; it never fabricates bank identifiers or exposes another user's candidates.
- **201** (an import run was recorded; `Location: /api/v1/imports/{id}`):
```json
{
  "id": "imp_88",
  "status": "SUCCESS | PARTIAL | NEEDS_INPUT | FAILED",
  "dryRun": true,
  "issuer": "MAX",
  "sha256": "9f2c…",
  "startedAt": "2025-03-14T09:21:07Z",
  "counts": {
    "read": 120, "imported": 112, "duplicate": 5, "skipped": 3, "needsReview": 3,
    "needsReviewByReason": [ { "reason": "UNKNOWN_CURRENCY", "count": 3 } ]
  },
  "warnings": ["Totals row does not match the sum of transactions."],
  "issues": [ { "sheetPosition": 1, "row": 17, "code": "INVALID_DATE", "severity": "ERROR", "message": "Date could not be read." } ],
  "identityQuestions": [
    { "id": "iq_5b1e…", "reason": "NO_MATCHING_CARD", "issuer": "MAX", "last4": "5530",
      "candidates": [CardRef], "allowedActions": ["USE_EXISTING_CARD", "CREATE_CARD"] }
  ]
}
```
  - `issuer` is the detected issuer. A recognized XLSX without a supported bank format returns `422 UNKNOWN_STATEMENT_FORMAT`, not a success-shaped result; record an internal FAILED audit.
  - No `fileName` is returned or persisted; the browser keeps the selected name. Safe hash-derived audit labels may remain internal.
  - When `status = NEEDS_INPUT`, `identityQuestions` is non-empty. Audit may persist, but owners, accounts, cards, ownership claims, plans, and transactions do not.
  - `needsReview` / `needsReviewByReason` are computed in memory from the import report; nothing extra is stored. They count user-visible reasons only (v1: `UNKNOWN_CURRENCY`), so `needsReview` may be lower than the report's `rows_needing_review`. Step 4 shows a single link to the currency-validation queue (§6.5).
  - `issues` are response-only. The UI shows "Sheet {sheetPosition}"; sheet names are never returned.
  - A dry run may record audit but commits no domain data or ownership claims.
  - Maximum workbook size is 20 MiB. Same-file in-progress exclusion is global; provider-owned card/key serialization also protects overlapping statements with different hashes.
- **Errors:** `401`; `413 FILE_TOO_LARGE`; `415 UNSUPPORTED_FILE_TYPE` (not an `.xlsx` workbook); `422 UNREADABLE_WORKBOOK` / `UNKNOWN_STATEMENT_FORMAT`, or invalid `identitySelections` (malformed JSON, unknown question, nickname too long); `404` inaccessible explicit card selection; `409 IMPORT_IN_PROGRESS` / `DATA_ALREADY_OWNED`. Cookie-only mutations also follow the shared CSRF policy.
- Synchronous `201`.

### 5.2 `GET /imports`
- **Screen:** Import history.
- **Query:** `page`, `pageSize`, `sort` (`startedAt` default `-startedAt`).
- **200:** paginated `ImportSummary`:
```json
{ "id": "imp_88", "sha256": "9f2c…", "startedAt": "…", "status": "PARTIAL", "rowsImported": 112 }
```
- **Ownership:** visibility follows inserted transactions → cards → caller's ownership claims, not the uploading user's identity or file hash.
- **Reviewed history policy:** only runs with inserted transactions visible to the caller appear in history. Dry runs, all-duplicate runs, `FAILED` and `NEEDS_INPUT` runs do **not** appear.
- **Errors:** `401`; `400`.

`GET /imports/{id}` is intentionally not defined: no screen opens a past import.

---

## 6. Transactions

### 6.1 `GET /transactions`
- **Screens:** Transactions list (+ chips, groups, footer), Analysis drill-down drawer, Category-mapping transactions table, trip "View all in Transactions".
- **Query (filters, all optional, repeatable = OR):**

| Param | Type | Meaning / source |
|---|---|---|
| `from`, `to` | date | `transaction_date` inclusive |
| `month` | `YYYY-MM` | shortcut for from/to |
| `category` | string, repeatable | **effective** category: Hebrew bank name or custom name (the client translates English input with its i18n dictionary) |
| `bankCategory` | string, repeatable | `bank_category_raw` exact |
| `customCategory` | string, repeatable; `none` | `none` = no effective custom category from override, rule, or mapping |
| `categorySource` | `OVERRIDE\|RULE\|MAPPING\|BANK`, repeatable | how the effective category was resolved |
| `cardId` | id, repeatable | |
| `currency` | ISO code, repeatable; `UNKNOWN` | effective original/purchase currency; `UNKNOWN` selects foreign rows with missing original currency, not domestic `original=null` |
| `billingCurrency` | ISO code | billing currency; required with either amount bound |
| `minAmount`, `maxAmount` | decimal string | signed billing amount in the explicit `billingCurrency` |
| `merchant` | string, repeatable | case-insensitive contains on merchant name only (list search box, analysis "merchant contains", category-mapping search) |
| `type` | enum, repeatable | `transaction_type` |
| `country` | ISO 3166-1 alpha-2, repeatable | `merchant_country` |
| `tripId` | id, repeatable; `none` | trip membership |
| `needsReview` | bool | `true` = has a user-visible review reason (v1: `UNKNOWN_CURRENCY`); `false` = none. Internal-only reasons are ignored (§6.4) |
| `reviewReason` | `ReviewReason`, repeatable | has this review reason; v1 accepts `UNKNOWN_CURRENCY` only, other values → `400 INVALID_FILTER` |

- **Other query:** `sort` (fields: `transactionDate` (default `-transactionDate`), `chargeDate`, `merchant`, `country`, `city`, `category`, `bankCategory`, `card`, `type`, `originalAmount`, `originalCurrency`, `billingAmount`, `effectiveRate`, `commission`, `trip`, `notes`, `reviewCount`); `page`, `pageSize`; `groupBy=category|customCategory|month|week|day|year|card|currency|merchant|type|trip|country` (optional); `include=summary,groups` (default `summary`).
- **200:**
```json
{
  "items": [ {
    "id": "txn_5521",
    "transactionDate": "2025-02-03", "chargeDate": "2025-03-02",
    "merchant": "SHUFERSAL DEAL", "city": "Tel Aviv", "country": "IL",
    "category": Category,
    "card": CardRef,
    "type": "INSTALLMENTS", "installment": { "number": 2, "total": 6 },
    "original": { "amount": "150.00", "currency": "USD" },
    "billing": { "amount": "556.85", "currency": "ILS" },
    "effectiveRate": "3.712300", "commission": { "amount": "8.10", "currency": "ILS" },
    "trip": TripRef | null,
    "notes": null,
    "reviewReasons": ["UNKNOWN_CURRENCY"]
  } ],
  "meta": PageMeta,
  "summary": {
    "count": 312,
    "unknownCurrencyCount": 3,
    "unknownBillingCurrencyCount": 0,
    "billingTotals": [
      { "count": 300, "net": { "amount": "10000.00", "currency": "ILS" } },
      { "count": 12, "net": { "amount": "-20.00", "currency": "USD" } }
    ]
  },
  "groups": [ { "key": "2025-02", "label": "Feb 2025", "count": 41,
                "unknownBillingCurrencyCount": 0,
                "billingTotals": [ { "count": 41, "net": { "amount": "1500.00", "currency": "ILS" } } ] } ]
}
```
  - `summary` and `groups` cover the **whole filtered set**, not only the page.
  - Summary/groups use `BillingSummary`: one `billingTotals` entry per known billing currency, never a combined mixed-currency total.
  - Counts include positive, negative, and zero rows. No gross/refund/purchase splits, percentage shares, original-currency totals, or aggregate commission fields; per-transaction FX/commission details remain available.
  - When `groupBy` is set, items are additionally ordered by group first. `groupBy=trip` counts each transaction once (a transaction has at most one trip).
  - `original` is `null` for domestic rows; `original.currency` is `null` when unknown. Missing billing currency cannot contribute to known-unit sums.
  - `reviewReasons` and the `reviewCount` sort cover user-visible reasons only (v1: `UNKNOWN_CURRENCY`).
- **Errors:** `401`; `400 INVALID_FILTER` / `INVALID_SORT`.

### 6.2 `GET /transactions/{transactionId}`
- **Screens:** Transactions detail panel, Analysis drawer → transaction detail.
- **200:** all list fields (incl. `trip` and `reviewReasons`), plus:
```json
{
  "fx": { "statedRate": "3.650000", "effectiveRate": "3.712300", "deviationPercent": "1.71", "commission": Money },
  "installmentPlan": { "number": 2, "total": 6, "planTotal": Money, "perPayment": Money,
                       "remainingPayments": 4, "remainingAmount": Money },
  "importedAt": "2025-03-14T09:21:07Z"
}
```
`fx` is `null` for domestic rows; `installmentPlan` is `null` unless `type=INSTALLMENTS`. The installment "first charge" date was dropped. When `reviewReasons` contains `UNKNOWN_CURRENCY`, the detail panel shows it with a link to the currency-validation queue (§9); there is no "Mark as reviewed" action (§6.4).
- **Errors:** `401`; `404`.

### 6.3 `PATCH /transactions/{transactionId}`
- **Screens:** detail panel (override category / clear), analysis drawer (change custom category), category-mapping table (per-row override / clear).
- **Body:** `{ "customCategory": "Groceries" | null }` (required).
- **Select-or-create:** `customCategory` must name an existing custom category of the user. "Create new" in any picker = `POST /custom-categories` first, then this call. `null` clears the override.
- **200:** `TransactionDetail`.
- **Errors:** `401`; `404`; `422 UNKNOWN_CATEGORY` (name does not exist), `422` empty / > 60 chars.

### 6.4 Review reasons in v1
- **User-visible:** `UNKNOWN_CURRENCY` only. It is resolved **only** in the currency-validation queue (§9); there is no generic "Mark as reviewed" and no `DELETE …/review-reasons/{reason}` endpoint.
- **Internal-only:** `UNMATCHED_FOREIGN_FEE` and `AMBIGUOUS_INSTALLMENT`. The parser still raises them and `transaction_review_reasons` still stores them (to measure how often they happen), but no endpoint returns, counts, filters by or resolves them.
  - An unmatched foreign fee row is an ordinary domestic row (no `fx`) and is categorised like any other row: override › rule › mapping › the bank's raw category.
  - An ambiguous installment row is shown as stored (`installmentPlan` may be `null`).

### 6.5 Needs-review navigation
There is one review queue per user-visible reason. In v1 that is only:

| Reason | Where the user resolves it |
|---|---|
| `UNKNOWN_CURRENCY` | Currency-validation queue (§9, review prototype) |

Import step 4 shows a single "Review N rows" link to the currency queue, and the `/me` counters feed its nav badge. The account prototype's generic "Review queue" screen is dropped.

### 6.6 `GET /filter-options`
- **Screens:** Transactions filter pickers, Analysis filter chips, Category-mapping selects.
- **200:**
```json
{
  "categories": [ { "bank": "מזון וצריכה", "count": 120 } ],
  "customCategories": [ { "name": "Groceries", "count": 98 } ],
  "cards": [CardRef],
  "currencies": [ { "code": "USD", "count": 40 } ],
  "unknownCurrencyCount": 3,
  "billingCurrencies": [ { "code": "ILS", "count": 280 }, { "code": "USD", "count": 32 } ],
  "unknownBillingCurrencyCount": 2,
  "types": ["REGULAR", "INSTALLMENTS", "RECURRING", "IMMEDIATE_CHARGE"],
  "countries": [ { "code": "IL", "count": 280 } ],
  "trips": [ { "id": "trp_3", "name": "Japan 2025" } ],
  "dateRange": { "min": "2024-01-02", "max": "2025-03-10" }
}
```
- Counts cover the caller's whole transaction dataset; this endpoint takes no filters. `billingCurrencies` includes only known billing currencies present in that dataset, counting positive, negative, and zero rows. Missing billing currency is counted separately, never defaulted to ILS.
- `currencies` and `unknownCurrencyCount` still describe effective original/purchase currency; they are separate from billing options/counts. Display names can come from the shared code/name catalogue.
- **Errors:** `401`.

---

## 7. Trips

Membership rules:
- A transaction belongs to **at most one** trip.
- **Auto** members = transactions whose `original_currency` equals the trip currency and whose `transaction_date` lies in `[startDate, endDate]`, minus exclusions.
- **Tagged** members are manually pinned and stay even if outside the range. A tag always wins over auto membership. Tagging a transaction to trip B removes its tag from any other trip (move). Tagging an excluded transaction removes the exclusion.
- Same-user trips may not overlap on inclusive calendar dates, regardless of currency. Other users' trips do not matter. Jan 1-5 and Jan 5-10 overlap; Jan 1-5 and Jan 6-10 do not.
- Trips cannot be edited or deleted in v1.

### 7.1 `GET /trips`
- **Screen:** Trips list; trip pickers.
- **Query:** `sort` (`startDate` default `-startDate`, `name`).
- **200:** `{ "items": [TripSummary] }`
```json
{ "id": "trp_3", "name": "Japan 2025", "currency": "JPY", "startDate": "2025-01-10", "endDate": "2025-01-24", "days": 15,
  "stats": { "count": 54, "taggedCount": 3, "autoCount": 51, "unknownBillingCurrencyCount": 0,
             "billingTotals": [ { "count": 54, "net": { "amount": "5400.00", "currency": "ILS" } } ] } }
```
Stats use `BillingSummary`. Trip currency still drives automatic membership, not monetary aggregation. There are no original/trip-currency spending totals.

### 7.2 `POST /trips`
- **Screen:** Create trip.
- **Body:** `{ "name": "Japan 2025", "currency": "JPY", "startDate": "2025-01-10", "endDate": "2025-01-24" }`
- **201:** `TripDetail` + `Location`.
- **Errors:** `401`; `409 TRIP_DATE_OVERLAP`; `422` (`name` required/≤80, `currency` in the approved catalogue, dates required, `endDate < startDate` → `BEFORE_START_DATE`).

### 7.3 `GET /trips/{tripId}`
- **Screen:** Trip detail.
- **200:**
```json
{
  "id": "trp_3", "name": "Japan 2025", "currency": "JPY", "startDate": "…", "endDate": "…", "days": 15,
  "stats": { "count": 54, "unknownCurrencyCount": 1, "unknownBillingCurrencyCount": 0,
             "billingTotals": [ { "count": 54, "net": { "amount": "5400.00", "currency": "ILS" } } ],
             "outsideRange": { "count": 2, "unknownBillingCurrencyCount": 0,
                               "billingTotals": [ { "count": 2, "net": { "amount": "200.00", "currency": "ILS" } } ] } },
  "byCategory": [ { "category": "Restaurants", "label": "Restaurants", "count": 12, "unknownBillingCurrencyCount": 0,
                    "billingTotals": [ { "count": 12, "net": { "amount": "1200.00", "currency": "ILS" } } ] } ],
  "byDay": [ { "date": "2025-01-10", "count": 4, "unknownBillingCurrencyCount": 0,
               "billingTotals": [ { "count": 4, "net": { "amount": "400.00", "currency": "ILS" } } ] } ],
  "tagged": [TransactionListItem],
  "auto": [TransactionListItem],
  "excluded": [TransactionListItem]
}
```
Stats, category/day groups, and outside-range members use `BillingSummary`. No daily-average metric is exposed.
- **Errors:** `401`; `404`.

### 7.4 `GET /trips/{tripId}/candidates`
- **Screen:** Trip detail → "Add transactions".
- **Query:** `q` (candidate search across merchant, date `YYYY-MM-DD` prefix, or category; optional), `limit` (default 8, max 50). Without `q`, returns non-members dated from `startDate − 30 days` to `endDate + 7 days`. Candidates may currently belong to another trip (tagging moves them).
- **200:** `{ "items": [TransactionListItem] }`
- **Errors:** `401`; `404`.

### 7.5 `PUT /trips/{tripId}/tagged-transactions/{transactionId}`
- **Screens:** Trip detail "+ Tag" / "Tag" (pin auto member); transactions detail "Add trip tag".
- Idempotent. **204.** Removes an existing exclusion on this trip and any tag on another trip.
- **Errors:** `401`; `404` trip or transaction.

### 7.6 `DELETE /trips/{tripId}/tagged-transactions/{transactionId}`
- **Screens:** Trip detail "Remove tag"; transactions detail remove tag chip.
- **204** (also when a visible transaction is not tagged). Removing a tag recalculates automatic membership. **Errors:** `401`; `404` trip or transaction.

### 7.7 `PUT /trips/{tripId}/excluded-transactions/{transactionId}`
- **Screen:** Trip detail "Exclude" on an auto member.
- **204**, including an already-excluded visible pair. **Errors:** `401`; `404`; `409 NOT_AUTO_MEMBER` (a new exclusion requires automatic membership).

### 7.8 `DELETE /trips/{tripId}/excluded-transactions/{transactionId}`
- **Screen:** Trip detail "Restore".
- **204**, including an already-restored visible pair. **Errors:** `401`; `404` trip or transaction.

---

## 8. Spending analysis

### 8.1 `GET /spending-analysis`
Purpose-built endpoint: one call supplies signed-billing stat cards, groups, time series, and a matrix. Positive-only prototype views require adaptation.

- **Screen:** Analysis (all views). Drill-down uses `GET /transactions` with the same filters plus the clicked group/bucket.
- **Query:**

| Param | Notes |
|---|---|
| `from`, `to` | required, inclusive |
| `billingCurrency` | required; every monetary aggregate uses signed billing in this currency |
| `compare` | `none` (default) \| `previous` \| `yoy` |
| `groupBy` | `category\|customCategory\|day\|week\|month\|year\|card\|currency\|merchant\|type\|trip\|country` (default `category`). `trip`: each transaction is counted once |
| `bucket` | `day\|week\|month\|year`, default `month`; the client resolves "auto". Weeks start Monday. `complete` flags use "today" in UTC |
| `seriesBy` | dimension for the stacked view (default `category`) |
| `include` | `groups,timeSeries,matrix` (default all) |
| filters | `category`, `customCategory`, `cardId`, `currency`, `type`, `tripId`, `country`, `merchant`, `minAmount`, `maxAmount` (same semantics as `/transactions`) |

- **200:**
```json
{
  "range": { "from": "2025-01-01", "to": "2025-03-14" },
  "comparisonRange": { "from": "2024-10-20", "to": "2024-12-31" },
  "totals": {
    "net": Money,
    "count": 312,
    "monthlyAverage": { "value": Money | null, "completeBuckets": 2, "partialBuckets": ["2025-03"] }
  },
  "comparisonTotals": { "...": "same shape, null when compare=none" },
  "groups": [ { "key": "Restaurants", "label": "Restaurants", "net": Money, "count": 40,
                "previous": { "net": Money, "count": 30 } | null } ],
  "timeSeries": { "bucket": "month", "average": Money | null,
                  "points": [ { "key": "2025-01", "start": "2025-01-01", "end": "2025-01-31", "net": Money, "complete": true } ],
                  "previous": { "average": Money | null, "points": [ "..." ] } | null },
  "matrix": { "rowDimension": "category", "bucket": "month",
              "columns": [ { "key": "2025-01", "complete": true } ],
              "rows": [ { "key": "Restaurants", "label": "Restaurants", "cells": [ { "net": Money } ], "total": Money } ],
              "columnTotals": [Money] }
}
```
  - All monetary values use signed billing in `billingCurrency`. Positive, negative, and zero rows all count.
  - No per-transaction, daily, weekly, or yearly average is exposed anywhere. Gross/refund/purchase splits, purchase-only averages, percentage shares, and original-currency spending aggregates are also excluded.
  - `monthlyAverage.value` uses active complete calendar months only; `completeBuckets` counts those months, independent of chart bucket. Exclude incomplete and transaction-free months; return `null` when no qualifying month exists.
  - Time series include zero-valued empty buckets. Include missing category/custom-category/country groups with `key: null` and the fallback labels in §1.9 so grouped totals retain all selected rows.
  - `compare=previous` uses the same number of inclusive days immediately before the range. `yoy` maps February 29 to February 28 in a non-leap year.
  - Only monthly charts have an average line, using the approved monthly average; it is `null` when undefined. For day/week/year buckets, current/comparison `average` is always `null`; no corresponding average is computed. The monthly-average stat remains available regardless of chart bucket, and signed bucket totals and comparison series remain unchanged.
  - When `groupBy` is a period, `matrix.rowDimension` = `seriesBy`.
  - For known values, group `key`/`label` for `category` is the Hebrew bank name or custom name; for `country` the ISO code. Missing keys are `null`; matrix rows use the same convention. The client localises labels.
  - Top-N presentation stays client-side. Positive-only donut/purchase-stack views must be adapted to signed data; do not derive gross or absolute-value spending to preserve them.
- **Errors:** `401`; `400` (missing range or billingCurrency, `from > to`, unknown dimension/bucket, range too large for `bucket=day`, > 1 000 buckets).

---

## 9. Currency validation

This queue resolves missing original currency on foreign transactions only. Pending = eligible transactions with review reason `UNKNOWN_CURRENCY`. Domestic `original=null` is not unknown currency. Implied rate is the positive magnitude of `billing / original`, nullable when undefined; preserve stored signs and original amounts. No automatic suggestions, confidence filters/bands, or suggestion groups.

Validating a row:
1. sets `transactions.original_currency`;
2. deletes the `UNKNOWN_CURRENCY` `transaction_review_reasons` row;
3. writes a `currency_validations` audit row (`transaction_id` PK, `currency`, `batch_id`, `validated_by_user_id`, `validated_at`) in a `currency_validation_batches` batch.

Undo / move back reverses all three: `original_currency` → `NULL`, the `UNKNOWN_CURRENCY` reason is re-inserted, the audit row is deleted. Immutable `source_original_currency` never changes, preserving reimport deduplication. Effective currency affects filtering, trip matching, and FX details, not an original-currency spending aggregate.

### 9.1 `GET /currency-validation-queue`
- **Screen:** Review queue header (progress, tab counts, country filter, manual currency picker); nav badge.
- **200:**
```json
{
  "total": 120, "validated": 83, "pending": 37, "progressPercent": "69.2",
  "countries": [ { "code": "JP", "count": 25 } ],
  "currencyOptions": [
    { "code": "EUR", "name": "Euro" },
    { "code": "USD", "name": "US Dollar" }
  ]
}
```
`total` = eligible `pending` + `validated`. `currencyOptions` contains `{code, name}` objects for every currency in pycountry's bundled ISO 4217 catalogue except `XXX` and `XTS`; the displayed list is abbreviated. Names come from the bundled catalogue. The same catalogue validates server-side choices and supplies local UI code/name search/autocomplete, without a new endpoint or per-keystroke requests.

### 9.2 `GET /currency-validation-queue/items`
- **Screen:** Pending tab and Validated tab tables. The Validated tab reads `currency_validations`.
- **Query:** `status=PENDING|VALIDATED` (default `PENDING`), `country` (repeatable), `from`, `to`, `merchant` (merchant contains, repeatable), `sort` (default `-transactionDate`), `page`, `pageSize` (default 40).
- **200:** paginated:
```json
{ "id": "txn_9", "transactionDate": "…", "merchant": "LAWSON", "country": "JP",
  "originalAmount": "1200.00", "billing": Money, "impliedRate": "0.024100",
  "validation": { "currency": "JPY", "batchId": "vb_7", "validatedAt": "…" } | null }
```
"Select all filtered" uses `meta.totalItems` for the count and a filter-based batch (§9.3), so the client never has to list every id.

### 9.3 `POST /currency-validation-batches`
- **Screen:** manually choose a currency for one row, selected rows, or every filtered pending row.
- **Body** – exactly one target (`transactionIds` or `filter`) and required `currency`:
```json
{ "currency": "JPY", "transactionIds": ["txn_9", "txn_10"] }
{ "currency": "JPY", "filter": { "country": ["JP"] } }
```
`filter` accepts `country`, `from`, `to`, and `merchant` (pending rows only), not pagination/sort/status. Deduplicate repeated IDs. There is no transaction-count business cap: all selected or filtered rows are processed atomically, without truncation or separately committed sub-batches.
- **201:** `{ "id": "vb_7", "validatedCount": 21, "transactionIds": ["…"], "createdAt": "…" }` + `Location`.
- An empty filter match still creates a `201` batch with `validatedCount=0` and `transactionIds=[]`.
- **Errors:** `401`; generic `404` if any explicit ID is missing/inaccessible; `422` (no target, both targets, missing/unsupported currency); `409 ALREADY_VALIDATED` if an accessible explicit row is already validated. Every failure rejects the whole batch.

### 9.4 `DELETE /currency-validation-batches/{batchId}`
- **Screen:** toast "Undo" after any validation.
- **204**, also for an already-undone visible batch. Only later currency/validation-state changes invalidate undo; category/trip edits do not. Retain original membership/count or equivalent evidence even when a member moves back individually. **Errors:** `401`; `404`; `409` if any member's currency state changed: reject the entire undo and change nothing.

### 9.5 `DELETE /currency-validations/{transactionId}`
- **Screen:** Validated tab "Move back to queue". (Its undo re-validates via §9.3.)
- **204**, including an already-pending visible transaction. **Errors:** `401`; `404` missing/inaccessible transaction.

---

## 10. Categorization

Layered model, all settings per user: effective category = override (`transaction_category_overrides`) › newest matching rule (`category_rules` + `category_rule_terms`) › bank-category mapping (`category_mappings`) › bank category. Rules match ANY normalized term as a case-insensitive merchant substring, not regex or notes. Custom categories live in `custom_categories`.

### 10.1 `GET /categorization`
- **Screen:** Category mapping (bank categories panel, rules list with stats, resulting categories, selects). The transactions table on this screen uses `GET /transactions?categorySource=…&merchant=…&bankCategory=…&pageSize=25`.
- **200:**
```json
{
  "bankCategories": [ { "name": "מסעדות", "count": 54, "mappedTo": "Eating out" | null } ],
  "rules": [ { "id": "rul_2", "terms": ["wolt", "10bis"], "category": "Eating out",
               "stats": { "matched": 40 } } ],
  "customCategories": [ { "name": "Eating out" } ],
  "resultingCategories": [ { "name": "Eating out", "count": 71 } ]
}
```
Rules are returned newest first. `matched` is the only rule statistic.

### 10.2 `POST /custom-categories`
- **Screen:** "create new" option in mapping/rule/override selects (transactions detail, analysis drawer, category mapping).
- **Body:** `{ "name": "Eating out" }` → **201** `{ "name": "Eating out" }`.
- Trim names; enforce case-insensitive uniqueness per user while preserving display case. Reserve `none`.
- **Errors:** `401`; `409 CATEGORY_EXISTS`; `422` empty / > 60 chars.

### 10.3 `PUT /category-mappings/{bankCategory}` / `DELETE …`
- **Screen:** map a bank category / reset it. `bankCategory` is URL-encoded raw bank name.
- **PUT body:** `{ "customCategory": "Eating out" }` → **200** `{ "bankCategory": "מסעדות", "customCategory": "Eating out" }`.
- **DELETE:** **204** (idempotent).
- **Errors:** `401`; `404` unknown bank category; `422` unknown custom category.

### 10.4 `POST /category-rules`
- **Screen:** Add rule; Undo after delete creates a new rule, making it newest rather than restoring old priority.
- **Body:** `{ "terms": ["wolt", "10bis"], "category": "Eating out" }`. The client splits input on `,` `/` `|`; the server trims, lower-cases, de-duplicates and rejects empty lists. No position input/output.
- **201:** `Rule` (with stats).
- **Errors:** `401`; `422` (no terms, unknown category).

### 10.5 `DELETE /category-rules/{ruleId}`
- **Screen:** delete rule. **204.** Errors: `401`; `404`.

---

## 11. Traceability

### 11.1 UI action → endpoint

| Prototype / screen | UI action | Endpoint(s) |
|---|---|---|
| account / Login | Sign in | `POST /sessions` |
| account / Top bar | Show user | `GET /me` |
| account / Top bar | Log out | `DELETE /sessions/current` |
| account / Import 1 | Choose / drop file, reject non-xlsx | client only (no endpoint) |
| account / Import 2 | Dry run (counts, warnings, issues, detected issuer, SHA-256) | `POST /imports` (`dryRun=true`) |
| account / Import 3 | Pick existing card | `GET /cards`, `POST /imports` (`identitySelections`) |
| account / Import 3 | Create card with label | `POST /imports` (`CREATE_CARD`) |
| account / Import 2–3 | Cancel | client only (nothing stored server-side) |
| account / Import 4 | Import for real | `POST /imports` (`dryRun=false`, same file re-sent) |
| account / Import 4 | "Review N needs-review rows" (single link) | currency queue (`GET /currency-validation-queue`) |
| account / Import history | List | `GET /imports` |
| account / Profile | Name, email, role | `GET /me` |
| account / Profile | My cards | `GET /cards` |
| account / Profile | Save nickname | `PATCH /cards/{id}` |
| transactions / List | View, filter, search (merchant), sort, paginate, group, footer | `GET /transactions` |
| transactions / List | Filter pickers | `GET /filter-options` |
| transactions / Detail | Open | `GET /transactions/{id}` |
| transactions / Detail | Override category (select or create) / clear | `POST /custom-categories` (create), `PATCH /transactions/{id}` |
| transactions / Detail | Unknown-currency link to the review queue | client navigation (`GET /currency-validation-queue`) |
| transactions / Detail | Add / remove trip tag | `GET /trips`, `PUT`/`DELETE /trips/{id}/tagged-transactions/{txId}` |
| transactions / Trips | List | `GET /trips` |
| transactions / Create trip | Create | `POST /trips` |
| transactions / Trip detail | View stats, by category, by day, member lists | `GET /trips/{id}` |
| transactions / Trip detail | Tag / remove tag | `PUT`/`DELETE …/tagged-transactions/{txId}` |
| transactions / Trip detail | Exclude / restore | `PUT`/`DELETE …/excluded-transactions/{txId}` |
| transactions / Trip detail | Search / suggestions to add | `GET /trips/{id}/candidates` |
| transactions / Trip detail, category bars, "View all" | Navigate to filtered list | `GET /transactions?tripId=…` |
| analysis / Controls, filters, stats, all charts, table | Render | `GET /spending-analysis`, `GET /filter-options` |
| analysis / Drill-down drawer | Matching transactions + summary | `GET /transactions` |
| analysis / Drawer detail | View | `GET /transactions/{id}` |
| analysis / Drawer detail | Change custom category | `PATCH /transactions/{id}` |
| review / Nav | Pending badge | `GET /me` (`counters`) |
| review / Queue | Progress, tabs, countries, currency catalogue | `GET /currency-validation-queue` |
| review / Queue | Filter, paginate, Pending/Validated tabs | `GET /currency-validation-queue/items` |
| review / Queue | Manually choose currency for one / selected / all filtered rows | `POST /currency-validation-batches` |
| review / Queue | Undo | `DELETE /currency-validation-batches/{id}` |
| review / Validated | Move back to queue | `DELETE /currency-validations/{txId}` (undo → `POST /currency-validation-batches`) |
| review / Category mapping | View overview | `GET /categorization` |
| review / Category mapping | Map / reset bank category | `PUT`/`DELETE /category-mappings/{bankCategory}` |
| review / Category mapping | Create new category | `POST /custom-categories` |
| review / Category mapping | Add rule / delete rule / undo delete | `POST /category-rules`, `DELETE /category-rules/{id}` |
| review / Category mapping | Transactions table (merchant search, source filter, page) | `GET /transactions` (`merchant`, `categorySource`, `bankCategory`) |
| review / Category mapping | Per-row override / clear | `PATCH /transactions/{id}` |

Not covered on purpose:

| Prototype action | Reason |
|---|---|
| account / Sharing (invite, sent/received lists, cancel, revoke, accept, decline) | Deferred – out of scope v1 |
| account / Shared with me (cards by owner, view-mode toggle) | Deferred – out of scope v1 |
| account / merged-view banner, shared badges, read-only shared cards on Profile | Deferred – out of scope v1 |
| account / generic "Review queue" screen | Dropped; the currency-validation queue is the only review queue in v1 |
| transactions / Detail "Mark as reviewed" | Dropped; `UNKNOWN_CURRENCY` is resolved only in the currency queue |
| review / Category mapping "Reset everything to bank categories" (+ undo) | Dropped |
| review / Queue automatic suggestions/confidence | Out of scope; manual selection from the full allowed currency catalogue |
| transactions / Detail installment "first charge" date | Dropped |
| "Reset demo data", user switcher, quick-login picks, "Simulate" scenario selector, analysis scenarios bar, column toggles, collapse groups, filter chips/URL state, chart view switch, top-N cuts | Prototype-only behaviour |

### 11.2 Endpoint → justifying screen
Every endpoint in §2 has its justifying screen in its "Screen" line. All 31 endpoints appear at least once in §11.1.
