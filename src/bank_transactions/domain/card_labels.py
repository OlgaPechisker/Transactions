"""The one place that turns a card into the label shown to the user."""

from bank_transactions.domain.enums import IssuerCode

__all__ = ["ISSUER_TITLES", "card_label"]

ISSUER_TITLES: dict[IssuerCode, str] = {
    IssuerCode.ISRACARD: "Isracard",
    IssuerCode.MAX: "Max",
    IssuerCode.CAL: "Cal",
}


def card_label(
    owner_name: str | None, user_name: str, issuer: IssuerCode, last4: str
) -> str:
    """`<owner or signed-in user> · <issuer> ••<last4>`."""
    return f"{owner_name or user_name} · {ISSUER_TITLES[issuer]} ••{last4}"
