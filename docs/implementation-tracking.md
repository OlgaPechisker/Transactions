# Implementation coordination and evidence

## APP delivery checkpoint and user-requested resumption - 2026-10-20

The user requested finishing the currently running APP agent task, then stopping
and recording the resume point. That task finished and its delivery was
preserved. The user then requested **"continue implementation process"** before
this checkpoint documentation was committed. Work has RESUMED at independent
APP review and LEAD verification. LEAD's initial APP candidate verification now
passes, including installed-wheel CLI acceptance. Independent Opus review
returned **BLOCK** for two identity-selection defects. Their first production
correction is delivered; its initial verification failed on a new test's SQL
column typo. The failed state is preserved in APP2, and APP3 records the
test-only correction with 47 targeted tests passing. Independent re-review and
final acceptance remain pending. The overall task is incomplete.

- Base branch: `master`, authorized baseline `ced9245`; it remains unchanged.
- Implementation branch: `implementation/bank-transactions`.
- Frozen APP iteration 1 delivery: **`d66a31d`**, parent `930ff65`.
- This documentation-only commit records the delivery checkpoint and resumption.
  All delivered files are committed; the final working-tree/staging check is
  performed after committing this ledger. Nothing was pushed or merged.
- Original persistent agents are retained; reuse the IDs in the ownership table.
  No independent APP review was launched during the pause. The original Opus
  reviewer was reactivated only after the user requested continuation.

### Resumption execution order

1. Ask the original independent Opus reviewer to review `d66a31d` against
   `930ff65`, the source documents and the approved contracts. Do not redo the
   already-approved PARSE/STORE scopes unless an actual integration finding
   affects them.
2. Validate findings and route valid corrections to the original APP owner,
   preserving a separate commit and verification record for every attempt.
3. Run LEAD's independent application verification and the remaining
   **installed-wheel CLI acceptance outside the checkout**. The existing
   installed-wheel migration round-trips are already covered; they do not
   establish installed CLI acceptance.
4. Finish any required re-review, integration corrections and final reporting.
   Preserve the existing per-issue counters and the three-unsuccessful-attempt
   stop rule.

**Independent APP iteration 1 review: BLOCK.** Opus reproduced APP-PC-01
(HIGH) through the actual import service: an explicit `CreateCard` with two
matching physical cards binds to the lowest-ID card and creates none.
`_Applier._create_card` must create the requested card, not return `existing[0]`.
Opus also reproduced APP-PC-02 (MEDIUM): `_bind` offers no candidates for
`CONFLICTING_HINTS`, preventing the explicit existing-card selection required
by the plan and diagram. LEAD validated both recommendations against the actual
code, plan section 6 and diagram selection clauses. Both are accepted and
assigned to the original APP owner in iteration 2, first correction attempt.
Genuinely failing caller-visible regressions are required before production
changes; original automatic uncontested reuse, noninteractive NEEDS_INPUT,
ownership guards and atomicity must remain intact.

Opus independently reproduced all 933 baseline tests, lint/format and strict
mypy (69 files), plus parser goldens. It did not independently execute the
installed-console acceptance and explicitly distinguishes LEAD's result from
its own evidence. The hash-based audit-label observation is informational:
that privacy deviation is already recorded in the approved clarifications
below. No review recommendation was rejected.

### APP owner's reported verification, not LEAD re-verification

The owner reports **933 tests passed in 112.56 seconds**, including 156
application tests and 39 real PostgreSQL integration tests; ruff passes,
129 files pass formatting, and strict mypy passes on 71 source files.
LEAD did not rerun those application checks during the pause. LEAD only
performed the required pre-commit scope/safety inspection, staged whitespace
check, input-preservation check, commits and session-helper cleanup. After the
user resumed, LEAD launched an independent full-suite/lint/format/type run;
the measured result appears below.

### LEAD verification after resumption

For the frozen APP candidate `d66a31d` (HEAD `334832a` adds only this ledger),
LEAD independently measured **933 tests passed in 147.69 seconds** against
PostgreSQL 16, `ruff check src tests` passed, all 129 files passed formatting,
and strict `mypy src` passed on **69 source files**. The owner's reported 71-file
count is not substituted for the actual LEAD result.

A fresh source copy produced a wheel installed into session artifacts outside
the checkout. LEAD verified the imported CLI module's installed origin and ran
the actual Windows `bank-transactions.exe` entry point from outside the
checkout: **18 console invocations passed**, covering help, detection of all
four originals without DSN/key, database initialization without a cipher key,
noninteractive NEEDS_INPUT with zero identity/transaction writes, interactive
Isracard dry-run and normal import, all four examples, repeats and both Max
overlap orders. SQL assertions verified the example counts/totals above,
607 Max FX rows, 374 unknown-currency reasons, 13 plans/44 linked rows,
refund/location counts, 2181 combined transactions, unchanged prior-row
provenance, no remaining RUNNING audit rows and hash-only audit source labels.
Both uniquely owned acceptance schemas were cleaned. Evidence is retained in
the session artifact `installed-cli-993c4df4d8/results.json` and its wheel.

These checks establish the initial candidate's measured behavior, not approval
of unresolved identity edge cases. Rebuild and rerun relevant acceptance after
any review corrections. The acceptance harness initially assumed `Scripts`
instead of pip's Windows `--target` `bin` directory; correcting that harness
assumption required no application change. Its eager wheel-path evaluation was
also corrected before the first execution.

### APP iteration 2: first correction and unsuccessful test verification

The original APP owner added six regressions and changed the previous
empty-candidate assertion to the required offered-card behavior. Genuine RED
against unchanged production was **6 failed / 41 passed**, including the two
real-PostgreSQL identity regressions. The production correction removes the
first-card reuse branch and offers stored candidates for conflicting hints;
automatic uncontested reuse remains unchanged.

