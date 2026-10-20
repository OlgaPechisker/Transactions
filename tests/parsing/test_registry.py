import pytest

from bank_transactions.domain.enums import IssuerCode
from bank_transactions.domain.errors import ParseError, UnknownFormatError
from bank_transactions.domain.models import StatementContext, StatementParseResult, StatementTotals
from bank_transactions.domain.workbook import WorkbookView
from bank_transactions.parsing.interfaces import StatementParser
from bank_transactions.parsing.registry import ParserRegistry

WORKBOOK = WorkbookView(())


def stub(code, confidence):
    class Stub(StatementParser):
        issuer_code = code

        def sniff(self, wb):
            if isinstance(confidence, Exception):
                raise confidence
            return confidence

        def read_context(self, wb):
            return StatementContext(code, ())

        def read_transactions(self, wb, ctx):
            return StatementParseResult((), (), 0, StatementTotals())

    return Stub()


def test_the_most_confident_parser_is_selected():
    low, high = stub(IssuerCode.MAX, 0.6), stub(IssuerCode.CAL, 0.9)

    assert ParserRegistry([low, high]).select(WORKBOOK) is high


def test_no_parser_clearing_the_threshold_is_an_unknown_format():
    registry = ParserRegistry([stub(IssuerCode.MAX, 0.49), stub(IssuerCode.CAL, 0.0)])

    with pytest.raises(UnknownFormatError):
        registry.select(WORKBOOK)


def test_an_empty_registry_is_an_unknown_format():
    with pytest.raises(UnknownFormatError):
        ParserRegistry().select(WORKBOOK)


def test_the_threshold_itself_is_accepted_and_is_configurable():
    parser = stub(IssuerCode.MAX, 0.5)
    assert ParserRegistry([parser]).select(WORKBOOK) is parser

    with pytest.raises(UnknownFormatError):
        ParserRegistry([parser], minimum_confidence=0.51).select(WORKBOOK)


def test_two_parsers_tied_for_best_are_refused_instead_of_guessing():
    registry = ParserRegistry([stub(IssuerCode.MAX, 0.8), stub(IssuerCode.CAL, 0.8)])

    with pytest.raises(UnknownFormatError, match="equally"):
        registry.select(WORKBOOK)


def test_a_tie_below_the_best_score_is_harmless():
    best = stub(IssuerCode.MAX, 0.9)

    registry = ParserRegistry([stub(IssuerCode.CAL, 0.6), best, stub(IssuerCode.ISRACARD, 0.6)])

    assert registry.select(WORKBOOK) is best


def test_a_parser_that_cannot_read_the_workbook_scores_zero_instead_of_failing_selection():
    broken, fine = stub(IssuerCode.MAX, ParseError("bad")), stub(IssuerCode.CAL, 0.7)

    registry = ParserRegistry([broken, fine])

    assert registry.select(WORKBOOK) is fine
    assert registry.scores(WORKBOOK) == ((IssuerCode.MAX, 0.0), (IssuerCode.CAL, 0.7))


def test_scores_are_clamped_to_the_zero_one_range():
    registry = ParserRegistry([stub(IssuerCode.MAX, 7.0), stub(IssuerCode.CAL, -1.0)])

    assert registry.scores(WORKBOOK) == ((IssuerCode.MAX, 1.0), (IssuerCode.CAL, 0.0))


def test_one_parser_per_issuer():
    registry = ParserRegistry([stub(IssuerCode.MAX, 0.9)])

    with pytest.raises(ValueError, match="already registered"):
        registry.register(stub(IssuerCode.MAX, 0.1))


def test_registered_parsers_are_listed_in_registration_order():
    first, second = stub(IssuerCode.CAL, 0.1), stub(IssuerCode.MAX, 0.2)
    registry = ParserRegistry([first])
    registry.register(second)

    assert registry.parsers == (first, second)


@pytest.mark.parametrize("threshold", [0, -0.1, 1.5])
def test_threshold_must_be_in_the_open_closed_unit_interval(threshold):
    with pytest.raises(ValueError, match="confidence"):
        ParserRegistry(minimum_confidence=threshold)
