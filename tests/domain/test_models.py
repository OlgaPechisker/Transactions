import dataclasses
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
from bank_transactions.domain.issues import RowIssue, SourceLocation
from bank_transactions.domain.models import (
    AccountHint,
    CardHint,
    CardHintKey,
    FeeCandidate,
    OwnerHint,
    ParsedFxDetail,
    ParsedInstallmentPlan,
    ParsedInstallmentRef,
    StatementContext,
    StatementParseResult,
    StatementPeriod,
    StatementTotals,
)
from bank_transactions.domain.money import ILS, USD, Money

KEY = CardHintKey(IssuerCode.CAL, "0538")


def test_card_key_keeps_leading_zeros_and_identifies_by_issuer_and_last4():
    assert CardHintKey(IssuerCode.CAL, "0538") == CardHintKey(IssuerCode.CAL, "0538")
    assert CardHintKey(IssuerCode.CAL, "0538") != CardHintKey(IssuerCode.MAX, "0538")
    assert CardHintKey(IssuerCode.CAL, "0538").last4 == "0538"


@pytest.mark.parametrize("last4", ["", "538", "05380", "05a8", "٠٥٣٨"])
def test_card_key_requires_exactly_four_ascii_digits(last4):
    with pytest.raises(ValueError):
        CardHintKey(IssuerCode.CAL, last4)


def test_card_key_repr_never_shows_the_last4():
    assert "0538" not in repr(KEY)
    assert "0538" not in str(KEY)


def test_account_hint_canonicalizes_the_account_number():
    hint = AccountHint(bank_code="LEUMI", account_number="891-7221038")

    assert hint.account_number == "8917221038"


def test_account_hint_repr_never_shows_the_account_number():
    assert "7221038" not in repr(AccountHint(bank_code="LEUMI", account_number="891-7221038"))


def test_account_hint_rejects_non_numeric_account_number():
    with pytest.raises(ValueError):
        AccountHint(bank_code=None, account_number="abc")


def test_account_hint_may_carry_only_a_label():
    hint = AccountHint(bank_code=None, account_number=None, display_name="Joint")

    assert hint.account_number is None


def test_context_exposes_cards_as_tuple_and_pairs_hints_with_statement_identity():
    owner = OwnerHint("Dana Levi")
    context = StatementContext(
        issuer_code=IssuerCode.CAL,
        cards=[CardHint(KEY, label="Visa")],
        owner=owner,
        account=AccountHint("LEUMI", "891-7221038"),
        charge_date=date(2026, 9, 10),
    )

    (hint,) = context.identity_hints()

    assert isinstance(context.cards, tuple)
    assert hint.card.key == KEY
    assert hint.owner == owner
    assert hint.account.bank_code == "LEUMI"


def test_context_charge_date_is_optional():
    context = StatementContext(issuer_code=IssuerCode.MAX, cards=[CardHint(KEY)])

    assert context.charge_date is None
    assert context.declared_totals == StatementTotals()


def test_context_rejects_repeated_card_keys():
    with pytest.raises(ValueError):
        StatementContext(IssuerCode.CAL, cards=[CardHint(KEY), CardHint(KEY, label="again")])


def test_context_repr_never_shows_identifiers():
    context = StatementContext(
        IssuerCode.CAL,
        cards=[CardHint(KEY)],
        account=AccountHint("LEUMI", "891-7221038"),
    )

    assert "0538" not in repr(context)
    assert "7221038" not in repr(context)


def test_period_start_cannot_follow_end():
    with pytest.raises(ValueError):
        StatementPeriod(start=date(2026, 3, 1), end=date(2026, 2, 1))


def test_totals_are_per_currency_and_include_an_unknown_currency_bucket():
    totals = StatementTotals.of(Money(Decimal("10"), ILS), Money(Decimal("5"), USD))

    assert totals.by_currency[ILS] == Decimal("10")
    assert totals.by_currency[USD] == Decimal("5")


def test_totals_sum_amounts_with_the_same_currency():
    totals = StatementTotals.of(Money(Decimal("10"), ILS), Money(Decimal("2.5"), ILS))

    assert totals.by_currency == {ILS: Decimal("12.5")}


def test_totals_with_equal_amounts_are_equal_regardless_of_order_and_hashable():
    one = StatementTotals.of(Money(Decimal("10"), ILS), Money(Decimal("5"), USD))
    two = StatementTotals.of(Money(Decimal("5.0"), USD), Money(Decimal("10.00"), ILS))

    assert one == two
    assert hash(one) == hash(two)


def test_totals_mapping_is_read_only():
    totals = StatementTotals.of(Money(Decimal("10"), ILS))

    with pytest.raises(TypeError):
        totals.by_currency[ILS] = Decimal("1")  # type: ignore[index]


def test_parsed_transaction_is_frozen(make_transaction):
    transaction = make_transaction()

    with pytest.raises(dataclasses.FrozenInstanceError):
        transaction.notes = "changed"  # type: ignore[misc]


def test_review_reasons_are_an_immutable_set(make_transaction):
    transaction = make_transaction(
        review_reasons={ReviewReason.UNKNOWN_CURRENCY},
        billing=Money(Decimal("660"), None),
    )

    assert isinstance(transaction.review_reasons, frozenset)
    assert not hasattr(transaction.review_reasons, "add")


def test_charge_date_is_mandatory_on_a_transaction(make_transaction):
    with pytest.raises(TypeError):
        make_transaction(charge_date=None)


def test_unknown_currency_requires_the_review_reason(make_transaction):
    with pytest.raises(ValueError):
        make_transaction(original=Money(Decimal("660"), None))


