# Bank Transactions Importer — Design

Design of the implemented importer library and CLI. Diagrams: [sequence-diagram.md](sequence-diagram.md). Usage: [../README.md](../README.md). The schema in §4 is the importer schema; the REST API adds tables and columns, listed in [api-design-phase-2.md](api-design-phase-2.md) §B.6.

## 1. Goal

A reusable Python module (`bank_transactions`) that:

1. Parses credit-card transaction history Excel files from three Israeli issuers — **Isracard, Max, Cal** — each with its own layout.
2. Normalizes them into one domain model: amounts, currencies, transaction vs. charge dates, merchant, bank category, installments, foreign-currency commissions.
3. Persists them to PostgreSQL for later expense analysis across cards with different billing cycles.

Delivered as a library plus a thin CLI, to be embedded into a larger app. A future module will re-categorize transactions; the bank's own category is stored verbatim and never overwritten.

## 2. Key Decisions

| Topic | Decision |
|---|---|
| Scope | Library + CLI (`import`, `detect`, `init-db`) |
| Persistence | SQLAlchemy 2.0 ORM + Alembic; PostgreSQL 15+ |
| Sensitive data | Card last-4 and bank account numbers stored **deterministically encrypted** (AES-SIV), never as plaintext; decryptable for display |
| Deduplication | Prefer a nullable issuer reference (with installment number); otherwise compare transaction-group counts across import runs, preserving every repeated row within one file |
| Installments | One transaction row per charge, linked to an `installment_plans` row |
| Foreign transactions | `transactions` holds the final billed amount incl. commission; `foreign_transaction_details` holds the commission with its own currency + rates |
| Foreign fee rows | Folded into the parent transaction — never persisted as a transaction |
| Identity | `owners` → `accounts` → `cards`, all links mandatory; deterministic ciphertext is used directly for equality lookup, while issuer + last-4 is non-unique and ambiguous matches are resolved interactively |
| Import audit | `import_runs` is audit data, committed before any domain write and retained even when the attempt fails |
| Raw source rows | Not stored |
| Malformed rows | Skip the affected row and report it; file-level structural errors fail the import attempt |
| Pending rows | Not imported; each issuer parser detects them, skips them and reports `PENDING_TRANSACTION` |
| Unknown currency | `NULL` currency + a `UNKNOWN_CURRENCY` review reason |

## 3. Source Formats

`issuer_code` ∈ {`ISRACARD`, `MAX`, `CAL`}, one parser each.

### Isracard — `9462_02_2026.xlsx`
- Single sheet `פירוט עסקאות`, banner block above the table.
- Banner supplies card product text + last-4 (`פלטינה מסטרקארד - 9462`), owner (`על שם ...`), statement month (`פברואר 2026`) and the **charge date for every row** (`לחיוב ב-10.02`).
- Columns: `תאריך רכישה | שם בית עסק | סכום עסקה | מטבע עסקה | סכום חיוב | מטבע חיוב | מס' שובר | פירוט נוסף`; `מס' שובר` is persisted as the nullable `issuer_reference`.
- Per-currency totals (₪ and $) are detected, skipped and used as a parse self-check.
- Discounts appear in `פירוט נוסף` (`הנחה ₪24.00`) with charge amount 0. Dates `dd.MM.yy`. No account number.

### Max — `transaction-details_export_*.xlsx`
- Two sheets, identical 16-column layout: `עסקאות במועד החיוב` (domestic) and `עסקאות חו"ל ומט"ח` (foreign); the foreign sheet also yields FX detail rows.
- Three banner rows (`כל המשתמשים (2)`, `כל הכרטיסים (2)`, period `07/2026` or `2025-08-01-2026-08-01`); header on row 4.
- **Multi-card file** — rows are split by the `4 ספרות אחרונות של כרטיס האשראי` column.
- Columns: txn date, merchant, category, card last-4, type (`רגילה`/`תשלומים`/`חיוב חודשי`/`חיוב עסקות מיידי`), charge amount + currency, original amount + currency, charge date, notes, tags, discount club, discount key, channel (`אינטרנט`/`טלפוני`/`תשלום בנייד`), FX rate.
- Installments in notes (`תשלום 4 מתוך 10`); `סכום עסקה מקורי` holds the **plan total** (₪1300), not the monthly charge (₪130).
- Trailing `סך הכל` rows skipped. No owner name, no account number.
- Foreign merchant strings = name + city + 2-letter country → split into `merchant_city` / `merchant_country`.
- Edge case: blank original currency (amount 660, rate 0.0183) → `NULL` + a `UNKNOWN_CURRENCY` review reason. Dates `dd-MM-yyyy`.

