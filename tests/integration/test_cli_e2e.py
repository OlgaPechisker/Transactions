"""The real CLI composition against a real PostgreSQL schema (settings only via the environment)."""

import io
from urllib.parse import quote

import pytest
from tests.storage.seeds import DEFAULT_EMAIL, create_user

from bank_transactions.cli import EXIT_NEEDS_INPUT, EXIT_OK, main

KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
USER = ("--user", DEFAULT_EMAIL)


@pytest.fixture
def environ(postgres_dsn, migrated_schema):
    separator = "&" if "?" in postgres_dsn else "?"
    options = quote(f"-csearch_path={migrated_schema}")
    return {"BT_DSN": f"{postgres_dsn}{separator}options={options}", "BT_CIPHER_KEY": KEY}


@pytest.fixture
def run(environ, user):
    def invoke(*argv, answers=(), env=None):
        out, err = io.StringIO(), io.StringIO()
        replies = iter(answers)
        code = main(
            list(argv),
            environ={**environ, **(env or {})},
            stdout=out,
            stderr=err,
            read=lambda prompt: next(replies),
        )
        return code, out.getvalue(), err.getvalue()

    return invoke


def test_detect_names_the_issuer_without_any_database_or_key(examples):
    out, err = io.StringIO(), io.StringIO()

    code = main(["detect", str(examples["max_monthly"])], environ={}, stdout=out, stderr=err)

    assert (code, "MAX" in out.getvalue(), err.getvalue()) == (EXIT_OK, True, "")


def test_interactive_import_prompts_then_stores_the_statement(run, examples, scalar):
    code, out, err = run("import", str(examples["isracard"]), *USER, answers=["", "", ""])

    assert code == EXIT_OK, err
    assert "4 imported" in out
    assert scalar("SELECT count(*) FROM transactions") == 4


def test_imported_cards_are_claimed_by_the_named_user(run, examples, scalar):
    run("import", str(examples["isracard"]), "--user", DEFAULT_EMAIL.upper(), answers=["", "", ""])

    assert scalar("SELECT count(*) FROM card_ownership_claims") >= 1
    assert scalar("SELECT count(DISTINCT user_id) FROM card_ownership_claims") == 1
    assert scalar("SELECT count(*) FROM cards WHERE claim_id IS NULL") == 0


def test_a_card_claimed_by_another_user_fails_the_import_without_naming_anyone(
    run, factory, examples, scalar
):
    run("import", str(examples["isracard"]), *USER, answers=["", "", ""])
    create_user(factory, "other.user@example.com", "Other User")

    code, out, err = run(
        "import",
        str(examples["isracard"]),
        "--user",
        "other.user@example.com",
        answers=["", "", ""],
    )

    assert code == 1
    assert "another account" in err
    assert DEFAULT_EMAIL not in out + err
    assert scalar("SELECT count(*) FROM transactions") == 4


def test_an_unknown_user_is_a_usage_error_and_nothing_is_written(run, examples, scalar):
    code, _, err = run("import", str(examples["isracard"]), "--user", "nobody@example.com")

    assert code == 2
    assert "no user" in err
    assert scalar("SELECT count(*) FROM import_runs") == 0


def test_dry_run_through_the_cli_leaves_the_database_empty(run, examples, scalar):
    code, out, _ = run(
        "import", str(examples["isracard"]), *USER, "--dry-run", answers=["", "", ""]
    )

    assert code == EXIT_OK
    assert "DRY RUN" in out
    assert scalar("SELECT count(*) FROM transactions") == 0
    assert scalar("SELECT count(*) FROM cards") == 0


def test_non_interactive_import_with_an_unknown_card_exits_with_needs_input(run, examples, scalar):
    code, out, _ = run("import", str(examples["max_monthly"]), *USER, "--non-interactive")

    assert code == EXIT_NEEDS_INPUT
    assert "rerun" in out.lower() or "interactive" in out.lower()
    assert scalar("SELECT count(*) FROM transactions") == 0
    assert scalar("SELECT count(*) FROM cards") == 0


def test_import_without_the_cipher_key_is_a_usage_error_and_touches_nothing(
    environ, examples, scalar
):
    out, err = io.StringIO(), io.StringIO()
    without_key = {"BT_DSN": environ["BT_DSN"]}

    code = main(
        ["import", str(examples["isracard"]), *USER], environ=without_key, stdout=out, stderr=err
    )

    assert code == 2
    assert "BT_CIPHER_KEY" in err.getvalue()
    assert scalar("SELECT count(*) FROM import_runs") == 0


def test_init_db_needs_only_the_dsn_and_is_repeatable(postgres_dsn, schema_name, sql):
    separator = "&" if "?" in postgres_dsn else "?"
    dsn = f"{postgres_dsn}{separator}options={quote(f'-csearch_path={schema_name}')}"
    outputs = []
    for _ in range(2):
        out, err = io.StringIO(), io.StringIO()
        code = main(["init-db"], environ={"BT_DSN": dsn}, stdout=out, stderr=err)
        outputs.append((code, err.getvalue()))

    assert outputs == [(EXIT_OK, ""), (EXIT_OK, "")]
    assert sql("SELECT to_regclass(:name)", name=f"{schema_name}.transactions")[0][0] is not None
