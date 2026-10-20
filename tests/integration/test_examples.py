"""Each unmodified example workbook, imported end to end into a real PostgreSQL schema."""

from decimal import Decimal

from bank_transactions.domain.enums import ImportStatus, IssuerCode

D = Decimal


def test_isracard_statement_is_imported_with_its_declared_totals(service, examples, scalar):
    report = service.import_file(examples["isracard"])

    assert (report.status, report.issuer_code) == (ImportStatus.SUCCESS, IssuerCode.ISRACARD)
    assert (report.rows_total, report.rows_imported, report.rows_duplicate) == (4, 4, 0)
    assert report.rows_skipped == 0
    assert not any("totals" in warning.lower() for warning in report.warnings)
    assert scalar(
        "SELECT sum(billing_amount) FROM transactions WHERE billing_currency = 'ILS'"
    ) == D("1337.60")
    assert scalar(
        "SELECT sum(billing_amount) FROM transactions WHERE billing_currency = 'USD'"
    ) == D("26.39")


def test_isracard_zero_charge_discount_row_is_kept(service, examples, scalar):
    service.import_file(examples["isracard"])

    assert scalar("SELECT count(*) FROM transactions WHERE billing_amount = 0") >= 1


def test_monthly_max_statement_imports_both_cards_with_foreign_detail(
    service, examples, scalar, answering_resolver
):
    report = service.import_file(examples["max_monthly"])

    assert (report.status, report.issuer_code) == (ImportStatus.SUCCESS, IssuerCode.MAX)
    assert (report.rows_total, report.rows_imported) == (158, 158)
    assert (report.rows_duplicate, report.rows_skipped) == (0, 0)
    assert len(answering_resolver.questions) == 2
    assert scalar("SELECT count(*) FROM cards") == 2
    assert scalar("SELECT sum(billing_amount) FROM transactions") == D("20839.67")
    assert scalar("SELECT count(*) FROM foreign_transaction_details") == 16
    assert scalar(
        "SELECT sum(t.billing_amount) FROM transactions t "
        "JOIN foreign_transaction_details f ON f.transaction_id = t.id"
    ) == D("490.66")
    assert scalar("SELECT count(*) FROM installment_plans") == 7


def test_yearly_max_statement_matches_the_workbook_goldens(service, examples, scalar):
    report = service.import_file(examples["max_yearly"])

    assert (report.status, report.rows_total, report.rows_imported) == (
        ImportStatus.SUCCESS,
        2169,
        2169,
    )
    assert scalar("SELECT sum(billing_amount) FROM transactions") == D("216563.68")
    assert scalar("SELECT count(*) FROM foreign_transaction_details") == 607
    assert (
        scalar("SELECT count(*) FROM transaction_review_reasons WHERE reason = 'UNKNOWN_CURRENCY'")
        == 374
    )
    assert scalar("SELECT count(*) FROM transactions WHERE billing_amount < 0") == 14
    assert scalar("SELECT count(*) FROM installment_plans") == 13
    assert scalar("SELECT count(*) FROM transactions WHERE installment_plan_id IS NOT NULL") == 44


def test_yearly_domestic_euro_row_carries_fx_detail_with_its_rates(service, examples, sql):
    service.import_file(examples["max_yearly"])

    rows = sql(
        "SELECT t.original_currency, f.stated_fx_rate, f.effective_fx_rate "
        "FROM transactions t JOIN foreign_transaction_details f ON f.transaction_id = t.id "
        "WHERE t.original_currency = 'EUR' AND t.billing_currency = 'ILS' "
        "AND f.effective_fx_rate > 0 LIMIT 1"
    )

    assert rows
    assert rows[0][2] > 0


def test_yearly_refunds_keep_the_source_signs(service, examples, sql):
    service.import_file(examples["max_yearly"])

    refunds = sql(
        "SELECT billing_amount, original_amount FROM transactions WHERE billing_amount < 0"
    )

    assert len(refunds) == 14
    assert all(original is None or original > 0 for _, original in refunds)


def test_cal_statement_imports_charged_rows_and_skips_pending_ones(service, examples, scalar):
    report = service.import_file(examples["cal"])

    assert (report.status, report.issuer_code) == (ImportStatus.SUCCESS, IssuerCode.CAL)
    assert (report.rows_total, report.rows_imported, report.rows_skipped) == (21, 8, 13)
    assert scalar("SELECT sum(billing_amount) FROM transactions") == D("645.13")


def test_cal_installment_keeps_the_plan_total_and_the_current_charge(service, examples, sql):
    service.import_file(examples["cal"])

    plan = sql("SELECT id, total_amount, total_installments FROM installment_plans")
    linked = sql(
        "SELECT billing_amount, installment_number FROM transactions "
        "WHERE installment_plan_id IS NOT NULL"
    )

    assert [(row[1], row[2]) for row in plan] == [(D("1680"), 5)]
    assert linked == [(D("336"), 4)]