The first post-correction run was **46 passed / 1 failed**: the new duplicate-card
PostgreSQL test queried `cards.card_id`, but the prescribed primary key is `id`.
The owner corrected two test-only SQL strings before freezing and reported a
subsequent full **939 passed**, lint/format and mypy pass. No production code
changed after the initial 46/1 verification.

That test-only correction should have been checkpointed separately before
continuing. To preserve both actual verification states, LEAD obtained the exact
original SQL strings from the owner's retained failure output and replayed
them: independently **46 passed / 1 failed in 6.80 seconds**, with the same
`UndefinedColumn` error. APP iteration 2 preserves that unsuccessful
verification state; APP iteration 3 will separately preserve the already-authored
test-only SQL correction. This replay is explicitly not original test-first
RED, and does not claim a byte-for-byte snapshot of the owner's pre-formatting
workspace. No additional production correction or counter reset occurred.
Independent final verification and Opus re-review are still required.

### APP iteration 3: SQL-only regression correction

APP iteration 2's unsuccessful state is preserved at **`6de265e`**. This
iteration applies the original owner's already-authored two SQL corrections:
`max(card_id)` / `WHERE card_id` on the `cards` table become `max(id)` /
`WHERE id`. No production code changes. LEAD independently reran both affected
identity test modules: **47 passed in 5.84 seconds**; full ruff passed, all
129 files passed formatting, and strict mypy passed on 69 source files.
The owner previously reported 939 full-suite tests passing for this same
corrected source/test state. LEAD's final full-suite run, expanded installed
console acceptance and original Opus re-review follow the separate commit.

| Original example | Owner-reported PostgreSQL result |
| --- | --- |
| Isracard | 4 rows; ILS 1337.60 and USD 26.39 |
| Max monthly | 158 rows, 2 cards, 16 FX rows, 7 plans |
| Max yearly | 2169 rows, 607 FX rows, 374 unknown-currency reasons, 14 refunds, 13 plans / 44 linked rows |
| Cal | 8 imported, 13 pending skips, ILS 645.13; plan 1680 / charge 336 / installment 4 of 5 |

The owner also reports passing both Max overlap orders (monthly first adds 2011
new yearly rows and counts 158 duplicates; yearly first makes the monthly file
all duplicates), repeat imports, identical-row multiplicity, dry runs,
NEEDS_INPUT/ambiguous identities, rollback, stale-run sweep, failures, privacy and
in-checkout CLI scenarios. These results await independent APP review and
LEAD validation. Original workbooks and source plan/diagram remain unchanged.

The new library surface is `StatementImportService(...).import_file(path,
dry_run=False)`, `Settings.from_env(...)`, the bootstrap builders, and the CLI
`import` / `detect` / `init-db` commands. CLI exit codes are 0 success,
1 failure, 2 usage/configuration, 3 needs input, 4 partial and 130 interrupted.
These are implemented choices awaiting APP review, not final acceptance.

### Retained test environment

The session-owned container `transactions-194a7f6b-postgres` was **stopped, not
removed** at the pause; its configuration and data were retained. It has now been
restarted; the initial `pg_isready` probe confirmed only internal container
readiness, not the old published endpoint. Restart reassigned the dynamic host
port from 57085 to 64956. LEAD's first resumed full-suite run encountered
connection errors and the installed-CLI harness blocked against the obsolete
port; both owned processes were stopped. These are environment failures, not
application correction attempts. The corrected full-suite run first passed an
external PostgreSQL `SELECT 1` against the current published port. Its 933 tests
and installed-CLI acceptance subsequently passed as recorded above. Docker
Desktop and unrelated containers were not stopped. If needed after a later pause:

```powershell
docker start transactions-194a7f6b-postgres
```

Use the existing `.venv\Scripts\python.exe` and `127.0.0.1`, deriving the current
host port from `docker inspect` (`NetworkSettings.Ports.'5432/tcp'[0].HostPort`)
after every start. Obtain the test DSN/password from runtime container
configuration without printing or committing secrets, and verify an external
connection before a suite. Do not use `localhost` (a prior IPv6 connection hung).
Unattributed old test schemas remain preserved. No session-owned bounded helper
process is left running by the APP owner.

## Source of truth and authorized decisions

The requirements are `.github/plan.md` and `.github/sequence-diagram.md`.
There was no application code, package manifest, test suite, or initial commit.
The user authorized inspecting and committing the 17 existing input files as
an initial baseline. No pre-existing input was modified.

- Base: `master`, baseline `ced9245`.
- Implementation branch: `implementation/bank-transactions`.
- No pushes, merges into `master`, squashes, amendments, or history rewriting.
- The team leader alone stages and commits. Agents share the implementation
  working tree, with disjoint ownership and explicit dependency barriers.
  Integration is serialized commits on this branch, not concurrent Git writes.

The user resolved two blocking source-document conflicts:

1. Reference-based installment identity includes the installment number, so
   different charges sharing a voucher are preserved. Non-installment identity
   remains card + purchase date + issuer reference. The database index and
   in-memory/reference lookup keys must agree, including NULL handling.
2. Max `read_context` may read only the identity column of transaction tables
   to discover cards. It must not parse dates, amounts, merchants, installments,
   or foreign details before the identity gate.

Other contract clarifications, derived from the stated invariants:

- Row issues live in the domain and are re-exported from `services.report`;
  the domain never imports services.
- `ParserSelector.select` returns a parser or raises `UnknownFormatError`.
- `StatementParseResult` additionally carries an immutable tuple of parsed
  installment plans, as required by sequence section 4.
- `WorkbookView` and `SheetView` are library-free immutable views; openpyxl
  adaptation must not leak into the service's runtime import graph.
- Repository and UoW ABCs live in `persistence.interfaces`, whose package
  initializer must not import SQLAlchemy. Identity ABCs are likewise pure.
