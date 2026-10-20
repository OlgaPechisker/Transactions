"""The registry wired with every supported issuer."""

from collections.abc import Iterable

from bank_transactions.parsing.banks.cal_visa import CalParser
from bank_transactions.parsing.banks.isracard import IsracardParser
from bank_transactions.parsing.banks.max_card import MaxParser
from bank_transactions.parsing.registry import ParserRegistry
from bank_transactions.parsing.shared.fee_folder import (
    DEFAULT_FEE_KEYWORDS,
    DEFAULT_FEE_WINDOW_DAYS,
)


def build_default_registry(
    *,
    fee_keywords: Iterable[str] = DEFAULT_FEE_KEYWORDS,
    fee_window_days: int = DEFAULT_FEE_WINDOW_DAYS,
    minimum_confidence: float = 0.5,
) -> ParserRegistry:
    """A registry of the Isracard, Max and Cal parsers, all sharing the same foreign-commission
    settings (keywords and day window)."""
    keywords = tuple(fee_keywords)
    parsers = (
        IsracardParser(fee_keywords=keywords, fee_window_days=fee_window_days),
        MaxParser(fee_keywords=keywords, fee_window_days=fee_window_days),
        CalParser(fee_keywords=keywords, fee_window_days=fee_window_days),
    )
    return ParserRegistry(parsers, minimum_confidence=minimum_confidence)
