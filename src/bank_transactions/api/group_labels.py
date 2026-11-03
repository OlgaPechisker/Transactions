"""Presentation shared by the routers that return groups: group labels and the card presenter."""

from collections.abc import Callable, Iterable

from bank_transactions.api.schemas.mappers import CardPresent
from bank_transactions.domain.queries import UNKNOWN, Dimension
from bank_transactions.services.card_service import CardView
from bank_transactions.services.composition import ApiComposition
from bank_transactions.services.read_models import VisibleCardRecord

__all__ = ["LabelOf", "card_presenter", "group_labeler"]

LabelOf = Callable[[Dimension, str | None], str]

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Labels of the group without a value (§1.9; the other dimensions follow the OQ-2 defaults).
_MISSING_LABELS = {
    Dimension.CATEGORY: "Uncategorized",
    Dimension.CUSTOM_CATEGORY: "No custom category",
    Dimension.COUNTRY: "Unknown country",
    Dimension.CARD: "No card",
    Dimension.CURRENCY: "No original currency",
    Dimension.TRIP: "No trip",
    Dimension.MERCHANT: "Unknown merchant",
    Dimension.TYPE: "Unknown type",
}


def card_presenter(comp: ApiComposition, user_name: str) -> CardPresent:
    def present(record: VisibleCardRecord) -> CardView:
        return comp.card_presenter.present(record, user_name)

    return present


def group_labeler(
    present_card: CardPresent,
    cards: Iterable[VisibleCardRecord],
    trips: Iterable[tuple[str, str]],
) -> LabelOf:
    """The label of a group key in a dimension; `trips` are (public id, name) pairs. A key the
    lookups do not know is its own label."""
    cards_by_id: dict[str, VisibleCardRecord] = {card.public_id: card for card in cards}
    trip_names = dict(trips)

    def label_of(dimension: Dimension, key: str | None) -> str:
        if key is None:
            return _MISSING_LABELS[dimension]
        match dimension:
            case Dimension.CURRENCY if key == UNKNOWN:
                return "Unknown currency"
            case Dimension.MONTH:
                year, month = key.split("-")
                return f"{_MONTHS[int(month) - 1]} {year}"
            case Dimension.WEEK:
                return f"Week of {key}"
            case Dimension.CARD:
                return present_card(cards_by_id[key]).label if key in cards_by_id else key
            case Dimension.TRIP:
                return trip_names.get(key, key)
            case _:
                return key

    return label_of