- One atomic identity gate covers every card in a file. Non-interactive
  unresolved identity rolls back all identity changes and parses no rows.
- A unique existing card with no conflicting owner/account evidence can be
  reused even if a file omits owner/account hints. Ambiguity is never resolved
  by choosing the first result.
- Max installment original amounts represent the current charge, with plan
  totals retained separately; do not use a plan total as a monthly expense.
- An installment with insufficient/unknown plan identity remains a valid
  charge with `AMBIGUOUS_INSTALLMENT`; do not invent a required plan currency.
- Zero-value domestic discount charges are retained. Pending rows are
  informational skips, not partial failures. Invalid FX originals are errors.
- Effective rates use Decimal arithmetic and only persistence rounds to six
  decimals. Refund signs follow the source billed amounts.
- Totals mismatches are warnings, never transaction review reasons or an
  automatic PARTIAL status. Error-level row issues cause PARTIAL.
- A dry run writes audit data but commits no identity, plan, or transaction
  changes; its report describes the proposed import. Unresolved identities
  still yield NEEDS_INPUT. Identity and domain changes share one live UoW in
  a dry run: flush allocates real IDs for comparison/insertion, then rollback
  discards all changes. Normal imports may use the same identity/domain UoW,
  committing only after parsing/insertion succeeds. Audit uses separate,
  committed UoWs. `detect` does not require a database or cipher key.
- Audit source names/paths must not leak identifiers already present in
  filenames: persist hash-based safe source labels rather than raw paths.
  Error summaries, reports, and issue locations/messages must also be safe.
- Since no audit heartbeat exists in the prescribed schema, the stale RUNNING
  sweep uses `started_at` and the stated one-hour threshold. No silent failures.
- Identifier protection explicitly takes `IdentifierType` on encrypt/decrypt;
  the AES-SIV key is decoded from base64 environment configuration into bytes.
  Ciphertext is bound to `card-last4` or `account-number` associated data.
- Repeated reference keys within a file are counted as duplicates after the
  first new key; they must not trigger a unique-constraint failure. This does
  not collapse distinct installment numbers. Reference-less repeated rows
  retain the count-based semantics in the source plan.
- Reference uniqueness must treat NULL installment numbers as equal, using
  PostgreSQL 15+ NULLS NOT DISTINCT or equivalent partial unique indexes.
  Audit issuer_code is nullable until parser selection/terminal update.
- Keep the prescribed unconditional installment-plan natural unique key.
  Upsert must handle both issuer-reference and natural conflicts, select the
  uniquely matching existing plan, and explicitly reject inconsistent matches;
  do not silently weaken the schema by making natural uniqueness partial.
- Cards must belong to their account's owner; enforce that invariant in
  service validation and database constraints. Account lookups are NULL-safe.
  Matching-text folding is shared pure domain logic, not duplicated by layers.
- Canonical last-4 preserves leading zeros and must contain four digits.
  Canonical account identifiers remove whitespace/hyphens for equality.
  Cal's bank maps to LEUMI. Account/card hints stay memory-only.
- Foreign classification includes Max foreign-sheet rows even when currencies
  are equal; differing known currencies and explicit foreign evidence also
  qualify. Do not infer a commission from a printed rate without a fee row.
  Preserve refund original amounts exactly as supplied: signed effective
  rates remain billing/original, not an absolute-value substitution.
- Future charge dates alone do not imply pending: explicit Max billed rows
  appear in declared totals. Missing billed amounts/dates are pending.
  Monthly/deferred-monthly types map to RECURRING; immediate to
  IMMEDIATE_CHARGE; REGULAR is not changed merely because notes look recurring.
  Unrecognized/blank channels use UNKNOWN. Fee date window defaults to three
  days and is configurable, along with issuer fee keywords.
- Original notes are preserved; nonempty discount-club metadata may be
  appended with labels. Do not remove source personal names speculatively;
  last-4/account identifiers and diagnostic output have the explicit
  confidentiality boundary. Keep raw merchant text while extracting only
  confidently recognized location suffixes.
- Precision boundary (independent review ruling): parsed money/FX retains full
  Decimal precision. Immutable persistence commands round money to two places
  and rates to six using HALF_UP, exactly once; repositories store command
  values as supplied. Comparison and plan keys use the same amount rounding.
  Do not reject source values solely for having more than two decimal places.
- STORE's pure `TransactionMapper` exposes `reference_key(tx, card_id)`,
  `comparison_key(tx, card_id)`, `plan_command(plan, card_id)` and
  `to_aggregate(tx, card_id, run_id, plan_id=None)`; no SQLAlchemy imports.
  Invalid cipher keys/tokens/type binding raise neutral ValueError.
- Account number lookup now returns all candidates as an ordered tuple.
  NULL-safe comparison is not permission to invent uniqueness for an unknown
  bank: preserve the source `(bank_code, account_number_enc)` unique rule.
  The service determines an account only from exactly one consistent candidate;
  zero/multiple/conflicting candidates go through explicit identity resolution.
- Necessary packaging deviation: canonical Alembic resources live under
  `bank_transactions.persistence.migrations` so installed wheels can run
  migrations. The root `alembic.ini` points to those same developer resources.
  There is one migration implementation, not duplicated source/package copies.
  SQLAlchemy minimum is 2.0.16, where `postgresql_nulls_not_distinct` was added
  (upstream tagged changelog, section 2.0.16, ticket 8240).
- The configurable two-digit-year window must reach the actual normalizers,
  not remain an unused setting. The additive parser/registry keyword is
  `two_digit_years_back=80`, validated from 0 through 99, defining the
  hundred-year interval `[anchor-back, anchor+99-back]`. Statement-derived
  anchors retain priority over today's date. APP owns settings/bootstrap;
  PARSE owns propagation through the registry, parsers and row readers.
