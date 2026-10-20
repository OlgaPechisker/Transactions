from datetime import date
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
from bank_transactions.domain.money import ILS, USD, Money, effective_fx_rate
from bank_transactions.domain.workbook import SheetView, WorkbookView
from bank_transactions.parsing.banks.isracard import IsracardParser

SOURCE_TOTALS = {"ILS": Decimal("1337.60"), "USD": Decimal("26.39")}


def read(workbook, parser=None):
    parser = parser or IsracardParser()
    return parser.read_transactions(workbook, parser.read_context(workbook))


def totals(result_totals):
    return {money.currency.code: money.amount for money in result_totals.amounts}


def issue_codes(result):
    return [issue.code for issue in result.issues]


# --- the real example -------------------------------------------------------------------


def test_example_is_recognised_with_full_confidence_and_other_issuers_score_zero(examples):
    parser = IsracardParser()

    assert parser.sniff(examples["isracard"]) == 1.0
    for other in ("cal", "max_monthly", "max_yearly"):
        assert parser.sniff(examples[other]) == 0.0


def test_example_context_describes_the_card_statement_and_declared_totals(examples):
    context = IsracardParser().read_context(examples["isracard"])

    assert context.issuer_code is IssuerCode.ISRACARD
    (card,) = context.cards
    assert card.key.issuer_code is IssuerCode.ISRACARD
    assert card.label == "פלטינה מסטרקארד"
    assert card.key.last4 not in card.label
    assert context.owner is not None
    assert context.charge_date == date(2026, 2, 10)
    assert (context.period.start, context.period.end) == (date(2026, 2, 1), date(2026, 2, 28))
    assert totals(context.declared_totals) == SOURCE_TOTALS


def test_example_yields_the_four_source_charges_matching_the_declared_totals(examples):
    workbook = examples["isracard"]
    parser = IsracardParser()
    context = parser.read_context(workbook)

    result = parser.read_transactions(workbook, context)

    assert len(result.transactions) == 4
    assert result.rows_seen == 4
    assert result.issues == ()
    assert totals(result.calculated_totals) == SOURCE_TOTALS
    assert result.calculated_totals == context.declared_totals
    assert {t.card_key for t in result.transactions} == {context.cards[0].key}
    assert {t.charge_date for t in result.transactions} == {context.charge_date}


def test_example_rows_keep_source_text_and_voucher_references(examples):
    result = read(examples["isracard"])
    first, second, discount, abroad = result.transactions

    assert first.transaction_date == date(2026, 2, 9)
    assert first.billing == Money(Decimal("1137.5"), ILS)
    assert first.original == Money(Decimal("1137.5"), ILS)
    assert first.issuer_reference == "839176964"
    assert second.billing.amount == Decimal("200.1")
    assert discount.billing == Money(Decimal("0"), ILS)
    assert discount.original == Money(Decimal("24"), ILS)
    assert discount.notes == "הנחה ₪24.00"
    assert abroad.merchant_name_raw == examples["isracard"].sheets[0].cell(17, 2)
    assert all(t.transaction_type is TransactionType.REGULAR for t in result.transactions)
    assert all(t.channel is None and t.bank_category_raw is None for t in result.transactions)


def test_example_foreign_site_row_carries_fx_detail_with_unit_rate_and_no_commission(examples):
    *domestic, abroad = read(examples["isracard"]).transactions

    assert all(t.fx is None for t in domestic)
    assert abroad.is_foreign
    assert abroad.billing == Money(Decimal("26.39"), USD)
    assert abroad.fx.effective_fx_rate == 1
    assert abroad.fx.commission_amount == 0
    assert abroad.fx.stated_fx_rate is None
    assert abroad.notes == 'אתר חו"ל'


def test_example_locations_are_safe_sheet_positions_and_rows(examples):
    result = read(examples["isracard"])

    assert [t.location for t in result.transactions] == [
        SourceLocation(1, row) for row in (14, 15, 16, 17)
    ]


# --- statement structure ------------------------------------------------------------------


def test_the_two_digit_year_follows_the_statement_month(isracard_workbook, isracard_row):
    workbook = isracard_workbook([isracard_row(date="31.12.25")], month="ינואר 2026")

    (transaction,) = read(workbook).transactions

    assert transaction.transaction_date == date(2025, 12, 31)


