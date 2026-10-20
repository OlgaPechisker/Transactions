from collections import Counter
from datetime import date
from decimal import Decimal

import pytest

from bank_transactions.domain.enums import (
    Channel,
    IssuerCode,
    IssueSeverity,
    ReviewReason,
    RowIssueCode,
    TransactionType,
)
from bank_transactions.domain.errors import ParseError
from bank_transactions.domain.issues import SourceLocation
from bank_transactions.domain.money import EUR, ILS, USD, Money, effective_fx_rate
from bank_transactions.domain.workbook import SheetView, WorkbookView
from bank_transactions.parsing.banks.max_card import MaxParser


def read(workbook, parser=None):
    parser = parser or MaxParser()
    return parser.read_transactions(workbook, parser.read_context(workbook))


def totals(result_totals):
    return {money.currency.code: money.amount for money in result_totals.amounts}


def on_sheet(result, position):
    return [t for t in result.transactions if t.location.sheet_position == position]


def billed(transactions):
    return sum((t.billing.amount for t in transactions), Decimal(0))


# --- the real examples ------------------------------------------------------------------


@pytest.mark.parametrize("name", ["max_monthly", "max_yearly"])
def test_examples_are_recognised_with_full_confidence(examples, name):
    assert MaxParser().sniff(examples[name]) == 1.0


@pytest.mark.parametrize("name", ["isracard", "cal"])
def test_other_issuers_score_zero(examples, name):
    assert MaxParser().sniff(examples[name]) == 0.0


def test_monthly_context_lists_both_cards_the_period_and_the_declared_totals(examples):
    context = MaxParser().read_context(examples["max_monthly"])

    assert context.issuer_code is IssuerCode.MAX
    assert len(context.cards) == 2
    assert len({card.key.last4 for card in context.cards}) == 2
    assert all(card.key.issuer_code is IssuerCode.MAX for card in context.cards)
    assert context.owner is None and context.account is None and context.charge_date is None
    assert (context.period.start, context.period.end) == (date(2026, 7, 1), date(2026, 7, 31))
    assert totals(context.declared_totals) == {"ILS": Decimal("20839.67")}


def test_monthly_example_has_142_domestic_and_16_foreign_rows_matching_the_footers(examples):
    workbook = examples["max_monthly"]
    parser = MaxParser()
    context = parser.read_context(workbook)

    result = parser.read_transactions(workbook, context)

    assert (len(on_sheet(result, 1)), len(on_sheet(result, 2))) == (142, 16)
    assert billed(on_sheet(result, 1)) == Decimal("20349.01")
    assert billed(on_sheet(result, 2)) == Decimal("490.66")
    assert result.rows_seen == 158
    assert result.issues == ()
    assert result.calculated_totals == context.declared_totals
    assert {t.card_key for t in result.transactions} == {card.key for card in context.cards}


def test_yearly_example_has_1563_domestic_and_606_foreign_rows_matching_the_footers(examples):
    workbook = examples["max_yearly"]
    parser = MaxParser()
    context = parser.read_context(workbook)

    result = parser.read_transactions(workbook, context)

    assert (len(on_sheet(result, 1)), len(on_sheet(result, 2))) == (1563, 606)
    assert billed(on_sheet(result, 1)) == Decimal("158460.88")
    assert billed(on_sheet(result, 2)) == Decimal("58102.80")
    assert result.rows_seen == 2169
    assert result.issues == ()
    assert totals(context.declared_totals) == {"ILS": Decimal("216563.68")}
    assert result.calculated_totals == context.declared_totals
    assert (context.period.start, context.period.end) == (date(2025, 8, 1), date(2026, 8, 1))


def test_yearly_example_keeps_the_identical_repeated_source_rows(examples):
    result = read(examples["max_yearly"])

    identities = Counter(
        (t.location.sheet_position, t.transaction_date, t.merchant_name_raw, t.billing, t.card_key)
        for t in result.transactions
    )
    assert len(result.transactions) == 2169
    assert sum(count - 1 for count in identities.values() if count > 1) >= 13


