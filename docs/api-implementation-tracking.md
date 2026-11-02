# API v1 implementation – tracking notes

Source of truth: [api-design-phase-2.md](api-design-phase-2.md) (work breakdown §B.7, tests §B.8),
[api-design-phase-1.md](api-design-phase-1.md), [api-contract/](api-contract/), [sequence-diagram.md](sequence-diagram.md).

## Git workflow
- Base branch: `master` @ `dce2280`. Implementation branch: `feature/api-v1-implementation`.
- One commit per iteration: `U<n> iter <k>: <summary>`. Review-driven fixes get their own commits.
- The team leader stages and commits centrally; parallel units work in separate git worktrees/branches
  (`unit/U<n>`) and are merged with `--no-ff` (no squash) in dependency order.
- No push and no merge into `master` unless the user asks for it.

## Cycles (the team leader stops and reports after each implement → review → commit cycle)
| Cycle | Units | Parallelism | Gate |
|---|---|---|---|
| 1 | U0 contract freeze & walking skeleton | single agent | blocks everything |
| 2 | U1 schema/persistence core, U2 API foundation, U3 adapters | parallel worktrees | U1 gates PG tests, U2 gates endpoint tests |
| 3 | U4 auth/profile/cards, U5 imports, U6 transactions | parallel worktrees | U6 provides `visible_id`/`lock_visible` |
| 4 | U7 categorization, U8 trips, U9 analysis, U10 currency validation | parallel worktrees | – |
| 5 | U11 integration, OpenAPI conformance, example-file E2E | single agent | final |

Ownership of files per unit is exactly the "Owns" column of §B.7.4. Frozen contract files (§B.7.2 rule 2) change
only through a separate contract-change commit coordinated by the team leader.

## Decisions during implementation
- **CLI import user (user decision, cycle 2):** `bank-transactions import` gets a required `--user <email>` option
  (user created with `create-user`); unknown email → usage error, exit 2. `detect` and `init-db` need no user.
- **Scope transfer to U1 (cycle 2):** once `user_id`/`claim_id` are NOT NULL, the CLI pipeline must keep working.
  U1 therefore also owns `persistence/providers/users.py` (plain user CRUD/lock; U4 consumes it),
  `persistence/providers/claims.py` (`find`, `create_or_get`, `lock`; U5 consumes it), and the minimal
  `user_id`/claim threading in `cli.py` (`import --user`), `services/import_service.py`, `services/identity_gate.py`,
  the identity resolvers and the existing import tests. U5 later adds the API path, outcome order and placeholders on top.
- Worktrees: `..\Transactions-wt\U<n>` on branch `unit/U<n>`; commands run with the main `.venv` python and
  `PYTHONPATH=<worktree>\src;<worktree>`.

## Agents
| Role | Model / effort | Scope |
|---|---|---|
| Coding agent per unit (persistent, retains context for fixes) | Claude Sonnet 5.5, high | one unit each |
| Tech lead reviewer (independent, persistent) | Claude Opus 5.5 | reviews every unit; never implements |

## Environment
- Test PostgreSQL: disposable container `bt-api-test-db` (postgres:16) on `127.0.0.1:55432`;
  `BT_TEST_DSN=postgresql+psycopg://bt:bttest@127.0.0.1:55432/bt_test` (port 5432 is reserved on this host).
- Baseline on `master` before any change: ruff clean, mypy clean, pytest 930 passed / 9 failed. All 9 failures are in
  `tests/parsing/test_max_parser.py` and come from an extra, git-ignored local file in `examples/` that the example glob picks up.
  They predate this work and are excluded from the pass/fail judgment of each unit.