- Identity label validation reuses the pure
  `domain.text.contains_identifier(text, identifiers)` helper. It extracts
  the existing CLI guard algorithm, including separator handling, so service
  validation does not import a concrete resolver or duplicate its logic.
  Ordinary merchant text and source notes are not redacted. APP owns the shared
  helper; STORE owns the CLI adapter's green-preserving adoption.

## Agent capabilities and ownership

The task API accepted the requested model overrides. Runtime agent metadata
confirmed the model names. `high` was explicitly supplied and accepted for
Sonnet; no private introspection of model internals is claimed.
Initial sync probes could not receive follow-ups and were not used for coding.
Background agents successfully received follow-ups and reproduced retained
tokens without rereading, verifying multi-turn context persistence.

| Scope | Persistent owner | Model / effort | Owned files and deliverables |
| --- | --- | --- | --- |
| APP | `b71965f7-02e3-47bb-8c00-7813361285c3` | Sonnet 5.5 / High | Scaffold, domain and all pure public contracts first; later services, config, bootstrap, CLI, README and application/E2E tests |
| PARSE | `f64d5656-fcbc-4a0f-9746-1daf9f94be05` | Sonnet 5.5 / High | `parsing/` concrete code, normalizers, shared fee/FX/installment algorithms and all three issuer parsers; `tests/parsing/` |
| STORE | `a8472c8a-a1c1-4deb-b23b-a530c46ec660` | Sonnet 5.5 / High | Concrete identity/crypto, SQLAlchemy models, repositories/UoW, mappers, migrations; `tests/storage/` |
| REVIEW | `937f7796-cf51-4599-8c1a-a4b7588ccf18` | Opus 5.5 | Independent read-only review of every implementation and correction; never implements |
| LEAD | Main session | Team coordinator | This ledger, contracts/decisions, independent finding validation, serialized Git, environment and final cross-scope verification |

Shared files belong to APP: `pyproject.toml`, package initializers, domain,
ports, `parsing/interfaces.py`, `identity/interfaces.py`, `identity/models.py`,
`persistence/interfaces.py`, and immutable persistence command contracts.
Other owners request contract changes through LEAD; they do not edit them.
LEAD alone edits this ledger. Inputs under `examples/` and `.github/` are
read-only. Generated caches, environments, credentials, and DB files are never
committed. Each scope must report its exact changed-file list.

## Milestones and integration contracts

1. **PLAN**: commit this coordination baseline before implementation.
2. **APP-FOUNDATION**: test-first scaffold and frozen contracts, independent
   review, corrections if needed, then commit before dependent implementation.
   The owner must record exact fields/signatures below, not leave duck-typed
   gaps for other scopes.
3. **PARSE** and **STORE**: independent implementations may run in parallel
   only after the reviewed foundation is committed. They may read, not edit,
   each other's files. Each iteration is frozen, reviewed, verified, and
   centrally committed separately. No uncommitted iteration may be overwritten.
4. **APP**: original APP owner implements the import flow against stable
   contracts. Scheduling refinement after the initial adapter reviews:
   service/unit work may proceed alongside bounded adapter corrections because
   those corrections do not change the frozen import/identity interfaces.
   Bootstrap integration additionally awaits the additive year-window option.
   Final integration remains blocked until all adapter corrections pass
   independent review. Bootstrap is the only composition root. Review and commit.
5. **INTEGRATION**: test all components together and the actual example files.
   Route corrections to original owners, independently review, commit each
   correction separately, and rerun the relevant checks.

Public contract invariants to be concretized in APP-FOUNDATION:

- Hints carry plaintext identifiers only in memory; sensitive fields have safe
  repr behavior. `CardHintKey` identifies a source card, not a database record.
  Parsed data, review-reason sets, results, and resolved mappings are immutable.
- Every parser implements `sniff(wb)`, `read_context(wb)`,
  `read_transactions(wb, context)`. Structural errors raise `ParseError`;
  malformed rows return safe, located issues and valid rows continue.
- Parsed transactions carry charge/purchase dates, Decimal amounts, nullable
  currencies, raw and normalized merchant, verbatim category, type/channel,
  source location, optional issuer reference/installment/FX and reason set.
- Parsed installment references distinguish plan total/count/currency and
  stable purchase date from the per-charge original amount.
- Resolved identity maps each source card key to a typed database card ID,
  without mutating parser outputs.
- The identity resolver receives hints and explicit candidate/owner/account
  choices and returns immutable selections/new-identity requests. The service
  validates ownership/conflicts and persists all selections atomically.
- UoW and repository contracts expose audit start/finish/sweep, identity
  lookup/create, plan upsert, known reference lookup, NULL-safe comparison
  group counts, and aggregate insertion. The UoW factory makes independent
  transactions for durable audit and an atomic identity/domain transaction;
  a dry-run identity/domain transaction is always rolled back.
- `NewTransactionAggregate` carries one transaction, a reason set and at most
  one FX detail. Mapping is pure; repositories never mutate parsed objects.
- Reference keys obey the user-approved installment extension. Reference-less
  keys use the exact six fields in plan section 7. Count all incoming rows
  against prior-run counts before inserting any; never update existing rows.
- Service audit RUNNING is committed before any domain write. Every caught
  failure reaches FAILED (or NEEDS_INPUT for unresolved noninteractive identity).
  Domain failures roll back before a separate committed terminal audit write.
  Unexpected exceptions are not converted into success-shaped reports.

### Frozen field/signature record

APP-FOUNDATION iterations 1 and 2 are preserved; independent Opus re-review
passed iteration 2 at `9967f8d`. PARSE and STORE implementation is authorized
in parallel against that reviewed contract commit.
The following modules are the authoritative typed contract definitions:

- `domain.ids`: NewType owner/account/card/import-run/installment-plan/
  transaction IDs and IdentifierToken.
