"""Text cell helpers shared by every issuer parser."""

import re
from dataclasses import dataclass

from bank_transactions.domain.text import fold_text
from bank_transactions.domain.workbook import CellValue

_BIDI_MARKS = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_FIXED_WIDTH_NAME_CITY_COUNTRY = re.compile(
    r"(?P<name>.*?\S)\s{2,}(?P<city>\S.*?)\s{2,}(?P<country>[A-Z]{2})"
)
_FIXED_WIDTH_NAME_COUNTRY = re.compile(r"(?P<name>.*?\S)\s{2,}(?P<country>[A-Z]{2})")
_INSTALLMENT_NOTE = re.compile(r"תשלום\s*(?:מס['’׳]?\s*)?(\d+)\s*(?:מתוך|/)\s*(\d+)")
_LAST4 = re.compile(r"[0-9]{4}")
_LAST4_MODULUS = 10_000


@dataclass(frozen=True)
class MerchantParts:
    name: str
    city: str | None = None
    country: str | None = None


def strip_bidi_marks(text: str) -> str:
    return _BIDI_MARKS.sub("", text)


def cell_text(value: CellValue) -> str | None:
    """Trimmed text of a cell; blank cells are None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def header_key(value: CellValue) -> str:
    """Folded single-line form of a header cell, so multi-line headers compare equal."""
    if value is None:
        return ""
    return fold_text(strip_bidi_marks(str(value)))


def split_merchant(raw: str) -> MerchantParts:
    """Split the fixed-width `name  city  CC` layout when it is unmistakable.

    Only a two-letter uppercase country separated by two or more spaces counts; anything else
    stays whole so no city or country is ever invented.
    """
    text = raw.strip()
    for pattern in (_FIXED_WIDTH_NAME_CITY_COUNTRY, _FIXED_WIDTH_NAME_COUNTRY):
        match = pattern.fullmatch(text)
        if match is not None:
            groups = match.groupdict()
            return MerchantParts(groups["name"], groups.get("city"), groups["country"])
    return MerchantParts(text)


def parse_installment_note(text: str | None) -> tuple[int, int] | None:
    """The (number, total) of a "תשלום 4 מתוך 10" note; range checks are the caller's."""
    if not text:
        return None
    match = _INSTALLMENT_NOTE.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def normalize_last4(value: CellValue) -> str | None:
    """Exactly four ASCII digits, restoring the leading zeros Excel drops from numeric cells."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        return f"{value:04d}" if 0 <= value < _LAST4_MODULUS else None
    if isinstance(value, str) and _LAST4.fullmatch(value.strip()):
        return value.strip()
    return None
