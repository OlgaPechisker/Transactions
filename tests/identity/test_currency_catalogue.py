from bank_transactions.identity.pycountry_catalogue import PycountryCurrencyCatalogue
from bank_transactions.ports.currency_catalogue import CurrencyCatalogue, CurrencyEntry


def test_catalogue_is_a_currency_catalogue():
    assert isinstance(PycountryCurrencyCatalogue(), CurrencyCatalogue)


def test_common_currencies_are_listed_with_their_english_names():
    entries = {entry.code: entry for entry in PycountryCurrencyCatalogue().entries()}

    assert entries["USD"] == CurrencyEntry("USD", "US Dollar")
    assert entries["EUR"] == CurrencyEntry("EUR", "Euro")
    assert entries["ILS"].name


def test_the_no_currency_and_testing_codes_are_not_offered():
    catalogue = PycountryCurrencyCatalogue()

    for code in ("XXX", "XTS"):
        assert code not in catalogue.codes()
        assert code not in {entry.code for entry in catalogue.entries()}


def test_codes_are_sorted_three_letter_codes_matching_the_entries():
    catalogue = PycountryCurrencyCatalogue()
    codes = catalogue.codes()

    assert codes == tuple(sorted(set(codes)))
    assert all(len(code) == 3 and code.isupper() and code.isalpha() for code in codes)
    assert codes == tuple(entry.code for entry in catalogue.entries())
    assert all(entry.name for entry in catalogue.entries())