def test_unknown_original_currency_with_reason_is_valid(make_transaction):
    transaction = make_transaction(
        original=Money(Decimal("660"), None),
        review_reasons={ReviewReason.UNKNOWN_CURRENCY},
    )

    assert transaction.original.currency is None


def test_blank_merchant_is_rejected(make_transaction):
    with pytest.raises(ValueError):
        make_transaction(merchant_name_raw=" ")


def test_refunds_and_discount_charges_keep_source_amounts(make_transaction):
    refund = make_transaction(
        billing=Money(Decimal("-40.00"), ILS), original=Money(Decimal("40.00"), USD)
    )
    discount = make_transaction(billing=Money(Decimal("0.00"), ILS))

    assert refund.billing.is_negative()
    assert refund.original.amount > 0
    assert discount.billing.is_zero()


def test_raw_notes_and_category_are_retained_verbatim(make_transaction):
    transaction = make_transaction(notes="לכבוד דנה  לוי", bank_category_raw=" מסעדות ")

    assert transaction.notes == "לכבוד דנה  לוי"
    assert transaction.bank_category_raw == " מסעדות "


def test_transaction_without_fx_detail_is_domestic(make_transaction):
    assert make_transaction().is_foreign is False


def test_foreign_transaction_exposes_fx_detail(make_transaction):
    transaction = make_transaction(
        billing=Money(Decimal("-366.00"), ILS),
        original=Money(Decimal("100.00"), USD),
        fx=ParsedFxDetail(
            commission_amount=Decimal("0"),
            stated_fx_rate=Decimal("3.66"),
            effective_fx_rate=Decimal("-3.66"),
        ),
        channel=Channel.INTERNET,
    )

    assert transaction.is_foreign is True
    assert transaction.fx.effective_fx_rate == Decimal("-3.66")


def test_fx_needs_a_non_zero_original_amount_and_known_billing_currency(make_transaction):
    fx = ParsedFxDetail(Decimal("0"), None, Decimal("3.66"))

    with pytest.raises(ValueError):
        make_transaction(fx=fx)
    with pytest.raises(ValueError):
        make_transaction(original=Money(Decimal("0"), USD), fx=fx)
    with pytest.raises(ValueError):
        make_transaction(
            billing=Money(Decimal("5"), None),
            original=Money(Decimal("1"), USD),
            fx=fx,
            review_reasons={ReviewReason.UNKNOWN_CURRENCY},
        )


def test_installment_ref_counts_are_one_based():
    with pytest.raises(ValueError):
        ParsedInstallmentRef(installment_number=0, total_installments=10)
    with pytest.raises(ValueError):
        ParsedInstallmentRef(installment_number=11, total_installments=10)


def installment_plan(card_key=KEY):
    return ParsedInstallmentPlan(
        card_key=card_key,
        merchant_name_norm="store",
        purchase_date=date(2026, 5, 1),
        total=Money(Decimal("1300.00"), ILS),
        total_installments=10,
    )


def test_plan_total_needs_a_known_currency():
    with pytest.raises(ValueError):
        ParsedInstallmentPlan(
            card_key=KEY,
            merchant_name_norm="store",
            purchase_date=date(2026, 5, 1),
            total=Money(Decimal("1300.00"), None),
            total_installments=10,
        )


def test_linked_installment_transaction_carries_ref_and_plan(make_transaction, card_key):
    plan = installment_plan(card_key)
    transaction = make_transaction(
        transaction_type=TransactionType.INSTALLMENTS,
        installment=ParsedInstallmentRef(4, 10, plan_total=plan.total),
        installment_plan=plan,
    )

    assert transaction.installment.installment_number == 4
    assert transaction.installment_plan == plan


def test_plan_without_installment_ref_is_rejected(make_transaction):
    with pytest.raises(ValueError):
        make_transaction(installment_plan=installment_plan())


def test_plan_installment_count_must_match_the_ref(make_transaction, card_key):
    with pytest.raises(ValueError):
        make_transaction(
            installment=ParsedInstallmentRef(1, 12),
            installment_plan=installment_plan(card_key),
        )


def test_plan_must_belong_to_the_transactions_card(make_transaction):
    with pytest.raises(ValueError):
        make_transaction(
            installment=ParsedInstallmentRef(1, 10),
            installment_plan=installment_plan(KEY),
        )


def test_installment_without_plan_may_be_flagged_ambiguous(make_transaction):
    transaction = make_transaction(
        installment=ParsedInstallmentRef(1, 12),
        review_reasons={ReviewReason.AMBIGUOUS_INSTALLMENT},
    )

    assert transaction.installment_plan is None


def test_fee_candidate_carries_its_card_and_amount():
    fee = FeeCandidate(
        card_key=KEY,
        location=SourceLocation(2, 4),
        transaction_date=date(2026, 7, 3),
        charge_date=date(2026, 8, 2),
        amount=Money(Decimal("2.50"), ILS),
    )

    assert fee.amount == Money(Decimal("2.5"), ILS)


def test_parse_result_freezes_sequences_and_defaults_plans_to_empty(make_transaction):
    issue = RowIssue(SourceLocation(1, 3), RowIssueCode.MALFORMED_ROW, IssueSeverity.ERROR, "bad")
    result = StatementParseResult(
        transactions=[make_transaction()],
        issues=[issue],
        rows_seen=2,
        calculated_totals=StatementTotals.of(Money(Decimal("120"), ILS)),
    )

    assert isinstance(result.transactions, tuple)
    assert isinstance(result.issues, tuple)
    assert result.installment_plans == ()


def test_parse_result_row_count_cannot_be_negative():
    with pytest.raises(ValueError):
        StatementParseResult((), (), -1, StatementTotals())