def test_a_charge_date_in_an_earlier_month_belongs_to_the_next_year(isracard_workbook):
    context = IsracardParser().read_context(
        isracard_workbook(month="דצמבר 2025", charge="לחיוב ב-02.01")
    )

    assert context.charge_date == date(2026, 1, 2)


def test_blank_and_footer_rows_are_not_counted_and_nothing_after_the_footer_is_read(
    isracard_workbook, isracard_row
):
    rows = [isracard_row(), (None,) * 8, isracard_row(merchant="B")]
    workbook = isracard_workbook(rows)
    after_footer = workbook.sheets[0].rows + (isracard_row(merchant="LATE"),)
    workbook = WorkbookView((SheetView("פירוט עסקאות", after_footer),))

    result = read(workbook)

    assert result.rows_seen == 2
    assert [t.merchant_name_raw for t in result.transactions] == ["ACME", "B"]


def test_identical_repeated_rows_are_kept_and_counted_in_the_totals(
    isracard_workbook, isracard_row
):
    workbook = isracard_workbook([isracard_row(), isracard_row()])

    result = read(workbook)

    assert len(result.transactions) == 2
    assert totals(result.calculated_totals) == {"ILS": Decimal("200.00")}


def test_multiline_and_differently_spaced_headers_are_recognised(isracard_workbook, isracard_row):
    header = (
        "תאריך\nרכישה",
        " שם בית  עסק",
        "סכום\nעסקה",
        "מטבע עסקה",
        "סכום חיוב",
        "מטבע חיוב",
        "מס׳ שובר",
        "פירוט\nנוסף",
    )
    workbook = isracard_workbook([isracard_row()], header=header)

    assert len(read(workbook).transactions) == 1


# --- structural failures ------------------------------------------------------------------


def test_a_missing_sheet_is_a_structural_error(isracard_workbook):
    workbook = isracard_workbook(sheet_name="גיליון אחר")

    with pytest.raises(ParseError):
        IsracardParser().read_context(workbook)


def test_a_missing_header_is_a_structural_error(isracard_workbook):
    workbook = isracard_workbook(header=(None,) * 8)

    with pytest.raises(ParseError, match="header"):
        read(workbook)


def test_a_missing_required_column_is_a_structural_error(isracard_workbook):
    broken = isracard_workbook(header=("תאריך רכישה", "שם בית עסק") + (None,) * 6)

    with pytest.raises(ParseError, match="required columns missing"):
        read(broken)


def test_a_statement_without_a_card_banner_is_a_structural_error(isracard_workbook):
    with pytest.raises(ParseError):
        IsracardParser().read_context(isracard_workbook(card="כרטיס ללא ספרות"))


def test_a_statement_without_a_month_is_a_structural_error(isracard_workbook):
    with pytest.raises(ParseError):
        IsracardParser().read_context(isracard_workbook(month=None))


def test_a_statement_without_a_charge_date_is_a_structural_error(isracard_workbook):
    with pytest.raises(ParseError):
        IsracardParser().read_context(isracard_workbook(charge=None))


def test_an_unrelated_workbook_scores_zero(isracard_workbook):
    blank = WorkbookView((SheetView("Sheet1", ((None, None),)),))

    assert IsracardParser().sniff(blank) == 0.0


# --- money --------------------------------------------------------------------------------


def test_a_blank_billed_amount_is_a_pending_transaction_not_a_charge(
    isracard_workbook, isracard_row
):
    workbook = isracard_workbook([isracard_row(), isracard_row(billing=None, merchant="B")])

    result = read(workbook)

    assert len(result.transactions) == 1
    assert result.rows_seen == 2
    (issue,) = result.issues
    assert issue.code is RowIssueCode.PENDING_TRANSACTION
    assert issue.severity is IssueSeverity.INFO
    assert issue.location == SourceLocation(1, 14 + 1)


def test_zero_and_negative_amounts_are_kept_with_their_sign(isracard_workbook, isracard_row):
    refund = isracard_row(billing=-50.25, original=-50.25)
    workbook = isracard_workbook([isracard_row(billing=0, original=10), refund])

    zero, negative = read(workbook).transactions

    assert zero.billing.amount == 0
    assert zero.original.amount == 10
    assert negative.billing.amount == Decimal("-50.25")


