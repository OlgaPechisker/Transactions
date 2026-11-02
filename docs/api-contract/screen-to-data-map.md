# Screen → data map

**Status: current V1 screen/API mapping; implementation not started.**
The [Phase 1 architecture](../api-design-phase-1.md) supplies the schema changes
and three-layer boundaries; [Phase 2](../api-design-phase-2.md) the detailed plan.
Prototype files are inputs, not authority for features this contract drops.

Sources analysed

- **Prototypes** (`prototypes\`):
  - `account\` – Login, Import wizard + history, Review queue (needs-review list; dropped – the currency queue is the only v1 review queue), Profile & cards, Sharing and Shared with me (both deferred – out of scope v1)
  - `transactions\` – Transactions list + detail panel, Trips list, Trip detail, Create trip, Analysis placeholder (deep links)
  - `analysis\` – Spending analysis (stats, 6 chart views, breakdown table, drill-down drawer, transaction detail in drawer)
  - `review\` – Currency-validation review queue (Pending / Validated tabs), Category mapping
- **Schema**: `src\bank_transactions\persistence\orm\models.py` and migration `0001_initial_schema.py`.

Legend for the *Source* column

- `table.column` – stored in the schema today.
- **computed** – derived on the server from stored columns (aggregation, formatting, decryption).
- **⚠ NO SOURCE** – planned V1 data absent from the checked-in schema. See the [Phase 1 schema changes](../api-design-phase-1.md#3-schema-changes).
- **client** – pure UI state; no API involvement.
- **i18n** – the frontend localizes real bank categories/countries from Hebrew bank names / ISO codes. Missing groups use null keys and readable fallback labels, also localizable by dimension.
- **Deferred – out of scope v1** – sharing-related; not in the v1 contract.
- **Dropped** – not in the contract.

The production target is a fresh database. Section 1 describes the checked-in
schema, while feature rows identify planned additions; no backfill or
ownership adoption is needed.

Abbreviations: `tx` = `transactions`, `ftd` = `foreign_transaction_details`, `trr` = `transaction_review_reasons`, `ip` = `installment_plans`, `ir` = `import_runs`.

---

## 1. Entity overview (schema)

| Entity | Key columns | Relationships | Constraints / enums |
|---|---|---|---|
| `owners` | `display_name`, `match_key` | 1–N accounts, cards | `match_key` unique |
| `accounts` | `bank_code`, `account_number_enc` (ciphertext), `display_name` | N–1 owner; 1–N cards | unique (`bank_code`,`account_number_enc`) |
| `cards` | `issuer_code`, `last4_enc` (ciphertext), `display_name` | N–1 owner, N–1 account (composite FK keeps owner consistent) | `issuer_code` ∈ {ISRACARD, MAX, CAL} |
| `import_runs` | file labels, `file_sha256`, `issuer_code`, `status`, row counts, `error_summary`, timestamps | 1–N transactions | `status` ∈ {RUNNING, SUCCESS, PARTIAL, NEEDS_INPUT, FAILED}; counts ≥ 0 |
| `installment_plans` | merchant, `purchase_date`, `total_amount`, `currency`, `total_installments` | N–1 card; 1–N transactions | natural-key unique; `total_installments ≥ 1` |
| `transactions` | dates, billing/original amount+currency, merchant raw/norm/city/country, `bank_category_raw`, `transaction_type`, `channel`, `notes`, installment link | N–1 card, import run, plan | `transaction_type` ∈ {REGULAR, INSTALLMENTS, RECURRING, IMMEDIATE_CHARGE}; `channel` ∈ {INTERNET, PHONE, MOBILE, UNKNOWN}; currency `^[A-Z]{3}$` |
| `transaction_review_reasons` | (`transaction_id`, `reason`) | N–1 transaction (cascade) | `reason` ∈ {UNKNOWN_CURRENCY, UNMATCHED_FOREIGN_FEE, AMBIGUOUS_INSTALLMENT} |
| `foreign_transaction_details` | `commission_amount`, `stated_fx_rate`, `effective_fx_rate` | 1–1 transaction (cascade) | – |

---

## 2. Account prototype (`prototypes\account`)

| Screen | Action | Required data | Source tables/fields |
|---|---|---|---|
| Login | View | – | – |
| Login | Sign in (email + password) | user identity, credential check | ⚠ NO SOURCE – administratively provisioned `users`, Argon2id hashes, 12-hour absolute sessions and worker-shared email/IP throttling; trimmed case-insensitive email, unchanged password |
| Login | Quick-pick mock account | – | prototype only (not an API feature) |
| Top bar (all pages) | Show signed-in user, avatar initial | user name, email | ⚠ NO SOURCE – proposed `users.display_name`, `users.email` |
| Top bar | Switch user (select) | – | Deferred – out of scope v1; prototype simulation only (not an API feature) |
| Top bar | Log out | session | ⚠ NO SOURCE – session store |
| Banner | "Merged view: your data plus N shared card(s) from X" | – | Deferred – out of scope v1 |
| Import – step 1 | Choose / drag-drop .xlsx, "Use sample file" | file name | **client** – nothing is uploaded until the dry run (no server-side upload storage). SHA-256 and detected issuer are **computed** by `POST /imports` and shown after the dry run (→ later `ir.file_sha256`, `ir.issuer_code`) |
| Import – step 1 | Reject non-.xlsx | file type validation | **client** (extension); server re-checks (`415`) |
| Import – step 2 | Run dry run (file sent with the request); show "Rows read / Would import / Duplicates / Skipped / Needs review" | counts | `ir.rows_total`, `ir.rows_imported`, `ir.rows_duplicate`, `ir.rows_skipped`; needs-review total and per-reason counts (`UNKNOWN_CURRENCY` only) **computed** in memory from the import report, not persisted |
| Import – step 2 | Warnings list | safe warning sentences | **computed** (`ImportReport.warnings`); only a flattened `ir.error_summary` is persisted |
| Import – step 2 | Row issues table (row, sheet, problem) | per-row issues | **computed**, response-only (`ImportReport.issues`: `location.sheet_position`, `location.row`, `code`, `severity`, `message`); UI shows "Sheet n"; sheet name and outcome column dropped |
| Import – step 2 | "Identity must be chosen" | NEEDS_INPUT status, unresolved cards | `ir.status = NEEDS_INPUT`; unresolved hints **computed** (`IdentityQuestion`, deterministic id from issuer + card hint so it survives the re-upload) |
| Import – step 3 | Show "Max ••••5530 could not be matched" | issuer + last 4 from the file | **computed** from file hint (plaintext only in memory) |
| Import – step 3 | Pick existing card | user's own cards (issuer, last4, nickname) | `cards` plus planned `card_ownership_claims`; only the caller's candidates |
| Import – step 3 | Create new card with optional label | card + claim + label | reserve issuer/canonical-last-four for the app user; import service resolves evidence or explicit unresolved placeholders, never fabricated bank IDs |
| Import – step 3 | Cancel import | – | **client** – nothing is stored server-side |
| Import – step 4 | Confirm & "Import for real" (same file re-sent) | final counts, status SUCCESS/PARTIAL | atomic claim/card/identity/transaction writes; global same-file and card/key guards; 20 MiB XLSX limit. Dry runs/NEEDS_INPUT keep at most audit; unknown bank format returns 422 |
| Import – step 4 | "Review N new needs-review rows →" | needs-review count (`UNKNOWN_CURRENCY` only) | **computed** in memory; single link to the currency queue |
| Import history | SHA-256, date, status (SUCCESS/PARTIAL), inserted rows | visible import runs | `ir.file_sha256`, `ir.started_at`, `ir.status`; visible inserted `tx` → `cards` → ownership claims. No `fileName`; dry runs, zero-insert runs, FAILED and NEEDS_INPUT are excluded |
| Review queue (generic) | List date, description, amount, card (+shared badge), reason | – | Dropped – the currency-validation queue (review prototype) is the only review queue in v1. `UNMATCHED_FOREIGN_FEE` / `AMBIGUOUS_INSTALLMENT` are stored in `trr` but internal-only; shared badge deferred |
| Profile & cards | Show name, email, role "Owner" | user profile | ⚠ NO SOURCE – proposed `users`; role is a constant label `OWNER` |
| Profile & cards | My cards: issuer, last 4, holder name, nickname | claimed cards | `cards`, holder-name metadata and ownership claims. No cardholder email; user profile email stays |
| Profile & cards | Save nickname | update card label | `cards.display_name` |
| Profile & cards | Shared cards (read-only, disabled Save) | – | Deferred – out of scope v1 |
| Sharing | Invite owner by email, scope All / specific cards | – | Deferred – out of scope v1 |
| Sharing | Invites I sent: to, scope, status; Cancel / Revoke | – | Deferred – out of scope v1 |
| Sharing | Invites for me: Accept / Decline | – | Deferred – out of scope v1 |
| Shared with me | View mode toggle "Merged" / "Only mine" | – | Deferred – out of scope v1 |
| Shared with me | Cards per sharing owner (last4, nickname, issuer, read-only) | – | Deferred – out of scope v1 |

## 3. Transactions prototype (`prototypes\transactions`)

| Screen | Action | Required data | Source tables/fields |
|---|---|---|---|
| Transactions list | View rows (16 toggleable columns) | date, charge date, merchant, country, city, effective category (+override dot), bank category, card label, type (+installment n/total), source original amount/currency, native billing amount/currency, rate magnitude, commission detail, trip, notes, review count | `tx.transaction_date`, `tx.charge_date`, `tx.merchant_name_raw`, `tx.merchant_country`, `tx.merchant_city`, `tx.bank_category_raw`, `owners.display_name`+`cards.issuer_code`+`cards.last4_enc` (card label **computed**), `tx.transaction_type`, `tx.installment_number`+`ip.total_installments`, `tx.original_amount`, `tx.original_currency`, `tx.billing_amount`, `ftd.effective_fx_rate`, `ftd.commission_amount`, `tx.notes`, `trr.reason` (count); country as ISO code, name via **i18n**; custom category ⚠; trip (at most one) ⚠ |
| Transactions list | Toggle columns | – | client |
| Transactions list | Sort by any column (asc/desc) | server-side ordering | same fields; trip names ⚠ |
| Transactions list | Filter: dates, category/custom (none = no override/rule/mapping category), card, original currency, billingCurrency, signed amount bounds, merchant, type, country, trip, visible review reason | scoped filtering/options | `tx` and resolved category/trip projections; filter-options billingCurrencies contains code/count per present known billing currency plus unknownBillingCurrencyCount, across the caller's whole dataset. Original-currency options stay separate; amount bounds require billingCurrency |
| Transactions list | Filter chips, remove one, clear all | – | client (URL state) |
| Transactions list | Group by category/period/card/original currency/merchant/type/trip; collapse groups | whole-filter counts and signed billing aggregates | **computed** `BillingSummary`: count, unknown billing count, billingTotals array with count/net Money per known currency |
| Transactions list | Footer: all-row count, foreign unknown count, signed billing totals | whole-filter aggregates | `BillingSummary` plus separate unknown-original count; no original/gross/refund/commission aggregates or per-transaction average |
| Transactions list | Breadcrumb from Analysis (category › trip › month) | category label, trip name | `tx.bank_category_raw`, English label via **i18n**, trip ⚠ |
| Detail panel | Header: merchant, date, city, country, amount, type badge, original amount | single transaction | `tx.*` |
| Detail panel | Review section: reasons; "Mark as reviewed" per reason | reasons | `trr.reason` (`UNKNOWN_CURRENCY` only) shown with a link to the currency queue; "Mark as reviewed" **dropped** |
| Detail panel | Details: tx date, charge date, card, type, notes | | `tx.*`, `cards.*` |
| Detail panel | Category: bank raw (+English), custom override, "counted as" | categories | `tx.bank_category_raw`; English via **i18n**; override ⚠; effective **computed** |
| Detail panel | Override category (select existing or create new) / Clear | write override | ⚠ NO SOURCE; select existing category or create it with POST /custom-categories first |
| Detail panel | Currency & FX: original, stated rate, effective rate, commission, effective-vs-stated %, billed; unknown-currency callout | FX details | `tx.original_amount`, `tx.original_currency`, `ftd.stated_fx_rate`, `ftd.effective_fx_rate`, `ftd.commission_amount`; deviation **computed** |
| Detail panel | Installment plan: payment n of N, progress, plan total, per payment, remaining payments & amount | plan | `tx.installment_number`, `ip.total_installments`, `ip.total_amount`, `tx.billing_amount`; remaining **computed** |
| Detail panel | Installment plan: "first charge" date | – | Dropped |
| Detail panel | Trip: the single trip (tagged – removable, or auto – static), add/move trip tag | membership + trips list | ⚠ NO SOURCE; at most one trip per transaction, tagging moves it |
| Trips list | Cards: name, dates, days, membership currency, signed billing totals by currency, all-row/tagged/auto counts | trips + aggregates | ⚠ trips; **computed** BillingSummary plus taggedCount/autoCount |
| Trips list | "+ Create trip" | navigation | client |
| Create trip | Name, catalogue currency, dates, end ≥ start; no inclusive same-user overlap across any currency | create trip | ⚠ trips; provider serializes same-user creation; overlap returns `409 TRIP_DATE_OVERLAP` |
| Trip detail | Hero stats: signed billing totals by currency, count, outside-dates/unknown-original notes | trip aggregates | BillingSummary for all members and outsideRange subset; no daily/per-transaction averages or original-currency spending |
| Trip detail | By category bars, linking to filtered list | currency-separated signed billing sums | **computed** BillingSummary per category with category/label; missing category uses null / Uncategorized |
| Trip detail | By day chart (in-range members) | daily sums | **computed** BillingSummary per date |
| Trip detail | Manually tagged list + "Remove tag" | membership | ⚠ |
| Trip detail | Auto-included list + "Tag" (pin) + "Exclude" | original-currency/date match minus exclusions | `tx.original_currency`, `tx.transaction_date`; no same-user trip overlaps; manual tag wins and removing it recalculates auto membership |
| Trip detail | Excluded list + "Restore" | exclusions | ⚠ |
| Trip detail | Add transactions: search (merchant, date, category) or default suggestions (30 days before → 7 after), "+ Tag" | candidate list | `tx.*` + membership ⚠ |
| Trip detail | "View all in Transactions →" | navigation with `trip` filter | client |
| Analysis placeholder | Deep links into list (`category`, `month`, `trip`, `currency`, `review`, `type`) | – | client (relies on list filter params) |

## 4. Analysis prototype (`prototypes\analysis`)

| Screen | Action | Required data | Source tables/fields |
|---|---|---|---|
| Controls | Period preset / custom from–to | date range | client computes range; server filters `tx.transaction_date` |
| Controls | Compare: none / previous period / same period last year | previous-range aggregates | **computed** |
| Controls | Group by: category, custom category, day, week, month, year, card, currency, merchant, type, trip, country | grouping dimension | `tx.bank_category_raw`, custom ⚠, `tx.transaction_date`, `cards`, `tx.original_currency`, `tx.merchant_name_raw`, `tx.transaction_type`, trip ⚠ (each transaction counted once), `tx.merchant_country` (ISO code; name via **i18n**) |
| Controls | Time bucket: auto/day/week/month/year | bucket | client resolves "auto"; server default month, Monday weeks, UTC completeness; zero-valued empty buckets |
| Scenarios bar | Preset scenarios | – | client |
| Filters | Required billingCurrency; category, custom, card, original currency, type, trip, country, merchant, signed amount bounds | scoped filters/options | `/filter-options` billingCurrencies code/count entries across caller's whole dataset, separate missing billing count; original currencies remain separate. Amount bounds always use explicit billingCurrency |
| Stat cards | Signed billed total, all-row count, active-complete-month mean, comparison | aggregates | **computed** from signed `tx.billing_amount`; no per-transaction mean or original/purchase/refund/gross/share fields; undefined monthly average null |
| Stat cards | Click "Avg per month" → monthly trend view with average line | – | client selects month bucket |
| Chart: Bar by group | Top-25 signed groups, count, comparison bars | billing aggregates | **computed**; missing category/custom-category/country keys are null, with Uncategorized / No custom category / Unknown country fallback labels |
| Positive-only donut prototype | Requires adaptation for signed V1 data | UI/contract detail pending | No gross, purchase-only or absolute-value spending supplied to preserve this view |
| Chart: Trend | Signed net per bucket, previous series, monthly-only average line, partial flags | time series | Month bucket uses active complete-month mean, null when undefined; day/week/year current/comparison average is always null. Monthly-average stat remains available for every bucket |
| Chart: Stacked bar | Signed billing stacks by bucket/series dimension | signed matrix | **computed**; no purchases-only stacks |
| Chart: Heatmap / Pivot | Dimension × bucket net matrix, column totals, row totals | matrix (net) | **computed**; missing-value rows use the same null keys and fallback labels as groups |
| Breakdown table | Signed net, count, previous values/deltas, totals row | billing groups + comparison | **computed**; no per-transaction mean, share or original spending fields; missing groups retain their null keys, labels, counts and totals |
| Drill-down drawer | Matching transactions with native billing/source-original details and currency-separated billing summary | list + summary | `tx.*`, resolved categories, shared BillingSummary |
| Drill-down drawer | "Add as filter" | – | client |
| Drawer – transaction detail | Date, installment n/N, merchant, native billing, source original + rate magnitude, charge date, country, bank category, card, type, trip, ID | single transaction | `tx.*`, `ip.total_installments`, stored rate projected as a magnitude (or computed `abs(billing/original)`); trip ⚠; opaque public ID |
| Drawer – transaction detail | Change custom category (select from list incl. none) | write override | ⚠; same select-or-create model |

## 5. Review prototype (`prototypes\review`)

| Screen | Action | Required data | Source tables/fields |
|---|---|---|---|
| Nav | Pending count badge | count of unvalidated unknown-currency rows | **computed** `COUNT(trr.reason = UNKNOWN_CURRENCY)` (validating deletes the reason row) |
| Nav | "Reset demo data" | – | prototype only (not an API feature) |
| Review queue | Progress: validated of total, % | counts | **computed**: eligible foreign pending (`trr`) + validated (planned `currency_validations`) |
| Review queue | Search/autocomplete currency by code/name | manual currency catalogue | Queue `currencyOptions` contains `{code, name}` objects from pycountry's bundled ISO 4217 catalogue except XXX/XTS; local search over both fields, shared server membership validation |
| Review queue | Tabs Pending (n) / Validated (n) | counts | **computed** + ⚠ |
| Review queue | Filters: merchant country, from, to, merchant contains; clear | filtering + country options | `tx.merchant_country`, `tx.transaction_date`, `tx.merchant_name_raw`; no suggestion/confidence filters |
| Review queue | Table: select, date, merchant, country, original amount, native billing, implied-rate magnitude, selected currency | rows | `tx`, **computed** `abs(billing/original)`; null if undefined, no stored-sign changes |
| Review queue | Manually choose currency for a row | atomic validation | sets effective `tx.original_currency`, deletes UNKNOWN_CURRENCY, writes validation audit; immutable `source_original_currency` stays unchanged |
| Review queue | Select row/page/all filtered and apply chosen currency | atomic IDs-or-filter batch | Deduplicate IDs; whole-batch 404 for unavailable IDs, 409 for already validated; 201 empty filter match; unlimited count with no truncation/separate commits |
| Review queue | Undo (toast) after validation | atomic batch undo | Only currency-state changes invalidate; retain original membership evidence after move-back. Any changed member rejects whole undo with 409; already-undone visible batch returns 204 |
| Review queue | Paginate (40/page) | pagination | – |
| Validated tab | Date, merchant, source original amount, native billing, validated currency; "Move back to queue" (+ undo) | validations | `tx.*` + planned `currency_validations`; effective original currency shows the validated value. Already-pending visible rows return 204; unavailable rows return 404 |
| Category mapping | Bank categories with tx counts | distinct bank categories + counts | **computed** `GROUP BY tx.bank_category_raw` |
| Category mapping | Bank categories with English labels | labels | **i18n** |
| Category mapping | Map bank category → custom category (select / create new) / reset | mapping | ⚠ |
| Category mapping | Focus a bank category (filters table below) | – | client + list filter `bankCategory` |
| Category mapping | Result: your categories with counts | resolved category counts | **computed** + ⚠ |
| Category mapping | Newest-first merchant rules, match count, delete (+undo) | rules + match counts | ⚠ rules; match ANY normalized merchant substring, no regex/notes or changed/shadowed statistics |
| Category mapping | Add rule: normalized terms → category | create rule | ⚠ rules; newest match wins after override, before mapping/bank; delete Undo recreates newest; no position |
| Category mapping | Reset everything to bank categories (+undo) | – | Dropped |
| Category mapping | Transactions table: date, merchant, native billing, bank/custom category + source badge; merchant search, source filter, 25/page | list with resolution source | `tx.*` + **computed** resolution |
| Category mapping | Per-row override (select / create new) / clear | write override | ⚠ |

---

## 6. Internal fields not directly displayed

Listed for information only (no removal proposed).

| Table | Column(s) | Note |
|---|---|---|
| `owners` | `match_key`, `created_at` | internal matching key |
| `accounts` | *all* (`bank_code`, `account_number_enc`, `display_name`, `created_at`) | no screen shows or picks accounts; identity step only offers cards |
| `cards` | `account_id`, `created_at` | |
| `import_runs` | safe file labels, `finished_at`, `error_summary` | internal audit only; do not retain original filenames, paths, or uploads |
| `installment_plans` | `issuer_reference`, `merchant_name_norm`, `purchase_date`, `currency`, `created_at` | "first charge" was dropped |
| `transactions` | `issuer_reference`, `merchant_name_norm`, `channel`, `created_at` | Matching/search metadata; `billing_currency` is used and displayed natively, not relabelled ILS |
| `transaction_review_reasons` | `created_at` | |

`transactions.import_run_id` links inserted rows to import audit for caller-scoped history and transaction details.

## 7. Places needing computed / aggregated data

| Where | Computation |
|---|---|
| Card label everywhere | `owners.display_name` + issuer + decrypted last 4 |
| Effective category | override › newest matching merchant rule › mapping › bank category |
| List footer & group headers | BillingSummary count/unknown billing count/billingTotals; separate foreign unknown-original count where returned |
| Trip cards & trip detail | tag wins over auto membership; no same-user overlaps; signed billing sums by currency, category, day and outside-range; no daily averages |
| Trip candidates | not-member transactions, text search or date window [start−30d, end+7d], limit 8 |
| FX deviation | Compare rate magnitudes; preserve stored source signs; undefined deviation is null |
| Installment remaining | `(total_installments − installment_number) × billing_amount` |
| Analysis | signed billing net, all-row count, active-complete-month mean; no per-transaction/daily/weekly/yearly mean; zero buckets/missing groups; previous equal inclusive days, YoY Feb 29 → Feb 28; monthly-only average line |
| Currency review | implied-rate magnitude, manual catalogue, progress, validation/undo audit and immutable source currency |
| Category mapping | bank category counts, rule match statistics, resolved category counts |
| Import | dry-run counts, needs-review count (total + per reason), issues, identity questions (deterministic ids), SHA-256, detected issuer |
| Import history ownership | runs with visible inserted transactions on the caller's claimed cards (`tx.import_run_id`); file hash is not ownership |
| `/me` counters | pending currency validations, needs-review count per reason |