## Iteration log
| Unit | Iter | Commit | Verification | Notes |
|---|---|---|---|---|
| U0 | 1 | 1b471fa | ruff ✔, mypy ✔ (139 files), pytest 1188 passed / 9 known max_parser failures | dev-u0 (Sonnet 5.5). Review: CHANGES REQUIRED |
| U0 | 2 | f400021 | ruff ✔, mypy ✔, pytest 1212 passed / 9 known max_parser failures | fixes R1–R7. Re-review (Opus 5.5): APPROVE |
| U3 | 1 | 792d549 | ruff ✔, mypy ✔, pytest 739 (unit scope) passed | dev-u3. Review: APPROVED with NITs R1–R3 |
| U3 | 2 | 94c52a5 | ruff ✔, mypy ✔, pytest passed | R2 fix. Re-review: APPROVED. Merged to feature branch as 2dc83d3 |
| U2 | 1 | e711fb1 | ruff ✔, mypy ✔, pytest 674 (contracts/api/application) passed | dev-u2. Review: CHANGES REQUIRED (R1–R4) |
| U2 | 2 | a3fd6d4 | ruff ✔, mypy ✔, pytest 1365 passed (worktree) | fixes R1–R4. Re-review: APPROVED. Merged as 601b401 (cli.py conflict resolved: serve + err arg) |
| U1 | 1 | 3b8c2af | ruff ✔, mypy ✔ (140 files), pytest 1256 passed incl. PG | dev-u1. Review: CHANGES REQUIRED (R1–R4) |
| U1 | 2 | 1800bfc | ruff ✔, mypy ✔, pytest 1257 passed incl. PG | fixes R1–R3 (R4 = leader contract-doc commit bfa8e97). Re-review: APPROVED. Merged as 085335a |
| U2 | 3 | 9f3396b | ruff ✔, mypy ✔, pytest 1439 passed incl. PG | U2-R6 PG-backed signed-in fixtures. Review: CHANGES REQUIRED (R7–R9) |
| U2 | 4 | a89b284 | ruff ✔, mypy ✔, pytest 1440 passed incl. PG | fixes R7–R8. Re-review: APPROVED. Merged as 6ea6fde |
| integration | – | 601b401 | feature branch: ruff ✔, mypy ✔, pytest 1418 passed / 9 known max_parser failures | after U1+U2 merge |
| U4 | 1 | 28b5e24 | ruff ✔, mypy ✔, pytest 1543 passed incl. PG | dev-u4 (cycle 3). Review (tech-lead-c3): APPROVED with NITs R1–R2 |
| U4 | 2 | aef0f94 | ruff ✔, mypy ✔, pytest 1544 passed | fixes R1–R2. Re-review: APPROVED. Merged as 325f601 |
| U6 | 1 | 20460b9 | ruff ✔, mypy ✔, pytest 1609 passed | dev-u6. Review: CHANGES REQUIRED (R1–R5, minor) |
| U6 | sync | bd27111 | – | merge feature (U4) into unit/U6 |
| U6 | 2 | 23703dc | ruff ✔, mypy ✔, pytest 1717 passed | fixes R1–R5. Re-review: APPROVED. Merged as 091552c |
| U5 | 1 | 29d564d | ruff ✔, mypy ✔, pytest 1479 passed excl. test_imports (30 failures pending U4 cards_visible) | dev-u5. Review: CHANGES REQUIRED (R1–R2 minor, R3 note) |
| U5 | sync | b8e91c5 | pytest 1655 passed | merge feature (U4) into unit/U5 |
| U5 | 2 | 918e0f8 | ruff ✔, mypy ✔, pytest 1659 passed | fixes R1–R2. Re-review: APPROVED. Merged as 930af63 |
| integration | – | 930af63 | feature branch: ruff ✔, mypy ✔, pytest 1823 passed / 9 known max_parser failures | cycle 3 complete |

