"""Currency recognition for symbols, Hebrew names and ISO codes."""

import re

from bank_transactions.domain.money import EUR, ILS, USD, Currency
from bank_transactions.domain.text import fold_text
from bank_transactions.domain.workbook import CellValue
from bank_transactions.parsing.normalizers.text import strip_bidi_marks

_ISO_CODE = re.compile(r"[a-z]{3}")
_KNOWN_SPELLINGS: dict[str, Currency] = {
    "₪": ILS,
    'ש"ח': ILS,
    "שח": ILS,
    "nis": ILS,
    "ils": ILS,
    "$": USD,
    "דולר": USD,
    "€": EUR,
    "אירו": EUR,
    "יורו": EUR,
    "£": Currency("GBP"),
    "פאונד": Currency("GBP"),
}


def parse_currency(value: CellValue) -> Currency | None:
    """The currency a cell names, or None when it is blank or not recognisable."""
    if not isinstance(value, str):
        return None
    spelling = fold_text(strip_bidi_marks(value))
    known = _KNOWN_SPELLINGS.get(spelling)
    if known is not None:
        return known
    if _ISO_CODE.fullmatch(spelling):
        return Currency(spelling.upper())
    return None
