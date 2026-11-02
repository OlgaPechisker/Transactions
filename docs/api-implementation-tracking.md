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

Accepted U0 deviations: additive `user_id=None` on existing repos; cookie `bt_session` in openapi; `CurrencyCatalogue.entries()`;
PATCH /transactions/{id} via `CategoryService.set_override`; trivial `open_bytes`; `pythonpath=["src","."]`; extra files
(`services/{composition,read_models,review_counter}.py`, identity adapter stubs handed to U3, separate `VisibleCardRepository`).
Ownership note: U0 created minimal `api/app.py`, `api/dependencies.py`, `api/problems.py`; U2 owns them from cycle 2.