### Cal — `פירוט חיובים לכרטיס ויזה 0538 - 13.09.26 (1).xlsx`
- Sheet name carries bank + account number (`לאומי לישראל 891-7221038`); title row carries card product text + last-4.
- Banner `עסקאות לחיוב ב-10/09/2026: 645.13 ₪` → charge date + expected total (self-check).
- Multi-line header cells (`תאריך\nעסקה`) → header normalization collapses newlines.
- Columns: date, merchant, txn amount, charge amount (blank means pending and is skipped), type, `ענף` (category), notes. Real `datetime` cells. No owner name.

## 4. Database Schema

```sql
owners(
  id            PK,
  display_name  NOT NULL,
  match_key     NOT NULL UNIQUE,        -- folded display_name: trimmed, whitespace-collapsed,
                                        -- apostrophe/gershayim unified, casefolded
  created_at)

accounts(
  id                  PK,
  owner_id            NOT NULL FK -> owners,
  bank_code           NULL,             -- e.g. LEUMI
  account_number_enc  NULL,             -- deterministic AES-SIV ciphertext; decryptable for display
  display_name        NULL,
  created_at,
  UNIQUE(bank_code, account_number_enc))

cards(
  id            PK,
  owner_id      NOT NULL FK -> owners,
  account_id    NOT NULL FK -> accounts,
  issuer_code   NOT NULL,               -- ISRACARD | MAX | CAL
  last4_enc     NOT NULL,               -- deterministic AES-SIV ciphertext of the last 4 digits
  display_name  NULL,                   -- optional label; UI normally shows issuer + decrypted last-4
  created_at)

-- Last-4 is search evidence, not a unique card identity. Different cards can share it.
CREATE INDEX ix_cards_issuer_last4
  ON cards(issuer_code, last4_enc);

CREATE INDEX ix_cards_account_issuer_last4
  ON cards(account_id, issuer_code, last4_enc);

import_runs(
  id PK, file_path, file_name, file_sha256, issuer_code,
  started_at, finished_at,
  status,                               -- RUNNING | SUCCESS | PARTIAL | NEEDS_INPUT | FAILED
  rows_total, rows_imported, rows_duplicate, rows_skipped, error_summary)

installment_plans(
  id                 PK,
  card_id            NOT NULL FK -> cards,
  issuer_reference   NULL,                      -- stable issuer plan/voucher id when available
  merchant_name_norm NOT NULL,
  purchase_date      NOT NULL,                  -- the transaction_date every installment row of
                                                 -- this plan carries (stable across statements)
  total_amount       NUMERIC(14,2) NOT NULL,   -- plan total (₪1300), not the per-charge amount
  currency           CHAR(3) NOT NULL,
  total_installments SMALLINT NOT NULL,
  created_at,
  UNIQUE(card_id, merchant_name_norm, purchase_date, total_amount, currency, total_installments))

CREATE UNIQUE INDEX ux_installment_plans_issuer_reference
  ON installment_plans(card_id, issuer_reference)
  WHERE issuer_reference IS NOT NULL;

transactions(
  id                  PK,
  card_id             NOT NULL FK -> cards,
  import_run_id       NOT NULL FK -> import_runs,
  issuer_reference    NULL,                    -- stable issuer transaction/voucher id when available
  transaction_date    NOT NULL,                -- when the purchase happened
  charge_date         NOT NULL,                -- pending rows are not imported
  billing_amount      NUMERIC(14,2) NOT NULL,  -- final billed amount, incl. FX commission
  billing_currency    CHAR(3) NULL,
  original_amount     NUMERIC(14,2) NULL,      -- amount transacted at the merchant
  original_currency   CHAR(3) NULL,
  merchant_name_raw   NOT NULL,
  merchant_name_norm  NOT NULL,
  merchant_city       NULL,
  merchant_country    NULL,
  bank_category_raw   NULL,
  transaction_type    NOT NULL,                -- REGULAR | INSTALLMENTS | RECURRING | IMMEDIATE_CHARGE
  channel             NULL,                    -- INTERNET | PHONE | MOBILE | UNKNOWN
  notes               NULL,
  installment_plan_id NULL FK -> installment_plans,
  installment_number  NULL,                    -- the "4" of "תשלום 4 מתוך 10"
  created_at)

CREATE UNIQUE INDEX ux_transactions_issuer_reference
  ON transactions(card_id, transaction_date, issuer_reference, installment_number)
  NULLS NOT DISTINCT
  WHERE issuer_reference IS NOT NULL;

transaction_review_reasons(
  transaction_id  NOT NULL FK -> transactions ON DELETE CASCADE,
  reason          review_reason_enum NOT NULL,
  created_at,
  PRIMARY KEY (transaction_id, reason))

foreign_transaction_details(
  transaction_id      PK FK -> transactions ON DELETE CASCADE,
  commission_amount   NUMERIC(14,2) NOT NULL DEFAULT 0, -- denominated in billing_currency
  stated_fx_rate      NUMERIC(18,6) NULL,       -- rate printed in the file, when present
  effective_fx_rate   NUMERIC(18,6) NOT NULL)   -- billing_amount / original_amount
```

