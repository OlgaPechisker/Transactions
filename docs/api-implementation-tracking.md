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

## Review findings
| Unit | # | Severity | Finding | Decision | Resolved in |
|---|---|---|---|---|---|
