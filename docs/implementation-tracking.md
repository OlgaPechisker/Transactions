# Implementation coordination and evidence

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
| APP-FOUNDATION / 1 | `65b80a3` | Owner RED: four collection errors for missing modules; owner, LEAD and REVIEW GREEN: 145 passed; ruff passed; strict mypy passed on 27 source files; isolated wheel build passed | Opus BLOCK: F1/F2; nonblocking N1/N2 accepted by LEAD; correction assigned to original owner |
| APP-FOUNDATION / 2 | `9967f8d` | Fix attempt 1: owner RED missing rate helper then six command regression failures; owner, LEAD and REVIEW GREEN 183 passed, ruff passed, strict mypy 27 source files passed | Independent Opus PASS; PARSE/STORE barrier released |
| PARSE / 1 | `f58ac75` | Owner GREEN 304 parser tests / 487 with foundation; LEAD GREEN 669 full delivered-component tests with real PostgreSQL, ruff passed, strict mypy 58 source files passed | Opus BLOCK P1/P2; LEAD validated both; real examples independently reconcile |
| STORE / 1 | `71ee0cf` | Owner RED missing CLI resolver then database modules; owner GREEN 261 storage/contract tests; LEAD GREEN 365 foundation/storage/contract tests on real PostgreSQL; scoped ruff and strict mypy nine source files passed | Delivery frozen for independent review; source migration packaging gap and added NULL-bank unique index explicitly reported |
| APP-SHARED / 1 | `02cced5` | Owner, LEAD and REVIEW: 83 contract tests pass, one wheel-resource test FAILS; full strict mypy FAILS on pending STORE optional-return adapter | Opus contract PASS, integration pending; unsuccessful standalone verification explicitly preserved, no overall success claimed |
| PARSE / 2 | `db7d58f` | Fix attempt 1 for P1-P4: owner true RED 14 failures / 305 passes before correction; owner, LEAD and REVIEW GREEN 423 parsing/domain tests, scoped lint/type checks | Opus PASS P1-P3; P4 narrowed catch correct, additional corrupt-input coverage accepted as follow-up under the same P4 counter |
| STORE / 2 | `f127a8a` | Owner RED seven failures / 34 passes; GREEN 271 storage/contracts tests, including installed-wheel PostgreSQL upgrade/downgrade/re-upgrade; LEAD combined suite 715 passed | Account/index correction attempt 1 and packaging correction attempt 2; independent Opus re-review pending |
| APP-SHARED / 2 | `b0d059b` | A1 stale-module regression genuinely RED before correction, 84 contract tests GREEN; fresh temporary source-copy wheel builds; A2 replaced by real PostgreSQL coverage | A1 correction attempt 1; independent Opus review pending; unused ini glob removed |
| PARSE / 3 | `78fa38b` | P5 true RED 19 failures / 321 passes; GREEN 444 parsing/domain tests; all three parser/registry window scenarios and unchanged example goldens pass | P5 correction attempt 1; independent Opus review pending |

LEAD independently ran the combined committed adapter/foundation tree at
`78fa38b`: **715 tests passed** against PostgreSQL 16 in 160.11 seconds,
`ruff check src tests` passed, all 104 files passed `ruff format --check`, and
strict `mypy src` passed on 60 source files. This is component integration
evidence, not completed application/end-to-end validation. The service and CLI
have not yet been delivered. The installed-wheel acceptance imports from a
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
| STORE-PARENT-01 | STORE / schema scope | Owner added a NULL-bank account-number unique index despite prior ruling; reviewer recommended accepting it, LEAD rejected that recommendation because unknown-bank identity is ambiguous and source DDL is explicit | STORE + APP | 1 | Extra index removed and tuple lookup/real PostgreSQL zero-one-many coverage verified in `f127a8a`; re-review pending |
| PACKAGE-01 / S1 | Cross-scope / HIGH | Root Alembic scripts omitted from wheel; installed init-db cannot locate migrations; LEAD reproduced missing resources in built wheel | STORE + APP | 2 | Attempt 1 unsuccessful in `02cced5`; attempt 2 passes real installed-wheel PostgreSQL round-trips in `f127a8a`; re-review pending |
| P1 | PARSE / MEDIUM | Reviewer and LEAD reproduced unusable installment note retaining plan total as original current-charge amount; existing test asserted wrong value | PARSE | 1 | Resolved; Opus independently verified `db7d58f` |
| P2 | PARSE / MEDIUM | Reviewer probed unknown/blank types silently becoming REGULAR; LEAD inspected both Max/Cal mappings and existing incorrect expectation | PARSE | 1 | Resolved; Opus independently verified `db7d58f` |
| P3 | PARSE / LOW | Foreign-currency installment lacks per-charge original; skip with explicit FX-original error, do not guess by dividing plan total | PARSE | 1 | Resolved; Opus independently verified parser-level regressions in `db7d58f` |
| P4 / P4a | PARSE / maintainability | Named expected-input catches preserve unexpected-error propagation, but reviewer and LEAD reproduced corrupt font-size TypeError and empty content-types OSError escaping read_stream | PARSE | 1 | Remaining coverage correction assigned as PARSE iteration 4, next attempt 2 under the original P4 counter, not a reset/new issue |
| P5 | PARSE / MEDIUM | LEAD found configurable year window cannot reach hardcoded date normalizer through registry/parser/row reader | PARSE + APP | 1 | Additive option fully propagated in `78fa38b`, component tests pass; Opus review and APP settings/bootstrap integration pending |
| A1 | APP-SHARED / LOW-MEDIUM | Reviewer found wheel fixture builds in checkout and can package stale build/lib files; LEAD read the fixture and accepted the finding | APP + STORE | 1 | Fresh source-copy build and genuine stale-module regression pass in `b0d059b`; STORE installed-wheel acceptance also isolates builds; re-review pending |
| A2 | APP-SHARED / LOW | Fake account none/one/many test only returns its configured value | APP + STORE | 1 | Fake removed in `b0d059b`; actual PostgreSQL zero/one/many, ordering and known-bank isolation pass |
| A3 | APP-SHARED / INFO | Migration ini glob has no canonical package file; root developer ini remains intentional | APP | 1 | Unused glob removed in `b0d059b`; single canonical migration implementation preserved |

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

The parser's full-suite attempt without BT_TEST_DSN errored on 124 PostgreSQL
test setups as designed; it is not a storage regression. LEAD reran the whole
delivered suite with the runtime DSN: 669 passed. Parser golden tests use all
four unchanged examples and reconcile their stated counts and totals.
The initial adapter reviews are complete. PARSE P1-P3 are independently closed;
the small P4 expected-input follow-up is assigned to the original owner.
STORE's resource/lookup correction, APP's shared wheel-test correction and
PARSE's year-window propagation are committed and locally green, with a combined
Opus review in progress. APP's original owner has resumed service/config/
bootstrap/CLI implementation; actual PostgreSQL application integration awaits
the adapter review gate. Full end-to-end application validation has not run.
Two old test schemas reported by STORE are preserved because their active-run
attribution was uncertain; no unattributed schema was dropped.