def test_example_transaction_types_follow_the_source_columns(examples):
    result = read(examples["max_yearly"])

    domestic = Counter(t.transaction_type for t in on_sheet(result, 1))
    foreign = Counter(t.transaction_type for t in on_sheet(result, 2))
    assert domestic == {
        TransactionType.REGULAR: 1517,
        TransactionType.INSTALLMENTS: 44,
        TransactionType.IMMEDIATE_CHARGE: 2,
    }
    assert foreign == {TransactionType.RECURRING: 420, TransactionType.IMMEDIATE_CHARGE: 186}


def test_example_channels_map_known_values_and_everything_else_is_unknown(examples):
    result = read(examples["max_yearly"])

    assert Counter(t.channel for t in result.transactions) == {
        Channel.INTERNET: 303,
        Channel.PHONE: 212,
        Channel.MOBILE: 724,
        Channel.UNKNOWN: 930,
    }


def test_example_foreign_sheet_rows_all_carry_fx_detail_and_one_domestic_euro_row_does_too(
    examples,
):
    monthly = read(examples["max_monthly"])
    yearly = read(examples["max_yearly"])

    assert all(t.is_foreign for t in on_sheet(monthly, 2))
    assert not any(t.is_foreign for t in on_sheet(monthly, 1))
    assert sum(t.is_foreign for t in on_sheet(yearly, 1)) == 1
    assert all(t.is_foreign for t in on_sheet(yearly, 2))
    assert all(t.fx.commission_amount == 0 for t in yearly.transactions if t.fx)


def test_example_euro_purchase_has_its_stated_and_effective_rate(examples):
    first = on_sheet(read(examples["max_monthly"]), 2)[0]

    assert first.original == Money(Decimal("7.05"), EUR)
    assert first.billing == Money(Decimal("23.54"), ILS)
    assert first.fx.stated_fx_rate == Decimal("3.29")
    assert first.fx.effective_fx_rate == effective_fx_rate(Decimal("23.54"), Decimal("7.05"))
    assert first.merchant_name_raw == "CROSSROAD KIOSK        APHOS         CY"
    assert (first.merchant_name_norm, first.merchant_city) == ("crossroad kiosk", "APHOS")
    assert first.merchant_country == "CY"


def test_example_foreign_rows_without_an_original_currency_are_flagged_for_review(examples):
    result = read(examples["max_yearly"])

    unknown = [t for t in result.transactions if ReviewReason.UNKNOWN_CURRENCY in t.review_reasons]

    assert len(unknown) == 374
    assert all(t.original.currency is None and t.fx is not None for t in unknown)
    assert all(t.billing.currency == ILS for t in unknown)
    assert {t.location.sheet_position for t in unknown} == {2}


def test_example_refunds_keep_source_signs_and_negative_rates_only_when_foreign(examples):
    result = read(examples["max_yearly"])

    refunds = [t for t in result.transactions if t.billing.amount < 0]

    assert len(refunds) == 14
    assert all(t.original.amount > 0 for t in refunds)
    domestic = [t for t in refunds if t.location.sheet_position == 1]
    foreign = [t for t in refunds if t.location.sheet_position == 2]
    assert len(domestic) == 4 and all(t.fx is None for t in domestic)
    assert len(foreign) == 10 and all(t.fx.effective_fx_rate < 0 for t in foreign)


def test_example_stated_rate_is_extracted_from_wording_cells(examples):
    result = read(examples["max_yearly"])

    worded = [t for t in on_sheet(result, 2) if t.location.row == 517]

    assert worded[0].fx.stated_fx_rate == Decimal("2.9950")


def test_example_installments_group_into_plans(examples):
    monthly = read(examples["max_monthly"])
    yearly = read(examples["max_yearly"])

    assert len([t for t in monthly.transactions if t.installment]) == 7
    assert len(monthly.installment_plans) == 7
    assert len([t for t in yearly.transactions if t.installment]) == 44
    assert len(yearly.installment_plans) == 13
    assert all(t.installment_plan is not None for t in yearly.transactions if t.installment)
    assert not any(
        ReviewReason.AMBIGUOUS_INSTALLMENT in t.review_reasons for t in yearly.transactions
    )