Enumerated types (PostgreSQL `ENUM`s, mirrored by Python `enum.StrEnum` in `domain/enums.py`):

| Enum | Values |
|---|---|
| `transaction_type_enum` | `REGULAR`, `INSTALLMENTS`, `RECURRING`, `IMMEDIATE_CHARGE` |
| `channel_enum` | `INTERNET`, `PHONE`, `MOBILE`, `UNKNOWN` |
| `review_reason_enum` | `UNKNOWN_CURRENCY`, `UNMATCHED_FOREIGN_FEE`, `AMBIGUOUS_INSTALLMENT` |
| `import_status_enum` | `RUNNING`, `SUCCESS`, `PARTIAL`, `NEEDS_INPUT`, `FAILED` |

`review_reason` is written only by the parser, never by a user, so it is a closed set. A transaction can carry **several** reasons at once, so they live in `transaction_review_reasons` — one row per (transaction, reason). Each reason is an answerable question in its own right, which lets the future UI ask one targeted question per row (*"which currency was this?"*, *"is this fee part of the purchase above?"*).

Derived at query time, never stored: `is_foreign` (an FX detail row exists), `is_refund` (`billing_amount < 0`), `needs_review` (a `transaction_review_reasons` row exists), converted-amount-excluding-commission (`billing_amount - commission_amount`).

Analysis indexes: `(card_id, charge_date)`, `(card_id, transaction_date)`, `(merchant_name_norm)`, `(bank_category_raw)`, `(installment_plan_id)`, `transaction_review_reasons(reason)`.

## 5. Architecture (SOLID)

