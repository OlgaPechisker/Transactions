"""Confidence-based parser selection."""

import math
from collections.abc import Iterable

from bank_transactions.domain.enums import IssuerCode
from bank_transactions.domain.errors import ParseError, UnknownFormatError
from bank_transactions.domain.workbook import WorkbookView
from bank_transactions.parsing.interfaces import StatementParser
from bank_transactions.ports.parser_selector import ParserSelector

DEFAULT_MINIMUM_CONFIDENCE = 0.5


class ParserRegistry(ParserSelector):
    """Asks every registered parser how well it understands a workbook and picks the best.

    - The best score must reach `minimum_confidence` (inclusive), else UnknownFormatError.
    - Two parsers tied for the best score are refused with UnknownFormatError rather than
      guessed between; ties below the best score do not matter.
    - A parser whose `sniff` raises ParseError scores 0.0; scores are clamped to [0, 1].
    """

    def __init__(
        self,
        parsers: Iterable[StatementParser] = (),
        *,
        minimum_confidence: float = DEFAULT_MINIMUM_CONFIDENCE,
    ) -> None:
        if not 0.0 < minimum_confidence <= 1.0:
            raise ValueError("minimum confidence must be within (0, 1]")
        self._minimum_confidence = minimum_confidence
        self._parsers: list[StatementParser] = []
        for parser in parsers:
            self.register(parser)

    @property
    def parsers(self) -> tuple[StatementParser, ...]:
        return tuple(self._parsers)

    def register(self, parser: StatementParser) -> None:
        if any(known.issuer_code == parser.issuer_code for known in self._parsers):
            raise ValueError("a parser for this issuer is already registered")
        self._parsers.append(parser)

    def scores(self, wb: WorkbookView) -> tuple[tuple[IssuerCode, float], ...]:
        return tuple((parser.issuer_code, _confidence(parser, wb)) for parser in self._parsers)

    def select(self, wb: WorkbookView) -> StatementParser:
        scored = [(_confidence(parser, wb), parser) for parser in self._parsers]
        best = max((score for score, _ in scored), default=0.0)
        if best < self._minimum_confidence:
            raise UnknownFormatError("no parser recognised the workbook format")
        leaders = [parser for score, parser in scored if score == best]
        if len(leaders) > 1:
            raise UnknownFormatError("the workbook matches several formats equally well")
        return leaders[0]


def _confidence(parser: StatementParser, wb: WorkbookView) -> float:
    try:
        score = parser.sniff(wb)
    except ParseError:
        return 0.0
    return min(1.0, max(0.0, score)) if math.isfinite(score) else 0.0