def test_example_installment_rows_split_the_monthly_original_from_the_plan_total(examples):
    first = next(t for t in on_sheet(read(examples["max_monthly"]), 1) if t.installment)

    assert first.original == Money(first.billing.amount, ILS)
    assert first.installment.plan_total.amount > first.billing.amount
    assert first.installment_plan.total == first.installment.plan_total
    assert first.notes.startswith("תשלום ")


def test_example_club_discounts_are_appended_to_the_notes_with_a_label(examples):
    result = read(examples["max_yearly"])

    labelled = [t for t in result.transactions if t.notes and "מועדון הנחות: " in t.notes]

    assert len(labelled) == 10


def test_example_merchant_suffixes_are_split_without_touching_the_raw_name(examples):
    workbook = examples["max_yearly"]
    result = read(workbook)

    split = [t for t in result.transactions if t.merchant_country]

    assert len(split) == 572
    assert sum(t.merchant_city is not None for t in result.transactions) == 484
    for transaction in split:
        sheet = workbook.sheets[transaction.location.sheet_position - 1]
        assert transaction.merchant_name_raw == sheet.cell(transaction.location.row, 2)
        assert transaction.merchant_name_norm


def test_example_dates_and_charge_dates_are_parsed_from_day_first_text(examples):
    first = on_sheet(read(examples["max_monthly"]), 1)[0]

    assert first.transaction_date == date(2026, 2, 10)
    assert first.charge_date == date(2026, 7, 10)
    assert first.issuer_reference is None


def test_example_locations_use_sheet_positions(examples):
    result = read(examples["max_monthly"])

    assert result.transactions[0].location == SourceLocation(1, 5)
    assert {t.location.sheet_position for t in result.transactions} == {1, 2}


# --- context reads only the identity column ----------------------------------------------


def test_read_context_never_parses_amounts_or_dates(max_workbook, max_row):
    broken = max_row(
        card="1111", billing="garbage", date="garbage", charge_date="garbage", original="garbage"
    )
    workbook = max_workbook([broken, max_row(card="2222")], [max_row(card="3333")])

    context = MaxParser().read_context(workbook)

    assert [c.key.last4 for c in context.cards] == ["1111", "2222", "3333"]


def test_cards_are_listed_in_order_of_first_appearance_and_numeric_cells_keep_leading_zeros(
    max_workbook, max_row
):
    workbook = max_workbook(
        [max_row(card=123), max_row(card="2222"), max_row(card=123.0)], [max_row(card="2222")]
    )

    context = MaxParser().read_context(workbook)

    assert [c.key.last4 for c in context.cards] == ["0123", "2222"]


def test_rows_with_an_unreadable_card_do_not_add_cards_and_are_reported_when_read(
    max_workbook, max_row
):
    workbook = max_workbook([max_row(), max_row(card=None), max_row(card="12ab")])

    result = read(workbook)

    assert len(result.transactions) == 1
    assert [i.code for i in result.issues] == [RowIssueCode.MISSING_REQUIRED_VALUE] * 2


def test_a_card_missing_from_the_context_is_a_malformed_row(max_workbook, max_row):
    parser = MaxParser()
    context = parser.read_context(max_workbook([max_row(card="1111")]))
    other = max_workbook([max_row(card="1111"), max_row(card="9999")])

    result = parser.read_transactions(other, context)

    assert len(result.transactions) == 1
    assert [i.code for i in result.issues] == [RowIssueCode.MALFORMED_ROW]


def test_declared_totals_are_summed_per_currency_over_both_footers(max_workbook, max_row):
    workbook = max_workbook(
        [max_row()], [max_row()], domestic_total="10.50₪", foreign_total="4.25₪"
    )

    assert totals(MaxParser().read_context(workbook).declared_totals) == {"ILS": Decimal("14.75")}


@pytest.mark.parametrize("missing", ["domestic_total", "foreign_total"])
def test_a_missing_or_unreadable_footer_total_leaves_the_declared_totals_empty(
    max_workbook, max_row, missing
):
    workbook = max_workbook([max_row()], [max_row()], **{missing: None})
    garbled = max_workbook([max_row()], [max_row()], **{missing: "not money"})

    assert MaxParser().read_context(workbook).declared_totals.amounts == ()
    assert MaxParser().read_context(garbled).declared_totals.amounts == ()


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        ("07/2026", (date(2026, 7, 1), date(2026, 7, 31))),
        ("2025-08-01-2026-08-01", (date(2025, 8, 1), date(2026, 8, 1))),
    ],
)
def test_the_period_comes_from_the_banner_in_either_layout(max_workbook, period, expected):
    context = MaxParser().read_context(max_workbook(period=period))

    assert (context.period.start, context.period.end) == expected