```
src/bank_transactions/
├── ports/                 abstract base classes (ABCs) only — no concrete/library imports;
│   │                      the boundary every other layer depends on instead of a concrete class
│   ├── workbook_reader.py WorkbookReader(ABC) — open(path) -> WorkbookView
│   ├── parser_selector.py ParserSelector(ABC) — select(WorkbookView) -> StatementParser
│   └── identifier_protector.py IdentifierProtector(ABC) — encrypt(value) / decrypt(token)
├── domain/
│   ├── enums.py           TransactionType, Channel, ImportStatus, ReviewReason
│   ├── money.py           Money (Decimal + Currency)
│   ├── models.py          StatementContext, OwnerHint, AccountHint, CardHint, CardHintKey,
│   │                      ParsedTransaction, ParsedFxDetail, ParsedInstallmentRef,
│   │                      StatementParseResult, StatementTotals, SourceLocation,
│   │                      FeeCandidate (frozen dataclasses;
│   │                      ParsedTransaction.review_reasons is a frozenset)
│   └── errors.py          ParseError, UnknownFormatError,
│                          IdentityUnresolvedError, PersistenceError
├── parsing/
│   ├── interfaces.py      StatementParser (ABC, public contract, §5.1)
│   ├── registry.py        ParserRegistry(ParserSelector) — confidence-based selection
│   ├── workbook.py        ExcelWorkbookReader(WorkbookReader) / WorkbookView / SheetView
│   │                      (openpyxl adapter)
│   ├── header_locator.py  HeaderLocator (multi-line header aware)
│   ├── column_map.py      ColumnMap / ColumnSpec (aliases, required, converter)
│   ├── normalizers/       dates.py, amounts.py, currency.py, text.py
│   ├── shared/            issuer-agnostic algorithms the parsers compose
│   │   ├── fee_folder.py       ForeignFeeFolder — generic nearest-match fold, driven by
│   │   │                       the FeeCandidate markers the parser supplies
│   │   ├── fx_calculator.py    EffectiveRateCalculator — pure arithmetic
│   │   ├── installment_grouper.py InstallmentPlanBuilder — groups ParsedInstallmentRefs
│   │   │                       into plans keyed by issuer reference when available, else by
│   │   │                       card + merchant + purchase (transaction) date + plan total +
│   │   │                       currency + installment count; the parser supplies the parsed
│   │   │                       4-of-10 values
│   └── banks/
│       ├── tabular_base.py  TabularStatementParser (template method; owns the shared
│       │                    algorithms and the default finalize pipeline)
│       ├── isracard.py      IsracardParser
│       ├── max_card.py      MaxParser
│       └── cal_visa.py      CalVisaParser
├── identity/
│   ├── interfaces.py      IdentityResolver (ABC)
│   ├── models.py          ResolvedCards (CardHintKey → CardId)
│   ├── crypto.py          IdentifierCipher(IdentifierProtector) — deterministic AES-SIV
│   │                      encrypt/decrypt
│   ├── cli_resolver.py    CliIdentityResolver (interactive prompts)
│   └── noninteractive.py  NonInteractiveResolver (raises IdentityUnresolvedError)
├── persistence/
│   ├── db.py              Engine/session factory
│   ├── orm/models.py      SQLAlchemy declarative models (§4)
│   ├── commands.py        NewTransaction, NewTransactionAggregate
│   ├── mappers.py         ParsedTransaction + resolved IDs → insertion commands
│   ├── repositories.py    Owner/Account/Card/Transaction/InstallmentPlan/ReviewReason/
│   │                      ImportRun repositories
│   └── uow.py             SqlAlchemyUnitOfWork
├── services/
│   ├── import_service.py  StatementImportService.import_file(path) — constructor takes
│   │                      WorkbookReader, ParserSelector, IdentifierProtector,
│   │                      IdentityResolver, UnitOfWork (all abstractions from `ports/`
│   │                      or the interfaces above; never a concrete adapter type)
│   └── report.py          ImportReport, RowIssue, RowIssueCode, IssueSeverity
├── config.py              Settings (DSN, cipher key, year window,
│                          fee keywords, fee date window)
├── bootstrap.py           build_import_service(settings) — the only module allowed to
│                          import every concrete adapter; constructs them and injects
│                          them into StatementImportService via its constructor
└── cli.py                 calls bootstrap.build_import_service(settings)
alembic/   tests/   docs/sequence-diagram.md
```

- **SRP** — parsing, normalizing, identity, persistence are separate classes; inside parsing, issuer-agnostic algorithms are separate from issuer-specific reading.
- **OCP** — a new issuer is one new `StatementParser` subclass registered in `ParserRegistry`; nothing else changes.
- **LSP** — every parser honours the same contract (§5.1); the importer never branches on issuer.
- **ISP** — narrow abstract interfaces: `StatementParser`, `IdentityResolver`, `WorkbookReader`, `ParserSelector`, `IdentifierProtector`, per-entity `Repository`, `UnitOfWork`.
- **DIP** — `StatementImportService` depends only on the ABCs above, never on `ExcelWorkbookReader`, `ParserRegistry`, `IdentifierCipher`, openpyxl or SQLAlchemy directly. `ports/` holds `WorkbookReader`, `ParserSelector`, `IdentifierProtector` as pure ABCs with no library imports, so the service module's import graph never pulls in openpyxl or the encryption library. Concrete adapters (`ExcelWorkbookReader`, `ParserRegistry`, `IdentifierCipher`) each subclass their port. `bootstrap.py` is the single composition root that constructs the concrete adapters and wires them into `StatementImportService`; swapping an adapter (e.g. a UI resolver instead of the CLI one, or a CSV reader instead of Excel) touches only `bootstrap.py`. **Parsers never see a repository, a session or a database id** — they return plain domain objects and the importer does every lookup and write.

