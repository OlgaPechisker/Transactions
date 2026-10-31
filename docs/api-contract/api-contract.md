# REST API contract – UI ↔ backend

Status: **proposal**. Machine-readable version: [`openapi.yaml`](openapi.yaml). Data mapping: [`screen-to-data-map.md`](screen-to-data-map.md). Gaps: [`gaps-and-questions.md`](gaps-and-questions.md).

Many endpoints depend on data that the schema does not hold yet (users, trips, custom categories, currency validations). Those endpoints are marked with the gap id (`G-nn`) they depend on.

**Decisions log:** every gap (`G-nn`) and question (`Q-nn`) has a recorded decision in [`gaps-and-questions.md`](gaps-and-questions.md) (§1 *Decision* column, §3 resolved questions). This contract reflects those decisions. Remaining open questions are listed in §4 of that file.

**Out of scope for v1:** sharing between users (invitations, "Shared with me", merged view, view-mode preference). See G-02/G-03. Every endpoint returns and changes only the caller's own data.

---

## 1. Conventions

### 1.1 Base URL and versioning
`/api/v1`. All paths below are relative to it. Breaking changes require a new major version.

### 1.2 Authentication
- App users live in a new `users` table (email + password hash); `user_owners` links a user to the one or more `owners` (statement card holders) they are (G-01).
- `POST /sessions` exchanges credentials for a session. The session is returned both as an `HttpOnly; Secure; SameSite=Lax` cookie and as a bearer token (`Authorization: Bearer <token>`) for non-browser clients.
- **Every endpoint except `POST /sessions` requires authentication.** Missing or invalid credentials → `401`.
- Requests that change state with cookie auth must carry an `X-CSRF-Token` header (value from `GET /me`).

### 1.3 Data visibility
- A user sees the cards of the owners linked to them via `user_owners`, and the transactions of those cards. Nothing else is visible.
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
| "Today" | computed in **UTC** (Q-9) |
| Money | `{"amount": "-123.45", "currency": "ILS"}`. `amount` is a decimal **string** (2 dp) to avoid float rounding. Negative = refund/credit. `currency` is ISO 4217, or `null` when unknown |
| Rates | decimal string, 6 dp, e.g. `"3.712300"` |
| Countries | ISO 3166-1 alpha-2 codes (`"JP"`). Names are rendered by the frontend i18n dictionary (Q-8) |
| Bank categories | Hebrew bank names only. English labels live in the frontend i18n dictionary (G-12) |
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
- Ranges use `from`/`to` (dates, inclusive) and `minAmount`/`maxAmount` (ILS billing amount, inclusive).
- Special values: `none` (no value, e.g. no custom category, no trip) and `UNKNOWN` (unknown original currency).
- Text search is **merchant-only** everywhere (`merchant`, case-insensitive contains, repeatable = OR) (Q-10). The only exception is trip candidates (`q`, §7.4).

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
Common status codes: `400` malformed request / bad query param, `401` not authenticated, `404` not found **or not visible to the caller** (never reveals existence), `409` conflict (duplicate / state), `413` payload too large, `415` unsupported media type, `422` semantic validation failure, `429` rate limited, `500` unexpected.

Error messages never contain card numbers, account numbers or file paths.

### 1.9 Shared shapes
- **CardRef** – `{ "id", "label", "issuer": "ISRACARD|MAX|CAL", "last4", "nickname", "ownerName" }`. `label` is the server-computed display label (e.g. `"Dana · Max ••5530"`).
- **TripRef** – `{ "id", "name", "membership": "TAGGED|AUTO" }`.
- **Category** – `{ "bank": "מזון וצריכה", "custom": "Groceries"|null, "effective": "Groceries", "source": "OVERRIDE|RULE|MAPPING|BANK" }`. Effective category = override › first matching rule › bank-category mapping › bank category.
- **ReviewReason** – user-visible review reasons. **v1 exposes `UNKNOWN_CURRENCY` only.** `UNMATCHED_FOREIGN_FEE` and `AMBIGUOUS_INSTALLMENT` stay in the database enum (still raised by the parser, for monitoring) but are never returned or accepted by the API (Q-19, §6.4).
- **ReviewReasonCount** – `{ "reason": "UNKNOWN_CURRENCY", "count": 4 }`.

