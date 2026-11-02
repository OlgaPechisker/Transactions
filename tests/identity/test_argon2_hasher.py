from argon2 import PasswordHasher as Argon2

from bank_transactions.identity.argon2_hasher import Argon2PasswordHasher
from bank_transactions.ports.password_hasher import PasswordHasher

PASSWORD = "correct horse battery staple"


def test_hasher_is_a_password_hasher():
    assert isinstance(Argon2PasswordHasher(), PasswordHasher)


def test_the_right_password_verifies_against_its_hash():
    hasher = Argon2PasswordHasher()

    stored = hasher.hash(PASSWORD)

    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored
    assert hasher.verify(stored, PASSWORD) is True


def test_the_same_password_hashes_differently_each_time():
    hasher = Argon2PasswordHasher()

    assert hasher.hash(PASSWORD) != hasher.hash(PASSWORD)


def test_a_wrong_password_is_rejected_without_raising():
    hasher = Argon2PasswordHasher()
    stored = hasher.hash(PASSWORD)

    assert hasher.verify(stored, PASSWORD + " ") is False
    assert hasher.verify(stored, "") is False


def test_passwords_are_compared_exactly_and_unicode_is_supported():
    hasher = Argon2PasswordHasher()
    stored = hasher.hash("Pässwörd-סיסמה")

    assert hasher.verify(stored, "Pässwörd-סיסמה") is True
    assert hasher.verify(stored, "passwörd-סיסמה") is False


def test_a_malformed_stored_hash_never_verifies_and_never_raises():
    hasher = Argon2PasswordHasher()

    assert hasher.verify("not-a-hash", PASSWORD) is False
    assert hasher.verify("", PASSWORD) is False
    assert hasher.verify("$argon2id$v=19$m=1,t=1,p=1$garbage$garbage", PASSWORD) is False


def test_a_current_hash_does_not_need_a_rehash():
    hasher = Argon2PasswordHasher()

    assert hasher.needs_rehash(hasher.hash(PASSWORD)) is False


def test_a_hash_with_weaker_parameters_needs_a_rehash():
    weak = Argon2(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    hasher = Argon2PasswordHasher()

    assert hasher.verify(weak, PASSWORD) is True
    assert hasher.needs_rehash(weak) is True


def test_a_malformed_stored_hash_needs_a_rehash():
    assert Argon2PasswordHasher().needs_rehash("not-a-hash") is True
