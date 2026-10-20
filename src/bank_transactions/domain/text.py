"""Pure text helpers shared by parsing and identity so matching never diverges."""

import re

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "׳": "'", "`": "'", "´": "'"})
_QUOTES = str.maketrans({"״": '"', "“": '"', "”": '"', "„": '"'})
_WHITESPACE = re.compile(r"\s+")
_ACCOUNT_SEPARATORS = re.compile(r"[\s\-./]")
_ASCII_DIGITS = re.compile(r"[0-9]+")


def fold_text(value: str) -> str:
    """Trim, collapse whitespace, unify apostrophe/gershayim variants and casefold."""
    unified = value.translate(_APOSTROPHES).translate(_QUOTES)
    return _WHITESPACE.sub(" ", unified).strip().casefold()


def canonical_account_number(value: str) -> str:
    """Return the digits of an account number with separators (e.g. branch-account) removed."""
    digits = _ACCOUNT_SEPARATORS.sub("", value)
    if _ASCII_DIGITS.fullmatch(digits) is None:
        raise ValueError("account number must contain only digits and separators")
    return digits