def test_an_unrecognised_banner_gives_no_period(max_workbook):
    assert MaxParser().read_context(max_workbook(period="?")).period is None


# --- structural failures -----------------------------------------------------------------


def test_a_missing_sheet_is_a_structural_error_and_halves_the_confidence(max_workbook):
    complete = max_workbook()
    domestic_only = WorkbookView(complete.sheets[:1])
    foreign_only = WorkbookView(complete.sheets[1:])

    assert MaxParser().sniff(domestic_only) == 0.5
    assert MaxParser().sniff(foreign_only) == 0.5
    for workbook in (domestic_only, foreign_only):
        with pytest.raises(ParseError, match="sheet"):
            MaxParser().read_context(workbook)


def test_sheet_names_are_matched_regardless_of_quote_style(max_workbook, max_row):
    workbook = max_workbook(
        [max_row()], domestic_name="  עסקאות  במועד החיוב ", foreign_name="עסקאות חו”ל ומט”ח"
    )

    assert len(read(workbook).transactions) == 1


def test_a_missing_header_or_column_is_a_structural_error(max_workbook):
    complete = max_workbook()
    headerless = SheetView(complete.sheets[0].name, complete.sheets[0].rows[:3])
    columnless = SheetView(
        complete.sheets[0].name,
        (*complete.sheets[0].rows[:3], ("תאריך עסקה", "שם בית העסק"), *complete.sheets[0].rows[4:]),
    )

    with pytest.raises(ParseError, match="header"):
        read(WorkbookView((headerless, complete.sheets[1])))
    with pytest.raises(ParseError, match="required columns missing"):
        read(WorkbookView((columnless, complete.sheets[1])))


def test_an_unrelated_workbook_scores_zero():
    assert MaxParser().sniff(WorkbookView((SheetView("x", ((None,),)),))) == 0.0


# --- row semantics -----------------------------------------------------------------------


def test_transaction_types_map_from_the_source_column(max_workbook, max_row):
    rows = [
        max_row(type="רגילה", notes="הוראת קבע"),
        max_row(type="חיוב חודשי"),
        max_row(type="דחוי לחיוב החודשי"),
        max_row(type="חיוב עסקות מיידי"),
    ]

    types = [t.transaction_type for t in read(max_workbook(rows)).transactions]

    assert types == [
        TransactionType.REGULAR,
        TransactionType.RECURRING,
        TransactionType.RECURRING,
        TransactionType.IMMEDIATE_CHARGE,
    ]


def test_an_unknown_or_blank_transaction_type_is_reported_and_the_row_skipped(
    max_workbook, max_row
):
    rows = [max_row(type="סוג שלא הכרנו"), max_row(type=None), max_row(type="  ")]

    result = read(max_workbook(rows))

    assert result.transactions == ()
    assert [(i.code, i.severity) for i in result.issues] == [
        (RowIssueCode.MALFORMED_ROW, IssueSeverity.ERROR),
        (RowIssueCode.MISSING_REQUIRED_VALUE, IssueSeverity.ERROR),
        (RowIssueCode.MISSING_REQUIRED_VALUE, IssueSeverity.ERROR),
    ]
    assert all("הכרנו" not in i.message for i in result.issues)
    assert "type" in result.issues[0].message


def test_a_pending_row_is_pending_even_when_its_type_is_blank(max_workbook, max_row):
    result = read(max_workbook([max_row(type=None, billing=None)]))

    assert [i.code for i in result.issues] == [RowIssueCode.PENDING_TRANSACTION]