- `domain.money`: `Currency(code)`, `Money(amount: Decimal, currency:
  Currency | None)`, `round_amount`, `round_rate`, and shared signed
  `effective_fx_rate(billing, original)` at deterministic 28-digit precision.
- `domain.workbook`: `SheetView(name, rows)` with one-based row/cell access;
  `WorkbookView(sheets)` with `find_sheet`/`require_sheet`. No library types.
- `domain.models`: `CardHintKey(issuer_code, last4)`, `CardHint(key, label)`,
  `OwnerHint(display_name)`, `AccountHint(bank_code, account_number,
  display_name)` and `CardIdentityHint(card, owner, account)`.
- `StatementContext(issuer_code, cards, owner, account, period, charge_date,
  declared_totals)` has `identity_hints()`. Totals expose a read-only
  `by_currency` mapping and `StatementTotals.of(*Money)`.
- `ParsedTransaction(card_key, location, transaction_date, charge_date,
  billing, merchant_name_raw, merchant_name_norm, transaction_type, original,
  merchant_city, merchant_country, bank_category_raw, channel, notes,
  issuer_reference, installment, installment_plan, fx, review_reasons)`;
  optional fields default to None and reasons to frozenset.
- `ParsedInstallmentRef(installment_number, total_installments, plan_total,
  issuer_reference)` and `ParsedInstallmentPlan(card_key, merchant_name_norm,
  purchase_date, total, total_installments, issuer_reference)`.
- `StatementParseResult(transactions, issues, rows_seen, calculated_totals,
  installment_plans=())`; frozen collections. Locations contain sheet
  position and row, never raw sheet titles. FX stores commission and
  stated/effective Decimal rates.
- `WorkbookReader.open(Path) -> WorkbookView`;
  `ParserSelector.select(WorkbookView) -> StatementParser`;
  `IdentifierProtector.encrypt(IdentifierType, str) -> IdentifierToken` and
  `decrypt(IdentifierType, IdentifierToken) -> str`.
- `StatementParser.sniff`, `read_context`, `read_transactions` are exactly the
  source contract, using the pure views and models above.
- `IdentityResolver.complete(IdentityQuestion) -> IdentitySelection`;
  `identity.models` defines explicit owner/account/card choices and
  ID-or-create selections, candidates, determined owner/account, and
  `ResolvedCards.card_id_for(CardHintKey) -> CardId`. A partial CreateCard
  selection keeps already-determined owner/account. The service validates
  ownership/conflicts and collects unresolved keys across the whole gate.
- `persistence.commands` defines immutable NewImportRun/ImportRunOutcome,
  NewOwner/NewAccount/NewCard, NewInstallmentPlan, NewTransaction,
  NewForeignDetail, and NewTransactionAggregate. `persistence.records`
  defines library-free owner/account/card records and CardLookup.
- `persistence.interfaces` has audit start/finish/sweep, per-entity
  list/get/find/create, installment `upsert_plans -> Mapping[command, id]`,
  transaction `find_known_references -> frozenset[key]`,
  `count_matching_groups -> Mapping[key, count]`, and
  `insert_many -> tuple[TransactionId, ...]`. Creates flush real IDs.
  Account `find_by_number` returns `tuple[AccountRecord, ...]` (ordered,
  zero/one/many), not a first-match optional result.
- `UnitOfWorkFactory.create() -> UnitOfWork`; UoW exposes six repositories,
  commit/rollback and context exit rollback. Each factory call is a new session.
- `domain.keys` defines exact reference/comparison dataclass keys and
  OwnerMatchKey; shared domain text folding and account canonicalization.
- `ImportReport` includes audit id/status/issuer, all four row counts,
  rows_needing_review, issues, warnings, unresolved keys, and dry_run.
  `services.report` re-exports the domain issue types, not copies.

## Acceptance and verification boundaries

All code is developed test-first: record the failing test command/result and
the green result. Use pytest, ruff, mypy, SQLAlchemy 2, Alembic, openpyxl,
cryptography AES-SIV and PostgreSQL 15+ as required by the plan. Python 3.12+
is required; the available `py` launcher runs Python 3.14.2. Bare `python`
is a Windows Store stub; use `py` or the project environment's executable.
openpyxl is installed. Docker engine 29.9.1 was started and is responsive.
Initial prerequisite probes found pytest, ruff, mypy and SQLAlchemy missing;
installing the declared project dependencies after scaffold creation is needed.
Database validation uses an isolated local test container/database, not any
existing customer database. Secrets are runtime-only, never in tracked files.
The session container is `transactions-194a7f6b-postgres` (PostgreSQL 16),
bound only to localhost on dynamically assigned port 57085. `pg_isready`
confirmed readiness. Credentials were generated at runtime and remain in
container configuration; obtain them locally without printing or committing
them. The database name is `bank_transactions_test`; test owners must use
separate schemas or serialize destructive schema setup.

| Boundary | Required observable scenarios |
| --- | --- |
| APP-FOUNDATION | Decimal money, enum values, immutable parse/results/mappings, safe representations, pure import graph, contract signatures |
| PARSE | Actual four unmodified workbooks; exact row/totals/date/card/category/reference behavior; pending/malformed/unknown currency; refunds/discounts; dual-sheet foreign data; installments; nearest compatible fee folding; unmatched fees; zero-original FX errors; format and structural failures |
| STORE | Real PostgreSQL migration upgrade/downgrade and schema/index/enum checks; encrypted identifier round trips/domain binding; ambiguous/noninteractive identities; required ownership; aggregate persistence; NULL-safe counts and reference identity; installment upsert; atomic rollback and durable audit |
| APP | Audit and identity order; all terminal statuses and unexpected failures; stale sweep; totals warning semantics; no parser branching by issuer; dry-run rollback; CLI detect/init-db/import; settings validation and safe errors |
| E2E | PostgreSQL imports of all actual examples, repeat and overlapping imports in both orders, identical repeated purchases, multiple cards/identities, installment linkage, FX totals/rates, reason persistence, audit provenance, CLI prompts/noninteractive runs, no plaintext identifiers in persisted fields or reports |

