import hashlib
import itertools

from bank_transactions.domain.enums import IssuerCode, LoginKeyKind
from bank_transactions.ports.currency_catalogue import CurrencyCatalogue, CurrencyEntry
from bank_transactions.ports.password_hasher import PasswordHasher
from bank_transactions.ports.token_service import TokenService

DEFAULT_CURRENCIES = (
    CurrencyEntry("EUR", "Euro"),
    CurrencyEntry("GBP", "Pound Sterling"),
    CurrencyEntry("ILS", "New Israeli Sheqel"),
    CurrencyEntry("USD", "US Dollar"),
)


class FakeTokenService(TokenService):
    """Deterministic and readable: session tokens are token-1, token-2, ..."""

    def __init__(self) -> None:
        self._counter = itertools.count(1)

    def new_session_token(self) -> str:
        return f"token-{next(self._counter)}"

    def digest(self, token: str) -> bytes:
        return hashlib.sha256(token.encode()).digest()

    def csrf_for(self, token_digest: bytes) -> str:
        return "csrf-" + token_digest.hex()[:16]

    def question_id(self, issuer: IssuerCode, hint_key: str) -> str:
        return f"q-{issuer.value}-{hint_key}"

    def throttle_key(self, kind: LoginKeyKind, value: str) -> bytes:
        return f"{kind.value}:{value}".encode()


class FakeHasher(PasswordHasher):
    """Not a hash: `hash("pw")` is `"hashed:pw"`, which keeps assertions readable."""

    PREFIX = "hashed:"

    def hash(self, password: str) -> str:
        return self.PREFIX + password

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == self.PREFIX + password

    def needs_rehash(self, password_hash: str) -> bool:
        return False


class FakeCatalogue(CurrencyCatalogue):
    def __init__(self, entries: tuple[CurrencyEntry, ...] = DEFAULT_CURRENCIES) -> None:
        self._entries = tuple(sorted(entries, key=lambda entry: entry.code))

    def codes(self) -> tuple[str, ...]:
        return tuple(entry.code for entry in self._entries)

    def entries(self) -> tuple[CurrencyEntry, ...]:
        return self._entries
