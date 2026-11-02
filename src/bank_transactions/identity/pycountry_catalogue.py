"""ISO 4217 currency catalogue backed by pycountry. Stub: implemented by U3."""

from bank_transactions.ports.currency_catalogue import CurrencyCatalogue, CurrencyEntry


class PycountryCurrencyCatalogue(CurrencyCatalogue):
    def codes(self) -> tuple[str, ...]:
        raise NotImplementedError

    def entries(self) -> tuple[CurrencyEntry, ...]:
        raise NotImplementedError
