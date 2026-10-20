"""Application settings read from the environment. Secrets are never echoed: errors name the
variable only, and the DSN and cipher key are hidden from repr."""

import base64
import binascii
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Self

from bank_transactions.domain.errors import BankTransactionsError

DSN_VARIABLE = "BT_DSN"
CIPHER_KEY_VARIABLE = "BT_CIPHER_KEY"
YEARS_BACK_VARIABLE = "BT_TWO_DIGIT_YEARS_BACK"
FEE_KEYWORDS_VARIABLE = "BT_FEE_KEYWORDS"
FEE_WINDOW_VARIABLE = "BT_FEE_WINDOW_DAYS"
CONFIDENCE_VARIABLE = "BT_MIN_CONFIDENCE"

DEFAULT_YEARS_BACK = 80
DEFAULT_FEE_WINDOW_DAYS = 3
DEFAULT_MINIMUM_CONFIDENCE = 0.5
_CIPHER_KEY_LENGTHS = (32, 48, 64)


class ConfigurationError(BankTransactionsError):
    """A setting is missing or invalid. The message names the variable, never its value."""


@dataclass(frozen=True)
class Settings:
    """`fee_keywords` of None means "use the parser defaults". The DSN and cipher key are
    validated lazily so commands that do not need them (detect, init-db) never require them."""

    dsn: str | None = field(default=None, repr=False)
    cipher_key: str | None = field(default=None, repr=False)
    two_digit_years_back: int = DEFAULT_YEARS_BACK
    fee_keywords: tuple[str, ...] | None = None
    fee_window_days: int = DEFAULT_FEE_WINDOW_DAYS
    minimum_confidence: float = DEFAULT_MINIMUM_CONFIDENCE

    @classmethod
    def from_env(cls, env: Mapping[str, str], dsn_override: str | None = None) -> Self:
        return cls(
            dsn=_text(dsn_override) or _text(env.get(DSN_VARIABLE)),
            cipher_key=_text(env.get(CIPHER_KEY_VARIABLE)),
            two_digit_years_back=_number(
                env, YEARS_BACK_VARIABLE, int, DEFAULT_YEARS_BACK, lambda n: 0 <= n <= 99
            ),
            fee_keywords=_keywords(env.get(FEE_KEYWORDS_VARIABLE)),
            fee_window_days=_number(
                env, FEE_WINDOW_VARIABLE, int, DEFAULT_FEE_WINDOW_DAYS, lambda n: n >= 0
            ),
            minimum_confidence=_number(
                env,
                CONFIDENCE_VARIABLE,
                float,
                DEFAULT_MINIMUM_CONFIDENCE,
                lambda n: 0.0 < n <= 1.0,
            ),
        )

    def require_dsn(self) -> str:
        if self.dsn is None:
            raise ConfigurationError(f"{DSN_VARIABLE} or --dsn is required for this command")
        return self.dsn

    def require_cipher_key(self) -> bytes:
        if self.cipher_key is None:
            raise ConfigurationError(f"{CIPHER_KEY_VARIABLE} is required for this command")
        try:
            key = base64.b64decode(self.cipher_key, validate=True)
        except (binascii.Error, ValueError):
            key = b""
        if len(key) not in _CIPHER_KEY_LENGTHS:
            raise ConfigurationError(
                f"{CIPHER_KEY_VARIABLE} must be base64 text of 32, 48 or 64 bytes"
            )
        return key


def _text(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()


def _keywords(value: str | None) -> tuple[str, ...] | None:
    text = _text(value)
    if text is None:
        return None
    keywords = tuple(part.strip() for part in text.split(",") if part.strip())
    return keywords or None


def _number[T: (int, float)](
    env: Mapping[str, str],
    variable: str,
    convert: Callable[[str], T],
    default: T,
    is_valid: Callable[[T], bool],
) -> T:
    text = _text(env.get(variable))
    if text is None:
        return default
    try:
        value = convert(text)
    except ValueError:
        raise ConfigurationError(f"{variable} is not a valid number") from None
    if not is_valid(value):
        raise ConfigurationError(f"{variable} is outside its allowed range")
    return value