### 5.1 Parser Contract

The public interface contains only operations required by import orchestration.

```python
class StatementParser(ABC):
    issuer_code: str

    @abstractmethod
    def sniff(self, wb: WorkbookView) -> float: ...           # 0.0 – 1.0 confidence

    @abstractmethod
    def read_context(self, wb: WorkbookView) -> StatementContext: ...

    @abstractmethod
    def read_transactions(self, wb: WorkbookView,
                          ctx: StatementContext) -> StatementParseResult: ...
```

- `read_context` is cheap — banners and sheet names only, no transaction rows. `TabularStatementParser` composes its protected hooks (`_get_owner_data`, `_get_account_data`, `_get_card_data`) with period, statement charge date and declared totals, so the importer can resolve identity and fail fast before any row is parsed.
- `read_transactions` walks the rows, using its protected `_get_installment_data` / `_get_foreign_data` hooks per row, then runs `finalize` over the whole set. It always returns the same explicit result:

```python
@dataclass(frozen=True)
class StatementParseResult:
    transactions: tuple[ParsedTransaction, ...]
    issues: tuple[RowIssue, ...]
    rows_seen: int
    calculated_totals: StatementTotals
```

- `RowIssue` contains a `SourceLocation` (sheet + row), `RowIssueCode`, severity and a sanitized message. Pending rows are informational; malformed transaction rows are errors.
- Parsers collect recoverable row issues, skip the affected rows and continue. File-level structural failures such as missing sheets, headers or required columns raise `ParseError` and fail the import attempt.
- Declared totals belong to `StatementContext`; `StatementParseResult.calculated_totals` contains totals from all valid charged source rows before deduplication. The import service compares the two.
- `StatementContext.cards` is always a list; each immutable `ParsedTransaction` carries a `CardHintKey`. Identity resolution returns a separate immutable `ResolvedCards` map from that key to `CardId`; parser output is never modified with database IDs.
- The protected hooks are implementation details of `TabularStatementParser` and its issuer subclasses. Concrete parser tests may exercise them directly, but clients depend only on `StatementParser`.
- **Issuer-specific, owned by the parser**: locating banners and headers, splitting rows per card, recognising pending rows and skipping them with a `PENDING_TRANSACTION` row issue, reading an issuer reference when available, recognising `תשלום 4 מתוך 10` versus `פירוט נוסף`, knowing that Max's *original amount* column holds the plan total, knowing which sheet is the foreign one, recognising an `עמלת מט"ח` row, reading the printed FX rate, reading the discount-club columns.
- **Issuer-agnostic, shared in `parsing/shared/`**: folding a fee into its parent by card + date window, grouping installment refs into plans and `billing_amount / original_amount`. `TabularStatementParser.finalize` runs them in order; an issuer overrides only the step it genuinely differs on.
- No parser reads or writes the database, and none of them import SQLAlchemy.

## 6. Import Flow

