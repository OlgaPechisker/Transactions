import pytest

from bank_transactions.domain.enums import IdentifierType
from bank_transactions.domain.ids import IdentifierToken
from bank_transactions.identity.crypto import IdentifierCipher
from bank_transactions.ports.identifier_protector import IdentifierProtector

KEY = bytes(range(64))


@pytest.fixture
def cipher():
    return IdentifierCipher(KEY)


def test_cipher_is_an_identifier_protector(cipher):
    assert isinstance(cipher, IdentifierProtector)


def test_last4_round_trips_through_encryption(cipher):
    token = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")

    assert cipher.decrypt(IdentifierType.CARD_LAST4, token) == "4396"


def test_account_number_round_trips_through_encryption(cipher):
    token = cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "8917221038")

    assert cipher.decrypt(IdentifierType.ACCOUNT_NUMBER, token) == "8917221038"


def test_same_identifier_always_yields_the_same_ciphertext_for_direct_lookup(cipher):
    first = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")
    second = IdentifierCipher(KEY).encrypt(IdentifierType.CARD_LAST4, "4396")

    assert first == second


def test_different_identifiers_yield_different_ciphertext(cipher):
    assert cipher.encrypt(IdentifierType.CARD_LAST4, "4396") != cipher.encrypt(
        IdentifierType.CARD_LAST4, "0538"
    )


def test_identifier_type_is_bound_into_the_ciphertext(cipher):
    as_card = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")
    as_account = cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "4396")

    assert as_card != as_account


def test_a_card_token_cannot_be_decrypted_as_an_account_number(cipher):
    token = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")

    with pytest.raises(ValueError, match="does not match"):
        cipher.decrypt(IdentifierType.ACCOUNT_NUMBER, token)


def test_a_tampered_token_is_rejected(cipher):
    token = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")
    tampered = IdentifierToken(bytes([token[0] ^ 1]) + token[1:])

    with pytest.raises(ValueError, match="does not match"):
        cipher.decrypt(IdentifierType.CARD_LAST4, tampered)


def test_a_token_from_another_key_is_rejected(cipher):
    token = IdentifierCipher(bytes(reversed(KEY))).encrypt(IdentifierType.CARD_LAST4, "4396")

    with pytest.raises(ValueError, match="does not match"):
        cipher.decrypt(IdentifierType.CARD_LAST4, token)


def test_ciphertext_does_not_contain_the_plaintext(cipher):
    token = cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "8917221038")

    assert b"8917221038" not in token


@pytest.mark.parametrize("length", [32, 48, 64])
def test_siv_key_lengths_are_accepted(length):
    cipher = IdentifierCipher(bytes(range(length)))

    token = cipher.encrypt(IdentifierType.CARD_LAST4, "4396")
    assert cipher.decrypt(IdentifierType.CARD_LAST4, token) == "4396"


@pytest.mark.parametrize("length", [0, 16, 31, 33, 63, 65])
def test_other_key_lengths_are_rejected_without_revealing_the_key(length):
    key = b"k" * length

    with pytest.raises(ValueError, match="32, 48 or 64 bytes") as raised:
        IdentifierCipher(key)

    assert "kkkk" not in str(raised.value)


def test_a_non_bytes_key_is_rejected():
    with pytest.raises(ValueError, match="32, 48 or 64 bytes"):
        IdentifierCipher("x" * 64)  # type: ignore[arg-type]


def test_blank_identifiers_are_rejected(cipher):
    with pytest.raises(ValueError, match="identifier"):
        cipher.encrypt(IdentifierType.CARD_LAST4, "")


def test_the_cipher_repr_never_exposes_the_key(cipher):
    assert repr(KEY) not in repr(cipher)
    assert str(KEY[:8]) not in repr(cipher)
