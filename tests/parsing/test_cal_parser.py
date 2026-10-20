from datetime import date, datetime
from decimal import Decimal

import pytest

from bank_transactions.domain.enums import (
    IssuerCode,
    IssueSeverity,
    ReviewReason,
    RowIssueCode,
    TransactionType,
)
from bank_transactions.domain.errors import ParseError
from bank_transactions.domain.issues import SourceLocation
from bank_transactions.domain.money import ILS, USD, Money
from bank_transactions.domain.workbook import SheetView, WorkbookView
from bank_transactions.parsing.banks.cal_visa import CalParser


def read(workbook, parser=None):
    parser = parser or CalParser()
    return parser.read_transactions(workbook, parser.read_context(workbook))


def totals(result_totals):
    return {money.currency.code: money.amount for money in result_totals.amounts}


# --- the real example -------------------------------------------------------------------


def test_example_is_recognised_with_full_confidence_and_other_issuers_score_zero(examples):
    parser = CalParser()

    assert parser.sniff(examples["cal"]) == 1.0
    for other in ("isracard", "max_monthly", "max_yearly"):
        assert parser.sniff(examples[other]) == 0.0


def test_example_context_has_the_card_account_charge_date_and_declared_total(examples):
    context = CalParser().read_context(examples["cal"])

    assert context.issuer_code is IssuerCode.CAL
    (card,) = context.cards
    assert card.label == "ויזה זהב עסקי"
    assert card.key.last4 not in card.label
    assert context.account.bank_code == "LEUMI"
    assert context.account.account_number.isdigit()
    assert context.charge_date == date(2026, 9, 10)
    assert context.owner is None and context.period is None
    assert totals(context.declared_totals) == {"ILS": Decimal("645.13")}


def test_example_has_eight_charged_and_thirteen_pending_rows(examples):
    workbook = examples["cal"]
    parser = CalParser()
    context = parser.read_context(workbook)

    result = parser.read_transactions(workbook, context)

    assert len(result.transactions) == 8
    assert result.rows_seen == 21
    assert len(result.issues) == 13
    assert {i.code for i in result.issues} == {RowIssueCode.PENDING_TRANSACTION}
    assert {i.severity for i in result.issues} == {IssueSeverity.INFO}
    assert totals(result.calculated_totals) == {"ILS": Decimal("645.13")}
    assert result.calculated_totals == context.declared_totals


def test_example_pending_rows_are_the_ones_without_a_charge_amount(examples):
    result = read(examples["cal"])

    pending = [i.location.row for i in result.issues]
    assert pending == [5, 6, 7, 8, 9, 17, 18, 19, 20, 21, 22, 23, 24]
    assert {t.location for t in result.transactions}.isdisjoint(i.location for i in result.issues)
    assert all(i.location.sheet_position == 1 for i in result.issues)


def test_example_charged_rows_take_the_banner_charge_date_and_ils(examples):
    workbook = examples["cal"]
    context = CalParser().read_context(workbook)

    result = read(workbook)

    assert {t.charge_date for t in result.transactions} == {context.charge_date}
    assert {t.billing.currency for t in result.transactions} == {ILS}
    assert all(t.channel is None and t.fx is None for t in result.transactions)
    assert all(t.bank_category_raw for t in result.transactions)


def test_example_partial_charge_row_keeps_both_source_amounts(examples):
    result = read(examples["cal"])

    row = next(t for t in result.transactions if t.location.row == 16)

    assert row.original == Money(Decimal("62"), ILS)
    assert row.billing == Money(Decimal("17.13"), ILS)
    assert row.transaction_type is TransactionType.REGULAR