1. `StatementImportService.import_file(path, dry_run=False)`.
2. SHA-256 of the file → `import_runs` row (`RUNNING`) in a short committed audit transaction. This audit row is deliberately written before any owner, account, card, installment plan or transaction data and survives a later failure.
3. `ExcelWorkbookReader` → `WorkbookView`; `ParserRegistry` picks a parser by `sniff()` confidence (none → `UnknownFormatError`).
4. `parser.read_context(wb)` → owner hint, account hint, card hints, period, statement charge date, declared totals. Last-4 and account numbers exist only in memory.
5. **Identity gate — after the audit run exists and before any transaction row is parsed or written.** `IdentifierCipher` deterministically encrypts each distinct last-4 and any account number. The ciphertext itself is used for indexed equality lookup; there is no separate fingerprint.
   - Resolve the owner hint and, when present, match the account by `(bank_code, account_number_enc)`.
   - Query card candidates by `(account_id, issuer_code, last4_enc)` when the account is known; otherwise narrow `(issuer_code, last4_enc)` candidates by the resolved owner.
   - Exactly one candidate with no conflicting hints → reuse its owner, account and card.
   - No candidates → `IdentityResolver` asks to pick or create the owner and account, then creates the card. A card `display_name` may be entered but is optional.
   - Several candidates or conflicting owner/account hints → `IdentityResolver` requires an explicit card selection; the importer never chooses the first match.
   - `--non-interactive` with zero, multiple or conflicting candidates → `IdentityUnresolvedError`; the run ends `NEEDS_INPUT` with no identity or transaction changes and lists the cards needing attention.
   - Interactive identity selections and creations are persisted atomically, producing a `CardHint → card_id` map before transaction parsing starts.
6. `parser.read_transactions(wb, ctx)` → `StatementParseResult`: finalized charged transactions, row issues, source-row count and calculated totals. Pending rows produce informational `PENDING_TRANSACTION` issues; malformed rows produce error issues. File-level structural failures raise `ParseError`.
7. The importer reports every row issue and continues with valid charged transactions. Error-level row issues make the final run `PARTIAL`; informational pending-row issues do not. It compares `ctx.declared_totals` with `parse_result.calculated_totals`, both representing charged source rows before deduplication — a per-currency self-check, not tied to any single row. A mismatch never blocks the import; it is a QA signal that some row was likely mis-parsed, since the importer cannot tell which one.
8. The importer uses `ResolvedCards` to build comparison keys without modifying parsed transactions, then groups reference-less rows as described in §7. In one `UnitOfWork` it upserts installment plans, skips known issuer references, compares incoming and existing group counts, and selects only new parsed transactions. `TransactionMapper` combines each selected `ParsedTransaction` with its resolved `CardId`, the current `ImportRunId` and any resolved installment-plan ID to create an immutable `NewTransactionAggregate`; the repository inserts those aggregates with their review reasons and FX details. Existing transactions are never updated or deleted.
9. `import_runs` finalized: a totals mismatch is appended as a short note to `error_summary` (no new column, and no transaction is ever tagged with it); `ImportReport` returned (imported / duplicate / skipped / needs-review / warnings), the same note surfaced as a warning.

**Failure lifecycle** — `import_file` wraps everything after `start_run` in a single try/except so `RUNNING` always reaches a terminal status:
   - `UnknownFormatError` → `finish_run(FAILED, ...)`, re-raised.
   - `IdentityUnresolvedError` (non-interactive only) → `finish_run(NEEDS_INPUT, ...)`, returned as an `ImportReport` — not an exception, since there is no user to prompt.
   - `ParseError` (file-level structural failure) → `finish_run(FAILED, ...)`, re-raised.
   - `UnitOfWork.commit()` failure or any other unexpected exception → the UoW's own rollback undoes the domain transaction; `finish_run(FAILED, ...)` is then written in its own separate, always-committed transaction, and the exception is re-raised.
   - A hard process kill between `start_run` and `finish_run` cannot be caught. This is a documented limitation: each `import` invocation first sweeps `import_runs` rows stuck in `RUNNING` with no update for over an hour and marks them `FAILED` with an "interrupted" note before doing anything else.

## 7. Deduplication

Re-importing overlapping files (a yearly export plus monthly statements) must not double-count, while genuinely repeated purchases must survive.