Independent raw-workbook observations (not application test results):

- Isracard: four transaction rows including a zero-charge discount; charged
  totals ILS 1337.60 and USD 26.39; statement charge date 2026-02-10.
- Monthly Max: 142 domestic charged rows and 16 foreign charged rows, with
  totals ILS 20349.01 and 490.66 respectively. Footer rows are not pending.
- Yearly Max includes 1563 domestic rows (ILS 158460.88) and 606 foreign rows
  (ILS 58102.80). The monthly file is a complete multiset subset; identical
  repeated rows must survive. Foreign original currency is blank on 374 rows.
- Cal: 21 data rows, eight charged and 13 pending; banner declared ILS 645.13,
  charge date 2026-09-10. One installment carries a plan total of 1680 and
  current charge 336. Do not modify sources to force reconciliation.

## Iteration and finding policy

Each implementation delivery and each fix-and-verification attempt gets its
own commit, even if its verification fails. Commit messages:
`TASK iteration N: concise description`; unsuccessful corrections explicitly
say `UNSUCCESSFUL`. Every commit includes the required Copilot co-author.
LEAD inspects the actual diff, scans for secrets/artifacts, stages only that
iteration's explicit files, and commits. Agents never stage or commit.

For each issue, count correction followed by verification as one attempt.
Record findings, evidence, owner, severity, acceptance/rejection reasoning,
attempt count, exact verification and commit. A different owner/solution does
not reset the counter. Stop all implementation at three unsuccessful attempts
on any issue; preserve commits and report branch/state/uncommitted files.
Review is independent and applies to fixes and integration work too.

### Commit and verification ledger

| Task / iteration | Commit | Verification | Review / state |
| --- | --- | --- | --- |
| BASELINE / 0 | `ced9245` | 17 authorized files; staged diff inspected; `git diff --cached --check` passed; credential-pattern scan of text and workbook cells found no credentials | Initial inputs preserved unchanged; clean tree before branch creation |
| PLAN / 0 | `13de890` | Documentation only | Ownership/contracts recorded before implementation |
| PLAN / 1 | `c3e7904` | Documentation diff inspected; staged whitespace check passed | Associated-data, dry-run, reference/NULL and owner-account invariants explicitly settled with independent reviewer |
| PLAN / 2 | `087186e` | Documentation-only correction hashes, 715-test evidence and finding counters | Preserved coordination checkpoint |
| PLAN / 3 | `098bfc8` | Documentation-only adapter approvals, shared validation ownership and test-first evidence limits | APP integration gate recorded open |
| PLAN / 4 | `930ff65` | Documentation-only final STORE3 approval | All adapter/shared-helper corrections independently approved before APP delivery |
| PLAN / 5 | `334832a` | Documentation-only APP delivery checkpoint, resumption and test provenance | Original APP review and independent validation resumed |
| APP-FOUNDATION / 1 | `65b80a3` | Owner RED: four collection errors for missing modules; owner, LEAD and REVIEW GREEN: 145 passed; ruff passed; strict mypy passed on 27 source files; isolated wheel build passed | Opus BLOCK: F1/F2; nonblocking N1/N2 accepted by LEAD; correction assigned to original owner |
| APP-FOUNDATION / 2 | `9967f8d` | Fix attempt 1: owner RED missing rate helper then six command regression failures; owner, LEAD and REVIEW GREEN 183 passed, ruff passed, strict mypy 27 source files passed | Independent Opus PASS; PARSE/STORE barrier released |
| PARSE / 1 | `f58ac75` | Owner GREEN 304 parser tests / 487 with foundation; LEAD GREEN 669 full delivered-component tests with real PostgreSQL, ruff passed, strict mypy 58 source files passed | Opus BLOCK P1/P2; LEAD validated both; real examples independently reconcile |
| STORE / 1 | `71ee0cf` | Owner RED missing CLI resolver then database modules; owner GREEN 261 storage/contract tests; LEAD GREEN 365 foundation/storage/contract tests on real PostgreSQL; scoped ruff and strict mypy nine source files passed | Delivery frozen for independent review; source migration packaging gap and added NULL-bank unique index explicitly reported |
| APP-SHARED / 1 | `02cced5` | Owner, LEAD and REVIEW: 83 contract tests pass, one wheel-resource test FAILS; full strict mypy FAILS on pending STORE optional-return adapter | Opus contract PASS, integration pending; unsuccessful standalone verification explicitly preserved, no overall success claimed |
| PARSE / 2 | `db7d58f` | Fix attempt 1 for P1-P4: owner true RED 14 failures / 305 passes before correction; owner, LEAD and REVIEW GREEN 423 parsing/domain tests, scoped lint/type checks | Opus PASS P1-P3; P4 narrowed catch correct, additional corrupt-input coverage accepted as follow-up under the same P4 counter |
| STORE / 2 | `f127a8a` | Owner RED seven failures / 34 passes; GREEN 271 storage/contracts tests, including installed-wheel PostgreSQL upgrade/downgrade/re-upgrade; LEAD and REVIEW combined suite 715 passed | Opus PASS; account/index correction attempt 1 and packaging correction attempt 2 resolved |
| APP-SHARED / 2 | `b0d059b` | A1 stale-module regression genuinely RED before correction, 84 contract tests GREEN; fresh temporary source-copy wheel builds; A2 replaced by real PostgreSQL coverage | Opus PASS; A1 attempt 1 resolved, unused ini glob removed |
| PARSE / 3 | `78fa38b` | P5 true RED 19 failures / 321 passes; GREEN 444 parsing/domain tests; all three parser/registry window scenarios and unchanged example goldens pass | Opus PASS; P5 attempt 1 resolved, APP settings/bootstrap wiring remains part of APP scope |
| PARSE / 4 | `bef4e77` | P4 attempt 2: owner genuine RED three failures / 11 passes, GREEN 14 workbook tests and 448 parsing/domain tests; REVIEW independently reproduced both RED and GREEN on the commit snapshot; LEAD scoped lint/format/type checks pass | Opus PASS; original P4 closed without resetting the counter |
| APP-SHARED / 3 | `f63f9df` | Owner and LEAD 52 text/import-graph tests pass; REVIEW 115 domain tests and scoped lint/format/type/purity checks pass | Opus PASS; original test-first RED not captured, reconstructed RED explicitly excluded from evidence |
| STORE / 3 | `c6728de` | Green-preserving refactor: baseline 19 resolver tests pass; owner 106 focused/contracts tests pass; LEAD 74 focused checks pass; REVIEW 76 selected privacy/resolver tests and full strict mypy 60 source files pass | Opus PASS; CLI and service share pure label validation without behavior or public-contract changes |
| APP / 1 | `d66a31d` | LEAD and Opus independently reproduce 933 tests, lint/format 129 files and strict mypy 69 source files; LEAD fresh installed-wheel CLI passes 18 console invocations with SQL goldens outside checkout | Opus BLOCK for APP-PC-01/02; both recommendations independently validated by LEAD |
| APP / 2 | `6de265e` | Owner genuine RED 6 failed / 41 passed; first correction verification 46 passed / 1 failed due to test SQL, independently replayed by LEAD in 6.80 seconds | UNSUCCESSFUL verification state preserved; production fix unchanged, test-only APP3 correction follows |
| APP / 3 | This SQL-only correction commit | LEAD 47 affected tests pass in 5.84 seconds, full lint/format 129 files and strict mypy 69 source files pass; owner previously reported full 939 pass on the same corrected state | No production change; final full/installed-CLI acceptance and original Opus re-review pending |