## Review findings
| Unit | # | Severity | Finding | Decision | Resolved in |
|---|---|---|---|---|---|
| U0 | R1 | MAJOR | `FilterOptionsRecord` lacks unknown counts and dateRange | valid → dev-u0 | U0 iter 2 |
| U0 | R2 | MAJOR | `ImportApiService` lacks ParserSelector/clock/stale-after wiring | valid → dev-u0 | U0 iter 2 |
| U0 | R3 | MAJOR | `start_run` contract doesn't expose `public_id` | valid → dev-u0 | U0 iter 2 |
| U0 | R4 | MINOR | `api_original_currency` docstring contradicts OQ-3 | valid → dev-u0 | U0 iter 2 |
| U0 | R5 | MINOR | `FakeUnitOfWorkFactory.create` lacks `isolation` | valid → dev-u0 | U0 iter 2 |
| U0 | R6 | MINOR | import-graph test doesn't enforce services rule over discovered modules | valid → dev-u0 | U0 iter 2 |
| U0 | R7 | MINOR | PyYAML only transitive | valid → dev-u0 | U0 iter 2 |
| U0 | R8 | MINOR | `month` overwrites from/to instead of AND | valid, deferred | U6 |
| U0 | R9 | MINOR | CV items route lacks from/to, sort lacks originalAmount | valid, deferred | U10 |
| U0 | R10 | MINOR | analysis uses shared FilterParams with non-openapi params | valid, deferred | U9 |
| U0 | R11 | MINOR | query/path validation → INVALID_REQUEST, no `instance` | valid, deferred | U2 |
| U0 | R12 | NIT | nickname max_length in DTO precedes trim | valid, deferred | U4 |
| U0 | R13 | NIT | CV batch DTOs allow extra keys | valid, deferred | U10 |
| U0 | R14 | NIT | single-value sort accepts lists | valid, deferred | U5/U8 |
| U0 | R15 | NIT | providers lack `@translates_database_errors`; placeholder methods non-abstract | valid, deferred | owning units |
| U0 | R16 | NIT | import-graph service exceptions not per-file, no dot boundary | valid, deferred | U5 (owns import services) |
| U0 | R17 | NIT | U1 must implement non-abstract `SqlImportRunRepository.public_id` | valid, deferred | U1 |
| U3 | R1 | NIT | plan §A HKDF info label for question ids | valid → team leader | 677e47d (`bt/iq`) |
| U3 | R2 | NIT | test picked example via glob | valid → dev-u3 | U3 iter 2 |
| U3 | R3 | NIT | currency_catalogue docstring should exclude XXX/XTS | valid → team leader | 677e47d |
| U2 | R1 | MAJOR | `_build_openapi` dropped route-declared 422s | valid → dev-u2 | U2 iter 2 |
| U2 | R2 | MAJOR | malformed JSON / sign-in body errors returned 422, contract says 400 | valid → dev-u2 | U2 iter 2 (400 INVALID_REQUEST, code MALFORMED_JSON) |
| U2 | R3 | NIT | filter ValueError leaked internal field names | valid → dev-u2 | U2 iter 2 |
| U2 | R4 | NIT | installment-plan derivation undocumented | valid → dev-u2 | U2 iter 2 (see note below) |
| U2 | R5 | MINOR | real `POST /sessions` must declare `responses=problem_responses(400, 429)` + `assert_operations_conform(app, "createSession")` | valid, deferred | U4 |
| U2 | R6 | MINOR | PG-backed `signed_in` fixture (bearer + cookie) | valid → dev-u2 | U2 iter 3 |
| U2 | R7 | MAJOR | NotImplementedError probe could silently keep stand-in authenticator | valid → dev-u2 | U2 iter 4 (explicit TEMPORARY swap; U4 must delete `StoredSessionAuthenticator` + swap) |
| U2 | R8 | NIT | no PG wrong-CSRF test | valid → dev-u2 | U2 iter 4 |
| U2 | R9 | NIT | hard-coded `SESSION_LIFETIME` in PG fixture | valid, deferred | U4 |
| U1 | R1 | MAJOR | internal-only review reasons leaked into `visible_review_reasons` | valid → dev-u1 | U1 iter 2 |
| U1 | R2 | MAJOR | test asserted the leak | valid → dev-u1 | U1 iter 2 |
| U1 | R3 | NIT | docstring said domestic rows never report country | valid → dev-u1 | U1 iter 2 |
| U1 | R4 | NIT | frozen `create_or_get` docstring said "any owner" | valid → team leader | bfa8e97 |
| U1 | note | – | `accounts.list_all` includes placeholder accounts (no bank/number); repository `user_id` scoping is optional — later units must always pass it | open | U5 / all |
| U0 | R17 | – | `public_id` implemented | resolved | U1 iter 1 |
| U4 | R1 | NIT | rehash computed inside UoW | valid → dev-u4 | U4 iter 2 |
| U4 | R2 | NIT | nickname maxLength missing in generated OpenAPI | valid → dev-u4 | U4 iter 2 |
| U4 | note | – | 501 probe in test_skeleton/test_foundation moved to `/api/v1/categorization`; U7 must move it again | open | U7 |
| U6 | R1 | MINOR | domestic and pending rows shared the null currency group | valid → dev-u6 | U6 iter 2 (pending key UNKNOWN; domestic null "No original currency") |
| U6 | R2 | MINOR | filter_options duplicated U4 card query | valid → dev-u6 | U6 iter 2 |
| U6 | R3 | MINOR | test-only UoW proxy hid stubbed visible_cards/trips | valid → dev-u6 (cards); trips deferred | U8: delete the TEMPORARY trips stand-in in test_transactions.py, run on real composition, add two-user trips check to filter-options isolation test |
| U6 | R4 | NIT | day/week keys depended on DateStyle | valid → dev-u6 | U6 iter 2 |
| U6 | R5 | NIT | duplicated `group:` prefix | valid → dev-u6 | U6 iter 2 (cross-ref comments) |
| U5 | R1 | MINOR | corrupt ZIP → 500 instead of 415 | valid → dev-u5 | U5 iter 2 |
| U5 | R2 | MINOR | 413 only on declared Content-Length | valid → dev-u5 | U5 iter 2 |
| U5 | R3 | NIT | row ParseError surfaces after identity/claims (sequence diagram order) — §A "parse error after detection" means read_context failures | recorded | – |
| U5 | note | – | `services/report.py` additive `rows_pending_currency` touched by U5 (allowed) | accepted | – |
| U3 | R4 | MINOR | `ExcelWorkbookReader._load` may not catch zlib.error → 500 instead of 422 UNREADABLE_WORKBOOK | valid, deferred | U11 |

Installment plan derivation (U2-R4, for U6): perPayment = round_amount(billing_amount) in billing currency; planTotal = plan_total_amount in plan_currency when stored, else perPayment × total in billing currency; remainingPayments = max(total − number, 0); remainingAmount = perPayment × remainingPayments in billing currency. An empty month ∩ from/to range returns 400 INVALID_FILTER (U0-R8 implemented by U2).

Accepted U0 deviations: additive `user_id=None` on existing repos; cookie `bt_session` in openapi; `CurrencyCatalogue.entries()`;
PATCH /transactions/{id} via `CategoryService.set_override`; trivial `open_bytes`; `pythonpath=["src","."]`; extra files
(`services/{composition,read_models,review_counter}.py`, identity adapter stubs handed to U3, separate `VisibleCardRepository`).
Ownership note: U0 created minimal `api/app.py`, `api/dependencies.py`, `api/problems.py`; U2 owns them from cycle 2.
