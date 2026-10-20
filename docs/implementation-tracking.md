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
4. **APP**: original APP owner implements the import flow against reviewed
   adapters. Bootstrap is the only composition root. Review and commit.
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

Pending APP-FOUNDATION delivery and review. No dependent implementation begins
until LEAD records the actual contract commit here and informs both owners.

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
- Yearly Max includes 1563 domestic typed rows and 606 foreign typed rows;
  row validity/pending classification must be verified against charge dates,
  not inferred solely from a nonempty amount or transaction type.
- Cal: banner declared ILS 645.13, charge date 2026-09-10; blank billed
  amounts are pending. Do not modify sources to force reconciliation.

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

### Findings ledger

| ID | Scope / severity | Evidence and ruling | Owner | Fix attempts | Status |
| --- | --- | --- | --- | --- | --- |
| CONTRACT-01 | Shared / correctness | Reference key could collapse installment charges; user approved including installment number | APP + STORE | 0 | Resolved by user decision; implementation/review pending |
| CONTRACT-02 | PARSE / ordering | Max card identities exist only in data column; user approved identity-column-only context scan | PARSE | 0 | Resolved by user decision; implementation/review pending |
| CONTRACT-03 | Shared / dependency | Domain issue objects cannot import services; define in domain, re-export | APP | 0 | Agreed with reviewer; implementation pending |
| CONTRACT-04 | Shared / data flow | Sequence requires parsed plans; add frozen plans tuple to result | APP | 0 | Agreed with reviewer; implementation pending |

No application tests have run yet. No functionality is claimed complete.
