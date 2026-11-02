# Phase 2 - REST API (`/api/v1`) detailed plan

**Status: approved plan; implementation has not started.** Architecture and
policies: [api-design-phase-1.md](api-design-phase-1.md). Wire contract (source of truth):
`api-contract/{api-contract.md, openapi.yaml, screen-to-data-map.md}`. Baseline schema:
`persistence/orm/models.py` + `migrations/versions/0001_initial_schema.py`.

## A. Decisions that shape the plan

- **User scoping.** `user_id` on owners, accounts and cards, with scoped uniques and composite FKs (§B.6). Every identity lookup takes a `UserId`; identity questions list only the caller's claimed cards and never other users' owners or accounts.
- **Placeholder identities.** `CREATE_CARD` carries only a nickname. Resolution order: evidence owner/account → per-user placeholder owner (`display_name NULL`, `match_key NULL`, one per user via partial unique index) → per-owner placeholder account (`bank_code`, number NULL, one per owner via partial unique index). No fabricated identifiers.
- **Owner name.** `CardRef.ownerName` and `Card.owner.name` fall back to the caller's `users.display_name` for placeholder owners; the card label uses the same fallback.
- **Visible UNKNOWN_CURRENCY.** A row is user-visible UNKNOWN_CURRENCY only when it has a pending reason row, a `foreign_transaction_details` row, and `original_currency IS NULL`. One shared SQL predicate serves every counter and filter; the import report applies the same predicate in memory (`fx is not None and original.currency is None`).
- **Nullable import issue location.** `ImportIssue.sheetPosition`/`row` are `[integer, 'null']`, always present (TOTALS_MISMATCH has no location).
- **Import outcome order.** 413 → 415 → 422 selections syntax → 409 IMPORT_IN_PROGRESS (audit start) → open workbook (failure → 422 UNREADABLE_WORKBOOK + FAILED audit) → detect (`UnknownFormatError` → 422 UNKNOWN_STATEMENT_FORMAT + FAILED audit) → parse error after detection → 201 FAILED (issuer known) → claim conflict on any file card key → 409 DATA_ALREADY_OWNED (before questions) → selection referencing an unknown/inaccessible card → 404 / unknown question → 422 → unresolved → 201 NEEDS_INPUT → 201 SUCCESS/PARTIAL.
- **Same-file guard.** Partial unique index `ux_import_runs_running_sha (file_sha256) WHERE status='RUNNING'`; 23505 on audit start → `ImportInProgressError` → 409. `sweep_stuck_runs` frees stale RUNNING rows first. `file_path`/`file_name` stay NOT NULL and are filled from sha-derived audit labels (no filename). The import service takes `StatementUpload(bytes)` instead of `Path`.
- **Undo evidence.** Immutable `currency_validation_batch_members(batch_id, transaction_id)` plus `currency_validation_batches.undone_at`. Undo is valid iff every member's `currency_validations.batch_id` equals the batch; `undone_at IS NOT NULL` → 204.
- **Deduplication on source currency.** `transactions.source_original_currency` is set at insert and never changes; `_stored_group_counts` and inserts use it, because validation mutates `original_currency`.
- **Question ids.** `iq_` + base32(HMAC-SHA256(HKDF(cipher key, "identity-question"), issuer‖canonical hint)), truncated to 128 bits. Deterministic and does not leak last-4.
- **Trip overlap** uses a per-user row lock (`SELECT … FROM users WHERE id=:u FOR UPDATE`) plus an overlap check; no `btree_gist` extension.
- **CSRF token** is derived (HMAC of the session digest with an HKDF subkey of `BT_CIPHER_KEY`), not stored; no new secret variable.
- **Visibility** goes through `cards.claim_id` (NOT NULL, composite FK to the claim's issuer/last-4/user) plus `user_id` on owners/accounts/cards.
- **Providers** are repository ABCs exposed on `UnitOfWork`, which owns the transaction; there are no standalone provider objects.
- **Provisioning.** CLI `create-user` creates users administratively (no registration endpoint).
- **Schema rollout.** Rewrite `0001` for a fresh database and update the migration tests.
- **Contract choices.** Trips `sort` follows openapi (superset of api-contract §7.1); both card shapes (`ownerName`, `owner.name`) are implemented as written. `Location` headers on imports and batches are emitted as specified even though no GET exists for them. Missing groups for trip/card/currency/merchant/type dimensions use `key: null`.
- **Dependencies:** `fastapi`, `uvicorn[standard]`, `python-multipart`, `argon2-cffi`, `pycountry` (runtime); `httpx` (dev).
- **Architecture tests:** `api` must not import sqlalchemy/psycopg/`persistence.*`; services must not import fastapi/starlette/pydantic. `WorkbookReader.open_bytes` replaces the path-based entry point for the API.

---

# B. Detailed plan


## B.1 Package layout (follows existing conventions)
```
src/bank_transactions/
  api/                      # NEW – HTTP only (FastAPI). Imports services + domain only.
    app.py                  # create_app(composition) -> FastAPI; mounts routers at /api/v1
    dependencies.py         # current_user (bearer > cookie), require_csrf, pagination/sort parsing
    problems.py             # exception -> RFC 9457 mapping table (single place)
    schemas/*.py            # pydantic DTOs per feature (camelCase aliases, nullable always present)
    routers/{sessions,me,cards,imports,transactions,trips,analysis,currency_validation,categorization}.py
  domain/
    errors.py               # + NotFoundError, ConflictError(code), ValidationFailedError(errors), InvalidQueryError(code),
                            #   AuthenticationError, RateLimitedError(retry_after), UnsupportedFileError,
                            #   FileTooLargeError, UnreadableWorkbookError, CsrfError
    ids.py                  # + UserId, TripId, RuleId, BatchId, CustomCategoryId, ClaimId, SessionId; PublicId NewType(str)
    queries.py              # NEW – TransactionFilter, SortSpec, PageRequest, AnalysisQuery, Dimension, Bucket (pure)
    review.py               # NEW – visible-review predicate (in-memory form) for import counts
  ports/
    workbook_reader.py      # + open_bytes(data: bytes)
    password_hasher.py      # NEW ABC (hash/verify/needs_rehash)
    token_service.py        # NEW ABC (new_token, digest, csrf_for, question_id)
    currency_catalogue.py   # NEW ABC (codes() -> tuple[str,...]) — pycountry adapter
  identity/ (adapters)      # + argon2_hasher.py, tokens.py (HKDF/HMAC via cryptography), pycountry_catalogue.py
  persistence/
    interfaces.py           # + new repository ABCs (B.5); UnitOfWork gains attributes
    records.py / commands.py# + read models / commands (library-free)
    providers/*.py          # NEW concrete repositories per feature; repositories.py keeps import-path ones
    visibility.py           # shared SQLAlchemy selectables: visible_transactions(user), visible_review(), effective_category()
  services/                 # + session_service, profile_service, card_service, import_api_service, transaction_service,
                            #   category_service, trip_service, analysis_service, currency_validation_service
  bootstrap.py              # + build_api_composition(settings)
  cli.py                    # + `serve` (uvicorn), `create-user`
```
Endpoints are **sync `def`**, so FastAPI runs them in its bounded threadpool. Services and providers stay synchronous and never block the event loop (api-contract §1.2).

## B.2 Cross-cutting
- **Auth dependency** (`api/dependencies.py`): if an `Authorization: Bearer` header is present, it alone is used (invalid → 401, no cookie fallback). Otherwise the `bt_session` cookie is used and marked `via_cookie=True`. Calls `SessionService.authenticate(token) -> AuthContext(user_id, session_id, csrf_token)`.
- **CSRF**: dependency on every mutating route except `POST /sessions`. If `via_cookie` and `X-CSRF-Token` doesn't match `constant_time_compare(token_service.csrf_for(session_digest))`, raise `CsrfError` → 403 `CSRF_VALIDATION_FAILED`.
- **Problems** (`api/problems.py`): one table maps types to responses: `ValidationFailedError`→422 `VALIDATION_ERROR` (+errors[]); request-model validation errors →422 for bodies, 400 for query/path; `InvalidQueryError`→400 `INVALID_FILTER|INVALID_SORT`; `AuthenticationError`→401 (`INVALID_CREDENTIALS` on sign-in); `CsrfError`→403; `NotFoundError`→404 `NOT_FOUND`; `ConflictError(code)`→409 code; `FileTooLargeError`→413 `FILE_TOO_LARGE`; `UnsupportedFileError`→415; `UnreadableWorkbookError`/`UnknownFormatError`→422 code; `RateLimitedError`→429 (+`Retry-After`); `PersistenceError`/other →500 generic. Messages come only from sanitized error text; there are no paths or numbers (§1.8).
- **Public IDs**: column `public_id text NOT NULL UNIQUE`, value `<prefix>_<26-char base32 of 16 random bytes>` (`usr_ crd_ txn_ imp_ trp_ rul_ cvb_`). Generated in providers on insert (`secrets`). Path ids are resolved to internal ids **inside the user-scoped query**, so unknown and foreign ids both give 404.
- **Pagination/sort/filter parsing**: done in the endpoint (syntax only: types, regex, enum). The domain checks semantics: amount bounds require `billingCurrency`; `reviewReason` ≠ UNKNOWN_CURRENCY → INVALID_FILTER; unknown sort field → INVALID_SORT; `from ≤ to`. `page` past the end → empty items.
- **Money/Rate DTO**: `amount` = Decimal quantized to 2 dp as a string; rate = `abs(stored)` quantized to 6 dp; unknown currency → `null`.
- **Card label** (domain `card_labels.py`): `f"{owner_name or user_name} · {issuer_title} ••{last4}"` (issuer titles Isracard/Max/Cal). last4 is decrypted through the existing `IdentifierProtector` port and cached per request.
- **Visibility** (`persistence/visibility.py`): `transactions t JOIN cards c ON c.id=t.card_id JOIN card_ownership_claims k ON k.id=c.claim_id AND k.user_id=:u`. Every read and write on transactions, cards, imports, trips, categories and validations goes through it.
- **Effective category** (SQL, one CTE): `COALESCE(ov.name, rule.name, map.name, t.bank_category_raw)` with source `OVERRIDE|RULE|MAPPING|BANK`. The rule is a LATERAL lookup: newest `category_rules.id` with any `term_norm` that is a substring of `t.merchant_name_norm` (`strpos > 0`). Terms are normalized with the same normalizer as `merchant_name_norm`.
- **Visible review predicate**: `EXISTS trr(reason='UNKNOWN_CURRENCY') AND EXISTS ftd AND t.original_currency IS NULL`.
- **Dependencies (flagged, new)**: fastapi, uvicorn[standard], python-multipart, argon2-cffi, pycountry; dev: httpx.

## B.3 Endpoints → Domain → Provider (all 31 operations)

Notation: **E** endpoint (route, DTO, status/errors) · **D** service method + rules · **P** repository methods + tx/concurrency.
All services raise domain errors only. All routes except sign-in require auth. ✱ = CSRF on cookie auth.

### Sessions & profile
1. **POST /sessions** — E: `SignInRequest{email:str, password:str}` → 201 `SessionResponse{token, expiresAt, user: Me}` + `Set-Cookie bt_session=…; HttpOnly; Secure; SameSite=Lax; Path=/api; Max-Age=43200`. Errors 400/401 INVALID_CREDENTIALS/429.
   D: `SessionService.sign_in(email, password, client_ip) -> NewSession`. Normalize the email (`strip().casefold()`) and keep the password exact. Check throttle(email_key, ip) ≥10 failures in 15 min → `RateLimitedError`. Verify with Argon2id; on an unknown email, still run a dummy verify (timing). On failure, record failure for both keys → `AuthenticationError`. On success, rehash if needed; token = 32 random bytes; store `sha256(token)`; expires = now+12h.
   P: `UserRepository.find_by_email_key`, `.update_password_hash`; `LoginThrottleRepository.count_failures(kind,key_digest,since)`, `.record_failure(...)`, `.purge_before(ts)`; `SessionRepository.create(NewSession)`. Each call is its own UoW; failure recording is committed even when sign-in fails.
2. **DELETE /sessions/current** ✱ — E: 204 + cookie cleared. D: `SessionService.sign_out(ctx)`. P: `SessionRepository.revoke(session_id, now)` (idempotent).
3. **GET /me** — E: 200 `Me{id,name,email,role:"OWNER",csrfToken,counters}`. D: `ProfileService.me(ctx) -> MeView`. Counters: `pendingCurrencyValidations` = visible-review count. `needsReviewByReason` = `[{UNKNOWN_CURRENCY,n}]` only if n>0.
   P: `UserRepository.get(user_id)`; `CurrencyValidationRepository.count_pending(user_id)`.

### Cards
4. **GET /cards** — E: 200 `{items:[Card]}`. D: `CardService.list(ctx) -> list[CardView]` (decrypt last4, owner name fallback, label). P: `CardRepository.list_visible(user_id) -> tuple[VisibleCardRecord(card_id, public_id, issuer, last4_enc, display_name, owner_display_name|None)]`.
5. **PATCH /cards/{cardId}** ✱ — E: `UpdateCardRequest{nickname: str|None ≤120}`; missing key → 422. 200 Card; 404. D: `CardService.set_nickname(ctx, card_public_id, nickname)`: trim; empty → None; >120 → `ValidationFailedError(field=nickname, code=TOO_LONG)`. P: `CardRepository.set_display_name(user_id, public_id, value) -> VisibleCardRecord | None` (single scoped UPDATE … RETURNING; None → 404).

### Imports
6. **POST /imports** ✱ — E: multipart `file: UploadFile`, `dryRun: bool`, `identitySelections: str|None`. Checks, in order:
   - Content-Length or streamed size > 20 971 520 → 413. The stream is read into memory with a cap.
   - Bytes not a ZIP/OOXML container (`PK\x03\x04` + `[Content_Types].xml` with the spreadsheetml main part) → 415.
   - `identitySelections` JSON parsed into `IdentitySelectionDto[]` (discriminated union); malformed → 422 VALIDATION_ERROR.
   - Calls the service, then maps `ImportOutcome` → 201 `ImportResult` + `Location`.
   D: `ImportApiService.run(ctx, upload: StatementUpload(data: bytes), dry_run, selections: tuple[ApiSelection]) -> ImportOutcome`. It adapts the existing `ImportService.import_file` to:
     - (a) `hash_bytes`;
     - (b) `AuditTrail.start(labels_from_sha, sha)`, where `ImportInProgressError` comes from the provider;
     - (c) `reader.open_bytes`;
     - (d) a user-scoped `IdentityGate(user_id)`;
     - (e) claim check;
     - (f) API selections mapped to domain `UseCard`/`CreateCard(nickname)` with the placeholder resolution order (§A);
     - (g) report → counts with `needsReview` via `domain/review.py` (visible review predicate);
     - (h) issues mapped with nullable location;
     - (i) the import outcome order (§A).
   Question ids come from `TokenService.question_id(issuer, hint)`. A selection with an unknown questionId → 422 (field `identitySelections[i].questionId`, code `UNKNOWN_QUESTION`). A USE_EXISTING_CARD cardId not among the caller's visible cards → 404. A nickname > 120 → 422. A dry run rolls back the domain UoW (existing behavior), including claims.
   P (inside the domain UoW): `ClaimRepository.find(issuer, last4_enc) -> ClaimRecord|None`, `.create_or_get(user_id, issuer, last4_enc) -> ClaimRecord` (`INSERT … ON CONFLICT (issuer_code,last4_enc) DO NOTHING RETURNING`, else `SELECT … FOR UPDATE`). A claim owned by another user → `DataAlreadyOwnedError` (409). `SELECT … FOR UPDATE` on existing claims serializes overlapping files on the same card key before dedup counting. `OwnerRepository.find_placeholder(user_id)/create_placeholder`, `AccountRepository.find_placeholder(owner_id)/create_placeholder`. Existing repos gain the `user_id` param. `ImportRunRepository.start_run` translates 23505 on `ux_import_runs_running_sha` → `ImportInProgressError`; it sets `public_id`.
7. **GET /imports** — E: `page,pageSize,sort∈{startedAt,-startedAt}` → 200 paginated `ImportSummary`. D: `ImportApiService.history(ctx, PageRequest, sort)`. P: `ImportHistoryRepository.page(user_id, page, size, descending) -> Page[ImportSummaryRecord]`:
   `SELECT r.public_id, r.file_sha256, r.started_at, r.status, count(t.id) FROM import_runs r JOIN visible_transactions t ON t.import_run_id=r.id WHERE r.status IN ('SUCCESS','PARTIAL') GROUP BY r.id ORDER BY r.started_at {dir}, r.id {dir}`. `rowsImported` = visible inserted count. Dry runs never commit transactions, so the join excludes them.

### Transactions
8. **GET /transactions** — E: parse every query param (openapi 225-305) into `TransactionFilterDto`; `include` csv; sort regex → 400 INVALID_SORT on mismatch. 200 `{items, meta, summary?}` (summary contains `BillingSummary` and `groups` when requested).
   D: `TransactionService.list(ctx, TransactionFilter, groupBy, include, SortSpec, PageRequest) -> TransactionPage`. Rules:
   - amount bounds need `billingCurrency` → INVALID_FILTER;
   - `reviewReason` only UNKNOWN_CURRENCY;
   - `month` → [first,last] ANDed with from/to;
   - `none`/`UNKNOWN` special values;
   - `groupBy` key/label rules (missing → null, labels per §1.9; other dims OQ-2).
   P: `TransactionQueryRepository.page(user_id, filter, sort, page) -> Page[TransactionViewRecord]`, `.summary(user_id, filter) -> BillingSummaryRecord`, `.groups(user_id, filter, dimension) -> tuple[GroupRecord]`. One base selectable: visible + effective category + trip membership (tag ∪ auto−excluded, one trip per tx) + visible review count. Sort keys map to columns, with `t.id` as the tiebreaker. Read-only; a single REPEATABLE READ UoW keeps page, total and summary consistent.
9. **GET /transactions/{id}** — E: 200 `TransactionDetail`; 404. D: `TransactionService.get(ctx, public_id)`. P: `TransactionQueryRepository.get(user_id, public_id) -> TransactionViewRecord|None`.
10. **PATCH /transactions/{id}** ✱ — E: `{customCategory: str|None (1..60)}` → 200 TransactionDetail; 404; 422 UNKNOWN_CATEGORY. D: `TransactionService.set_override(ctx, public_id, name|None)`. Resolve the category by name key (trimmed, casefolded) within the user; a missing one → `ValidationFailedError(customCategory, UNKNOWN_CATEGORY)`. P: `CategoryRepository.find_by_key(user_id, key)`; `OverrideRepository.upsert(user_id, tx_id, category_id)` / `.delete(user_id, tx_id)` (ON CONFLICT (user_id, transaction_id) DO UPDATE). Same UoW; then re-read the view.
11. **GET /filter-options** — E: 200 `FilterOptions` (openapi schema). D: `TransactionService.filter_options(ctx)`. P: `TransactionQueryRepository.filter_options(user_id)`: distinct visible bank categories, custom categories, cards, original currencies (+UNKNOWN when any visible-review), billing currencies, countries, types, trips (via TripRepository.list).

### Trips
12. **GET /trips** — E: `sort ∈ {startDate,-startDate,name,-name}` → 200 `{items:[TripSummary]}`. D: `TripService.list(ctx, sort)`. P: `TripRepository.list_with_stats(user_id, sort) -> tuple[TripSummaryRecord]` (members = tags ∪ auto−exclusions, BillingSummary per trip).
13. **POST /trips** ✱ — E: `{name 1..80, currency, startDate, endDate}` → 201 TripDetail + Location; 409 TRIP_DATE_OVERLAP; 422 (`endDate` BEFORE_START_DATE, currency not in catalogue → INVALID_CURRENCY). D: `TripService.create(ctx, NewTripCommand)`: trim the name; validate the currency via the `CurrencyCatalogue` port; overlap check inclusive `start ≤ other.end AND end ≥ other.start`. P: `UserRepository.lock(user_id)` (`SELECT … FOR UPDATE`), `TripRepository.exists_overlap(user_id, start, end)`, `.create(...)`, then commit. The lock serializes concurrent creates for one user.
14. **GET /trips/{id}** — E: 200 TripDetail; 404. D: `TripService.get(ctx, public_id)` assembles stats, category/day/outside-range breakdowns, and member lists (TAGGED vs AUTO). P: `TripRepository.get(user_id, public_id)`, `.members(trip_id, user_id)`, `.breakdowns(trip_id, user_id)`.
   Auto rule: API-visible original currency = trip currency (OQ-3) AND `transaction_date BETWEEN start AND end` AND no exclusion AND no tag on any trip. A tag wins.
15. **GET /trips/{id}/candidates** — E: `q?`, `limit 1..50 (8)` → 200 `{items:[TransactionListItem]}`. D: `TripService.candidates(ctx, public_id, q, limit)`. With no `q`, the window is start−30d..end+7d. With `q`, the search runs across merchant ILIKE, a `YYYY-MM-DD` prefix of the date, and effective-category ILIKE. Non-members only. P: `TripRepository.candidates(user_id, trip_id, window|None, q|None, limit)` (q escaped for LIKE).
16. **PUT …/tagged-transactions/{txId}** ✱ — 204/404. D: `TripService.tag(ctx, trip_pid, tx_pid)`. P: same UoW: `TripRepository.get_id`, `TransactionQueryRepository.visible_id`, `TagRepository.upsert(tx_id, trip_id)` (PK transaction_id, ON CONFLICT DO UPDATE trip_id), `ExclusionRepository.delete(trip_id, tx_id)`.
17. **DELETE …/tagged-transactions/{txId}** ✱ — 204 (also when not tagged here); 404 when either is invisible. P: `TagRepository.delete(tx_id, trip_id)` (only if tagged to this trip). Auto membership is computed at read time, so there is nothing to recalculate.
18. **PUT …/excluded-transactions/{txId}** ✱ — 204; 404; 409 NOT_AUTO_MEMBER. D: an already excluded pair → 204; else require auto membership (computed with the same predicate as #14) else `ConflictError(NOT_AUTO_MEMBER)`. P: `TripRepository.is_auto_member(user_id, trip_id, tx_id)`, `ExclusionRepository.insert_if_absent`.
19. **DELETE …/excluded-transactions/{txId}** ✱ — 204 idempotent; 404. P: `ExclusionRepository.delete`.

### Analysis
20. **GET /spending-analysis** — E: params per openapi 532-600 → 200 `SpendingAnalysis`; 400 for a missing `billingCurrency`, from>to, or an unknown enum.
   D: `AnalysisService.analyze(ctx, AnalysisQuery) -> SpendingAnalysisView`. Pure domain functions:
   - bucket calendar (Monday weeks, UTC "today", complete vs partial);
   - zero-filled buckets;
   - comparison ranges (`previous` = preceding equal inclusive-day range; `yoy` with Feb 29 → Feb 28);
   - monthly average over active complete calendar months only;
   - missing-group keys.
   No shares, gross/refund splits, or original-currency aggregates.
   P: `AnalysisRepository.groups(user_id, q, dim)`, `.series(user_id, q, bucket, series_dim)`, `.matrix(user_id, q, row_dim, bucket)`. SQL `date_trunc` on `transaction_date` with `billing_currency = :bc`, sum and count. The domain fills gaps. All queries use one REPEATABLE READ UoW.

### Currency validation
21. **GET /currency-validation-queue** — E: 200 `CurrencyValidationQueue`. D: `CurrencyValidationService.overview(ctx)`. `total = pending(visible-review) + validated`. Country options come from pending rows. The currency picker is `CurrencyCatalogue.codes()` (pycountry, minus XXX/XTS). P: `CurrencyValidationRepository.count_pending`, `.count_validated`, `.pending_countries`.
22. **GET /currency-validation-queue/items** — E: `status, country, from, to, merchant, sort (default -transactionDate), page, pageSize(40, ≤200)` → 200 paginated. Sort fields aren't enumerated in the contract (OQ-8); default to the transaction list sort set, unknown → 400 INVALID_SORT. D: `.items(ctx, ItemsQuery)`. P: `.page_items(user_id, status, filter, sort, page)`. PENDING uses the visible-review predicate; VALIDATED joins `currency_validations`.
23. **POST /currency-validation-batches** ✱ — E: `CurrencyValidationBatchRequest` (exactly one of `transactionIds[]` or `filter`) → 201 `CurrencyValidationBatch{id, currency, validatedCount, transactionIds}` + Location; 404; 409 ALREADY_VALIDATED; 422.
   D: `.create_batch(ctx, cmd)`:
   - validate the currency in the catalogue;
   - require exactly one target;
   - deduplicate ids;
   - any invisible id → 404 for the whole batch;
   - any visible id already validated → 409;
   - any explicit id that is visible but not pending → 409 ALREADY_VALIDATED if validated, else 422 `NOT_PENDING` (OQ-9);
   - a filter target resolves to the current pending set (may be empty → 201 with count 0).
   P, one UoW, atomic: `TransactionQueryRepository.lock_visible(user_id, ids)` (`SELECT … FOR UPDATE ORDER BY id`); for a filter, `lock_pending_matching(user_id, filter)`. Then `CurrencyValidationRepository.create_batch`, `.add_members`, `UPDATE transactions SET original_currency=:c`, `DELETE trr(UNKNOWN_CURRENCY)`, `INSERT currency_validations(..., validated_by_user_id)`. `source_original_currency` is never touched.
24. **DELETE /currency-validation-batches/{id}** ✱ — 204/404/409. D: `.undo_batch(ctx, pid)`. Invisible → 404. `undone_at` set → 204. Any member whose `currency_validations.batch_id ≠ batch` (moved back or revalidated) → 409 `BATCH_CHANGED`, change nothing. Else, for every member: `original_currency=NULL`, re-insert the reason, delete the validation, set `undone_at=now`. Members stay as evidence. P: `.lock_batch(user_id, pid)` (FOR UPDATE), `.members_state(batch_id)` (FOR UPDATE on the member transactions), `.undo(batch_id, now)`.
25. **DELETE /currency-validations/{txId}** ✱ — 204/404. D: `.move_back(ctx, tx_pid)`. Invisible → 404. Validated → reset (same three writes for the one row). Not validated (pending, or not eligible) → 204 no-op. The batch keeps its member row, so a later undo of that batch returns 409. P: `.lock_validation(tx_id)`, `.reset(tx_id)`.

### Categorization
26. **GET /categorization** — E: 200 `CategorizationOverview`. D: `CategoryService.overview(ctx)`: bank categories with counts, mappings, rules newest-first with terms, custom categories. P: `CategoryRepository.list`, `MappingRepository.list`, `RuleRepository.list_newest_first`, `TransactionQueryRepository.bank_category_counts(user_id)`.
27. **POST /custom-categories** ✱ — E: `CustomCategory{name}` → 201; 409 CATEGORY_EXISTS; 422. D: `.create_category`: trim; 1..60 (openapi CustomCategory bounds); `casefold()=="none"` → 422 RESERVED_NAME. P: `CategoryRepository.create(user_id, name, name_key)`; a 23505 on `uq_custom_categories_user_key` → `ConflictError(CATEGORY_EXISTS)` (race-safe).
28. **PUT /category-mappings/{bankCategory}** ✱ — E: the path is URL-decoded by the framework; body `{customCategory}` → 200 CategoryMapping; 404; 422 UNKNOWN_CATEGORY. D: `.put_mapping`: the bank category must appear on a visible transaction (OQ-4) else 404; the custom category must exist else 422. P: `TransactionQueryRepository.has_bank_category(user_id, raw)`, `MappingRepository.upsert(user_id, raw, category_id)`.
29. **DELETE /category-mappings/{bankCategory}** ✱ — 204; 404 if no mapping. P: `MappingRepository.delete(user_id, raw) -> bool`.
30. **POST /category-rules** ✱ — E: `{terms[≥1], category}` (additionalProperties false → extra keys 422) → 201 CategoryRule. D: `.create_rule`: normalize terms with the merchant normalizer; drop blanks/duplicates; empty after normalizing → 422; the category must exist → else 422 UNKNOWN_CATEGORY. P: `RuleRepository.create(user_id, category_id, terms) -> RuleRecord` (rule + terms in one UoW).
31. **DELETE /category-rules/{id}** ✱ — 204/404. P: `RuleRepository.delete(user_id, public_id) -> bool` (terms cascade).

## B.4 Domain additions (pure, library-free)
- `domain/queries.py`: `TransactionFilter`, `AnalysisQuery`, `SortSpec`/`SortField` enums per endpoint, `PageRequest(page≥1, 1≤size≤200)`, `Dimension`, `Bucket`, special `NONE`/`UNKNOWN` sentinels.
- `domain/review.py`: `is_user_visible_unknown_currency(parsed_tx) -> bool` (fx present ∧ original.currency None ∧ reason present).
- `domain/calendar.py`: bucket ranges, completeness, comparison ranges, leap-day mapping.
- `services/card_labels.py`: label/owner-name fallback.
- Errors listed in B.1, each with a `code` attribute used by the problem mapper.

## B.5 Provider interfaces (in `persistence/interfaces.py`, ABCs, library-free)
`UserRepository(find_by_email_key, get, lock, create, update_password_hash)`, `SessionRepository(create, find_active_by_digest, revoke)`,
`LoginThrottleRepository(count_failures, record_failure, purge_before)`, `ClaimRepository(find, create_or_get, lock)`,
`CardRepository(list_visible, get_visible, set_display_name)` + existing `CardRepository.find_candidates(lookup with user_id)`,
`OwnerRepository/AccountRepository` + placeholder methods and `user_id` on all methods, `ImportHistoryRepository(page)`,
`TransactionQueryRepository(page, summary, groups, get, visible_id, lock_visible, lock_pending_matching, filter_options, bank_category_counts, has_bank_category)`,
`CategoryRepository`, `OverrideRepository`, `MappingRepository`, `RuleRepository`, `TripRepository`, `TagRepository`, `ExclusionRepository`,
`AnalysisRepository`, `CurrencyValidationRepository`. `UnitOfWork` gains one attribute per repository. `SqlAlchemyUnitOfWork` wires them.
`UnitOfWorkFactory.create(isolation: Isolation = READ_COMMITTED)` gets an optional `REPEATABLE_READ` for read-model consistency.
All concrete classes are decorated with `@translates_database_errors`; 23505 constraint names map to specific domain conflicts in a small table inside `db.py`.

## B.6 Schema changes — rewrite `0001_initial_schema.py` (fresh DB; ORM kept in step)

**Changed existing tables** (deltas vs current 0001):
```sql
-- owners
ALTER: + user_id bigint NOT NULL REFERENCES users(id) ON DELETE RESTRICT;
       display_name text NULL; match_key text NULL;
       DROP uq_owners_match_key;
       + CONSTRAINT uq_owners_user_match_key UNIQUE (user_id, match_key);
       + CONSTRAINT uq_owners_id_user UNIQUE (id, user_id);
       + CONSTRAINT ck_owners_placeholder CHECK ((display_name IS NULL) = (match_key IS NULL));
       + CREATE UNIQUE INDEX ux_owners_user_placeholder ON owners(user_id) WHERE match_key IS NULL;
-- accounts
       + user_id bigint NOT NULL;
       + FOREIGN KEY (owner_id, user_id) REFERENCES owners(id, user_id)  -- fk_accounts_owner_user
       DROP uq_accounts_bank_code_account_number_enc;
       + UNIQUE (user_id, bank_code, account_number_enc)               -- uq_accounts_user_bank_number
       + UNIQUE (id, owner_id, user_id)                                -- uq_accounts_id_owner_user (keeps uq_accounts_id_owner_id)
       + CREATE UNIQUE INDEX ux_accounts_owner_placeholder ON accounts(owner_id)
           WHERE bank_code IS NULL AND account_number_enc IS NULL;
-- cards
       + user_id bigint NOT NULL; + claim_id bigint NOT NULL; + public_id text NOT NULL UNIQUE;
       fk_cards_account_owner → FOREIGN KEY (account_id, owner_id, user_id) REFERENCES accounts(id, owner_id, user_id);
       + FOREIGN KEY (claim_id, issuer_code, last4_enc, user_id)
           REFERENCES card_ownership_claims(id, issuer_code, last4_enc, user_id)   -- fk_cards_claim
       + INDEX ix_cards_claim_id (claim_id)
       (ix_cards_issuer_last4 / ix_cards_account_issuer_last4 kept, non-unique)
-- import_runs
       + public_id text NOT NULL UNIQUE;
       + INDEX ix_import_runs_file_sha256 (file_sha256);
       + CREATE UNIQUE INDEX ux_import_runs_running_sha ON import_runs(file_sha256) WHERE status = 'RUNNING';
       (file_path/file_name kept NOT NULL, filled with sha-derived audit labels)
-- transactions
       + public_id text NOT NULL UNIQUE;
       + source_original_currency char(3) NULL CHECK (source_original_currency ~ '^[A-Z]{3}$');
       + INDEX ix_transactions_import_run_id (import_run_id);
       + INDEX ix_transactions_card_original_currency_date (card_id, original_currency, transaction_date);
```
> No `dry_run` column: dry runs roll back their transactions (`import_service.py:136`), so they can never join visible
> transactions in history. The audit already marks them in `error_summary` (`import_service.py:306-307`).

**New tables**:
```sql
CREATE TABLE users (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  public_id text NOT NULL UNIQUE,
  email text NOT NULL, email_key text NOT NULL,
  display_name text NOT NULL CHECK (btrim(display_name) <> ''),
  password_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_users_email_key UNIQUE (email_key),
  CONSTRAINT ck_users_email_key CHECK (email_key = lower(btrim(email_key))));
CREATE TABLE sessions (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_digest bytea NOT NULL UNIQUE,
  created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, revoked_at timestamptz NULL,
  CONSTRAINT ck_sessions_expiry CHECK (expires_at > created_at));
CREATE INDEX ix_sessions_user_id ON sessions(user_id);
CREATE INDEX ix_sessions_expires_at ON sessions(expires_at);
CREATE TYPE login_key_kind_enum AS ENUM ('EMAIL','IP');
CREATE TABLE login_failures (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key_kind login_key_kind_enum NOT NULL, key_digest bytea NOT NULL, failed_at timestamptz NOT NULL);
CREATE INDEX ix_login_failures_key_time ON login_failures(key_kind, key_digest, failed_at);
CREATE TABLE card_ownership_claims (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id bigint NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  issuer_code text NOT NULL,                       -- same IssuerCodeColumn + _issuer_code_check("card_ownership_claims") as cards (orm/models.py:45-50,88)
  last4_enc bytea NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_claims_issuer_last4 UNIQUE (issuer_code, last4_enc),
  CONSTRAINT uq_claims_key_user UNIQUE (id, issuer_code, last4_enc, user_id));
CREATE INDEX ix_claims_user_id ON card_ownership_claims(user_id);
CREATE TABLE custom_categories (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name text NOT NULL, name_key text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_custom_categories_name CHECK (name = btrim(name) AND char_length(name) BETWEEN 1 AND 60),
  CONSTRAINT ck_custom_categories_reserved CHECK (name_key <> 'none'),
  CONSTRAINT uq_custom_categories_user_key UNIQUE (user_id, name_key),
  CONSTRAINT uq_custom_categories_id_user UNIQUE (id, user_id));
CREATE TABLE transaction_category_overrides (
  user_id bigint NOT NULL, transaction_id bigint NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  custom_category_id bigint NOT NULL,
  PRIMARY KEY (user_id, transaction_id),
  FOREIGN KEY (custom_category_id, user_id) REFERENCES custom_categories(id, user_id) ON DELETE CASCADE);
CREATE INDEX ix_overrides_category ON transaction_category_overrides(custom_category_id);
CREATE TABLE category_mappings (
  user_id bigint NOT NULL, bank_category_raw text NOT NULL, custom_category_id bigint NOT NULL,
  PRIMARY KEY (user_id, bank_category_raw),
  FOREIGN KEY (custom_category_id, user_id) REFERENCES custom_categories(id, user_id) ON DELETE CASCADE);
CREATE TABLE category_rules (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, public_id text NOT NULL UNIQUE,
  user_id bigint NOT NULL, custom_category_id bigint NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (custom_category_id, user_id) REFERENCES custom_categories(id, user_id) ON DELETE CASCADE);
CREATE INDEX ix_category_rules_user_id_desc ON category_rules(user_id, id DESC);
CREATE TABLE category_rule_terms (
  rule_id bigint NOT NULL REFERENCES category_rules(id) ON DELETE CASCADE,
  term_norm text NOT NULL CHECK (term_norm <> ''), PRIMARY KEY (rule_id, term_norm));
CREATE TABLE trips (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, public_id text NOT NULL UNIQUE,
  user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name text NOT NULL CHECK (name = btrim(name) AND char_length(name) BETWEEN 1 AND 80),
  currency char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  start_date date NOT NULL, end_date date NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_trips_dates CHECK (end_date >= start_date));
CREATE INDEX ix_trips_user_start ON trips(user_id, start_date);
CREATE TABLE trip_transaction_tags (
  transaction_id bigint PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
  trip_id bigint NOT NULL REFERENCES trips(id) ON DELETE CASCADE);
CREATE INDEX ix_trip_tags_trip_id ON trip_transaction_tags(trip_id);
CREATE TABLE trip_transaction_exclusions (
  trip_id bigint NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
  transaction_id bigint NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  PRIMARY KEY (trip_id, transaction_id));
CREATE INDEX ix_trip_exclusions_transaction_id ON trip_transaction_exclusions(transaction_id);
CREATE TABLE currency_validation_batches (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, public_id text NOT NULL UNIQUE,
  user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  currency char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  applied_count integer NOT NULL CHECK (applied_count >= 0),
  created_at timestamptz NOT NULL, undone_at timestamptz NULL,
  CONSTRAINT ck_batches_undone CHECK (undone_at IS NULL OR undone_at >= created_at));
CREATE INDEX ix_cv_batches_user_created ON currency_validation_batches(user_id, created_at);
CREATE TABLE currency_validation_batch_members (
  batch_id bigint NOT NULL REFERENCES currency_validation_batches(id) ON DELETE CASCADE,
  transaction_id bigint NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  PRIMARY KEY (batch_id, transaction_id));
CREATE INDEX ix_cv_members_transaction ON currency_validation_batch_members(transaction_id);
CREATE TABLE currency_validations (
  transaction_id bigint PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
  batch_id bigint NOT NULL REFERENCES currency_validation_batches(id) ON DELETE RESTRICT,
  currency char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  validated_by_user_id bigint NOT NULL REFERENCES users(id),
  validated_at timestamptz NOT NULL);
CREATE INDEX ix_cv_batch_id ON currency_validations(batch_id);
CREATE INDEX ix_cv_user_time ON currency_validations(validated_by_user_id, validated_at);
```
**Order in upgrade:** enums → users → sessions, login_failures → card_ownership_claims → owners → accounts → cards → import_runs → installment_plans → transactions (+children) → custom_categories → overrides, mappings → rules → terms → trips → tags, exclusions → cv_batches → members → validations.
**Downgrade:** drop in exact reverse order, then the enum types (incl. `login_key_kind_enum`). This is **destructive**: it drops all user data; the docstring and README say so.
**Backfill:** none (fresh DB, user decision). `source_original_currency` is set at insert = parsed original currency (the same value as `original_currency` at import).
**Locking/downtime:** none. The tables are created empty, so there are no table rewrites or lock contention. Production upgrade from the old 0001 is unsupported by decision; the `init-db` path expects an empty schema.
**Rollback feasibility:** the downgrade is complete; re-upgrade is covered by the existing round-trip test.

## B.7 Work breakdown for parallel implementation

### B.7.1 Strategy
- **Wave 0 (one developer, short, blocking):** U0 "contract freeze + walking skeleton". It writes every cross-unit
  interface, record, error, DTO building block, stub class and the DI wiring. Stubs raise `NotImplementedError`.
  After U0 merges, the app starts, every route exists (501 from the stubs), and every shared file is frozen.
- **Wave 1 (parallel, up to 10 developers):** foundation units **U1–U3** and feature slices **U4–U10** run
  side by side. Each unit owns its files exclusively and codes only against U0 contracts. Units that need
  another unit's runtime behaviour use the **shared fakes** U0 ships.
  - **Soft gates:** a unit's *PostgreSQL* tests need U1 merged; its *endpoint* tests need U2 merged. Domain
    and service unit tests never wait.
- **Wave 2 (one developer):** U11 integration hardening: wiring check, contract/OpenAPI conformance, and
  cross-slice end-to-end scenarios.

```
U0 ──┬─> U1 schema+persistence core ─┐ (gates PG tests of U4..U10)
     ├─> U2 API foundation ──────────┤ (gates endpoint tests of U4..U10)
     ├─> U3 adapters ────────────────┤ (fakes used until merged)
     └─> U4 auth/profile/cards, U5 imports, U6 transactions, U7 categorization,
         U8 trips, U9 analysis, U10 currency validation ──> U11 integration
```

### B.7.2 Ground rules for all units
1. **Exclusive ownership:** a unit edits only the files in its "Owns" list. Every other file is read-only.
2. **Frozen contracts (owned by U0):** `domain/ids.py`, `domain/errors.py`, `domain/queries.py`, `domain/card_labels.py`,
   `ports/*.py`, `persistence/interfaces.py`, `persistence/records.py`, `persistence/commands.py`,
   `services/interfaces.py`, `api/schemas/common.py`, `api/routers/__init__.py`, `bootstrap.py`, `persistence/uow.py`,
   `tests/fakes/*`.
   - **Changing a contract:** open a "contract change" PR that only touches these files. Changes must be
     additive (new optional field or new method). The tech lead reviews it, and it merges before the unit PR
     that uses it.
3. **Layer rules** are enforced by the extended import-graph test, which U0 ships first so every PR runs it:
   - `api` → `services`, `domain`;
   - `services` → `domain`, `ports`, `persistence.interfaces/records/commands`;
   - `persistence.providers` → `persistence.*`, `domain`;
   - only `bootstrap` imports concrete classes.
4. **Error codes and Problem shape** come only from `domain/errors.py` + `api/problems.py`. A unit never builds
   Problem JSON itself.
5. **Definition of done (every unit):** ruff + mypy strict clean; the unit's tests are green (including PG tests
   once U1 is merged); no stub `NotImplementedError` left in owned files; owned OpenAPI operations pass the
   U2 conformance helper; no edits outside "Owns".

### B.7.3 Contract catalogue (frozen by U0)
Signatures are Python-style. Every type is a frozen dataclass or NewType, library-free.

**K1 IDs and errors** (`domain/ids.py`, `domain/errors.py`)
- `UserId, SessionId, ClaimId, TripId, RuleId, BatchId, CustomCategoryId = NewType(int)`; `PublicId = NewType(str)`.
- `ApiError(BankTransactionsError)` with `code: str`. Subclasses:
  - `NotFoundError`;
  - `ConflictError(code)`, with codes `IMPORT_IN_PROGRESS`, `DATA_ALREADY_OWNED`, `TRIP_DATE_OVERLAP`,
    `NOT_AUTO_MEMBER`, `ALREADY_VALIDATED`, `BATCH_CHANGED`, `CATEGORY_EXISTS`;
  - `ValidationFailedError(errors: tuple[FieldError(field, code, message)])`;
  - `InvalidQueryError(code: INVALID_FILTER|INVALID_SORT, message)`;
  - `AuthenticationError`, `CsrfError`, `RateLimitedError(retry_after_s)`;
  - `FileTooLargeError`, `UnsupportedFileError`, `UnreadableWorkbookError`.
  - The existing `UnknownFormatError` is reused (code `UNKNOWN_STATEMENT_FORMAT`).

**K2 Queries** (`domain/queries.py`)
- `PageRequest(page:int=1, size:int=50)`; `Page[T](items:tuple[T,...], page, size, total)`.
- `SortSpec(fields: tuple[tuple[str, bool], ...])`. The field names are validated by each service against its own enum.
- `TransactionFilter`:
  - `date_from: date|None`, `date_to: date|None`;
  - `categories`, `bank_categories`, `custom_categories`, `category_sources`, `card_ids: tuple[PublicId]`;
  - `currencies` (may contain `UNKNOWN`), `billing_currency`, `min_amount`, `max_amount`;
  - `merchants`, `types`, `countries`, `trip_ids` (may contain `NONE`);
  - `needs_review: bool|None`, `review_reasons`.
  - `NONE`/`UNKNOWN` are module constants.
- `Dimension`, `Bucket`, `CompareMode` enums; `AnalysisQuery(filter, date_from, date_to, billing_currency, compare, group_by, bucket, series_by, include)`.

**K3 Read records** (`persistence/records.py`, additions)
- `UserRecord(user_id, public_id, email, display_name, password_hash)`; `SessionRecord(session_id, user_id, token_digest, expires_at, revoked_at)`.
- `VisibleCardRecord(card_id, public_id, issuer, last4_enc, nickname, owner_display_name|None)`.
- `TransactionViewRecord`: every field needed by `TransactionListItem`/`TransactionDetail`:
  - ids, dates, merchant, country, city;
  - `bank_category`, `custom_category`, `effective_category`, `category_source`;
  - `card: VisibleCardRecord`;
  - type, installment n/N;
  - `original_amount`, `original_currency` (API-visible), billing amount/currency, `effective_rate`, `commission`;
  - `trip: (public_id, name, membership)|None`, `notes`, `visible_review_reasons`.
- `BillingSummaryRecord(count, unknown_billing_currency_count, totals: tuple[(currency, count, net)])`; `GroupRecord(key|None, summary)`.
- `ImportSummaryRecord`, `TripRecord`, `TripSummaryRecord`, `CustomCategoryRecord`, `MappingRecord`, `RuleRecord`, `BatchRecord`, `ValidationItemRecord`.

**K4 Repository ABCs** (`persistence/interfaces.py`): exactly the methods of §B.5. `UnitOfWork` gains one attribute per
repository. `UnitOfWorkFactory.create(isolation=READ_COMMITTED|REPEATABLE_READ)`.

**K5 Ports** (`ports/`)
- `PasswordHasher.hash(pw)->str`, `.verify(hash, pw)->bool`, `.needs_rehash(hash)->bool`.
- `TokenService`:
  - `.new_session_token()->str`, `.digest(token)->bytes`;
  - `.csrf_for(digest)->str`;
  - `.question_id(issuer, hint_key: str)->str`;
  - `.throttle_key(kind, value)->bytes`.
- `CurrencyCatalogue.codes()->tuple[str,...]`.
- `WorkbookReader.open_bytes(data: bytes)->Workbook`.
- `Clock.now()->datetime` (UTC). Existing usages take `now` params; the new services take a `Clock`.

**K6 Service boundary** (`services/interfaces.py`, consumed by the API foundation and the import slice)
- `Authenticator.authenticate(token:str)->AuthContext|None`, where `AuthContext(user_id, session_id, csrf_token, user_display_name)`.
- `ReviewCounter.count_visible_unknown_currency(user_id)->int`. It is implemented in U1 (`persistence/visibility.py`-backed
  repository) and used by U4 (`/me`) and U10.

**K7 Shared SQL building blocks** (`persistence/visibility.py`, implemented by U1, signatures frozen by U0)
- `visible_transactions(user_id) -> Select` (`t.*` + `card_id`/claim join).
- `effective_category(user_id) -> Subquery(transaction_id, bank, custom, effective, source)`.
- `trip_membership(user_id) -> Subquery(transaction_id, trip_id, trip_public_id, trip_name, membership)`.
- `visible_unknown_currency() -> ColumnElement[bool]`.
- `api_original_currency() -> ColumnElement[str|None]`.
- `transaction_view_select(user_id) -> Select`, and `to_view_record(row) -> TransactionViewRecord`.

**K8 Shared API pieces** (`api/schemas/common.py` DTOs frozen by U0; `api/dependencies.py`, `api/problems.py` implemented by U2)
- DTOs: `Money`, `CardRefDto`, `CardDto`, `CategoryDto`, `TripRefDto`, `TransactionListItemDto`, `TransactionDetailDto`,
  `BillingSummaryDto`, `GroupDto`, `PageMeta`, `ProblemDto`.
- Mappers: `to_transaction_list_item(TransactionViewRecord, label_fn)`, `to_billing_summary(...)`.
- FastAPI dependencies:
  - `current_user() -> AuthContext`;
  - `csrf_protected()`;
  - `page_params() -> PageRequest`;
  - `sort_param(allowed: frozenset[str], default: str) -> SortSpec`;
  - `transaction_filter_params() -> TransactionFilter`.
- `PublicIdPath` type.

**K9 Test fakes** (`tests/fakes/`, U0)
- In-memory: `FakeAuthenticator`, `FakeReviewCounter`, `FakeTokenService`, `FakeHasher`, `FakeCatalogue`, `FakeClock`.
- `InMemoryUnitOfWork` with an attribute per repository. Each slot raises unless a test injects its own fake.
  Every slice writes in-memory fakes for its own repositories.

### B.7.4 Units

| Unit | Scope (operations / concerns) | Owns (exclusive) | Provides | Consumes | Gates |
|---|---|---|---|---|---|
| **U0 Contract freeze & skeleton** | K1–K9 signatures; stubs for every repository, service and router; DI wiring in `bootstrap.build_api_composition`; `pyproject` deps; contract-doc amendment (nullable `ImportIssue` location, ownerName fallback note, §B.9 defaults); import-graph test rules (§A) | all frozen files in B.7.2 rule 2; `persistence/providers/*.py` (stub classes only); `services/*_service.py` (stub signatures only); `api/routers/*.py` (stub routes); `docs/api-contract/*` edits; `tests/contracts/test_import_graph.py` | everything below | — | blocks all |
| **U1 Schema & persistence core** | Rewrite 0001 + ORM (B.6); `visibility.py` (K7); `ReviewCounter` repo; scoping changes in existing `repositories.py` (owners/accounts/cards `user_id`, `public_id` generation, placeholders, `source_original_currency`, dedup switch to source currency); `start_run` 23505 → `IMPORT_IN_PROGRESS`; error-name map in `db.py` | `persistence/migrations/versions/0001_initial_schema.py`, `persistence/orm/models.py`, `persistence/visibility.py`, `persistence/repositories.py`, `persistence/db.py`, `persistence/providers/review_counter.py`, `tests/storage/conftest.py`, `tests/storage/test_migrations.py`, `tests/storage/test_visibility.py` | K7 behaviour; seed fixtures `seed_user`, `seed_claimed_card`, `seed_transaction` | K1, K3, K4 | PG tests of U4–U10 |
| **U2 API foundation** | `create_app`, auth/CSRF deps (B.2), problem mapper (complete table), pagination/sort/filter parsing deps, common DTO mappers, CLI `serve`, OpenAPI conformance helper | `api/app.py`, `api/dependencies.py`, `api/problems.py`, `api/schemas/mappers.py`, `cli.py` (`serve` subcommand only), `tests/integration/api/conftest.py` (`client`, `signed_in` fixture inserting user + session through U1 seeds), `tests/integration/api/test_foundation.py`, `tests/contracts/openapi_conformance.py` | K8 behaviour; `client`/`signed_in` fixtures | K1, K2, K6 (via `FakeAuthenticator`) | endpoint tests of U4–U10 |
| **U3 Adapters** | Argon2id hasher; HKDF/HMAC token service (CSRF, question ids, throttle keys); pycountry catalogue (minus XXX/XTS); `WorkbookReader.open_bytes` | `identity/argon2_hasher.py`, `identity/tokens.py`, `identity/pycountry_catalogue.py`, `parsing/workbook_reader` adapter file(s), `tests/identity/test_*.py` | K5 implementations | K5 | — (fakes until merged) |
| **U4 Auth, profile & cards** | ops 1–5: `POST /sessions`, `DELETE /sessions/current`, `GET /me`, `GET /cards`, `PATCH /cards/{id}`; `Authenticator` impl; CLI `create-user` | `services/session_service.py`, `profile_service.py`, `card_service.py`; `persistence/providers/{users,sessions,login_throttle,cards_visible}.py`; `api/routers/{sessions,me,cards}.py`; `api/schemas/{auth,cards}.py`; `cli.py` (`create-user` only); tests under `tests/application/auth/`, `tests/storage/test_auth_providers.py`, `tests/integration/api/test_{sessions,me,cards}.py` | `Authenticator` (K6), `UserRepository.lock` (used by U8) | K1–K6, K8, `ReviewCounter` | — |
| **U5 Imports** | ops 6–7: `POST /imports`, `GET /imports`; adapt `import_service`/`identity_gate`/`audit_trail` to bytes, user scope, claims, placeholders, outcome order (§A), visible `needsReview`, nullable issue location | `services/import_service.py`, `identity_gate.py`, `audit_trail.py`, `services/import_api_service.py`, `domain/review.py`, `persistence/providers/{claims,import_history}.py`, `api/routers/imports.py`, `api/schemas/imports.py`; tests under `tests/application/imports/`, `tests/storage/test_claims*.py`, `tests/integration/api/test_imports.py`; existing import tests it must update | — | K1–K5, K7 (history), U1 scoped repos | — |
| **U6 Transactions** | ops 8, 9, 11: list (filters, sort, summary, groups), detail, filter-options | `services/transaction_service.py`, `persistence/providers/transaction_query.py`, `api/routers/transactions.py` (GET routes only), `api/schemas/transactions.py`, tests for those | `TransactionQueryRepository.visible_id/lock_visible` (used by U7, U8, U10) | K2, K3, K7, K8 | — |
| **U7 Categorization** | ops 10, 26–31: `PATCH /transactions/{id}` (override), categorization overview, custom categories, mappings, rules | `services/category_service.py`, `persistence/providers/{categories,overrides,mappings,rules}.py`, `api/routers/categorization.py`, `api/routers/transaction_overrides.py` (PATCH on the same path, separate router), `api/schemas/categorization.py`, tests | — | K2, K3, K7, K8, `visible_id` (fake until U6) | — |
| **U8 Trips** | ops 12–19 | `services/trip_service.py`, `persistence/providers/{trips,trip_tags,trip_exclusions}.py`, `api/routers/trips.py`, `api/schemas/trips.py`, tests | — | K3, K5 `CurrencyCatalogue`, K7 (`trip_membership`, `transaction_view_select`), K8, `UserRepository.lock`, `visible_id` (fakes until U4/U6) | — |
| **U9 Analysis** | op 20; `domain/calendar.py` (buckets, compare, leap day, complete months) | `domain/calendar.py`, `services/analysis_service.py`, `persistence/providers/analysis.py`, `api/routers/analysis.py`, `api/schemas/analysis.py`, tests | — | K2, K7, K8 | — |
| **U10 Currency validation** | ops 21–25 | `services/currency_validation_service.py`, `persistence/providers/currency_validation.py`, `api/routers/currency_validation.py`, `api/schemas/currency_validation.py`, tests | — | K2, K5 `CurrencyCatalogue`, K7 (`visible_unknown_currency`, `api_original_currency`), K8, `lock_visible` (fake until U6) | — |
| **U11 Integration** | wiring check (no stub left), full OpenAPI conformance (31 ops), cross-slice E2E: import → list → validate currency → undo → trip auto-membership → category precedence → `/me` counters; two-user isolation sweep across all id routes | `tests/integration/api/test_e2e_*.py`, `tests/contracts/test_openapi_conformance.py`, README API section | — | all | after U1–U10 |

**Cross-unit runtime dependencies** (each one is a frozen K4/K6 method, so the consumer uses a fake until the provider merges):

| Consumer | Needs | Provider unit | Fake used until merge |
|---|---|---|---|
| U2 auth dep | `Authenticator.authenticate` | U4 | `FakeAuthenticator` |
| U4 `/me` | `ReviewCounter.count_visible_unknown_currency` | U1 | `FakeReviewCounter` |
| U5, U8, U10, U4 | `TokenService`, `CurrencyCatalogue`, `PasswordHasher`, `WorkbookReader.open_bytes` | U3 | K9 fakes |
| U7, U8, U10 | `TransactionQueryRepository.visible_id`, `lock_visible` | U6 | in-memory fake (slice-local) |
| U8 | `UserRepository.lock` | U4 | in-memory fake |
| U5 history, U6–U10 PG tests | `visibility.py`, seeds | U1 | none: PG tests are gated on U1 |

### B.7.5 Per-unit contract details (what each unit must honour)
- **U1:**
  - The DDL must match B.6 exactly. Constraint and index **names** are part of the contract, because `db.py`
    maps 23505 by name:
    - `uq_claims_issuer_last4` → `DATA_ALREADY_OWNED`;
    - `ux_import_runs_running_sha` → `IMPORT_IN_PROGRESS`;
    - `uq_custom_categories_user_key` → `CATEGORY_EXISTS`.
  - `to_view_record` must populate every `TransactionViewRecord` field. `original_*` is null for domestic rows.
  - Seed fixtures accept `user=` so other units can create two-user scenarios.
- **U2:**
  - Problem bodies follow §1.8, with `instance` = request path.
  - Request-validation errors on the body → 422 `VALIDATION_ERROR` with `errors[].field` in camelCase;
    on query/path → 400 `INVALID_FILTER`, or `INVALID_SORT` for `sort`.
  - Bearer precedence and the cookie name `bt_session` are fixed here.
  - The `signed_in` fixture returns `(client, user, csrf)` for both bearer and cookie modes.
- **U3:**
  - `question_id` = `"iq_" + base32(HMAC(K_q, issuer|hint_key))[:26]`.
  - `csrf_for` = base64url(HMAC(K_csrf, digest)).
  - Subkeys are derived with HKDF-SHA256 over the cipher key, info strings `bt/csrf`, `bt/iq`, `bt/throttle`.
  - Argon2 uses the argon2-cffi defaults.
- **U4:** session cookie attributes (B.3 op 1); throttle 10 failures per 15 min per key; `create-user` reads the
  password from a prompt (never argv).
- **U5:**
  - Outcome order is exactly the import outcome order in §A.
  - `ImportResult` mapping: issues with a null location → `sheetPosition: null, row: null`.
  - Questions contain only the caller's cards.
  - A dry run never commits claims.
- **U6:**
  - Sort enum is exactly the openapi regex set.
  - Groups use null keys with the §1.9 labels (other dimensions: OQ-2 defaults).
  - Summary and page come from one REPEATABLE READ UoW.
- **U7:**
  - Name key = `casefold(strip(name))`.
  - Rule terms use the same normalizer as `merchant_name_norm` (existing `domain` normalizer, read-only).
  - Precedence: override → newest rule → mapping → bank.
- **U8:** overlap check under the `UserRepository.lock` row lock; auto-member predicate identical to `trip_membership`
  (K7, U1-owned). U8 reads it and does not redefine it.
- **U9:** pure calendar functions with unit tests. The SQL returns only non-empty buckets; the domain zero-fills them.
- **U10:** all writes in one UoW with `FOR UPDATE ORDER BY transaction id`. Batch members are immutable. Never touch
  `source_original_currency`.

### B.7.6 Merge protocol
1. U0 merges first (one PR, reviewed by the lead).
2. Units open PRs independently. CI runs lint, mypy, the import graph, unit tests, and, once U1 is in `main`, PG tests.
3. U1 and U2 merge before any slice PR (slices keep developing and running unit tests meanwhile). U3 can merge anytime: slices stay on K9 fakes in unit tests regardless.
4. A contract change merges as its own PR before the dependent unit PR (B.7.2 rule 2).
5. U11 starts once all slices merge and closes the plan.

## B.8 Test plan (existing layout `tests/{domain,application,storage,integration,contracts}`)
Ownership: each test below belongs to the unit that owns the code under test (B.7.4 "Owns"). Cross-slice scenarios
belong to U11. Concurrency tests belong to the unit owning the lock: U5 for claims, U8 for trips, U10 for batches.
- **Domain unit (`tests/domain`)**:
  - review predicate (domestic UNKNOWN_CURRENCY excluded);
  - calendar: Monday weeks, UTC today, complete months, previous range, yoy Feb 29;
  - sort/filter validation (INVALID_SORT, amount-without-billingCurrency, reviewReason other);
  - card label fallback;
  - question-id determinism + HMAC (the same file gives the same id; different keys give different ids).
- **Application (`tests/application`, in-memory fakes of the ABCs)**:
  - sign-in: throttle at the 10th failure for email or IP, dummy verify on an unknown email, rehash;
  - CSRF derivation;
  - import outcome order (each branch of §A), dry-run rolls back claims, CREATE_CARD placeholder reuse, USE_EXISTING_CARD for an invisible card → 404;
  - batch rules: exactly one target, dedup ids, 404 whole batch, 409 ALREADY_VALIDATED, empty filter → 201/0;
  - undo: 204 already undone, 409 after move-back/revalidation, success restores the reason;
  - move-back no-op 204;
  - trip overlap inclusive bounds; exclude NOT_AUTO_MEMBER; tag moves from another trip and clears the exclusion;
  - custom category reserved/duplicate casing;
  - rule normalization;
  - effective-category precedence.
- **Storage integration (`tests/storage`, `postgres` marker)**:
  - migration round-trip + ORM sync (existing tests updated);
  - scoped uniques (two users, same match_key OK);
  - claim uniqueness across users;
  - `fk_cards_claim` rejects a mismatched last4;
  - `ux_import_runs_running_sha` 23505 → ImportInProgressError;
  - concurrency: two threads creating overlapping trips → exactly one 409; two concurrent batches over the same row → one 409; two imports of the same card key serialize;
  - dedup uses source currency after validation (re-import inserts 0);
  - visibility: user B never sees user A rows through any repository;
  - LATERAL newest-rule selection.
- **Endpoint integration (`tests/integration/api`, FastAPI `TestClient` + real Postgres schema fixture)**: one test module per router:
  - status codes and Problem bodies per openapi;
  - bearer precedence and no cookie fallback;
  - cookie mutation without CSRF → 403;
  - 404 for foreign ids on every id route;
  - nullable fields present as `null`;
  - money/rate formatting;
  - 413/415/422 upload paths;
  - pagination out-of-range → empty.
- **Contracts (`tests/contracts`)**:
  - extend `test_import_graph.py`: `api` must not import sqlalchemy/psycopg/alembic/`persistence`; `services`/`domain` must not import fastapi/starlette/pydantic/argon2/pycountry; only bootstrap wires adapters;
  - an OpenAPI conformance test compares `app.openapi()` operationIds/status codes against `docs/api-contract/openapi.yaml` (all 31 operations).

## B.9 Defaults pending product confirmation
Each default below is implemented as stated until confirmed otherwise.
- OQ-1 `Location` headers point at undefined GETs (imports, batches). Default: emit as specified.
- OQ-2 Missing-group labels for card/currency/trip/merchant/type dimensions. Default: `key: null`, labels `No card`/`Unknown currency`/`No trip`/`Unknown merchant`/`Unknown type` pending confirmation.
- OQ-3 Trip auto membership on domestic rows that carry a stored original currency. Default: API-visible (foreign-only) original currency.
- OQ-4 Meaning of 404 for category mappings. Default: bank category absent from visible transactions (PUT) / no mapping (DELETE).
- OQ-6 Nickname `""`. Default: trimmed, empty → `null`.
- OQ-7 bucket=day range cap. Default: none.
- OQ-8 Sortable fields for `/currency-validation-queue/items`. Default: `transactionDate, merchant, country, originalAmount, billingAmount` (± prefix).
- OQ-9 Explicit batch id that is visible, not validated, and not pending (domestic). Default: 422 `NOT_PENDING` for the whole batch.