def test_unmapped_and_blank_channels_are_unknown(max_workbook, max_row):
    rows = [
        max_row(channel="אינטרנט"),
        max_row(channel="טלפוני"),
        max_row(channel="תשלום בנייד"),
        max_row(channel="משהו חדש"),
        max_row(channel=None),
    ]

    channels = [t.channel for t in read(max_workbook(rows)).transactions]

    assert channels == [
        Channel.INTERNET,
        Channel.PHONE,
        Channel.MOBILE,
        Channel.UNKNOWN,
        Channel.UNKNOWN,
    ]


def test_category_is_kept_exactly_as_the_source_wrote_it(max_workbook, max_row):
    (transaction,) = read(max_workbook([max_row(category=" רפואה  ובתי מרקחת ")])).transactions

    assert transaction.bank_category_raw == " רפואה  ובתי מרקחת "


def test_notes_keep_the_source_text_and_append_labelled_club_metadata(max_workbook, max_row):
    rows = [
        max_row(notes="למי: דנה", club="הנחת מועדון: הנחות + -3.24 ₪", club_key="K1", tags="t"),
        max_row(club="C"),
        max_row(club_key="K"),
        max_row(),
    ]

    notes = [t.notes for t in read(max_workbook(rows)).transactions]

    assert notes == [
        "למי: דנה | מועדון הנחות: הנחת מועדון: הנחות + -3.24 ₪ | מפתח דיסקונט: K1",
        "מועדון הנחות: C",
        "מפתח דיסקונט: K",
        None,
    ]


def test_blank_billed_amount_or_charge_date_is_pending_but_future_charges_stay_charged(
    max_workbook, max_row
):
    rows = [
        max_row(billing=None),
        max_row(charge_date=None),
        max_row(charge_date="01-01-2099", merchant="FUTURE"),
    ]

    result = read(max_workbook(rows))

    assert [t.merchant_name_raw for t in result.transactions] == ["FUTURE"]
    assert result.transactions[0].charge_date == date(2099, 1, 1)
    assert [i.code for i in result.issues] == [RowIssueCode.PENDING_TRANSACTION] * 2
    assert {i.severity for i in result.issues} == {IssueSeverity.INFO}
    assert result.rows_seen == 3


def test_bad_values_are_reported_per_row_with_safe_locations(max_workbook, max_row):
    rows = [
        max_row(billing="12abc"),
        max_row(date="99-99-2026"),
        max_row(date=None),
        max_row(merchant=None),
        max_row(original="x"),
    ]

    result = read(max_workbook(rows))

    assert result.transactions == ()
    assert [(i.code, i.location) for i in result.issues] == [
        (RowIssueCode.INVALID_AMOUNT, SourceLocation(1, 5)),
        (RowIssueCode.INVALID_DATE, SourceLocation(1, 6)),
        (RowIssueCode.MISSING_REQUIRED_VALUE, SourceLocation(1, 7)),
        (RowIssueCode.MISSING_REQUIRED_VALUE, SourceLocation(1, 8)),
        (RowIssueCode.INVALID_AMOUNT, SourceLocation(1, 9)),
    ]
    assert all("12abc" not in i.message and "1111" not in i.message for i in result.issues)


def test_zero_billing_is_a_valid_charge(max_workbook, max_row):
    (transaction,) = read(max_workbook([max_row(billing=0, original=15)])).transactions

    assert transaction.billing.amount == 0


def test_blank_rows_are_skipped_and_the_footer_ends_the_sheet(max_workbook, max_row):
    workbook = max_workbook([max_row(), (None,) * 16, max_row()])

    assert read(workbook).rows_seen == 2


def test_identical_rows_are_all_kept_and_summed(max_workbook, max_row):
    result = read(max_workbook([max_row(), max_row()]))

    assert len(result.transactions) == 2
    assert totals(result.calculated_totals) == {"ILS": Decimal("200.0")}


# --- foreign rows ------------------------------------------------------------------------


def test_every_foreign_sheet_row_is_foreign_even_in_the_same_currency(max_workbook, max_row):
    row = max_row(type="חיוב חודשי", billing=32.9, original=32.9, rate=None)

    (transaction,) = read(max_workbook(foreign=[row])).transactions

    assert transaction.is_foreign
    assert transaction.fx.effective_fx_rate == 1
    assert transaction.fx.stated_fx_rate is None
    assert transaction.fx.commission_amount == 0