---

## 2. Endpoint index

| # | Method | Path | Purpose | Gap |
|---|---|---|---|---|
| 1 | POST | `/sessions` | Sign in | G-01 |
| 2 | DELETE | `/sessions/current` | Sign out | G-01 |
| 3 | GET | `/me` | Profile and counters | G-01, G-15 |
| 4 | GET | `/cards` | My cards | G-01 |
| 5 | PATCH | `/cards/{cardId}` | Set card nickname | – |
| 6 | POST | `/imports` | Upload + dry run or real import (incl. identity choices) | G-01 |
| 7 | GET | `/imports` | Import history | G-01, G-08 |
| 8 | GET | `/transactions` | Filtered list + summary + groups | G-10, G-11 |
| 9 | GET | `/transactions/{transactionId}` | Transaction detail | G-10, G-11 |
| 10 | PATCH | `/transactions/{transactionId}` | Set / clear custom category override | G-10 |
| 11 | GET | `/filter-options` | Values for filter pickers | G-10, G-11 |
| 12 | GET | `/trips` | Trips with stats | G-11 |
| 13 | POST | `/trips` | Create trip | G-11 |
| 14 | GET | `/trips/{tripId}` | Trip detail & members | G-11 |
| 15 | GET | `/trips/{tripId}/candidates` | Transactions that could be tagged | G-11 |
| 16 | PUT | `/trips/{tripId}/tagged-transactions/{transactionId}` | Tag (or move) transaction to trip | G-11 |
| 17 | DELETE | `/trips/{tripId}/tagged-transactions/{transactionId}` | Remove tag | G-11 |
| 18 | PUT | `/trips/{tripId}/excluded-transactions/{transactionId}` | Exclude auto member | G-11 |
| 19 | DELETE | `/trips/{tripId}/excluded-transactions/{transactionId}` | Restore excluded | G-11 |
| 20 | GET | `/spending-analysis` | All aggregates for the analysis screen | G-10, G-11 |
| 21 | GET | `/currency-validation-queue` | Queue summary & suggestion groups | G-15 |
| 22 | GET | `/currency-validation-queue/items` | Pending/validated rows | G-15 |
| 23 | POST | `/currency-validation-batches` | Validate currency for rows (single/bulk/group) | G-15 |
| 24 | DELETE | `/currency-validation-batches/{batchId}` | Undo a validation batch | G-15 |
| 25 | DELETE | `/currency-validations/{transactionId}` | Move one row back to the queue | G-15 |
| 26 | GET | `/categorization` | Category mapping screen overview | G-10, G-17, G-18 |
| 27 | POST | `/custom-categories` | Create custom category | G-10 |
| 28 | PUT | `/category-mappings/{bankCategory}` | Map bank → custom category | G-17 |
| 29 | DELETE | `/category-mappings/{bankCategory}` | Reset mapping | G-17 |
| 30 | POST | `/category-rules` | Add merchant-text rule | G-18 |
| 31 | DELETE | `/category-rules/{ruleId}` | Delete rule | G-18 |

Removed by the review decisions (see gaps doc): `PATCH /me/preferences`, `GET`/`POST /invitations`, `PATCH /invitations/{id}` (sharing deferred, G-02/G-03); `POST /statement-files`, `DELETE /statement-files/{id}` (no server-side upload storage, G-04); `DELETE`/`PUT /category-settings` ("Reset everything" dropped, Q-16); `DELETE /transactions/{transactionId}/review-reasons/{reason}` ("Mark as reviewed" dropped, Q-21).

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
`role` is a constant label `OWNER` (Q-18). `needsReviewByReason` lists only user-visible reasons (v1: `UNKNOWN_CURRENCY`) with a non-zero count.
- **Errors:** `401`.

---

## 4. Cards

### 4.1 `GET /cards`
- **Screens:** Profile & cards, Import step 3 (choose existing card).
- Returns only the caller's own cards (cards of the owners linked to the user). No query parameters.
- **200:** `{ "items": [Card] }` (not paginated – a user has few cards).
```json
{ "id": "crd_91", "label": "Dana · Max ••5530", "issuer": "MAX", "last4": "5530", "nickname": "Groceries card",
  "owner": { "name": "Dana Levi", "email": "dana@example.com" } }
```
- **Errors:** `401`.

