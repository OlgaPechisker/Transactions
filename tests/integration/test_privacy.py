"""No plaintext card or account identifier reaches the database, reports or audit records."""

import re

from bank_transactions.domain.enums import IdentifierType
from bank_transactions.parsing.default_registry import build_default_registry
from bank_transactions.parsing.workbook import ExcelWorkbookReader

LAST4S = ("9462", "2023", "4396", "0538")
IDENTITY_TABLES = ("owners", "accounts", "cards", "import_runs")


def cal_account_number(path):
    workbook = ExcelWorkbookReader().open(path)
    context = build_default_registry().select(workbook).read_context(workbook)
    assert context.account is not None
    assert len(context.account.account_number) >= 6
    return context.account.account_number


def import_everything(service, examples):
    return [
        service.import_file(examples[name])
        for name in ("isracard", "max_monthly", "max_yearly", "cal")
    ]


def table_text(sql, table):
    return "\n".join(row[0] for row in sql(f'SELECT row_to_json(t)::text FROM "{table}" t'))


def test_identity_tables_and_audit_hold_no_plaintext_identifiers(service, examples, sql):
    import_everything(service, examples)
    account_number = cal_account_number(examples["cal"])

    for table in IDENTITY_TABLES:
        text = table_text(sql, table)
        for secret in (*LAST4S, account_number):
            assert not re.search(rf"(?<!\d){secret}(?!\d)", text), (table, "identifier leaked")


def test_the_account_number_appears_nowhere_in_the_database(service, examples, stored_text):
    import_everything(service, examples)
    dump = stored_text()

    assert "merchant_name_raw" in dump
    assert cal_account_number(examples["cal"]) not in dump


def test_stored_identifiers_are_ciphertext_bound_to_their_type(service, examples, sql, cipher):
    import_everything(service, examples)

    tokens = [bytes(row[0]) for row in sql("SELECT last4_enc FROM cards")]
    decrypted = {cipher.decrypt(IdentifierType.CARD_LAST4, token) for token in tokens}

    assert decrypted == set(LAST4S)
    assert all(token not in {s.encode() for s in LAST4S} for token in tokens)
    accounts = [bytes(row[0]) for row in sql("SELECT account_number_enc FROM accounts") if row[0]]
    assert accounts
    expected = cal_account_number(examples["cal"])
    assert expected in {cipher.decrypt(IdentifierType.ACCOUNT_NUMBER, t) for t in accounts}


def test_reports_name_no_identifier_or_file_location(service, examples):
    reports = import_everything(service, examples)
    account_number = cal_account_number(examples["cal"])

    for report in reports:
        text = repr(report) + " ".join(report.warnings) + " ".join(report.unresolved_cards)
        assert account_number not in text
        for last4 in LAST4S:
            assert not re.search(rf"(?<!\d){last4}(?!\d)", text)


def test_audit_rows_identify_a_file_by_hash_only(service, examples, sql):
    import_everything(service, examples)

    rows = sql("SELECT file_path, file_name, file_sha256 FROM import_runs")

    assert len(rows) == 4
    for file_path, file_name, sha in rows:
        assert re.fullmatch(r"[0-9a-f]{64}", sha)
        assert file_path == f"sha256/{sha}"
        assert file_name.startswith(sha[:12])
        assert not any(name.stem in file_path + file_name for name in examples.values())
        assert "examples" not in file_path + file_name


def test_the_unresolved_card_labels_of_a_blocked_run_hold_no_identifier(
    make_service, refusing_resolver, examples
):
    report = make_service(refusing_resolver).import_file(examples["max_monthly"])

    assert report.unresolved_cards
    for label in report.unresolved_cards:
        assert not re.search(r"\d{4}", label)
