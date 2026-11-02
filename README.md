# bank-transactions

Parses Israeli credit-card statement workbooks (Isracard, Max, Cal) and stores normalized
transactions in PostgreSQL. Card last-4 digits and bank account numbers are encrypted before they
reach the database; the library imports, deduplicates and audits each file.

See [docs/importer-design.md](docs/importer-design.md) for the design and behavior rules,
[docs/sequence-diagram.md](docs/sequence-diagram.md) for the diagrams, and
[docs/api-design-phase-1.md](docs/api-design-phase-1.md) for the planned REST API (not yet implemented;
detailed plan in [docs/api-design-phase-2.md](docs/api-design-phase-2.md), wire contract in
[docs/api-contract/](docs/api-contract/)).

## Install

Python 3.12 or newer and PostgreSQL 15 or newer (the schema uses `NULLS NOT DISTINCT`).

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Settings

Settings come from environment variables; `--dsn` overrides `BT_DSN` for a single command.

| Variable | Needed by | Meaning |
| --- | --- | --- |
| `BT_DSN` | `import`, `init-db` | SQLAlchemy URL, for example `postgresql+psycopg://user:password@host:5432/db` |
| `BT_CIPHER_KEY` | `import` | Base64 of 32, 48 or 64 random bytes (AES-SIV key). Keep it outside source control; losing it makes stored identifiers unreadable |
| `BT_TWO_DIGIT_YEARS_BACK` | optional | How many years back a two-digit year may reach (0–99, default 80) |
| `BT_FEE_KEYWORDS` | optional | Comma-separated issuer words that mark a foreign-currency fee row; replaces the defaults |
| `BT_FEE_WINDOW_DAYS` | optional | Days a fee row may differ from its transaction date (default 3) |
| `BT_MIN_CONFIDENCE` | optional | Minimum parser confidence to accept a workbook (default 0.5) |

Generate a key once:

```powershell
.\.venv\Scripts\python.exe -c "import base64, os; print(base64.b64encode(os.urandom(64)).decode())"
```

## Commands

```powershell
bank-transactions detect statement.xlsx            # names the issuer; needs no DSN or key
bank-transactions init-db                          # creates or upgrades the schema; needs the DSN only
bank-transactions create-user --email dana@example.com --name "Dana Levi"  # prompts twice for the password (never an argument); needs the DSN and key
bank-transactions import statement.xlsx --user dana@example.com            # prompts when an identity must be chosen
bank-transactions import statement.xlsx --user dana@example.com --dry-run  # runs every step, then rolls the data back
bank-transactions import statement.xlsx --user dana@example.com --non-interactive
```

`python -m bank_transactions ...` is equivalent.

`create-user` is the only way to add a user (the API has no registration endpoint). The password is
read from a hidden prompt, asked twice, and is never accepted as an argument. The email is compared
after trimming and case-folding; a duplicate or invalid email, or mismatched passwords, is a usage
error (exit 2) and nothing is written.

`import` requires `--user <email>`: the email (compared after trimming and case-folding) of an
existing user, who becomes the owner of every card, owner and account the import creates. An unknown
email is a usage error (exit 2) and nothing is written. A card already linked to another user
fails the import (exit 1) without saying whose it is.

An import reports the rows read, imported, duplicated, skipped and needing review, then any
warnings and row issues. The report never contains card or account numbers; only an interactive
identity prompt shows a card's masked last 4 (see [Privacy](#privacy)).

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | The command failed |
| 2 | Usage or configuration error (including a missing DSN or key, or an unknown `--user`) |
| 3 | An identity must be chosen; `--non-interactive` never guesses, so nothing was imported |
| 4 | Imported, but some rows had errors (`PARTIAL`) |
| 130 | Interrupted; nothing was reported as completed |

### Dry run

`--dry-run` runs the whole flow, including the identity questions, and rolls back every domain
change, including identities created during the run. The report shows what would have been
imported. The audit record is still written, marked `DRY RUN`, with `rows_imported` of 0 so it never
claims rows that were not committed.

### Re-importing

Importing the same file twice inserts nothing the second time. A row with an issuer reference is
matched by card, transaction date, issuer reference and installment number (`IssuerReferenceKey`;
a row that is not an installment has no installment number, and that counts as one value); rows
without a reference are matched by how many identical rows are already stored. A yearly Max export that contains an already-imported monthly export adds
only the rows that are new. Stored rows are never updated or deleted.

## Privacy

* Card last-4 and account numbers are stored only as deterministic AES-SIV ciphertext, bound to the
  identifier type; equal values encrypt equally so they can be looked up.
* Owner, account and card labels may not contain a known identifier.
* Import reports, audit records, logs and failure summaries carry no plaintext identifiers, DSN, key
  or file path. Audit rows identify a file by its SHA-256 hash.
* The one deliberate exception is the interactive identity prompt: so that you can make an explicit
  choice it shows the issuer and the masked last 4 (`••••4396`) of the card in question and of the
  existing cards offered. Full card and account numbers are never shown, and `--non-interactive`
  prints no prompt.
* Merchant text and notes are stored exactly as read.

## Using the library

```python
import os
from pathlib import Path

from bank_transactions.bootstrap import build_import_service
from bank_transactions.config import Settings

settings = Settings.from_env(os.environ)
service = build_import_service(settings, interactive=False)
report = service.import_file(Path("statement.xlsx"), user_id=user_id, dry_run=True)
```

`StatementImportService` depends only on abstractions (`WorkbookReader`, `ParserSelector`,
`IdentifierProtector`, `IdentityResolver`, `UnitOfWorkFactory`). Importing it loads neither
SQLAlchemy, openpyxl nor cryptography; `bootstrap.py` is the only module that wires the concrete
adapters.

### Adding an issuer

1. Add a parser under `src/bank_transactions/parsing/banks/` that subclasses the shared tabular
   parser (or implements `StatementParser`), supplying only the layout: how to recognise the
   workbook, where the header is, and which columns hold which fields.
2. Add its `IssuerCode` value and register the parser in `parsing/default_registry.py`.
3. Add a test with a small synthetic workbook in `tests/parsing/` and, if you have a real export, a
   golden test.

The service has no issuer-specific code, so nothing else changes.

### Implementing an `IdentityResolver`

An `IdentityResolver` answers `IdentityQuestion`s: which owner, account and card a statement card
belongs to. Implement `complete(question) -> IdentitySelection` from
`bank_transactions.identity.interfaces`. The question carries the identifier hints, the candidate
cards and the owner and account choices; return `UseCard`, or `CreateCard` with `UseOwner` /
`CreateOwner` and `UseAccount` / `CreateAccount` choices. Raise `IdentityUnresolvedError` to leave
the card unresolved. The resolver never touches the database, and the service validates every
answer before it is applied. Pass the resolver to `StatementImportService`; the CLI uses
`CliIdentityResolver` for prompts and `NonInteractiveResolver` for `--non-interactive`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not postgres"   # no database needed
.\.venv\Scripts\python.exe -m pytest                       # also runs the PostgreSQL tests
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy
```

PostgreSQL tests need `BT_TEST_DSN` pointing at a disposable PostgreSQL 15+ server, for example
`postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres`. Each test creates and drops its
own schema and never touches `public`. Without the variable these tests fail on purpose instead of
being skipped silently; use `-m "not postgres"` to leave them out.
