from datetime import date
from decimal import Decimal

import pytest

from bank_transactions.domain.enums import IssuerCode, TransactionType
from bank_transactions.domain.issues import SourceLocation
from bank_transactions.domain.models import CardHintKey, ParsedTransaction
from bank_transactions.domain.money import ILS, Money


@pytest.fixture
def card_key():
    return CardHintKey(IssuerCode.MAX, "4396")


@pytest.fixture
def make_transaction(card_key):
    def build(**overrides):
        values = {
            "card_key": card_key,
            "location": SourceLocation(sheet_position=1, row=5),
            "transaction_date": date(2026, 7, 3),
            "charge_date": date(2026, 8, 2),
            "billing": Money(Decimal("120.00"), ILS),
            "merchant_name_raw": "Super  Pharm",
            "merchant_name_norm": "super pharm",
            "transaction_type": TransactionType.REGULAR,
        }
        return ParsedTransaction(**{**values, **overrides})

    return build
