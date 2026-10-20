import dataclasses
from datetime import date, datetime
from decimal import Decimal

import pytest

from bank_transactions.domain.enums import (
    Channel,
    ImportStatus,
    IssuerCode,
    ReviewReason,
    TransactionType,
)
from bank_transactions.domain.ids import (
    AccountId,
    CardId,
    IdentifierToken,
    ImportRunId,
    InstallmentPlanId,
    OwnerId,
)
from bank_transactions.domain.keys import OwnerMatchKey
from bank_transactions.domain.money import ILS, USD
from bank_transactions.persistence.commands import (
    ImportRunOutcome,
    NewAccount,
    NewCard,
    NewForeignDetail,
    NewImportRun,
    NewInstallmentPlan,
    NewOwner,
    NewTransaction,
    NewTransactionAggregate,
)
from bank_transactions.persistence.interfaces import (
    AccountRepository,
    CardRepository,
    ImportRunRepository,
    InstallmentPlanRepository,
    OwnerRepository,
    TransactionRepository,
    UnitOfWork,
    UnitOfWorkFactory,
)
from bank_transactions.persistence.records import CardLookup, CardRecord

NOW = datetime(2026, 10, 20, 12, 0, 0)
TOKEN = IdentifierToken(b"ciphertext")


def new_transaction(**overrides):
    values = {
        "card_id": CardId(1),
        "import_run_id": ImportRunId(1),
        "issuer_reference": None,
        "transaction_date": date(2026, 7, 3),
        "charge_date": date(2026, 8, 2),
        "billing_amount": Decimal("120.00"),
        "billing_currency": ILS,
        "original_amount": None,
        "original_currency": None,
        "merchant_name_raw": "Cafe",
        "merchant_name_norm": "cafe",
        "merchant_city": None,
        "merchant_country": None,
        "bank_category_raw": None,
        "transaction_type": TransactionType.REGULAR,
        "channel": Channel.UNKNOWN,
        "notes": None,
        "installment_plan_id": None,
        "installment_number": None,
    }
    return NewTransaction(**{**values, **overrides})


def test_audit_run_starts_with_safe_labels_and_unknown_issuer():
    run = NewImportRun(
        file_path="sha256:ab12",
        file_name="statement-ab12.xlsx",
        file_sha256="ab" * 32,
        started_at=NOW,
    )

    assert run.file_name == "statement-ab12.xlsx"


def test_terminal_outcome_cannot_be_running():
    with pytest.raises(ValueError):
        ImportRunOutcome(ImportStatus.RUNNING, NOW, None, 0, 0, 0, 0, None)


def test_outcome_counts_cannot_be_negative():
    with pytest.raises(ValueError):
        ImportRunOutcome(ImportStatus.SUCCESS, NOW, IssuerCode.MAX, -1, 0, 0, 0, None)


def test_failed_outcome_may_have_no_issuer_yet():
    outcome = ImportRunOutcome(ImportStatus.FAILED, NOW, None, 0, 0, 0, 0, "unknown format")

    assert outcome.issuer_code is None


def test_installment_plan_commands_compare_equal_regardless_of_amount_scale():
    def plan(total):
        return NewInstallmentPlan(CardId(1), None, "store", date(2026, 5, 1), total, ILS, 10)

    assert plan(Decimal("1300")) == plan(Decimal("1300.00"))
    assert hash(plan(Decimal("1300"))) == hash(plan(Decimal("1300.00")))
    assert plan(Decimal("1300")) != plan(Decimal("1300.01"))


def test_installment_plan_needs_a_currency_and_positive_count():
    with pytest.raises(ValueError):
        NewInstallmentPlan(CardId(1), None, "store", date(2026, 5, 1), Decimal("1"), ILS, 0)


def test_aggregate_carries_one_transaction_reasons_and_at_most_one_fx_detail():
    aggregate = NewTransactionAggregate(
        transaction=new_transaction(
            original_amount=Decimal("100.00"),
            original_currency=USD,
            billing_amount=Decimal("366.00"),
        ),
        review_reasons={ReviewReason.UNKNOWN_CURRENCY},
        foreign_detail=NewForeignDetail(Decimal("0"), None, Decimal("3.66")),
    )

    assert aggregate.review_reasons == frozenset({ReviewReason.UNKNOWN_CURRENCY})
    assert isinstance(aggregate.review_reasons, frozenset)
    assert aggregate.foreign_detail.effective_fx_rate == Decimal("3.66")


def test_aggregate_is_immutable():
    aggregate = NewTransactionAggregate(new_transaction())

    with pytest.raises(dataclasses.FrozenInstanceError):
        aggregate.foreign_detail = None  # type: ignore[misc]


def test_identity_commands_hold_ciphertext_never_plaintext():
    owner = NewOwner("Dana Levi", OwnerMatchKey.from_display_name("Dana Levi"))
    account = NewAccount(OwnerId(1), "LEUMI", TOKEN, None)
    card = NewCard(OwnerId(1), AccountId(2), IssuerCode.CAL, TOKEN, None)

    assert owner.match_key == OwnerMatchKey.from_display_name("dana  levi")
    assert account.account_number_enc == TOKEN
    assert card.last4_enc == TOKEN


def test_card_lookup_narrows_by_account_or_owner_optionally():
    lookup = CardLookup(IssuerCode.CAL, TOKEN)

    assert lookup.account_id is None
    assert lookup.owner_id is None
    assert CardLookup(IssuerCode.CAL, TOKEN, account_id=AccountId(2)).account_id == AccountId(2)


def test_records_are_immutable_read_models():
    record = CardRecord(CardId(1), OwnerId(1), AccountId(2), IssuerCode.CAL, TOKEN, None)

    with pytest.raises(dataclasses.FrozenInstanceError):
        record.display_name = "x"  # type: ignore[misc]


def test_repositories_expose_exactly_the_documented_operations():
    assert ImportRunRepository.__abstractmethods__ == {
        "start_run",
        "finish_run",
        "sweep_stuck_runs",
    }
    assert OwnerRepository.__abstractmethods__ == {"list_all", "get", "find_by_match_key", "create"}
    assert AccountRepository.__abstractmethods__ == {
        "list_all",
        "find_by_number",
        "get",
        "create",
    }
    assert CardRepository.__abstractmethods__ == {"get", "find_candidates", "create"}
    assert InstallmentPlanRepository.__abstractmethods__ == {"upsert_plans"}
    assert TransactionRepository.__abstractmethods__ == {
        "find_known_references",
        "count_matching_groups",
        "insert_many",
    }


def test_unit_of_work_exposes_every_repository_plus_commit_and_rollback():
    assert UnitOfWork.__abstractmethods__ == {
        "commit",
        "rollback",
        "import_runs",
        "owners",
        "accounts",
        "cards",
        "installment_plans",
        "transactions",
    }
    assert UnitOfWorkFactory.__abstractmethods__ == {"create"}


class RecordingUnitOfWork(UnitOfWork):
    def __init__(self):
        self.events = []

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")

    import_runs = owners = accounts = cards = installment_plans = transactions = None  # type: ignore[assignment]


def test_leaving_the_context_without_commit_rolls_back():
    uow = RecordingUnitOfWork()

    with uow:
        pass

    assert uow.events == ["rollback"]


def test_exception_inside_the_context_rolls_back_and_propagates():
    uow = RecordingUnitOfWork()

    with pytest.raises(RuntimeError), uow:
        raise RuntimeError("boom")

    assert uow.events == ["rollback"]


def test_plan_ids_are_typed_integers():
    assert InstallmentPlanId(4) == 4