LEAD independently ran the combined committed adapter/foundation tree at
`78fa38b`: **715 tests passed** against PostgreSQL 16 in 160.11 seconds,
`ruff check src tests` passed, all 104 files passed `ruff format --check`, and
strict `mypy src` passed on 60 source files. This is component integration
evidence, not completed application/end-to-end validation. At that earlier
checkpoint the service and CLI had not yet been delivered. The installed-wheel
migration acceptance imports from a
temporary installation outside the checkout, verifies the module origin, and
round-trips an isolated schema; it does not merely inspect ZIP members.

### Findings ledger

| ID | Scope / severity | Evidence and ruling | Owner | Fix attempts | Status |
| --- | --- | --- | --- | --- | --- |
| CONTRACT-01 | Shared / correctness | Reference key could collapse installment charges; user approved including installment number | APP + STORE | 0 | Contracts/storage implemented and reviewed; full service/E2E verification pending |
| CONTRACT-02 | PARSE / ordering | Max card identities exist only in data column; user approved identity-column-only context scan | PARSE | 0 | Implemented and independently reviewed; no transaction parsing before identity gate |
| CONTRACT-03 | Shared / dependency | Domain issue objects cannot import services; define in domain, re-export | APP | 0 | Resolved in independently approved foundation |
| CONTRACT-04 | Shared / data flow | Sequence requires parsed plans; add frozen plans tuple to result | APP | 0 | Resolved in independently approved foundation |
| CONTRACT-05 | APP / blocking | Independent reviewer identified dry-run ID lifetime conflict; one live identity/domain UoW flushes IDs then rolls back dry runs | APP | 0 | Accepted; contract clarified in `c3e7904` |
| CONTRACT-06 | Shared / blocking | Independent reviewer required explicit identifier associated-data type on encrypt/decrypt | APP + STORE | 0 | Accepted; contract clarified in `c3e7904` |
| DECISION-01 | PARSE / source fidelity | Rejected sign-normalizing refund original_amount: source plan requires billed/original, not an absolute rate | PARSE | 0 | Source signs preserved |
| DECISION-02 | STORE / schema fidelity | Rejected partial natural installment unique index: source DDL requires unconditional natural uniqueness | STORE | 0 | Resolve both specified uniqueness conflicts without schema weakening |
| F1 | APP-FOUNDATION / HIGH | Reviewer found CardHint label can expose last-4; LEAD reproduced accepting synthetic identifier-containing label and repr exposure | APP | 1 | Resolved; Opus independently verified guard/regressions in `9967f8d` |
| F2 | APP-FOUNDATION / MEDIUM | Reviewer found inconsistent precision boundary; LEAD reproduced excess precision retained in foreign-detail command | APP | 1 | Resolved; Opus verified command quantization and parsed precision in `9967f8d` |
| N1 | APP-FOUNDATION / test validity | Reviewer found domain isolation test imports only empty initializer; LEAD inspected actual test and agreed | APP | 1 | Resolved; Opus verified every concrete domain module is exercised |
| N2 | APP-FOUNDATION / correctness | Reviewer found stale FX rate accepted; LEAD reproduced billed 12 / original 3 with incorrect rate 3 | APP | 1 | Resolved; Opus verified signed consistency and shared helper regressions |
| STORE-PARENT-01 | STORE / schema scope | Owner added a NULL-bank account-number unique index despite prior ruling; reviewer recommended accepting it, LEAD rejected that recommendation because unknown-bank identity is ambiguous and source DDL is explicit | STORE + APP | 1 | Resolved; extra index removed and all-candidate lookup independently approved in `f127a8a` |
| PACKAGE-01 / S1 | Cross-scope / HIGH | Root Alembic scripts omitted from wheel; installed init-db cannot locate migrations; LEAD reproduced missing resources in built wheel | STORE + APP | 2 | Resolved; attempt 1 unsuccessful in `02cced5`, attempt 2 independently approved with actual installed-wheel PostgreSQL round-trips in `f127a8a` |
| P1 | PARSE / MEDIUM | Reviewer and LEAD reproduced unusable installment note retaining plan total as original current-charge amount; existing test asserted wrong value | PARSE | 1 | Resolved; Opus independently verified `db7d58f` |
| P2 | PARSE / MEDIUM | Reviewer probed unknown/blank types silently becoming REGULAR; LEAD inspected both Max/Cal mappings and existing incorrect expectation | PARSE | 1 | Resolved; Opus independently verified `db7d58f` |
| P3 | PARSE / LOW | Foreign-currency installment lacks per-charge original; skip with explicit FX-original error, do not guess by dividing plan total | PARSE | 1 | Resolved; Opus independently verified parser-level regressions in `db7d58f` |
| P4 / P4a | PARSE / maintainability | Named expected-input catches preserve unexpected-error propagation; corrupt font-size and empty content-types cases required two additional expected exception classes | PARSE | 2 | Resolved; Opus independently verified `bef4e77`, including three genuinely failing regressions before the correction |
| P5 | PARSE / MEDIUM | LEAD found configurable year window cannot reach hardcoded date normalizer through registry/parser/row reader | PARSE + APP | 1 | Parser propagation independently approved in `78fa38b`; APP settings/bootstrap integration pending |
| A1 | APP-SHARED / LOW-MEDIUM | Reviewer found wheel fixture builds in checkout and can package stale build/lib files; LEAD read the fixture and accepted the finding | APP + STORE | 1 | Resolved; fresh builds and stale-module regression independently approved in `b0d059b` |
| A2 | APP-SHARED / LOW | Fake account none/one/many test only returns its configured value | APP + STORE | 1 | Fake removed in `b0d059b`; actual PostgreSQL zero/one/many, ordering and known-bank isolation pass |
| A3 | APP-SHARED / INFO | Migration ini glob has no canonical package file; root developer ini remains intentional | APP | 1 | Unused glob removed in `b0d059b`; single canonical migration implementation preserved |
| APP-PC-01 | APP / HIGH | Opus actual-service probe binds explicit CreateCard to card 1 of 2 and creates none; LEAD validated `existing[0]` against plan section 6 / diagram's nonunique physical-card and explicit-selection rules | APP | 1 | Production correction delivered in APP2; test SQL follow-up and independent re-review pending |
| APP-PC-02 | APP / MEDIUM | Opus actual-service probe cannot select a conflicting existing card because `_bind` offers no candidates; LEAD validated required explicit selection against plan section 6 and diagram | APP | 1 | Production correction delivered in APP2; noninteractive behavior retained; independent re-review pending |
| APP-TEST-01 | APP / test validity | New PostgreSQL regression used `cards.card_id` instead of `id`; LEAD reproduced initial 46/1 failure from exact owner-reported SQL | APP | 1 | APP3 separately preserves owner-authored SQL-only correction; LEAD 47 affected tests pass, independent re-review pending |

