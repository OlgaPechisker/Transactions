"""Re-importing and overlapping imports against a real PostgreSQL schema."""

from decimal import Decimal

from bank_transactions.domain.enums import ImportStatus

MONTHLY, YEARLY = 158, 2169


def snapshot(sql, run_id):
    return sql(
        "SELECT id, card_id, transaction_date, billing_amount, merchant_name_raw, import_run_id "
        "FROM transactions WHERE import_run_id = :run ORDER BY id",
        run=run_id,
    )


def test_importing_the_same_file_twice_adds_nothing(service, examples, scalar, answering_resolver):
    first = service.import_file(examples["max_monthly"])
    second = service.import_file(examples["max_monthly"])

    assert (first.rows_imported, first.rows_duplicate) == (MONTHLY, 0)
    assert (second.status, second.rows_imported, second.rows_duplicate) == (
        ImportStatus.SUCCESS,
        0,
        MONTHLY,
    )
    assert scalar("SELECT count(*) FROM transactions") == MONTHLY
    assert scalar("SELECT count(*) FROM cards") == 2
    assert len(answering_resolver.questions) == 2


def test_the_yearly_file_after_the_monthly_one_adds_only_the_new_rows(
    service, examples, scalar, sql
):
    first = service.import_file(examples["max_monthly"])
    before = snapshot(sql, first.run_id)

    second = service.import_file(examples["max_yearly"])

    assert (second.rows_imported, second.rows_duplicate) == (YEARLY - MONTHLY, MONTHLY)
    assert second.warnings == ()
    assert scalar("SELECT count(*) FROM transactions") == YEARLY
    assert scalar("SELECT sum(billing_amount) FROM transactions") == Decimal("216563.68")
    assert scalar("SELECT count(*) FROM installment_plans") == 13
    assert scalar("SELECT count(*) FROM transactions WHERE installment_plan_id IS NOT NULL") == 44
    assert snapshot(sql, first.run_id) == before
    assert scalar("SELECT count(*) FROM transactions WHERE import_run_id = :r", r=first.run_id) == (
        MONTHLY
    )


def test_the_monthly_file_after_the_yearly_one_is_entirely_duplicate(service, examples, scalar):
    service.import_file(examples["max_yearly"])

    second = service.import_file(examples["max_monthly"])

    assert (second.rows_imported, second.rows_duplicate, second.rows_total) == (0, MONTHLY, MONTHLY)
    assert second.warnings == ()
    assert scalar("SELECT count(*) FROM transactions") == YEARLY
    assert scalar("SELECT count(*) FROM installment_plans") == 13


def test_identical_repeated_purchases_in_one_file_all_survive(service, examples, sql):
    service.import_file(examples["max_yearly"])

    repeated = sql(
        "SELECT count(*) FROM (SELECT card_id, transaction_date, merchant_name_raw, "
        "billing_amount FROM transactions GROUP BY 1, 2, 3, 4 HAVING count(*) > 1) g"
    )

    assert repeated[0][0] > 0


def test_each_run_leaves_its_own_audit_record(service, examples, sql):
    first = service.import_file(examples["max_monthly"])
    second = service.import_file(examples["max_yearly"])

    runs = sql(
        "SELECT id, status, rows_total, rows_imported, rows_duplicate, issuer_code "
        "FROM import_runs ORDER BY id"
    )

    assert runs == [
        (first.run_id, "SUCCESS", MONTHLY, MONTHLY, 0, "MAX"),
        (second.run_id, "SUCCESS", YEARLY, YEARLY - MONTHLY, MONTHLY, "MAX"),
    ]


def test_isracard_and_cal_reimports_are_duplicates_too(service, examples, scalar):
    service.import_file(examples["isracard"])
    service.import_file(examples["cal"])

    again_isracard = service.import_file(examples["isracard"])
    again_cal = service.import_file(examples["cal"])

    assert (again_isracard.rows_imported, again_isracard.rows_duplicate) == (0, 4)
    assert (again_cal.rows_imported, again_cal.rows_duplicate) == (0, 8)
    assert scalar("SELECT count(*) FROM transactions") == 12
    assert scalar("SELECT count(*) FROM installment_plans") == 1
