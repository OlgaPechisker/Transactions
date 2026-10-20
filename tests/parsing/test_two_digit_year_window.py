from datetime import date

import pytest

from bank_transactions.parsing.banks.cal_visa import CalParser
from bank_transactions.parsing.banks.isracard import IsracardParser
from bank_transactions.parsing.banks.max_card import MaxParser
from bank_transactions.parsing.default_registry import build_default_registry

PARSERS = [IsracardParser, CalParser, MaxParser]


def read(workbook, parser):
    return parser.read_transactions(workbook, parser.read_context(workbook))


@pytest.fixture
def workbooks(isracard_workbook, isracard_row, cal_workbook, cal_row, max_workbook, max_row):
    """One workbook per issuer whose only row is dated with the two-digit year 50."""
    return {
        IsracardParser: isracard_workbook([isracard_row(date="01.01.50")]),
        CalParser: cal_workbook([cal_row(date="01/01/50")]),
        MaxParser: max_workbook([max_row(date="01-01-50")]),
    }


@pytest.mark.parametrize("parser_type", PARSERS)
def test_by_default_year_50_is_1950_and_a_shorter_look_back_makes_it_2050(workbooks, parser_type):
    workbook = workbooks[parser_type]

    (default,) = read(workbook, parser_type()).transactions
    (custom,) = read(workbook, parser_type(two_digit_years_back=20)).transactions

    assert default.transaction_date == date(1950, 1, 1)
    assert custom.transaction_date == date(2050, 1, 1)


@pytest.mark.parametrize("parser_type", PARSERS)
def test_a_look_back_outside_0_to_99_is_refused_when_the_parser_is_built(parser_type):
    for bad in (-1, 100):
        with pytest.raises(ValueError, match="two_digit_years_back"):
            parser_type(two_digit_years_back=bad)


def test_the_look_back_does_not_move_the_statement_anchor(max_workbook, max_row):
    workbook = max_workbook([max_row(date="01-01-26"), max_row(date="01-01-50")])
    parser = MaxParser(two_digit_years_back=20, today=lambda: date(2099, 1, 1))

    first, second = read(workbook, parser).transactions

    assert first.transaction_date == date(2026, 1, 1)
    assert second.transaction_date == date(2050, 1, 1)


def test_without_a_statement_date_the_injected_today_is_the_anchor(max_workbook, max_row):
    workbook = max_workbook([max_row(date="01-01-50")], period="?")

    (early,) = read(workbook, MaxParser(today=lambda: date(2026, 1, 1))).transactions
    (late,) = read(workbook, MaxParser(today=lambda: date(2040, 1, 1))).transactions

    assert early.transaction_date == date(1950, 1, 1)
    assert late.transaction_date == date(2050, 1, 1)


@pytest.mark.parametrize("parser_type", PARSERS)
def test_the_default_registry_passes_the_look_back_to_every_parser(workbooks, parser_type):
    workbook = workbooks[parser_type]

    default = build_default_registry().select(workbook)
    custom = build_default_registry(two_digit_years_back=20).select(workbook)

    assert read(workbook, default).transactions[0].transaction_date == date(1950, 1, 1)
    assert read(workbook, custom).transactions[0].transaction_date == date(2050, 1, 1)


def test_the_default_registry_refuses_a_look_back_outside_0_to_99():
    for bad in (-1, 100):
        with pytest.raises(ValueError, match="two_digit_years_back"):
            build_default_registry(two_digit_years_back=bad)