Build environment note: `pip wheel --no-build-isolation` initially failed
because setuptools was not installed in the runtime environment. Standard
isolated `pip wheel . --no-deps` successfully installed the declared build
requirements and produced the wheel in session artifacts; no source change
or additional dependency declaration was needed.

PARSE test-first evidence limitation: the owner reported genuine missing-module
RED runs for several modules, but used module-removal sensitivity checks for
registry/row-reader/installment-fields and observed no separate RED for
default_registry. Those checks are not claimed as genuine test-first history.
Future corrections must add and run failing regressions against the existing
implementation before changing it.

APP-SHARED iteration 3 has a separate test-first provenance limitation: the owner
did not capture RED before writing the eight-line helper. Renaming it afterwards
produced an ImportError but is a reconstruction, not genuine TDD evidence. The
commit and this ledger explicitly preserve that limitation. Functional tests and
import isolation pass; all remaining new APP components were instructed to run
their actual failing tests before implementation, without manufactured RED.

APP iteration 1's owner reports genuine missing-module RED runs for totals,
deduplication, settings, the main import flow, CLI and bootstrap. The identity
gate and failure handling were nevertheless written before their tests; two
reverted mutation checks establish sensitivity, not original test-first history.
Import-graph and integration tests were added against implemented code. Initial
test failures caused by incorrect Money fixture types and a wrong expected
currency sum were corrected in tests, not production logic. These provenance
limits are disclosed rather than represented as full TDD compliance.

The parser's full-suite attempt without BT_TEST_DSN errored on 124 PostgreSQL
test setups as designed; it is not a storage regression. LEAD reran the whole
delivered suite with the runtime DSN: 669 passed. Parser golden tests use all
four unchanged examples and reconcile their stated counts and totals.
The adapter reviews and corrections are independently approved: PARSE P1-P5,
STORE packaging/account lookup and APP's stale-wheel correction are closed.
The APP adapter integration gate is OPEN. APP iteration 1 has now been delivered
and preserved at `d66a31d`; its owner reports passing application/PostgreSQL/
example/in-checkout CLI tests. The shared label helper and its green-preserving
CLI adoption are independently approved. The user requested a pause after APP
delivery, then resumed before its documentation checkpoint was committed.
LEAD full verification and installed-wheel CLI acceptance now pass for the
initial APP candidate. Independent APP review returned BLOCK for the two
validated identity findings. The original owner's production correction is
delivered, with its initially unsuccessful test-query verification preserved
separately from the SQL-only correction. Independent re-review and final
acceptance/reporting remain pending.
Two old test schemas reported by STORE are preserved because their active-run
attribution was uncertain; no unattributed schema was dropped.
