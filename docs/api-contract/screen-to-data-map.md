# Screen → data map

Sources analysed

- **Prototypes** (`prototypes\`; the path given in the brief, `C:\Users\Pechisker\...\prototypes`, does not exist, so the repository copy was used):
  - `account\` – Login, Import wizard + history, Review queue (needs-review list; dropped – the currency queue is the only v1 review queue, Q-6/Q-19), Profile & cards, Sharing and Shared with me (both deferred – out of scope v1, G-02)
  - `transactions\` – Transactions list + detail panel, Trips list, Trip detail, Create trip, Analysis placeholder (deep links)
  - `analysis\` – Spending analysis (stats, 6 chart views, breakdown table, drill-down drawer, transaction detail in drawer)
  - `review\` – Currency-validation review queue (Pending / Validated tabs), Category mapping
- **Schema**: `src\bank_transactions\persistence\orm\models.py` and migration `0001_initial_schema.py`.

Legend for the *Source* column

- `table.column` – stored in the schema today.
- **computed** – derived on the server from stored columns (aggregation, formatting, decryption).
- **⚠ NO SOURCE** – the UI needs it but nothing in the schema holds it. See `gaps-and-questions.md` (gap ids `G-nn`).
- **client** – pure UI state; no API involvement.
- **i18n** – rendered by the frontend translation dictionary (English category labels, country names); the API returns only Hebrew bank names / ISO codes.
- **Deferred – out of scope v1 (G-02)** – sharing-related; not in the v1 contract.
- **Dropped** – removed by a review decision; not in the contract.

Decisions for every gap / question are in `gaps-and-questions.md`.

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
| Login | Sign in (email + password) | user identity, credential check | ⚠ NO SOURCE – proposed `users` (email, password hash) + session store (G-01) |
| Login | Quick-pick mock account | – | prototype only (not an API feature) |
| Top bar (all pages) | Show signed-in user, avatar initial | user name, email | ⚠ NO SOURCE – proposed `users.display_name`, `users.email` (G-01) |
| Top bar | Switch user (select) | – | Deferred – out of scope v1 (G-02); prototype simulation only (not an API feature) |
| Top bar | Log out | session | ⚠ NO SOURCE – session store (G-01) |
| Banner | "Merged view: your data plus N shared card(s) from X" | – | Deferred – out of scope v1 (G-02, G-03) |
| Import – step 1 | Choose / drag-drop .xlsx, "Use sample file" | file name | **client** – nothing is uploaded until the dry run (G-04: no server-side upload storage). SHA-256 and detected issuer are **computed** by `POST /imports` and shown after the dry run (→ later `ir.file_sha256`, `ir.issuer_code`) |
| Import – step 1 | Reject non-.xlsx | file type validation | **client** (extension); server re-checks (`415`) |
| Import – step 2 | Run dry run (file sent with the request); show "Rows read / Would import / Duplicates / Skipped / Needs review" | counts | `ir.rows_total`, `ir.rows_imported`, `ir.rows_duplicate`, `ir.rows_skipped`; needs-review total and per-reason counts (`UNKNOWN_CURRENCY` only, Q-19) **computed** in memory from the import report, not persisted (G-05) |
| Import – step 2 | Warnings list | safe warning sentences | **computed** (`ImportReport.warnings`); only a flattened `ir.error_summary` is persisted |
| Import – step 2 | Row issues table (row, sheet, problem) | per-row issues | **computed**, response-only (`ImportReport.issues`: `location.sheet_position`, `location.row`, `code`, `severity`, `message`); UI shows "Sheet n"; sheet name and outcome column dropped (G-06) |
| Import – step 2 | "Identity must be chosen" | NEEDS_INPUT status, unresolved cards | `ir.status = NEEDS_INPUT`; unresolved hints **computed** (`IdentityQuestion`, deterministic id from issuer + card hint so it survives the re-upload) |
| Import – step 3 | Show "Max ••••5530 could not be matched" | issuer + last 4 from the file | **computed** from file hint (plaintext only in memory) |
| Import – step 3 | Pick existing card | user's own cards (issuer, last4, nickname) | `cards.id`, `cards.issuer_code`, `cards.last4_enc` (decrypted), `cards.display_name`; "own" via `user_owners` (G-01) |
| Import – step 3 | Create new card with optional label | new card + label | creates `cards` row (`display_name` = label); owner/account resolution per identity service |
| Import – step 3 | Cancel import | – | **client** – nothing is stored server-side (G-04) |
| Import – step 4 | Confirm & "Import for real" (same file re-sent) | final counts, status SUCCESS/PARTIAL | inserts `tx`, `trr`, `ftd`, `ip`; `ir.*` |
| Import – step 4 | "Review N new needs-review rows →" | needs-review count (`UNKNOWN_CURRENCY` only) | **computed** in memory (G-05); single link to the currency queue (Q-6, Q-19) |
| Import history | List file, SHA-256, date, status (SUCCESS/PARTIAL), rows imported | per-user import runs | `ir.file_name`, `ir.file_sha256`, `ir.started_at`, `ir.status`, `ir.rows_imported`; "DRY RUN" rows dropped – dry runs are not shown (G-07); per-user scoping **computed** via `tx.import_run_id` → `cards` → `user_owners` (G-08); runs with zero imported transactions are not visible |
| Review queue (generic) | List date, description, amount, card (+shared badge), reason | – | Dropped – the currency-validation queue (review prototype) is the only review queue in v1 (Q-6, Q-19). `UNMATCHED_FOREIGN_FEE` / `AMBIGUOUS_INSTALLMENT` are stored in `trr` but internal-only; shared badge deferred (G-02) |
| Profile & cards | Show name, email, role "Owner" | user profile | ⚠ NO SOURCE – proposed `users` (G-01); role is a constant label `OWNER` (Q-18) |
| Profile & cards | My cards: issuer, last 4, owner, nickname | own cards | `cards.issuer_code`, `cards.last4_enc` (decrypted → last 4), `cards.display_name`, `owners.display_name`; "own" via `user_owners` (G-01) |
| Profile & cards | Save nickname | update card label | `cards.display_name` |
| Profile & cards | Shared cards (read-only, disabled Save) | – | Deferred – out of scope v1 (G-02) |
| Sharing | Invite owner by email, scope All / specific cards | – | Deferred – out of scope v1 (G-02) |
| Sharing | Invites I sent: to, scope, status; Cancel / Revoke | – | Deferred – out of scope v1 (G-02) |
| Sharing | Invites for me: Accept / Decline | – | Deferred – out of scope v1 (G-02) |
| Shared with me | View mode toggle "Merged" / "Only mine" | – | Deferred – out of scope v1 (G-02, G-03) |
| Shared with me | Cards per sharing owner (last4, nickname, issuer, read-only) | – | Deferred – out of scope v1 (G-02) |

## 3. Transactions prototype (`prototypes\transactions`)

| Screen | Action | Required data | Source tables/fields |
|---|---|---|---|
| Transactions list | View rows (16 toggleable columns) | date, charge date, merchant, country, city, effective category (+override dot), bank category, card label, type (+installment n/total), original amount/currency, billing ₪, effective rate, commission, trip, notes, review count | `tx.transaction_date`, `tx.charge_date`, `tx.merchant_name_raw`, `tx.merchant_country`, `tx.merchant_city`, `tx.bank_category_raw`, `owners.display_name`+`cards.issuer_code`+`cards.last4_enc` (card label **computed**), `tx.transaction_type`, `tx.installment_number`+`ip.total_installments`, `tx.original_amount`, `tx.original_currency`, `tx.billing_amount`, `ftd.effective_fx_rate`, `ftd.commission_amount`, `tx.notes`, `trr.reason` (count); country as ISO code, name via **i18n** (Q-8); custom category ⚠ (G-10); trip (at most one) ⚠ (G-11) |
| Transactions list | Toggle columns | – | client |
| Transactions list | Sort by any column (asc/desc) | server-side ordering | same fields; trip names ⚠ (G-11) |
| Transactions list | Filter: date range, month, category (effective, Hebrew or English input), custom category (incl. "no override"), card, currency (incl. "unknown"), amount range ₪, merchant text, type, country, trip, needs review / review reason | filter options + filtering | `tx.transaction_date`, `tx.bank_category_raw`, `cards.id`, `tx.original_currency` (NULL = unknown), `tx.billing_amount`, `tx.merchant_name_raw`/`merchant_name_norm` (search is merchant-only, notes not searched – Q-10), `tx.transaction_type`, `tx.merchant_country` (ISO code, Q-8), `trr.reason` (`UNKNOWN_CURRENCY` only, Q-19); English category input translated to Hebrew by **i18n** on the client (G-12); custom category ⚠ (G-10); trip ⚠ (G-11) |
| Transactions list | Filter chips, remove one, clear all | – | client (URL state) |
| Transactions list | Group by category/period/card/currency/merchant/type/trip with per-group count, net ₪, foreign totals; collapse groups | group aggregates over the whole filtered set | **computed** from the columns above |
| Transactions list | Footer: count, unknown-currency count, foreign totals per currency, commission total, net billed | aggregates over filtered set | **computed** (`SUM(billing_amount)`, `SUM(original_amount) GROUP BY original_currency`, `COUNT(original_currency IS NULL)`, `SUM(ftd.commission_amount)`) |
| Transactions list | Breadcrumb from Analysis (category › trip › month) | category label, trip name | `tx.bank_category_raw`, English label via **i18n** (G-12), trip ⚠ (G-11) |
| Detail panel | Header: merchant, date, city, country, amount, type badge, original amount | single transaction | `tx.*` |
| Detail panel | Review section: reasons; "Mark as reviewed" per reason | reasons | `trr.reason` (`UNKNOWN_CURRENCY` only) shown with a link to the currency queue; "Mark as reviewed" **dropped** (Q-21) |
| Detail panel | Details: tx date, charge date, card, type, notes | | `tx.*`, `cards.*` |
| Detail panel | Category: bank raw (+English), custom override, "counted as" | categories | `tx.bank_category_raw`; English via **i18n** (G-12); override ⚠ (G-10); effective **computed** |
| Detail panel | Override category (select existing or create new) / Clear | write override | ⚠ NO SOURCE (G-10); select-or-create, free text no longer creates categories implicitly (Q-5) |
| Detail panel | Currency & FX: original, stated rate, effective rate, commission, effective-vs-stated %, billed; unknown-currency callout | FX details | `tx.original_amount`, `tx.original_currency`, `ftd.stated_fx_rate`, `ftd.effective_fx_rate`, `ftd.commission_amount`; deviation **computed** |
| Detail panel | Installment plan: payment n of N, progress, plan total, per payment, remaining payments & amount | plan | `tx.installment_number`, `ip.total_installments`, `ip.total_amount`, `tx.billing_amount`; remaining **computed** |
| Detail panel | Installment plan: "first charge" date | – | Dropped (G-14) |
| Detail panel | Trip: the single trip (tagged – removable, or auto – static), add/move trip tag | membership + trips list | ⚠ NO SOURCE (G-11); at most one trip per transaction, tagging moves it (Q-12) |
| Trips list | Cards: name, date range, days, currency, billed ₪, total in trip currency, tx count, tagged vs auto counts | trips + aggregates | ⚠ trips (G-11); aggregates **computed** from `tx` |
| Trips list | "+ Create trip" | navigation | client |
| Create trip | Name, currency, start, end; validation (name required, dates required, end ≥ start) | create trip | ⚠ NO SOURCE (G-11) |
| Trip detail | Hero stats: total billed, spent in trip currency, other currencies, count, avg/day, FX commission; outside-dates note; unknown-currency note | trip aggregates | **computed** from `tx`, `ftd` + trip membership ⚠ (G-11) |
| Trip detail | By category (₪) bars, linking to filtered list | category aggregates | **computed** |
| Trip detail | By day chart (in-range members) | daily sums | **computed** |
| Trip detail | Manually tagged list + "Remove tag" | membership | ⚠ (G-11) |
| Trip detail | Auto-included list + "Tag" (pin) + "Exclude" | auto-match = same original currency & date in range; exclusions | `tx.original_currency`, `tx.transaction_date`; exclusions ⚠ (G-11); overlapping same-currency trips: earliest-starting trip claims auto members, a tag always wins (Q-12) |
| Trip detail | Excluded list + "Restore" | exclusions | ⚠ (G-11) |
| Trip detail | Add transactions: search (merchant, date, category) or default suggestions (30 days before → 7 after), "+ Tag" | candidate list | `tx.*` + membership ⚠ (G-11) |
| Trip detail | "View all in Transactions →" | navigation with `trip` filter | client |
| Analysis placeholder | Deep links into list (`category`, `month`, `trip`, `currency`, `review`, `type`) | – | client (relies on list filter params) |

## 4. Analysis prototype (`prototypes\analysis`)

| Screen | Action | Required data | Source tables/fields |
|---|---|---|---|
| Controls | Period preset / custom from–to | date range | client computes range; server filters `tx.transaction_date` |
| Controls | Compare: none / previous period / same period last year | previous-range aggregates | **computed** |
| Controls | Group by: category, custom category, day, week, month, year, card, currency, merchant, type, trip, country | grouping dimension | `tx.bank_category_raw`, custom ⚠ (G-10), `tx.transaction_date`, `cards`, `tx.original_currency`, `tx.merchant_name_raw`, `tx.transaction_type`, trip ⚠ (G-11; each transaction counted once), `tx.merchant_country` (ISO code; name via **i18n**) |
| Controls | Time bucket: auto/day/week/month/year | bucket | client resolves "auto"; server buckets |
| Scenarios bar | Preset scenarios | – | client |
| Filters | Add/remove chips: category, custom, card, currency, type, trip, country (multi-value OR), merchant contains, min/max ₪; clear all | filter values list + filtering | options **computed** (`DISTINCT`), plus G-10/G-11 |
| Stat cards | Net spending (+refunds netted), transactions (purchases vs refunds), avg per purchase, avg per month (complete months only, partial noted), original amount when single foreign currency; deltas vs comparison | aggregates | **computed** from `tx.billing_amount`, `tx.original_amount`, `tx.original_currency`; complete/partial uses "today" in UTC (Q-9) |
| Stat cards | Click "Avg per month" → trend view with average line | – | client |
| Chart: Bar by group | Top-25 groups, share %, count, ghost previous bar, average marker for period groupings | group aggregates + averages | **computed** |
| Chart: Donut | Top 8 + "Other (n)" gross positive groups | group aggregates | **computed** (top-N can be client-side) |
| Chart: Trend | Net per bucket, previous series, average + previous average, partial buckets marked | time series | **computed** |
| Chart: Stacked bar | Per bucket, purchases-only stacks by series dimension (top 7 + other), net avg line | bucket × dimension matrix (gross) | **computed** |
| Chart: Heatmap / Pivot | Dimension × bucket net matrix, column totals, row totals | matrix (net) | **computed** |
| Breakdown table | Per group: net, share, count, avg/purchase, original (single currency or "mixed"); previous, Δ, Δ% when comparing; totals row | group aggregates (+ previous) | **computed** |
| Drill-down drawer | Click any bar/slice/point/cell/row → list of matching transactions (date, merchant, installment tag, bank + custom category, ILS, original) with summary (net, count, avg/purchase, original) | filtered transaction list + summary | `tx.*`, custom ⚠ (G-10) |
| Drill-down drawer | "Add as filter" | – | client |
| Drawer – transaction detail | Date, installment n/N, merchant, ILS, original + implied rate, charge date, country, bank category, card, type, trip, ID | single transaction | `tx.*`, `ip.total_installments`, `ftd.effective_fx_rate` (or computed `billing/original`); trip ⚠ (G-11); ID → opaque `tx.id` |
| Drawer – transaction detail | Change custom category (select from list incl. none) | write override | ⚠ (G-10); same select-or-create model (Q-5) |

## 5. Review prototype (`prototypes\review`)

| Screen | Action | Required data | Source tables/fields |
|---|---|---|---|
| Nav | Pending count badge | count of unvalidated unknown-currency rows | **computed** `COUNT(trr.reason = UNKNOWN_CURRENCY)` (validating deletes the reason row, G-15) |
| Nav | "Reset demo data" | – | prototype only (not an API feature) |
| Review queue | Progress: validated of total, % | counts | **computed**: total = pending (`trr` rows) + validated (`currency_validations` rows ⚠ G-15) |
| Review queue | Suggestion groups: "N rows with rate a–b → CUR", confirm group; low-confidence count + "Show them" | per-row implied rate, suggested currency, confidence | rate **computed** = `tx.billing_amount / tx.original_amount` (or `ftd.stated_fx_rate` if present); suggestion **computed** (rate ranges) |
| Review queue | Tabs Pending (n) / Validated (n) | counts | **computed** + ⚠ (G-15) |
| Review queue | Filters: merchant country, from, to, suggested currency, merchant contains; clear | filtering + country options | `tx.merchant_country` (ISO code; name via **i18n**), `tx.transaction_date`, `tx.merchant_name_raw`; suggestion **computed** |
| Review queue | Table: select, date, merchant, country, original amount, billing ₪, stated rate, suggested currency (+confidence, hint) | rows | `tx.transaction_date`, `tx.merchant_name_raw`, `tx.merchant_country`, `tx.original_amount`, `tx.billing_amount`; rate/suggestion **computed** |
| Review queue | Confirm one suggestion; pick another currency from the fixed list (JPY, USD, EUR, GBP) | write validation | sets `tx.original_currency`, deletes the `UNKNOWN_CURRENCY` `trr` row, writes `currency_validations` ⚠ (G-15) |
| Review queue | Pick "Other" currency | – | Dropped (G-16, Q-7) |
| Review queue | Select row / page / all filtered; bulk apply JPY / other; confirm each suggestion; clear selection | bulk write by ids or filter | ⚠ (G-15) |
| Review queue | Undo (toast) after any validation | revert last batch | `currency_validation_batches` ⚠ (G-15); resets `tx.original_currency` to NULL, re-inserts the `trr` row, deletes the audit rows |
| Review queue | Paginate (40/page) | pagination | – |
| Validated tab | Date, merchant, original, billing, validated currency, bank cell; "Move back to queue" (+ undo) | validations | `tx.*` + `currency_validations` ⚠ (G-15). Validation now writes `tx.original_currency`, so the prototype's "(empty) – unchanged" bank cell no longer holds; the audit row records that the value came from validation |
| Category mapping | Bank categories with tx counts | distinct bank categories + counts | **computed** `GROUP BY tx.bank_category_raw` |
| Category mapping | Bank categories with English labels | labels | **i18n** (G-12) |
| Category mapping | Map bank category → custom category (select / create new) / reset | mapping | ⚠ (G-17) |
| Category mapping | Focus a bank category (filters table below) | – | client + list filter `bankCategory` |
| Category mapping | Result: your categories with counts | resolved category counts | **computed** + ⚠ (G-10, G-17, G-18) |
| Category mapping | Rules by merchant text: list with match / changed / kept-by-override / shadowed counts; delete (+undo) | rules + stats | ⚠ (G-18); stats **computed** |
| Category mapping | Add rule: terms (split on `,` `/` `\|`) → category (or create new) | create rule | ⚠ (G-18) |
| Category mapping | Reset everything to bank categories (+undo) | – | Dropped (Q-16) |
| Category mapping | Transactions table: date, merchant, ILS, bank category, your category + source badge (override/rule/mapping/bank); search merchant; filter by source; 25/page | list with resolution source | `tx.*` + **computed** resolution |
| Category mapping | Per-row override (select / create new) / clear | write override | ⚠ (G-10) |

---

## 6. Schema fields never used by any screen

Listed for information only (no removal proposed).

| Table | Column(s) | Note |
|---|---|---|
| `owners` | `match_key`, `created_at` | internal matching key |
| `accounts` | *all* (`bank_code`, `account_number_enc`, `display_name`, `created_at`) | no screen shows or picks accounts; identity step only offers cards |
| `cards` | `account_id`, `created_at` | |
| `import_runs` | `file_path`, `finished_at`, `error_summary` | history shows start date only; `error_summary` could back the warnings in history if desired |
| `installment_plans` | `issuer_reference`, `merchant_name_norm`, `purchase_date`, `currency`, `created_at` | "first charge" was dropped (G-14) |
| `transactions` | `issuer_reference`, `billing_currency`, `merchant_name_norm`, `channel`, `created_at` | `merchant_name_norm` is used internally for search/grouping, not displayed; `billing_currency` is always shown as ₪ |
| `transaction_review_reasons` | `created_at` | |

`transactions.import_run_id` is no longer unused: it attributes import runs to a user for the history (G-08) and can count needs-review rows per run (G-05).

## 7. Places needing computed / aggregated data

| Where | Computation |
|---|---|
| Card label everywhere | `owners.display_name` + issuer + decrypted last 4 |
| Effective category | override › first matching rule › bank-category mapping › bank category (needs G-10/17/18) |
| List footer & group headers | count, net ₪, per-currency original sums, unknown-currency count, commission sum |
| Trip cards & trip detail | membership (tagged ∪ auto − excluded; at most one trip per transaction, earliest-starting trip claims auto members, tag wins), totals, per-currency, avg/day, by category, by day, outside-range |
| Trip candidates | not-member transactions, text search or date window [start−30d, end+7d], limit 8 |
| FX deviation | `effective_fx_rate / stated_fx_rate − 1` |
| Installment remaining | `(total_installments − installment_number) × billing_amount` |
| Analysis | net/gross/refunds/counts, avg per purchase, avg per complete bucket, previous-period/YoY ranges, groups, time series, dimension × bucket matrix |
| Currency review | implied rate, suggested currency + confidence, group rate ranges, progress |
| Category mapping | bank category counts, rule match statistics, resolved category counts |
| Import | dry-run counts, needs-review count (total + per reason), issues, identity questions (deterministic ids), SHA-256, detected issuer |
| Import history ownership | runs whose imported transactions belong to cards of the user's owners (`tx.import_run_id`) |
| `/me` counters | pending currency validations, needs-review count per reason |
