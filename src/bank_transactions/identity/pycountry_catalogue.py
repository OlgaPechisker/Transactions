"""ISO 4217 currency catalogue backed by pycountry."""

import pycountry

from bank_transactions.ports.currency_catalogue import CurrencyCatalogue, CurrencyEntry

# Codes that are valid ISO 4217 entries but never a real billing or original currency.
_EXCLUDED_CODES = frozenset({"XXX", "XTS"})


class PycountryCurrencyCatalogue(CurrencyCatalogue):
    def __init__(self) -> None:
        self._entries = tuple(
            sorted(
                (
                    CurrencyEntry(currency.alpha_3, currency.name)
                    for currency in pycountry.currencies
                    if currency.alpha_3 not in _EXCLUDED_CODES
                ),
                key=lambda entry: entry.code,
            )
        )

    def codes(self) -> tuple[str, ...]:
        return tuple(entry.code for entry in self._entries)

    def entries(self) -> tuple[CurrencyEntry, ...]:
        return self._entries