def test_an_invalid_amount_or_date_is_reported_with_its_row_and_no_cell_data(
    isracard_workbook, isracard_row
):
    rows = [isracard_row(billing="12abc"), isracard_row(date="32.01.26"), isracard_row(date=None)]

    result = read(isracard_workbook(rows))

    assert result.transactions == ()
    assert result.rows_seen == 3
    assert issue_codes(result) == [
        RowIssueCode.INVALID_AMOUNT,
        RowIssueCode.INVALID_DATE,
        RowIssueCode.MISSING_REQUIRED_VALUE,
    ]
    assert all(i.severity is IssueSeverity.ERROR for i in result.issues)
    assert all("12abc" not in i.message and "32.01.26" not in i.message for i in result.issues)


def test_a_blank_merchant_is_a_missing_value(isracard_workbook, isracard_row):
    result = read(isracard_workbook([isracard_row(merchant=" ")]))

    assert issue_codes(result) == [RowIssueCode.MISSING_REQUIRED_VALUE]


def test_an_unrecognised_billing_currency_is_flagged_for_review(isracard_workbook, isracard_row):
    row = isracard_row(billing_currency="זהב", original_currency="זהב")

    (transaction,) = read(isracard_workbook([row])).transactions

    assert transaction.billing.currency is None
    assert ReviewReason.UNKNOWN_CURRENCY in transaction.review_reasons
    assert transaction.fx is None


def test_the_unknown_currency_total_is_its_own_bucket(isracard_workbook, isracard_row):
    row = isracard_row(billing_currency="זהב", original_currency="זהב")

    result = read(isracard_workbook([row, isracard_row()]))

    assert {m.currency for m in result.calculated_totals.amounts} == {None, ILS}


# --- foreign rows -------------------------------------------------------------------------


def test_a_charge_in_a_different_currency_than_the_purchase_is_foreign_with_its_rate(
    isracard_workbook, isracard_row
):
    row = isracard_row(original=10, original_currency="$", billing=36.5, details="")

    (transaction,) = read(isracard_workbook([row])).transactions

    assert transaction.is_foreign
    assert transaction.original == Money(Decimal("10"), USD)
    assert transaction.fx.effective_fx_rate == effective_fx_rate(Decimal("36.5"), Decimal("10"))
    assert transaction.fx.commission_amount == 0


def test_an_explicit_foreign_note_makes_an_ils_row_foreign(isracard_workbook, isracard_row):
    row = isracard_row(details='אתר חו"ל')

    (transaction,) = read(isracard_workbook([row])).transactions

    assert transaction.is_foreign
    assert transaction.fx.effective_fx_rate == 1


def test_a_foreign_looking_merchant_name_alone_does_not_make_a_row_foreign(
    isracard_workbook, isracard_row
):
    (transaction,) = read(isracard_workbook([isracard_row(merchant='ביטוחי חו"ל')])).transactions

    assert not transaction.is_foreign


def test_a_refund_keeps_source_signs_and_a_negative_effective_rate(isracard_workbook, isracard_row):
    row = isracard_row(original=20, original_currency="$", billing=-72.0)

    (transaction,) = read(isracard_workbook([row])).transactions

    assert transaction.original.amount == 20
    assert transaction.billing.amount == Decimal("-72.0")
    assert transaction.fx.effective_fx_rate == Decimal("-3.6")


def test_a_foreign_row_without_an_original_amount_is_reported_and_skipped(
    isracard_workbook, isracard_row
):
    rows = [
        isracard_row(original=0, original_currency="$", billing=5),
        isracard_row(original=None, original_currency="$", billing=5),
    ]

    result = read(isracard_workbook(rows))

    assert result.transactions == ()
    assert issue_codes(result) == [RowIssueCode.MISSING_OR_ZERO_FX_ORIGINAL_AMOUNT] * 2
    assert all(i.severity is IssueSeverity.ERROR for i in result.issues)


def test_a_foreign_commission_row_is_folded_into_the_nearest_foreign_charge(
    isracard_workbook, isracard_row
):
    purchase = isracard_row(
        date="05.02.26", original=10, original_currency="$", billing=36.0, merchant="SHOP"
    )
    fee = isracard_row(
        date="06.02.26",
        merchant="עמלת המרת מטבע",
        original=1.1,
        billing=1.1,
        original_currency="₪",
        voucher="1",
    )
    unrelated = isracard_row(date="05.02.26", merchant="LOCAL")

    result = read(isracard_workbook([purchase, fee, unrelated]))

    shop, local = result.transactions
    assert shop.billing.amount == Decimal("37.1")
    assert shop.fx.commission_amount == Decimal("1.1")
    assert shop.fx.effective_fx_rate == effective_fx_rate(Decimal("37.1"), Decimal("10"))
    assert local.fx is None
    assert result.rows_seen == 3
    assert totals(result.calculated_totals) == {"ILS": Decimal("137.1")}