- When `issuer_reference` is present, `(card_id, transaction_date, issuer_reference, installment_number)` identifies the transaction; a missing installment number equals another missing one. An existing key is counted as a duplicate and skipped; a new key is inserted. Of repeated keys inside one file, the first is selected and the later copies are duplicates.
- Without an issuer reference, the comparison key is `(card_id, transaction_date, merchant_name_norm, original_amount, original_currency, installment_number)`. Amount and currency comparisons are NULL-safe.
- The importer groups all rows from the current file before querying existing reference-less transactions. For each comparison key, `insert_count = max(0, incoming_count - existing_count)`. The remaining incoming rows are counted as duplicates and skipped.
- This preserves repeated purchases within one file: three identical coffee-shop rows in the first file insert three transactions. A later file containing the same three rows inserts none; if it contains four, only the surplus fourth row is inserted.
- Existing transactions are never overwritten or deleted, and their original `import_run_id` never changes. Pending rows are excluded by the parsers, so a later export does not need to update a pending transaction into a charged transaction.
- `import_run_id` is provenance only: it is not part of either deduplication key. Existing counts are queried before any transaction from the current run is inserted, so the audit row itself cannot suppress repeated rows within the same file.
- There is no unique constraint on the comparison key because genuine repeated purchases are valid.

## 8. Sensitive Data

- Card last-4 and bank account numbers are never written as plaintext. They are encrypted with **deterministic AES-SIV** using a key from `BT_CIPHER_KEY` (environment/secret store, never source control).
- Determinism allows indexed equality lookups directly on the ciphertext; decryption allows the UI to show `•••• 4396` later. Card last-4 ciphertext is deliberately non-unique.
- Identifier type is authenticated as associated data (`card-last4` vs. `account-number`) so ciphertext cannot be substituted between domains.
- Plaintext identifiers live only in memory during a single import and are excluded from logs, exception messages and `ImportReport`.

## 9. Foreign Transactions & Commission