### 4.2 `PATCH /cards/{cardId}`
- **Screen:** Profile & cards → nickname + Save.
- **Body:** `{ "nickname": "Groceries card" | null }` (max 120 chars, maps to `cards.display_name`).
- **200:** `Card`.
- **Errors:** `401`; `404`; `422` too long.

---

## 5. Statement import

The import wizard is: choose file → dry run → (identity) → real import. **No upload is stored on the server** (G-04): step 1 (choose / drop file, reject non-`.xlsx` by extension) is purely client-side, and the client sends the same file bytes with every `POST /imports` call. The detected issuer and SHA-256 are shown after the dry run. "Cancel import" is client-only (nothing to delete).

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
Maps to `UseCard` / `CreateCard`. Identity question ids are **deterministic** (derived from issuer + card hint, e.g. a hash), so ids from a dry run stay valid when the same file is re-uploaded.
- **201** (an import run was recorded; `Location: /api/v1/imports/{id}`):
```json
{
  "id": "imp_88",
  "status": "SUCCESS | PARTIAL | NEEDS_INPUT | FAILED",
  "dryRun": true,
  "issuer": "MAX",
  "fileName": "max_2025_02.xlsx",
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
  - `issuer` is the detected issuer (`null` if no parser recognises the file). `fileName` is echoed for display only and never persisted.
  - When `status = NEEDS_INPUT`, `identityQuestions` is non-empty and nothing was written.
  - `needsReview` / `needsReviewByReason` are computed in memory from the import report; nothing extra is stored (G-05). They count user-visible reasons only (v1: `UNKNOWN_CURRENCY`), so `needsReview` may be lower than the report's `rows_needing_review`. Step 4 shows a single link to the currency-validation queue (§6.5).
  - `issues` are response-only (G-06). The UI shows "Sheet {sheetPosition}"; sheet names are never returned.
  - A dry run writes no transactions.
- **Errors:** `401`; `413 FILE_TOO_LARGE`; `415 UNSUPPORTED_FILE_TYPE` (not an `.xlsx` workbook); `422 UNREADABLE_WORKBOOK`, or invalid `identitySelections` (malformed JSON, unknown question, card not owned, nickname too long); `409 IMPORT_IN_PROGRESS` for the same file.
- Synchronous `201` (Q-3).

### 5.2 `GET /imports`
- **Screen:** Import history.
- **Query:** `page`, `pageSize`, `sort` (`startedAt` default `-startedAt`).
- **200:** paginated `ImportSummary`:
```json
{ "id": "imp_88", "fileName": "max_2025_02.xlsx", "sha256": "9f2c…", "startedAt": "…", "status": "PARTIAL", "rowsImported": 112 }
```
- **Ownership:** there is no `import_runs.user_id`. A run belongs to the user who owns the cards of the transactions it imported (`transactions.import_run_id` → `cards` → `user_owners`) (G-08).
- **Caveat:** runs that imported zero transactions – dry runs (G-07, never shown), all-duplicate runs, `FAILED` and `NEEDS_INPUT` runs – do **not** appear in history (see open question in the gaps doc).
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
| `customCategory` | string, repeatable; `none` | custom category; `none` = no override/custom |
| `categorySource` | `OVERRIDE\|RULE\|MAPPING\|BANK`, repeatable | how the effective category was resolved |
| `cardId` | id, repeatable | |
| `currency` | ISO code, repeatable; `UNKNOWN` | `original_currency` |
| `minAmount`, `maxAmount` | decimal string | ILS billing amount |
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
    "count": 312, "purchaseCount": 300, "refundCount": 12,
    "net": Money, "gross": Money, "refunds": Money,
    "originalTotals": [ { "amount": "450.00", "currency": "USD" } ],
    "unknownCurrencyCount": 3,
    "commission": Money
  },
  "groups": [ { "key": "2025-02", "label": "Feb 2025", "count": 41, "net": Money, "originalTotals": [Money] } ]
}
```
  - `summary` and `groups` cover the **whole filtered set**, not only the page.
  - When `groupBy` is set, items are additionally ordered by group first. `groupBy=trip` counts each transaction once (a transaction has at most one trip).
  - `original` is `null` for domestic rows; `original.currency` is `null` when unknown.
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
`fx` is `null` for domestic rows; `installmentPlan` is `null` unless `type=INSTALLMENTS`. The installment "first charge" date was dropped (G-14). When `reviewReasons` contains `UNKNOWN_CURRENCY`, the detail panel shows it with a link to the currency-validation queue (§9); there is no "Mark as reviewed" action (§6.4).
- **Errors:** `401`; `404`.