def test_a_domestic_row_in_another_currency_is_foreign_too(max_workbook, max_row):
    row = max_row(original=10, original_currency="€", billing=40)

    (transaction,) = read(max_workbook([row])).transactions

    assert transaction.is_foreign
    assert transaction.original == Money(Decimal("10"), EUR)
    assert transaction.fx.effective_fx_rate == Decimal("4")


def test_a_refund_keeps_its_positive_original_and_gets_a_negative_rate(max_workbook, max_row):
    row = max_row(billing=-35.0, original=10, original_currency="$", rate=" 3.5000")

    (transaction,) = read(max_workbook(foreign=[row])).transactions

    assert transaction.original == Money(Decimal("10"), USD)
    assert transaction.fx.effective_fx_rate == Decimal("-3.5")
    assert transaction.fx.stated_fx_rate == Decimal("3.5000")


def test_a_blank_original_currency_keeps_the_row_and_asks_for_review(max_workbook, max_row):
    row = max_row(original=660, original_currency=None, billing=12.1, rate=" 0.0183")

    (transaction,) = read(max_workbook(foreign=[row])).transactions

    assert transaction.original == Money(Decimal("660"), None)
    assert ReviewReason.UNKNOWN_CURRENCY in transaction.review_reasons
    assert transaction.fx.effective_fx_rate == effective_fx_rate(Decimal("12.1"), Decimal("660"))


def test_an_unrecognised_billing_currency_on_a_domestic_row_is_flagged(max_workbook, max_row):
    (transaction,) = read(max_workbook([max_row(billing_currency="???")])).transactions

    assert transaction.billing.currency is None
    assert ReviewReason.UNKNOWN_CURRENCY in transaction.review_reasons


def test_a_foreign_row_without_an_original_amount_is_reported_and_skipped(max_workbook, max_row):
    rows = [max_row(original=0), max_row(original=None)]

    result = read(max_workbook(foreign=rows))

    assert result.transactions == ()
    assert [i.code for i in result.issues] == [RowIssueCode.MISSING_OR_ZERO_FX_ORIGINAL_AMOUNT] * 2
    assert {i.severity for i in result.issues} == {IssueSeverity.ERROR}


def test_a_domestic_row_without_an_original_amount_is_still_a_charge(max_workbook, max_row):
    (transaction,) = read(
        max_workbook([max_row(original=None, original_currency=None)])
    ).transactions

    assert transaction.original is None


def test_stated_rate_is_never_turned_into_a_commission(max_workbook, max_row):
    row = max_row(original=10, original_currency="€", billing=36.2, rate=" 3.2900")

    (transaction,) = read(max_workbook(foreign=[row])).transactions

    assert transaction.fx.commission_amount == 0
    assert transaction.billing.amount == Decimal("36.2")


def test_a_commission_row_is_folded_into_the_nearest_foreign_charge_of_the_same_card(
    max_workbook, max_row
):
    purchase = max_row(date="01-06-2026", original=10, original_currency="€", billing=36.0)
    other_card = max_row(
        date="01-06-2026", original=10, original_currency="€", billing=36.0, card="2222"
    )
    fee = max_row(date="02-06-2026", merchant="עמלת המרת מטבע", billing=1.2, original=1.2)

    result = read(max_workbook(foreign=[other_card, purchase, fee]))

    other, bought = result.transactions
    assert bought.billing.amount == Decimal("37.2")
    assert bought.fx.commission_amount == Decimal("1.2")
    assert other.billing.amount == Decimal("36.0")
    assert result.rows_seen == 3
    assert totals(result.calculated_totals) == {"ILS": Decimal("73.2")}


def test_an_unmatched_commission_row_remains_a_charge_for_review(max_workbook, max_row):
    fee = max_row(merchant="עמלת המרת מטבע", billing=1.2, original=1.2)

    (transaction,) = read(max_workbook(foreign=[fee])).transactions

    assert ReviewReason.UNMATCHED_FOREIGN_FEE in transaction.review_reasons
    assert transaction.fx is None