def test_example_installment_charge_separates_the_monthly_original_from_the_plan_total(examples):
    result = read(examples["cal"])

    row = next(t for t in result.transactions if t.location.row == 25)

    assert row.transaction_type is TransactionType.INSTALLMENTS
    assert (row.installment.installment_number, row.installment.total_installments) == (4, 5)
    assert row.installment.plan_total == Money(Decimal("1680"), ILS)
    assert row.original == Money(Decimal("336"), ILS)
    assert row.billing == Money(Decimal("336"), ILS)
    assert row.transaction_date == date(2026, 5, 11)
    assert row.installment_plan.total == Money(Decimal("1680"), ILS)
    assert row.installment_plan.purchase_date == row.transaction_date
    assert result.installment_plans == (row.installment_plan,)
    assert row.notes == "תשלום 4 מתוך 5"


# --- synthetic statements -----------------------------------------------------------------


def test_the_currency_comes_from_the_banner(cal_workbook, cal_row):
    workbook = cal_workbook([cal_row()], banner="עסקאות לחיוב ב-10/09/2026: 50.00 $")

    (transaction,) = read(workbook).transactions

    assert transaction.billing == Money(Decimal("50.0"), USD)
    assert transaction.fx is None


def test_category_and_notes_are_kept_as_the_source_wrote_them(cal_workbook, cal_row):
    row = cal_row(category=" ענף  מיוחד ", notes="הערה  חופשית")

    (transaction,) = read(cal_workbook([row])).transactions

    assert transaction.bank_category_raw == " ענף  מיוחד "
    assert transaction.notes == "הערה  חופשית"


def test_transaction_types_map_and_an_absent_type_column_means_regular(cal_workbook, cal_row):
    typed = cal_workbook([cal_row(type="רגילה")])
    sheet = typed.sheets[0]
    cut = lambda row: row[:4] + row[5:]  # noqa: E731 - drops the type column
    without_type = WorkbookView(
        (
            SheetView(
                sheet.name,
                (*sheet.rows[:3], *(cut(row) for row in sheet.rows[3:5]), *sheet.rows[5:]),
            ),
        )
    )

    (transaction,) = read(without_type).transactions

    assert transaction.transaction_type is TransactionType.REGULAR
    assert read(typed).transactions[0].transaction_type is TransactionType.REGULAR


def test_an_unknown_or_blank_type_in_a_present_column_is_reported_and_the_row_skipped(
    cal_workbook, cal_row
):
    rows = [cal_row(type="סוג חדש"), cal_row(type=None), cal_row(type="תשלומים", notes="x")]

    result = read(cal_workbook(rows))

    assert [(i.code, i.severity) for i in result.issues[:2]] == [
        (RowIssueCode.MALFORMED_ROW, IssueSeverity.ERROR),
        (RowIssueCode.MISSING_REQUIRED_VALUE, IssueSeverity.ERROR),
    ]
    assert all("חדש" not in i.message for i in result.issues)
    assert len(result.transactions) == 1
    assert result.transactions[0].transaction_type is TransactionType.INSTALLMENTS


def test_a_pending_cal_row_is_pending_even_when_its_type_is_blank(cal_workbook, cal_row):
    result = read(cal_workbook([cal_row(type=None, charge=None)]))

    assert [i.code for i in result.issues] == [RowIssueCode.PENDING_TRANSACTION]


def test_a_blank_charge_amount_is_pending_even_with_a_charge_date_banner(cal_workbook, cal_row):
    result = read(cal_workbook([cal_row(charge=None), cal_row(merchant="B")]))

    assert [t.merchant_name_raw for t in result.transactions] == ["B"]
    assert result.rows_seen == 2
    (issue,) = result.issues
    assert issue.location == SourceLocation(1, 5)
    assert issue.code is RowIssueCode.PENDING_TRANSACTION


def test_zero_and_negative_charges_keep_their_sign(cal_workbook, cal_row):
    rows = [cal_row(charge=0, amount=10), cal_row(charge=-20.5, amount=-20.5)]

    zero, negative = read(cal_workbook(rows)).transactions

    assert zero.billing.amount == 0
    assert negative.billing.amount == Decimal("-20.5")
    assert negative.original.amount == Decimal("-20.5")