### 6.3 `PATCH /transactions/{transactionId}`
- **Screens:** detail panel (override category / clear), analysis drawer (change custom category), category-mapping table (per-row override / clear).
- **Body:** `{ "customCategory": "Groceries" | null }` (required).
- **Select-or-create (Q-5):** `customCategory` must name an existing custom category of the user. "Create new" in any picker = `POST /custom-categories` first, then this call. `null` clears the override.
- **200:** `TransactionDetail`.
- **Errors:** `401`; `404`; `422 UNKNOWN_CATEGORY` (name does not exist), `422` empty / > 60 chars.

### 6.4 Review reasons in v1 (Q-19, Q-21)
- **User-visible:** `UNKNOWN_CURRENCY` only. It is resolved **only** in the currency-validation queue (§9); there is no generic "Mark as reviewed" and no `DELETE …/review-reasons/{reason}` endpoint (G-13 / Q-15 superseded by Q-21).
- **Internal-only:** `UNMATCHED_FOREIGN_FEE` and `AMBIGUOUS_INSTALLMENT`. The parser still raises them and `transaction_review_reasons` still stores them (to measure how often they happen), but no endpoint returns, counts, filters by or resolves them.
  - An unmatched foreign fee row is an ordinary domestic row (no `fx`) and is categorised like any other row: override › rule › mapping › the bank's raw category (G-20).
  - An ambiguous installment row is shown as stored (`installmentPlan` may be `null`).

### 6.5 Needs-review navigation
There is one review queue per user-visible reason (Q-6). In v1 that is only:

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
  "types": ["REGULAR", "INSTALLMENTS", "RECURRING", "IMMEDIATE_CHARGE"],
  "countries": [ { "code": "IL", "count": 280 } ],
  "trips": [ { "id": "trp_3", "name": "Japan 2025" } ],
  "dateRange": { "min": "2024-01-02", "max": "2025-03-10" }
}
```
- **Errors:** `401`.

---

## 7. Trips (all depend on G-11)

Membership rules (Q-12):
- A transaction belongs to **at most one** trip.
- **Auto** members = transactions whose `original_currency` equals the trip currency and whose `transaction_date` lies in `[startDate, endDate]`, minus exclusions.
- **Tagged** members are manually pinned and stay even if outside the range. A tag always wins over auto membership. Tagging a transaction to trip B removes its tag from any other trip (move). Tagging an excluded transaction removes the exclusion.
- Overlapping same-currency trips are allowed; the **earliest-starting** trip claims auto members (tie → earliest created).
- Trips cannot be edited or deleted in v1 (Q-11).

### 7.1 `GET /trips`
- **Screen:** Trips list; trip pickers.
- **Query:** `sort` (`startDate` default `-startDate`, `name`).
- **200:** `{ "items": [TripSummary] }`
```json
{ "id": "trp_3", "name": "Japan 2025", "currency": "JPY", "startDate": "2025-01-10", "endDate": "2025-01-24", "days": 15,
  "stats": { "count": 54, "taggedCount": 3, "autoCount": 51, "billed": Money, "inTripCurrency": Money } }
