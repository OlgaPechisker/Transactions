"""The transaction service: query validation, one snapshot per list, and detail visibility."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pytest
from tests.fakes.auth import make_auth_context
from tests.fakes.unit_of_work import InMemoryUnitOfWorkFactory

from bank_transactions.domain.enums import (
    CategorySource,
    IssuerCode,
    ReviewReason,
    TransactionType,
)
from bank_transactions.domain.errors import InvalidQueryError, NotFoundError
from bank_transactions.domain.ids import CardId, IdentifierToken, PublicId, TransactionId, TripId
from bank_transactions.domain.queries import (
    Dimension,
    Page,
    PageRequest,
    SortSpec,
    TransactionFilter,
    TransactionInclude,
)
from bank_transactions.persistence.interfaces import Isolation
from bank_transactions.persistence.records import (
    BillingSummaryRecord,
    FilterOptionsRecord,
    GroupRecord,
    TransactionViewRecord,
    TripRecord,
    VisibleCardRecord,
)
from bank_transactions.services.transaction_service import TransactionService

ANN = make_auth_context(user_id=1)
BEN = make_auth_context(user_id=2)
CARD = VisibleCardRecord(
    CardId(1), PublicId("crd_1"), IssuerCode.MAX, IdentifierToken(b"enc"), None, None
)
TRIP = TripRecord(TripId(1), PublicId("trp_1"), "Rome", "EUR", date(2026, 7, 1), date(2026, 7, 9))
SUMMARY = BillingSummaryRecord(2, 0, (), 1)
GROUPS = (GroupRecord("2026-07", SUMMARY),)
SUMMARY_AND_GROUPS = frozenset({TransactionInclude.SUMMARY, TransactionInclude.GROUPS})
NEWEST_FIRST = SortSpec((("transactionDate", True),))
ONE_PAGE = PageRequest(1, 50)
NO_OPTIONS = FilterOptionsRecord((), (), (CARD,), (), (), (), (), 0, 0, None, None)


def view(transaction_id: int, public_id: str) -> TransactionViewRecord:
    return TransactionViewRecord(
        transaction_id=TransactionId(transaction_id),
        public_id=PublicId(public_id),
        transaction_date=date(2026, 7, 3),
        charge_date=date(2026, 8, 2),
        merchant="Super Pharm",
        city=None,
        country=None,
        bank_category=None,
        custom_category=None,
        effective_category=None,
        category_source=CategorySource.BANK,
        card=CARD,
        transaction_type=TransactionType.REGULAR,
        installment_number=None,
        installment_total=None,
        original_amount=None,
        original_currency=None,
        billing_amount=Decimal("10.00"),
        billing_currency="ILS",
        effective_rate=None,
        commission=None,
        trip=None,
        notes=None,
    )


@dataclass
class FakeTransactionQuery:
    """Each user owns the records filed under their id."""

    owned: dict[int, list[TransactionViewRecord]] = field(default_factory=dict)

    def page(self, user_id, filter, sort, page):
        items = tuple(self.owned.get(user_id, []))
        return Page(items, page.page, page.size, len(items))

    def summary(self, user_id, filter):
        return SUMMARY

    def groups(self, user_id, filter, dimension):
        return GROUPS

    def get(self, user_id, public_id):
        return next((r for r in self.owned.get(user_id, []) if r.public_id == public_id), None)

    def filter_options(self, user_id):
        return NO_OPTIONS


class FakeVisibleCards:
    def list_visible(self, user_id):
        return (CARD,)


class FakeTrips:
    def list(self, user_id):
        return (TRIP,)


@pytest.fixture
def factory() -> InMemoryUnitOfWorkFactory:
    factory = InMemoryUnitOfWorkFactory()
    factory.template.transaction_query = FakeTransactionQuery(
        {ANN.user_id: [view(1, "txn_1"), view(2, "txn_2")]}
    )
    factory.template.visible_cards = FakeVisibleCards()
    factory.template.trips = FakeTrips()
    return factory


@pytest.fixture
def service(factory) -> TransactionService:
    return TransactionService(factory)


def list_with(service, filter=None, sort=NEWEST_FIRST, group_by=None, include=frozenset()):
    return service.list(ANN, filter or TransactionFilter(), group_by, include, sort, ONE_PAGE)


@pytest.mark.parametrize(
    "filter",
    [
        TransactionFilter(min_amount=Decimal("1")),
        TransactionFilter(max_amount=Decimal("100")),
        TransactionFilter(review_reasons=(ReviewReason.UNMATCHED_FOREIGN_FEE,)),
        TransactionFilter(
            review_reasons=(ReviewReason.UNKNOWN_CURRENCY, ReviewReason.AMBIGUOUS_INSTALLMENT)
        ),
    ],
    ids=["min amount", "max amount", "internal review reason", "mixed review reasons"],
)
def test_an_inconsistent_filter_is_an_invalid_filter_and_reads_nothing(service, factory, filter):
    with pytest.raises(InvalidQueryError) as raised:
        list_with(service, filter)

    assert raised.value.code == InvalidQueryError.INVALID_FILTER
    assert factory.created == []


def test_amount_bounds_are_accepted_with_a_billing_currency(service):
    filter = TransactionFilter(billing_currency="ILS", min_amount=Decimal("1"))

    assert list_with(service, filter).page.total == 2


def test_the_unknown_currency_review_reason_is_accepted(service):
    filter = TransactionFilter(review_reasons=(ReviewReason.UNKNOWN_CURRENCY,))

    assert list_with(service, filter).page.total == 2


@pytest.mark.parametrize("name", ["amount", "billingCurrency", "", "group:month", "Merchant"])
def test_an_unknown_sort_field_is_an_invalid_sort(service, factory, name):
    with pytest.raises(InvalidQueryError) as raised:
        list_with(service, sort=SortSpec(((name, False),)))

    assert raised.value.code == InvalidQueryError.INVALID_SORT
    assert factory.created == []


def test_the_page_summary_and_groups_come_from_one_repeatable_read_snapshot(service, factory):
    result = list_with(service, group_by=Dimension.MONTH, include=SUMMARY_AND_GROUPS)

    assert [r.public_id for r in result.page.items] == ["txn_1", "txn_2"]
    assert (result.summary, result.groups) == (SUMMARY, GROUPS)
    assert [isolation for _, isolation in factory.created] == [Isolation.REPEATABLE_READ]


def test_summary_and_groups_are_present_only_when_requested(service):
    only_page = list_with(service, group_by=Dimension.MONTH)
    with_summary = list_with(service, include=frozenset({TransactionInclude.SUMMARY}))
    with_groups = list_with(
        service, group_by=Dimension.MONTH, include=frozenset({TransactionInclude.GROUPS})
    )

    assert (only_page.summary, only_page.groups) == (None, None)
    assert (with_summary.summary, with_summary.groups) == (SUMMARY, None)
    assert (with_groups.summary, with_groups.groups) == (None, GROUPS)


def test_groups_without_a_group_by_are_left_out(service):
    assert list_with(service, include=SUMMARY_AND_GROUPS).groups is None


def test_a_card_grouping_brings_the_cards_for_the_labels_and_a_trip_grouping_the_trips(service):
    by_card = list_with(service, group_by=Dimension.CARD, include=SUMMARY_AND_GROUPS)
    by_trip = list_with(service, group_by=Dimension.TRIP, include=SUMMARY_AND_GROUPS)
    by_month = list_with(service, group_by=Dimension.MONTH, include=SUMMARY_AND_GROUPS)

    assert (by_card.cards, by_card.trips) == ((CARD,), ())
    assert (by_trip.cards, by_trip.trips) == ((), (TRIP,))
    assert (by_month.cards, by_month.trips) == ((), ())


def test_the_detail_of_a_transaction_is_returned_to_its_owner(service):
    assert service.get(ANN, PublicId("txn_2")).public_id == "txn_2"


def test_a_missing_or_foreign_transaction_is_not_found(service):
    with pytest.raises(NotFoundError):
        service.get(ANN, PublicId("txn_missing"))
    with pytest.raises(NotFoundError):
        service.get(BEN, PublicId("txn_1"))


def test_filter_options_carry_the_trips_of_the_user(service):
    result = service.filter_options(ANN)

    assert (result.options, result.trips) == (NO_OPTIONS, (TRIP,))