def test_bad_values_are_reported_per_row_without_cell_data(cal_workbook, cal_row):
    rows = [
        cal_row(charge="abc"),
        cal_row(date="not-a-date"),
        cal_row(date=None),
        cal_row(merchant=None),
        cal_row(amount="12x"),
    ]

    result = read(cal_workbook(rows))

    assert result.transactions == ()
    assert [i.code for i in result.issues] == [
        RowIssueCode.INVALID_AMOUNT,
        RowIssueCode.INVALID_DATE,
        RowIssueCode.MISSING_REQUIRED_VALUE,
        RowIssueCode.MISSING_REQUIRED_VALUE,
        RowIssueCode.INVALID_AMOUNT,
    ]
    assert all("abc" not in i.message and "not-a-date" not in i.message for i in result.issues)


def test_text_dates_are_accepted(cal_workbook, cal_row):
    (transaction,) = read(cal_workbook([cal_row(date="03/09/2026")])).transactions

    assert transaction.transaction_date == date(2026, 9, 3)


def test_identical_rows_are_not_deduplicated_and_blank_and_footer_rows_are_ignored(
    cal_workbook, cal_row
):
    result = read(cal_workbook([cal_row(), cal_row()]))

    assert len(result.transactions) == 2
    assert result.rows_seen == 2
    assert totals(result.calculated_totals) == {"ILS": Decimal("100.0")}


def test_installment_rows_of_one_plan_share_it_and_conflicts_become_ambiguous(
    cal_workbook, cal_row
):
    plan = {"amount": 900, "charge": 300, "type": "תשלומים", "date": datetime(2026, 7, 1)}
    rows = [
        cal_row(**plan, notes="תשלום 1 מתוך 3"),
        cal_row(**plan, notes="תשלום 2 מתוך 3"),
        cal_row(**plan, merchant="OTHER", notes="תשלום 1 מתוך 3"),
        cal_row(**plan, merchant="OTHER", notes="תשלום 1 מתוך 3"),
        cal_row(**plan, merchant="NOTE", notes="ללא מספר"),
    ]

    first, second, other_a, other_b, unnumbered = read(cal_workbook(rows)).transactions

    assert first.installment_plan is not None
    assert first.installment_plan == second.installment_plan
    assert other_a.installment_plan is None and other_b.installment_plan is None
    assert ReviewReason.AMBIGUOUS_INSTALLMENT in other_a.review_reasons
    assert ReviewReason.AMBIGUOUS_INSTALLMENT in unnumbered.review_reasons
    assert unnumbered.installment is None
    assert unnumbered.billing.amount == 300


# --- structural failures ------------------------------------------------------------------


def test_a_workbook_without_sheets_is_a_structural_error():
    with pytest.raises(ParseError):
        CalParser().read_context(WorkbookView(()))


def test_a_missing_header_or_column_is_a_structural_error(cal_workbook):
    with pytest.raises(ParseError, match="header"):
        read(cal_workbook(header=(None,) * 7))
    with pytest.raises(ParseError, match="required columns missing"):
        read(cal_workbook(header=("תאריך עסקה", "שם בית עסק") + (None,) * 5))


def test_a_missing_title_or_banner_is_a_structural_error(cal_workbook):
    with pytest.raises(ParseError):
        CalParser().read_context(cal_workbook(title="כותרת אחרת"))
    with pytest.raises(ParseError):
        CalParser().read_context(cal_workbook(banner="ללא חיוב"))


def test_an_unrelated_workbook_scores_zero():
    assert CalParser().sniff(WorkbookView((SheetView("x", ((None,),)),))) == 0.0


def test_the_account_is_canonical_digits_and_never_in_issue_messages(cal_workbook, cal_row):
    workbook = cal_workbook([cal_row(charge=None), cal_row(date=None)])
    context = CalParser().read_context(workbook)

    result = CalParser().read_transactions(workbook, context)

    assert context.account.account_number == "1234567890"
    assert context.account.bank_code == "LEUMI"
    text = " ".join(i.message for i in result.issues)
    assert "1234567890" not in text and "4321" not in text