```

### 7.2 `POST /trips`
- **Screen:** Create trip.
- **Body:** `{ "name": "Japan 2025", "currency": "JPY", "startDate": "2025-01-10", "endDate": "2025-01-24" }`
- **201:** `TripDetail` + `Location`.
- **Errors:** `401`; `422` (`name` required/≤80, `currency` ISO 4217, dates required, `endDate < startDate` → `BEFORE_START_DATE`).

### 7.3 `GET /trips/{tripId}`
- **Screen:** Trip detail.
- **200:**
```json
{
  "id": "trp_3", "name": "Japan 2025", "currency": "JPY", "startDate": "…", "endDate": "…", "days": 15,
  "stats": { "billed": Money, "inTripCurrency": Money, "otherCurrencies": [Money], "count": 54,
             "averagePerDay": Money, "commission": Money, "unknownCurrencyCount": 1,
             "outsideRange": { "count": 2, "billed": Money } },
  "byCategory": [ { "category": "Restaurants", "billed": Money, "count": 12 } ],
  "byDay": [ { "date": "2025-01-10", "billed": Money } ],
  "tagged": [TransactionListItem],
  "auto": [TransactionListItem],
  "excluded": [TransactionListItem]
}
```
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
- **204** (also when not tagged). **Errors:** `401`; `404` trip.

### 7.7 `PUT /trips/{tripId}/excluded-transactions/{transactionId}`
- **Screen:** Trip detail "Exclude" on an auto member.
- **204.** **Errors:** `401`; `404`; `409 NOT_AUTO_MEMBER` (only auto members can be excluded).

### 7.8 `DELETE /trips/{tripId}/excluded-transactions/{transactionId}`
- **Screen:** Trip detail "Restore".
- **204.** **Errors:** `401`; `404`.

---

## 8. Spending analysis

### 8.1 `GET /spending-analysis`
Purpose-built endpoint: one call renders stat cards, all chart views and the breakdown table (the prototype needs groups, a time series and a matrix at the same time).

- **Screen:** Analysis (all views). Drill-down uses `GET /transactions` with the same filters plus the clicked group/bucket.
- **Query:**

| Param | Notes |
|---|---|
| `from`, `to` | required, inclusive |
| `compare` | `none` (default) \| `previous` \| `yoy` |
| `groupBy` | `category\|customCategory\|day\|week\|month\|year\|card\|currency\|merchant\|type\|trip\|country` (default `category`). `trip`: each transaction is counted once |
| `bucket` | `day\|week\|month\|year` – concrete bucket for time series/matrix; the client resolves "auto". Weeks start Monday. `complete` flags use "today" in UTC (Q-9) |
| `seriesBy` | dimension for the stacked view (default `category`) |
| `include` | `groups,timeSeries,matrix` (default all) |
| filters | `category`, `customCategory`, `cardId`, `currency`, `type`, `tripId`, `country`, `merchant`, `minAmount`, `maxAmount` (same semantics as `/transactions`) |

- **200:**
```json
{
  "range": { "from": "2025-01-01", "to": "2025-03-14" },
  "comparisonRange": { "from": "2024-10-19", "to": "2024-12-31" },
  "totals": {
    "net": Money, "gross": Money, "refunds": Money,
    "count": 312, "purchaseCount": 300, "refundCount": 12,
    "averagePerPurchase": Money,
    "monthlyAverage": { "value": Money, "completeBuckets": 2, "partialBuckets": ["2025-03"] },
    "original": Money | null
  },
  "comparisonTotals": { "...": "same shape, null when compare=none" },
  "groups": [ { "key": "Restaurants", "label": "Restaurants", "net": Money, "gross": Money, "count": 40,
                "sharePercent": "12.5", "averagePerPurchase": Money, "original": Money | null, "originalMixed": false,
                "previous": { "net": Money, "count": 30 } | null } ],
  "timeSeries": { "bucket": "month", "average": Money,
                  "points": [ { "key": "2025-01", "start": "2025-01-01", "end": "2025-01-31", "net": Money, "complete": true } ],
                  "previous": { "average": Money, "points": [ "..." ] } | null },
  "matrix": { "rowDimension": "category", "bucket": "month",
              "columns": [ { "key": "2025-01", "complete": true } ],
              "rows": [ { "key": "Restaurants", "label": "Restaurants", "cells": [ { "net": Money, "gross": Money } ], "total": Money } ],
              "columnTotals": [Money] }
}
```
  - `original` is set only when the filtered set has a single foreign currency.
  - When `groupBy` is a period, `matrix.rowDimension` = `seriesBy`.
  - Group `key`/`label` for `category` is the Hebrew bank name or custom name; for `country` the ISO code. The client localises labels.
  - Donut "top 8 + other" and bar "top 25" are cut on the client.
- **Errors:** `401`; `400` (missing range, `from > to`, unknown dimension/bucket, range too large for `bucket=day`, > 1 000 buckets).

---

## 9. Currency validation (review prototype; depends on G-15)

This is the `UNKNOWN_CURRENCY` review queue. Pending = transactions with review reason `UNKNOWN_CURRENCY`. Implied rate = `billing / original`. Suggestions come from server-side rate ranges with confidence `HIGH|MEDIUM|LOW`.

Validating a row (G-15, Q-14):
1. sets `transactions.original_currency`;
2. deletes the `UNKNOWN_CURRENCY` `transaction_review_reasons` row;
3. writes a `currency_validations` audit row (`transaction_id` PK, `currency`, `batch_id`, `validated_by_user_id`, `validated_at`) in a `currency_validation_batches` batch.

Undo / move back reverses all three: `original_currency` → `NULL`, the `UNKNOWN_CURRENCY` reason is re-inserted, the audit row is deleted. The validated currency therefore flows into list/analysis currency totals like any stored currency.

### 9.1 `GET /currency-validation-queue`
- **Screen:** Review queue header (progress, suggestion groups, low-confidence banner, tab counts, country filter); nav badge.
- **200:**
```json
{
  "total": 120, "validated": 83, "pending": 37, "progressPercent": "69.2",
  "suggestionGroups": [ { "currency": "JPY", "count": 21, "rateMin": "0.023100", "rateMax": "0.025400" } ],
  "lowConfidenceCount": 4,
  "countries": [ { "code": "JP", "count": 25 } ],
  "currencyOptions": ["JPY", "USD", "EUR", "GBP"]
}
```
`total` = `pending` (reason rows) + `validated` (audit rows). `currencyOptions` is the fixed list the UI offers (no free choice, G-16 / Q-7).

### 9.2 `GET /currency-validation-queue/items`
- **Screen:** Pending tab and Validated tab tables. The Validated tab reads `currency_validations`.
- **Query:** `status=PENDING|VALIDATED` (default `PENDING`), `country` (repeatable), `from`, `to`, `suggestedCurrency` (repeatable), `confidence` (repeatable; "Show them" = `LOW`), `merchant` (merchant contains, repeatable), `sort` (default `-transactionDate`), `page`, `pageSize` (default 40).
- **200:** paginated:
```json
{ "id": "txn_9", "transactionDate": "…", "merchant": "LAWSON", "country": "JP",
  "originalAmount": "1200.00", "billing": Money, "impliedRate": "0.024100",
  "suggestion": { "currency": "JPY", "confidence": "HIGH", "hint": "Rate 0.0241 fits JPY" } | null,
  "validation": { "currency": "JPY", "batchId": "vb_7", "validatedAt": "…" } | null }
