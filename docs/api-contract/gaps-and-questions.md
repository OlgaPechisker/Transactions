# Gaps, decisions and open questions

Companion to [`api-contract.md`](api-contract.md) and [`screen-to-data-map.md`](screen-to-data-map.md).
Nothing here has been applied to the schema. Schema changes are **proposals only**.

All gaps (G-nn) and the original questions (Q-1..Q-18), plus Q-19 and Q-21, were reviewed; the decisions are recorded below and are reflected in the contract and `openapi.yaml`. Remaining open questions are in §4.

Input note: the prototypes path given in the brief (`C:\Users\Pechisker\source\repos\Transactions\prototypes`) does not exist. The analysis used the repository copy at `C:\Users\opechisker\source\repos\Transactions\prototypes`.

---

## 1. Schema gaps and decisions

| Id | Gap | Needed by | Decision | Schema change (proposal only) |
|---|---|---|---|---|
| G-01 | No users, credentials, emails or sessions. `owners` are statement card holders, not app users | Login, top bar, Profile, every endpoint (auth) | **Accepted.** New `users` table + `user_owners` link (one user may be several owners). Email + password; session cookie / bearer token. Keep `POST /sessions`, `DELETE /sessions/current`, `GET /me` | `users(id, email UNIQUE, display_name, password_hash, created_at)`; `user_owners(user_id, owner_id, PK(user_id, owner_id))`; sessions in a store (`user_sessions` table or signed tokens) |
| G-02 | No sharing (invitations, grants) | Sharing, Shared with me, merged view, banner | **Deferred (out of scope for v1).** All sharing endpoints, `scope` params, read-only flags and the banner removed from the contract | *Future:* `share_invitations(id, inviter_user_id, invitee_email, scope ALL\|CARDS, status PENDING\|ACCEPTED\|DECLINED\|REVOKED, created_at, responded_at)`; `share_invitation_cards(invitation_id, card_id)` |
| G-03 | No user preferences | View-mode toggle | **Deferred (out of scope for v1)** – only needed for the merged view | *Future:* `user_preferences(user_id PK, view_mode MINE\|MERGED)` |
| G-04 | No storage for uploaded files between dry run and real import | Import wizard | **No server-side upload storage.** `POST /imports` takes the file as `multipart/form-data` (`file`, `dryRun`, `identitySelections`); the client re-sends the same file for the real import. Identity question ids are deterministic (issuer + card hint hash). Step 1 is client-only; issuer/SHA-256 shown after the dry run; cancel is client-only | None |
| G-05 | Needs-review count of a run is not stored | Import step 2/4 counts | **Computed, no column.** Response: in memory from the import report (`counts.needsReview` + `needsReviewByReason`). History (if ever needed): from `transaction_review_reasons` via `transactions.import_run_id` | None |
| G-06 | Issues are not persisted; no sheet *name* and no per-row *outcome* | Import row-issues table | **Response-only.** No sheet-name or per-row outcome field; UI shows "Sheet n" from `sheetPosition` | None (no `import_run_issues`) |
| G-07 | No dry-run flag | Import history "DRY RUN" | **Dry runs are not shown in history** (they import zero transactions, so they never match the ownership rule of G-08) | None (no `dry_run` column) |
| G-08 | `import_runs` has no owner/user | Per-user import history | **Derived** from the cards of the run's imported transactions (`transactions.import_run_id` → `cards` → `user_owners`). Caveat: runs that imported zero transactions (all duplicates, `FAILED`, `NEEDS_INPUT`, dry runs) do not appear in history (see Q-20) | None (no `import_runs.user_id`) |
| G-09 | Account-proto review reasons ("Unknown category", "Possible duplicate", "Credit row", "Missing merchant") are not in `ReviewReason` | Review UI | **Align the UI to the domain enum** (`UNKNOWN_CURRENCY`, `UNMATCHED_FOREIGN_FEE`, `AMBIGUOUS_INSTALLMENT`). The prototype reasons were placeholders. v1 exposes only `UNKNOWN_CURRENCY`; the other two are stored but internal-only (Q-19) | None (no enum change; DB enum keeps all three) |
| G-10 | No custom categories or per-transaction overrides | Transactions detail, analysis drawer, category mapping, filters, `groupBy=customCategory` | **Accepted** – full layered model: override › first matching rule › bank mapping › bank category, all per user. Overrides must reference an existing custom category (select-or-create, Q-5) | `custom_categories(id, user_id, name, UNIQUE(user_id, name))`; `transaction_category_overrides(user_id, transaction_id, custom_category_id, PK(user_id, transaction_id))` |
| G-11 | No trips, manual tags or exclusions | Trips list/detail/create, trip filter/grouping, detail panel trip | **Accepted.** Auto membership = same original currency AND `transaction_date` in `[start, end]`; manual tags; exclusions. At most one trip per transaction (Q-12); no edit/delete in v1 (Q-11) | `trips(id, user_id, name, currency CHAR(3), start_date, end_date, created_at, CHECK end_date >= start_date)`; `trip_tagged_transactions(trip_id, transaction_id UNIQUE)`; `trip_excluded_transactions(trip_id, transaction_id, PK(trip_id, transaction_id))` |
| G-12 | No English labels for Hebrew bank categories | Filters (English input), detail panel, analysis labels | **Frontend i18n dictionary.** API returns Hebrew bank names only (no English label fields). `category` filter matches the Hebrew bank name or custom name; the client translates English input | None (no `bank_category_labels`) |
| G-13 | "Mark as reviewed" has nowhere to go | Detail panel review section | **Superseded by Q-21: "Mark as reviewed" dropped.** `UNKNOWN_CURRENCY` (the only user-visible reason) is resolved only in the currency queue (G-15). No `DELETE /transactions/{id}/review-reasons/{reason}` endpoint | None |
| G-14 | Installment "first charge" date is not stored | Detail panel installment plan | **Dropped** from the UI and the contract | None (no `first_charge_date`) |
| G-15 | No currency validations or validation batches | Currency review queue, Validated tab, undo, nav badge | **Accepted.** Validating sets `transactions.original_currency`, deletes the `UNKNOWN_CURRENCY` reason and writes an audit row. Validated tab reads the audit rows. Undo / move back: `original_currency` → `NULL`, re-insert the reason, delete the audit row. Queue total = pending (reason rows) + validated (audit rows) | `currency_validation_batches(id, user_id, created_at)`; `currency_validations(transaction_id PK, currency CHAR(3), batch_id FK, validated_by_user_id FK users, validated_at)` |
| G-16 | "Other" offered as a currency is not ISO 4217 | Currency picker | **"Other" dropped.** Fixed list only (`currencyOptions`); any other code → `422` | None |
| G-17 | No bank-category → custom-category mappings | Category mapping | **Accepted** (part of the layered model, G-10) | `category_mappings(user_id, bank_category_raw, custom_category_id, PK(user_id, bank_category_raw))` |
| G-18 | No merchant-text rules | Category mapping rules | **Accepted** (part of the layered model, G-10) | `category_rules(id, user_id, position, custom_category_id, UNIQUE(user_id, position))`; `category_rule_terms(rule_id, term, PK(rule_id, term))` |
| G-19 | Business rule: **the billing currency is never unknown.** The schema (`transactions.billing_currency` nullable) and the parser (`tabular_base._build` and the domain invariant raise `UNKNOWN_CURRENCY` when the billing currency is `NULL`) still allow it | Currency queue (validates `original_currency` only) | **Accepted as a rule.** `UNKNOWN_CURRENCY` always means "original currency unknown", so the currency queue covers it fully. Backend alignment is a follow-up (not part of this contract) | *Proposal:* `transactions.billing_currency` `NOT NULL`; parser rejects a row whose billing currency cannot be read (row issue) instead of flagging it |
| G-20 | Unmatched foreign fee rows (`UNMATCHED_FOREIGN_FEE`) have no dedicated handling | Categorisation, analysis | **No special handling in v1.** The row stays an ordinary domestic row and is categorised by the normal chain (override › rule › mapping › the bank's raw category). No forced "Bank fee"/"Other" category, no review UI (Q-19) | None |

### 1.1 Proposed schema changes for v1 (summary)

Needed:
- `users`, `user_owners` (+ session store) – G-01
- `custom_categories`, `transaction_category_overrides`, `category_mappings`, `category_rules`, `category_rule_terms` – G-10, G-17, G-18
- `trips`, `trip_tagged_transactions` (**`transaction_id` UNIQUE** – at most one trip per transaction), `trip_excluded_transactions` – G-11
- `currency_validation_batches`, `currency_validations` – G-15

Follow-up proposal (backend alignment, not required by the API): `transactions.billing_currency NOT NULL` – G-19.
Not needed (decided against): `user_preferences`, `share_invitations`, `share_invitation_cards` (future, sharing deferred); `statement_uploads`; `import_run_issues`; `import_runs.dry_run`; `import_runs.rows_needing_review`; `import_runs.user_id`; `bank_category_labels`; `installment_plans.first_charge_date`; `transaction_review_reasons.resolved_at` / `resolved_by_user_id`.

## 2. Schema fields the UI never uses (list only, no removal proposed)

| Table | Column(s) |
|---|---|
| `owners` | `match_key`, `created_at` |
| `accounts` | all columns: `bank_code`, `account_number_enc`, `display_name`, `created_at` |
| `cards` | `account_id`, `created_at` |
| `import_runs` | `file_path`, `finished_at`, `error_summary` |
| `installment_plans` | `issuer_reference`, `merchant_name_norm`, `purchase_date`, `currency`, `created_at` |
| `transactions` | `issuer_reference`, `billing_currency`, `merchant_name_norm` (search/grouping only, not displayed), `channel`, `created_at` |
| `transaction_review_reasons` | `created_at` |

`transactions.import_run_id` is now used: it attributes import runs to a user for the history (G-08) and can count needs-review rows per run (G-05).

Not exposed on purpose: internal integer keys (exposed as opaque ids), ciphertext columns (only decrypted `last4` is returned), `file_path` / hash labels.

## 3. Resolved questions

| Id | Question | Decision |
|---|---|---|
| Q-1 | Are trips, custom categories, mappings, rules and overrides per user? Whose categorisation applies to shared transactions in merged view? | **Moot for v1** – no sharing (G-02). All these settings are per user |
| Q-2 | Is categorising/tagging shared transactions allowed? | **Moot for v1** – no sharing; no read-only errors exist in v1 |
| Q-3 | Should imports run asynchronously (`202` + polling)? | **Synchronous `201`** (kept) |
| Q-4 | Second invitation to the same email while one is pending? | **Moot for v1** – invitations deferred |
| Q-5 | Which category-override UX is canonical? Does free text create a category? | **Select-or-create.** `PATCH /transactions/{id}` `{customCategory}` must reference an existing custom category (`422 UNKNOWN_CATEGORY` otherwise); "create new" = `POST /custom-categories` first. No implicit creation |
| Q-6 | Account-proto "Review queue" vs. the review-proto currency queue | **One queue per user-visible reason, each its own screen with its own layout (no shared queue shell).** The generic account "Review queue" is dropped. In v1 the only queue is the currency-validation queue (`UNKNOWN_CURRENCY`, Q-19). Import step 4 shows a single link to it; `/me` counters feed its nav badge |
| Q-7 | Meaning of "Other" currency | **Dropped** (G-16); fixed `currencyOptions` only, `422` otherwise |
| Q-8 | Country names vs. codes | **ISO 3166-1 alpha-2 codes**; names rendered by the frontend i18n. `CountryOption = {code, count}`; filters take codes |
| Q-9 | Timezone for "today" | **UTC** (complete/partial buckets) |
| Q-10 | Should text search include notes? | **Merchant-only everywhere.** `/transactions` uses the repeatable `merchant` param (list search and category-mapping search); currency-queue `q` renamed `merchant`. Trip candidates keep `q` (candidate search across merchant/date/category) |
| Q-11 | Trip edit / delete | **Not in v1** |
| Q-12 | Single trip vs. multiple trips per transaction | **At most one trip per transaction.** `trip: TripRef \| null` on list items and detail. Tagging to trip B removes the tag from any other trip (move). Overlapping same-currency trips allowed; the earliest-starting trip claims auto members (tie → earliest created); a tag always wins over auto. `groupBy=trip` counts each transaction once |
| Q-13 | Source of installment "first charge" | **Dropped** (G-14) |
| Q-14 | Does validating a currency resolve `UNKNOWN_CURRENCY` and change totals? | **Yes.** It sets `original_currency`, deletes the reason row and writes an audit row; totals use the stored currency. Undo reverses all three (G-15) |
| Q-15 | Does "Mark as reviewed" resolve all reasons or one at a time? | **Superseded by Q-21:** "Mark as reviewed" is dropped |
| Q-16 | Is "Reset everything" also expected to delete custom categories? | **Feature dropped.** `DELETE`/`PUT /category-settings` and the snapshot schema removed |
| Q-17 | Should import history include dry runs? | **No.** History has no dry-run filter or flag |
| Q-18 | What is "role: Owner" on the Profile page? | **Constant label `OWNER`** |
| Q-19 | Review queues for `UNMATCHED_FOREIGN_FEE` and `AMBIGUOUS_INSTALLMENT`? | **Out of scope for v1.** Both reasons stay in the DB enum and are still raised and stored (to measure frequency), but are **internal-only**: the API never returns, counts, filters by or resolves them. `ReviewReason` in the API = `UNKNOWN_CURRENCY` only; `needsReview`, `needsReviewByReason`, `reviewReasons`, `reviewCount` and the `reviewReason` filter cover it only (other values → `400 INVALID_FILTER`). Unmatched fees are categorised normally (G-20) |
| Q-21 | "Mark as reviewed" on `UNKNOWN_CURRENCY` would leave `original_currency` `NULL` and silently drop the row from the currency queue | **"Mark as reviewed" and `DELETE /transactions/{id}/review-reasons/{reason}` removed.** `UNKNOWN_CURRENCY` is resolved only in the currency queue; the detail panel links to it |
| Q-20 | Are import runs that imported zero transactions (all duplicates, `FAILED`, `NEEDS_INPUT`) invisible in import history (consequence of G-08)? | **Accepted as a known limitation.** Changing it would need `import_runs.user_id` |
| Q-22 | Does a transaction excluded from the trip that auto-claims it fall through to an overlapping same-currency trip? | **No.** The claiming trip is decided before exclusions, so the transaction belongs to no trip (unless tagged) |
| Q-23 | Content of the fixed `currencyOptions` list | **Static server-side list** (initially JPY, USD, EUR, GBP as in the prototype) |
| Q-24 | Maximum upload size for `POST /imports` | **10 MB** (`413 FILE_TOO_LARGE` above it). Default chosen during review; adjust if needed |

## 4. Open questions

None at the moment. Backend follow-ups are tracked as gaps (G-19, G-20).

## 5. Prototype-only behaviours (not API features)

"Reset demo data", user switch select, quick-login account picks, "Simulate" scenario selector, analysis scenarios bar, column toggles, collapse groups, chart view switch, top-N cuts in charts, URL/filter-chip state.