def test_an_unmatched_commission_row_stays_an_ordinary_charge_for_review(
    isracard_workbook, isracard_row
):
    fee = isracard_row(merchant="עמלת המרת מטבע", original=1.1, billing=1.1)

    (transaction,) = read(isracard_workbook([fee])).transactions

    assert transaction.billing.amount == Decimal("1.1")
    assert transaction.review_reasons == {ReviewReason.UNMATCHED_FOREIGN_FEE}
    assert not transaction.is_foreign


def test_fee_keywords_and_window_are_constructor_settings(isracard_workbook, isracard_row):
    purchase = isracard_row(date="05.02.26", original=10, original_currency="$", billing=36.0)
    fee = isracard_row(date="08.02.26", merchant="CUSTOM FEE", original=1, billing=1)
    workbook = isracard_workbook([purchase, fee])

    default = read(workbook)
    configured = read(workbook, IsracardParser(fee_keywords=("custom fee",), fee_window_days=3))
    too_narrow = read(workbook, IsracardParser(fee_keywords=("custom fee",), fee_window_days=2))

    assert len(default.transactions) == 2
    assert len(configured.transactions) == 1
    assert len(too_narrow.transactions) == 2


# --- installments -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "details",
    ["תשלום 2 מתוך 12", "תשלום מס' 2 מתוך 12", "תשלום 2/12", "עסקת תשלומים - תשלום 2 מתוך 12 "],
)
def test_installment_notes_are_recognised_in_their_spellings(
    isracard_workbook, isracard_row, details
):
    row = isracard_row(original=1200, billing=100, details=details)

    (transaction,) = read(isracard_workbook([row])).transactions

    assert transaction.transaction_type is TransactionType.INSTALLMENTS
    assert (
        transaction.installment.installment_number,
        transaction.installment.total_installments,
    ) == (2, 12)


def test_the_monthly_charge_is_the_original_and_the_purchase_total_belongs_to_the_plan(
    isracard_workbook, isracard_row
):
    rows = [
        isracard_row(date="10.01.26", original=1200, billing=100, details="תשלום 1 מתוך 12"),
        isracard_row(date="10.01.26", original=1200, billing=100, details="תשלום 2 מתוך 12"),
    ]

    result = read(isracard_workbook(rows))

    first, second = result.transactions
    assert first.original == Money(Decimal("100"), ILS)
    assert first.installment.plan_total == Money(Decimal("1200"), ILS)
    assert first.installment_plan == second.installment_plan
    assert result.installment_plans == (first.installment_plan,)
    assert first.installment_plan.total == Money(Decimal("1200"), ILS)
    assert first.installment_plan.purchase_date == date(2026, 1, 10)


def test_conflicting_charges_of_one_plan_are_reported_as_ambiguous(isracard_workbook, isracard_row):
    rows = [
        isracard_row(original=1200, billing=100, details="תשלום 2 מתוך 12"),
        isracard_row(original=1200, billing=100, details="תשלום 2 מתוך 12"),
    ]

    result = read(isracard_workbook(rows))

    assert result.installment_plans == ()
    assert all(
        ReviewReason.AMBIGUOUS_INSTALLMENT in t.review_reasons and t.installment_plan is None
        for t in result.transactions
    )
    assert len(result.transactions) == 2


def test_an_out_of_range_installment_note_is_ambiguous_and_still_charged(
    isracard_workbook, isracard_row
):
    row = isracard_row(original=1200, billing=100, details="תשלום 13 מתוך 12")

    (transaction,) = read(isracard_workbook([row])).transactions

    assert transaction.installment is None
    assert transaction.billing.amount == 100


# --- privacy ------------------------------------------------------------------------------


def test_issue_messages_never_contain_the_card_or_the_owner(isracard_workbook, isracard_row):
    rows = [isracard_row(billing="x"), isracard_row(billing=None), isracard_row(date=None)]

    result = read(isracard_workbook(rows))

    text = " ".join(issue.message for issue in result.issues)
    assert "1234" not in text and "כהן" not in text
