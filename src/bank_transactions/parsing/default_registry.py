"""The registry wired with every supported issuer."""

from collections.abc import Iterable

from bank_transactions.parsing.banks.cal_visa import CalParser
from bank_transactions.parsing.banks.isracard import IsracardParser
from bank_transactions.parsing.banks.max_card import MaxParser
from bank_transactions.parsing.normalizers.dates import DEFAULT_TWO_DIGIT_YEARS_BACK
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
    two_digit_years_back: int = DEFAULT_TWO_DIGIT_YEARS_BACK,
) -> ParserRegistry:
    """A registry of the Isracard, Max and Cal parsers, all sharing the same foreign-commission
    settings (keywords and day window) and the same two-digit-year look-back (0..99 years)."""
    keywords = tuple(fee_keywords)
    parsers = (
        IsracardParser(
            fee_keywords=keywords,
            fee_window_days=fee_window_days,
            two_digit_years_back=two_digit_years_back,
        ),
        MaxParser(
            fee_keywords=keywords,
            fee_window_days=fee_window_days,
            two_digit_years_back=two_digit_years_back,
        ),
        CalParser(
            fee_keywords=keywords,
            fee_window_days=fee_window_days,
            two_digit_years_back=two_digit_years_back,
        ),
    )
    return ParserRegistry(parsers, minimum_confidence=minimum_confidence)