def test_fee_settings_are_constructor_arguments(max_workbook, max_row):
    purchase = max_row(date="01-06-2026", original=10, original_currency="€", billing=36.0)
    fee = max_row(date="06-06-2026", merchant="MY FEE", billing=1.0, original=1.0)
    workbook = max_workbook(foreign=[purchase, fee])

    assert len(read(workbook).transactions) == 2
    assert (
        len(read(workbook, MaxParser(fee_keywords=("my fee",), fee_window_days=5)).transactions)
        == 1
    )
    assert (
        len(read(workbook, MaxParser(fee_keywords=("my fee",), fee_window_days=4)).transactions)
        == 2
    )


# --- merchants ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "city", "country"),
    [
        ("ALIEXPRESS        LONDON      GB", "LONDON", "GB"),
        ("AMAZON  US", None, "US"),
        ("SOME SHOP JP", None, None),
        ("סופר פארם טבעון", None, None),
    ],
)
def test_confident_merchant_suffixes_are_split_and_the_raw_name_is_untouched(
    max_workbook, max_row, raw, city, country
):
    (transaction,) = read(max_workbook([max_row(merchant=raw)])).transactions

    assert transaction.merchant_name_raw == raw
    assert (transaction.merchant_city, transaction.merchant_country) == (city, country)
    assert transaction.merchant_name_norm


# --- installments ------------------------------------------------------------------------


def test_a_charge_of_a_plan_has_the_monthly_original_and_the_plan_total_separately(
    max_workbook, max_row
):
    plan = {"type": "תשלומים", "original": 1200, "billing": 100, "date": "10-01-2026"}
    rows = [max_row(**plan, notes="תשלום 1 מתוך 12"), max_row(**plan, notes="תשלום 2 מתוך 12")]

    result = read(max_workbook(rows))

    first, second = result.transactions
    assert first.original == Money(Decimal("100"), ILS)
    assert first.installment.plan_total == Money(Decimal("1200"), ILS)
    assert first.installment_plan == second.installment_plan
    assert result.installment_plans == (first.installment_plan,)


def test_an_installment_row_without_a_usable_note_is_charged_but_ambiguous(max_workbook, max_row):
    rows = [
        max_row(type="תשלומים", original=900, billing=300, notes=note)
        for note in (None, "תשלום 9 מתוך 3")
    ]

    result = read(max_workbook(rows))

    assert all(t.transaction_type is TransactionType.INSTALLMENTS for t in result.transactions)
    assert all(ReviewReason.AMBIGUOUS_INSTALLMENT in t.review_reasons for t in result.transactions)
    assert result.installment_plans == ()
    assert totals(result.calculated_totals) == {"ILS": Decimal("600")}


def test_an_installment_row_with_a_bad_note_keeps_the_monthly_charge_as_original(
    max_workbook, max_row
):
    row = max_row(type="תשלומים", original=1200, billing=300, notes="ללא פירוט")

    (transaction,) = read(max_workbook([row])).transactions

    assert transaction.installment is None
    assert transaction.original == Money(Decimal("300"), ILS)
    assert transaction.billing == Money(Decimal("300"), ILS)


@pytest.mark.parametrize("note", ["תשלום 1 מתוך 3", "ללא פירוט"])
@pytest.mark.parametrize("sheet", ["domestic", "foreign"])
def test_an_installment_in_another_currency_has_no_per_charge_original_so_it_is_rejected(
    max_workbook, max_row, note, sheet
):
    row = max_row(
        type="תשלומים", original=90, original_currency="$", billing=100, notes=note, rate=" 3.5"
    )
    workbook = max_workbook(**{sheet: [row]})

    result = read(workbook)

    assert result.transactions == ()
    assert [(i.code, i.severity) for i in result.issues] == [
        (RowIssueCode.MISSING_OR_ZERO_FX_ORIGINAL_AMOUNT, IssueSeverity.ERROR)
    ]
    assert result.installment_plans == ()


def test_cards_never_share_a_plan(max_workbook, max_row):
    plan = {"type": "תשלומים", "original": 300, "billing": 100, "notes": "תשלום 1 מתוך 3"}
    rows = [max_row(**plan), max_row(**plan, card="2222")]

    result = read(max_workbook(rows))

    assert len(result.installment_plans) == 2