- A foreign row yields `original_amount`/`original_currency` plus the billed amount; the file may also print a rate.
- A separate commission row (`עמלת מט"ח` and similar) is recognised by the **parser** (the wording and placement are issuer-specific) and handed to the shared `ForeignFeeFolder`, which matches it to the nearest unmatched foreign transaction on the same card within a date window and folds it in; the fee row itself is not persisted as a transaction.
- If no parent is found, the fee row is persisted as an ordinary transaction with a `UNMATCHED_FOREIGN_FEE` review reason, so period totals still reconcile.
- `transactions.billing_amount` = converted amount + commission.
- Commission is always denominated in `transactions.billing_currency` (the card's local/settlement currency), so no separate commission-currency field is stored.
- Every foreign transaction has exactly one detail row. `commission_amount` is `0` when there is no fee, `stated_fx_rate` stores the issuer's printed rate when present, and `effective_fx_rate` is always derived as `billing_amount / original_amount`.
- An imported foreign transaction must have a non-zero `original_amount`, a `billing_amount` and a `billing_currency`. A missing or zero original amount produces an error-level `MISSING_OR_ZERO_FX_ORIGINAL_AMOUNT` row issue and the row is skipped. `original_currency` may remain `NULL`; the numeric rate is still calculated and the transaction carries `UNKNOWN_CURRENCY`.
- Effective-rate arithmetic uses full `Decimal` precision and is rounded only when persisted to `NUMERIC(18,6)`.

## 10. Implemented behavior rules

These rules refine the flow above; where they differ, they win.

1. **One live unit of work for identity and domain data.** The identity gate, the plan upserts and the transaction insertion share a single `UnitOfWork`. Card identities are flushed (so they receive real ids) before `read_transactions` runs. A normal run commits that unit once, after insertion. A dry run always rolls the whole unit back, including identities the run created. The audit record is written through separate, independent units of work and is always committed.
2. **Reference keys include the installment number** (§7). Stored rows are never updated or deleted.
3. **Per-card identity questions.** The gate resolves every card in the file before any transaction is read. A card with exactly one consistent stored candidate is reused without a question, even when the file omits identity hints. With more than one consistent candidate, or none, the resolver receives an `IdentityQuestion` with explicit choices; the service never picks the first. A non-interactive run collects every unresolved card, rolls back all identity changes and returns `NEEDS_INPUT` without parsing a row.
4. **Account lookup returns every match.** `AccountRepository.find_by_number(bank_code, encrypted_number)` returns a tuple (none, one or many, ordered by id), because different or unknown banks may reuse the same digits. An account is determined only when exactly one match is consistent with the owner; otherwise the user is asked.
5. **Max identity column scan.** Max statements carry the card last-4 in a column rather than in a banner; the Max parser scans that identity column and reports one card per distinct value.
6. **Ciphertext only.** Card last-4 values and account numbers are encrypted with `encrypt(kind, value)` / `decrypt(kind, token)`, bound to the identifier type, before any command reaches a repository. Owner, account and card labels that contain a known identifier are rejected, and reports, audit rows and failure summaries never contain plaintext identifiers.
7. **Report and audit counts.** `rows_total` is the number of data rows read (banners, footers and blank rows excluded). `rows_skipped` is rows read minus transactions produced (pending, malformed and folded fee rows). `duplicates` is valid rows minus selected rows. On a dry run the report shows the proposed counts with `dry_run=True`, while the audit row records `rows_imported=0` and a summary starting "DRY RUN: nothing was committed", so the audit never claims rows that were not committed.
8. **Declared totals are a warning only.** The declared totals are compared with all valid charged rows before deduplication. A mismatch adds a warning, an issue note and an error-summary note; it does not force `PARTIAL`. A file that declares no totals reports the check as unavailable. `PARTIAL` is set only by `ERROR` issues.
9. **Failure handling.** After the run is started, every failure ends in a terminal audit record: `NEEDS_INPUT` for unresolved identities, otherwise the domain unit is rolled back and a separate committed `FAILED` record is written before the error is re-raised. A record that was already finished (for example swept as stale) is never overwritten. Summaries never contain the text of untrusted errors.
10. **Two-digit year window.** `BT_TWO_DIGIT_YEARS_BACK` (default 80) selects the 100-year window `[anchor - back, anchor + 99 - back]` for two-digit years; the statement anchor priority is unchanged.
11. **Precision boundary.** Parsed money and FX values keep full `Decimal` precision. Persistence commands round exactly once with `ROUND_HALF_UP`: money to 2 places, rates to 6 (`domain/money.py`). Comparison and plan keys use the same amount rounding. Source values are not rejected for having extra decimals.
12. **Stale-run sweep and audit labels.** At the start of every import, `RUNNING` audit rows older than one hour (by `started_at`; there is no heartbeat) are marked `FAILED`. `file_path`/`file_name` hold hash-based labels (`sha256/<hash>`, `<hash[:12]><suffix>`), never the original path or name (`services/audit_trail.py`).
13. **Installment-plan upsert.** The unconditional natural unique key is kept. An upsert inserts with `ON CONFLICT DO NOTHING`, then selects the plan by natural key and by issuer reference; if they disagree, it fails with `PersistenceError` and never merges or weakens the key.
14. **Row classification.**
    - Pending: a missing billed amount or charge date (skipped as `PENDING_TRANSACTION`, severity INFO, not a partial failure). A future charge date alone does not make a row pending.
    - Foreign: Max foreign-sheet rows (even with equal currencies), differing known currencies, or explicit foreign evidence. A printed rate without a fee row never implies a commission.
    - Fee folding window defaults to 3 days and, with the issuer fee keywords, is configurable.
    - Refund original amounts are kept as supplied; signed effective rates are billing/original.
    - Zero-value domestic discounts are kept. An installment with unknown plan identity stays a valid charge with `AMBIGUOUS_INSTALLMENT`.
15. **Packaging.** Alembic resources live in the package (`bank_transactions.persistence.migrations`) so installed wheels can migrate; the root `alembic.ini` points to the same directory. `SQLAlchemy>=2.0.16` is required for `postgresql_nulls_not_distinct`.

Quality gates: components are developed test-first; mappings, migrations and repositories use PostgreSQL integration tests (see the README for commands).

## 11. Notes

- Money is always `Decimal`; `NUMERIC(14,2)` for amounts, `NUMERIC(18,6)` for rates.
- Two-digit years (`09.02.26`) are resolved against the statement period when available, else a sliding window.
- Transaction date and charge date are both first-class — the enabler for summarising actual expenses over a custom period across cards with different billing cycles.
- Hebrew text is stored as-is (UTF-8); normalization only folds whitespace and quote variants for matching.
- The categorization module will later add its own `resolved_category_id`; `bank_category_raw` is never modified.
- Extension points: CSV/`.xls` behind the same `WorkbookView`, more issuers via `ParserRegistry`, a UI `IdentityResolver`. The REST API is specified in [api-design-phase-1.md](api-design-phase-1.md).
