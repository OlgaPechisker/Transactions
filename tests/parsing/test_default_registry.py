import pytest

from bank_transactions.domain.enums import IssuerCode, TransactionType
from bank_transactions.domain.errors import UnknownFormatError
from bank_transactions.domain.workbook import SheetView, WorkbookView
from bank_transactions.parsing.default_registry import build_default_registry


@pytest.mark.parametrize(
    ("example", "issuer"),
    [
        ("isracard", IssuerCode.ISRACARD),
        ("cal", IssuerCode.CAL),
        ("max_monthly", IssuerCode.MAX),
        ("max_yearly", IssuerCode.MAX),
    ],
)
def test_each_example_selects_its_own_parser_without_ties(examples, example, issuer):
    registry = build_default_registry()

    assert registry.select(examples[example]).issuer_code is issuer
    scores = dict(registry.scores(examples[example]))
    assert scores[issuer] == 1.0
    assert sorted(scores.values()) == [0.0, 0.0, 1.0]


def test_the_default_registry_knows_isracard_max_and_cal():
    registry = build_default_registry()

    assert {p.issuer_code for p in registry.parsers} == {
        IssuerCode.ISRACARD,
        IssuerCode.MAX,
        IssuerCode.CAL,
    }


def test_an_unrelated_workbook_is_an_unknown_format():
    with pytest.raises(UnknownFormatError):
        build_default_registry().select(WorkbookView((SheetView("x", (("a",),)),)))


def test_fee_settings_reach_every_parser(max_workbook, max_row):
    purchase = max_row(date="01-06-2026", original=10, original_currency="€", billing=36.0)
    fee = max_row(date="06-06-2026", merchant="MY FEE", billing=1.0, original=1.0)
    workbook = max_workbook(foreign=[purchase, fee])

    parser = build_default_registry(fee_keywords=("my fee",), fee_window_days=5).select(workbook)
    result = parser.read_transactions(workbook, parser.read_context(workbook))

    assert [t.transaction_type for t in result.transactions] == [TransactionType.REGULAR]
    assert result.transactions[0].fx.commission_amount == 1