```
"Select all filtered" uses `meta.totalItems` for the count and a filter-based batch (§9.3), so the client never has to list every id.

### 9.3 `POST /currency-validation-batches`
- **Screen:** confirm one suggestion, pick another currency, bulk apply to selection, apply to all filtered, confirm a suggestion group, "confirm each suggestion".
- **Body** – exactly one target (`transactionIds` or `filter`) and exactly one of `currency` / `useSuggestion`:
```json
{ "currency": "JPY", "transactionIds": ["txn_9", "txn_10"] }
{ "useSuggestion": true, "filter": { "suggestedCurrency": ["JPY"], "confidence": ["HIGH","MEDIUM"] } }
```
`filter` accepts the same keys as §9.2 (pending rows only).
- **201:** `{ "id": "vb_7", "validatedCount": 21, "transactionIds": ["…"], "createdAt": "…" }` + `Location`.
- **Errors:** `401`; `422` (no target, both targets, `currency` not in `currencyOptions`, rows without suggestion when `useSuggestion`); `409 ALREADY_VALIDATED` if any id is already validated (whole batch rejected).

### 9.4 `DELETE /currency-validation-batches/{batchId}`
- **Screen:** toast "Undo" after any validation.
- **204.** Rows return to pending (see reversal above). **Errors:** `401`; `404`; `409` if a row of the batch was changed afterwards.

### 9.5 `DELETE /currency-validations/{transactionId}`
- **Screen:** Validated tab "Move back to queue". (Its undo re-validates via §9.3.)
- **204.** **Errors:** `401`; `404` (not validated).

---

## 10. Categorization (review prototype "Category mapping"; G-10, G-17, G-18)

Layered model, all settings per user: effective category = override (`transaction_category_overrides`) › first matching rule by position (`category_rules` + `category_rule_terms`) › bank-category mapping (`category_mappings`) › bank category. Custom categories live in `custom_categories`.

### 10.1 `GET /categorization`
- **Screen:** Category mapping (bank categories panel, rules list with stats, resulting categories, selects). The transactions table on this screen uses `GET /transactions?categorySource=…&merchant=…&bankCategory=…&pageSize=25`.
- **200:**
```json
{
  "bankCategories": [ { "name": "מסעדות", "count": 54, "mappedTo": "Eating out" | null } ],
  "rules": [ { "id": "rul_2", "position": 0, "terms": ["wolt", "10bis"], "category": "Eating out",
               "stats": { "matched": 40, "changed": 31, "keptByOverride": 2, "shadowed": 3 } } ],
  "customCategories": [ { "name": "Eating out" } ],
  "resultingCategories": [ { "name": "Eating out", "count": 71 } ]
}
```

### 10.2 `POST /custom-categories`
- **Screen:** "create new" option in mapping/rule/override selects (transactions detail, analysis drawer, category mapping).
- **Body:** `{ "name": "Eating out" }` → **201** `{ "name": "Eating out" }`.
- **Errors:** `401`; `409 CATEGORY_EXISTS`; `422` empty / > 60 chars.

### 10.3 `PUT /category-mappings/{bankCategory}` / `DELETE …`
- **Screen:** map a bank category / reset it. `bankCategory` is URL-encoded raw bank name.
- **PUT body:** `{ "customCategory": "Eating out" }` → **200** `{ "bankCategory": "מסעדות", "customCategory": "Eating out" }`.
- **DELETE:** **204** (idempotent).
- **Errors:** `401`; `404` unknown bank category; `422` unknown custom category.

### 10.4 `POST /category-rules`
- **Screen:** Add rule; also Undo after delete (re-insert at original `position`).
- **Body:** `{ "terms": ["wolt", "10bis"], "category": "Eating out", "position": 0 }` (`position` optional, default = append). The client splits input on `,` `/` `|`; the server trims, lower-cases, de-duplicates and rejects empty lists.
- **201:** `Rule` (with stats).
- **Errors:** `401`; `422` (no terms, unknown category, position out of range).

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
| account / Import 1 | Choose / drop file, reject non-xlsx | client only (no endpoint; G-04) |
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
| review / Queue | Progress, suggestion groups, tabs, countries | `GET /currency-validation-queue` |
| review / Queue | Filter, paginate, Pending/Validated tabs, "Show them" | `GET /currency-validation-queue/items` |
| review / Queue | Confirm one / pick other listed currency / bulk / all filtered / group / each suggestion | `POST /currency-validation-batches` |
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
| account / Sharing (invite, sent/received lists, cancel, revoke, accept, decline) | Deferred – out of scope v1 (G-02) |
| account / Shared with me (cards by owner, view-mode toggle) | Deferred – out of scope v1 (G-02, G-03) |
| account / merged-view banner, shared badges, read-only shared cards on Profile | Deferred – out of scope v1 (G-02) |
| account / generic "Review queue" screen | Dropped; the currency-validation queue is the only review queue in v1 (Q-6, Q-19, §6.5) |
| transactions / Detail "Mark as reviewed" | Dropped; `UNKNOWN_CURRENCY` is resolved only in the currency queue (Q-21, §6.4) |
| review / Category mapping "Reset everything to bank categories" (+ undo) | Dropped (Q-16) |
| review / Queue "Other" currency | Dropped; fixed `currencyOptions` only (G-16, Q-7) |
| transactions / Detail installment "first charge" date | Dropped (G-14) |
| "Reset demo data", user switcher, quick-login picks, "Simulate" scenario selector, analysis scenarios bar, column toggles, collapse groups, filter chips/URL state, chart view switch, top-N cuts | Prototype-only behaviour |

### 11.2 Endpoint → justifying screen
Every endpoint in §2 has its justifying screen in its "Screen" line. All 31 endpoints appear at least once in §11.1.
